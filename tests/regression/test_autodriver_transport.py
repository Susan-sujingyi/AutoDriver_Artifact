"""Tree carrier preserves constructor semantics; it never repairs assertions."""
from fractions import Fraction
import unittest

from method.autodriver.implementation.system.transport import render_tree
from method.autodriver.implementation.system.transport import resolve_citations
from method.autodriver.implementation.system.contract.model import parse,ContractError
from method.autodriver.implementation.system.syntax import normalize_contract_response


class TreeTransportTests(unittest.TestCase):
    def test_raw_json_decimals_keep_every_digit_without_a_float_round_trip(self):
        from method.autodriver.implementation.system.calls import decode_response
        raw='{"artifact":{"kind":"fp","format":"constructor_tree","content":{"$constructor":"FrameworkProfile","os":"synthetic","snapshot":{"$constructor":"Ref","identifier":"scope"},"outputs":{"x":{"$constructor":"OutputEncoding","storage_type":"f32","unit":"Pa","scale":0.100000000000000000000001}}}}}'
        response,audit=decode_response(raw)
        self.assertEqual(audit['decimal_literals'][0]['literal'],'0.100000000000000000000001')
        normalized,_=normalize_contract_response(response)
        value=parse(normalized['artifact']['content'],'fp')
        self.assertEqual(value['outputs']['x']['scale'],Fraction('0.100000000000000000000001'))
        self.assertNotEqual(value['outputs']['x']['scale'],Fraction('0.1'))
        edit='{"artifact":{"kind":"fp","format":"constructor_edits","edits":[{"value":1.234567890123456789e-3}]}}'
        response,audit=decode_response(edit)
        self.assertEqual(response['artifact']['edits'][0]['value'],{'$decimal':'1.234567890123456789e-3'})
        self.assertEqual(audit['policy'],'json-exact-decimal-carrier/v1')
        response,audit=decode_response(edit.replace('1.234567890123456789e-3','"1.234567890123456789e-3"'))
        self.assertIsNone(audit);self.assertEqual(response['artifact']['edits'][0]['value'],'1.234567890123456789e-3')

    def test_named_tree_variadic_arguments_preserve_the_existing_language(self):
        tree={'$constructor':'DeviceContract','device':'synthetic','connection':{'$constructor':'I2C','address_7bit':7},
            'selected_policy':{'$constructor':'FrozenPolicy'},'applicable_conditions':{},
            'behavior':{'$constructor':'Sequence','steps':[{'$constructor':'WriteBytes','data':[1,2],'name':'a'},{'$constructor':'WriteBytes','data':[3],'name':'b'}]}}
        root=parse(render_tree(tree,'dbc'),'dbc')
        self.assertEqual(len(root['behavior']['steps']),2);self.assertEqual(root['behavior']['steps'][1]['name'],'b')

    def test_excerpt_handles_never_fill_unprovided_intervening_lines(self):
        import json
        context=[{'citation_id':'ca','material_id':'raw','sha256':'a'*64,'start_line':10,'end_line':12},
                 {'citation_id':'cb','material_id':'raw','sha256':'a'*64,'start_line':30,'end_line':33}]
        messages=[{'content':json.dumps({'context':context})}]
        response={'evidence_links':[{'claim':'same assertion','basis':'literal provided evidence','citations':['ca','cb']}]}
        result,audit=resolve_citations(response,messages)
        self.assertEqual([(r['start_line'],r['end_line']) for r in result['evidence_links']],[(10,12),(30,33)])
        self.assertIn('citations',response['evidence_links'][0]);self.assertEqual(len(audit['resolved']),2)
        response['evidence_links'][0]['citations']=['missing']
        with self.assertRaisesRegex(ContractError,'unprovided citation'):resolve_citations(response,messages)

    def test_visible_candidates_cannot_be_used_as_assertion_sources_in_either_carrier(self):
        import json
        context=[{'citation_id':'c1','material_id':'candidate','sha256':'a'*64,'start_line':1,'end_line':3,'evidence_eligible':False},
                 {'citation_id':'c2','material_id':'parent','sha256':'b'*64,'start_line':1,'end_line':3,'evidence_eligible':True}]
        messages=[{'content':json.dumps({'context':context})}]
        for citation in ({'citations':['c1']},{'material_id':'candidate','start_line':1,'end_line':2}):
            with self.subTest(citation=citation),self.assertRaisesRegex(ContractError,'not eligible'):
                resolve_citations({'evidence_links':[dict(claim='assertion',basis='supplied candidate',**citation)]},messages)
        response={'evidence_links':[{'claim':'parent assertion','basis':'read-only parent','citations':['c2']}]}
        actual,_=resolve_citations(response,messages)
        self.assertEqual(actual['evidence_links'][0]['material_id'],'parent')
    def test_literals_fields_and_delimiters_round_trip_without_binary64(self):
        tree={'$constructor':'FrameworkProfile','args':['synthetic',{'$constructor':'Ref','args':['scope'],'fields':{}}],
            'fields':{'outputs':{'x':{'$constructor':'OutputEncoding','args':['f32','Pa'],
                'fields':{'scale':{'$decimal':'0.100000000000000000000001'},'offset':0,'range':[-10,10]}}},
                'unresolved':['literal } ) [ and quotes " unchanged']}}
        text=render_tree(tree,'fp');value=parse(text,'fp')
        self.assertEqual(value['outputs']['x']['scale'],Fraction('0.100000000000000000000001'))
        response={'artifact':{'kind':'fp','format':'constructor_tree','content':tree}}
        normalized,audit=normalize_contract_response(response)
        self.assertEqual(normalized['artifact']['format'],'constructor');self.assertEqual(audit['policy'],'constructor-json-tree-carrier/v1')
        self.assertEqual(response['artifact']['format'],'constructor_tree')

    def test_unknown_fields_have_actionable_diagnostics_without_deletion(self):
        tree={'$constructor':'FrameworkProfile','args':['x',{'$constructor':'Ref','args':['s'],'fields':{}}],'fields':{'invented':True}}
        text=render_tree(tree,'fp');self.assertIn('invented=True',text)
        with self.assertRaisesRegex(ContractError,'FrameworkProfile.invented'):parse(text,'fp')

    def test_code_symbols_wrong_layers_float_detours_and_reserved_tags_refuse(self):
        for tree in ({'$constructor':'eval','args':[],'fields':{}},
                     {'$constructor':'DeviceContract','args':[],'fields':{}},
                     {'$constructor':'FrameworkProfile','args':[0.1],'fields':{}},
                     {'$constructor':'FrameworkProfile','args':[],'fields':{},'extra':1}):
            with self.subTest(tree=tree),self.assertRaises(ContractError):render_tree(tree,'fp')


if __name__=='__main__':unittest.main()
