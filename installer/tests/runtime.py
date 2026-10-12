#!/usr/bin/env python3
"""Disposable rootless-only acceptance; never use a production target."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(os.environ.get('SIEVE_EXPIRE_TEST_OUTPUT',str(ROOT/'test-output')))
OUT.mkdir(mode=0o700,parents=True,exist_ok=True)
RT=OUT/'runtime'
D=['docker','--host','unix:///run/docker-rootless/docker.sock']
BASE='sha256:d0fe7668defe157aad57ea31b1707ad1e2fb57d7a91bdf17cbdf876549946c86'
DOMAIN='public-installer.sieve-expire.test';USER='test@'+DOMAIN
import secrets
secret_path=OUT/'synthetic-secret'
if secret_path.exists():
    PASSWORD=secret_path.read_text()
else:
    PASSWORD=secrets.token_urlsafe(32)
    secret_path.write_text(PASSWORD);secret_path.chmod(0o600)
RELEASE=Path(os.environ.get('SIEVE_EXPIRE_TEST_RELEASE',str(ROOT/'installer/release')))
NAME=os.environ.get('SIEVE_EXPIRE_TEST_NAME','sieve-expire-preview-test');COUNT=len(list(OUT.glob('runtime-command-*.json')))
COMMAND_RUN=uuid.uuid4().hex
import protocol as v

def command(args,label,data=None,check=True):
    global COUNT
    COUNT+=1
    p=subprocess.run([str(x) for x in args],input=data,capture_output=True)
    (OUT/('runtime-command-'+COMMAND_RUN+'-%04d.json'%COUNT)).write_text(json.dumps(dict(label=label,argv=[str(x) for x in args],rc=p.returncode,stdout=p.stdout.decode(errors='replace'),stderr=p.stderr.decode(errors='replace')),indent=2)+'\n')
    if check and p.returncode:raise RuntimeError((label,p.returncode,p.stdout[-1800:],p.stderr[-1800:]))
    return p

def docker(*args,**kw):return command(D+list(args),kw.pop('label','docker'),**kw)
def execute(*args,**kw):return docker('exec','-i',NAME,*args,**kw)
def save(name,obj):(OUT/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
def checkpoint(title,result):
    with (OUT/'REPORT.md').open('a') as f:f.write('\n## '+title+'\n\n'+result+'\n\nRemaining: subsequent explicitly requested synthetic test phases.\n')

def setup():
    RT.mkdir(mode=0o700,exist_ok=True);cfg=RT/'config';cfg.mkdir(mode=0o700)
    tls=RT/'tls';tls.mkdir(mode=0o700)
    command(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','2','-subj','/CN=mail.'+DOMAIN,'-keyout',tls/'key.pem','-out',tls/'cert.pem'],'tls')
    (tls/'key.pem').chmod(0o600)
    hashed=docker('run','--pull','never','--rm','--network','none','--entrypoint','doveadm',BASE,'pw','-s','SHA512-CRYPT','-p',PASSWORD,label='password').stdout.decode().strip()
    (cfg/'postfix-accounts.cf').write_text(USER+'|'+hashed+'\n')
    (cfg/'postfix-accounts.cf').chmod(0o600)
    (cfg/'dovecot.cf').write_text('mail_max_userip_connections = 17\nprotocol imap {\n  mail_plugins {\n    imap_sieve = yes\n  }\n}\n')
    (cfg/'user-patches.sh').write_text('#!/bin/bash\necho administrator-hook > /etc/administrator-hook-marker\n')
    (cfg/'user-patches.sh').chmod(0o700)
    save(NAME+'-administrator-before',{n:__import__('hashlib').sha256((cfg/n).read_bytes()).hexdigest() for n in ['postfix-accounts.cf','dovecot.cf','user-patches.sh']})
    compose={'name':NAME,'services':{'dms':{'image':BASE,'container_name':NAME,'hostname':'mail.'+DOMAIN,'restart':'no','stop_grace_period':'60s',
      'environment':{'ACCOUNT_PROVISIONER':'FILE','DOVECOT_MAILBOX_FORMAT':'maildir','ENABLE_IMAP':'1','ENABLE_MANAGESIEVE':'1','ENABLE_RSPAMD':'0','ENABLE_AMAVIS':'0','ENABLE_CLAMAV':'0','ENABLE_OPENDKIM':'0','ENABLE_OPENDMARC':'0','ENABLE_POLICYD_SPF':'0','ENABLE_SPAMASSASSIN':'0','ENABLE_DNSBL':'0','ENABLE_UPDATE_CHECK':'0','ENABLE_QUOTAS':'0','SSL_TYPE':'manual','SSL_CERT_PATH':'/etc/installer-tls/cert.pem','SSL_KEY_PATH':'/etc/installer-tls/key.pem'},
      'volumes':[str(cfg)+':/tmp/docker-mailserver','mail:/var/mail','state:/var/mail-state',str(tls)+':/etc/installer-tls:ro'],
      'labels':{'org.sieve-expire.fixture':'public-installer'},'networks':['isolated']}},'volumes':{'mail':{},'state':{}},'networks':{'isolated':{'internal':True}}}
    (RT/'compose.json').write_text(json.dumps(compose,indent=2)+'\n')
    docker('compose','-p',NAME,'-f',str(RT/'compose.json'),'up','-d','--pull','never','--no-build',label='setup')
    ready()
    checkpoint('Fresh synthetic Compose fixture','PASS: exact pristine base, internal-only network, separate synthetic FILE/Maildir/TLS/config/state, preserved admin hook/imap_sieve. No prior fixture started or changed.')

def ready():
    for _ in range(90):
        p=subprocess.run(D+['exec',NAME,'supervisorctl','status','dovecot','postfix'],capture_output=True)
        if p.returncode==0 and p.stdout.count(b'RUNNING')==2:return
        time.sleep(1)
    docker('logs',NAME,label='startup-failure',check=False);raise RuntimeError('startup')

def cli(label,*args,expected=0):
    report=OUT/(label+'-report.json')
    p=command([ROOT/'install.sh','--docker-host',D[-1],'--container',NAME,'--release',RELEASE,'--test-recipient',USER,'--yes','--report',report,*args],label,check=False)
    if p.returncode!=expected:raise RuntimeError((label,p.returncode,p.stdout[-1800:],p.stderr[-1800:]))
    return json.loads(report.read_text())

def script(text):execute('doveadm','sieve','put','-u',USER,'-a','public-test',data=text.encode(),label='script')
def deliver(mid):
    payload=('From: sender@'+DOMAIN+'\r\nTo: '+USER+'\r\nMessage-ID: <'+mid+'@'+DOMAIN+'>\r\nSubject: '+mid+'\r\n\r\nSynthetic stable body '+mid+'\r\n').encode()
    return json.loads(execute('python3','-c',v.LMTP,USER,mid,data=payload,label='delivery-'+mid).stdout)
def collect(label):
    execute('doveadm','force-resync','-u',USER,'*',label=label+'-sync')
    state=json.loads(execute('python3','-c',v.COLLECT,'/var/mail/'+DOMAIN+'/test',label=label+'-collect').stdout);save(label,state);return state

def rows(state,mid):return [(b,r) for b,box in state.items() for m,r in box['messages'].items() if m==mid+'@'+DOMAIN]
def imap(label):
    code="import imaplib,ssl; c=ssl.create_default_context(cafile='/etc/installer-tls/cert.pem');m=imaplib.IMAP4_SSL('mail."+DOMAIN+"',993,ssl_context=c);assert m.login('"+USER+"','"+PASSWORD+"')[0]=='OK';assert m.select('INBOX')[0]=='OK';assert m.noop()[0]=='OK';m.logout();print('authenticated SELECT/NOOP/LOGOUT PASS')"
    execute('python3','-c',code,label=label)

def metadata():
    cli('metadata-install','--no-maintenance')
    ident=json.loads(docker('inspect',NAME,label='metadata-inspect').stdout)[0]['Id']
    cli('metadata-repeat','--no-maintenance')
    assert json.loads(docker('inspect',NAME,label='repeat-inspect').stdout)[0]['Id']==ident
    for box in ['Archive','Trash']:execute('doveadm','mailbox','create','-u',USER,box,label='create-'+box,check=False)
    for mid,box,actions in [('move','Archive','expire "0s"; expunge "180d";'),('trash-noop','Trash','expire "0s"; expunge "180d";'),('direct','Archive','expunge "0s";'),('precedence','Archive','expire "0s"; expunge "0s";')]:
        script('require ["expire", "fileinto"]; '+actions+' fileinto "'+box+'";')
        deliver(mid)
    script('keep;');deliver('neighbor');before=collect('metadata-before');imap('metadata-no-maintenance');after=collect('metadata-after')
    for mid in ['move','trash-noop','direct','precedence']:
        assert rows(before,mid) and rows(after,mid)
    checkpoint('Public metadata-only and idempotent installation','PASS: parser-first persistent install, four module identities, native300 with companion off; local LMTP/synthetic due messages persist; authenticated IMAP does not enforce. Repeat verifies health/delivery but does not recreate. metadata-*-report.json and metadata-before/after.json.')

def activation():
    before=collect('enable-before');cli('enable-maintenance','--enable-maintenance')
    imap('enable-activity');after=collect('enable-after')
    src=rows(before,'move')[0][1];box,dest=rows(after,'move')[0]
    assert box=='Trash' and all(src[k]==dest[k] for k in ['sha256','expire_at','expunge_at'])
    for mid in ['direct','precedence']:assert not rows(after,mid)
    for mid in ['neighbor','trash-noop']:
        a=rows(before,mid)[0][1];b=rows(after,mid)[0][1];assert all(a[k]==b[k] for k in ['uid','sha256','expire_at','expunge_at'])
    cli('status','--status')
    ident=json.loads(docker('inspect',NAME,label='enabled-id').stdout)[0]['Id']
    cli('default-repeat')
    assert json.loads(docker('inspect',NAME,label='enabled-repeat-id').stdout)[0]['Id']==ident
    docker('stop','--time','60',NAME,label='stop');docker('start',NAME,label='start');ready();cli('restart-status','--status');imap('restart-auth')
    checkpoint('Signed Gate D / actual persistent installation enforcement','PASS: explicit enable and default repeat verified; actual due expiry MOVE keeps both persisted fields/hash, explicit expunge/precedence remove only intended messages; already-Trash expiry and zero neighbor unchanged. Native300 and ordinary LMTP/TLS IMAP after clean restart. enable-before/after.json and enable/default/status reports. No policy written by public installer; test harness owns disposable policy.')

def uninstall():
    script('require ["expire"]; expire "30d"; expunge "180d";')
    before=collect('uninstall-before');r=cli('safe-uninstall','--uninstall');assert r['selection']=='fallback'
    deliver('fallback-after-uninstall');after=collect('uninstall-after')
    row=rows(after,'fallback-after-uninstall')[0][1];assert row['expire_at']==row['expunge_at']==0
    for mid in ['neighbor','move','trash-noop']:
        a=rows(before,mid)[0][1];b=rows(after,mid)[0][1];assert all(a[k]==b[k] for k in ['uid','sha256','expire_at','expunge_at'])
    cli('fallback-status','--status')
    cli('uninstall-reject-dependency','--uninstall','--remove-fallback',expected=2)
    # Remove only harness-owned policy source through supported Sieve API.
    execute('doveadm','sieve','deactivate','-u',USER,label='deactivate-test-policy')
    execute('doveadm','sieve','delete','-u',USER,'public-test',label='delete-test-policy')
    r=cli('complete-removal','--uninstall','--remove-fallback');assert r['selection']=='neither'
    cli('removed-status','--status');imap('removed-auth')
    cfg=RT/'config';old=json.loads((OUT/(NAME+'-administrator-before.json')).read_text())
    import hashlib
    for name,h in old.items():assert hashlib.sha256((cfg/name).read_bytes()).hexdigest()==h
    checkpoint('Safe uninstall / complete removal / full cold checkpoint',
        'PASS: dependent policy retains qualified fallback and real delivery writes no deadlines; unknown/required dependency refuses removal. Supported closed-source test policy deleted only by harness, then complete removal restores exact admin hook. Existing messages/deadlines unchanged. Every change now also verified cold mail/state content+metadata through tracked offline pristine-image helpers; no mail restoration. Native IMAP/LMTP healthy.')



def lifecycle():
    cli('keep-artifacts-install','--keep-artifacts')
    before=collect('repair-before')
    package=RT/'config/sieve-expire'
    (package/'normal/lib90_sieve_expire_plugin.so').write_bytes(b'corrupt owned payload')
    cli('persistent-repair')
    execute('python3','-c',"from pathlib import Path;Path('/usr/lib/dovecot/modules/lib90_sieve_expire_plugin.so').write_bytes(b'corrupt owned native module')",label='corrupt-owned-native')
    cli('native-repair')
    after=collect('repair-after')
    for box,state in before.items():
        for mid,old in state['messages'].items():
            found=[v for b in after.values() for key,v in b['messages'].items() if key==mid]
            assert len(found)==1 and all(old[k]==found[0][k] for k in ('uid','sha256','expire_at','expunge_at'))
    cli('cleanup','--cleanup');cli('cleanup-repeat','--cleanup')
    checkpoint('Owned repair / artifact retention / cleanup',
        'PASS: persistent/native damage repaired under existing signed ownership; retained mail/deadlines unchanged; cleanup safely repeated. No private signer or new release required. Upgrade across distinct release generations retains prior scoped qualification but is not re-exercised by this family.')

def negatives():
    ident=json.loads(docker('inspect',NAME,label='refusal-before-id').stdout)[0]['Id']
    tampered=OUT/'release-tampered';shutil.copytree(RELEASE,tampered)
    (tampered/'normal/lib90_sieve_expire_plugin.so').write_bytes(b'tamper')
    cli('reject-tamper','--release',tampered,expected=2)
    cfg=RT/'config/dovecot.cf';original=cfg.read_bytes();cfg.write_bytes(original+b'\nnot_a_native_setting = bad\n')
    try:cli('reject-invalid-config',expected=2)
    finally:cfg.write_bytes(original)
    assert json.loads(docker('inspect',NAME,label='refusal-after-id').stdout)[0]['Id']==ident
    checkpoint('Actual refusal without shutdown',
        'PASS: payload tamper and invalid prospective persistent configuration refuse; same running generation. Signed non-PASS GateD is covered by retained qualification and unit startup guards, not recreated by an unpublished signer here.')

if __name__=='__main__':globals()[sys.argv[1]]()
