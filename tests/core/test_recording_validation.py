"""Independent fixtures prove the recording checker reads every encoded frame."""
import json
import hashlib
from pathlib import Path
import struct
import zlib

import pytest

cv2 = pytest.importorskip('cv2', reason='recording validation uses optional system OpenCV')
np = pytest.importorskip('numpy', reason='recording validation uses optional system NumPy')


def recording_fixture(tmp_path, bad_frame=None, unreadable_frame=None):
    """The fixture stamp uses stdlib CRC, never the validator's decoder."""
    width, height = 640, 180
    points = [[10 + i % 32 * 19, 12 + i // 32 * 16] for i in range(64)]
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:] = (27, 15, 8)
    capture = 0x12345678
    crc = zlib.crc32(struct.pack('>I', capture))
    for index, (x, y) in enumerate(points):
        word = capture if index < 32 else crc
        image[y-4:y+5, x-4:x+5] = (30, 230, 30) if word >> (index % 32) & 1 else (30, 30, 230)
    cv2.putText(image, 'CAPTURE 305419896', (20, 110), cv2.FONT_HERSHEY_SIMPLEX, .7, (232, 216, 69), 2)
    reference = tmp_path / 'reference.png'
    cv2.imwrite(str(reference), image)
    observation = {
        'kind': 'capture', 'snapshot': {'capture_id': capture, 'session_id': 'fixture'},
        'canvas': {'x': 0, 'y': 0, 'width': width, 'height': height, 'intrinsicWidth': width, 'intrinsicHeight': height},
        'layout': {'sourceWidth': width, 'sourceHeight': height, 'markerPixels': points,
                   'imageRect': {'x': 0, 'y': 0, 'width': width, 'height': height}},
        'reference': reference.name,
        'regions': [{'name': 'capture', 'x': 10, 'y': 82, 'width': 350, 'height': 35}],
    }
    observations = tmp_path / 'recording-observations.jsonl'
    observations.write_text(json.dumps(observation) + '\n')
    video = tmp_path / 'screen.avi'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 25, (width, height))
    assert writer.isOpened()
    for index in range(12):
        frame = image.copy()
        if index == bad_frame:
            frame[82:117, 10:360] = (27, 15, 8)
            cv2.putText(frame, 'CAPTURE 305419897', (20, 110), cv2.FONT_HERSHEY_SIMPLEX, .7, (232, 216, 69), 2)
        if index == unreadable_frame:
            frame[:40] = (80, 80, 80)
        writer.write(frame)
    writer.release()
    return video, observations


def test_every_encoded_frame_is_compared_with_independent_panel_reference(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path)
    result = validate_recording(video, observations)
    assert result['decoded_frames'] == 12
    assert result['counts']['matched'] == 12
    assert result['strict_full_frame_passed'] is True


def test_one_wrong_panel_between_good_frames_is_detected(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path, bad_frame=7)
    result = validate_recording(video, observations)
    assert result['decoded_frames'] == 12
    assert result['counts']['panel_mismatch'] == 1
    assert result['counts']['matched'] == 11
    assert result['failures'][0]['frame_index'] == 7
    assert result['strict_full_frame_passed'] is False


def test_unreadable_stamp_is_reported_as_coverage_gap_not_success(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path, unreadable_frame=5)
    result = validate_recording(video, observations)
    assert result['counts']['unreadable_stamp'] == 1
    assert result['counts']['matched'] == 11
    assert result['strict_full_frame_passed'] is False


def test_crc_corruption_cannot_select_a_matching_panel(tmp_path):
    from scripts.validate_recording import decode_identity
    video, observations = recording_fixture(tmp_path)
    entry = json.loads(observations.read_text())
    frame = cv2.imread(str(tmp_path / entry['reference']))
    x, y = entry['layout']['markerPixels'][42]
    frame[y-4:y+5, x-4:x+5] = (30, 30, 230) if frame[y, x, 1] > frame[y, x, 2] else (30, 230, 30)
    result = decode_identity(frame, entry)
    assert result['status'] == 'crc_mismatch'


def test_reused_capture_ids_do_not_choose_the_wrong_session_template(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path)
    entry = json.loads(observations.read_text())
    wrong = cv2.imread(str(tmp_path / entry['reference']))
    wrong[82:117, 10:360] = 0
    cv2.imwrite(str(tmp_path / 'wrong.png'), wrong)
    duplicate = {**entry, 'reference': 'wrong.png', 'snapshot': {**entry['snapshot'], 'session_id': 'other'}}
    observations.write_text(json.dumps(duplicate) + '\n' + json.dumps(entry) + '\n')
    result = validate_recording(video, observations)
    assert result['counts']['matched'] == 12
    assert result['strict_full_frame_passed'] is True


def active_window(tmp_path, *, end_id=0x12345678):
    path = tmp_path / 'active-window.json'
    path.write_text(json.dumps({'minimum_duration_s': .3,
        'start': {'session_id': 'fixture', 'capture_ids': [0x12345678]},
        'end': {'session_id': 'fixture', 'capture_ids': [end_id]}}))
    return path


def test_predeclared_window_counts_every_interior_frame(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path, unreadable_frame=5)
    result = validate_recording(video, observations, window=active_window(tmp_path))
    assert result['active_window']['decoded_frames'] == 12
    assert result['active_window']['counts']['unreadable_stamp'] == 1
    assert result['active_window']['passed'] is False


def test_predeclared_window_reports_outer_blank_frame_without_hiding_it(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path, unreadable_frame=0)
    result = validate_recording(video, observations, window=active_window(tmp_path))
    assert result['strict_full_frame_passed'] is False
    assert result['counts']['unreadable_stamp'] == 1
    assert result['active_window']['passed'] is True
    assert result['active_window']['decoded_frames'] == 11
    assert result['active_window']['outside_window_counts']['unreadable_stamp'] == 1


def test_missing_predeclared_anchor_fails_instead_of_selecting_a_good_interval(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path)
    result = validate_recording(video, observations, window=active_window(tmp_path, end_id=999))
    assert result['active_window']['passed'] is False
    assert result['active_window']['reason'] == 'missing_predeclared_anchor'


def test_truncated_recording_is_not_reported_as_full_frame_success(tmp_path):
    from scripts.validate_recording import validate_recording
    video, observations = recording_fixture(tmp_path)
    encoded = video.read_bytes()
    cursor = encoded.index(b'movi') + 4
    for _ in range(6):
        assert encoded[cursor:cursor+4] == b'00dc'
        length = struct.unpack('<I', encoded[cursor+4:cursor+8])[0]
        cursor += 8 + length + length % 2
    video.write_bytes(encoded[:cursor])
    result = validate_recording(video, observations)
    assert result['decoded_frames'] == result['counts']['matched'] == 6
    assert result['container_frame_count'] == 12
    assert result['strict_full_frame_passed'] is False


def png_recording_fixture(tmp_path):
    _, observations = recording_fixture(tmp_path)
    pixels = (tmp_path / 'reference.png').read_bytes()
    frames = []
    for index in range(12):
        name = f'screen-{index}.png'; (tmp_path / name).write_bytes(pixels)
        frames.append({'file': name, 'sha256': hashlib.sha256(pixels).hexdigest(),
                       'timestamp_ms': 100_000 + index * 1000, 'width': 640, 'height': 180})
    manifest = tmp_path / 'screen-recording.json'
    manifest.write_text(json.dumps({'schema': 'chromium-cdp-png/v1', 'complete': True,
        'received_frames': 12, 'persisted_frames': 12, 'errors': [],
        'viewport': {'width': 640, 'height': 180}, 'frames': frames}))
    return manifest, observations


def test_lossless_screen_sequence_checks_every_frame_and_uses_real_timestamps(tmp_path):
    from scripts.validate_recording import validate_recording
    manifest, observations = png_recording_fixture(tmp_path)
    window = active_window(tmp_path)
    spec = json.loads(window.read_text()); spec['minimum_duration_s'] = 8
    window.write_text(json.dumps(spec))
    result = validate_recording(manifest, observations, window=window)
    assert result['recording_format'] == 'chromium-cdp-png/v1'
    assert result['decoded_frames'] == result['counts']['matched'] == 12
    assert result['active_window']['duration_s'] == 11
    assert result['active_window']['passed'] is True
    assert result['strict_full_frame_passed'] is True


@pytest.mark.parametrize('damage', ['missing', 'truncated', 'timestamp', 'incomplete'])
def test_lossless_sequence_does_not_hide_missing_or_damaged_evidence(tmp_path, damage):
    from scripts.validate_recording import validate_recording
    manifest, observations = png_recording_fixture(tmp_path)
    data = json.loads(manifest.read_text())
    if damage == 'missing': (tmp_path / 'screen-5.png').unlink()
    if damage == 'truncated': (tmp_path / 'screen-5.png').write_bytes(b'not a png')
    if damage == 'timestamp': data['frames'][5]['timestamp_ms'] = data['frames'][4]['timestamp_ms'] - 1
    if damage == 'incomplete': data['complete'] = False
    manifest.write_text(json.dumps(data))
    result = validate_recording(manifest, observations, window=active_window(tmp_path))
    assert result['recording_integrity_passed'] is False
    assert result['strict_full_frame_passed'] is False
    assert result['active_window']['passed'] is False
    assert result['recording_integrity_errors']


@pytest.mark.parametrize('label', ['capture', 'state'])
def test_chromium_cyan_single_digit_change_is_rejected_at_original_threshold(label):
    from scripts.validate_recording import compare_region
    fixtures = Path(__file__).resolve().parents[1] / 'fixtures/recording'
    # Independent Chromium canvas goldens: actual 14px capture and 15px state
    # labels, cyan #45d8e8, same documented font/coordinates as the UI. Only the
    # digit 0 -> 8 changes. No application renderer generated these fixtures.
    expected = cv2.imread(str(fixtures / f'{label}-0.png'))
    wrong_digit = cv2.imread(str(fixtures / f'{label}-8.png'))
    assert compare_region(expected, expected)['matched'] is True
    result = compare_region(wrong_digit, expected)
    assert result['matched'] is False
    assert result['max_block_mae'] > 38
