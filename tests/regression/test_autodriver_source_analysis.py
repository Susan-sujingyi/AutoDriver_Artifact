"""Synthetic source dependencies, configuration selection and error paths."""
from pathlib import Path
import tempfile
import unittest

from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.protocol.assembler import digest
from method.autodriver.implementation.system.source_analysis import CAnalysis,cmake_source_membership


class SourceAnalysisTests(unittest.TestCase):
    def analyze(self,source,configuration=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        (root/'input.c').write_text(source)
        evidence=Evidence(root,{'materials':[dict(id='source',path='input.c',sha256=digest(source.encode()),kind='device_source',approved=True,parents=[],os_role='source')]})
        return CAnalysis(evidence,configuration).add('source')

    def test_failure_return_controls_later_io_and_reaching_values(self):
        analysis=self.analyze('int sample(void) { int r = send(); if (r < 0) return r; int x = 9; return receive(x); }')
        report=analysis.report();facts=report['facts'];call=next(f for f in facts.values() if any(c['callee']=='receive' for c in f['calls']))
        guard=next(f for f in facts.values() if f['kind']=='guard')
        self.assertIn({'guard':guard['identity'],'value':False},call['control'])
        send=next(f for f in facts.values() if any(c['callee']=='send' for c in f['calls']))
        self.assertTrue(any(e['kind']=='data_dependency' and e['from']==send['identity'] and e['to']==guard['identity'] for e in report['dependencies']))
        error=next(f for f in facts.values() if f['kind']=='return' and not f['calls'])
        self.assertTrue(error['control'][0]['value'])
        self.assertFalse(any(e['from']==error['identity'] and e['to']==call['identity'] for e in report['dependencies']))

    def test_interprocedural_slice_keeps_numeric_helper_and_caller(self):
        analysis=self.analyze('int calc(int x) {return x * 3 / 2;}\nint sample(void) {int raw = read_value(); return calc(raw);}\nint unused(void) {return other();}')
        report=analysis.slice(['sample'])
        self.assertEqual({f['name'] for f in report['functions'].values()},{'sample','calc'})
        self.assertTrue(any(e['kind']=='argument_binding' for e in report['dependencies']))
        self.assertTrue(any(e['kind']=='return_dependency' for e in report['dependencies']))

    def test_known_disabled_macro_uses_else_without_hiding_unknown(self):
        source='int sample(void) {\n#ifdef MODE\nreturn wrong_mode();\n#else\nreturn chosen_mode();\n#endif\n}'
        known=self.analyze(source,{'MODE':False}).report()
        self.assertNotIn('wrong_mode',{c['callee'] for c in known['calls']})
        self.assertIn('chosen_mode',{c['callee'] for c in known['calls']})
        unknown=self.analyze(source).report()
        self.assertEqual({c['callee'] for c in unknown['calls']},{'wrong_mode','chosen_mode'})
        self.assertTrue(any('preprocessor' in g['reason'] for g in unknown['gaps']))

    def test_loops_do_not_duplicate_io_at_structural_nodes(self):
        report=self.analyze('int f(void) {int x=0; do { x = io(); } while (x == 0); return x;}').report()
        self.assertEqual(sum(c['callee']=='io' for c in report['calls']),1)
        self.assertTrue(any(e.get('branch')=='true' for e in report['dependencies']))

    def test_branch_merges_preserve_both_reaching_definitions(self):
        report=self.analyze('int f(int ready) {int x; if (ready) x=1; else x=2; return out(x);}').report()
        out=next(f for f in report['facts'].values() if any(c['callee']=='out' for c in f['calls']))
        incoming=[e for e in report['dependencies'] if e['kind']=='data_dependency' and e['to']==out['identity'] and e['variable']=='x']
        self.assertEqual(len(incoming),2)

    def test_io_buffer_has_conservative_data_dependency_not_an_assumed_success(self):
        report=self.analyze('int f(void) {unsigned char buf[2]; int r = bus_read(buf,2); if(r) return r; return buf[0]*256+buf[1];}').report()
        io=next(f for f in report['facts'].values() if any(c['callee']=='bus_read' for c in f['calls']))
        decoded=next(f for f in report['facts'].values() if f['kind']=='return' and 'buf[*]' in f['reads'])
        self.assertIn('buf[*]',io['may_writes'])
        self.assertTrue(any(e['kind']=='data_dependency' and e['from']==io['identity'] and e['to']==decoded['identity'] for e in report['dependencies']))
        self.assertTrue(any('actual direction/completion' in g['reason'] for g in report['gaps']))

    def test_defined_and_numeric_false_have_distinct_configuration_semantics(self):
        source='int f(void) {\n#if defined(MODE) && MODE == 0\nreturn selected();\n#else\nreturn other();\n#endif\n}'
        self.assertEqual({c['callee'] for c in self.analyze(source,{'MODE':0}).report()['calls']},{'selected'})
        self.assertEqual({c['callee'] for c in self.analyze(source,{'MODE':False}).report()['calls']},{'other'})

    def test_disabled_whole_function_is_not_a_call_graph_target(self):
        source='#ifdef MODE\nint f(void){return wrong();}\n#else\nint f(void){return chosen();}\n#endif\nint g(void){return f();}'
        report=self.analyze(source,{'MODE':False}).report()
        self.assertEqual({f['name'] for f in report['functions'].values()},{'f','g'})
        self.assertEqual(len(report['excluded']),1)
        self.assertEqual(next(c for c in report['calls'] if c['callee']=='f')['resolution'],'unique_syntax_target')
        self.assertNotIn('wrong',{c['callee'] for c in report['calls']})

    def test_build_membership_does_not_treat_frozen_disabled_file_as_active(self):
        source='zephyr_library_sources(main.c)\nzephyr_library_sources_ifdef(MODE trigger.c)\nzephyr_library_sources_ifndef(MODE other.c)\nif(UNKNOWN)\nzephyr_library_sources(maybe.c)\nendif()\n'
        records=cmake_source_membership(source,'source/module',{'MODE':False})
        states={r['path']:r['active'] for r in records}
        self.assertEqual(states,{'source/module/main.c':True,'source/module/trigger.c':False,
                                 'source/module/other.c':True,'source/module/maybe.c':None})


if __name__=='__main__':unittest.main()
