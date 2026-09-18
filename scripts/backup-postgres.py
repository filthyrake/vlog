"""Private, atomic PostgreSQL backups for the CPU host's daily systemd timer."""
import argparse
import fcntl
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--directory', required=True, type=Path)
parser.add_argument('--retention-days', type=int, default=7)
args = parser.parse_args()
if args.retention_days < 1:
    parser.error('retention-days must be positive')
os.umask(0o077)
args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
url = urlsplit(os.environ['VLOG_DATABASE_URL'])
env = {**os.environ, 'PGHOST': url.hostname or 'localhost', 'PGPORT': str(url.port or 5432),
       'PGUSER': unquote(url.username or ''), 'PGPASSWORD': unquote(url.password or ''),
       'PGDATABASE': unquote(url.path.lstrip('/'))}
with (args.directory / '.backup.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    now = datetime.now(timezone.utc)
    destination = args.directory / ('vlog-pg17-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '.dump')
    descriptor, name = tempfile.mkstemp(prefix='.partial-', dir=args.directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            subprocess.run(['pg_dump', '--no-password', '--format=custom', '--no-owner', '--no-acl'],
                           env=env, stdout=output, check=True, timeout=1800)
            output.flush()
            os.fsync(output.fileno())
        subprocess.run(['pg_restore', '--list', str(temporary)], stdout=subprocess.DEVNULL,
                       check=True, timeout=60)
        temporary.rename(destination)
        # Remove only this timer's completed backups, after a new backup succeeds.
        cutoff = (now - timedelta(days=args.retention_days)).timestamp()
        for backup in args.directory.glob('vlog-pg17-*.dump'):
            if backup != destination and backup.is_file() and backup.stat().st_mtime < cutoff:
                backup.unlink()
        print(f'Backup verified: {destination.name} ({destination.stat().st_size} bytes)')
    finally:
        temporary.unlink(missing_ok=True)
