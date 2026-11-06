"""Apply bounded file edits in prepared target copies, with complete preflight."""
import os
from pathlib import Path
import secrets
import stat

from .protocol.assembler import virtual_parts,path_allowed,digest
from .contract.model import ContractError


def parent_fd(root,path,*,create=False):
    parts=virtual_parts(path)
    if parts[0]!='target':raise ContractError('candidate edit is outside target')
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try:os.mkdir(part,dir_fd=fd)
                except FileExistsError:pass
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        return fd,parts[-1]
    except BaseException:
        os.close(fd);raise


def current_file(root,path):
    try:fd,name=parent_fd(root,path)
    except FileNotFoundError:return None,None
    try:
        try:f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        except FileNotFoundError:return None,None
        with os.fdopen(f,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>10_000_000:
                raise ContractError('candidate baseline must be a bounded regular file')
            return stream.read(),stat.S_IMODE(info.st_mode)
    finally:os.close(fd)


def replace(root,path,data,mode):
    fd,name=parent_fd(root,path,create=data is not None)
    temporary=None
    try:
        if data is None:
            os.unlink(name,dir_fd=fd);return
        temporary='.autodriver-'+secrets.token_hex(16)
        f=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=fd)
        with os.fdopen(f,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,name,src_dir_fd=fd,dst_dir_fd=fd);temporary=None
    finally:
        if temporary is not None:os.unlink(temporary,dir_fd=fd)
        os.close(fd)


def apply_edits(root,artifact,request):
    if artifact.get('kind')!='code' or artifact.get('format')!='file_edits':
        raise ContractError('code edits required')
    edits=artifact['edits']
    if not edits or len(edits)>128:raise ContractError('candidate edit count bound exceeded')
    prepared=[];seen=set()
    for edit in edits:
        path=edit['path']
        if path in seen or not path_allowed(path,request):raise ContractError('duplicate/out-of-scope candidate path')
        seen.add(path)
        old,mode=current_file(root,path)
        operation=edit['operation']
        expected=request.get('base_files',{}).get(path)
        if operation=='create':
            if old is not None or expected is not None or edit['base_sha256'] is not None:
                raise ContractError('create would overwrite an existing file')
        elif operation in {'replace','delete'}:
            if old is None or expected!=digest(old) or edit['base_sha256']!=expected:
                raise ContractError('candidate baseline changed before application')
        else:raise ContractError('unsupported candidate edit')
        if operation=='delete':
            if edit['content'] is not None:raise ContractError('delete content must be null')
            new=None
        else:
            if not isinstance(edit['content'],str):raise ContractError('candidate content must be complete text')
            new=edit['content'].encode()
            if len(new)>10_000_000:raise ContractError('candidate file size bound exceeded')
        prepared.append((path,old,mode or 0o644,new))
    applied=[]
    try:
        for path,old,mode,new in prepared:
            # Recheck immediately before replacement; the method itself never
            # executes generated source or permits simultaneous target editors.
            observed,_=current_file(root,path)
            if observed!=old:raise ContractError('candidate target changed during edit transaction')
            replace(root,path,new,mode);applied.append((path,old,mode))
    except BaseException:
        for path,old,mode in reversed(applied):replace(root,path,old,mode)
        raise
    return {path:digest(new) if new is not None else None for path,_,_,new in prepared}
