/**
 * Real browser WHEP E2E. This test does not start the gateway or video producer.
 * Prerequisites: a local player page and a live 1280x720 WHEP stream.
 * Run: npm run test:whep
 * Custom page: P0_WHEP_URL=http://127.0.0.1:5173/ npm run test:whep
 * Optional: CHROME_BIN, P0_BROWSER_OUTPUT, P0_VIDEO_SELECTOR,
 *           P0_EXPECT_WIDTH, P0_EXPECT_HEIGHT, P0_BROWSER_TIMEOUT_MS.
 * Pass requires actual decoded video frames and inbound RTC statistics to grow
 * during at least 3 seconds of playback. No media or getStats mocks are used.
 */
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const playerUrl = process.env.P0_WHEP_URL ?? 'http://127.0.0.1:8889/stream';
const videoSelector = process.env.P0_VIDEO_SELECTOR ?? 'video';
const expectedWidth = Number(process.env.P0_EXPECT_WIDTH ?? 1280);
const expectedHeight = Number(process.env.P0_EXPECT_HEIGHT ?? 720);
const readyTimeout = Number(process.env.P0_BROWSER_TIMEOUT_MS ?? 60_000);
const outputDir = path.resolve(process.env.P0_BROWSER_OUTPUT ?? path.join(here, '../../../outputs/p0/whep-browser'));
const executablePath = process.env.CHROME_BIN ?? '/usr/bin/google-chrome';

function isLoopbackUrl(value) {
  const url = new URL(value);
  return ['http:', 'https:'].includes(url.protocol) && ['127.0.0.1', 'localhost'].includes(url.hostname);
}

test('WHEP player decodes and presents 1280x720 live video for at least 3 seconds', { timeout: readyTimeout + 30_000 }, async () => {
  assert.ok(isLoopbackUrl(playerUrl), 'P0_WHEP_URL must use http(s)://127.0.0.1 or localhost');
  assert.ok(Number.isInteger(expectedWidth) && expectedWidth > 0, 'P0_EXPECT_WIDTH must be positive');
  assert.ok(Number.isInteger(expectedHeight) && expectedHeight > 0, 'P0_EXPECT_HEIGHT must be positive');
  assert.ok(Number.isFinite(readyTimeout) && readyTimeout > 0, 'P0_BROWSER_TIMEOUT_MS must be positive');
  await mkdir(outputDir, { recursive: true });
  const profileDir = await mkdtemp(path.join(tmpdir(), 'p0-whep-browser-'));
  const report = {
    schemaVersion: 1,
    timestamp: new Date().toISOString(),
    mode: 'real-headless-browser',
    mediaMocked: false,
    rtcStatsMocked: false,
    url: playerUrl,
    executablePath,
    expected: { width: expectedWidth, height: expectedHeight, minimumPlaybackMs: 3000 },
    profile: { temporary: true, path: profileDir, removed: false },
    gpu: { disabledByLaunchFlags: true },
    requests: [], responses: [], console: [], pageErrors: [], actions: [], samples: [],
  };
  let context;
  let page;
  try {
    report.launchArgs = [
      '--disable-gpu', '--disable-software-rasterizer', '--use-gl=disabled',
      '--disable-accelerated-video-decode', '--disable-accelerated-video-encode',
      '--disable-background-networking', '--autoplay-policy=no-user-gesture-required',
    ];
    context = await chromium.launchPersistentContext(profileDir, {
      executablePath, headless: true, viewport: { width: 1440, height: 960 },
      serviceWorkers: 'block', args: report.launchArgs,
    });
    report.browserVersion = context.browser().version();
    await context.route('**/*', async route => {
      const url = route.request().url();
      const allowed = isLoopbackUrl(url);
      report.requests.push({ method: route.request().method(), url, allowed });
      if (allowed) await route.continue();
      else await route.abort('blockedbyclient');
    });
    await context.addInitScript(() => {
      // Observe construction only. The returned connection and its getStats are native.
      const NativePeerConnection = window.RTCPeerConnection;
      const connections = [];
      Object.defineProperty(window, '__p0PeerConnections', { value: connections });
      window.RTCPeerConnection = new Proxy(NativePeerConnection, {
        construct(target, args, newTarget) {
          const connection = Reflect.construct(target, args, newTarget);
          connections.push(connection);
          return connection;
        },
      });
      if (window.webkitRTCPeerConnection === NativePeerConnection) {
        window.webkitRTCPeerConnection = window.RTCPeerConnection;
      }
    });
    page = context.pages()[0] ?? await context.newPage();
    page.setDefaultTimeout(readyTimeout);
    page.on('pageerror', error => report.pageErrors.push(error.message));
    page.on('console', message => {
      if (report.console.length < 100) report.console.push({ type: message.type(), text: message.text() });
    });
    page.on('response', response => {
      report.responses.push({ status: response.status(), url: response.url() });
    });
    const response = await page.goto(playerUrl, { waitUntil: 'domcontentloaded', timeout: readyTimeout });
    assert.ok(response?.ok(), `player HTTP response must be successful (received ${response?.status()})`);
    report.finalUrl = page.url();
    assert.ok(isLoopbackUrl(report.finalUrl), 'player must remain on a loopback origin');

    // Click a real page control if the player requires an explicit connection.
    const startButtons = page.getByRole('button', { name: /^(?:play|start|connect|播放|开始|连接)(?:\b|\s|$)/i });
    for (let index = 0; index < Math.min(await startButtons.count(), 3); index += 1) {
      const button = startButtons.nth(index);
      if (await button.isVisible() && await button.isEnabled()) {
        report.actions.push({ action: 'click', label: await button.innerText() });
        await button.click();
        break;
      }
    }
    const video = page.locator(videoSelector).first();
    await video.waitFor({ state: 'visible' });
    await video.evaluate(element => {
      element.muted = true;
      element.playsInline = true;
      element.autoplay = true;
      const observed = { count: 0, first: null, last: null };
      window.__p0Video = element;
      window.__p0VideoFrames = observed;
      const observe = (now, metadata) => {
        const frame = {
          callbackTime: now, mediaTime: metadata.mediaTime,
          presentedFrames: metadata.presentedFrames, width: metadata.width, height: metadata.height,
          rtpTimestamp: metadata.rtpTimestamp ?? null,
          receiveTime: metadata.receiveTime ?? null,
          captureTime: metadata.captureTime ?? null,
        };
        observed.count += 1;
        observed.first ??= frame;
        observed.last = frame;
        element.requestVideoFrameCallback(observe);
      };
      element.requestVideoFrameCallback(observe);
      // A real media-element operation; any browser refusal is retained as evidence.
      element.play().catch(error => { observed.playError = `${error.name}: ${error.message}`; });
    });
    report.actions.push({ action: 'video.play', muted: true, selector: videoSelector });
    await page.waitForFunction(({ width, height }) => {
      const video = window.__p0Video;
      return video && !video.paused && video.readyState >= 2 &&
        video.videoWidth === width && video.videoHeight === height && window.__p0VideoFrames.count >= 2;
    }, { width: expectedWidth, height: expectedHeight }, { timeout: readyTimeout });
    await page.waitForFunction(async () => {
      for (const connection of window.__p0PeerConnections) {
        const stats = await connection.getStats();
        for (const entry of stats.values()) {
          if (entry.type === 'inbound-rtp' && (entry.kind === 'video' || entry.mediaType === 'video') && entry.framesDecoded > 0) return true;
        }
      }
      return false;
    }, undefined, { timeout: readyTimeout });

    async function sample() {
      return page.evaluate(async () => {
        const video = window.__p0Video;
        const quality = video.getVideoPlaybackQuality?.();
        const peerConnections = [];
        for (const [index, connection] of window.__p0PeerConnections.entries()) {
          const stats = [...(await connection.getStats()).values()];
          peerConnections.push({
            index, connectionState: connection.connectionState, iceConnectionState: connection.iceConnectionState,
            inboundVideo: stats.filter(entry => entry.type === 'inbound-rtp' && (entry.kind === 'video' || entry.mediaType === 'video')),
            codecs: stats.filter(entry => entry.type === 'codec'),
            candidatePairs: stats.filter(entry => entry.type === 'candidate-pair' && (entry.nominated || entry.selected)),
            transport: stats.filter(entry => entry.type === 'transport'),
          });
        }
        return {
          timestamp: new Date().toISOString(), elapsedMs: performance.now(),
          video: {
            width: video.videoWidth, height: video.videoHeight, currentTime: video.currentTime,
            paused: video.paused, ended: video.ended, readyState: video.readyState,
            totalVideoFrames: quality?.totalVideoFrames ?? null,
            droppedVideoFrames: quality?.droppedVideoFrames ?? null,
            decodedFrames: video.webkitDecodedFrameCount ?? quality?.totalVideoFrames ?? null,
            error: video.error ? { code: video.error.code, message: video.error.message } : null,
          },
          frameCallbacks: structuredClone(window.__p0VideoFrames), peerConnections,
        };
      });
    }
    report.samples.push(await sample());
    // A duration measurement is part of the contract, not a substitute for readiness.
    for (let index = 0; index < 3; index += 1) {
      await new Promise(resolve => setTimeout(resolve, 1050));
      report.samples.push(await sample());
    }
    const first = report.samples[0];
    const last = report.samples.at(-1);
    const inbound = sample => sample.peerConnections.flatMap(connection => connection.inboundVideo.map(stats => ({
      ...stats, connectionIndex: connection.index,
    })));
    const initialStreams = inbound(first);
    const finalStreams = inbound(last);
    report.playback = {
      elapsedMs: last.elapsedMs - first.elapsedMs,
      mediaTimeAdvanceSeconds: last.video.currentTime - first.video.currentTime,
      decodedFramesDelta: last.video.decodedFrames - first.video.decodedFrames,
      frameCallbacksDelta: last.frameCallbacks.count - first.frameCallbacks.count,
      inboundStreams: finalStreams.map(end => {
        const start = initialStreams.find(entry => entry.connectionIndex === end.connectionIndex && entry.id === end.id);
        return {
          id: end.id, connectionIndex: end.connectionIndex, ssrc: end.ssrc, codecId: end.codecId,
          continuous: Boolean(start),
          framesDecodedDelta: start ? end.framesDecoded - start.framesDecoded : null,
          bytesReceivedDelta: start ? end.bytesReceived - start.bytesReceived : null,
          frameWidth: end.frameWidth, frameHeight: end.frameHeight,
        };
      }),
    };
    assert.ok(report.playback.elapsedMs >= 3000, 'measure at least three seconds of playback');
    assert.ok(report.playback.mediaTimeAdvanceSeconds >= 2.5, 'media time must advance during playback');
    assert.ok(report.playback.decodedFramesDelta > 0, 'real video decoded frame count must grow');
    assert.ok(report.playback.frameCallbacksDelta > 0, 'actual video frame callbacks must continue');
    for (const { video: sampleVideo } of report.samples) {
      assert.equal(sampleVideo.width, expectedWidth);
      assert.equal(sampleVideo.height, expectedHeight);
      assert.equal(sampleVideo.paused, false);
      assert.equal(sampleVideo.ended, false);
      assert.equal(sampleVideo.error, null);
    }
    assert.ok(report.playback.inboundStreams.some(stream => stream.continuous && stream.framesDecodedDelta > 0 && stream.bytesReceivedDelta > 0),
      'the same native inbound RTC video stream must receive bytes and decode frames throughout playback');
    assert.ok(report.requests.every(request => request.allowed), 'page must request only loopback origins');
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed';
    report.error = { name: error.name, message: error.message };
    throw error;
  } finally {
    if (page && !page.isClosed()) {
      try {
        report.screenshot = path.join(outputDir, 'whep-playback.png');
        await page.screenshot({ path: report.screenshot, fullPage: true, timeout: 5000 });
        report.pageText = (await page.locator('body').innerText({ timeout: 5000 })).slice(0, 6000);
      } catch (error) { report.captureError = error.message; }
    }
    await context?.close();
    await rm(profileDir, { recursive: true, force: true });
    report.profile.removed = true;
    await writeFile(path.join(outputDir, 'whep-playback.json'), `${JSON.stringify(report, null, 2)}\n`);
    console.log(`P0 WHEP browser report: ${path.join(outputDir, 'whep-playback.json')}`);
    console.log(`P0 WHEP browser status: ${report.status}`);
  }
});
