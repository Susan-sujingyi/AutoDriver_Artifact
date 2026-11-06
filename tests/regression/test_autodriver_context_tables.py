"""Large mechanical graphs retain all facts, locations, edges and gaps."""
from copy import deepcopy
import json
import unittest
import yaml

from method.autodriver.implementation.system.context_tables import compact,expand,render_tool_result
from method.autodriver.implementation.system.protocol.assembler import canonical


class ContextTableTests(unittest.TestCase):
    def test_tool_carrier_keeps_every_record_and_exact_scalar_with_lower_wire_cost(self):
        value={'facts':{str(i):{'text':'if (x == "literal") { return api("value"); }\n',
             'line':i,'name':'symbol_'+str(i)} for i in range(120)},
             'values':['on','off','null',True,None,1234567890123456789012345678901234567890]}
        text,carrier=render_tool_result(value)
        self.assertIn(carrier,{'json','yaml'});self.assertEqual(expand(yaml.safe_load(text)),value)
        original=canonical(compact(value))+'\n'
        cost=lambda s:len(json.dumps(json.dumps(s,ensure_ascii=False),ensure_ascii=False).encode())
        self.assertLessEqual(cost(text),cost(original))
        self.assertNotIn('&id001',text)

    def test_full_dependency_graph_survives_compaction_and_tampering_is_refused(self):
        original={'facts':{str(i):{'identity':str(i),'kind':'call','reads':['buffer[*]'],
            'start_line':i+1,'end_line':i+1,'calls':[{'callee':'transfer'}]} for i in range(40)},
            'dependencies':[{'from':str(i),'to':str(i+1),'kind':'data_dependency','variable':'buffer[*]'} for i in range(39)],
            'gaps':[{'reason':'direction unknown','material_id':'source.0','line':i} for i in range(20)]}
        packed=compact(original)
        self.assertLess(len(canonical(packed)),len(canonical(original)))
        self.assertEqual(expand(packed),original)
        changed=deepcopy(packed);changed['value']['dependencies']['$table']['rows'].pop()
        with self.assertRaisesRegex(ValueError,'identity mismatch'):expand(changed)
        self.assertTrue(packed['strings'])
        changed=deepcopy(packed);changed['strings'][0]='changed-original-text'
        with self.assertRaises(ValueError):expand(changed)

    def test_heterogeneous_records_and_empty_values_remain_literal(self):
        value={'x':[{},None,{'other':False},[1,2]],'empty':{},'flags':[True,False],'exact':123456789012345678901234567890,'literal':'$s:0'}
        self.assertEqual(expand(compact(value)),value)
        with self.assertRaises(ValueError):compact({'$table':[]})
