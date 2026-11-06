"""Public build dependency selection, without builds or device behavior."""
import unittest
from method.autodriver.implementation.system.framework import cargo_dependencies


class CargoGraphTests(unittest.TestCase):
    def test_board_aliases_are_configuration_evidence_without_device_code_or_cfg_guesses(self):
        from method.autodriver.implementation.system.framework import board_type_aliases
        source=b'''type Bus = virtual_bus::Device<'static, chip::Twi<'static>>;
type Clock = VirtualAlarm<'static, chip::Rtc<'static>>;
type Sensor = components::device::ExistingDevice<Bus, Clock>;
const COMMAND: u8 = 91;
fn device_answer() -> u8 { COMMAND }
#[cfg(feature = "alternative")]
type Alternative = VirtualAlarm<'static, chip::OtherClock>;
#[cfg(test)]
type TestClock = VirtualAlarm<'static, chip::OtherClock>;
'''
        aliases,gaps=board_type_aliases(source,{'Device','VirtualAlarm'})
        self.assertEqual([item['alias'] for item in aliases],['Bus','Clock'])
        self.assertTrue(all(item['source'].startswith('type ') for item in aliases))
        self.assertFalse(any('ExistingDevice' in item['source'] or 'COMMAND' in item['source'] for item in aliases))
        self.assertEqual({item['symbol'] for item in gaps},{'Alternative','TestClock'})

    def test_public_native_event_layout_is_a_declaration_root(self):
        import tempfile,json,yaml
        from pathlib import Path
        from tests.regression.test_autodriver_pipeline import prepared
        from method.autodriver.implementation.system.workspace import Workspace
        from method.autodriver.implementation.system.framework import select_framework
        from method.autodriver.implementation.system.protocol.assembler import digest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared(root)
            path=root/'target/include/nuttx/uorb.h';path.write_text('typedef float sensor_data_t;\nstruct public_event {sensor_data_t reading;};\n')
            header=root/'target/include/nuttx/sensors/sensor.h';header.write_text('#include <nuttx/uorb.h>\nint sensor_register(void);\n')
            task=yaml.safe_load((root/'task.yaml').read_text());task['target']['interface']['native_events']=[{'structure':'public_event','field':'reading','unit':'Pa'}]
            (root/'task.yaml').write_text(yaml.safe_dump(task))
            manifest=json.loads((root/'materials.json').read_text())
            for p in (path,header,root/'task.yaml'):manifest['files'][p.relative_to(root).as_posix()]={'sha256':digest(p.read_bytes()),'executable':False}
            (root/'materials.json').write_text(json.dumps(manifest))
            w=Workspace(root).bootstrap();_,_,audit=select_framework(w,'target')
            names={part['symbol'].rsplit(':',1)[-1] for part in audit['semantic_units']}
            self.assertIn('public_event',names);self.assertIn('sensor_data_t',names)

    def test_only_reachable_public_crates_are_candidates(self):
        files={
            'target/boards/vendor/board/Cargo.toml':'[dependencies]\nperipheral={path="../../../chips/related"}\n[target.\'cfg(other)\'.dependencies]\nmaybe={path="../../../chips/maybe"}\n',
            'target/chips/related/Cargo.toml':'[dependencies]\nkernel={path="../../kernel"}\nexternal={path="../../../../private"}\n',
            'target/chips/maybe/Cargo.toml':'[package]\nname="maybe"\n',
            'target/kernel/Cargo.toml':'[package]\nname="kernel"\n',
            'target/chips/unrelated/Cargo.toml':'[package]\nname="unrelated"\n'}
        class Public:
            manifest={'files':files}
            def __init__(self):self.reads=[]
            def read_original(self,path):self.reads.append(path);return files[path].encode()
        workspace=Public();graph=cargo_dependencies(workspace,'target/','board')
        self.assertIn('target/chips/related/',[root+'/' for root in graph['crate_roots']])
        self.assertNotIn('target/chips/unrelated/Cargo.toml',workspace.reads)
        self.assertTrue(any('conditional' in gap['reason'] for gap in graph['gaps']))
        self.assertTrue(any('absent from approved' in gap['reason'] for gap in graph['gaps']))
        self.assertFalse(any('private' in path for path in workspace.reads))
        limited=cargo_dependencies(Public(),'target/','board',maximum_manifests=1)
        self.assertEqual(len(limited['manifests']),1);self.assertTrue(any('bound' in gap['reason'] for gap in limited['gaps']))


if __name__=='__main__':unittest.main()
