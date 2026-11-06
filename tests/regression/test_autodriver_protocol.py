"""Boundary tests for the offline protocol prototype, not contract semantics."""
import json
import tempfile
import unittest
from pathlib import Path

from method.autodriver.implementation.system.protocol.assembler import ProtocolError, assemble, check_response, digest,canonical
from method.autodriver.implementation.system.protocol.schemas import SCHEMAS
from tests.regression.autodriver_fixtures import clone, make_fixture
import jsonschema


class ProtocolBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.visible, self.registry, self.requests, self.responses = make_fixture(self.temporary.name)

    def entry(self, identifier):
        return next(e for e in self.registry["materials"] if e["id"] == identifier)

    def run_call(self, stage="dbc_recover", request=None):
        return assemble(self.visible, self.registry, request or self.requests[stage])

    def reject_call(self, stage="dbc_recover", request=None):
        with self.assertRaises(ProtocolError):
            self.run_call(stage, request)

    def reject_response(self, stage, response):
        messages, _ = self.run_call(stage)
        with self.assertRaises(ProtocolError):
            check_response(response, self.requests[stage], messages)

    def permitted_dbc_request(self, condition):
        request = clone(self.requests["dbc_recover"])
        request["condition"] = condition
        forbidden_roles = {"D": {"source_configuration", "source_evidence", "source_fp"},
                           "S": {"document_evidence"}}[condition]
        request["selections"] = [s for s in request["selections"] if s["role"] not in forbidden_roles]
        return request

    def test_schema_definitions_are_valid(self):
        for schema in SCHEMAS.values():
            jsonschema.Draft202012Validator.check_schema(schema)

    def test_five_calls_render_and_responses_check(self):
        for stage in self.requests:
            with self.subTest(stage=stage):
                messages, audit = self.run_call(stage)
                result = check_response(self.responses[stage], self.requests[stage], messages)
                self.assertTrue(result["transport_valid"])
                self.assertEqual(result["semantic_validation"]["status"], "unknown")
                self.assertTrue(audit["assembly_only"])

    def test_same_inputs_are_deterministic(self):
        self.assertEqual(self.run_call(), self.run_call())

    def test_contract_repair_keeps_candidate_text_but_only_sources_are_citable(self):
        request=clone(self.requests['bp_build']);request.update(stage='repair',repair_target='bp')
        request['selections'] += [dict(role='candidate',material_id='bp'),dict(role='internal_diagnostic',material_id='diagnostic')]
        self.entry('diagnostic')['parents']=['bp']
        messages,_=self.run_call(request=request);data=json.loads(messages[1]['content'])
        candidates=[item for item in data['context'] if item['role'] in {'candidate','internal_diagnostic','method_specification'}]
        self.assertTrue(candidates);self.assertTrue(all(item['text'] and not item['evidence_eligible'] for item in candidates))
        schema=json.dumps(data['response_schema'])
        for item in candidates:self.assertNotIn('"'+item['citation_id']+'"',schema)
        for item in data['context']:
            if item['role'] in {'dbc','target_fp','target_support'}:self.assertTrue(item['evidence_eligible'])
        response=clone(self.responses['bp_build']);response.update(call_id=request['call_id'],stage='repair')
        item=candidates[0];response['evidence_links']=[{'claim':'untrusted candidate claim','basis':'visible repair input',
            'material_id':item['material_id'],'start_line':item['start_line'],'end_line':item['end_line']}]
        with self.assertRaisesRegex(ProtocolError,'not eligible'):check_response(response,request,messages)

    def test_context_whitespace_compaction_preserves_all_fields_and_exact_text(self):
        request=clone(self.requests['dbc_recover']);request['max_context_bytes']=2000000
        original,_=self.run_call(request=request);data=json.loads(original[1]['content'])
        expected=clone(original);expected[1]['content']=canonical(data)
        request['max_context_bytes']=len(canonical(expected).encode())
        actual,audit=self.run_call(request=request)
        self.assertEqual(actual,expected);self.assertEqual(json.loads(actual[1]['content']),data)
        self.assertEqual(audit['context_serialization'],'compact_json_lossless')
        request['max_context_bytes']-=1
        self.reject_call(request=request)

    def test_bp_language_is_supplied_even_when_selected_guide_is_only_an_excerpt(self):
        for mode in ["build", "repair", "consume"]:
            with self.subTest(mode=mode):
                request = clone(self.requests["code_generate" if mode == "consume" else "bp_build"])
                request["selections"][0]["end_line"] = 1
                if mode == "repair":
                    request.update(stage="repair", repair_target="bp")
                    request["selections"] += [dict(role="candidate", material_id="bp"),
                                               dict(role="internal_diagnostic", material_id="diagnostic")]
                    self.entry("diagnostic")["parents"] = ["bp"]
                messages, audit = self.run_call(request=request)
                self.assertIn("artifact.kind", messages[0]["content"])
                self.assertIn("device_contract", messages[0]["content"])
                self.assertIn("Ref(identifier)", messages[0]["content"])
                self.assertIn("references/response-protocol.md", audit["reference_sha256"])
                self.assertIn("references/bp-language.md", audit["reference_sha256"])

    def test_contract_repair_targets_accept_their_own_context(self):
        for kind, initial, candidate in [("fp", "fp_build", "target_fp"),
                                         ("dbc", "dbc_recover", "dbc"),
                                         ("bp", "bp_build", "bp")]:
            with self.subTest(repair_target=kind):
                request = clone(self.requests[initial])
                request.update(stage="repair", repair_target=kind, call_id="repair-" + kind)
                request["selections"] += [dict(role="candidate", material_id=candidate),
                                           dict(role="internal_diagnostic", material_id="diagnostic")]
                self.entry("diagnostic")["parents"] = [candidate]
                messages, _ = self.run_call(request=request)
                response = clone(self.responses[initial])
                response.update(stage="repair", call_id=request["call_id"])
                self.assertTrue(check_response(response, request, messages)["transport_valid"])

    def test_s_and_d_permitted_minimal_calls(self):
        for condition in ["S", "D"]:
            self.run_call(request=self.permitted_dbc_request(condition))

    def test_d_rejects_source_and_configuration(self):
        request = clone(self.requests["dbc_recover"])
        request["condition"] = "D"
        self.reject_call(request=request)

    def test_s_rejects_device_documents(self):
        request = clone(self.requests["dbc_recover"])
        request["condition"] = "S"
        self.reject_call(request=request)

    def test_d_rejects_source_hidden_in_derived_artifact(self):
        request = clone(self.requests["bp_build"])
        request["condition"] = "D"
        self.reject_call("bp_build", request)

    def test_s_rejects_documents_hidden_in_derived_artifact(self):
        request = clone(self.requests["bp_build"])
        request["condition"] = "S"
        self.reject_call("bp_build", request)

    def test_fp_rejects_device_lineage(self):
        self.entry("target_framework")["parents"] = ["document"]
        self.reject_call("fp_build")

    def test_approved_flag_cannot_admit_hidden_kind(self):
        for kind in ["hidden_reference", "benchmark_feedback", "research_note", "vendor_supplement"]:
            with self.subTest(kind=kind):
                self.entry("source")["kind"] = kind
                self.reject_call()

    def test_unapproved_ancestor_is_rejected(self):
        self.entry("target_framework")["approved"] = False
        self.reject_call("bp_build")

    def test_derived_material_requires_parents(self):
        self.entry("source_fp")["parents"] = []
        self.reject_call()

    def test_absent_parent_is_rejected(self):
        self.entry("source_fp")["parents"] = ["missing"]
        self.reject_call()

    def test_provenance_cycle_is_rejected(self):
        self.entry("source_framework")["parents"] = ["source_fp"]
        self.reject_call()

    def test_duplicate_material_id_is_rejected(self):
        self.registry["materials"].append(clone(self.entry("source")))
        self.reject_call()

    def test_frozen_hash_drift_is_rejected(self):
        (self.visible / self.entry("source")["path"]).write_text("changed\n")
        self.reject_call()

    def test_final_symlink_is_rejected(self):
        path = self.visible / self.entry("source")["path"]
        path.unlink()
        path.symlink_to(self.visible / self.entry("document")["path"])
        self.reject_call()

    def test_directory_symlink_is_rejected(self):
        original = self.visible / "input"
        renamed = self.visible / "renamed-input"
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
        self.reject_call()

    def test_relative_traversal_and_absolute_paths_are_rejected(self):
        for bad in ["../input/source.c", "/tmp/source.c", "input/./source.c", "input/.env"]:
            with self.subTest(path=bad):
                self.entry("source")["path"] = bad
                self.reject_call()

    def test_task_projection_rejects_evaluation_fields(self):
        path = self.visible / self.entry("task")["path"]
        data = json.loads(path.read_text())
        data["evaluation"] = {"hidden": "never forward"}
        payload = json.dumps(data).encode()
        path.write_bytes(payload)
        self.entry("task")["sha256"] = digest(payload)
        self.reject_call()

    def test_missing_role_is_rejected(self):
        request = clone(self.requests["dbc_recover"])
        request["selections"] = [s for s in request["selections"] if s["role"] != "source_configuration"]
        self.reject_call(request=request)

    def test_duplicate_selection_is_rejected(self):
        request = clone(self.requests["dbc_recover"])
        request["selections"].append(clone(request["selections"][0]))
        self.reject_call(request=request)

    def test_budget_is_a_hard_limit(self):
        request = clone(self.requests["dbc_recover"])
        request["max_context_bytes"] = 1
        self.reject_call(request=request)

    def test_untrusted_diagnostic_producer_is_rejected(self):
        self.entry("validation")["producer"] = "benchmark_score"
        self.reject_call("code_generate")

    def test_code_gate_must_be_open(self):
        request = clone(self.requests["code_generate"])
        request["host_generation_gate"] = "closed"
        self.reject_call("code_generate", request)

    def test_code_baseline_cannot_be_an_excerpt(self):
        request = clone(self.requests["code_generate"])
        request["selections"][-1]["end_line"] = 1
        self.reject_call("code_generate", request)

    def test_repair_candidate_type_must_match(self):
        request = clone(self.requests["repair"])
        next(s for s in request["selections"] if s["role"] == "candidate")["material_id"] = "dbc"
        self.reject_call("repair", request)

    def test_response_must_match_call(self):
        response = clone(self.responses["dbc_recover"])
        response["call_id"] = "another-call"
        self.reject_response("dbc_recover", response)

    def test_unknown_or_unsupplied_evidence_is_rejected(self):
        for material in ["unknown", "target_framework"]:
            response = clone(self.responses["dbc_recover"])
            response["evidence_links"][0]["material_id"] = material
            self.reject_response("dbc_recover", response)

    def test_evidence_cannot_escape_supplied_excerpt(self):
        request = clone(self.requests["dbc_recover"])
        next(s for s in request["selections"] if s["material_id"] == "source")["end_line"] = 1
        messages, _ = self.run_call(request=request)
        response = clone(self.responses["dbc_recover"])
        response["evidence_links"][0].update(start_line=2, end_line=2)
        with self.assertRaises(ProtocolError):
            check_response(response, request, messages)

    def test_disallowed_context_requests_are_rejected(self):
        cases = [("fp_build", "device_documents"), ("bp_build", "source")]
        for stage, scope in cases:
            response = clone(self.responses[stage])
            response["context_requests"] = [dict(question="need context", scope=scope, query="x", reason="missing")]
            self.reject_response(stage, response)

    def test_condition_specific_context_requests_and_tools(self):
        for condition, forbidden in [("S", "device_documents"), ("D", "source_framework")]:
            request = self.permitted_dbc_request(condition)
            messages, _ = self.run_call(request=request)
            content = json.loads(messages[-1]["content"])
            response = clone(self.responses["dbc_recover"])
            response["artifact"] = None
            response["evidence_links"] = []
            response["outcome"] = "needs_evidence"
            response["context_requests"] = [dict(question="need context", scope=forbidden, query="x", reason="missing")]
            with self.assertRaises(ProtocolError):
                check_response(response, request, messages)
            hidden_tool = "retrieve_device_evidence" if condition == "S" else "slice_device_effects"
            self.assertNotIn(hidden_tool, content["available_tool_names"])

    def test_wrong_artifact_type_is_rejected(self):
        response = clone(self.responses["dbc_recover"])
        response["artifact"] = self.responses["bp_build"]["artifact"]
        self.reject_response("dbc_recover", response)

    def test_constructor_is_parsed_without_executable_forms(self):
        bad = ['__import__("os").system("false")', 'DeviceContract(x=[x for x in y])',
               'DeviceContract(x=object.attr)', 'DeviceContract(**data)', 'DeviceContract(x=lowercase())']
        for content in bad:
            response = clone(self.responses["dbc_recover"])
            response["artifact"]["content"] = content
            self.reject_response("dbc_recover", response)

    def test_interface_validation_does_not_claim_semantic_validation(self):
        response = clone(self.responses["dbc_recover"])
        messages, _ = self.run_call()
        result = check_response(response, self.requests["dbc_recover"], messages)
        self.assertEqual(result["semantic_validation"]["status"], "unknown")
        self.assertTrue(result["semantic_validation"]["findings"])

    def test_supplied_pinned_reference_resolves_without_claiming_evidence_entailment(self):
        response=clone(self.responses['fp_build'])
        response['artifact']['content']="FrameworkProfile('ExampleTarget',Ref('target_framework'))"
        messages,_=self.run_call('fp_build')
        result=check_response(response,self.requests['fp_build'],messages)
        findings=result['semantic_validation']['findings']
        self.assertTrue(any(f['check']=='parent_reference' and f['status']=='satisfied' for f in findings))
        self.assertEqual(result['semantic_validation']['status'],'unknown')

    def test_separate_excerpts_of_one_material_are_not_treated_as_duplicate(self):
        request=clone(self.requests['dbc_recover'])
        selected=next(s for s in request['selections'] if s['role']=='source_evidence')
        selected.update(start_line=1,end_line=1)
        request['selections'].append(dict(role='source_evidence',material_id='source',start_line=2,end_line=2))
        messages,_=self.run_call(request=request)
        excerpts=[c for c in json.loads(messages[1]['content'])['context'] if c['role']=='source_evidence']
        self.assertEqual(len(excerpts),2)

    def test_edit_outside_scope_is_rejected(self):
        response = clone(self.responses["code_generate"])
        response["artifact"]["edits"][0]["path"] = "target/other.c"
        self.reject_response("code_generate", response)

    def test_edit_baseline_must_match(self):
        response = clone(self.responses["code_generate"])
        response["artifact"]["edits"][0]["base_sha256"] = "0" * 64
        self.reject_response("code_generate", response)

    def test_create_cannot_overwrite_baseline(self):
        response = clone(self.responses["code_generate"])
        response["artifact"]["edits"][0].update(operation="create", base_sha256=None)
        self.reject_response("code_generate", response)

    def test_delete_content_must_be_null(self):
        response = clone(self.responses["code_generate"])
        response["artifact"]["edits"][0]["operation"] = "delete"
        self.reject_response("code_generate", response)

    def test_success_claim_is_not_a_protocol_outcome(self):
        response = clone(self.responses["dbc_recover"])
        response["outcome"] = "validated"
        self.reject_response("dbc_recover", response)

    def test_needs_evidence_and_blocked_have_required_explanations(self):
        for outcome in ["needs_evidence", "blocked"]:
            response = clone(self.responses["dbc_recover"])
            response.update(outcome=outcome, artifact=None, unresolved=[], context_requests=[])
            self.reject_response("dbc_recover", response)


if __name__ == "__main__":
    unittest.main()
