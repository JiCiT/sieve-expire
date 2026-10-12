#!/usr/bin/python3
"""Offline, explicit config-volume hook integration/removal. Does not call Docker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

WRAPPER = b'''#!/bin/bash
# Managed sieve-expire installer wrapper; original administrator hook preserved.
admin_status=0
if [[ -f /tmp/docker-mailserver/user-patches.sieve-expire-original.sh ]]; then
  /bin/bash /tmp/docker-mailserver/user-patches.sieve-expire-original.sh || admin_status=$?
fi
if [[ -f /tmp/docker-mailserver/sieve-expire/startup.py ]]; then
  /usr/bin/python3 -B /tmp/docker-mailserver/sieve-expire/startup.py ||
    echo 'sieve-expire installer: failure; see startup logs' >&2
else
  echo 'sieve-expire installer: persistent package missing' >&2
fi
exit "$admin_status"
'''


def safe(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_mode & 0o022:
        raise ValueError('refuse nonregular or group/other-writable file: ' + str(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['install-hook', 'remove-hook'])
    ap.add_argument('--config-dir', required=True, type=Path,
                    help='DMS configuration volume directory; never a running container path')
    a = ap.parse_args()
    d = a.config_dir
    if not d.is_dir() or d.is_symlink():
        ap.error('config directory must be an existing nonsymlink directory')
    hook = d / 'user-patches.sh'
    backup = d / 'user-patches.sieve-expire-original.sh'
    record = d / 'sieve-expire-hook.json'
    if a.action == 'install-hook':
        if not (d / 'sieve-expire/manifest.json').is_file():
            ap.error('copy the complete package to config-dir/sieve-expire first')
        if record.exists():
            safe(record)
            safe(hook)
            if hook.read_bytes() != WRAPPER:
                ap.error('managed hook changed; preserve and resolve manually')
            meta = json.loads(record.read_text())
            if meta['original_sha256'] and not backup.exists():
                ap.error('preserved administrator hook missing')
            if backup.exists():
                safe(backup)
                if hashlib.sha256(backup.read_bytes()).hexdigest() != meta['original_sha256']:
                    ap.error('preserved administrator hook changed')
            print('hook already installed; unchanged')
            return
        if backup.exists() or backup.is_symlink():
            ap.error('preservation path already occupied')
        original_hash = None
        if hook.exists() or hook.is_symlink():
            safe(hook)
            original_hash = hashlib.sha256(hook.read_bytes()).hexdigest()
            os.rename(hook, backup)  # Original bytes, mode, owner, timestamps preserved.
        with hook.open('xb') as f:
            f.write(WRAPPER)
        hook.chmod(0o700)
        with record.open('x') as f:
            json.dump({'original_sha256': original_hash,
                       'wrapper_sha256': hashlib.sha256(WRAPPER).hexdigest()}, f, indent=2)
        record.chmod(0o600)
    else:
        safe(record)
        safe(hook)
        meta = json.loads(record.read_text())
        if hook.read_bytes() != WRAPPER:
            ap.error('managed hook changed; cannot safely restore original')
        if meta['original_sha256']:
            safe(backup)
            if hashlib.sha256(backup.read_bytes()).hexdigest() != meta['original_sha256']:
                ap.error('administrator backup changed; cannot restore')
            os.replace(backup, hook)
        else:
            if backup.exists() or backup.is_symlink():
                ap.error('unexpected administrator backup')
            hook.unlink()
        record.unlink()
    print(json.dumps({'action': a.action, 'config_dir': str(d), 'runtime_changed': False}))


if __name__ == '__main__':
    main()
