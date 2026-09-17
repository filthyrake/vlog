"""Dump a consistent snapshot and verify its restore in an isolated PostgreSQL 17.

The source is read-only. The dump contains private application data: explicitly
approve its destination before running against production. The temporary restore
container is removed on exit; the mode-0600 backup and count report are retained.
"""
import argparse
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg2
from dotenv import dotenv_values
from psycopg2 import sql

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-env', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--port', type=int, default=55434)
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(mode=0o700, parents=True, exist_ok=False)
source = dotenv_values(args.source_env)['VLOG_DATABASE_URL']
url = urlsplit(source)
env = os.environ.copy()
env.update(PGHOST=url.hostname or 'localhost', PGPORT=str(url.port or 5432),
           PGUSER=unquote(url.username or ''), PGPASSWORD=unquote(url.password or ''),
           PGDATABASE=unquote(url.path.lstrip('/')))
backup = root / 'snapshot.dump'
with psycopg2.connect(source) as connection:
    connection.set_session(isolation_level='REPEATABLE READ', readonly=True)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_export_snapshot()')
        snapshot = cursor.fetchone()[0]
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables = [row[0] for row in cursor.fetchall()]
        counts = {}
        for table in tables:
            cursor.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table)))
            counts[table] = cursor.fetchone()[0]
        with backup.open('xb') as output:
            os.chmod(backup, 0o600)
            subprocess.run(['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--snapshot', snapshot],
                           env=env, stdout=output, check=True)
password = secrets.token_urlsafe(36)
settings = root / 'container.env'
settings.write_text(f'POSTGRES_USER=vlog\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=vlog\n')
os.chmod(settings, 0o600)
container = 'vlog-pg17-rehearsal-' + secrets.token_hex(4)
subprocess.run(['docker', 'run', '--rm', '-d', '--name', container, '--env-file', str(settings),
                '-p', f'127.0.0.1:{args.port}:5432', 'postgres:17.11'],
               check=True, stdout=subprocess.DEVNULL)
try:
    for _ in range(60):
        try:
            destination = psycopg2.connect(host='127.0.0.1', port=args.port, user='vlog', password=password, dbname='vlog')
            destination.close()
            break
        except psycopg2.OperationalError:
            time.sleep(1)
    else:
        raise RuntimeError('Rehearsal database did not start')
    with backup.open('rb') as input_file:
        subprocess.run(['docker', 'exec', '-i', container, 'pg_restore', '--exit-on-error', '--no-owner', '--no-acl',
                        '-U', 'vlog', '-d', 'vlog'], stdin=input_file, check=True)
    with psycopg2.connect(host='127.0.0.1', port=args.port, user='vlog', password=password, dbname='vlog') as destination:
        with destination.cursor() as cursor:
            for table, count in counts.items():
                cursor.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table)))
                actual = cursor.fetchone()[0]
                if actual != count:
                    raise RuntimeError(f'Count mismatch: {table}: {count} vs {actual}')
            cursor.execute('SHOW server_version')
            version = cursor.fetchone()[0]
    result = {'status': 'passed', 'postgres': version, 'tables_verified': len(counts),
              'videos': counts.get('videos'), 'backup_bytes': backup.stat().st_size, 'backup': str(backup)}
    (root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
finally:
    subprocess.run(['docker', 'stop', container], stdout=subprocess.DEVNULL, check=True)
    settings.unlink()
