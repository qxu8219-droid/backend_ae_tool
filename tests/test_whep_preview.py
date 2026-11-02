import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.spike.whep_preview import make_server


class WhepPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = make_server(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_serves_isolated_browser_page_and_no_arbitrary_files(self):
        with urlopen(self.url + '/') as response:
            self.assertIn(b'WHEP', response.read())
            self.assertEqual(response.headers['Cross-Origin-Opener-Policy'], 'same-origin')
            self.assertEqual(response.headers['Cross-Origin-Embedder-Policy'], 'require-corp')
        with self.assertRaises(HTTPError) as context:
            urlopen(self.url + '/../configs/robot-asset.json')
        self.assertEqual(context.exception.code, 404)

    def test_control_is_validated_and_written_for_real_worker(self):
        request = Request(self.url + '/control', data=b'{"action":"seek","time_s":99}',
                          headers={'Content-Type':'application/json'})
        with urlopen(request) as response:
            result = json.load(response)
        command = json.loads((self.root/'control.json').read_text())
        self.assertEqual(command['time_s'], 12)
        self.assertEqual(command['request_id'], result['control']['request_id'])
        self.assertFalse(result['acknowledged_by_simulation'])

    def test_no_snapshot_is_not_reported_as_zero_pose(self):
        with self.assertRaises(HTTPError) as context:
            urlopen(self.url + '/snapshot')
        self.assertEqual(context.exception.code, 503)
        (self.root/'latest_snapshot.json').write_text(json.dumps({'epoch':7,'render_seq':12}))
        with urlopen(self.url + '/snapshot') as response:
            self.assertEqual(json.load(response), {'epoch':7,'render_seq':12})


if __name__ == '__main__':
    unittest.main()
