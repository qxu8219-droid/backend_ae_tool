"""Validate real JPEG pixels and worker acknowledgements, not browser presentation."""
import argparse
import io
import json
import statistics
import time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image
from paired_preview import decode_frame
from smoke import decode_marker_rgb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    ages, seen, commands = [], set(), []

    def frame():
        with urlopen(args.url + '/frame', timeout=5) as response:
            meta, jpeg = decode_frame(response.read())
        image = Image.open(io.BytesIO(jpeg)).convert('RGB')
        marker = decode_marker_rgb([image.getpixel(tuple(p)) for p in meta['marker_pixels']])
        assert marker == meta['capture_seq'] == meta['render_seq'] % 4096
        seen.add(meta['render_seq'])
        ages.append((time.time() - meta['captured_wall_s']) * 1000)
        return meta

    def control(command):
        request = Request(args.url + '/control', data=json.dumps(command).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            accepted = json.load(response)
        request_id = accepted['control']['request_id']
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            meta = frame()
            if meta['request_id'] >= request_id:
                commands.append({'command': command, 'request_id': request_id,
                    'frame': {k: meta[k] for k in ['epoch', 'render_seq', 'log_time_s', 'state', 'speed']}})
                return meta
            time.sleep(.02)
        raise AssertionError('Worker did not acknowledge control; is the probe still running?')

    for _ in range(10):
        frame()
        time.sleep(.04)
    meta = control({'action': 'pause'})
    time.sleep(.15)
    assert frame()['log_time_s'] == meta['log_time_s']
    assert control({'action': 'seek', 'time_s': 3})['log_time_s'] == 3
    assert abs(control({'action': 'step', 'direction': 1})['log_time_s'] - (3 + 1/60)) < 1e-9
    assert abs(control({'action': 'step', 'direction': -1})['log_time_s'] - 3) < 1e-9
    control({'action': 'speed', 'speed': .1})
    before = control({'action': 'play'})
    time.sleep(.3)
    assert 0 < frame()['log_time_s'] - before['log_time_s'] < .1
    control({'action': 'speed', 'speed': 10})
    control({'action': 'play'})
    time.sleep(.4)
    control({'action': 'pause'})
    for position in [8, 1, 11, 0, 6]:
        assert control({'action': 'seek', 'time_s': position})['log_time_s'] == position
    meta = control({'action': 'stop'})
    assert meta['log_time_s'] == 0 and meta['state'] == 'ready'
    control({'action': 'seek', 'time_s': 11.9})
    control({'action': 'play'})
    time.sleep(.2)
    assert frame()['state'] == 'ended'
    control({'action': 'play'})
    control({'action': 'pause'})
    for _ in range(30):
        frame()
        time.sleep(.035)
    report = {'status': 'pass', 'unique_jpeg_frames_checked': len(seen),
              'marker_mismatches': 0, 'commands': commands,
              'http_frame_age_ms_median': statistics.median(ages), 'http_frame_age_ms_max': max(ages),
              'browser_verified': False,
              'scope': 'HTTP JPEG pixels and real worker acknowledgements; not browser presentation'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k != 'commands'}, indent=2))


if __name__ == '__main__':
    main()
