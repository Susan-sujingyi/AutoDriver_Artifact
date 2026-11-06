"""Bounded named physical-relation dependencies, not a device formula table.

The immutable DBC remains the only normative representation. Expanded text is
a derived solver view; finite evaluation memoizes the same named definitions.
"""
import ast
from collections.abc import Mapping
from fractions import Fraction

from .model import Node,ContractError,Unknown
from .expression import scalar,quantity,evaluate as predicate_value
from .data import DataInvalid


def finite_value(relation,values,*,validity_values=None):
    """Evaluate explicit validity before arithmetic at one observed data point."""
    requirements=relation.get('requires',())
    if not isinstance(requirements,(tuple,list)) or len(requirements)>128:
        raise ContractError('bounded physical quantity requirement list required')
    environments=[values]+([validity_values] if validity_values is not None else [])
    for environment in environments:
        for predicate in requirements:
            valid=predicate_value(predicate,environment)
            if type(valid) is not bool:raise Unknown('physical quantity requirement is not boolean')
            if not valid:raise DataInvalid('physical quantity prerequisite is false at commit or use')
    domain=relation.get('domain')
    if isinstance(domain,str):
        valid=predicate_value(domain,values)
        if type(valid) is not bool:raise Unknown('physical quantity domain is not boolean')
        if not valid:raise DataInvalid('physical quantity input is outside its declared domain')
    elif isinstance(domain,Mapping):
        from .domains import symbolic_domain
        symbolic_domain(domain)
        for path,declaration in domain.items():
            if path not in values:raise Unknown('declared numeric domain input unavailable: '+path)
            value=values[path];kind=declaration['type'];lo,hi=declaration['bounds']
            if (kind=='bool' and type(value) is not bool or kind!='bool' and type(value) not in {int,Fraction}
                    or kind=='int' and getattr(value,'denominator',1)!=1 or not lo<=value<=hi):
                raise DataInvalid('physical quantity input violates its declared type/range: '+path)
    return scalar(relation['expression'],values,bit_width=relation.get('bit_width'))


def reference(node):
    if isinstance(node,ast.Name) and not node.id.startswith('_'):return node.id
    if isinstance(node,ast.Attribute) and not node.attr.startswith('_'):
        parent=reference(node.value)
        if parent:return parent+'.'+node.attr
    return None


class Relations:
    def __init__(self,definitions,*,external_names=()):
        if not isinstance(definitions,Mapping) or len(definitions)>128:raise Unknown('bounded named quantity map required')
        self.definitions=definitions;self.trees={};self.references={};self.edges={};external_names=set(external_names)
        for name,relation in definitions.items():
            if not isinstance(name,str) or not name or len(name)>128:raise Unknown('bounded named quantity key required')
            if not isinstance(relation,Node) or relation.name!='PhysicalQuantity':raise Unknown('typed physical relation required')
            expression=relation['expression']
            if not isinstance(expression,str) or len(expression)>16384:raise Unknown('physical relation text bound exceeded')
            try:tree=ast.parse(expression,mode='eval')
            except (SyntaxError,RecursionError):raise ContractError('named physical relation syntax invalid: '+name) from None
            if sum(1 for _ in ast.walk(tree))>2048:raise Unknown('physical relation node bound exceeded')
            refs=set()
            def collect(node):
                if isinstance(node,ast.Call):
                    for arg in node.args:collect(arg)
                    for keyword in node.keywords:collect(keyword.value)
                    return
                path=reference(node)
                if path:refs.add(path);return
                for child in ast.iter_child_nodes(node):collect(child)
            collect(tree.body)
            requirements=relation.get('requires',())
            if not isinstance(requirements,(tuple,list)) or len(requirements)>128:
                raise ContractError('bounded physical quantity requirement list required')
            predicates=list(requirements)
            domain=relation.get('domain')
            if isinstance(domain,str):predicates.append(domain)
            elif isinstance(domain,Mapping):refs.update(path for path in domain if path.startswith('quantities.'))
            for predicate in predicates:
                if not isinstance(predicate,str):continue
                try:collect(ast.parse(predicate,mode='eval').body)
                except (SyntaxError,RecursionError):pass  # Finite validity evaluation reports unsupported prose.
            self.trees[name]=tree;self.references[name]=refs
            if any(path in external_names and path in definitions for path in refs):
                raise Unknown('ambiguous frame/FSM and quantity name; qualify frame input or use quantities.<name>')
            self.edges[name]={self.alias(path) for path in refs if self.alias(path) is not None}
        visited=set();active=set()
        def visit(name,depth=0):
            if depth>64:raise Unknown('named physical relation depth exceeded')
            if name in active:raise ContractError('cyclic named physical relation: '+name)
            if name in visited:return
            active.add(name)
            for child in sorted(self.edges[name]):visit(child,depth+1)
            active.remove(name);visited.add(name)
        for name in self.edges:visit(name)

    def alias(self,path):
        if path.startswith('quantities.'):
            name=path[len('quantities.'):]
            if name not in self.definitions:raise Unknown('undeclared named physical relation: '+path)
            return name
        return path if path in self.definitions else None

    def unit_dependency(self,name,child):
        value=quantity(1,self.definitions[child]['unit'])
        if not value.dimensions:return
        expression=self.trees[name].body
        # A same-unit alias is exact. Dimensional algebra through other named
        # physical values requires a separately encoded conversion relation.
        if reference(expression) in {child,'quantities.'+child} and quantity(1,self.definitions[name]['unit'])==value:return
        raise Unknown('cross-unit named relation algebra is not encoded: '+name+' -> '+child)

    def value(self,name,environment,*,validity_environment=None):
        if name not in self.definitions:raise Unknown('physical relation not declared: '+name)
        memo={};base=dict(environment)
        def evaluate(current):
            if current in memo:return memo[current]
            values=dict(base);validity_values=dict(validity_environment) if validity_environment is not None else None
            for path in sorted(self.references[current]):
                child=self.alias(path)
                if child is None:continue
                if path in base:raise Unknown('ambiguous frame/FSM and quantity name; use quantities.<name>: '+path)
                self.unit_dependency(current,child);values[path]=evaluate(child)
                if validity_values is not None:validity_values[path]=values[path]
            result=finite_value(self.definitions[current],values,validity_values=validity_values);memo[current]=result;return result
        return evaluate(name)

    def expanded(self,name):
        """Information-preserving pure-text view, bounded against DAG blow-up."""
        from copy import deepcopy
        memo={};total=0
        def expand(current):
            nonlocal total
            if current in memo:return deepcopy(memo[current])
            owner=self
            class Substitute(ast.NodeTransformer):
                def visit_Name(self,node):return self.replace(node)
                def visit_Attribute(self,node):return self.replace(node)
                def visit_Call(self,node):
                    # Function names are grammar, never quantity aliases.
                    node.args=[self.visit(arg) for arg in node.args]
                    for keyword in node.keywords:keyword.value=self.visit(keyword.value)
                    return node
                def replace(self,node):
                    path=reference(node);child=owner.alias(path) if path else None
                    if child is None:return node
                    owner.unit_dependency(current,child);return expand(child)
            tree=Substitute().visit(deepcopy(self.trees[current].body));total+=sum(1 for _ in ast.walk(tree))
            if total>8192:raise Unknown('expanded physical relation node bound exceeded')
            memo[current]=tree;return deepcopy(tree)
        if name not in self.definitions:raise Unknown('physical relation not declared: '+name)
        tree=expand(name);ast.fix_missing_locations(tree);text=ast.unparse(tree)
        if len(text)>16384:raise Unknown('expanded physical relation text bound exceeded')
        return text

    def solver_definitions(self,name):
        """Keep shared arithmetic definitions shared in the solver's DAG.

        Only the reachable named relations are selected. Unit compatibility is
        checked on every edge, including aliases; external inputs stay inputs.
        No domain, bit width, calibration value or approximation is invented.
        """
        if name not in self.definitions:raise Unknown('physical relation not declared: '+name)
        definitions={};seen=set()
        def visit(current):
            if current in seen:return
            seen.add(current)
            for path in sorted(self.references[current]):
                child=self.alias(path)
                if child is None:continue
                self.unit_dependency(current,child)
                definitions[path]=self.definitions[child]['expression'];visit(child)
        visit(name)
        return definitions

    def solver_preconditions(self,name):
        """Declared requirements on the reachable DAG, not proven premises."""
        result=[];seen=set()
        def visit(current):
            if current in seen:return
            seen.add(current);value=self.definitions[current]
            result.extend(value.get('requires',()))
            if isinstance(value.get('domain'),str):result.append(value['domain'])
            for child in sorted(self.edges[current]):visit(child)
        visit(name);return result

    def solver_widths(self,name):
        widths={};seen=set()
        def visit(current):
            if current in seen:return
            seen.add(current)
            for path in self.references[current]:
                child=self.alias(path)
                if child is not None:
                    widths[path]=self.definitions[child].get('bit_width');visit(child)
        visit(name);return widths
