"""Audited capability roots and bounded framework evidence closure.

Descriptors select OS framework locations, not sensor behavior. Provider name
matches are candidates only; active-provider and timing guarantees remain proof
obligations for the recovered FP.
"""
from pathlib import PurePosixPath
import json
import posixpath
import tomllib

from .evidence import SourceIndex,descendants,SourceLocations
from .source_analysis import CAnalysis
from .contract.model import Unknown


FRAMEWORK_PREFIXES={
    'tock':('kernel/','capsules/core/','chips/','arch/'),
    'nuttx':('include/nuttx/','arch/','sched/','drivers/i2c/','drivers/spi/'),
    'zephyr':('include/zephyr/','kernel/','drivers/i2c/','drivers/spi/','drivers/gpio/','drivers/timer/'),
    'riot':('core/','sys/include/','sys/ztimer/','drivers/include/periph/','cpu/'),
}
ROOTS={
    'tock':{'i2c':['kernel/src/hil/i2c.rs','boards/components/src/i2c.rs'],
            'spi':['kernel/src/hil/spi.rs','boards/components/src/spi.rs'],'gpio':['kernel/src/hil/gpio.rs'],
            'time':['kernel/src/hil/time.rs','boards/components/src/alarm.rs'],'sensor':['kernel/src/hil/sensors.rs']},
    'nuttx':{'i2c':['include/nuttx/i2c/i2c_master.h'],'spi':['include/nuttx/spi/spi.h'],
             'gpio':['include/nuttx/ioexpander/gpio.h'],'time':['include/nuttx/clock.h','include/nuttx/signal.h'],
             'sensor':['include/nuttx/sensors/sensor.h','drivers/sensors/sensor.c']},
    'zephyr':{'i2c':['include/zephyr/drivers/i2c.h'],'spi':['include/zephyr/drivers/spi.h'],
              'gpio':['include/zephyr/drivers/gpio.h'],'time':['include/zephyr/kernel.h'],'sensor':['include/zephyr/drivers/sensor.h']},
    'riot':{'i2c':['drivers/include/periph/i2c.h'],'spi':['drivers/include/periph/spi.h'],
            'gpio':['drivers/include/periph/gpio.h'],'time':['sys/include/ztimer.h'],'sensor':['drivers/include/saul.h']},
}
QUERIES={
    'tock':{'i2c':['I2CDevice','I2CClient'],'spi':['SpiMasterDevice','SpiMasterClient'],
            'gpio':['Pin','Input','Output','Interrupt'],'time':['Alarm','AlarmClient','Time','Ticks','Frequency','ConvertTicks'],
            'sensor':[]},
    'nuttx':{'i2c':['i2c_master_s','i2c_ops_s','i2c_msg_s','I2C_TRANSFER'],'spi':['spi_dev_s','spi_ops_s'],
             'gpio':['gpio_dev_s','gpio_operations_s'],'time':['nxsig_usleep','usleep','clock_systime_ticks'],
             'sensor':['sensor_lowerhalf_s','sensor_ops_s','sensor_register','sensor_unregister']},
    'zephyr':{'i2c':['i2c_transfer','i2c_write_dt','i2c_read_dt'],'spi':['spi_transceive_dt','spi_write_dt'],
              'gpio':['gpio_pin_set_dt','gpio_pin_get_dt'],'time':['k_sleep','k_msleep'],
              'sensor':['sensor_value','sensor_channel','sensor_sample_fetch','sensor_channel_get']},
    'riot':{'i2c':['i2c_acquire','i2c_release','i2c_read_bytes','i2c_write_bytes'],'spi':['spi_acquire','spi_release','spi_transfer_bytes'],
            'gpio':['gpio_read','gpio_write'],'time':['ztimer_sleep','ztimer_t','ztimer_clock_t'],
            'sensor':['saul_driver_t','phydat_t']},
}


def cargo_dependencies(workspace,base,board,*,maximum_manifests=48):
    """Literal public board Cargo path graph, never a build or crate download."""
    entries=workspace.manifest['files'];roots=[path for path in entries if path.startswith(base+'boards/')
        and PurePosixPath(path).name=='Cargo.toml' and PurePosixPath(path).parent.name==board]
    if len(roots)!=1:return {'manifests':[],'crate_roots':[],'edges':[],'gaps':[{'scope':'board Cargo root','reason':'unique approved board manifest not found'}]}
    pending=roots.copy();selected=[];seen=set();edges=[];gaps=[]
    while pending:
        path=pending.pop(0)
        if path in seen:continue
        seen.add(path)
        if len(selected)>=maximum_manifests:
            gaps.append({'path':path,'reason':'Cargo dependency manifest bound reached'});continue
        try:document=tomllib.loads(workspace.read_original(path).decode('utf8'))
        except (tomllib.TOMLDecodeError,UnicodeDecodeError):
            gaps.append({'path':path,'reason':'Cargo manifest syntax unsupported'});continue
        selected.append(path)
        groups=[(None,document.get('dependencies',{})),('build',document.get('build-dependencies',{}))]
        groups += [(condition,group.get('dependencies',{})) for condition,group in document.get('target',{}).items()]
        for condition,dependencies in groups:
            for name,specification in dependencies.items():
                if not isinstance(specification,dict) or 'path' not in specification:continue
                dependency=specification['path']
                if not isinstance(dependency,str):continue
                target=posixpath.normpath(str(PurePosixPath(path).parent/dependency/'Cargo.toml'))
                record={'from':path,'crate':name,'manifest':target,'condition':condition,'optional':bool(specification.get('optional'))}
                edges.append(record)
                if not target.startswith(base) or target not in entries:
                    gaps.append({'path':target,'reason':'Cargo path dependency absent from approved target snapshot'});continue
                if condition is not None or specification.get('optional'):
                    gaps.append({'path':target,'reason':'Cargo conditional/optional dependency retained as candidate; activation not proved'})
                pending.append(target)
    return {'manifests':selected,'crate_roots':[str(PurePosixPath(path).parent) for path in selected],'edges':edges,'gaps':gaps}


def board_type_aliases(source,known_types):
    """Select literal aliases of already indexed framework types only.

    Board integration is configuration evidence, not a source of device
    algorithms. Functions, constants and aliases of unknown device components
    are excluded. A type alias does not prove construction or active dispatch.
    """
    from tree_sitter import Language,Parser
    import tree_sitter_rust
    tree=Parser(Language(tree_sitter_rust.language())).parse(source)
    locations=SourceLocations(source);aliases=[];gaps=[];attributes=[]
    if tree.root_node.has_error:return [],[{'reason':'public board syntax needs configuration-aware analysis'}]
    for item in tree.root_node.named_children:
        if item.type=='attribute_item':attributes.append(source[item.start_byte:item.end_byte].decode());continue
        guarded=any('cfg' in value for value in attributes);attributes=[]
        if item.type!='type_item':continue
        value=item.child_by_field_name('type');name=item.child_by_field_name('name')
        if value is None or name is None:continue
        while value.type=='generic_type':value=value.child_by_field_name('type')
        if value.type=='scoped_type_identifier':value=value.child_by_field_name('name')
        if value is None or value.type!='type_identifier':continue
        root=source[value.start_byte:value.end_byte].decode()
        if root not in known_types:continue
        alias=source[name.start_byte:name.end_byte].decode()
        if guarded:
            gaps.append({'symbol':alias,'reason':'board alias has an unresolved cfg; not an unconditional provider binding'});continue
        aliases.append({'alias':alias,'framework_type':root,'start_line':locations.line(item.start_byte),
            'end_line':locations.line(item.end_byte-1),'source':source[item.start_byte:item.end_byte].decode()})
    return aliases,gaps


def select_framework(workspace,os_role,*,maximum_files=24,maximum_lines=10000):
    if os_role not in {'source','target'}:raise ValueError('explicit framework role required')
    task=workspace.task
    os_name=task['source' if os_role=='source' else 'target']['os']
    if os_name not in ROOTS:raise Unknown('framework selector has no OS descriptor')
    base='source_os/' if os_role=='source' else 'target/'
    connection=task['requirements']['connection']
    capabilities=[kind for kind in ('i2c','spi','gpio') if connection.get(kind)] + ['time','sensor']
    entries=workspace.manifest['files']
    cargo=cargo_dependencies(workspace,base,task['target'].get('board','')) if os_name=='tock' and os_role=='target' else {
        'manifests':[],'crate_roots':[],'edges':[],'gaps':[]}
    library_roots=[root+'/' for root in cargo['crate_roots'] if root.startswith(base+'libraries/')]
    chip_roots=[root+'/' for root in cargo['crate_roots'] if root.startswith(base+'chips/')]
    board_mains={str(PurePosixPath(path).parent/'src/main.rs') for path in cargo['manifests']
                 if PurePosixPath(path).parent.name==task['target'].get('board')} if os_role=='target' and os_name=='tock' else set()
    excluded={x['path'] for x in task['source']['selected_files']} | set(task['target']['interface_files'])
    # Generic Sensor framework implementation is explicitly allowed; no other
    # device implementation enters FP under the NuttX drivers/sensors prefix.
    def allowed(path):
        local=path.removeprefix(base)
        return path.startswith(base) and path not in excluded and (
            local.startswith(FRAMEWORK_PREFIXES[os_name]) or local=='drivers/sensors/sensor.c'
            or os_name=='tock' and local in {p for capability in capabilities for p in ROOTS['tock'][capability]}
            or path in board_mains
            or any(path.startswith(root) for root in library_roots))
    selected=[];gaps=list(cargo['gaps']);audit=[];lines=0;attempted=set()

    def add(path,reason):
        nonlocal lines
        if path in attempted:return
        attempted.add(path)
        if path not in entries:
            gaps.append({'path':path,'reason':'root/dependency absent from approved prepared snapshot'});return
        if not allowed(path):
            gaps.append({'path':path,'reason':'candidate is outside generic framework scope'});return
        data=workspace.read_original(path)
        size=len(data.splitlines())
        # The line budget applies to complete selected declarations, not raw
        # registration. A large required header must not crowd out other roots.
        if len(selected)>=maximum_files:
            gaps.append({'path':path,'reason':'framework analysis file bound reached'});return
        selected.append(path);lines+=size;audit.append({'path':path,'reason':reason,'sha256':entries[path]['sha256']})

    for capability in capabilities:
        for name in ROOTS[os_name][capability]:add(base+name,'public task capability: '+capability)
    # Configuration roots precede optional provider/type candidates within the
    # unchanged file bound. Their functions are not added to the source index.
    for path in sorted(board_mains):add(path,'public board configuration for type/construction projection only')
    # Provider discovery expands beyond declarations. Path terms only select
    # candidates to parse; they never establish a provider/API guarantee.
    provider_terms=[c for c in capabilities if c in {'i2c','spi','gpio'}]+['alarm','rtc','timer','clock']
    board=task['target'].get('board','').lower() if os_role=='target' else ''
    family='nrf52' if board.startswith('nrf52') else None
    provider_paths=[]
    for path in entries:
        if not allowed(path) or PurePosixPath(path).suffix not in {'.c','.rs'}:continue
        local=path.removeprefix(base)
        if any(term in PurePosixPath(local).stem.casefold() for term in provider_terms) and (
                any(path.startswith(root) for root in chip_roots) or family is not None and family in local or 'virtual' in local):
            rank=(0 if 'virtual' in local else 1,len(local),local)
            provider_paths.append((rank,path))
    for _,path in sorted(provider_paths)[:8]:add(path,'candidate provider/resource dependency; active configuration requires checking')
    if provider_paths:
        gaps.append({'scope':'provider_resolution','reason':'candidate provider set does not prove active dispatch/configuration'})
    # Follow literal C include dependencies with audited ambiguity/stop rules.
    pending=list(selected)
    from tree_sitter import Language,Parser
    import tree_sitter_c
    c_language=Language(tree_sitter_c.language())
    c_parser=Parser(c_language)
    while pending:
        path=pending.pop(0)
        if PurePosixPath(path).suffix not in {'.c','.h'}:continue
        source=workspace.read_original(path);tree=c_parser.parse(source);locations=SourceLocations(source)
        for part in descendants(tree.root_node):
            if part.type!='preproc_include':continue
            include=part.child_by_field_name('path')
            if include is None:continue
            text=source[include.start_byte:include.end_byte].decode('utf8')
            if not (text.startswith('<') and text.endswith('>') or text.startswith('"') and text.endswith('"')):
                gaps.append({'path':path,'line':locations.line(part.start_byte),'reason':'computed include unresolved'});continue
            name=text[1:-1]
            matches=[p for p in entries if allowed(p) and (p.endswith('/include/'+name) or p==str(PurePosixPath(path).parent/name))]
            if len(matches)>1:gaps.append({'path':path,'include':name,'reason':'multiple include candidates; search-path configuration unresolved'})
            for candidate in matches:
                before=len(selected);add(candidate,'include dependency of '+path+':'+str(locations.line(part.start_byte)))
                if len(selected)>before:pending.append(candidate)
    ids=[]
    for i,path in enumerate(selected):
        ids.append(workspace.add_original(f'{os_role}.framework.{i}',path,'framework_source','framework_evidence',os_role=os_role))
    index=SourceIndex(workspace.evidence())
    for id in ids:
        if workspace.entries[id]['path'] not in board_mains:index.add(id)
    type_frontier={ref for symbol in index.symbols.values() for ref in symbol.references
                   if ref[:1].isupper() and ref not in index.by_name and ref!='Self'}
    # A coincident type/file name does not select an unrelated chip provider.
    # Recover explicitly imported Rust crates from parsed use declarations;
    # only these chips and the board family are type-file candidates.
    imported_crates=set()
    if os_name=='tock':
        import tree_sitter_rust
        rust_parser=Parser(Language(tree_sitter_rust.language()))
        for path in selected:
            if not path.endswith('.rs'):continue
            source=workspace.read_original(path);tree=rust_parser.parse(source)
            for part in descendants(tree.root_node):
                if part.type!='use_declaration':continue
                identifiers=[child for child in descendants(part) if child.type=='identifier']
                if identifiers:
                    first=identifiers[0];imported_crates.add(source[first.start_byte:first.end_byte].decode())
    for path in sorted(entries):
        if not allowed(path) or PurePosixPath(path).suffix not in {'.rs','.h'}:continue
        local=PurePosixPath(path.removeprefix(base))
        if local.parts[0]=='chips' and len(local.parts)>1 and local.parts[1] not in imported_crates|{family} and not any(path.startswith(root) for root in chip_roots):continue
        stem=PurePosixPath(path).stem.replace('_','').casefold()
        if not any(name.replace('_','').casefold()==stem for name in type_frontier):continue
        before=len(selected);add(path,'type declaration dependency discovered from parsed API references')
        if len(selected)>before:
            id=workspace.add_original(f'{os_role}.framework.{len(ids)}',path,'framework_source','framework_evidence',os_role=os_role)
            ids.append(id);index.evidence=workspace.evidence();index.add(id)
    wiring=[];construction=[]
    if os_name=='tock' and os_role=='target':
        # Use the unique public Cargo board root, not a guessed chip family or
        # a same-device driver example. Merge only the selected type aliases
        # into the syntax index; never board functions or constants.
        boards=[path for path in cargo['manifests'] if PurePosixPath(path).parent.name==task['target'].get('board')]
        for manifest in boards:
            path=str(PurePosixPath(manifest).parent/'src/main.rs')
            if path not in entries:continue
            source=workspace.read_original(path)
            aliases,alias_gaps=board_type_aliases(source,set(index.by_name));gaps.extend(alias_gaps)
            components={symbol.name for symbol in index.symbols.values()
                if symbol.path.startswith(base+'boards/components/src/') and symbol.syntax_kind in {'struct_item','type_item'}}
            from .framework_wiring import construction_slice
            try:configured=construction_slice(source,components)
            except Unknown as error:configured={'statements':[],'gaps':[{'path':path,'reason':str(error)}]}
            gaps.extend(configured['gaps'])
            if not aliases and not configured['statements']:continue
            if path not in selected and len(selected)>=maximum_files:
                gaps.append({'path':path,'reason':'framework file bound prevents public board alias selection'});continue
            if path in selected:id=next(id for id in ids if workspace.entries[id]['path']==path)
            else:
                id=workspace.add_original(f'{os_role}.framework.{len(ids)}',path,'framework_source','framework_evidence',os_role=os_role)
                selected.append(path);ids.append(id)
                audit.append({'path':path,'reason':'public board aliases/construction of indexed framework types only; no device implementation', 'sha256':entries[path]['sha256']})
            board_index=SourceIndex(workspace.evidence());board_index.add(id)
            ranges={(item['alias'],item['start_line'],item['end_line']) for item in aliases}
            for sid,symbol in board_index.symbols.items():
                if (symbol.name,symbol.start_line,symbol.end_line) not in ranges:continue
                index.symbols[sid]=symbol;index.by_name[symbol.name].append(sid)
            index.materials.update(board_index.materials);index.evidence=workspace.evidence()
            wiring.extend(dict(item,material_id=id,sha256=entries[path]['sha256']) for item in aliases)
            construction.extend(dict(item,material_id=id,sha256=entries[path]['sha256']) for item in configured['statements'])
            gaps.append({'path':path,'reason':'board type aliases establish types only; active construction and provider conformance need source recovery'})
    requested=[query for capability in capabilities for query in QUERIES[os_name][capability]]
    requested.extend(item['alias'] for item in wiring)
    requested += [str(trait).split('::')[-1] for trait in (task['target'].get('interface') or {}).get('traits',[])] if os_role=='target' else []
    if os_role=='target':
        # Native event layouts are public ABI roots, rather than sensor
        # implementations or hand-selected examples. Follow their field types
        # through the same parsed declaration closure as all other API roots.
        requested += [item['structure'] for item in (task['target'].get('interface') or {}).get('native_events',[])
                      if isinstance(item,dict) and isinstance(item.get('structure'),str)]
    if os_role=='source':
        source_index=SourceIndex(workspace.evidence());external=[]
        for id,e in workspace.entries.items():
            if e['kind']=='device_source' and PurePosixPath(e['path']).suffix in {'.c','.h','.rs'}:source_index.add(id)
        defined=set(source_index.by_name)
        for symbol in source_index.symbols.values():
            external.extend(call for call in symbol.calls if call not in defined)
            external.extend(ref for ref in symbol.references if any(index.symbols[sid].syntax_kind in {
                'struct_specifier','enum_specifier','type_definition','trait_item','preproc_def','preproc_function_def'} for sid in index.by_name.get(ref,[])))
        requested.extend(external)
    requested.extend(name for name in ('handle_interrupt','command_complete','set_alarm','now','frequency','minimum_dt',
                                      'read','write','transfer','ticks_from_us','ticks_from_ms') if name in index.by_name)
    matched=list(dict.fromkeys(name for name in requested if name in index.by_name))
    for name in dict.fromkeys(requested):
        if name not in index.by_name:gaps.append({'symbol':name,'reason':'requested capability/API declaration not indexed'})
    configuration_lines=sum(len(workspace.read_original(path).splitlines()) for path in cargo['manifests'])
    configuration_lines+=sum(row['end_line']-row['start_line']+1 for row in construction)
    if configuration_lines>=maximum_lines:raise Unknown('complete required Cargo configuration exceeds framework context line budget')
    chunks=index.excerpts(matched,maximum_lines=maximum_lines-configuration_lines) if matched else {'selections':[],'gaps':[{'reason':'no required framework symbols resolved'}],'lines':0,'units':[],'dependencies':[]}
    gaps.extend(chunks['gaps'])
    for item in construction:
        chunks['selections'].append({key:item[key] for key in ('material_id','start_line','end_line')})
        chunks['lines']+=item['end_line']-item['start_line']+1
    for i,path in enumerate(cargo['manifests']):
        id=workspace.add_original(f'{os_role}.framework.config.{i}',path,'framework_document','framework_evidence',os_role=os_role)
        ids.append(id);size=len(workspace.read_original(path).splitlines());lines+=size
        chunks['selections'].append({'material_id':id,'start_line':1,'end_line':size});chunks['lines']+=size
        audit.append({'path':path,'reason':'public board Cargo dependency/configuration metadata','sha256':entries[path]['sha256']})
    for capability in capabilities:
        roots_for_capability={base+name for name in ROOTS[os_name][capability]}
        covered={workspace.entries[s['material_id']]['path'] for s in chunks['selections']}
        if roots_for_capability.isdisjoint(covered):gaps.append({'capability':capability,'reason':'no complete declaration selected for required capability'})
    gap_groups={}
    for gap in gaps:
        reason=gap['reason'];group=gap_groups.setdefault(reason,{'reason':reason,'count':0,'subjects':set()});group['count']+=1
        group['subjects'].add(str(gap.get('symbol',gap.get('path',gap.get('capability',gap.get('scope','unspecified'))))))
    prompt_gaps=[dict(group,subjects=sorted(group['subjects'])) for group in gap_groups.values()]
    scope={'os':os_name,'snapshot_materials':{id:workspace.entries[id]['sha256'] for id in ids},
           'capabilities':capabilities,'configuration_scope':'prepared public task; active provider selection must be established from raw framework/configuration evidence',
           'retrieval':{'roots':matched,'excerpts':chunks['selections'],'gaps':prompt_gaps},
           'questions':['API preconditions and types','accept/reject/completion distinction','resource ownership on every path',
                        'physical event ordering and provider conditions','clock conversion and counter range','native output units and error channels']}
    if os_role=='target':
        # This retrieval/semantic revision changes the scope identity. An older
        # shared FP remains a readonly draft, not a certified current profile;
        # the document pipeline recovers a task-local target FP from originals.
        scope['semantic_recovery_revision']='framework-action-establishment-with-abi/v2'
        scope['questions'] += [
            'Exact valid parameter/field/selector domains, including reserved/sentinel and metadata-dependent cases',
            'Actual APIs or field initialization that establish each precondition, their required actor and lifetime',
            'Success-conditioned postconditions, readiness invalidation and partial-setup rollback']
        scope['interpretation']='This is a retrieval/syntax audit, not a normative conclusion that the originals cannot establish a configured interface. Recover source-level facts from the complete excerpts, distinguish interface contracts and conditional configuration facts from actual execution/hardware assurance, and keep genuine missing facts explicit. A parser not certifying a property does not itself negate source evidence for it.'
        native=task['target'].get('interface') or {}
        # Project only OS ABI/configuration selectors. Device identity,
        # connection settings, commands and preserved acquisition policy do
        # not belong in a framework scope.
        configuration={key:native[key] for key in ('native_events','timestamp_unit','float_configuration') if key in native}
        if configuration:scope['native_abi_configuration']=configuration
    declaration_scopes=[{'symbol':unit['symbol'],'enclosing':index.symbols[unit['symbol']].enclosing}
                        for unit in chunks['units'] if index.symbols[unit['symbol']].enclosing]
    if declaration_scopes:scope['mechanical_declaration_scopes']=declaration_scopes
    control_conditions=[{'symbol':unit['symbol'],'conditions':index.symbols[unit['symbol']].conditions}
                        for unit in chunks['units'] if index.symbols[unit['symbol']].conditions]
    if control_conditions:
        scope['mechanical_control_conditions']=control_conditions
    if cargo['edges']:scope['cargo_path_dependencies']=cargo['edges']
    if wiring:scope['public_board_type_aliases']=wiring
    if construction:scope['public_board_construction']={
        'statements':construction,'scope':'parsed generic framework construction dependencies and receiver effects; conditions retained, no device implementation, execution or provider proof'}
    scope_id=workspace.add_derived(os_role+'.framework.scope',os_role+'-framework-scope.json','framework_scope','framework_context',
                                   json.dumps(scope,ensure_ascii=False,indent=2)+'\n',ids,os_role=os_role)
    audit_record={'os':os_name,'role':os_role,'roots_and_dependencies':audit,'gaps':gaps,'lines':lines,
                  'selected_lines':chunks['lines'],'selections':chunks['selections'],'semantic_units':chunks['units'],
                  'symbol_dependencies':chunks['dependencies'],'stop':'bounded_symbol_type_call_closure','syntax_gaps':index.gaps,'scope_id':scope_id,'material_ids':ids,
                  'symbol_count':len(index.symbols),'device_source_roots_excluded':True}
    audit_record['excluded_test_declarations']=index.excluded
    return ids,scope_id,audit_record
