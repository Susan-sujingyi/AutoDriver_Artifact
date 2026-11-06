"""Offline invalid saved FP routing; synthetic prepared input."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.regression.test_autodriver_pipeline import prepared
from method.autodriver.implementation.system.contract.model import parse
from method.autodriver.implementation.system.protocol.assembler import digest

from method.autodriver.implementation.system.pipeline import Pipeline


class SavedFrameworkTests(unittest.TestCase):
    def test_invalid_current_saved_profile_gets_fp_only_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepared(root);seen=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt);call=data['call'];seen.append(call)
                self.assertEqual(call['stage'],'repair');self.assertEqual(call['repair_target'],'fp');self.assertEqual(call['condition'],'FRAMEWORK')
                self.assertEqual(len([c for c in data['context'] if c['role']=='candidate']),1)
                self.assertFalse(any(c['kind']=='device_source' for c in data['context']))
                source=next(c for c in data['context'] if c['role']=='framework_context');evidence=next(c for c in data['context'] if c['role']=='framework_evidence')
                return json.dumps({'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':call['stage'],'outcome':'candidate',
                    'artifact':{'kind':'fp','format':'constructor','content':"FrameworkProfile('zephyr',Ref("+repr(source['material_id'])+"))"},
                    'evidence_links':[{'claim':'synthetic only','material_id':evidence['material_id'],'start_line':evidence['start_line'],'end_line':evidence['start_line'],'basis':'synthetic raw declaration'}],
                    'unresolved':[],'context_requests':[],'change_summary':['synthetic same-object semantic repair']})
            p=Pipeline(root,client=client);p.select();scope=p.framework_scope['source']
            text="FrameworkProfile('zephyr',Ref("+repr(scope)+"),spi=AsyncI2CContract('i','w','r',[],[],[],[],'p'))"
            p.workspace.add_derived('old.fp','old.fp.constructor','fp_task_local','candidate',text+'\n',[scope],os_role='source')
            p.saved_objects['source_fp']={'root':parse(text,'fp'),'artifact':{'kind':'fp','format':'constructor','content':text},
                'origin':{'request_id':'synthetic','receipt_sha256':'1'*64},'checks':{'status':'violated','findings':[{'subject':'spi','status':'violated','check':'framework_slot_type','detail':'wrong bus type'}]}}
            self.assertTrue(p.fp('source'));self.assertEqual(len(seen),1)
            self.assertEqual(p.artifacts['source_fp']['content'],"FrameworkProfile('zephyr',Ref("+repr(scope)+"))")


if __name__=='__main__':unittest.main()
