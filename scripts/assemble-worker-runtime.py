"""Assemble a shell-free CPU runtime and retain provenance for copied Debian files.

Run only in the Docker build stage. Python's optional terminal UI and native UUID
extensions are omitted; uuid retains its standard pure-Python implementation.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

root = Path('/runtime')
root.mkdir()
shutil.copytree('/usr/local', root / 'usr/local', symlinks=True)
for pattern in ('_curses*.so', 'readline*.so', '_uuid*.so', '_tkinter*.so'):
    for module in (root / 'usr/local/lib').glob('python*/lib-dynload/' + pattern):
        module.unlink()

copied = set()
def copy_file(source):
    source = Path(source)
    if str(source).startswith('/usr/local/'):
        return
    target = root / source.relative_to('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)  # Dereference: no dangling links in the final root.
    copied.add(str(source))
    copied.add(str(source.resolve()))

for command in ('pg_dump', 'pg_restore'):
    copy_file('/usr/lib/postgresql/17/bin/' + command)

# ldd reports the recursive shared-library closure, including wheel libraries.
for binary in (root / 'usr/local').rglob('*'):
    if not binary.is_file():
        continue
    with binary.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            continue
    original = '/' + str(binary.relative_to(root))
    result = subprocess.run(
        ['ldd', original], capture_output=True, text=True,
        env={**os.environ, 'LD_LIBRARY_PATH': str(Path(original).parent) + ':/usr/local/lib'},
    )
    if 'not found' in result.stdout:
        raise RuntimeError(f'Missing dependency for {original}: {result.stdout}')
    for library in re.findall(r'(?:=>\s+|^\s*)(/\S+)', result.stdout, re.M):
        copy_file(library)

for name in ('/etc/ssl/certs/ca-certificates.crt', '/etc/nsswitch.conf', '/etc/os-release',
             '/usr/lib/os-release', '/etc/debian_version'):
    copy_file(name)
for directory in ('/usr/share/zoneinfo', '/usr/lib/x86_64-linux-gnu/ossl-modules',
                  '/usr/lib/aarch64-linux-gnu/ossl-modules'):
    if Path(directory).exists():
        for path in Path(directory).rglob('*'):
            if path.is_file():
                copy_file(path)

# Preserve Debian package versions/source attribution and licenses for every
# included system component, rather than hiding the package database from scans.
packages = set()
for listing in Path('/var/lib/dpkg/info').glob('*.list'):
    if any(line in copied for line in listing.read_text().splitlines()):
        packages.add(listing.name.removesuffix('.list').split(':')[0])
status = []
for record in Path('/var/lib/dpkg/status').read_text().split('\n\n'):
    match = re.search(r'^Package: (.+)$', record, re.M)
    if match and match[1] in packages:
        status.append(record)
        copyright_file = Path('/usr/share/doc') / match[1] / 'copyright'
        if copyright_file.exists():
            copy_file(copyright_file)
(root / 'var/lib/dpkg').mkdir(parents=True)
(root / 'var/lib/dpkg/status').write_text('\n\n'.join(status) + '\n\n')
(root / 'usr/share/doc/vlog-runtime').mkdir(parents=True)
(root / 'usr/share/doc/vlog-runtime/debian-files.json').write_text(
    json.dumps({'packages': sorted(packages), 'files': sorted(copied)}, indent=2) + '\n')
(root / 'etc/passwd').write_text('root:x:0:0:root:/root:/sbin/nologin\nvlog:x:1000:1000:vlog:/tmp:/sbin/nologin\n')
(root / 'etc/group').write_text('root:x:0:\nvlog:x:1000:\n')
(root / 'tmp').mkdir(mode=0o1777)
(root / 'tmp').chmod(0o1777)
print('Runtime Debian components:', ', '.join(sorted(packages)))
