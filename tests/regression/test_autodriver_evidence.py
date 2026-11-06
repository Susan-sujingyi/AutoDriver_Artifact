"""Synthetic evidence and frontend checks, independent of private evaluation."""
from pathlib import Path
import tempfile
import unittest
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from method.autodriver.implementation.system.evidence import Evidence,SourceIndex,extract_pdf,clang_facts
from method.autodriver.implementation.system.contract.model import ContractError
from method.autodriver.implementation.system.protocol.assembler import digest,ProtocolError


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.entries=[]

    def add(self,id,path,kind,content,parents=()):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(content if isinstance(content,bytes) else content.encode())
        self.entries.append(dict(id=id,path=path,kind=kind,sha256=digest(p.read_bytes()),approved=True,parents=list(parents),os_role='shared'))
        return p

    def evidence(self):
        return Evidence(self.root,{'materials':self.entries})

    def test_c_call_closure_and_unresolved_provider(self):
        self.add('source','source/input.c','device_source','int helper(void) { return provider(); }\nint sample(void) { if (helper()) return 1; return 0; }\n')
        index=SourceIndex(self.evidence());index.add('source')
        closure=index.closure(['sample'])
        self.assertEqual({s['name'] for s in closure['symbols']},{'sample','helper'})
        self.assertTrue(any(gap['query']=='provider' for gap in closure['gaps']))
        self.assertTrue(next(s for s in closure['symbols'] if s['name']=='sample')['conditions'])

    def test_large_line_numbers_without_native_point_properties(self):
        # Real files exceed CPython's small-int cache and triggered the pinned
        # library's Point.row bug. Compute every location from source bytes.
        source='\n'*5000+'int first(void) {return 1;}\n'+'\n'*1000+'int second(void) {return first();}\n'
        self.add('large','source/large.c','device_source',source)
        index=SourceIndex(self.evidence());index.add('large')
        symbols=index.closure(['second'])['symbols']
        self.assertEqual({s['name']:s['start_line'] for s in symbols},{'first':5001,'second':6002})

    def test_semantic_excerpts_keep_whole_distant_functions_and_their_docs(self):
        source='/* first API guarantee */\nint first(void) {return 1;}\n'+'int unrelated;\n'*2000+'/* last API guarantee */\nint last(void) {return 2;}\n'
        self.add('large','source/large.c','framework_source',source)
        index=SourceIndex(self.evidence());index.add('large')
        selected=index.excerpts(['first','last'],maximum_lines=10)
        self.assertEqual(len(selected['selections']),2);self.assertLessEqual(selected['lines'],10)
        rendered='\n'.join(self.evidence().excerpt('large',s['start_line'],s['end_line'])['text'] for s in selected['selections'])
        self.assertIn('first API guarantee',rendered);self.assertIn('last API guarantee',rendered)
        self.assertNotIn('unrelated',rendered)

    def test_rust_provider_ambiguity_and_bounded_stop(self):
        self.add('source','source/input.rs','framework_source','trait Bus { fn send(&self); }\nimpl Bus for A { fn send(&self) {} }\nfn start() { send(); }\n')
        index=SourceIndex(self.evidence());index.add('source')
        closure=index.closure(['send'],maximum_symbols=1)
        self.assertTrue(any('multiple' in gap['reason'] for gap in closure['gaps']))
        self.assertTrue(any('budget' in gap['reason'] for gap in closure['gaps']))

    def test_release_framework_slices_exclude_test_clocks_and_follow_methods(self):
        text='pub fn frequency() -> u32 {32768}\nimpl Device { pub fn arm(&self) {self.helper();} fn helper(&self) {} }\n#[cfg(test)]\nmod tests { fn frequency() -> u32 {7} }\n'
        self.add('framework','framework/input.rs','framework_source',text)
        index=SourceIndex(self.evidence());index.add('framework')
        self.assertEqual(len(index.by_name['frequency']),1);self.assertTrue(index.excluded)
        selected=index.excerpts(['arm','frequency'])
        names={index.symbols[edge['symbol']].name for edge in selected['dependencies'] if 'symbol' in edge}
        self.assertIn('helper',names)
        rendered='\n'.join(self.evidence().excerpt(s['material_id'],s['start_line'],s['end_line'])['text'] for s in selected['selections'])
        self.assertIn('fn helper',rendered)
        self.assertNotIn('fn frequency() -> u32 {7}',rendered)

    def test_pin_no_symlink_and_forbidden_ancestor(self):
        path=self.add('source','source/input.c','device_source','int a(void) {return 1;}')
        evidence=self.evidence();path.write_text('changed')
        with self.assertRaises(ProtocolError): evidence.read('source')
        path.unlink();path.symlink_to('/etc/passwd')
        with self.assertRaises(ProtocolError): evidence.read('source')
        path.unlink();path.write_text('int a(void) {return 1;}')
        self.entries[0]['kind']='hidden_reference'
        self.add('derived','output/facts.json','source_analysis','{}',parents=('source',))
        with self.assertRaises(ContractError): self.evidence().read('derived')

    def test_search_uses_literal_queries(self):
        self.add('source','source/input.c','device_source','line one\nwait anchor\nline three\n')
        evidence=self.evidence()
        self.assertEqual(evidence.search(['source'],['wait'])['excerpts'][0]['start_line'],1)
        self.assertEqual(evidence.search(['source'],['$(touch injected)'])['excerpts'],[])
        self.assertFalse((self.root/'injected').exists())
        with self.assertRaises(ContractError): evidence.excerpt('source',1,10000)

    def test_pdf_pages_coordinates_and_identity(self):
        import pymupdf
        doc=pymupdf.open();p=doc.new_page();p.insert_text((72,72),'Raw table heading and footnote');doc.new_page()
        self.add('pdf','documents/raw.pdf','device_document',doc.tobytes());doc.close()
        extracted=extract_pdf(self.evidence(),'pdf')
        self.assertEqual(len(extracted['pages']),2)
        self.assertIn('Raw table',extracted['pages'][0]['text'])
        self.assertTrue(extracted['pages'][0]['blocks'][0]['bbox'])
        self.assertTrue(extracted['pages'][1]['needs_ocr_or_image'])
        self.assertEqual(extracted['original_sha256'],self.entries[0]['sha256'])

    def test_clang_effective_branch_and_missing_header_unknown(self):
        self.add('source','source/input.c','device_source','#if ENABLED\nint value(void) {return 7;}\n#else\nint value(void) {return 2;}\n#endif\n')
        result=clang_facts(self.evidence(),'source',{'ENABLED':True})
        self.assertEqual(result['status'],'facts_extracted')
        self.assertIn('return 7',result['preprocessed']);self.assertNotIn('return 2',result['preprocessed'])
        self.add('ifdef','source/ifdef.c','device_source','#ifdef ENABLED\nint a(void) {return 7;}\n#else\nint a(void) {return 2;}\n#endif\n')
        disabled=clang_facts(self.evidence(),'ifdef',{'ENABLED':False})
        self.assertIn('return 2',disabled['preprocessed']);self.assertNotIn('return 7',disabled['preprocessed'])
        with self.assertRaises(ContractError): clang_facts(self.evidence(),'source',{'bad key':1})
        self.add('missing','source/missing.c','device_source','#include "missing.h"\n')
        self.assertEqual(clang_facts(self.evidence(),'missing',{})['status'],'unknown')

    def test_clang_timeout_retains_bound_unknown_and_never_partial_facts(self):
        self.add('source','source/input.c','device_source','int value(void) { return 7; }\n')
        timeout=subprocess.TimeoutExpired(['clang-14'],30,output=b'{"partial":true}')
        complete=SimpleNamespace(returncode=0,stdout=b'{}',stderr=b'')
        for answers,stage in [([timeout],'ast'),([complete,timeout],'preprocessing'),([FileNotFoundError('clang-14')],'ast')]:
            with self.subTest(stage=stage),patch('subprocess.run',side_effect=answers):
                result=clang_facts(self.evidence(),'source',{'ENABLED':True})
            self.assertEqual(result['status'],'unknown');self.assertIsNone(result['ast']);self.assertIsNone(result['preprocessed'])
            self.assertEqual(result['tool_gap']['stage'],stage);self.assertEqual(result['sha256'],self.entries[0]['sha256'])


if __name__=='__main__':unittest.main()
