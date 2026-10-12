"""Read-only identity/integrity validation of Docker's OCI Docker-save transport."""
import hashlib,json,tarfile

def validate(path,image):
 with tarfile.open(path,'r') as t:
  members=t.getmembers(); names=[m.name for m in members]
  if len(names)!=len(set(names)):raise RuntimeError('duplicate archive member')
  if any(m.issym() or m.islnk() or not (m.isfile() or m.isdir()) for m in members):raise RuntimeError('unsupported archive member')
  # These are the exact Docker 29 OCI exports already qualified by this release.
  index=json.load(t.extractfile('index.json'))
  if not any(d['digest']==image for d in index['manifests']):raise RuntimeError('archive immutable image identity mismatch')
  for m in members:
   if m.isfile() and m.name.startswith('blobs/sha256/'):
    h=hashlib.sha256();f=t.extractfile(m)
    for b in iter(lambda:f.read(1048576),b''):h.update(b)
    if h.hexdigest()!=m.name.split('/')[-1]:raise RuntimeError('corrupt archive blob')
  manifests=json.load(t.extractfile('manifest.json'))
  if len(manifests)!=1:raise RuntimeError('ambiguous multi-image Docker save archive')
  item=manifests[0];config=json.load(t.extractfile(item['Config']))
  if config['architecture']!='arm64' or config['os']!='linux':raise RuntimeError('archive platform mismatch')
  for n in [item['Config'],*item['Layers']]:
   if n not in names:raise RuntimeError('missing restoration layer/config')
  return {'image':image,'config_sha256':item['Config'].split('/')[-1],'platform':'linux/arm64','blobs_verified':sum(m.isfile() and m.name.startswith('blobs/sha256/') for m in members),'rootfs':config['rootfs']}
