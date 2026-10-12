#!/usr/bin/python3
"""DMS fresh-container hook. No daemon, build, download or maintenance execution."""
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parent
CONFIG = Path('/etc/dovecot/conf.d/99-sieve-expire-installer.conf')
STATE = Path('/run/sieve-expire-installer')
OWNED = Path('/var/lib/sieve-expire-installer/owned.json')
DESTINATIONS = {
    '/usr/lib/dovecot/modules/lib90_sieve_expire_plugin.so': 'normal/lib90_sieve_expire_plugin.so',
    '/usr/lib/dovecot/modules/sieve/lib90_sieve_expire_plugin.so': 'normal/lib90_sieve_expire_plugin.so',
    '/usr/lib/dovecot/modules/lib91_imap_sieve_expire_plugin.so': 'normal/lib91_imap_sieve_expire_plugin.so',
    '/usr/lib/dovecot/modules/settings/libsieve_expire_settings.so': 'normal/libsieve_expire_settings.so',
    '/usr/lib/dovecot/modules/sieve/lib90_sieve_expire_fallback_plugin.so': 'fallback/lib90_sieve_expire_fallback_plugin.so',
}
RESERVED = re.compile(r'\b(?:sieve_expire(?:_fallback|_maintenance_cooldown_seconds)?|imap_sieve_expire)\b')
NORMAL = '''# Managed by sieve-expire DMS installer; metadata only, Gate D disabled.
mail_plugins {
  sieve_expire = yes
}
sieve_plugins {
  sieve_expire = yes
}
sieve_extensions {
  expire = yes
}
protocol imap {
  mail_plugins {
    imap_sieve_expire = no
  }
  sieve_expire_maintenance_cooldown_seconds = 300
}
'''
FALLBACK = '''# Managed by sieve-expire DMS installer; deadline actions are no-ops.
sieve_plugins {
  sieve_expire_fallback = yes
}
sieve_extensions {
  expire = yes
}
'''
RESULT = {'selection': 'neither', 'errors': [], 'notification': 'not-requested',
          'maintenance_enabled': False, 'validation': []}
PUBLIC_FORMAT = 'sieve-expire-public-release-v1'
# Replaced only by a reviewed release publication. The signature authenticates
# qualification and artifacts together; a user-editable PASS marker cannot enable
# maintenance. The trusted installer itself is the trust boundary (not root-proof).
RELEASE_KEY = Path(__file__).resolve().parent / 'release-key.pem'
TRUSTED_KEY_SHA256 = '7a762bf45a7bb86e4850df7ffb149d8cf802a85d54b36f0f1b51e586e2a02e5c'


class Refusal(Exception):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path, private=False):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise Refusal('unsafe owner/type/write permissions: ' + str(path))
    if private and s.st_mode & 0o077:
        raise Refusal('options must be root-only: ' + str(path))


def atomic(path, data, mode=0o644):
    if path.is_symlink():
        raise Refusal('refuse symlink destination: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.sieve-expire-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def checked_manifest():
    m = json.loads((PACKAGE / 'manifest.json').read_text())
    if not isinstance(m, dict) or m.get('format') not in ('sieve-expire-dms-installation-v1', PUBLIC_FORMAT):
        raise Refusal('unsupported package manifest')
    if m['format'] == PUBLIC_FORMAT:
        regular(RELEASE_KEY)
        # The key digest is bound to the reviewed startup source, not the manifest.
        if digest(RELEASE_KEY) != TRUSTED_KEY_SHA256:
            raise Refusal('untrusted release key')
        command(['openssl', 'dgst', '-sha256', '-verify', str(RELEASE_KEY),
                 '-signature', str(PACKAGE / 'manifest.sig'), str(PACKAGE / 'manifest.json')],
                'qualification-signature')
        if any(m.get('gates', {}).get(g) != 'PASS' for g in ('A', 'B', 'C')):
            raise Refusal('release metadata gates not qualified')
    if m.get('destinations') != DESTINATIONS:
        raise Refusal('unsupported installation paths')
    required = set(DESTINATIONS.values()) | {'startup.py'}
    if not isinstance(m.get('files'), dict) or set(m['files']) != required:
        raise Refusal('incomplete/unrecognized package file list')
    if any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for h in m['files'].values()):
        raise Refusal('invalid checksum declaration')
    if m.get('implementations') != {
            'normal': [r for r in m['implementations'].get('normal', [])],
            'fallback': ['fallback/lib90_sieve_expire_fallback_plugin.so']}:
        raise Refusal('invalid implementation file list')
    if set(m['implementations']['normal']) != {r for r in required if r.startswith('normal/')}:
        raise Refusal('incomplete normal implementation')
    if m.get('maintenance_enabled') is not False:
        raise Refusal('this installer cannot enable maintenance')
    if not isinstance(m.get('target'), dict) or not isinstance(m['target'].get('normal_packages'), dict):
        raise Refusal('missing qualified target')
    if set(m['target']['normal_packages']) != {'dovecot-core', 'dovecot-sieve'}:
        raise Refusal('missing core/Pigeonhole package qualification')
    return m


def command(argv, label, timeout=15, data=None):
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        stdout, stderr = process.communicate(data, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        if process is not None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise Refusal(label + ': ' + type(e).__name__) from e
    # Private records may contain effective administrator settings. Never email them.
    record = {'label': label, 'argv': argv, 'returncode': process.returncode,
              'stdout': stdout.decode(errors='replace'),
              'stderr': stderr.decode(errors='replace')}
    RESULT['validation'].append(record)
    if process.returncode:
        raise Refusal(label + ': exit ' + str(process.returncode) + ' (see private result)')
    return stdout.decode(errors='replace')


def cleanup():
    """Remove only files proven to be ours, never an unknown administrator file."""
    if not OWNED.exists():
        if CONFIG.exists() or CONFIG.is_symlink():
            raise Refusal('unowned installer configuration path exists')
        return
    regular(OWNED, private=True)
    files = json.loads(OWNED.read_text())
    allowed = {str(CONFIG)} | set(DESTINATIONS)
    # Check every entry first; never partially erase an externally changed installation.
    for name, expected in files.items():
        if name not in allowed:
            raise Refusal('unrecognized ownership entry')
        p = Path(name)
        if p.exists() or p.is_symlink():
            regular(p)
            if digest(p) != expected:
                raise Refusal('administrator changed installed file: ' + name)
    for name in files:
        Path(name).unlink(missing_ok=True)
    OWNED.unlink()


def config_inputs(path=Path('/etc/dovecot/dovecot.conf'), seen=None):
    """Follow actual file includes, including outside conf.d; don't edit any of them."""
    seen = set() if seen is None else seen
    key = str(path.resolve())
    if key in seen:
        return
    seen.add(key)
    if len(seen) > 512:
        raise Refusal('configuration include graph too large')
    text = path.read_text()
    for line in text.splitlines():
        live = line.split('#', 1)[0].strip()
        if RESERVED.search(live):
            raise Refusal('administrator-owned plugin directive: ' + str(path))
        if live.startswith(('!include ', '!include_try ')):
            target = live.split(None, 1)[1].strip('"\'')
            pattern = target if target.startswith('/') else str(path.parent / target)
            for name in glob.glob(pattern):
                yield from config_inputs(Path(name), seen)
    yield key


def integrity(manifest, selection):
    for rel in manifest['implementations'][selection]:
        path = PACKAGE / rel
        regular(path)
        if digest(path) != manifest['files'][rel]:
            raise Refusal(selection + ' artifact checksum: ' + rel)


def compatible(manifest, selection):
    arch = command(['dpkg', '--print-architecture'], selection + '-architecture').strip()
    version = command(['dovecot', '--version'], selection + '-runtime').strip()
    if arch != manifest['target']['architecture'] or not version.startswith('2.4.'):
        raise Refusal(selection + ' unsupported architecture/runtime family')
    if selection == 'normal':
        for package, expected in manifest['target']['normal_packages'].items():
            value = command(['dpkg-query', '-W', '-f=${Version}', package],
                            'package-' + package).strip()
            if value != expected:
                raise Refusal('normal unqualified package revision: ' + package)
    if selection == 'normal' and manifest.get('format') == PUBLIC_FORMAT:
        if version != manifest['target']['runtime'] or os.environ.get('DMS_RELEASE') != manifest['target']['dms_version']:
            raise Refusal('normal unqualified runtime/DMS release')
        for path, expected in manifest['target'].get('runtime_files', {}).items():
            if command(['sha256sum', path], 'runtime-fingerprint').split()[0] != expected:
                raise Refusal('normal runtime/build identity changed: ' + path)
    # Fallback has fewer dependencies. Native loader/ABI and compiler probes below
    # decide usability on 2.4; passing is best effort, not a future ABI guarantee.


def install(manifest, selection, maintenance=False):
    owned = {}
    for dest, rel in manifest['destinations'].items():
        if rel not in manifest['implementations'][selection]:
            continue
        path = Path(dest)
        if path.exists() or path.is_symlink():
            raise Refusal('unowned module destination: ' + dest)
    if CONFIG.exists() or CONFIG.is_symlink():
        raise Refusal('unowned configuration destination')
    # Journal before every copy so interrupted attempts can be cleaned next time.
    for dest, rel in manifest['destinations'].items():
        if rel in manifest['implementations'][selection]:
            owned[dest] = manifest['files'][rel]
            atomic(OWNED, (json.dumps(owned) + '\n').encode(), 0o600)
            atomic(Path(dest), (PACKAGE / rel).read_bytes())
    text = NORMAL if selection == 'normal' else FALLBACK
    if maintenance and selection == 'normal':
        if manifest.get('format') != PUBLIC_FORMAT or manifest.get('gates', {}).get('D') != 'PASS':
            raise Refusal('maintenance requires authenticated Gate D PASS')
        text = text.replace('metadata only, Gate D disabled.', 'qualified maintenance enabled.')
        text = text.replace('imap_sieve_expire = no', 'imap_sieve_expire = yes')
    owned[str(CONFIG)] = hashlib.sha256(text.encode()).hexdigest()
    atomic(OWNED, (json.dumps(owned) + '\n').encode(), 0o600)
    # The normal settings parser is already installed when the key becomes visible.
    atomic(CONFIG, text.encode())


def validate(selection, maintenance=False):
    command(['doveconf', '-n'], selection + '-configuration')
    imap = command(['doveconf', '-f', 'protocol=imap', '-h', 'mail_plugins'],
                   selection + '-imap-selection')
    enabled = bool(re.search(r'\bimap_sieve_expire\s*=\s*yes\b', imap))
    if enabled != (maintenance and selection == 'normal'):
        raise Refusal('maintenance companion differs from requested state')
    with tempfile.TemporaryDirectory(prefix='probe-', dir=STATE) as directory:
        source = Path(directory) / 'probe.sieve'
        binary = Path(directory) / 'probe.svbin'
        source.write_text('require ["expire"]; expire "30d"; expunge "180d"; keep;\n')
        # Compilation loads the selected Pigeonhole module. Never execute the script.
        command(['sievec', str(source), str(binary)], selection + '-sieve-compile')
        dump = command(['sieve-dump', str(binary)], selection + '-bytecode')
        if 'EXPIRE' not in dump or 'EXPUNGE' not in dump:
            raise Refusal('missing distinct operations')


def notify(address, needed=True):
    if not address:
        RESULT['notification_mailbox_validation'] = 'disabled'
        return
    RESULT['notification_mailbox_validation'] = 'invalid-local-mailbox'
    if not isinstance(address, str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.+\-]*@[A-Za-z0-9][A-Za-z0-9.\-]*', address):
        return
    if os.environ.get('ACCOUNT_PROVISIONER', 'FILE') != 'FILE':
        RESULT['notification_mailbox_validation'] = 'unsupported-account-provisioner'
        return
    # FILE accounts, not hosted domains or generated userdb alias dummy records.
    accounts = Path('/tmp/docker-mailserver/postfix-accounts.cf')
    vmailbox = Path('/etc/postfix/vmailbox')
    if not accounts.is_file() or not vmailbox.is_file():
        return
    logins = {line.split('|', 1)[0].strip() for line in accounts.read_text().splitlines()
              if line.strip() and not line.lstrip().startswith('#')}
    boxes = {line.split()[0] for line in vmailbox.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')}
    if address not in logins or address not in boxes:
        return
    RESULT['notification_mailbox_validation'] = 'valid-local-mailbox'
    if not needed:
        return
    try:
        fd = os.open(STATE / 'notification-attempted', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        RESULT['notification'] = 'already-attempted'
        return
    os.close(fd)
    message = ('From: ' + address + '\nTo: ' + address +
               '\nSubject: sieve-expire startup: ' + RESULT['selection'] +
               '\nAuto-Submitted: auto-generated\n\n' +
               'Startup selection: ' + RESULT['selection'] + '.\n' +
               ('expire and expunge are no-ops; requested deadlines are not stored.\n'
                if RESULT['selection'] == 'fallback' else
                'Neither implementation is available; scripts requiring expire may fail.\n') +
               'Degraded selection has no maintenance; administrator conflicts remain untouched.\n'
               'Inspect container startup logs and the protected\n'
               '/run/sieve-expire-installer/result.json for diagnostics.\n').encode()
    atomic(STATE / 'notification.json', json.dumps({'recipient': address,
           'message': message.decode()}).encode(), 0o600)
    try:
        # A bounded one-shot submitter, not a monitor or retry loop. Postdrop may
        # wait for pickup before the daemon exists. Do not delay daemon startup
        # while that handshake completes. Reserve the single attempt above.
        subprocess.Popen([sys.executable, '-B', str(PACKAGE / 'startup.py'), '--notify'],
                         stdin=subprocess.DEVNULL, start_new_session=True)
        RESULT['notification'] = 'scheduled-best-effort'
    except OSError as e:
        RESULT['notification'] = 'submission-launch-failed'
        RESULT['errors'].append(type(e).__name__)


def notification_worker():
    try:
        regular(STATE / 'notification.json', private=True)
        fd = os.open(STATE / 'notification-worker-started', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return 0
    data = json.loads((STATE / 'notification.json').read_text())
    status = 'submission-failed'
    try:
        command(['/usr/sbin/sendmail', '-oi', '-f', data['recipient'], data['recipient']],
                'notification-submit', timeout=15, data=data['message'].encode())
        status = 'queued-best-effort'
    except Refusal as e:
        RESULT['errors'].append(str(e))
    record = {'status': status, 'errors': RESULT['errors'], 'validation': RESULT['validation']}
    atomic(STATE / 'notification-result.json', (json.dumps(record, indent=2) + '\n').encode(), 0o600)
    print('sieve-expire installer notification: ' + status, file=sys.stderr)
    return 0


def main():
    if os.geteuid() != 0:
        print('sieve-expire installer: must run as container root', file=sys.stderr)
        return 1
    # No live-process switching; invocation is exclusively before fresh startup.
    if Path('/CONTAINER_START').exists() or Path('/run/dovecot/master.pid').exists():
        print('sieve-expire installer: ordinary restart/live invocation refused', file=sys.stderr)
        return 1
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    try:
        fd = os.open(STATE / 'attempted', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        print('sieve-expire installer: startup already attempted', file=sys.stderr)
        return 0
    os.close(fd)
    options = {}
    try:
        for name in ('manifest.json', 'startup.py'):
            regular(PACKAGE / name)
        manifest = checked_manifest()
        if digest(PACKAGE / 'startup.py') != manifest['files']['startup.py']:
            raise Refusal('installer checksum mismatch')
        if (PACKAGE / 'options.json').exists():
            regular(PACKAGE / 'options.json', private=True)
            options = json.loads((PACKAGE / 'options.json').read_text())
            allowed = {'notification_mailbox'}
            if manifest['format'] == PUBLIC_FORMAT:
                allowed |= {'maintenance', 'selection'}
            if set(options) - allowed:
                raise Refusal('unknown installer option')
        maintenance = options.get('maintenance', False)
        selection_request = options.get('selection', 'normal')
        if not isinstance(maintenance, bool) or selection_request not in ('normal', 'fallback'):
            raise Refusal('invalid public selection options')
        if maintenance and manifest.get('gates', {}).get('D') != 'PASS':
            raise Refusal('maintenance activation rejected: Gate D not PASS')
        cleanup()
        list(config_inputs())
        for path in manifest['destinations']:
            if Path(path).exists() or Path(path).is_symlink():
                raise Refusal('pre-existing unowned module: ' + path)
        command(['doveconf', '-n'], 'administrator-baseline')
        for selection in (('normal', 'fallback') if selection_request == 'normal' else ('fallback',)):
            try:
                integrity(manifest, selection)
                compatible(manifest, selection)
                install(manifest, selection, maintenance)
                validate(selection, maintenance)
                RESULT['selection'] = selection
                RESULT['maintenance_enabled'] = maintenance and selection == 'normal'
                break
            except (Refusal, OSError, ValueError, KeyError) as e:
                RESULT['errors'].append(selection + ': ' + str(e))
                cleanup()
        if RESULT['selection'] == 'neither':
            command(['doveconf', '-n'], 'neither-clean-configuration')
    except (Refusal, OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        RESULT['errors'].append('conflict/infrastructure: ' + str(e))
        RESULT['unresolved_conflict'] = True
    try:
        notify(options.get('notification_mailbox'), needed=RESULT['selection'] != 'normal')
    except (OSError, ValueError) as e:
        RESULT['notification'] = 'notification-error'
        RESULT['errors'].append(type(e).__name__)
    atomic(STATE / 'result.json', (json.dumps(RESULT, indent=2) + '\n').encode(), 0o600)
    print('sieve-expire installer: ' + json.dumps({k: v for k, v in RESULT.items()
          if k != 'validation'}), file=sys.stderr)
    # DMS continues even after a degraded selection. Unknown admin conflicts are
    # explicitly reported; we cannot promise startup with broken external config.
    return 0 if not RESULT.get('unresolved_conflict') else 1


if __name__ == '__main__':
    sys.exit(notification_worker() if sys.argv[1:] == ['--notify'] else main())
