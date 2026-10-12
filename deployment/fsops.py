#!/usr/bin/env python3
"""Read-only tree evidence (never follows directory symlinks)."""
import hashlib, json, os, stat, sys
from pathlib import Path

def inventory(root, exclude=()):
    root = Path(root)
    result = {}
    links = {}
    for base, dirs, files in os.walk(root, followlinks=False,onerror=lambda e: (_ for _ in ()).throw(e)):
        dirs[:] = [n for n in dirs if str((Path(base)/n).relative_to(root)) not in exclude]
        paths = [Path(base)] + [Path(base)/n for n in files] + [Path(base)/n for n in dirs if (Path(base)/n).is_symlink()]
        for p in paths:
            s=p.lstat(); key=str(p.relative_to(root))
            entry={'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,
                   'mtime_ns':s.st_mtime_ns,'type':stat.S_IFMT(s.st_mode),
                   'xattrs':{n:os.getxattr(p,n,follow_symlinks=False).hex() for n in sorted(os.listxattr(p,follow_symlinks=False))}}
            if stat.S_ISREG(s.st_mode):
                h=hashlib.sha256()
                with p.open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
                entry.update(size=s.st_size,sha256=h.hexdigest())
                links.setdefault((s.st_dev,s.st_ino),[]).append(key)
            elif stat.S_ISLNK(s.st_mode): entry['target']=os.readlink(p)
            elif not (stat.S_ISDIR(s.st_mode) or stat.S_ISSOCK(s.st_mode) or stat.S_ISFIFO(s.st_mode)):
                raise RuntimeError('unsupported special file: '+str(p))
            result[key]=entry
    for group in links.values():
        if len(group)>1:
            for key in group:result[key]['hardlinks']=sorted(group)
    return result

if __name__=='__main__':
    print(json.dumps(inventory(sys.argv[1]),sort_keys=True))
