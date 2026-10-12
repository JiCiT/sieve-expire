import argparse
import copy
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('manager',ROOT/'installer/manage.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
RELEASE=Path(os.environ.get('SIEVE_EXPIRE_TEST_RELEASE',str(ROOT/'installer/release')))

def args():
    a=m.parser().parse_args([]);a.operation='install';return a

class Controls(unittest.TestCase):
    def setUp(self):m.LOG_DIRECTORY=None
    def tearDown(self):m.LOG_DIRECTORY=None

    def test_invalid_report_refused_before_any_operation(self):
        with tempfile.TemporaryDirectory() as t:
            report=Path(t)/'missing'/'report.json'
            with patch.object(m.Manager,'execute') as operation,contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(m.main(['--report',str(report)]),2)
            operation.assert_not_called();self.assertIn('report parent missing',output.getvalue())

    def test_report_io_failure_still_reports_failure(self):
        with tempfile.TemporaryDirectory() as t:
            report=Path(t)/'report.json'
            with patch.object(m.Manager,'execute'),patch.object(m,'writejson',side_effect=OSError('simulated full disk')),contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(m.main(['--report',str(report)]),2)
            self.assertIn('report persistence failed',output.getvalue())



    def test_public_verifier_rejects_unqualified_host_source(self):
        spec=importlib.util.spec_from_file_location('qualification',ROOT/'installer/qualification.py')
        q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
        original=q.sha
        def changed(path):
            return 'changed' if Path(path)==ROOT/'installer/manage.py' else original(path)
        with patch.object(q,'sha',changed):
            with self.assertRaisesRegex(ValueError,'installer component changed'):q.verify(RELEASE)

    def test_public_verifier_rejects_changed_native_source(self):
        spec=importlib.util.spec_from_file_location('qualification',ROOT/'installer/qualification.py')
        q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
        original=q.sha
        def changed(path):
            return 'changed' if Path(path)==ROOT/'src/sieve-expire-plugin.c' else original(path)
        with patch.object(q,'sha',changed):
            with self.assertRaisesRegex(ValueError,'native source changed'):q.verify(RELEASE)

    def test_opaque_multiline_script_driver_retains_support(self):
        obj=m.Manager(args())
        with patch.object(obj,'exec',return_value='sieve_script opaque {\n  driver = dict\n}\n') as calls:
            self.assertIsNone(obj.dependency_check())
        self.assertEqual(calls.call_count,1)

    def test_remote_environment_refused_without_daemon_access(self):
        obj=m.Manager(args())
        with patch.dict(m.os.environ,{'DOCKER_HOST':'tcp://unreachable.invalid:2375','DOCKER_CONTEXT':''}),patch.object(obj,'docker') as calls:
            with self.assertRaisesRegex(m.Refusal,'local Unix'):obj.discover()
        calls.assert_not_called()

    def test_effective_imap_hibernation_rejected(self):
        manifest=json.loads((RELEASE/'manifest.json').read_text());obj=m.Manager(args())
        obj.inspect={'Image':manifest['target']['image_ids'][0],'Config':{'Env':['DMS_RELEASE='+manifest['target']['dms_version'],'ACCOUNT_PROVISIONER=FILE']}}
        def value(*argv,**kw):
            if argv[0]=='dpkg':return manifest['target']['architecture']
            if argv[0]=='dpkg-query':return manifest['target']['normal_packages'][argv[-1]]
            if argv[0]=='dovecot':return manifest['target']['runtime']
            if argv[0]=='sha256sum':return manifest['target']['runtime_files'][argv[-1]]+'  '+argv[-1]
            if argv[-1]=='mail_driver':return 'maildir'
            if argv[-1]=='mail_plugins':return ''
            if argv[-1]=='imap_hibernate_timeout':return '60 secs' if 'protocol=imap' in argv else '0 secs'
            raise AssertionError(argv)
        with patch.object(obj,'exec',side_effect=value):
            with self.assertRaisesRegex(m.Refusal,'hibernation'):obj.compatibility(manifest)

    def test_permissions_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as t:
            obj=m.Manager(args());obj.cfg=Path(t)
            with patch.object(m.os,'access',return_value=False):
                with self.assertRaisesRegex(m.Refusal,'not writable'):obj.state_setup()
            self.assertFalse((obj.cfg/'.sieve-expire-manager').exists())

    def test_directory_inventory_exclusion(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'.sieve-expire-manager').mkdir();(root/'.sieve-expire-manager'/'large-backup').write_text('not traversed');(root/'admin').write_text('preserve')
            data=m.inventory_excluding(root);self.assertIn('admin',data);self.assertFalse(any('.sieve-expire-manager' in key for key in data))

    def test_qualified_release_and_tampering(self):
        manifest,h=m.verify_release(RELEASE,True);self.assertEqual(manifest['gates']['D'],'PASS')
        with tempfile.TemporaryDirectory() as t:
            d=Path(t)/'release';shutil.copytree(RELEASE,d)
            (d/'normal/lib90_sieve_expire_plugin.so').write_bytes(b'tampered')
            with self.assertRaises(m.Refusal):m.verify_release(d,True)
            shutil.copyfile(RELEASE/'normal/lib90_sieve_expire_plugin.so',d/'normal/lib90_sieve_expire_plugin.so')
            obj=json.loads((d/'manifest.json').read_text());obj['gates']['D']='NOT TESTED';(d/'manifest.json').write_text(json.dumps(obj))
            with self.assertRaises(m.Refusal):m.verify_release(d,True)
    def test_no_path_or_symbol_relaxation(self):
        a=m.parser().parse_args(['--no-maintenance']);self.assertTrue(a.no_maintenance)
        with self.assertRaises(SystemExit):m.parser().parse_args(['--status','--uninstall'])
        self.assertFalse(m.parser().parse_args([]).no_maintenance)
    def test_symlink_private_and_atomic(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'other').mkdir();(root/'link').symlink_to(root/'other')
            with self.assertRaises(m.Refusal):m.private_directory(root/'link'/'new')
            (root/'file').write_text('original');(root/'out').symlink_to(root/'file')
            with self.assertRaises(m.Refusal):m.atomic(root/'out',b'overwrite')
            self.assertEqual((root/'file').read_text(),'original')
    def test_cleanup_preserves_unowned_and_backup(self):
        with tempfile.TemporaryDirectory() as t:
            a=args();obj=m.Manager(a);obj.state=Path(t);obj.work=None
            (obj.state/'runs').mkdir();(obj.state/'backups').mkdir();(obj.state/'backups'/'keep').write_text('backup')
            owned=obj.state/'runs'/'one';owned.mkdir();m.writejson(owned/'owner.json',{'kind':'sieve-expire-temporary-run-v1','run':'one'});(owned/'image.yml').write_text('temp');(owned/'REPORT.json').write_text('durable')
            unowned=obj.state/'runs'/'unrelated';unowned.mkdir();(unowned/'image.yml').write_text('unrelated')
            obj.cleanup();obj.cleanup()
            self.assertFalse((owned/'image.yml').exists());self.assertTrue((owned/'REPORT.json').exists())
            self.assertTrue((unowned/'image.yml').exists());self.assertEqual((obj.state/'backups'/'keep').read_text(),'backup')
    def test_unsupported_image_and_runtime(self):
        obj=m.Manager(args());obj.inspect={'Image':'sha256:unsupported'}
        manifest=json.loads((RELEASE/'manifest.json').read_text())
        with self.assertRaisesRegex(m.Refusal,'image not qualified'):obj.compatibility(manifest)
    def test_gate_d_missing_cannot_enable(self):
        # Signed NOT TESTED release is generated only in the runtime negative family.
        # Here the actual startup install precondition rejects absent D even if host
        # code were bypassed. No filesystem writes before the guard are acceptable.
        manifest=json.loads((RELEASE/'manifest.json').read_text());manifest['gates']['D']='NOT TESTED'
        with tempfile.TemporaryDirectory() as t:
            # Mock module writes to isolate the validation predicate; return empty
            # destinations so only the activation check is exercised.
            manifest['destinations']={};manifest['implementations']['normal']=[]
            with patch.object(m.startup,'CONFIG',Path(t)/'conf'):
                with self.assertRaisesRegex(m.startup.Refusal,'Gate D'):m.startup.install(manifest,'normal',True)
                self.assertFalse((Path(t)/'conf').exists())
    def test_install_lock_nonblocking(self):
        import fcntl
        with tempfile.TemporaryDirectory() as t:
            a=m.Manager(args());b=m.Manager(args())
            for obj in (a,b):
                obj.cfg=Path(t);obj.project='p';obj.service='s';obj.container='c';obj.projectdir=t;obj.composefiles=[]
            a.state_setup()
            try:
                with self.assertRaisesRegex(m.Refusal,'another installer'):b.state_setup()
            finally:a.lock.close();b.lock.close()
    def test_backup_space_refused_before_shutdown(self):
        with tempfile.TemporaryDirectory() as t:
            obj=m.Manager(args());obj.state=Path(t);(obj.state/'backups').mkdir();obj.work=Path(t)/'run';obj.work.mkdir();obj.cfg=Path(t)/'cfg';obj.cfg.mkdir();obj.inspect={'Image':'sha256:x'}
            calls=[]
            obj.docker=lambda *a,**kw: calls.append(a) or json.dumps([{'Size':1000}])
            obj.exec=lambda *a,**kw:'0 /var/mail\n0 /var/mail-state'
            with patch.object(m.shutil,'disk_usage',return_value=argparse.Namespace(free=0)):
                with self.assertRaisesRegex(m.Refusal,'insufficient'):obj.backup_create()
            self.assertEqual(calls,[('image','inspect','sha256:x')])

    def test_cold_tree_content_and_metadata(self):
        import socket
        spec=importlib.util.spec_from_file_location('cold_tree',ROOT/'installer/tree.py')
        tree=importlib.util.module_from_spec(spec);spec.loader.exec_module(tree)
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'source';source.mkdir(mode=0o750)
            original=source/'content';original.write_bytes(b'synthetic checkpoint payload')
            original.chmod(0o640);os.setxattr(original,'user.sieve-expire-test',b'preserved')
            os.link(original,source/'hardlink');(source/'symlink').symlink_to('content')
            os.mkfifo(source/'fifo',0o600)
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(str(source/'socket'))
                expected=m.inventory(source)
                self.assertEqual(tree.copy_tree(source,Path(t)/'copy',m.inventory),expected)
                self.assertEqual(m.inventory(source),expected)
            with self.assertRaisesRegex(ValueError,'already exists'):
                tree.copy_tree(source,Path(t)/'copy',m.inventory)

    def test_cold_tree_rejects_concurrent_change(self):
        spec=importlib.util.spec_from_file_location('cold_tree',ROOT/'installer/tree.py')
        tree=importlib.util.module_from_spec(spec);spec.loader.exec_module(tree)
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'source';source.mkdir();(source/'content').write_text('before')
            hits=[]
            def changing(path):
                data=m.inventory(path)
                if Path(path)==source:
                    hits.append(True)
                    if len(hits)==1:(source/'content').write_text('after')
                return data
            with self.assertRaisesRegex(ValueError,'source changed'):
                tree.copy_tree(source,Path(t)/'copy',changing)

if __name__=='__main__':unittest.main(verbosity=2)
