#!/usr/bin/env python3
"""Qualified offline DMS Compose installer. Never builds images or restores mail."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deployment'))
from fsops import inventory
from deploy import same_bind_source, Refusal as RecoveryRefusal
sys.path.insert(0, str(ROOT/'dms-installation'))
import startup

class Refusal(Exception): pass
LOG_DIRECTORY = None

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def jsonread(p): return json.loads(p.read_text())
def private_directory(p):
    p=Path(p)
    for parent in [p, *p.parents]:
        if parent.is_symlink(): raise Refusal('symlink directory: '+str(parent))
    if p.exists() and (not p.is_dir() or p.stat().st_uid != os.geteuid()):
        raise Refusal('directory not owned by installer user: '+str(p))
    p.mkdir(parents=True, mode=0o700, exist_ok=True)
    p.chmod(0o700)
    return p

def atomic(p, data):
    p=Path(p)
    if p.is_symlink(): raise Refusal('symlink managed destination')
    fd,n=tempfile.mkstemp(prefix='.write-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(n,0o600);os.replace(n,p)
    finally:
        if os.path.exists(n): os.unlink(n)

def writejson(p, obj): atomic(p,(json.dumps(obj,indent=2)+'\n').encode())

def run(argv, timeout=90, data=None):
    p=subprocess.Popen([str(x) for x in argv], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try: out,err=p.communicate(data,timeout=timeout)
    except (subprocess.TimeoutExpired, BaseException):
        os.killpg(p.pid,signal.SIGKILL);p.communicate();raise
    if LOG_DIRECTORY is not None:
        writejson(LOG_DIRECTORY/('command-'+uuid.uuid4().hex+'.json'),dict(argv=[str(x) for x in argv],returncode=p.returncode,stdout=out.decode(errors='replace'),stderr=err.decode(errors='replace')))
    if p.returncode: raise Refusal('command failed ('+str(p.returncode)+'): '+str(argv[0]))
    return out.decode(errors='replace')


def verify_release(path, maintenance, check_components=True):
    path=Path(path).absolute()
    if path.is_symlink(): raise Refusal('symlink release root')
    # Trusted distribution key, separate from the untrusted release payload.
    if sha(ROOT/'installer/release-key.pem') != startup.TRUSTED_KEY_SHA256:
        raise Refusal('trusted distributor key changed')
    run(['openssl','dgst','-sha256','-verify',ROOT/'installer/release-key.pem',
         '-signature',path/'manifest.sig',path/'manifest.json'])
    m=jsonread(path/'manifest.json')
    if m.get('format') != startup.PUBLIC_FORMAT: raise Refusal('unsupported release manifest')
    if any(m.get('gates',{}).get(g) != 'PASS' for g in 'ABC'):
        raise Refusal('release metadata qualification incomplete')
    if maintenance and m['gates'].get('D') != 'PASS': raise Refusal('Gate D not qualified')
    if m['destinations'] != startup.DESTINATIONS: raise Refusal('unknown release installation paths')
    required=set(startup.DESTINATIONS.values())|{'startup.py'}
    if set(m['files']) != required: raise Refusal('incomplete release')
    names=set()
    for base,dirs,files in os.walk(path,followlinks=False):
        if any((Path(base)/n).is_symlink() for n in dirs):raise Refusal('symlink release directory')
        for n in files:
            item=Path(base)/n
            if item.is_symlink():raise Refusal('symlink release member')
            names.add(str(item.relative_to(path)))
    if names != required|{'manifest.json','manifest.sig','release-key.pem','options.json'}:
        raise Refusal('unexpected release member')
    for rel,h in m['files'].items():
        p=path/rel
        if p.is_symlink() or not p.is_file() or sha(p)!=h: raise Refusal('tampered artifact: '+rel)
    if sha(path/'release-key.pem') != sha(ROOT/'installer/release-key.pem'):
        raise Refusal('release verification key mismatch')
    for rel,h in (m.get('installer_components',{}) if check_components else {}).items():
        if rel not in {'install.sh','installer/manage.py','installer/tree.py','dms-installation/integrate.py','deployment/fsops.py','deployment/archive.py','deployment/deploy.py'}:
            raise Refusal('unknown installer component')
        if sha(ROOT/rel)!=h:raise Refusal('installer changed since release qualification: '+rel)
    return m,sha(path/'manifest.json')


# Read-only protocol test. A unique harmless message is retained, never expunged.
LMTP_SMOKE = r'''
import socket,sys,uuid,json
account=sys.argv[1]; mid='sieve-expire-install-'+uuid.uuid4().hex+'@installer.invalid'
s=socket.socket(socket.AF_UNIX);s.settimeout(20);s.connect('/run/dovecot/lmtp');f=s.makefile('rb')
def reply():
 rows=[]
 while True:
  line=f.readline();rows.append(line)
  if not line or len(line)<4:raise RuntimeError('LMTP EOF')
  if line[3:4]!=b'-':return rows
assert reply()[-1].startswith(b'220')
for request in ['LHLO installer.invalid','MAIL FROM:<>','RCPT TO:<'+account+'>','DATA']:
 s.sendall((request+'\r\n').encode());r=reply()[-1]
 assert r.startswith(b'354' if request=='DATA' else b'250'),r
s.sendall(('From: installer@installer.invalid\r\nTo: '+account+'\r\nMessage-ID: <'+mid+'>\r\nSubject: sieve-expire installation verification\r\nAuto-Submitted: auto-generated\r\n\r\nHarmless local installation verification.\r\n.\r\n').encode())
assert reply()[-1].startswith(b'250');s.sendall(b'QUIT\r\n');reply();s.close();print(json.dumps({'message_id':mid,'delivered':True}))
'''

class Manager:
    def __init__(self, a):
        self.a=a; self.report={'operation':a.operation,'overall':'FAIL','checks':[],
            'warnings':[], 'administrator_action':[], 'rollback':'not-needed','backup':'not-needed',
            'cleanup':'not-needed'}
        self.container=None;self.state=None;self.work=None;self.lock=None
        self.d=['docker']+(['--host',a.docker_host] if a.docker_host else [])
        self.saved=None
    def check(self,name,status='PASS',**extra): self.report['checks'].append(dict(name=name,status=status,**extra))
    def docker(self,*args,**kw): return run(self.d+list(args),**kw)
    def exec(self,*args,**kw):return self.docker('exec','-i',self.container,*args,**kw)
    def compose(self,*args, backup=False):
        files=[self.backup/'compose.original.json'] if backup else self.composefiles
        cmd=self.d+['compose','--project-directory',self.projectdir,'-p',self.project]
        for p in files:cmd+=['-f',str(p)]
        if backup: cmd+=['-f',str(self.backup/'recovery.yml')]
        return run(cmd+list(args),timeout=180)
    def discover(self):
        # Docker's environment can select a daemon independently of the stored
        # current context. Reject nonlocal DOCKER_HOST before contacting it.
        endpoint=self.a.docker_host
        if not endpoint:
            if os.environ.get('DOCKER_CONTEXT'):
                endpoint=self.docker('context','inspect',os.environ['DOCKER_CONTEXT'],'--format','{{.Endpoints.docker.Host}}').strip()
            elif os.environ.get('DOCKER_HOST'):
                endpoint=os.environ['DOCKER_HOST']
            else:endpoint=self.docker('context','inspect','--format','{{.Endpoints.docker.Host}}').strip()
        if not endpoint.startswith('unix://'):raise Refusal('only local Unix Docker daemons supported')
        if self.a.container:self.container=self.a.container
        else:
            ids=self.docker('ps','-q','--filter','label=com.docker.compose.service').split()
            candidates=[]
            for i in ids:
                obj=json.loads(self.docker('inspect',i))[0]
                if obj['Config'].get('Env') and any(v.startswith('DMS_RELEASE=') for v in obj['Config']['Env']):candidates.append(i)
            if len(candidates)!=1:raise Refusal('target ambiguous or absent; specify --container')
            self.container=candidates[0]
        self.inspect=json.loads(self.docker('inspect',self.container))[0]
        self.container=self.inspect['Name'].lstrip('/')
        labels=self.inspect['Config'].get('Labels') or {}
        self.project=labels.get('com.docker.compose.project');self.service=labels.get('com.docker.compose.service')
        if not self.project or not self.service:raise Refusal('supported Compose target required')
        # Interrupted targets may be stopped; the protected transaction is recovered
        # before attempting runtime compatibility or accepting another change.
        mounts=self.inspect['Mounts']; matches=[m for m in mounts if m['Destination']=='/tmp/docker-mailserver']
        if len(matches)!=1 or matches[0]['Type']!='bind' or not matches[0]['RW']:raise Refusal('writable bind DMS config required')
        self.cfg=Path(matches[0]['Source'])
        if self.cfg.is_symlink() or not self.cfg.is_dir():raise Refusal('config path unsupported')
        for p in [self.cfg,*self.cfg.parents]:
            if p.is_symlink():raise Refusal('symlink config parent')
        self.projectdir=labels.get('com.docker.compose.project.working_dir')
        names=self.a.compose_file or labels.get('com.docker.compose.project.config_files','').split(',')
        target=self.cfg/'.sieve-expire-manager/target.json'
        if not self.a.compose_file and target.exists():
            if target.is_symlink():raise Refusal('symlink target registry')
            stored=jsonread(target)
            if stored['project']!=self.project or stored['service']!=self.service or stored['container']!=self.container:
                raise Refusal('target ownership identity drift')
            names=stored['composefiles'];self.projectdir=stored['projectdir']
        self.composefiles=[Path(n).absolute() for n in names if n]
        if not self.projectdir or not self.composefiles:raise Refusal('Compose input discovery incomplete')
        if any(p.is_symlink() or not p.is_file() for p in self.composefiles):raise Refusal('invalid Compose input path')
        self.resolved=json.loads(self.compose('config','--format','json'))
        # Compare all stores/access before recreation; no path resolution to conceal symlinks.
        service=self.resolved['services'][self.service]
        configured={v['target']:v for v in service.get('volumes',[])}
        for m in mounts:
            v=configured.get(m['Destination'])
            if not v or v['type']!=m['Type'] or bool(v.get('read_only',False)) != (not m['RW']):raise Refusal('Compose mount/access drift')
            if m['Type']=='bind':
                try:equal=same_bind_source(v['source'],m['Source'])
                except RecoveryRefusal as e:raise Refusal(str(e)) from e
                if not equal:raise Refusal('Compose bind path drift')
            if m['Type']=='volume' and self.resolved.get('volumes',{}).get(v['source'],{}).get('name')!=m['Name']:
                raise Refusal('Compose named-volume identity drift')
        if len(configured)!=len(mounts):raise Refusal('Compose mount catalog drift')
        current_env=dict(v.split('=',1) for v in self.inspect['Config'].get('Env',[]) if '=' in v)
        if any(value is None or str(value)!=current_env.get(name) for name,value in service.get('environment',{}).items()):
            raise Refusal('Compose environment drift')
        original_policy=self.inspect['HostConfig']['RestartPolicy']
        restart=original_policy['Name']
        if restart=='on-failure' and original_policy['MaximumRetryCount']:restart+=':'+str(original_policy['MaximumRetryCount'])
        if service.get('restart','no')!=restart:raise Refusal('Compose restart-policy drift')
        base_config=json.loads(self.docker('image','inspect',self.inspect['Image']))[0]['Config']
        if any(self.inspect['Config'].get(k)!=base_config.get(k) for k in ('Entrypoint','Cmd')):
            raise Refusal('nonstandard DMS startup command unsupported')
        if self.a.operation not in ('status','cleanup'):
            # No other container may write the affected config tree during backup/update.
            for i in self.docker('ps','-q').split():
                o=json.loads(self.docker('inspect',i))[0]
                if o['Id']==self.inspect['Id']:continue
                for m in o.get('Mounts',[]):
                    if m['RW']:
                        for ours in mounts:
                            if ours['Destination'] not in ('/tmp/docker-mailserver','/var/mail','/var/mail-state'):continue
                            if m['Type']==ours['Type']=='volume' and m['Name']==ours['Name']:raise Refusal('other persistent-data writer')
                            if m['Type']==ours['Type']=='bind':
                                a=Path(ours['Source']);b=Path(m['Source'])
                                if a==b or a in b.parents or b in a.parents:raise Refusal('other persistent-data writer')
        self.report['target']={'container':self.container,'project':self.project,'service':self.service,'image':self.inspect['Image']}
        self.check('target-discovery')
    def compatibility(self,m):
        if self.inspect['Image'] not in m['target']['image_ids']:raise Refusal('image not qualified; image tags/version strings are insufficient')
        if self.exec('dpkg','--print-architecture').strip()!=m['target']['architecture']:raise Refusal('architecture mismatch')
        for name,expected in m['target']['normal_packages'].items():
            if self.exec('dpkg-query','-W','-f=${Version}',name).strip()!=expected:raise Refusal('package/build mismatch')
        if self.exec('dovecot','--version').strip()!=m['target']['runtime']:raise Refusal('runtime mismatch')
        for path,h in m['target'].get('runtime_files',{}).items():
            if self.exec('sha256sum',path).split()[0]!=h:raise Refusal('runtime ABI/build file changed: '+path)
        env=dict(v.split('=',1) for v in self.inspect['Config']['Env'] if '=' in v)
        if env.get('DMS_RELEASE')!=m['target']['dms_version'] or env.get('ACCOUNT_PROVISIONER','FILE')!='FILE':raise Refusal('unqualified DMS/account provisioner')
        if not getattr(self,'native_repair',False):
            if self.exec('doveconf','-h','mail_driver').strip()!='maildir':raise Refusal('storage not qualified')
            plugins=self.exec('doveconf','-f','protocol=imap','-h','mail_plugins')
            names=set(re.findall(r'^\s*(\S+)\s*=\s*yes\s*$',plugins,re.M))
            allowed={'sieve_expire','imap_sieve_expire','quota','imap_quota','imap_sieve'}
            if names-allowed:raise Refusal('unknown active IMAP plugin ownership')
            if self.exec('doveconf','-f','protocol=imap','-h','imap_hibernate_timeout').strip() not in ('0','0 secs'):
                raise Refusal('enabled hibernation unqualified for this release')
        self.report['compatibility']='PASS';self.check('target-compatibility')
    def future_configuration(self):
        # DMS copies dovecot.cf to local.conf only in a fresh generation. Parse the
        # prospective include graph now without overwriting generated/live config.
        code=r"""
import pathlib,re,subprocess,tempfile
base=pathlib.Path('/etc/dovecot')
future=pathlib.Path('/tmp/docker-mailserver/dovecot.cf')
def absolute_includes(text):
 result=[]
 for line in text.splitlines():
  m=re.match(r'(\s*!include(?:_try)?\s+)([^#]+)',line)
  if m:
   name=m.group(2).strip().strip('\"\'')
   if not name.startswith('/'):name=str(base/name)
   line=m.group(1)+name
  result.append(line)
 return '\n'.join(result)+'\n'
with tempfile.TemporaryDirectory(prefix='sieve-expire-preflight-') as t:
 root=pathlib.Path(t);local=root/'local.conf'
 text=future.read_text() if future.exists() else ''
 # Administrator plugin directives cannot be silently claimed as installer-owned.
 reserved=re.compile(r'\b(?:sieve_expire(?:_fallback|_maintenance_cooldown_seconds)?|imap_sieve_expire)\b')
 if any(reserved.search(line.split('#',1)[0]) for line in text.splitlines()):raise RuntimeError('administrator plugin directive conflicts with managed installer')
 local.write_text(absolute_includes(text))
 main=absolute_includes((base/'dovecot.conf').read_text())
 if str(base/'local.conf') not in main:raise RuntimeError('unsupported DMS local.conf include layout')
 main=main.replace(str(base/'local.conf'),str(local));(root/'dovecot.conf').write_text(main)
 subprocess.run(['doveconf','-c',str(root/'dovecot.conf'),'-n'],check=True)
"""
        self.exec('python3','-c',code)
        self.check('prospective-persistent-configuration')
    def health(self, selection=None, maintenance=None, delivery=False):
        self.exec('doveconf','-n')
        output=self.exec('supervisorctl','status','dovecot','postfix')
        if not all(re.search(x+r'\s+RUNNING',output) for x in ('dovecot','postfix')):raise Refusal('required services not running')
        if self.inspect['Config'].get('Healthcheck'):
            self.exec('dms-healthcheck')
        if selection is not None:
            result=json.loads(self.exec('cat','/run/sieve-expire-installer/result.json'))
            self.report.update(selection=result['selection'],maintenance=result['maintenance_enabled'],metadata=result['selection']=='normal')
            if result['selection']!=selection or result.get('unresolved_conflict'):raise Refusal('startup selection failed')
            if result['maintenance_enabled'] != bool(maintenance):raise Refusal('startup maintenance mismatch')
            imap=self.exec('doveconf','-f','protocol=imap','-h','mail_plugins')
            enabled=bool(re.search(r'\bimap_sieve_expire\s*=\s*yes\b',imap))
            if enabled!=bool(maintenance):raise Refusal('effective maintenance drift')
            owned=json.loads(self.exec('cat',str(startup.OWNED)))
            for path,h in owned.items():
                if self.exec('sha256sum',path).split()[0]!=h:raise Refusal('installed artifact drift')
                if self.exec('stat','-c','%u:%g:%a',path).strip()!='0:0:644':raise Refusal('installed ownership/mode drift')
            expected = {p:h for p,rel in self.manifest['destinations'].items()
                        if rel in self.manifest['implementations'][selection]
                        for h in [self.manifest['files'][rel]]}
            if any(owned.get(p)!=h for p,h in expected.items()):raise Refusal('installed release identity mismatch')
            if set(owned)!=set(expected)|{str(startup.CONFIG)}:raise Refusal('installed ownership catalog differs')
            if selection=='normal' and self.exec('doveconf','-f','protocol=imap','-h','sieve_expire_maintenance_cooldown_seconds').strip()!='300':raise Refusal('cooldown differs')
            self.exec('python3','-c',"import pathlib,subprocess,tempfile; d=tempfile.TemporaryDirectory();p=pathlib.Path(d.name);(p/'p.sieve').write_text('require [\"expire\"]; expire \"30d\"; expunge \"180d\"; keep;');subprocess.run(['sievec',str(p/'p.sieve'),str(p/'p.svbin')],check=True);r=subprocess.run(['sieve-dump',str(p/'p.svbin')],check=True,capture_output=True);assert b'EXPIRE' in r.stdout and b'EXPUNGE' in r.stdout")
        if delivery:
            if not self.a.test_recipient or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]*@[A-Za-z0-9][A-Za-z0-9.\-]*',self.a.test_recipient):raise Refusal('explicit local --test-recipient required')
            accounts=self.exec('cat','/tmp/docker-mailserver/postfix-accounts.cf')
            if self.a.test_recipient not in {r.split('|')[0] for r in accounts.splitlines()}:raise Refusal('test recipient is not an existing local FILE account')
            # Existing user policy may discard/redirect; receipt is mandatory.
            result=json.loads(self.exec('python3','-c',LMTP_SMOKE,self.a.test_recipient))
            found=self.exec('doveadm','search','-u',self.a.test_recipient,'HEADER','Message-ID',result['message_id'])
            if not found.strip():raise Refusal('local acceptance message not stored; choose a compatible recipient/policy')
            self.report['delivery']=result;self.check('local-delivery')
        if self.a.test_recipient:
            imap=self.exec('doveadm','exec','imap','-u',self.a.test_recipient,data=b'v1 CAPABILITY\r\nv2 LOGOUT\r\n')
            if 'v1 OK' not in imap or 'v2 OK' not in imap:raise Refusal('IMAP process/plugin verification failed')
            self.check('native-imap-process')
        if selection is None:
            plugins=self.exec('doveconf','-h','sieve_plugins')
            normal=bool(re.search(r'^\s*sieve_expire\s*=\s*yes\s*$',plugins,re.M))
            fallback=bool(re.search(r'^\s*sieve_expire_fallback\s*=\s*yes\s*$',plugins,re.M))
            imap=self.exec('doveconf','-f','protocol=imap','-h','mail_plugins')
            active=bool(re.search(r'^\s*imap_sieve_expire\s*=\s*yes\s*$',imap,re.M))
            if normal and fallback:raise Refusal('normal/fallback both selected')
            self.report.update(selection='normal' if normal else 'fallback' if fallback else 'neither',metadata=normal,maintenance=normal and active)
        self.report['health']='PASS';self.check('configuration-services-bytecode')
    def ready(self):
        for _ in range(self.a.startup_timeout):
            try:
                o=json.loads(self.docker('inspect',self.container))[0]
                if o['State']['Running']:
                    output=self.exec('supervisorctl','status','dovecot','postfix')
                    if all(re.search(n+r'\s+RUNNING',output) for n in ('dovecot','postfix')):
                        self.inspect=o;return
            except Refusal: pass
            time.sleep(1)
        raise Refusal('startup timed out')
    def state_setup(self, readonly=False):
        self.state=self.cfg/'.sieve-expire-manager'
        if not readonly and not os.access(self.cfg,os.W_OK):raise Refusal('config directory not writable')
        if readonly:
            if self.state.is_symlink():raise Refusal('symlink state')
            return
        private_directory(self.state)
        if (self.state/'installer.lock').is_symlink():raise Refusal('symlink installer lock')
        self.lock=(self.state/'installer.lock').open('a+b')
        (self.state/'installer.lock').chmod(0o600)
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Refusal('another installer owns target')
        private_directory(self.state/'runs');private_directory(self.state/'backups')
        if not (self.state/'target.json').exists():
            writejson(self.state/'target.json',dict(project=self.project,service=self.service,container=self.container,projectdir=self.projectdir,composefiles=[str(p) for p in self.composefiles]))
        self.work=private_directory(self.state/'runs'/uuid.uuid4().hex)
        global LOG_DIRECTORY
        LOG_DIRECTORY=self.work
        writejson(self.work/'owner.json',{'kind':'sieve-expire-temporary-run-v1','run':self.work.name})
        self.report['report_path']=str(self.work/'REPORT.json')
    def read_installation(self):
        p=self.state/'installed.json'
        return jsonread(p) if p.exists() else None
    def known_native_damage(self):
        old=self.read_installation()
        if not old or old['desired']['selection']=='neither':return False
        package=self.cfg/'sieve-expire'
        if package.is_symlink():raise Refusal('persistent package symlink')
        if not (package/'manifest.json').exists():
            package=self.state/'releases'/old['desired']['manifest']
            if not package.exists() and old['desired']['manifest']==self.manifest_hash:package=self.a.release
        run(['openssl','dgst','-sha256','-verify',ROOT/'installer/release-key.pem',
             '-signature',package/'manifest.sig',package/'manifest.json'])
        m=jsonread(package/'manifest.json')
        if sha(package/'manifest.json')!=old['desired']['manifest'] or m['destinations']!=startup.DESTINATIONS:
            raise Refusal('old installation ownership unauthenticated')
        owned=json.loads(self.exec('cat',str(startup.OWNED)))
        if self.exec('stat','-c','%u:%g:%a',str(startup.OWNED)).strip()!='0:0:600':raise Refusal('unsafe native ownership record')
        changed=False
        for path,h in owned.items():
            if path==str(startup.CONFIG):
                if self.exec('sha256sum',path).split()[0]!=h:raise Refusal('generated config externally modified')
                continue
            rel=m['destinations'].get(path)
            if not rel or m['files'].get(rel)!=h:raise Refusal('native ownership not from signed release')
            mode=self.exec('python3','-c',"import os,stat,sys;\ntry:\n s=os.lstat(sys.argv[1]);print(stat.S_ISREG(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==420)\nexcept FileNotFoundError:print('MISSING')",path).strip()
            if mode=='MISSING':changed=True;continue
            if mode!='True':raise Refusal('native ownership/type changed')
            if self.exec('sha256sum',path).split()[0]!=h:changed=True
        return changed
    def desired(self, maintenance,selection):
        options={'notification_mailbox':self.a.notification_mailbox or '', 'maintenance':maintenance,'selection':selection}
        return {'manifest':self.manifest_hash,'selection':selection,'maintenance':maintenance,'options':options}
    def package_ok(self,desired):
        p=self.cfg/'sieve-expire'
        try:
            m,h=verify_release(p,desired['maintenance'],check_components=False)
            return h==desired['manifest'] and jsonread(p/'options.json')==desired['options']
        except (OSError,ValueError,Refusal):return False
    def backup_create(self):
        self.backup=private_directory(self.state/'backups'/self.work.name)
        # Ensure space BEFORE stopping. Image export can exceed compressed layers.
        image=json.loads(self.docker('image','inspect',self.inspect['Image']))[0]
        sizes=self.exec('du','-sb','/var/mail','/var/mail-state').splitlines()
        required=image['Size']*2+sum(int(row.split()[0]) for row in sizes)+sum(p.stat().st_size for p in self.cfg.rglob('*') if p.is_file() and self.state not in p.parents)
        if shutil.disk_usage(self.backup).free < required+64*1024*1024:raise Refusal('insufficient backup space')
        writejson(self.backup/'container.original.json',self.inspect)
        writejson(self.backup/'compose.original.json',self.resolved)
        writejson(self.backup/'image.original.json',image)
        run(self.d+['image','save','-o',self.backup/'original-image.tar',self.inspect['Image']],timeout=300)
        # Saving is not loading: archive integrity + exact image reference retained;
        # image load/functional recovery is release qualification, not live rehearsal.
        from archive import validate
        record=validate(self.backup/'original-image.tar',self.inspect['Image'])
        writejson(self.backup/'archive.json',dict(record,sha256=sha(self.backup/'original-image.tar')))
        policy=self.inspect['HostConfig']['RestartPolicy']['Name']
        if policy not in ('no','always','unless-stopped','on-failure'):raise Refusal('unknown restart policy')
        if policy=='on-failure' and self.inspect['HostConfig']['RestartPolicy']['MaximumRetryCount']:
            policy+=':'+str(self.inspect['HostConfig']['RestartPolicy']['MaximumRetryCount'])
        (self.backup/'recovery.yml').write_text('services:\n  '+self.service+':\n    image: '+self.inspect['Image']+'\n    build: !reset null\n    restart: "'+policy+'"\n')
        (self.backup/'recovery.yml').chmod(0o600)
        self.saved={'phase':'before-stop','backup':str(self.backup),'container':self.container,
                    'input_fingerprint':self.fingerprint()}
        writejson(self.state/'transaction.json',self.saved)
        self.report['backup']=str(self.backup)
    def fingerprint(self):
        return {str(p):sha(p) for p in self.composefiles+[self.cfg/'dovecot.cf',self.cfg/'postfix-accounts.cf'] if p.exists()}
    def cold_copy(self):
        self.docker('stop','--time','60',self.container)
        self.saved['phase']='stopped';writejson(self.state/'transaction.json',self.saved)
        run(['rsync','-aHAX','--numeric-ids','--exclude=/.sieve-expire-manager',str(self.cfg)+'/',str(self.backup/'config')+'/'])
        diff=run(['rsync','-aHAXnci','--delete','--numeric-ids','--exclude=/.sieve-expire-manager',str(self.cfg)+'/',str(self.backup/'config')+'/'])
        if diff.strip():raise Refusal('cold backup filesystem differences')
        if inventory_excluding(self.cfg)!=inventory(self.backup/'config'):raise Refusal('backup inventory differs')
        writejson(self.backup/'config.inventory.json',inventory(self.backup/'config'))
        for target,label in [('/var/mail','mail'),('/var/mail-state','state')]:
            entries=[m for m in self.inspect['Mounts'] if m['Destination']==target]
            if len(entries)!=1 or entries[0]['Type'] not in ('bind','volume'):raise Refusal('unsupported persistent store')
            self.cold_store(entries[0],label)
        self.saved['phase']='backed-up';writejson(self.state/'transaction.json',self.saved)
        self.check('cold-config-backup')
    def cold_store(self, mount, label):
        source=mount['Name'] if mount['Type']=='volume' else mount['Source']
        if ',' in source or ',' in str(self.backup):raise Refusal('unsupported mount delimiter')
        name='sieve-expire-backup-'+self.work.name+'-'+label
        resources=jsonread(self.work/'resources.json') if (self.work/'resources.json').exists() else []
        resources.append(name);writejson(self.work/'resources.json',resources)
        # Exact supported base supplies Python; no build/download/dev helper image.
        # Inject trusted inventory/copier code over stdin, no target config changes.
        code=(ROOT/'deployment/fsops.py').read_text().split("if __name__=='__main__':")[0]
        code+='\n'+(ROOT/'installer/tree.py').read_text()
        code+="\nresult=copy_tree('/source','/checkpoint/"+label+"',inventory);pathlib=__import__('pathlib');pathlib.Path('/checkpoint/"+label+".inventory.json').write_text(json.dumps(result,sort_keys=True));print('cold content/metadata verified')\n"
        run(self.d+['run','--pull','never']+([] if self.a.keep_artifacts else ['--rm'])+['--name',name,'--network','none',
            '--label','org.sieve-expire.installer.run='+self.work.name,
            '--mount','type='+mount['Type']+',source='+source+',target=/source,readonly',
            '--mount','type=bind,source='+str(self.backup)+',target=/checkpoint',
            '--entrypoint','python3',self.inspect['Image'],'-'],data=code.encode(),timeout=600)
        self.check('cold-'+label+'-backup')
    def cancel_helpers(self, directory):
        p=directory/'resources.json'
        if not p.exists():return
        if p.is_symlink():raise Refusal('symlink resource registry')
        for name in jsonread(p):
            if not re.fullmatch('sieve-expire-backup-'+directory.name+'-(mail|state)',name):raise Refusal('unknown helper identity')
            try:o=json.loads(self.docker('inspect',name))[0]
            except Refusal:continue
            if o['Config']['Labels'].get('org.sieve-expire.installer.run')!=directory.name:raise Refusal('helper ownership mismatch')
            self.docker('rm','-f',name)
    def stage(self,desired):
        p=self.cfg/'sieve-expire'
        if p.exists():shutil.rmtree(p)
        shutil.copytree(self.a.release,p)
        writejson(p/'options.json',desired['options'])
        run([sys.executable,'-B',ROOT/'dms-installation/integrate.py','install-hook','--config-dir',self.cfg])
        self.saved['staged_paths']=self.managed_inputs()
        self.saved['phase']='staged';writejson(self.state/'transaction.json',self.saved)
    def recreate(self,backup=False):
        # Immutable same-image override; no pull/build or config edits in admin files.
        if backup:return self.compose('up','-d','--no-deps','--force-recreate','--pull','never','--no-build',self.service,backup=True)
        override=self.state/'selection.yml'
        override.write_text('services:\n  '+self.service+':\n    image: '+self.inspect['Image']+'\n    build: !reset null\n')
        override.chmod(0o600)
        cmd=self.d+['compose','--project-directory',self.projectdir,'-p',self.project]
        for p in self.composefiles:cmd+=['-f',str(p)]
        cmd+=['-f',str(override),'up','-d','--no-deps','--force-recreate','--pull','never','--no-build',self.service]
        run(cmd,timeout=180)
    def managed_inputs(self):
        result={}
        for name in ('sieve-expire','user-patches.sh','user-patches.sieve-expire-original.sh','sieve-expire-hook.json'):
            p=self.cfg/name
            if p.is_symlink():raise Refusal('managed recovery symlink')
            if p.is_file():result[name]=sha(p)
            elif p.is_dir():result[name]=inventory(p)
        return result
    def rollback(self):
        self.report['rollback']='INCOMPLETE'
        self.report['health']='NOT VERIFIED'
        self.backup=Path(self.saved['backup'])
        self.docker('stop','--time','60',self.container)
        original=jsonread(self.backup/'container.original.json')['Image']
        try:self.docker('image','inspect',original)
        except Refusal:
            from archive import validate
            archive=self.backup/'original-image.tar'
            if sha(archive)!=jsonread(self.backup/'archive.json')['sha256']:raise Refusal('recovery image checksum differs')
            validate(archive,original)
            self.docker('image','load','-i',str(archive),timeout=300)
            self.docker('image','inspect',original)
        # Only installer-owned paths; never restore the whole configuration tree or mail.
        # Preserve external edits rather than deleting them during recovery.
        if self.fingerprint()!=self.saved['input_fingerprint']:raise Refusal('administrator inputs changed; rollback requires review')
        if 'staged_paths' in self.saved and self.managed_inputs()!=self.saved['staged_paths']:
            raise Refusal('managed files changed externally; rollback requires review')
        for name in ('sieve-expire','user-patches.sh','user-patches.sieve-expire-original.sh','sieve-expire-hook.json'):
            dest=self.cfg/name; source=self.backup/'config'/name
            if dest.is_symlink():raise Refusal('rollback destination symlink')
            if dest.is_dir():shutil.rmtree(dest)
            elif dest.exists():dest.unlink()
            if source.exists():run(['rsync','-aHAX','--numeric-ids',str(source),str(self.cfg)+'/'])
        self.saved['staged_paths']=self.managed_inputs();writejson(self.state/'transaction.json',self.saved)
        self.recreate(backup=True);self.ready();self.health(delivery=True)
        self.saved['phase']='rolled-back';writejson(self.state/'transaction.json',self.saved)
        self.report['rollback']='PASS';self.check('software-rollback')
    def recover_pending(self):
        p=self.state/'transaction.json'
        if not p.exists():return
        tx=jsonread(p)
        if tx['phase'] in ('committed','rolled-back'):return
        self.saved=tx;self.backup=Path(tx['backup'])
        if self.backup.parent!=self.state/'backups' or self.backup.is_symlink():raise Refusal('invalid recovery checkpoint path')
        previous=self.state/'runs'/self.backup.name
        self.cancel_helpers(previous)
        if tx['phase']=='before-stop':
            self.docker('start',self.container);self.ready()
            tx['phase']='rolled-back';writejson(p,tx)
        elif tx['phase']=='stopped':
            # Interrupted copy has not modified config; restart existing generation.
            self.docker('start',self.container);self.ready();tx['phase']='rolled-back';writejson(p,tx)
        else:self.rollback()
        self.check('interruption-recovery')
    def dependency_check(self):
        # Conservative FILE source closure. Unknown include/storage ownership keeps fallback.
        conf=self.exec('doveconf','-n')
        if re.search(r'\b(?:sieve_script|sieve_global)\b.*(?:ldap|dict)',conf):return None
        # Native 2.4 named script blocks can put driver on a separate line.
        # Opaque storage is not proven safe by scanning FILE source roots.
        for block in re.findall(r'\bsieve_script\b[^{}]*\{([^{}]*)\}',conf,re.S):
            for driver in re.findall(r'^\s*driver\s*=\s*(\S+)',block,re.M):
                if driver.strip('"').lower()!='file':return None
        code=r'''
import pathlib,json,re
roots=[pathlib.Path('/var/mail'),pathlib.Path('/usr/lib/dovecot/sieve-global')]
files=[]
for root in roots:
 for p in root.rglob('*.sieve'):
  if p.is_symlink() and not p.resolve().is_relative_to(root):raise RuntimeError('external source')
  s=p.read_text();files.append(str(p))
  # False positives are safe; unknown includes cannot authorize removal.
  if re.search(r'\b(expire|expunge|include)\b',s,re.I):print(json.dumps({'safe':False,'sources':len(files)}));raise SystemExit
print(json.dumps({'safe':True,'sources':len(files)}))
'''
        try:
            result=json.loads(self.exec('python3','-c',code))
            # Any configured external script/include path remains unresolved.
            paths=re.findall(r'^\s*path\s*=\s*(\S+)',conf,re.M)
            if any(not p.startswith(('/var/mail/','/usr/lib/dovecot/sieve-global/')) or (not p.endswith('.sieve') and not p.endswith('/')) for p in paths):return None
            if self.exec('doveconf','-h','mail_home').strip()!='/var/mail/%{user | domain}/%{user | username}/home':return None
            return result['safe']
        except Refusal:return None
    def cleanup(self):
        if not self.state.exists():self.report['cleanup']='nothing-to-clean';return
        runs=self.state/'runs';removed=0
        if runs.is_symlink():raise Refusal('symlink runs')
        for p in runs.iterdir() if runs.exists() else []:
            if p==self.work or p.is_symlink() or not p.is_dir():continue
            marker=p/'owner.json'
            if marker.is_symlink() or not marker.is_file():continue
            o=jsonread(marker)
            if o.get('kind')!='sieve-expire-temporary-run-v1' or o.get('run')!=p.name:continue
            self.cancel_helpers(p)
            # Reports durable, only known temporary resources removed.
            for name in ('image.yml','staging'):
                q=p/name
                if q.is_symlink():raise Refusal('symlink cleanup path')
                if q.is_dir():shutil.rmtree(q)
                else:q.unlink(missing_ok=True)
            removed+=1
        self.report['cleanup']='PASS';self.check('owned-temporary-cleanup',runs=removed)
    def execute(self):
        maintenance=self.a.operation in ('install','enable') and not self.a.no_maintenance
        self.manifest,self.manifest_hash=verify_release(self.a.release,maintenance)
        self.report.update(release=self.manifest['release'],qualification=self.manifest['gates'],manifest_sha256=self.manifest_hash)
        self.check('authenticated-release')
        self.discover();self.state_setup(readonly=self.a.operation=='status')
        if self.a.operation not in ('status','cleanup'):
            if not self.a.test_recipient:raise Refusal('changes require --test-recipient for nondestructive local verification')
            self.recover_pending()
        self.native_repair=False
        if self.a.operation not in ('status','cleanup'):
            self.native_repair=self.known_native_damage()
            if self.native_repair:self.check('owned-native-damage','WARN')
        self.compatibility(self.manifest)
        if self.a.operation=='status':
            installed=self.read_installation()
            if installed:
                desired=installed['desired']
                if desired['selection']!='neither' and not self.package_ok(desired):raise Refusal('persistent release/options drift')
                if desired['selection']!='neither':
                    self.manifest,self.manifest_hash=verify_release(self.cfg/'sieve-expire',desired['maintenance'],check_components=False)
                    self.report.update(release=self.manifest['release'],manifest_sha256=self.manifest_hash,qualification=self.manifest['gates'])
                self.health(None if desired['selection']=='neither' else desired['selection'],desired['maintenance'])
                self.report.update(selection=desired['selection'],maintenance=desired['maintenance'],metadata=desired['selection']=='normal')
            else:
                self.health();self.report.update(selection='neither',maintenance=False,metadata=False)
            self.report['overall']='PASS';return
        if self.a.operation=='cleanup':self.cleanup();self.report['overall']='PASS';return
        if not self.a.test_recipient:raise Refusal('changes require --test-recipient for nondestructive local verification')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]*@[A-Za-z0-9][A-Za-z0-9.\-]*',self.a.test_recipient):raise Refusal('invalid local test recipient')
        local=self.exec('cat','/tmp/docker-mailserver/postfix-accounts.cf')
        if self.a.test_recipient not in {line.split('|')[0] for line in local.splitlines()}:raise Refusal('test recipient not in local FILE accounts')
        if not self.native_repair:
            self.health(delivery=False)
            self.future_configuration()
        if self.native_repair:self.check('prospective-persistent-configuration','SKIPPED',reason='owned native module damage; full replacement parse mandatory')
        installed=self.read_installation()
        if self.a.operation=='enable' and not installed:raise Refusal('--enable-maintenance requires an existing installation')
        selection='normal'
        if self.a.operation=='uninstall':
            removable=self.dependency_check()
            selection='neither' if removable is True else 'fallback'
            if self.a.remove_fallback and removable is not True:
                raise Refusal('Sieve dependency closure incomplete or expire required; retain fallback')
            if selection=='fallback':self.report['warnings'].append('Safe uninstall retains qualified fallback; scripts and metadata preserved')
            maintenance=False
        if installed and self.a.notification_mailbox is None:
            self.a.notification_mailbox=installed['desired']['options'].get('notification_mailbox','')
        desired=self.desired(maintenance,selection)
        if installed and installed['desired']==desired and (selection=='neither' or self.package_ok(desired)):
            try:
                self.health(None if selection=='neither' else selection,maintenance,delivery=True)
                self.check('current-installation','REUSED');self.report['overall']='PASS'
                self.report.update(selection=selection,maintenance=maintenance,metadata=selection=='normal');return
            except Refusal:
                self.check('current-installation','WARN');self.report['warnings'].append('Repairing detected drift')
        if maintenance and not self.a.yes:
            answer=input('Maintenance may irreversibly enforce existing deadlines. Confirm no external host writer modifies these DMS stores while stopped. Continue? [y/N] ')
            if answer.lower()!='y':raise Refusal('maintenance activation not confirmed')
        for tool in ('rsync','openssl','docker'):
            if not shutil.which(tool):raise Refusal('required tool absent: '+tool)
        if installed and (self.cfg/'sieve-expire').exists():
            # Reject unrelated/config script edits; damaged binary repair is allowed
            # only within paths in the previous trusted release catalog.
            if (self.cfg/'sieve-expire').is_symlink():raise Refusal('package symlink')
            allowed=set(startup.DESTINATIONS.values())|{'manifest.json','manifest.sig','release-key.pem','options.json','startup.py'}
            for base,dirs,files in os.walk(self.cfg/'sieve-expire',followlinks=False):
                if any((Path(base)/n).is_symlink() for n in dirs):raise Refusal('administrator symlink in package')
                for name in files:
                    path=Path(base)/name
                    if path.is_symlink() or str(path.relative_to(self.cfg/'sieve-expire')) not in allowed:
                        raise Refusal('unowned file in managed package')
        elif (self.cfg/'sieve-expire').exists():raise Refusal('unowned package conflict')
        registry=self.cfg/'sieve-expire-hook.json'
        if registry.exists():
            if registry.is_symlink():raise Refusal('symlink hook registry')
            record=jsonread(registry)
            if (self.cfg/'user-patches.sh').read_bytes()!=__import__('integrate').WRAPPER:
                raise Refusal('administrator changed managed hook')
            old=self.cfg/'user-patches.sieve-expire-original.sh'
            if record['original_sha256'] and (not old.is_file() or old.is_symlink() or sha(old)!=record['original_sha256']):
                raise Refusal('administrator original hook changed')
        self.backup_create()
        try:
            self.cold_copy()
            if selection=='neither':
                run([sys.executable,'-B',ROOT/'dms-installation/integrate.py','remove-hook','--config-dir',self.cfg])
                shutil.rmtree(self.cfg/'sieve-expire')
                self.saved['staged_paths']=self.managed_inputs()
                self.saved['phase']='staged';writejson(self.state/'transaction.json',self.saved)
            else:self.stage(desired)
            self.recreate();self.ready()
            self.native_repair=False;self.compatibility(self.manifest)
            self.health(None if selection=='neither' else selection,maintenance,delivery=True)
            if selection!='neither':
                authorities=private_directory(self.state/'releases')
                authority=private_directory(authorities/self.manifest_hash)
                atomic(authority/'manifest.json',(self.a.release/'manifest.json').read_bytes())
                atomic(authority/'manifest.sig',(self.a.release/'manifest.sig').read_bytes())
            writejson(self.state/'installed.json',{'desired':desired,'generation':self.inspect['Id']})
            self.saved['phase']='committed';writejson(self.state/'transaction.json',self.saved)
        except BaseException:
            if self.saved['phase'] in ('before-stop','stopped'):
                self.cancel_helpers(self.work)
                self.docker('start',self.container);self.ready();self.report['rollback']='original-restarted-no-config-change'
                self.saved['phase']='rolled-back';writejson(self.state/'transaction.json',self.saved)
            else:self.rollback()
            raise
        self.report.update(selection=selection,maintenance=maintenance,metadata=selection=='normal',overall='PASS')
        self.check('generation-replacement')
        if not self.a.keep_artifacts:
            (self.work/'image.yml').unlink(missing_ok=True);self.report['cleanup']='PASS'
        else:self.report['cleanup']='retained'
        backups=list((self.state/'backups').iterdir())
        if len(backups)>5:self.report['warnings'].append('More than five protected recovery backups retained; review retention manually (never auto-deleted)')


def inventory_excluding(root):
    return inventory(root, exclude=('.sieve-expire-manager',))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group()
    g.add_argument('--no-maintenance',action='store_true',help='metadata-only install, not cooldown zero')
    g.add_argument('--enable-maintenance',action='store_true')
    g.add_argument('--status',action='store_true')
    g.add_argument('--uninstall',action='store_true')
    g.add_argument('--cleanup',action='store_true')
    p.add_argument('--remove-fallback',action='store_true',help='with uninstall only; requires reliable Sieve dependency closure')
    p.add_argument('--keep-artifacts',action='store_true')
    p.add_argument('--container',help='DMS Compose container; required if discovery ambiguous')
    p.add_argument('--compose-file',action='append',help='explicit original Compose inputs (repeat for overrides)')
    p.add_argument('--docker-host',help='local Unix socket; default current Docker context')
    p.add_argument('--release',type=Path,default=ROOT/'installer/release')
    p.add_argument('--test-recipient',help='existing local mailbox whose policy keeps harmless acceptance mail')
    p.add_argument('--notification-mailbox',help='optional startup degradation alert recipient')
    p.add_argument('--yes',action='store_true',help='accept existing-deadline enforcement and exclusive-writer responsibility without prompt')
    p.add_argument('--startup-timeout',type=int,default=90)
    p.add_argument('--report',type=Path,help='optional additional private JSON report')
    return p

def main(argv=None):
    global LOG_DIRECTORY
    LOG_DIRECTORY=None
    p=parser();a=p.parse_args(argv)
    a.operation='status' if a.status else 'uninstall' if a.uninstall else 'cleanup' if a.cleanup else 'enable' if a.enable_maintenance else 'install'
    if a.remove_fallback and not a.uninstall:p.error('--remove-fallback requires --uninstall')
    if a.startup_timeout<1 or a.startup_timeout>300:p.error('startup timeout must be 1..300 seconds')
    m=Manager(a);rc=0
    try:
        if a.report:
            if not a.report.parent.is_dir():raise Refusal('report parent missing')
            if any(path.is_symlink() for path in [a.report,*a.report.parents]):raise Refusal('symlink report destination')
            if a.report.exists() and not a.report.is_file():raise Refusal('report destination not a file')
        m.execute()
    except (Refusal,OSError,ValueError,KeyError,subprocess.TimeoutExpired) as e:
        rc=2;m.report['error']=str(e);m.report['administrator_action'].append('Review protected report/journal; rerun only after resolving refusal')
        if m.report['rollback']=='INCOMPLETE':rc=3;m.report['administrator_action'].append('Recovery incomplete; preserve current mail and use protected recovery Compose inputs')
    except KeyboardInterrupt:
        rc=130;m.report['error']='Interrupted; recovery journal retained'
    finally:
        m.report['automatic_expiration']=m.report.get('maintenance')
        m.report['automatic_expunging']=m.report.get('maintenance')
        sel=m.report.get('selection')
        m.report['operational_state']=('failed-or-partially-completed' if m.report.get('health')!='PASS' else 'fully-operational' if sel=='normal' and m.report.get('maintenance') else 'metadata-only' if sel=='normal' else 'fallback-only' if sel=='fallback' else 'not-installed' if sel=='neither' else 'failed-or-incompatible')
        for destination in ([m.work/'REPORT.json'] if m.work else [])+([a.report] if a.report else []):
            try:writejson(destination,m.report)
            except (Refusal,OSError) as e:
                rc=rc or 2;m.report['overall']='FAIL'
                m.report['warnings'].append('Unable to save requested report: '+str(e))
                m.report['administrator_action'].append('Preserve console JSON; installation outcome is recorded above, report persistence failed')
        print('sieve-expire: '+m.report['overall']+'; operation='+a.operation+'; selection='+str(m.report.get('selection','unknown'))+'; maintenance='+str(m.report.get('maintenance','unknown')))
        print(json.dumps(m.report,indent=2))
        if m.lock:m.lock.close()
        LOG_DIRECTORY=None
    return rc

if __name__=='__main__':sys.exit(main())
