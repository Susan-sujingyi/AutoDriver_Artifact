"""Bounded, readonly compilation of current primary documents for checkers.

No original-source recovery, candidate code, scoring feedback or normative
revision is an input. The existing model gateway accounts every call; failed
view compilation never rejects an already generated candidate.
"""
from .document_projection import executable_view
from .protocol.assembler import canonical, MethodLimitError, digest


def compile_views(pipeline):
    for key in ('target_fp','dbc','bp') if pipeline.policy.binding_plan else ('target_fp','dbc'):
        primary=pipeline.material_ids[key]
        if pipeline.view_primary.get(key)==primary:
            continue
        kind='fp' if key=='target_fp' else key
        document=pipeline.documents[key]
        content,report=executable_view(document.raw_text,kind)
        if key=='target_fp' and content is not None:
            pipeline.view_primary[key]=primary
            continue
        origin='primary_optional_view'
        selected=[dict(role='method_specification',material_id='method'),
                  dict(role='candidate',material_id=primary)]
        if key=='bp':
            selected += [dict(role=name,material_id=pipeline.material_ids[name]) for name in ('dbc','target_fp')]
            if 'dbc' in pipeline.derived_views:
                selected.append(dict(role='validation_report',material_id=pipeline.derived_views['dbc']))
        raw=document.raw_text
        if document.availability!='present':
            content=None
            report=dict(status='not_checked',reason='no primary contract draft was produced',entailment='unverified')
            origin='missing_primary_no_compiler_call'
        elif content is None or report.get('execution_capability')=='unsupported':
            request=pipeline.request('repair',selected,
                'Compile only a derived executable view of this exact readonly primary document. '
                'Preserve gaps and one global model. Do not recover new facts, change a primary contract or inspect code.',
                repair_target=kind,view_only=True,**({'framework_role':'target'} if kind=='fp' else {}))
            origin='model_assisted_readonly_view_compilation'
            try:
                raw=pipeline.runner.run(request)
                content,report=executable_view(raw,kind)
            except (MethodLimitError,RuntimeError,OSError) as error:
                raw=''
                content=None
                report=dict(status='not_checked',reason=str(error),error_type=type(error).__name__,
                            entailment='unverified',scope='bounded view compilation failed; candidate remains eligible')
        material='document.executable-view.'+key+'.'+str(len(pipeline.workspace.entries))
        parents=list(dict.fromkeys(s['material_id'] for s in selected))
        value=dict(schema='autodriver.derived-executable-view/v1',object=key,kind=kind,
            primary_material_id=primary,primary_material_sha256=pipeline.workspace.entries[primary]['sha256'],
            primary_document_identity=document.identity,origin=origin,raw_text=raw,
            raw_sha256=digest(raw.encode()),content=content,checks=report,entailment='unverified',
            normative=False,scope='derived checker input only; no primary-document mutation or generation admission')
        pipeline.workspace.add_derived(material,material+'.json','internal_diagnostic','validation_report',
            canonical(value)+'\n',parents,producer='method_contract_checker')
        pipeline.derived_views[key]=material
        pipeline.view_primary[key]=primary
    pipeline.export_state()
