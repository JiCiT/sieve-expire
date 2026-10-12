"""Qualified directory-bind safety helpers; no deployment entry point."""
from pathlib import Path

class Refusal(RuntimeError): pass

def require(ok,message):
    if not ok: raise Refusal(message)

def plain(path, directory=False):
    p=Path(path)
    require(p.is_absolute(),'absolute path required: '+str(p))
    for ancestor in [p,*p.parents]:
        require(not ancestor.is_symlink(),'symlink path forbidden: '+str(ancestor))
    require(p.is_dir() if directory else p.is_file(),'missing or wrong file type: '+str(p))
    return p

def same_bind_source(source, actual):
    # Docker inspect and Compose can spell a directory bind differently solely
    # by trailing slashes. Do not resolve aliases or normalize other differences:
    # file binds (including the declared timezone link) retain exact comparison.
    if source == actual:
        return True
    if not isinstance(source,str) or not isinstance(actual,str):
        return False
    directory=source.rstrip('/')
    if not directory or directory != actual.rstrip('/'):
        return False
    plain(directory,True)  # Existing absolute-path / physical-directory safeguards.
    return True
