"""Saved material names must not duplicate the same current source graph."""
import tempfile
from pathlib import Path
import unittest

from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.tools import ToolExecutor
from method.autodriver.implementation.system.protocol.assembler import digest


class EvidenceAliasTests(unittest.TestCase):
    def test_source_graph_uses_one_identity_but_distinct_files_stay_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.c';source.write_text('int read_value(void) { return 1; }\n')
            def record(id,path,kind='device_source',parents=()):
                return dict(id=id,path=path,kind=kind,approved=True,os_role='source',parents=list(parents),sha256=digest((root/path).read_bytes()))
            records=[record('current','source.c'),record('saved.alias','source.c')]
            evidence=Evidence(root,{'materials':records});tool=ToolExecutor(evidence,{'stage':'repair','repair_target':'dbc','condition':'SD'},configuration={})
            result=tool.execute('slice_device_effects',{'roots':['read_value']})
            self.assertEqual(len(result['functions']),1)
            self.assertEqual(tool.scope_ids('source'),['current'])
            self.assertEqual(tool.scope_ids('source',deduplicate=False),['current','saved.alias'])
            self.assertEqual(tool.events[-1]['scope_aliases']['source'][0]['canonical_material_id'],'current')
            (root/'other.c').write_bytes(source.read_bytes());records.append(record('other','other.c'))
            tool=ToolExecutor(Evidence(root,{'materials':records}),{'stage':'repair','repair_target':'dbc','condition':'SD'},configuration={})
            self.assertEqual(tool.scope_ids('source'),['current','other'])

    def test_identical_extract_only_aliases_the_same_original_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,text in [('original.pdf','PDF bytes'),('other.pdf','PDF bytes'),('extract.txt','raw extract'),('saved.txt','raw extract')]:
                (root/name).write_text(text)
            def record(id,path,kind,parents=()):
                return dict(id=id,path=path,kind=kind,approved=True,os_role='shared',parents=list(parents),sha256=digest((root/path).read_bytes()))
            records=[record('pdf','original.pdf','device_document'),record('pdf.alias','original.pdf','device_document'),
                record('other.pdf','other.pdf','device_document'),record('text','extract.txt','document_extract',['pdf']),
                record('saved.text','saved.txt','document_extract',['pdf.alias']),record('other.text','saved.txt','document_extract',['other.pdf'])]
            evidence=Evidence(root,{'materials':records});tool=ToolExecutor(evidence,{'stage':'dbc_recover','condition':'SD'})
            self.assertEqual(tool.scope_ids('device_documents'),['pdf','other.pdf','text','other.text'])
            tool.execute('retrieve_device_evidence',{'query':'raw'})
            self.assertEqual(len(tool.events[-1]['scope_aliases']['device_documents']),2)
