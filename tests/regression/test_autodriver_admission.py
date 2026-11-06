"""Upstream unknowns stay closed; future implementation checks are scheduled."""
import unittest

from method.autodriver.implementation.system.contract.admission import generation_admission


class GenerationAdmissionTests(unittest.TestCase):
    def reports(self):
        return {key:{'status':'satisfied','findings':[]} for key in ('dbc','target_fp','bp','binding')}

    def gap(self,check='implementation_conformance',subject='candidate',status='unknown'):
        return {'subject':subject,'status':status,'check':check,'detail':'synthetic semantic finding'}

    def test_future_candidate_is_not_a_circular_prerequisite_or_a_pass(self):
        checks=self.reports();checks['binding']={'status':'unknown','findings':[self.gap()]}
        report=generation_admission(checks)
        self.assertEqual(report['status'],'satisfied');self.assertEqual(report['coverage_status'],'unknown')
        self.assertEqual(report['candidate_conformance'],'not_checked')
        self.assertEqual(len(report['post_generation_obligations']),1)
        self.assertEqual(checks['binding']['status'],'unknown')

    def test_required_unknown_cannot_be_exempted_by_model_phase(self):
        for key,subject,check in [('binding','wait','timing_provider_evidence'),('dbc','behavior','fsm_coverage'),
                                  ('target_fp','outputs','coverage'),('bp','candidate','implementation_conformance')]:
            with self.subTest(key=key,check=check):
                checks=self.reports();finding={**self.gap(check,subject),'phase':'post_generation'}
                checks[key]={'status':'unknown','findings':[finding]}
                self.assertEqual(generation_admission(checks)['status'],'unknown')

    def test_overall_satisfied_cannot_hide_an_unknown_or_violation(self):
        for state in ('unknown','violated'):
            checks=self.reports();checks['dbc']['findings']=[self.gap('domain','quantities',state)]
            self.assertEqual(generation_admission(checks)['status'],state)

    def test_opaque_unknown_missing_report_and_violated_candidate_stay_closed(self):
        for report in (None,{'status':'unknown','findings':[]},{'status':'violated','findings':[self.gap(status='violated')]}):
            checks=self.reports();checks['binding']=report
            self.assertIn(generation_admission(checks)['status'],{'unknown','violated'})

    def test_without_binding_has_no_empty_bp_or_inherited_bp_gate(self):
        checks=self.reports();checks['binding']={'status':'violated','findings':[]};checks.pop('bp')
        report=generation_admission(checks,binding_plan=False)
        self.assertEqual(report['status'],'satisfied');self.assertEqual(report['required_reports'],['dbc','target_fp'])

    def scoped(self):
        from method.autodriver.implementation.system.contract.model import parse,identity
        contracts={'dbc':parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))",'dbc'),
            'target_fp':parse("FrameworkProfile('synthetic',Ref('scope'),spi=Unspecified('unused'))",'fp'),
            'bp':parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic')",'bp')}
        checks=self.reports()
        for key,value in checks.items():value['contract_sha256']=identity(contracts['bp' if key=='binding' else key])
        audits={key:{'schema':'autodriver.recovery-support/v1','contract_sha256':identity(value),'status':'satisfied','evidence_entailment':'unknown'}
                for key,value in contracts.items()}
        return checks,contracts,audits

    def test_universal_entailment_retains_unknown_after_bounded_support_audit(self):
        checks,contracts,audits=self.scoped()
        checks['dbc']={**checks['dbc'],'status':'unknown','findings':[self.gap('evidence_entailment','evidence')]}
        report=generation_admission(checks,contracts=contracts,support_audits=audits)
        self.assertEqual(report['status'],'satisfied');self.assertEqual(report['coverage_status'],'unknown')
        self.assertEqual(len(report['assurance_limitations']),1)
        self.assertEqual(checks['dbc']['status'],'unknown')
        for replacement in ({},dict(audits['dbc'],status='unknown'),dict(audits['dbc'],contract_sha256='0'*64)):
            audits['dbc']=replacement
            self.assertEqual(generation_admission(checks,contracts=contracts,support_audits=audits)['status'],'unknown')

    def test_unused_framework_unknown_is_not_a_device_or_contradiction_exemption(self):
        for key,subject,check,status,expected in [
            ('target_fp','spi','coverage','unknown','satisfied'),
            ('binding','fp.spi','inherited_parent_gap','unknown','satisfied'),
            ('target_fp','spi','coverage','violated','violated'),
            ('target_fp','outputs','coverage','unknown','unknown'),
            ('target_fp','unresolved.0','coverage','unknown','unknown'),
            ('dbc','spi','coverage','unknown','unknown'),
            ('binding','wait','timing_provider_evidence','unknown','unknown')]:
            with self.subTest(key=key,subject=subject,status=status):
                checks,contracts,audits=self.scoped()
                checks[key].update(status=status,findings=[self.gap(check,subject,status)])
                result=generation_admission(checks,contracts=contracts,support_audits=audits)
                self.assertEqual(result['status'],expected)
                if expected=='satisfied':
                    self.assertEqual(result['coverage_status'],'unknown')
                    self.assertEqual(len(result['unused_framework_findings']),1)
                    self.assertEqual(generation_admission(checks,contracts=contracts,support_audits=audits,binding_plan=False)['status'],
                                     'satisfied')

    def test_current_report_identity_cannot_be_replaced_by_a_scope_flag(self):
        checks,contracts,audits=self.scoped();checks['dbc']['contract_sha256']='0'*64
        report=generation_admission(checks,contracts=contracts,support_audits=audits)
        self.assertEqual(report['status'],'unknown')
        self.assertTrue(any(x['check']=='current_contract_report' for x in report['blocking_findings']))

    def test_without_bp_framework_unknown_retains_candidate_obligations(self):
        checks,contracts,audits=self.scoped()
        checks['target_fp'].update(status='unknown',findings=[self.gap('provider','i2c')])
        report=generation_admission(checks,binding_plan=False,contracts=contracts,support_audits=audits)
        self.assertEqual(report['status'],'satisfied')
        self.assertEqual(report['coverage_status'],'unknown')
        self.assertEqual(report['candidate_conformance'],'not_checked')
        self.assertEqual(len(report['candidate_framework_obligations']),1)
        for key in ('target_fp','dbc'):
            broken={**audits,key:{}}
            self.assertEqual(generation_admission(checks,binding_plan=False,contracts=contracts,support_audits=broken)['status'],'unknown')
        checks['target_fp']['contract_sha256']='0'*64
        self.assertEqual(generation_admission(checks,binding_plan=False,contracts=contracts,support_audits=audits)['status'],'unknown')
        checks,contracts,audits=self.scoped()
        for key,state in [('target_fp','violated'),('dbc','unknown')]:
            checks[key].update(status=state,findings=[self.gap('coverage','spi',state)])
            self.assertEqual(generation_admission(checks,binding_plan=False,contracts=contracts,support_audits=audits)['status'],state)
            checks,contracts,audits=self.scoped()
