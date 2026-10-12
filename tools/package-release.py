#!/usr/bin/env python3
"""Package accepted public checkout/payload. No signing, building or qualification."""
import argparse,hashlib,json,tarfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'installer'))
import qualification
VERSION='v0.1.0'
def package(output):
    qualification.verify(ROOT/'installer/release')
    output.mkdir(parents=True,exist_ok=True)
    archive=output/('sieve-expire-'+VERSION+'-dms16.0.1-deb13u6-linux-arm64.tar.gz')
    if archive.exists() or (output/'SHA256SUMS').exists():raise ValueError('release output exists; never overwrite')
    # Checked-in allowlist; do not traverse arbitrary generated or hidden files.
    names=json.loads((ROOT/'tools/public-files.json').read_text())
    with tarfile.open(archive,'w:gz') as tar:
        for name in names:
            path=ROOT/name
            if path.is_symlink() or not path.is_file():raise ValueError('missing/nonregular public member: '+name)
            info=tar.gettarinfo(str(path),arcname='sieve-expire-'+VERSION+'/'+name)
            info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=0
            with path.open('rb') as source:tar.addfile(info,source)
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS').write_text(sha+'  '+archive.name+'\n')
    print(json.dumps({'status':'PACKAGED_NOT_PUBLISHED','archive':str(archive),'sha256':sha,'files':len(names)}))
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    package(parser.parse_args().output)
