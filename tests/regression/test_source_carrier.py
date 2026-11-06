"""Bounded source transport and original declaration projection; synthetic only."""
import json
from pathlib import Path
import tempfile
import unittest

from test_framework_action_semantics import public_workspace, HEADER, PROVIDER
from method.autodriver.implementation.system.document_code import extract_code, single_source_target
from method.autodriver.implementation.system.document_pipeline import DocumentPipeline
from method.autodriver.implementation.system.evidence import Evidence, SourceIndex
from method.autodriver.implementation.system.protocol.assembler import ProtocolError, digest


class SourceCarrierTests(unittest.TestCase):
    def request(self, path='target/native.rs', existing=False):
        return dict(editable_paths=[path], base_files={path:'a'*64} if existing else {})

    def test_complete_source_preserves_bytes_and_binds_only_host_metadata(self):
        source='// UTF-8: 温度\npub fn line() { let s = "literal\\n and \\"quoted\\""; }\n'
        for existing in (False,True):
            for language in ('rust',''):
                with self.subTest(existing=existing,language=language):
                    artifact,report=extract_code('```'+language+'\n'+source+'```',self.request(existing=existing))
                    edit=artifact['edits'][0]
                    self.assertEqual(edit['content'],source)
                    self.assertEqual(edit['path'],'target/native.rs')
                    self.assertEqual(edit['operation'],'replace' if existing else 'create')
                    self.assertEqual(edit['base_sha256'],'a'*64 if existing else None)
                    self.assertFalse(report['envelope_normalization']['source_content_normalized'])

    def test_fence_never_infers_which_file_or_language_or_selects_prose(self):
        raw='```c\nint native(void) { return 0; }\n```'
        for request in [dict(editable_paths=['target/'],base_files={}),
                dict(editable_paths=['target/a.c','target/b.c'],base_files={}),
                self.request()]:
            with self.subTest(request=request),self.assertRaises(ProtocolError):extract_code(raw,request)
        for malformed in [raw+'\nexplanation','explanation\n'+raw,raw+'\n'+raw,
                '```c\nint x;```','```c\nint x;\n```\n```']:
            with self.subTest(raw=malformed),self.assertRaises(ProtocolError):
                extract_code(malformed,self.request('target/native.c'))

    def test_auxiliary_directory_keeps_the_unique_host_bound_source_file(self):
        request=dict(editable_paths=['target/native.c','target/native/'],base_files={})
        source='int native(void) { return 0; }\n'
        artifact,report=extract_code('```c\n'+source+'```',request)
        self.assertEqual(artifact['edits'],[dict(path='target/native.c',operation='create',base_sha256=None,content=source)])
        self.assertEqual(report['envelope_normalization']['target']['path'],'target/native.c')

    def test_existing_json_carriers_remain_available_and_enforce_original_hash(self):
        artifact=dict(kind='code',format='file_edits',edits=[dict(path='target/native.rs',
            operation='replace',base_sha256='a'*64,content='pub fn native() {}\n')])
        for raw in [json.dumps(artifact),'```json\n'+json.dumps(artifact)+'\n```',
                '```\n'+json.dumps(artifact)+'\n```']:
            parsed,_=extract_code(raw,self.request(existing=True));self.assertEqual(parsed,artifact)
        artifact['edits'][0]['base_sha256']='b'*64
        with self.assertRaises(ProtocolError):extract_code(json.dumps(artifact),self.request(existing=True))

    def malformed_envelope(self, suffix=']}', tail=None):
        artifact=dict(kind='code',format='file_edits',edits=[dict(path='target/native.rs',
            operation='create',base_sha256=None,content='pub fn native() { let s = "]}\\n"; }\n')])
        tail='"evidence_links":[]}' if tail is None else tail
        return '{"outcome":"candidate","artifact":'+json.dumps(artifact)+suffix+','+tail,artifact

    def test_redundant_outer_closers_preserve_code_and_all_tail_metadata(self):
        raw,artifact=self.malformed_envelope()
        parsed,report=extract_code(raw,self.request())
        self.assertEqual(parsed,artifact)
        self.assertEqual(report['original_envelope_metadata'],dict(outcome='candidate'))
        self.assertEqual(report['envelope_normalization']['removed_outer_text'],']}')
        self.assertEqual(report['raw_response_sha256'],digest(raw.encode()))

    def test_malformed_tail_duplicate_key_or_source_is_never_discarded_or_repaired(self):
        for tail in ['"evidence_links":bogus}', '"artifact":null}', '"evidence_links":[]} prose']:
            raw,_=self.malformed_envelope(tail=tail)
            with self.subTest(tail=tail),self.assertRaises(ProtocolError):extract_code(raw,self.request())
        raw,_=self.malformed_envelope()
        for malformed in [raw.replace('"content": "','"content": unquoted "',1),
                raw.replace('"outcome":"candidate",','"outcome":"candidate","outcome":"draft",',1),
                raw.replace('"path": "target/native.rs"','"path": "target/other.rs"',1)]:
            with self.subTest(raw=malformed),self.assertRaises(ProtocolError):extract_code(malformed,self.request())

    def test_actual_pipeline_accepts_source_fence_without_code_repair_or_format_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);public_workspace(root);seen=[]
            import yaml
            from method.autodriver.implementation.system.protocol.assembler import canonical
            task=yaml.safe_load((root/'task.yaml').read_text())
            task['submission']['editable_paths'].append('drivers/sensors/synthetic/')
            (root/'task.yaml').write_text(yaml.safe_dump(task))
            manifest=json.loads((root/'materials.json').read_text())
            manifest['files']['task.yaml']['sha256']=digest((root/'task.yaml').read_bytes())
            manifest['identity']=digest(canonical(manifest['files']).encode())
            (root/'materials.json').write_text(json.dumps(manifest))
            def client(prompt,**kwargs):
                payload=json.loads(prompt);seen.append(payload)
                if payload['expected_artifact_kind']!='code':return 'Readable draft with explicit unknown provider guarantees.'
                self.assertEqual(payload['preferred_code_carrier']['target']['path'],'target/drivers/sensors/synthetic.c')
                return '```c\nint synthetic_register(void) { return 0; }\n```'
            pipeline=DocumentPipeline(root,client=client,variant_name='without_source_framework_lifting')
            result=pipeline.run()
            self.assertEqual(result['status'],'candidate_proposed')
            self.assertEqual(pipeline.code_repairs_used(),0)
            self.assertFalse((root/'target/drivers/sensors/synthetic.c').exists())
            self.assertEqual(sum(x['expected_artifact_kind']=='code' for x in seen),1)


class MacroDeclarationTests(unittest.TestCase):
    def projection(self, text, roots, **kwargs):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);(root/'raw.h').write_text(text)
        evidence=Evidence(root,dict(materials=[dict(id='raw',path='raw.h',kind='framework_source',
            approved=True,parents=[],os_role='target',sha256=digest(text.encode()))]))
        index=SourceIndex(evidence);index.add('raw')
        result=index.excerpts(roots,declarations_only=True,**kwargs)
        rendered=''.join(evidence.excerpt(x['material_id'],x['start_line'],x['end_line'],maximum_lines=2000)['text'] for x in result['selections'])
        return result,rendered,index

    def test_selector_macro_block_uses_syntax_adjacency_without_prefix_heuristic(self):
        source='''#define NOTHING_SELECTED 0
/* Named selector values; original declarations do not prove validity. */
#define SELECT_THERMAL 1
#define HUMID_CHANNEL 2
#define PUBLIC_SELECTOR_LIMIT 3
struct public_entry { int type; };
static int validate(struct public_entry *entry) {
 return entry->type < PUBLIC_SELECTOR_LIMIT;
}
'''
        result,text,index=self.projection(source,['validate'])
        self.assertIn('#define SELECT_THERMAL 1',text);self.assertIn('#define HUMID_CHANNEL 2',text)
        self.assertNotIn('return entry->type',text)
        self.assertTrue(any('projection' in unit for unit in result['units']))
        bounded,small,_=self.projection(source,['PUBLIC_SELECTOR_LIMIT'],maximum_lines=2)
        self.assertTrue(bounded['gaps']);self.assertEqual(small,'')

    def test_blocks_stop_at_declarations_and_conditional_branches_and_keep_unknowns(self):
        source='''#define UNRELATED_VALUE 91
struct boundary { int field; };
#if CONFIG_VARIANT
#define PICKED_VALUE 2
#define LIMIT 3
#else
#define OTHER_VARIANT 8
#endif
#define OUTSIDE_BRANCH 17
'''
        result,text,index=self.projection(source,['LIMIT'])
        self.assertIn('PICKED_VALUE',text);self.assertNotIn('UNRELATED_VALUE',text)
        self.assertNotIn('OTHER_VARIANT',text);self.assertNotIn('OUTSIDE_BRANCH',text)
        self.assertTrue(index.symbols[index.by_name['LIMIT'][0]].conditions)

    def test_target_fp_and_code_both_receive_original_selector_declarations(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);public_workspace(root)
            types='''#define RESERVED_KIND 0
#define VALID_KIND 1
#define KIND_COUNT 2
struct public_record { int reading; };
struct public_meta { unsigned span; const char *name; };
'''
            path='target/include/nuttx/registry_types.h';(root/path).write_text(types)
            manifest=json.loads((root/'materials.json').read_text())
            manifest['files'][path]=dict(sha256=digest(types.encode()),executable=False)
            from method.autodriver.implementation.system.protocol.assembler import canonical
            manifest['identity']=digest(canonical(manifest['files']).encode())
            (root/'materials.json').write_text(json.dumps(manifest));seen=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt)
                if not data['call'].get('view_only') and (data['expected_artifact_kind']=='code' or data['call'].get('framework_role')=='target'):
                    originals=''.join(x['text'] for x in data['context'] if x['kind']=='framework_source')
                    self.assertIn('#define VALID_KIND 1',originals);seen.append(data['expected_artifact_kind'])
                    self.assertNotIn('unrelated_device.c',originals)
                if data['expected_artifact_kind']=='code':return '```c\nint synthetic_register(void) { return 0; }\n```'
                return 'Readable framework draft; selector validity needs registration metadata as well as constants.'
            pipeline=DocumentPipeline(root,client=client,variant_name='without_source_framework_lifting')
            self.assertEqual(pipeline.run()['status'],'candidate_proposed')
            self.assertEqual(seen,['fp','code'])


if __name__=='__main__':unittest.main()
