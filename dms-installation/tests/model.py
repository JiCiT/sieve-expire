"""Focused offline controls; does not call Docker or modify runtime."""
from pathlib import Path
import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('startup', ROOT / 'startup.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class Controls(unittest.TestCase):
    def test_include_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'main').write_text('!include other\n')
            (d / 'other').write_text('protocol imap {\n mail_plugins {\n imap_sieve_expire = yes\n }\n}\n')
            with self.assertRaises(s.Refusal):
                list(s.config_inputs(d / 'main'))
            (d / 'other').write_text('# sieve_expire old proposal\nauth_verbose = no\n')
            self.assertEqual(len(list(s.config_inputs(d / 'main'))), 2)

    def test_native_metadata_only(self):
        self.assertIn('imap_sieve_expire = no', s.NORMAL)
        self.assertNotIn('imap_sieve_expire = yes', s.NORMAL)
        self.assertIn('sieve_expire_maintenance_cooldown_seconds = 300', s.NORMAL)
        self.assertNotIn('maintenance_cooldown', s.FALLBACK)
        self.assertNotIn('sieve_expire = yes', s.FALLBACK)

    def test_process_timeout(self):
        with self.assertRaises(s.Refusal):
            s.command([sys.executable, '-c', 'import time;time.sleep(10)'], 'timeout', timeout=.02)

    def test_hook_preservation_and_idempotence(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'sieve-expire').mkdir()
            (d / 'sieve-expire/manifest.json').write_text('{}')
            original = b'#!/bin/bash\necho custom\nexit 7\n'
            hook = d / 'user-patches.sh'
            hook.write_bytes(original)
            hook.chmod(0o700)
            timestamp = hook.stat().st_mtime_ns
            args = [sys.executable, '-B', str(ROOT / 'integrate.py')]
            subprocess.run(args + ['install-hook', '--config-dir', str(d)], check=True, capture_output=True)
            self.assertEqual((d / 'user-patches.sieve-expire-original.sh').read_bytes(), original)
            self.assertEqual((d / 'user-patches.sieve-expire-original.sh').stat().st_mtime_ns, timestamp)
            current = hook.read_bytes()
            subprocess.run(args + ['install-hook', '--config-dir', str(d)], check=True, capture_output=True)
            self.assertEqual(hook.read_bytes(), current)
            subprocess.run(args + ['remove-hook', '--config-dir', str(d)], check=True, capture_output=True)
            self.assertEqual(hook.read_bytes(), original)
            self.assertEqual(hook.stat().st_mtime_ns, timestamp)

    def test_hook_symlink_and_modified_wrapper_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'sieve-expire').mkdir()
            (d / 'sieve-expire/manifest.json').write_text('{}')
            (d / 'target').write_text('unchanged')
            (d / 'user-patches.sh').symlink_to('target')
            args = [sys.executable, '-B', str(ROOT / 'integrate.py'), 'install-hook', '--config-dir', str(d)]
            self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
            self.assertEqual((d / 'target').read_text(), 'unchanged')
            (d / 'user-patches.sh').unlink()
            subprocess.run(args, check=True, capture_output=True)
            (d / 'user-patches.sh').write_text('administrator change')
            self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
            self.assertEqual((d / 'user-patches.sh').read_text(), 'administrator change')

    def test_bad_recipient_never_submitted(self):
        for address in ('-X/tmp/file@example.test', 'a@example.test\nBcc: b@else.test', 'missing@example.test'):
            with patch.object(s, 'command') as run:
                s.notify(address)
                run.assert_not_called()

    def test_cleanup_modified_file_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            file = d / 'module'
            file.write_bytes(b'changed')
            owned = d / 'owned.json'
            owned.write_text(json.dumps({str(file): hashlib.sha256(b'original').hexdigest()}))
            (d / 'manifest.json').write_text(json.dumps({'destinations': {str(file): 'module'}}))
            with patch.object(s, 'CONFIG', file), patch.object(s, 'OWNED', owned), patch.object(s, 'regular'):
                with self.assertRaises(s.Refusal):
                    s.cleanup()
                self.assertEqual(file.read_bytes(), b'changed')

    def test_target_package_and_fallback_policy(self):
        manifest = {'target': {'architecture': 'arm64', 'normal_packages': {'dovecot-core': 'qualified'}}}
        with patch.object(s, 'command', side_effect=['arm64', '2.4.1-4', 'changed']):
            with self.assertRaises(s.Refusal):
                s.compatible(manifest, 'normal')
        with patch.object(s, 'command', side_effect=['arm64', '2.4.1-4']) as run:
            s.compatible(manifest, 'fallback')
            self.assertEqual(run.call_count, 2)
        for arch, version in [('amd64', '2.4.1-4'), ('arm64', '2.5.0')]:
            with patch.object(s, 'command', side_effect=[arch, version]):
                with self.assertRaises(s.Refusal):
                    s.compatible(manifest, 'fallback')

    def test_artifact_corruption_and_path_whitelist(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'fallback').mkdir()
            rel = 'fallback/module'
            (d / rel).write_bytes(b'corrupt')
            m = {'implementations': {'fallback': [rel]}, 'files': {rel: hashlib.sha256(b'original').hexdigest()}}
            with patch.object(s, 'PACKAGE', d), patch.object(s, 'regular'):
                with self.assertRaises(s.Refusal):
                    s.integrity(m, 'fallback')
                (d / 'manifest.json').write_text(json.dumps({'format': 'sieve-expire-dms-installation-v1', 'destinations': {'/etc/passwd': rel}}))
                with self.assertRaises(s.Refusal):
                    s.checked_manifest()

    def test_no_notifications_for_normal_and_single_worker(self):
        # Both functions are invoked under container root in integration. Offline
        # controls establish no submission for a normal selection and atomic claim.
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'notification.json').write_text('{"recipient":"a@example.test","message":"test"}')
            (d / 'notification-worker-started').write_text('')
            with patch.object(s, 'STATE', d), patch.object(s, 'regular'), patch.object(s, 'command') as run:
                self.assertEqual(s.notification_worker(), 0)
                run.assert_not_called()

    def test_live_master_guard_precedes_changes(self):
        with patch.object(s.os, 'geteuid', return_value=0), \
                patch.object(Path, 'exists', lambda p: str(p) == '/run/dovecot/master.pid'), \
                patch.object(s, 'atomic') as write:
            self.assertEqual(s.main(), 1)
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
