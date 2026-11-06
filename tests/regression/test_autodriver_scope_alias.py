"""An imported scope alias denotes the same pinned bytes, never a new scope."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.test_autodriver_pipeline import prepared
from method.autodriver.implementation.system.pipeline import Pipeline
from method.autodriver.implementation.system.contract.model import parse
from method.autodriver.implementation.system.protocol.assembler import ProtocolError


class FrameworkScopeAliasTests(unittest.TestCase):
    def fixture(self,folder):
        root=Path(folder);prepared(root);pipeline=Pipeline(root);pipeline.select()
        scope=pipeline.framework_scope['target'];text=pipeline.workspace.evidence().read(scope).decode()
        pipeline.workspace.add_derived('scope.alias','scope-alias.json','framework_scope','framework_context',text,[scope],os_role='target')
        pipeline.update_evidence();return pipeline,scope

    def test_only_approved_whole_scope_with_exact_bytes_os_and_role_can_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            p,scope=self.fixture(folder);root=parse("FrameworkProfile('nuttx',Ref('scope.alias'))",'fp')
            self.assertTrue(p.fp_scope_current(root,'target'))
            self.assertFalse(p.fp_scope_current(parse("FrameworkProfile('zephyr',Ref('scope.alias'))",'fp'),'target'))
            p.workspace.add_derived('other.scope','other-scope.json','framework_scope','framework_context','{}\n',[scope],os_role='target')
            self.assertFalse(p.fp_scope_current(parse("FrameworkProfile('nuttx',Ref('other.scope'))",'fp'),'target'))
            (p.workspace.root/p.workspace.entries['scope.alias']['path']).write_text('{}\n')
            with self.assertRaises(ProtocolError):p.fp_scope_current(root,'target')

    def test_single_field_fp_repair_retains_the_visible_alias_and_resolves_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            p,scope=self.fixture(folder);content="FrameworkProfile('nuttx',Ref('scope.alias'),unresolved=['genuine remaining gap'])"
            p.workspace.add_derived('fp.seed','fp-seed.constructor','fp_task_local','target_fp',content+'\n',['scope.alias'],os_role='target')
            p.artifacts['target_fp']={'kind':'fp','format':'constructor','content':content};p.material_ids['target_fp']='fp.seed';p.checks['target_fp']={'status':'unknown','findings':[]}
            p.revisions.add('target_fp',p.artifacts['target_fp'],{})
            def client(prompt,**kwargs):
                data=json.loads(prompt);self.assertEqual(next(c['material_id'] for c in data['context'] if c['role']=='framework_context'),'scope.alias')
                base=data['contract_repair_base'];source=next(c for c in data['context'] if c['kind']=='framework_source')
                return json.dumps({'schema':'autodriver.llm-response/v0.1','call_id':data['call']['call_id'],'stage':'repair','outcome':'candidate',
                    'artifact':{'kind':'fp','format':'constructor_edits','base_material_id':base['material_id'],'base_sha256':base['sha256'],
                        'edits':[{'operation':'replace','path':['unresolved'],'value':['genuine remaining gap','newly specified gap']}]},
                    'evidence_links':[{'claim':'synthetic framework input','basis':'offline only','citations':[source['citation_id']]}],
                    'unresolved':[],'context_requests':[],'change_summary':['synthetic field edit']})
            p.runner.client=client;self.assertTrue(p.refine_contract('target_fp',{}))
            report=p.checks['target_fp'];self.assertEqual(report['status'],'unknown')
            self.assertFalse(any(f['status']=='violated' for f in report['findings']))
            self.assertTrue(any(f['check']=='parent_reference' and f['status']=='satisfied' for f in report['findings']))
