#!/usr/bin/env python3
"""Cold checkpoint copier; input mounted read-only, destination must be new.

Linux metadata/HAX preservation with exact inventory comparison. No restore API.
Socket/FIFO filesystem nodes are preserved, never socket contents/runtime state.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import sys

# Shared, previously qualified inventory definition is injected by the caller.
def copy_tree(source, target, inventory):
    source=Path(source);target=Path(target)
    if target.exists() or target.is_symlink():raise ValueError('checkpoint tree already exists')
    if not source.is_dir() or source.is_symlink():raise ValueError('source directory invalid')
    before=inventory(source); links={}
    def copy(src,dst):
        s=src.lstat();mode=stat.S_IMODE(s.st_mode)
        if stat.S_ISDIR(s.st_mode):
            dst.mkdir(mode=0o700)
            for p in src.iterdir():copy(p,dst/p.name)
        elif stat.S_ISLNK(s.st_mode):os.symlink(os.readlink(src),dst)
        elif stat.S_ISREG(s.st_mode):
            key=(s.st_dev,s.st_ino)
            if key in links:os.link(links[key],dst)
            else:
                with src.open('rb') as a,dst.open('xb') as b:shutil.copyfileobj(a,b,1024*1024)
                links[key]=dst
        elif stat.S_ISFIFO(s.st_mode):os.mkfifo(dst,mode)
        elif stat.S_ISSOCK(s.st_mode):os.mknod(dst,stat.S_IFSOCK|mode)
        else:raise ValueError('unsupported filesystem node')
        os.chown(dst,s.st_uid,s.st_gid,follow_symlinks=False)
        if not stat.S_ISLNK(s.st_mode):os.chmod(dst,mode)
        for name in os.listxattr(src,follow_symlinks=False):
            os.setxattr(dst,name,os.getxattr(src,name,follow_symlinks=False),follow_symlinks=False)
        os.utime(dst,ns=(s.st_atime_ns,s.st_mtime_ns),follow_symlinks=False)
    copy(source,target)
    after=inventory(source); copied=inventory(target)
    if before!=after or before!=copied:raise ValueError('source changed or copied content/metadata differs')
    return copied
