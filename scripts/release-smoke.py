"""Exercise real login, upload, CPU encoding and playback in an isolated database.

Run with the release interpreter and VLOG_SMOKE_ADMIN_DB pointing at a disposable
PostgreSQL server. Never loads a deployment .env file. All created rows/files are
isolated; the database created by this run is removed on exit.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg2
from psycopg2 import sql

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--hold-seconds', type=int, default=0, help='Keep the tested app available for browser checks')
parser.add_argument('--worker-image', help='Use a built CPU image for the local worker process')
args = parser.parse_args()
admin_url = os.environ['VLOG_SMOKE_ADMIN_DB']
parsed = urlsplit(admin_url)
if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != 55433:
    raise SystemExit('Use the isolated PostgreSQL instance on localhost:55433')
root = Path(__file__).resolve().parent.parent
work = Path(tempfile.mkdtemp(prefix='vlog-smoke-'))
db_name = 'vlog_smoke_' + secrets.token_hex(6)
connection = psycopg2.connect(admin_url)
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db_name)))
db_url = admin_url.rsplit('/', 1)[0] + '/' + db_name
env = {k: v for k, v in os.environ.items() if not k.startswith('VLOG_')}
env.update({
    'VLOG_DATABASE_URL': db_url, 'VLOG_STORAGE_PATH': str(work / 'storage'),
    'VLOG_SESSION_SECRET_KEY': secrets.token_urlsafe(48),
    'VLOG_SECURE_COOKIES': 'false', 'VLOG_REDIS_URL': '', 'VLOG_JOB_QUEUE_MODE': 'database',
    'VLOG_LIVE_ENABLED': 'false', 'VLOG_TRANSCRIPTION_ENABLED': 'false',
    'VLOG_TRANSCRIPTION_ON_UPLOAD': 'false', 'VLOG_BACKUP_ENABLED': 'false',
    'VLOG_HWACCEL_TYPE': 'none', 'VLOG_PARALLEL_QUALITIES': '1',
    'VLOG_PARALLEL_QUALITIES_AUTO': 'false', 'VLOG_WORKER_FALLBACK_POLL_INTERVAL': '1',
    'VLOG_LOG_FILE': str(work / 'app.log'), 'VLOG_AUDIT_LOG_PATH': str(work / 'audit.log'),
    'PYTHONUNBUFFERED': '1',
})
for directory in ('videos', 'uploads', 'archive'):
    (work / 'storage' / directory).mkdir(parents=True, exist_ok=True)
processes = []
handles = []

def start(name, command):
    log = (work / f'{name}.log').open('w')
    handles.append(log)
    process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
    processes.append(process)
    return process

def wait_ready(url):
    for _ in range(60):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if any(p.poll() is not None for p in processes):
            raise RuntimeError('Service exited; inspect ' + str(work))
        time.sleep(1)
    raise RuntimeError('Startup timeout: ' + url)

try:
    subprocess.run([sys.executable, '-m', 'api.database'], cwd=root, env=env, check=True,
                   stdout=subprocess.DEVNULL)
    start('admin', [sys.executable, '-m', 'uvicorn', 'api.admin:app', '--host', '127.0.0.1', '--port', '19001'])
    start('public', [sys.executable, '-m', 'uvicorn', 'api.public:app', '--host', '127.0.0.1', '--port', '19000'])
    wait_ready('http://127.0.0.1:19001/health')
    wait_ready('http://127.0.0.1:19000/health')
    clip = work / 'sample.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=640x360:rate=24', '-f', 'lavfi', '-i', 'sine=frequency=440',
                    '-t', '4', '-c:v', 'libx264', '-threads', '2', '-c:a', 'aac', str(clip)], check=True)
    password = 'Smoke-Aa1!' + secrets.token_urlsafe(24)
    with httpx.Client(base_url='http://127.0.0.1:19001', timeout=30) as client:
        response = client.post('/api/v1/auth/setup', json={
            'username': 'smokeadmin', 'email': 'smoke@example.com',
            'password': password,
        })
        response.raise_for_status()
        csrf = client.get('/api/v1/auth/csrf-token')
        csrf.raise_for_status()
        client.headers['X-CSRF-Token'] = csrf.json()['csrf_token']
        client.post('/api/v1/auth/logout').raise_for_status()
        client.headers.pop('X-CSRF-Token')
        client.post('/api/v1/auth/login', json={'username_or_email': 'smokeadmin', 'password': password}).raise_for_status()
        csrf = client.get('/api/v1/auth/csrf-token')
        csrf.raise_for_status()
        client.headers['X-CSRF-Token'] = csrf.json()['csrf_token']
        with clip.open('rb') as video:
            response = client.post('/api/videos', files={'file': ('sample.mp4', video, 'video/mp4')},
                                   data={'title': 'Release smoke clip', 'description': 'Isolated synthetic video'})
        response.raise_for_status()
        uploaded = response.json()
        if args.worker_image:
            worker_command = [
                'docker', 'run', '--rm', '--network', 'host', '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--cpus', '2', '--memory', '8g', '--pids-limit', '512',
                '--user', f'{os.getuid()}:{os.getgid()}',
                '--tmpfs', '/tmp:rw,nosuid,size=256m',
                '--volume', f'{work}:{work}',
            ]
            for key, value in env.items():
                if key.startswith('VLOG_'):
                    worker_command += ['--env', f'{key}={value}']
            worker_command += [args.worker_image, 'python', '-m', 'worker.transcoder']
        else:
            worker_command = [sys.executable, 'worker/transcoder.py']
        start('worker', worker_command)
        for _ in range(150):
            detail = client.get(f"/api/videos/{uploaded['video_id']}")
            detail.raise_for_status()
            status = detail.json()['status']
            if status == 'ready':
                break
            if status == 'failed':
                raise RuntimeError('Transcoding failed; inspect ' + str(work))
            time.sleep(2)
        else:
            raise RuntimeError('Transcoding timeout; inspect ' + str(work))
        slug = uploaded['slug']
        with httpx.Client(base_url='http://127.0.0.1:19000', timeout=15) as public:
            response = public.get(f'/api/videos/{slug}')
            response.raise_for_status()
            playlist = public.get(f'/videos/{slug}/master.m3u8')
            playlist.raise_for_status()
            assert '#EXTM3U' in playlist.text
            assert public.get(f'/watch/{slug}').status_code == 200
            segment = next((work / 'storage' / 'videos' / slug).rglob('*.m4s'), None)
            if segment is None:
                segment = next((work / 'storage' / 'videos' / slug).rglob('*.ts'))
            path = segment.relative_to(work / 'storage' / 'videos')
            assert public.get('/videos/' + str(path)).status_code == 200
            assert public.post('/api/analytics/session', json={'video_id': uploaded['video_id'], 'quality': 'auto'}).status_code == 200
            assert public.post('/api/analytics/session', json={'video_id': uploaded['video_id'], 'quality': 'invalid'}).status_code == 422
        result = {'status': 'passed', 'slug': slug, 'video_status': status,
                  'watch_url': f'http://127.0.0.1:19000/watch/{slug}', 'logs': str(work)}
        (work / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        if args.hold_seconds:
            time.sleep(args.hold_seconds)
finally:
    for process in reversed(processes):
        process.terminate()
    for process in processes:
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for handle in handles:
        handle.close()
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(db_name)))
    connection.close()
    print('Isolated smoke database removed; logs retained at ' + str(work), flush=True)
