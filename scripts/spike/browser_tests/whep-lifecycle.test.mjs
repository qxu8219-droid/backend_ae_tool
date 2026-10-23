/**
 * Real WHEP lifecycle E2E. Start the local gateway, producer, and custom player first.
 * Run: npm run test:whep:lifecycle
 * Env: P0_WHEP_URL, P0_MEDIAMTX_API_URL (full sessions-list URL),
 *      P0_BROWSER_OUTPUT, CHROME_BIN, P0_BROWSER_TIMEOUT_MS.
 * Requires exclusive use of this gateway: baseline and disconnected session count
 * must be zero. Two disconnect/reconnect cycles plus final cleanup are verified.
 * Public p0Connect/p0Disconnect, native frame callbacks and MediaMTX HTTP are real.
 */
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const playerUrl = process.env.P0_WHEP_URL ?? 'http://127.0.0.1:8768/?autoconnect=1';
const sessionsUrl = process.env.P0_MEDIAMTX_API_URL ?? 'http://127.0.0.1:9997/v3/webrtcsessions/list';
const outputDir = path.resolve(process.env.P0_BROWSER_OUTPUT ?? path.join(here, '../../../outputs/p0/whep-browser-lifecycle'));
const executablePath = process.env.CHROME_BIN ?? '/usr/bin/google-chrome';
const readyTimeout = Number(process.env.P0_BROWSER_TIMEOUT_MS ?? 60_000);
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

function isLoopbackUrl(value) {
  const url = new URL(value);
  return ['http:', 'https:'].includes(url.protocol) && ['127.0.0.1', 'localhost'].includes(url.hostname);
}

test('real WHEP disconnects, releases resources and reconnects twice', { timeout: readyTimeout * 3 + 45_000 }, async () => {
  assert.ok(isLoopbackUrl(playerUrl), 'P0_WHEP_URL must use a loopback HTTP(S) origin');
  assert.ok(isLoopbackUrl(sessionsUrl), 'P0_MEDIAMTX_API_URL must use a loopback HTTP(S) origin');
  assert.ok(Number.isFinite(readyTimeout) && readyTimeout > 0);
  await mkdir(outputDir, { recursive: true });
  const profileDir = await mkdtemp(path.join(tmpdir(), 'p0-whep-lifecycle-'));
  const report = {
    schemaVersion: 1, timestamp: new Date().toISOString(),
    mode: 'real-headless-browser', mediaMocked: false, rtcStatsMocked: false,
    url: playerUrl, sessionsUrl, executablePath,
    expected: { reconnectCycles: 2, minimumRealFramesPerConnection: 10, disconnectedSessions: 0 },
    profile: { temporary: true, path: profileDir, removed: false },
    gpu: { disabledByLaunchFlags: true },
    requests: [], responses: [], pageErrors: [], console: [], apiSamples: [], cycles: [],
  };
  let context;
  let page;

  async function readSessions(stage) {
    const response = await fetch(sessionsUrl, { redirect: 'error', signal: AbortSignal.timeout(5000) });
    assert.ok(response.ok, `MediaMTX API must be available: HTTP ${response.status}`);
    const body = await response.json();
    assert.ok(Array.isArray(body.items), 'MediaMTX sessions list must contain items[]');
    const count = body.itemCount ?? body.items.length;
    assert.ok(Number.isInteger(count) && count >= body.items.length, 'MediaMTX session count must be valid');
    const sample = { stage, timestamp: new Date().toISOString(), count, ids: body.items.map(item => item.id) };
    report.apiSamples.push(sample);
    return sample;
  }

  async function waitForNoSessions(stage) {
    const deadline = Date.now() + 15_000;
    let sample;
    do {
      sample = await readSessions(stage);
      if (sample.count === 0) return sample;
      await pause(250);
    } while (Date.now() < deadline);
    assert.equal(sample.count, 0, `${stage}: MediaMTX sessions must be released within 15 seconds`);
  }

  async function waitForRealPlayback(stage, afterConnectionIndex = -1) {
    await page.waitForFunction(({ afterConnectionIndex }) => {
      const video = window.__p0LifecycleVideo;
      const observation = window.__p0LifecycleFrames;
      const connections = window.__p0LifecycleConnections;
      return observation.count >= 10 && video.readyState >= 2 && !video.paused &&
        video.videoWidth > 0 && video.videoHeight > 0 &&
        connections.some((connection, index) => index > afterConnectionIndex && connection.connectionState === 'connected');
    }, { afterConnectionIndex }, { timeout: readyTimeout });
    const playback = await page.evaluate(async () => {
      const video = window.__p0LifecycleVideo;
      const connections = [];
      for (const [index, connection] of window.__p0LifecycleConnections.entries()) {
        const inboundVideo = connection.connectionState === 'closed' ? [] :
          [...(await connection.getStats()).values()].filter(entry => entry.type === 'inbound-rtp' && (entry.kind === 'video' || entry.mediaType === 'video'));
        connections.push({ index, state: connection.connectionState, iceState: connection.iceConnectionState, inboundVideo });
      }
      return {
        video: { width: video.videoWidth, height: video.videoHeight, currentTime: video.currentTime, readyState: video.readyState },
        frameCallbacks: structuredClone(window.__p0LifecycleFrames), connections,
      };
    });
    const active = playback.connections.filter(connection => connection.state === 'connected');
    assert.equal(active.length, 1, `${stage}: only one native connection may remain active`);
    assert.ok(active[0].index > afterConnectionIndex, `${stage}: reconnect must create a new native connection`);
    assert.ok(active[0].inboundVideo.some(stats => stats.framesDecoded >= 10 && stats.bytesReceived > 0),
      `${stage}: native RTC receiver must actually decode at least ten frames`);
    const sessions = await readSessions(stage);
    assert.equal(sessions.count, 1, `${stage}: one active MediaMTX session is expected`);
    const creation = report.responses.filter(response => response.method === 'POST' && response.status === 201 && response.location).at(-1);
    assert.ok(creation, `${stage}: successful WHEP POST must expose its resource Location`);
    const resourceUrl = new URL(creation.location, creation.url).href;
    assert.ok(isLoopbackUrl(resourceUrl), 'WHEP resource must remain local');
    const screenshot = path.join(outputDir, `${stage}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    return { ...playback, connectionIndex: active[0].index, sessions, resourceUrl, screenshot };
  }

  async function disconnectAndVerify(stage, resourceUrl) {
    const requestOffset = report.requests.length;
    const responseOffset = report.responses.length;
    await page.evaluate(async () => { await window.p0Disconnect(); });
    const browser = await page.evaluate(() => ({
      connections: window.__p0LifecycleConnections.map((connection, index) => ({
        index, state: connection.connectionState, iceState: connection.iceConnectionState,
      })),
      videoDetached: window.__p0LifecycleVideo.srcObject === null,
    }));
    assert.ok(browser.connections.length > 0);
    assert.ok(browser.connections.every(connection => connection.state === 'closed'), `${stage}: every native PC must be closed`);
    assert.equal(browser.videoDetached, true, `${stage}: video must release srcObject`);
    const deletes = report.requests.slice(requestOffset).filter(request => request.method === 'DELETE');
    assert.ok(deletes.some(request => request.url === resourceUrl), `${stage}: disconnect must DELETE its WHEP resource`);
    const deleteResponses = report.responses.slice(responseOffset).filter(response => response.method === 'DELETE' && response.url === resourceUrl);
    assert.ok(deleteResponses.some(response => response.status >= 200 && response.status < 300), `${stage}: resource DELETE must succeed`);
    const sessions = await waitForNoSessions(stage);
    return { browser, deletes, deleteResponses, sessions };
  }

  try {
    report.baseline = await waitForNoSessions('baseline');
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
      const NativePeerConnection = window.RTCPeerConnection;
      const connections = [];
      Object.defineProperty(window, '__p0LifecycleConnections', { value: connections });
      window.RTCPeerConnection = new Proxy(NativePeerConnection, {
        construct(target, args, newTarget) {
          const connection = Reflect.construct(target, args, newTarget);
          connections.push(connection);
          return connection;
        },
      });
      if (window.webkitRTCPeerConnection === NativePeerConnection) window.webkitRTCPeerConnection = window.RTCPeerConnection;
    });
    page = context.pages()[0] ?? await context.newPage();
    page.setDefaultTimeout(readyTimeout);
    page.on('pageerror', error => report.pageErrors.push(error.message));
    page.on('console', message => {
      if (report.console.length < 100) report.console.push({ type: message.type(), text: message.text() });
    });
    page.on('response', response => report.responses.push({
      method: response.request().method(), status: response.status(), url: response.url(),
      location: response.headers().location ?? null,
    }));
    const response = await page.goto(playerUrl, { waitUntil: 'domcontentloaded', timeout: readyTimeout });
    assert.ok(response?.ok(), `player HTTP response must be successful (received ${response?.status()})`);
    await page.waitForFunction(() => typeof window.p0Connect === 'function' && typeof window.p0Disconnect === 'function');
    const video = page.locator('video').first();
    await video.waitFor({ state: 'visible' });
    await video.evaluate(element => {
      window.__p0LifecycleVideo = element;
      window.__p0LifecycleFrames = { count: 0, first: null, last: null };
      const observe = (now, metadata) => {
        const observation = window.__p0LifecycleFrames;
        const frame = {
          callbackTime: now, width: metadata.width, height: metadata.height,
          mediaTime: metadata.mediaTime, presentedFrames: metadata.presentedFrames,
          rtpTimestamp: metadata.rtpTimestamp ?? null,
        };
        observation.count += 1;
        observation.first ??= frame;
        observation.last = frame;
        element.requestVideoFrameCallback(observe);
      };
      element.requestVideoFrameCallback(observe);
    });
    // Custom URLs may omit autoconnect; use the same public entry point in that case.
    if (await page.evaluate(() => window.__p0LifecycleConnections.length === 0)) {
      await page.evaluate(async () => { await window.p0Connect(); });
    }
    report.initialPlayback = await waitForRealPlayback('initial-playback');
    let previousPlayback = report.initialPlayback;
    for (let round = 1; round <= 2; round += 1) {
      const cycle = { round };
      report.cycles.push(cycle);
      cycle.disconnect = await disconnectAndVerify(`round-${round}-disconnect`, previousPlayback.resourceUrl);
      await page.evaluate(async () => {
        window.__p0LifecycleFrames = { count: 0, first: null, last: null };
        await window.p0Connect();
      });
      cycle.reconnect = await waitForRealPlayback(`round-${round}-reconnect`, previousPlayback.connectionIndex);
      assert.notEqual(cycle.reconnect.resourceUrl, previousPlayback.resourceUrl, 'reconnect must create a new WHEP resource');
      previousPlayback = cycle.reconnect;
    }
    report.finalDisconnect = await disconnectAndVerify('final-disconnect', previousPlayback.resourceUrl);
    assert.ok(report.requests.every(request => request.allowed), 'page must request only loopback origins');
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed';
    report.error = { name: error.name, message: error.message };
    throw error;
  } finally {
    if (page && !page.isClosed()) {
      try {
        report.finalScreenshot = path.join(outputDir, 'lifecycle-final.png');
        await page.screenshot({ path: report.finalScreenshot, fullPage: true, timeout: 5000 });
        report.pageText = (await page.locator('body').innerText({ timeout: 5000 })).slice(0, 6000);
        await page.evaluate(async () => {
          if (typeof window.p0Disconnect === 'function') await window.p0Disconnect();
        });
      } catch (error) { report.cleanupError = error.message; }
    }
    await context?.close();
    await rm(profileDir, { recursive: true, force: true });
    report.profile.removed = true;
    await writeFile(path.join(outputDir, 'whep-lifecycle.json'), `${JSON.stringify(report, null, 2)}\n`);
    console.log(`P0 WHEP lifecycle report: ${path.join(outputDir, 'whep-lifecycle.json')}`);
    console.log(`P0 WHEP lifecycle status: ${report.status}`);
  }
});
