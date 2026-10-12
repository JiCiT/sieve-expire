#!/usr/bin/python3
"""Offline package assembly from qualified artifacts, never from startup."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--normal-manifest', type=Path, required=True)
    ap.add_argument('--normal-artifacts', type=Path, required=True)
    ap.add_argument('--fallback-manifest', type=Path, required=True)
    ap.add_argument('--fallback-artifact', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True, help='new package directory')
    a = ap.parse_args()
    normal = json.loads(a.normal_manifest.read_text())
    fallback = json.loads(a.fallback_manifest.read_text())
    sources = {}
    for name, item in normal['modules'].items():
        p = a.normal_artifacts / name
        if sha(p) != item['sha256']:
            ap.error('normal artifact differs from qualification: ' + name)
        sources['normal/' + name] = p
    if sha(a.fallback_artifact) != fallback['sha256']:
        ap.error('fallback artifact differs from qualification')
    sources['fallback/' + fallback['module']] = a.fallback_artifact
    sources['startup.py'] = Path(__file__).with_name('startup.py')
    a.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    files = {}
    for rel, source in sources.items():
        dest = a.output / rel
        dest.parent.mkdir(exist_ok=True)
        dest.parent.chmod(0o700 if dest.parent == a.output else 0o755)
        shutil.copyfile(source, dest)
        dest.chmod(0o644)
        files[rel] = sha(dest)
    destinations = {path: 'normal/' + item['module']
                    for path, item in normal['installed_paths'].items()}
    destinations['/usr/lib/dovecot/modules/sieve/' + fallback['module']] = 'fallback/' + fallback['module']
    manifest = {
        'format': 'sieve-expire-dms-installation-v1',
        'normal_release': normal['candidate']['docker_image_id'],
        'normal_source': normal['build']['source_identity'],
        'base': normal['approved_base']['image_id'],
        'fallback_qualification': sha(a.fallback_manifest),
        'normal_qualification': sha(a.normal_manifest),
        'target': {'architecture': normal['approved_base']['package_architecture'],
                   'normal_packages': {p: normal['approved_base']['debian_package_revision']
                                       for p in ('dovecot-core', 'dovecot-sieve')}},
        'implementations': {'normal': [r for r in files if r.startswith('normal/')],
                            'fallback': [r for r in files if r.startswith('fallback/')]},
        'files': files, 'destinations': destinations,
        'maintenance_enabled': False,
    }
    (a.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (a.output / 'manifest.json').chmod(0o644)
    (a.output / 'options.json').write_text('{"notification_mailbox": ""}\n')
    (a.output / 'options.json').chmod(0o600)
    print(json.dumps({'package': str(a.output), 'manifest_sha256': sha(a.output / 'manifest.json')}))


if __name__ == '__main__':
    main()
