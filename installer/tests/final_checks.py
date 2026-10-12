#!/usr/bin/env python3
"""Final-source transition/recovery checks on the disposable fixture only."""
import importlib.util
import json
import sys
from unittest.mock import patch
import runtime as r

def module():
    spec=importlib.util.spec_from_file_location('manager',r.ROOT/'installer/manage.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def age():
    path='/var/mail/'+r.DOMAIN+'/test/home/dovecot.sieve-expire-maintenance.lock.cooldown'
    r.execute('python3','-c','import pathlib,time,sys;pathlib.Path(sys.argv[1]).write_text("v1 %d"%(int(time.time())-301)+chr(10))',path,label='synthetic-valid-cooldown-aging')

def transitions():
    # Earlier enable activity was correctly suppressed by persisted native300.
    before=r.collect('final-admitted-before');age();r.imap('final-admitted-auth');after=r.collect('final-admitted-after')
    box,moved=r.rows(after,'final-disabled-due')[0];old=r.rows(before,'final-disabled-due')[0][1]
    assert box=='Trash' and all(old[k]==moved[k] for k in ('sha256','expire_at','expunge_at'))
    r.execute('python3','-c',"from pathlib import Path;Path('/usr/lib/dovecot/modules/sieve/lib90_sieve_expire_plugin.so').unlink()",label='final-missing-owned-module')
    r.cli('final-missing-module-repair');r.imap('final-repaired-auth')
    r.checkpoint('Final transitions, cooldown and missing-module repair',
      'PASS: final disabled/enable reports correctly changed companion state; first enable activity was suppressed by persisted native300, not a defect. After valid synthetic state aging, real TLS IMAP moved intended content and both unchanged deadlines. Missing positively owned native module repaired using final signed code with full native/protocol checks. final-admitted-before/after and final-missing-module-repair-report.json; first failed assertion preserved in final-transitions.stderr.')

def removal_failure():
    r.cli('final-removal-metadata-base','--no-maintenance')
    # Delete only harness-owned policy so complete-removal dependency closure is safe.
    r.execute('doveadm','sieve','deactivate','-u',r.USER,label='final-deactivate-harness-policy')
    r.execute('doveadm','sieve','delete','-u',r.USER,'public-test',label='final-delete-harness-policy')
    before=r.collect('final-removal-before')
    m=module();original=m.Manager.health;hit=[]
    def injected(obj,*args,**kw):
        value=original(obj,*args,**kw)
        if obj.saved and obj.saved['phase']=='staged' and not hit:
            assert obj.saved['staged_paths'] and 'user-patches.sh' in obj.saved['staged_paths']
            hit.append(obj.report['delivery']['message_id'])
            raise m.Refusal('test-only final complete-removal verification failure')
        return value
    args=['--docker-host',r.D[-1],'--container',r.NAME,'--release',str(r.RELEASE),'--test-recipient',r.USER,'--yes','--uninstall','--remove-fallback','--report',str(r.OUT/'final-removal-rollback-report.json')]
    with patch.object(m.Manager,'health',injected):assert m.main(args)==2
    report=json.loads((r.OUT/'final-removal-rollback-report.json').read_text())
    assert report['rollback']=='PASS' and report['maintenance'] is False and report['automatic_expiration'] is False and report['operational_state']=='metadata-only' and report['health']=='PASS'
    after=r.collect('final-removal-after');assert any(hit[0] in box['messages'] for box in after.values())
    for box,data in before.items():
        for mid,row in data['messages'].items():
            assert all(after[box]['messages'][mid][key]==row[key] for key in ('uid','sha256','expire_at','expunge_at'))
    r.cli('final-removal-rollback-status','--status');r.imap('final-removal-rollback-auth')
    r.cli('final-public-default');r.cli('final-public-default-repeat');r.cli('final-public-status','--status')
    r.checkpoint('Final complete-removal journal and rollback reporting',
       'PASS: complete removal journals owned changed paths at the install staged boundary, enabling external-edit protection/interruption recovery. Real injected post-health removal failure restored original metadata-only software and accurately reports maintenance flags false, health/rollback PASS, overallFAIL. New and old mail/deadlines unchanged. Final default/repeat/status verified normal/native300. final-removal-rollback-report and before/after inventories. No mail/state restoration.')

if __name__=='__main__':globals()[sys.argv[1]]()
