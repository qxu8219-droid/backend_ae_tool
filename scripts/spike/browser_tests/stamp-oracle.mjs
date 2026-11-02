/** Independent test oracle; does not import client stamp/catalog parsing code. */
export function decodeCaptureStamp(pixels) {
  let captureId = 0;
  let encodedCrc32 = 0;
  let confident = Array.isArray(pixels) && pixels.length === 64;
  if (Array.isArray(pixels)) pixels.forEach((rgba, bit) => {
    if (bit >= 64 || !Array.isArray(rgba) || rgba.length < 3 ||
        !Number.isFinite(rgba[0]) || !Number.isFinite(rgba[1]) || Math.abs(rgba[1] - rgba[0]) < 30) {
      confident = false;
      return;
    }
    if (rgba[1] > rgba[0]) {
      if (bit < 32) captureId += 2 ** bit;
      else encodedCrc32 += 2 ** (bit - 32);
    }
  });
  // Reflected IEEE CRC32, initial/final XOR 0xffffffff, over the ID's four big-endian bytes.
  let crc = 0xffffffff;
  for (const shift of [24, 16, 8, 0]) {
    crc ^= (captureId >>> shift) & 0xff;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  const expectedCrc32 = (crc ^ 0xffffffff) >>> 0;
  return { captureId, encodedCrc32, expectedCrc32, crcValid: confident && encodedCrc32 === expectedCrc32, confident };
}
