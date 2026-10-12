#!/usr/bin/env python3
"""Download public, checksum-pinned upstream inputs to a new/existing cache."""
import argparse,hashlib,json,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def fetch(destination):
    destination.mkdir(parents=True,exist_ok=True)
    records=json.loads((ROOT/'qualification/native-build.json').read_text())['upstream_inputs']
    for recorded,item in records.items():
        name=recorded.replace('1%3a','')
        target=destination/name
        if target.exists():
            data=target.read_bytes()
        else:
            with urllib.request.urlopen('https://deb.debian.org/debian/pool/main/d/dovecot/'+name,timeout=90) as response:data=response.read()
        if len(data)!=item['size'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise ValueError('input differs from reviewed authenticated package/source record: '+name)
        if not target.exists():target.write_bytes(data)
        print(name,'verified')
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    fetch(parser.parse_args().output)
