"""Pinned recovery support and conservative framework dependencies, offline."""
import tempfile
from pathlib import Path
import unittest

from method.autodriver.implementation.system.contract.model import parse,identity,ContractError
from method.autodriver.implementation.system.contract.support import audit_support
from method.autodriver.implementation.system.contract.scope import framework_scope,path_required
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.protocol.assembler import digest


class RecoverySupportTests(unittest.TestCase):
    def test_nonempty_wrong_claim_label_is_a_reference_violation_but_missing_support_stays_unknown(self):
        from method.autodriver.implementation.system.contract.check import check_contract
        text="FrameworkProfile('synthetic',Ref('scope'),assumptions=[Constraint(True,'framework_requirement',evidence=['literal claim'])])"
        for supplied,expected in [(None,'unknown'),([], 'violated'),(['literal claim.'],'violated'),(['literal claim'],'satisfied')]:
            _,checks=check_contract(text,'fp',evidence_claims=supplied)
            finding=next(f for f in checks['findings'] if f['check']=='origin_and_evidence_reference')
            self.assertEqual(finding['status'],expected)
        _,checks=check_contract(text.replace("['literal claim']",'[]'),'fp',evidence_claims=['literal claim'])
        self.assertEqual(next(f['status'] for f in checks['findings'] if f['check']=='origin_and_evidence_reference'),'unknown')

    def test_envelope_gaps_survive_recheck_without_exempting_deterministic_gaps(self):
        from method.autodriver.implementation.system.contract.check import include_response_gaps
        report={'status':'satisfied','findings':[]}
        gaps=[{'subject':'data','reason':'missing required data','impact':'required'},
              {'subject':'clock','reason':'unresolved premise','impact':'unknown'},
              {'subject':'unused','reason':'optional extra API','impact':'optional'}]
        result=include_response_gaps(report,gaps)
        self.assertEqual(result['status'],'unknown');self.assertEqual(len(result['findings']),2)
        self.assertEqual(result['declared_response_gaps'],gaps);self.assertEqual(report['findings'],[])
        violation={'status':'violated','findings':[{'status':'violated','subject':'data','check':'domain','detail':'bad'}]}
        self.assertEqual(include_response_gaps(violation,[gaps[2]])['status'],'violated')

    def evidence(self,root):
        records=[]
        for name,kind in [('source','device_source'),('manual','document_extract'),('raw','device_document'),
                          ('framework','framework_source'),('dbc','dbc_candidate'),('fp','fp_task_local'),
                          ('integration','public_target_support'),('diagnostic','internal_diagnostic')]:
            path=root/(name+'.txt');path.write_text(name+' frozen original\nsecond line\n')
            parents={'manual':['raw'],'dbc':['source','manual'],'fp':['framework']}.get(name,[])
            records.append(dict(id=name,path=path.name,kind=kind,approved=True,os_role='shared',
                sha256=digest(path.read_bytes()),parents=parents))
        return Evidence(root,{'materials':records})

    def links(self,*names):
        return [dict(claim=name+' claim',basis='synthetic source support only',material_id=name,start_line=1,end_line=1) for name in names]

    def test_model_assisted_review_is_not_entailment_or_an_artifact_flag(self):
        contract=parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))",'dbc')
        with tempfile.TemporaryDirectory() as folder:
            evidence=self.evidence(Path(folder));links=self.links('source','manual')
            initial=audit_support(contract,'dbc',links,evidence,refinement_required=True)
            self.assertEqual(initial['status'],'unknown')
            reviewed=audit_support(contract,'dbc',links,evidence,evidence_refined=True,refinement_required=True)
            self.assertEqual(reviewed['status'],'satisfied');self.assertEqual(reviewed['evidence_entailment'],'unknown')
            self.assertEqual(reviewed['contract_sha256'],identity(contract))
            self.assertEqual(audit_support(contract,'dbc',links,evidence,refinement_required=False)['status'],'satisfied')
            for changed in (self.links('source'),self.links('manual'),self.links('diagnostic'),[],[dict(links[0],end_line=999)],
                            [dict(links[0],basis='')]):
                with self.subTest(links=changed):
                    self.assertEqual(audit_support(contract,'dbc',changed,evidence,evidence_refined=True)['status'],'unknown')
            (Path(folder)/'source.txt').write_text('changed input\n')
            with self.assertRaisesRegex(ValueError,'frozen material changed'):
                audit_support(contract,'dbc',links,evidence,evidence_refined=True)

    def test_constraint_uses_claim_labels_and_binding_requires_each_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence=self.evidence(Path(folder))
            for label,expected in [('framework claim','satisfied'),('framework','unknown')]:
                fp=parse(f"FrameworkProfile('synthetic',Ref('framework'),assumptions=[Constraint(True,'framework_requirement',evidence=[{label!r}])])",'fp')
                self.assertEqual(audit_support(fp,'fp',self.links('framework'),evidence)['status'],expected)
            bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('integration'),'synthetic')",'bp')
            self.assertEqual(audit_support(bp,'bp',self.links('dbc','fp','integration'),evidence)['status'],'satisfied')
            for missing in ('dbc','fp','integration'):
                self.assertEqual(audit_support(bp,'bp',self.links(*(x for x in ('dbc','fp','integration') if x!=missing)),evidence)['status'],'unknown')


class FrameworkDependencyTests(unittest.TestCase):
    def parents(self,*,wait=False,extra=''):
        action="Sequence(WriteBytes([1],name='send'),WaitAfterCompletion('send','1 ms'))" if wait else 'Sequence()'
        dbc=parse(f"DeviceContract('synthetic',I2C(17),FrozenPolicy(),{{}},Lifecycle(Sequence(),{action}))",'dbc')
        fp=parse("FrameworkProfile('synthetic',Ref('snapshot'),i2c=Unspecified('required bus'),spi=Unspecified('unused bus'),"
                 "alarm=Unspecified('clock'),capabilities={'first':APIContract('first',{},requires=[Ref('fp.capabilities.second')]),"
                 "'second':APIContract('second',{}),'unused':Unspecified('unused capability')})",'fp')
        bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',setup={'entry':FromFP('capabilities.first')}"+extra+")",'bp')
        return dbc,fp,bp

    def test_closed_dependencies_keep_global_gaps_and_follow_references(self):
        dbc,fp,bp=self.parents();scope=framework_scope(dbc,fp,bp)
        for path in ('i2c','outputs.anything','execution.reentry','resources.any','unresolved.0','capabilities.first','capabilities.second','capabilities',''):
            self.assertTrue(path_required(path,scope),path)
        for path in ('spi','alarm','capabilities.unused'):
            self.assertFalse(path_required(path,scope),path)
        self.assertEqual(scope['contract_sha256'],identity(fp))
        self.assertEqual(scope['bp_sha256'],identity(bp))
        self.assertTrue(path_required('alarm',framework_scope(*self.parents(wait=True))))
        self.assertTrue(path_required('spi',framework_scope(*self.parents(extra=",concurrency=FromFP('spi')"))))
        self.assertTrue(path_required('capabilities.unused',framework_scope(dbc,fp)))

    def test_missing_exact_path_is_refused_and_gpio_is_not_inferred_as_i2c(self):
        dbc,fp,_=self.parents()
        bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',setup=FromFP('capabilities.missing'))",'bp')
        with self.assertRaisesRegex(ContractError,'does not resolve'):framework_scope(dbc,fp,bp)
        gpio=parse("DeviceContract('synthetic',GPIOConnection({'data':1},'high'),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))",'dbc')
        bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic')",'bp')
        scope=framework_scope(gpio,fp,bp)
        self.assertTrue(path_required('gpio',scope));self.assertFalse(path_required('i2c',scope))
