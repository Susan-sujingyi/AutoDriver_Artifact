"""Large reviews retain raw evidence and bind the full underlying graph."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.test_autodriver_pipeline import prepared
from method.autodriver.implementation.system.pipeline import Pipeline
from method.autodriver.implementation.system.context_tables import expand
from method.autodriver.implementation.system.protocol.assembler import canonical,digest
from method.autodriver.implementation.system.contract.check import check_contract


class ReviewContextTests(unittest.TestCase):
    def test_review_index_keeps_all_raw_evidence_and_an_exact_full_graph_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepared(root);p=Pipeline(root,client=lambda *a,**k:self.fail('unexpected model call'))
            facts={'program_dependencies':{'functions':{'root':{'name':'root','start_line':1,'end_line':2}},
                'calls':[{'callee':'io','caller_fact':'root:1'}],'configuration':{'MODE':True},'gaps':[{'reason':'alias unresolved'}],
                'excluded':[],'facts':{'failure':{'text':'if (error) return failure;'+'x'*70000}},'dependencies':[{'from':'guard','to':'failure'}]},
                'gaps':[{'reason':'syntax macro unresolved'}],'source_api_retrieval':{'roots':['io']},'build_membership':[]}
            serialized=canonical(facts)+'\n';p.workspace.add_derived('source.syntax','full-analysis.json','source_analysis','source_facts',serialized,['source.0','configuration.0'],os_role='source')
            selected=p.workspace.selections({'source_evidence','document_evidence','source_configuration','source_facts'})
            actual=p.review_source_selections(selected,facts,serialized)
            self.assertEqual([s for s in actual if s['role']!='source_facts'],[s for s in selected if s['role']!='source_facts'])
            self.assertEqual(p.workspace.evidence().read('source.syntax').decode(),serialized)
            self.assertEqual(p.workspace.entries['source.review-index']['parents'],['source.syntax'])
            view=expand(json.loads(p.workspace.evidence().read('source.review-index')))
            self.assertEqual(view['complete_analysis_sha256'],digest(serialized.encode()))
            self.assertEqual(view['gaps'],facts['program_dependencies']['gaps']);self.assertEqual(view['calls'],facts['program_dependencies']['calls'])
            self.assertEqual(view['functions'],facts['program_dependencies']['functions'])

    def test_malformed_physical_formula_is_a_violation_not_an_unknown_proof(self):
        _,report=check_contract("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),quantities={'v':PhysicalQuantity('(a + 1','Pa')})",'dbc')
        findings=[f for f in report['findings'] if f['check']=='physical_expression_syntax']
        self.assertEqual(findings[0]['status'],'violated')
