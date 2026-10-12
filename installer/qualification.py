#!/usr/bin/env python3
"""Read-only verification of the accepted public payload and source correspondence.

This verifies an existing signed release. It cannot qualify arbitrary new binaries
or sign them. New release qualification requires fresh runtime evidence and review.
"""
import argparse,hashlib,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def verify(release):
    spec=importlib.util.spec_from_file_location('manager',ROOT/'installer/manage.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    manifest,digest=m.verify_release(release,True)
    for rel,h in manifest['qualification']['evidence'].items():
        if sha(ROOT/rel)!=h:raise ValueError('public qualification record changed: '+rel)
    if sha(Path(__file__))!=manifest['qualification']['publisher_code_sha256']:
        raise ValueError('public verifier changed')
    for rel,h in manifest['installer_components'].items():
        if sha(ROOT/rel)!=h:raise ValueError('installer component changed: '+rel)
    provenance=json.loads((ROOT/'qualification/native-build.json').read_text())
    for rel,h in provenance['source_sha256'].items():
        if sha(ROOT/rel)!=h:raise ValueError('native source changed; existing binary qualification cannot qualify a new build: '+rel)
    for target,relative in manifest['destinations'].items():
        if sha(Path(release)/relative)!=manifest['files'][relative]:raise ValueError('artifact changed')
    return {'status':'PASS','manifest_sha256':digest,'gates':manifest['gates'],'scope':'existing authenticated release and source only; not new runtime qualification'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,default=ROOT/'installer/release')
    args=parser.parse_args();print(json.dumps(verify(args.release),indent=2))
