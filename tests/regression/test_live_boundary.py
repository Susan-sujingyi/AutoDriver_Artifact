"""Live boundary exercises a local HTTP fixture; never contacts a model."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from benchmark.common.api import ROOT
from tests.common.execution.process import execute
from tests.common.sandbox import tool_roots


class Boundary(unittest.TestCase):
    def test_offline_bootstrap_keeps_config_until_consumed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "run") as directory:
            root = Path(directory)
            agent = root / "agent"
            agent.mkdir()
            record = execute([str(ROOT / ".venv/bin/python"), "-c", "print('offline')"],
                             cwd=agent, readonly=tool_roots(ROOT), writable=[agent, Path("/dev/null")],
                             environment={"PATH": "/usr/bin:/bin"}, output=root / "host", timeout=10)
            self.assertEqual(record["status"], "pass", (root / "host/stderr.log").read_text())
            self.assertFalse((root / "host/.launch.json").exists())

    def test_fixed_gateway_and_host_read_denial(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"local_fixture_only")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(dir=ROOT / "run") as directory:
                root = Path(directory)
                agent = root / "agent"
                agent.mkdir()
                secret = root / "private.txt"
                secret.write_text("host only")
                program = """
import json, os, socket, urllib.request
checks={}
try:
 open(%r).read()
except PermissionError:
 checks['host_read_denied']=True
checks['new_session_supported']=os.setsid() is None
try:
 socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
except PermissionError:
 checks['unix_connect_denied']=True
# Even a different destination yields ONLY a descriptor for the local fixture.
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with client.open('http://192.0.2.1:9/test',timeout=5) as r:
 checks['fixed_destination']=r.read().decode()=='local_fixture_only'
print(json.dumps(checks))
assert len(checks)==4 and all(checks.values())
""" % str(secret)
                record = execute(
                    [str(ROOT / ".venv/bin/python"), "-c", program], cwd=agent,
                    readonly=tool_roots(ROOT), writable=[agent, Path("/dev/null")],
                    environment={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                    output=root / "host", gateway_port=server.server_port, timeout=15)
                self.assertEqual(record["status"], "pass",
                                 (root / "host/stderr.log").read_text())
                self.assertFalse(record["transport"]["continue_used"])
                self.assertEqual(record["cleanup"]["status"], "pass")
                self.assertTrue(json.loads((root / "host/stdout.jsonl").read_text())["host_read_denied"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
