#!/usr/bin/env python3
"""Focused real rootless operations with test-only lifecycle fault injection."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest.mock import patch

import runtime as r
spec=importlib.util.spec_from_file_location('manager',r.ROOT/'installer/manage.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def argv(label,*extra):
    return ['--docker-host',r.D[-1],'--container',r.NAME,'--release',str(r.RELEASE),
            '--test-recipient',r.USER,'--yes','--report',str(r.OUT/(label+'-report.json')),*extra]


def verification_failure():
    r.cli('fault-base-metadata','--no-maintenance')
    before=r.collect('rollback-before')
    original=m.Manager.health;hit=[]
    def injected(obj,*args,**kw):
        value=original(obj,*args,**kw)
        if obj.saved and obj.saved['phase']=='staged' and not hit:
            hit.append(obj.report.get('delivery'))
            raise m.Refusal('test-only mandatory post-install check failure')
        return value
    with patch.object(m.Manager,'health',injected):
        assert m.main(argv('verification-failure'))==2
    report=json.loads((r.OUT/'verification-failure-report.json').read_text())
    assert report['rollback']=='PASS'
    after=r.collect('rollback-after')
    new=hit[0]['message_id'];assert any(new in box['messages'] for box in after.values())
    for mid in ['neighbor','move','trash-noop']:
        a=r.rows(before,mid)[0][1];b=r.rows(after,mid)[0][1]
        assert all(a[k]==b[k] for k in ['uid','sha256','expire_at','expunge_at'])
    r.cli('rollback-status','--status');r.imap('rollback-auth')
    r.checkpoint('Actual software rollback after mandatory verification failure',
      'PASS: test-only injected failure follows successful new-generation native/LMTP checks; real cold backup/config restore/exact-image recreation and verified rollback complete. Newly delivered message remains physically present with all older identity/deadline neighbors unchanged. No mail/state restoration. verification-failure-report.json and rollback-before/after.json.')


def startup_failure():
    orig=m.Manager.recreate;hit=[]
    def injected(obj,backup=False):
        value=orig(obj,backup)
        if not backup and not hit:
            hit.append(True);obj.docker('stop','--time','1',obj.container)
        return value
    with patch.object(m.Manager,'recreate',injected):
        assert m.main(argv('startup-failure','--startup-timeout','3'))==2
    report=json.loads((r.OUT/'startup-failure-report.json').read_text())
    assert report['rollback']=='PASS';r.cli('startup-recovered-status','--status')
    r.checkpoint('Real replacement-container startup interruption',
      'PASS: isolated injector stopped replacement generation before readiness; installer timed out and recreated protected original configuration/image. Mandatory rollback health/IMAP/LMTP passed; no persistent mail restoration. startup-failure-report.json.')


def failed_rollback():
    original=m.Manager.health;hit=[]
    def badhealth(obj,*args,**kw):
        original(obj,*args,**kw)
        if obj.saved and obj.saved['phase']=='staged' and not hit:
            hit.append(True);raise m.Refusal('test-only health failure')
    recreate=m.Manager.recreate
    def badrecreate(obj,backup=False):
        if backup:raise m.Refusal('test-only recovery recreation unavailable')
        return recreate(obj,backup)
    with patch.object(m.Manager,'health',badhealth),patch.object(m.Manager,'recreate',badrecreate):
        assert m.main(argv('failed-rollback'))==3
    report=json.loads((r.OUT/'failed-rollback-report.json').read_text())
    assert report['rollback']=='INCOMPLETE' and report['administrator_action']
    # Failure leaves a stopped, journaled target. Real unpatched CLI must recover it.
    r.cli('failed-rollback-resume')
    r.checkpoint('Failed rollback reporting / actual resumed recovery',
      'PASS: test-only recovery Compose refusal reports exit3/INCOMPLETE/actionable protected checkpoint instead of success. Stopped target resumed via ordinary unpatched CLI, exact original software recovered and requested state installed/verified. Mail never restored. failed-rollback and failed-rollback-resume reports.')


def interrupted():
    r.cli('interruption-base','--no-maintenance')
    # Actual SIGKILL after atomic staged journal, before replacement generation.
    code="""import sys,os,signal
sys.path.insert(0,sys.argv[1]);import manage
old=manage.Manager.stage
def killed(obj,desired):
 old(obj,desired);os.kill(os.getpid(),signal.SIGKILL)
manage.Manager.stage=killed
manage.main(sys.argv[2:])
"""
    result=r.command([sys.executable,'-B','-c',code,r.ROOT/'installer',*argv('interrupted')], 'actual-sigkill',check=False)
    assert result.returncode==-signal.SIGKILL
    tx=json.loads((r.RT/'config/.sieve-expire-manager/transaction.json').read_text());assert tx['phase']=='staged'
    r.save('interruption-journal',tx)
    r.cli('interruption-resume');r.imap('interruption-auth')
    r.checkpoint('Actual interrupted installation / resume',
      'PASS: SIGKILL after full verified cold backup and staged journal leaves DMS stopped, no commit marker. Unpatched CLI detects and recovers protected original software before completing requested default; service/LMTP/TLS IMAP pass. interruption-journal.json and interruption-resume-report.json. This is a specific stage-boundary interruption, not power-loss durability qualification.')

if __name__=='__main__':globals()[sys.argv[1]]()
