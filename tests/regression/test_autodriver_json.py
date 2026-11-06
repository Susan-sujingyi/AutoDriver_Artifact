"""Exact JSON Unicode is retained; native YAML is still parsed as YAML."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.common.autodriver_json import load


class MethodJSONTests(unittest.TestCase):
    def test_json_c1_source_text_and_json_types_are_not_yaml_or_sanitized(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/'state.json';value={'text':'raw \u0093source\u0094','number':1e-3,'string':'ON'}
            p.write_text(json.dumps(value,ensure_ascii=False))
            self.assertEqual(load(p),value);self.assertIn('\u0093',load(p)['text'])
            p.write_text('text: YAML masquerading as JSON\n')
            with self.assertRaises(json.JSONDecodeError):load(p)

    def test_native_yaml_and_link_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/'task.yaml';p.write_text('task_id: synthetic\ncondition: SD\n')
            self.assertEqual(load(p),{'task_id':'synthetic','condition':'SD'})
            target=Path(temporary)/'state.json';target.write_text('{}')
            link=Path(temporary)/'linked.json';link.symlink_to(target)
            with self.assertRaisesRegex(ValueError,'Linked'):load(link)


if __name__=='__main__':unittest.main()
