import assert from 'node:assert/strict';
import test from 'node:test';
import { decodeCaptureStamp } from './stamp-oracle.mjs';

// CRC literals were independently checked with Python zlib.crc32(id.to_bytes(4, 'big')).
function colors(id, crc) {
  return Array.from({ length: 64 }, (_, bit) => {
    const value = bit < 32 ? id : crc;
    return ((value >>> (bit % 32)) & 1) ? [15, 220, 10, 255] : [220, 15, 10, 255];
  });
}

test('64-bit stamp decodes unsigned IDs and CRC32 over four big-endian bytes', () => {
  for (const [id, crc] of [[1, 0x5643ef8a], [0x12345678, 0x4a090e98], [0x80000000, 0xcc1d6927], [0xffffffff, 0xffffffff]]) {
    const result = decodeCaptureStamp(colors(id, crc));
    assert.equal(result.captureId, id);
    assert.equal(result.encodedCrc32, crc);
    assert.equal(result.expectedCrc32, crc);
    assert.equal(result.crcValid, true);
    assert.equal(result.confident, true);
  }
});

test('changing one ID bit without changing the CRC is rejected', () => {
  const pixels = colors(1, 0x5643ef8a);
  pixels[1] = [15, 220, 10, 255];
  assert.equal(decodeCaptureStamp(pixels).crcValid, false);
});

test('missing or unreadable pixels cannot produce a valid stamp', () => {
  assert.equal(decodeCaptureStamp(colors(1, 0x5643ef8a).slice(0, 63)).confident, false);
  const pixels = colors(1, 0x5643ef8a);
  pixels[0] = [120, 120, 120, 255];
  assert.equal(decodeCaptureStamp(pixels).confident, false);
});
