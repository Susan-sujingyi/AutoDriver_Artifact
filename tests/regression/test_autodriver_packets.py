"""Check that no prior conversation is needed to identify packet fields."""
import json
import unittest

from tests.regression import test_autodriver_protocol as fixtures
from method.autodriver.implementation.system.protocol.assembler import readable_messages
from method.autodriver.implementation.system.protocol.prompt_support import ROLE_DESCRIPTIONS
from method.autodriver.implementation.system.protocol.schemas import RESPONSE,outbound_response_schema
import jsonschema


class ModelPacketTests(unittest.TestCase):
    setUp = fixtures.ProtocolBoundaryTests.setUp
    run_call = fixtures.ProtocolBoundaryTests.run_call

    def test_actual_packet_explains_material_roles_and_includes_language(self):
        for stage in self.requests:
            with self.subTest(stage=stage):
                messages, _ = self.run_call(stage)
                data = json.loads(messages[-1]["content"])
                self.assertEqual(data["task_brief"]["本次具体目标"], self.requests[stage]["goal"])
                self.assertTrue(data["task_brief"]["应交付的对象"])
                self.assertTrue(data["task_brief"]["材料使用范围"])
                for selected in data["context"]:
                    self.assertEqual(selected["role_description"], ROLE_DESCRIPTIONS[selected["role"]])
                self.assertIn("### Ref(identifier)", messages[0]["content"])
                self.assertEqual(data["response_schema"],outbound_response_schema(self.requests[stage],data['expected_artifact_kind'],[c['citation_id'] for c in data['context'] if c['evidence_eligible']]))

    def test_fixed_response_tags_are_already_filled_for_the_current_call(self):
        for stage in self.requests:
            with self.subTest(stage=stage):
                messages, _ = self.run_call(stage)
                data = json.loads(messages[-1]["content"])
                template = data["output_template"]
                self.assertEqual(template["call_id"], self.requests[stage]["call_id"])
                self.assertEqual(template["stage"], stage)
                self.assertEqual(template["artifact"]["kind"], data["expected_artifact_kind"])
                if template["artifact"]["kind"] != "code":
                    # The string is still a placeholder, not a validated model.
                    jsonschema.Draft202012Validator(RESPONSE).validate(template)

    def test_readable_view_contains_exact_message_text_not_a_summary(self):
        messages, _ = self.run_call()
        view = readable_messages(messages)
        for message in messages:
            self.assertIn(message["content"], view)
        self.assertIn("[SYSTEM MESSAGE]", view)
        self.assertIn("[USER MESSAGE]", view)

    def test_outbound_schema_is_canonical_while_old_responses_remain_readable(self):
        from copy import deepcopy
        for stage in self.requests:
            with self.subTest(stage=stage):
                request=deepcopy(self.requests[stage])
                if stage!='code_generate':request['contract_transport']='constructor_tree'
                messages,_=self.run_call(request=request);data=json.loads(messages[1]['content']);schema=data['response_schema']
                self.assertEqual(schema['properties']['call_id'],{'const':request['call_id']})
                self.assertEqual(schema['properties']['stage'],{'const':request['stage']})
                legacy=deepcopy(self.responses[stage]);jsonschema.Draft202012Validator(RESPONSE).validate(legacy)
                with self.assertRaises(jsonschema.ValidationError):jsonschema.Draft202012Validator(schema).validate(legacy)
                canonical=deepcopy(data['output_template'])
                if canonical['artifact']['kind']=='code':canonical['artifact']=legacy['artifact']
                canonical['evidence_links']=[{'claim':'synthetic assertion','basis':'synthetic text','citations':[next(c['citation_id'] for c in data['context'] if c['evidence_eligible'])]}]
                jsonschema.Draft202012Validator(schema).validate(canonical)
                for excluded in (c for c in data['context'] if not c['evidence_eligible']):
                    refused=deepcopy(canonical)
                    refused['evidence_links'][0]['citations']=[excluded['citation_id']]
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.Draft202012Validator(schema).validate(refused)
                self.assertNotIn('material_id',data['response_schema']['properties']['evidence_links']['items']['properties'])


if __name__ == "__main__":
    unittest.main()
