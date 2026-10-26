/** T14 diagnostic only: observe native WHEP video pixels, never claim paired UI. */
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from './spike/browser_tests/node_modules/playwright/index.mjs';
import { decodeCaptureStamp } from './spike/browser_tests/stamp-oracle.mjs';

const [url, runtimeFile, output] = process.argv.slice(2);
const runtime = JSON.parse(await readFile(runtimeFile, 'utf8'));
const allowed = new Set([new URL(url).origin, new URL(new URL(url).searchParams.get('whep')).origin]);
assert([...allowed].every(origin => new URL(origin).hostname === '127.0.0.1'));
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true,
  args: ['--disable-gpu', '--disable-accelerated-video-decode', '--disable-background-networking',
    '--no-proxy-server', '--autoplay-policy=no-user-gesture-required'] });
let page;
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
  await context.route('**/*', route => allowed.has(new URL(route.request().url()).origin) ? route.continue() : route.abort());
  page = await context.newPage();
  await page.goto(url);
  await page.waitForFunction(() => {
    const video = document.querySelector('#source');
    return video?.videoWidth === 1280 && video.videoHeight === 720 && video.getVideoPlaybackQuality().totalVideoFrames >= 30;
  }, undefined, { timeout: 45000 });
  const observations = [];
  for (let index = 0; index < 5; index++) {
    const sample = await page.evaluate(pixels => new Promise(resolve => {
      const video = document.querySelector('#source');
      video.requestVideoFrameCallback((now, metadata) => {
        const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        const context = canvas.getContext('2d', { willReadFrequently: true }); context.drawImage(video, 0, 0);
        resolve({ width: canvas.width, height: canvas.height, mediaTime: metadata.mediaTime, presentedFrames: metadata.presentedFrames,
          pixels: pixels.map(([x, y]) => [...context.getImageData(x, y, 1, 1).data]) });
      });
    }), runtime.marker_pixels);
    const identity = decodeCaptureStamp(sample.pixels);
    assert(identity.crcValid && identity.captureId > 0, JSON.stringify(identity));
    observations.push({ ...sample, pixels: undefined, ...identity });
  }
  assert(new Set(observations.map(row => row.captureId)).size >= 3);
  await page.screenshot({ path: output + '.png', fullPage: true });
  await page.locator('#disconnect').click();
  await writeFile(output + '.json', JSON.stringify({ passed: true, scope: 'Native WHEP video only; independent pixel CRC; no paired data panel claim', observations }, null, 2));
} finally {
  await browser.close();
}
