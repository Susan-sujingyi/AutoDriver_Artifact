"""Conservative generation dependency projection, derived from current parents.

Only FP entries outside this projection may be unused. Device gaps, global FP
gaps, contradictions and opaque reports cannot be removed by the projection.
"""
from collections.abc import Mapping

from .model import Node, ContractError, identity, unwrap, walk


def framework_scope(dbc, fp, bp=None):
    roots={'os','snapshot','execution','lifecycle','resources','assumptions','unresolved','outputs'}
    reasons={path:['global framework or native-output obligation'] for path in roots}
    def require(path,reason):
        path=path.removeprefix('fp.')
        if not path or path.startswith('.') or any(not part for part in path.split('.')):
            raise ContractError('invalid framework dependency path')
        current=fp
        for part in path.split('.'):
            if isinstance(current,Node):current=current.fields
            if not isinstance(current,Mapping) or part not in current:raise ContractError('framework dependency does not resolve: '+path)
            current=current[part]
        roots.add(path);reasons.setdefault(path,[]).append(reason)
    connection=unwrap(dbc['connection'])
    if not isinstance(connection,Node) or connection.name not in {'I2C','SPI','GPIOConnection'}:
        raise ContractError('typed connection required to select framework dependencies')
    require('gpio' if connection.name=='GPIOConnection' else connection.name.lower(),'device connection')
    if any(value.name in {'WaitAfterCompletion','WaitFrom','Deadline'} for _,value in walk(dbc['behavior'])):
        require('alarm','device waiting/deadline semantics')
    # Without BP, dependencies cannot be narrowed to a nonexistent mapping.
    if bp is None:
        roots.update(fp.fields)
        for path in fp.fields:reasons.setdefault(path,[]).append('direct generation has no BP dependency projection')
    else:
        def references(value,where):
            if isinstance(value,Node):
                if value.name=='FromFP':require(value['path'],where)
                elif value.name=='Ref' and value['identifier'].startswith('fp.'):require(value['identifier'],where)
                for key,child in value.fields.items():references(child,where+'.'+key)
            elif isinstance(value,Mapping):
                for key,child in value.items():references(child,where+'.'+str(key))
            elif isinstance(value,(tuple,list)):
                for index,child in enumerate(value):references(child,where+'.'+str(index))
            elif isinstance(value,str) and value.startswith('fp.'):
                require(value,where)
        references(bp,'bp')
        # A selected entry may refer to another entry in the same profile.
        seen=set()
        while roots-seen:
            path=sorted(roots-seen)[0];seen.add(path);value=fp
            for part in path.split('.'):
                if isinstance(value,Node):value=value.fields
                value=value[part]
            references(value,'fp.'+path)
    return {'schema':'autodriver.framework-dependencies/v1','contract_sha256':identity(fp),
        'dbc_sha256':identity(dbc),'bp_sha256':identity(bp) if bp is not None else None,
        'required_paths':sorted(roots),'reasons':reasons,
        'scope':'conservative framework dependencies for generation; no device gap, global unresolved item or contradiction is exempted'}


def path_required(path,scope):
    return any(path==root or path.startswith(root+'.') or root.startswith(path+'.')
               for root in scope['required_paths']) if path else True
