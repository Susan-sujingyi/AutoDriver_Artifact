"""Bounded Rust board construction dependencies, without device algorithms.

This is syntax/dataflow evidence. It retains branch/configuration uncertainty
and never certifies that a constructor executed or hardware obeys an API.
"""
from .evidence import descendants,SourceLocations
from .contract.model import Unknown


def construction_slice(source,component_types,*,maximum_statements=64,maximum_bytes=65536):
    from tree_sitter import Language,Parser
    import tree_sitter_rust
    tree=Parser(Language(tree_sitter_rust.language())).parse(source)
    if tree.root_node.has_error:raise Unknown('board construction syntax requires a configuration-aware frontend')
    locations=SourceLocations(source);records=[];gaps=[]
    def text(node):return source[node.start_byte:node.end_byte].decode()
    def names(node):return {text(part) for part in descendants(node) if part.type in {'identifier','type_identifier'}}
    def path(node):
        if node.type=='identifier':return text(node)
        if node.type=='field_expression':
            value=node.child_by_field_name('value');field=node.child_by_field_name('field')
            parent=path(value) if value is not None else None
            if parent and field is not None:return parent+'.'+text(field)
        return None
    def value_paths(node):
        # Namespace/type/macro tokens are not local runtime receivers.
        if node.type in {'scoped_identifier','scoped_type_identifier','token_tree','macro_invocation','type_arguments'}:return set()
        if node.type=='call_expression':
            result=set();function=node.child_by_field_name('function');arguments=node.child_by_field_name('arguments')
            if function is not None and function.type=='field_expression':
                receiver=function.child_by_field_name('value')
                if receiver is not None:result.update(value_paths(receiver))
            if arguments is not None:result.update(value_paths(arguments))
            return result
        reference=path(node)
        if reference:return {reference}
        result=set()
        for child in node.named_children:result.update(value_paths(child))
        return result
    def method_receivers(node):
        result=set()
        for part in descendants(node):
            if part.type!='call_expression':continue
            function=part.child_by_field_name('function')
            if function is not None and function.type=='field_expression':
                receiver=function.child_by_field_name('value')
                # A constructor call used as a receiver is a different value,
                # not an effect on one of its arguments.
                if receiver is not None and path(receiver):result.add(path(receiver))
        return result
    for function in tree.root_node.named_children:
        if function.type!='function_item':continue
        body=function.child_by_field_name('body');name=function.child_by_field_name('name')
        if body is None or name is None:continue
        attribute=function.prev_named_sibling;test_only=False
        while attribute is not None and attribute.type=='attribute_item':
            test_only|=''.join(text(attribute).split()) in {'#[cfg(test)]','#[test]'}
            attribute=attribute.prev_named_sibling
        if test_only:continue
        declarations={};effects=[];seeds=[]
        for part in descendants(body):
            # Nested functions have their own scope and are not board entry
            # statements merely because they appear textually inside a body.
            parent=part.parent;nested=False
            while parent is not None and parent!=function:
                if parent.type in {'function_item','closure_expression'}:nested=True;break
                parent=parent.parent
            if nested:continue
            if part.type=='let_declaration':
                pattern=part.child_by_field_name('pattern');value=part.child_by_field_name('value')
                if pattern is None or value is None:continue
                if pattern.type in {'identifier','mut_pattern'}:
                    identifiers=[n for n in descendants(pattern) if n.type=='identifier']
                    if len(identifiers)==1:declarations.setdefault(text(identifiers[0]),[]).append((part,value))
                elif text(pattern)=='_':effects.append((part,value))
                for call in descendants(value):
                    callee=call.child_by_field_name('function') if call.type=='call_expression' else None
                    parent=call.parent;local=True
                    while parent is not None and parent!=part:
                        if parent.type in {'closure_expression','function_item'}:local=False;break
                        parent=parent.parent
                    if not local:continue
                    if callee is not None and callee.type=='scoped_identifier' and names(callee)&component_types:
                        seeds.append((part,value));break
            elif part.type=='expression_statement' and part.named_children and part.named_children[0].type=='call_expression':
                effects.append((part,part))
        selected={part.start_byte:(part,value) for part,value in seeds};pending=list(seeds);used=set()
        while pending:
            part,value=pending.pop()
            for identifier in names(value):
                if identifier in used:continue
                used.add(identifier);bindings=declarations.get(identifier,[])
                if len(bindings)>1:
                    gaps.append({'function':text(name),'symbol':identifier,'reason':'shadowed board binding is not resolved by this syntax slice'});continue
                if bindings:
                    declaration=bindings[0]
                    if declaration[0].start_byte not in selected:selected[declaration[0].start_byte]=declaration;pending.append(declaration)
        receivers=set()
        for part,value in selected.values():
            receivers.update(value_paths(value))
            if any(node.type=='macro_invocation' for node in descendants(value)):
                gaps.append({'function':text(name),'line':locations.line(part.start_byte),
                    'reason':'macro construction source retained; expansion is not proved by this syntax slice'})
        for part,value in effects:
            if any(reference==receiver or reference.startswith(receiver+'.')
                   for receiver in method_receivers(value) for reference in receivers):
                selected.setdefault(part.start_byte,(part,value))
        for part,value in sorted(selected.values(),key=lambda item:item[0].start_byte):
            conditions=[];parent=part.parent;scopes=[part,function]
            while parent is not None and parent!=function:
                scopes.append(parent)
                if parent.type in {'if_expression','match_expression','match_arm','while_expression','for_expression','loop_expression'}:
                    condition=parent.child_by_field_name('condition') or parent.child_by_field_name('value')
                    if parent.type=='match_arm':
                        guard=source[parent.start_byte:condition.start_byte].decode() if condition is not None else 'unresolved match arm'
                    else:guard=text(condition) if condition is not None else 'unresolved control/iteration context'
                    item={'kind':parent.type,'condition':guard}
                    if parent.type=='if_expression':
                        alternative=parent.child_by_field_name('alternative')
                        item['branch']='alternative' if alternative is not None and alternative.start_byte<=part.start_byte<alternative.end_byte else 'consequence'
                    conditions.append(item)
                parent=parent.parent
            for scope in scopes:
                previous=scope.prev_named_sibling
                while previous is not None and previous.type=='attribute_item':
                    if 'cfg' in text(previous):conditions.append({'kind':'cfg','condition':text(previous)})
                    previous=previous.prev_named_sibling
            records.append({'function':text(name),'start_line':locations.line(part.start_byte),
                'end_line':locations.line(part.end_byte-1),'source':text(part),'conditions':conditions})
    if len(records)>maximum_statements or sum(len(row['source'].encode()) for row in records)>maximum_bytes:
        raise Unknown('complete board construction slice exceeds its explicit bound')
    return {'statements':records,'gaps':gaps,
        'scope':'parsed framework constructor inputs and receiver effects only; branch/cfg conditions retained; not active-dispatch, execution or provider proof'}
