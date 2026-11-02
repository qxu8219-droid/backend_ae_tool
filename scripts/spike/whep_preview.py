"""Local P0 WHEP page and the existing experimental control mailbox."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

try:
    from .paired_preview import PreviewHandler, PreviewServer
except ImportError:
    from paired_preview import PreviewHandler, PreviewServer


class WhepHandler(PreviewHandler):
    def end_headers(self):
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        super().end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        files = {'/':('whep_preview.html','text/html; charset=utf-8'),
                 '/whep_client.js':('whep_client.js','text/javascript; charset=utf-8'),
                 '/sei_transform.js':('sei_transform.js','text/javascript; charset=utf-8'),
                 '/frame_pair.js':('frame_pair.js','text/javascript; charset=utf-8'),
                 '/h264_sei.js':('h264_sei.js','text/javascript; charset=utf-8')}
        if path in files:
            name, mime = files[path]
            source = Path(__file__).with_name(name)
            if not source.is_file():
                self._json(404, {'error':'Probe resource is not installed'})
                return
            self._respond(200, source.read_bytes(), mime)
        elif path == '/snapshot':
            try:
                data = json.loads((self.server.output_dir/'latest_snapshot.json').read_text())
            except FileNotFoundError:
                self._json(503, {'error':'No simulation snapshot yet'})
                return
            self._json(200, data)
        elif path == '/status':
            self._json(200, {'ok':True,'mode':'P0 WHEP',
                'whep_url':'http://127.0.0.1:8889/stream/whep','browser_sync_verified':False})
        else:
            self._json(404, {'error':'Unknown endpoint'})


def make_server(output_dir: Path, port: int = 8768):
    server = PreviewServer(output_dir, port)
    server.RequestHandlerClass = WhepHandler
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    server = make_server(args.output_dir, args.port)
    print(f'P0 WHEP viewer: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
