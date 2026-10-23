/**
 * Real SEI + decoded-canvas identity/control E2E. No media or metadata mocks.
 * Run: npm run test:sync
 * Optional delayed scenarios: P0_SYNC_DELAY_MODES=metadata,video npm run test:sync
 * Optional base-scenario resilience: P0_SYNC_RESILIENCE=1 npm run test:sync
 * Optional real-media soak: P0_SYNC_SOAK_MS=60000 npm run test:sync
 * Env: P0_WHEP_URL, P0_BROWSER_OUTPUT, CHROME_BIN, P0_BROWSER_TIMEOUT_MS,
 *      P0_SYNC_CONTROL_TIMEOUT_MS, P0_SYNC_DELAY_MODES, P0_SYNC_RECORD_VIDEO=1,
 *      P0_MEDIAMTX_API_URL (sessions-list URL; resilience requires exclusive gateway).
 * Identity/control are assertions. FPS/latency are measurements, not acceptance gates.
 * The stream producer, gateway and page must already be running.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { chromium } from 'playwright';
import { decodeCaptureStamp } from './stamp-oracle.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const playerUrl = process.env.P0_WHEP_URL ?? 'http://127.0.0.1:8768/?autoconnect=1&sync=1';
const outputDir = path.resolve(process.env.P0_BROWSER_OUTPUT ?? path.join(here, '../../../outputs/p0/sync-browser'));
const executablePath = process.env.CHROME_BIN ?? '/usr/bin/google-chrome';
const readyTimeout = Number(process.env.P0_BROWSER_TIMEOUT_MS ?? 60_000);
const controlTimeout = Number(process.env.P0_SYNC_CONTROL_TIMEOUT_MS ?? 20_000);
const resilienceEnabled = process.env.P0_SYNC_RESILIENCE === '1';
const soakMs = Number(process.env.P0_SYNC_SOAK_MS ?? 0);
const sessionsUrl = process.env.P0_MEDIAMTX_API_URL ?? 'http://127.0.0.1:9997/v3/webrtcsessions/list';
const delayModes = (process.env.P0_SYNC_DELAY_MODES ?? '').split(',').map(value => value.trim()).filter(Boolean);
const scenarios = ['base', ...new Set(delayModes)];
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const near = (actual, expected, label, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) <= tolerance,
  `${label}: expected ${expected} ± ${tolerance}, received ${actual}`);

function isLoopbackUrl(value) {
  const url = new URL(value);
  return ['http:', 'https:'].includes(url.protocol) && ['127.0.0.1', 'localhost'].includes(url.hostname);
}

function performanceSummary(stats) {
  const presentations = stats?.presentations ?? [];
  const latencies = (stats?.latencies_ms ?? []).filter(Number.isFinite).sort((a, b) => a - b);
  const quantile = fraction => latencies.length ? latencies[Math.min(latencies.length - 1, Math.floor((latencies.length - 1) * fraction))] : null;
  const durationMs = presentations.length > 1 ? presentations.at(-1).wall_ms - presentations[0].wall_ms : 0;
  return {
    status: 'measured-only', thresholdsApplied: false,
    scope: 'Same-frame canvas presentations retained by the client; includes paused/control intervals.',
    presentationCount: presentations.length, durationMs,
    presentationFps: durationMs > 0 ? (presentations.length - 1) * 1000 / durationMs : null,
    latencyMs: { count: latencies.length, minimum: latencies[0] ?? null, median: quantile(0.5), p95: quantile(0.95), maximum: latencies.at(-1) ?? null },
  };
}

for (const scenario of scenarios) {
  const runResilience = scenario === 'base' && resilienceEnabled;
  const runSoakMs = scenario === 'base' && Number.isFinite(soakMs) ? soakMs : 0;
  const optionalTimeout = (runResilience ? readyTimeout * 2 + 60_000 : 0) + Math.max(0, runSoakMs);
  test(`real same-frame canvas identity and presented controls (${scenario})`, { timeout: readyTimeout + controlTimeout * 25 + 30_000 + optionalTimeout }, async () => {
    assert.ok(['base', 'metadata', 'video'].includes(scenario), 'P0_SYNC_DELAY_MODES supports metadata,video only');
    assert.ok(isLoopbackUrl(playerUrl), 'P0_WHEP_URL must use a loopback HTTP(S) origin');
    assert.ok(Number.isFinite(readyTimeout) && readyTimeout > 0 && Number.isFinite(controlTimeout) && controlTimeout > 0);
    assert.ok(Number.isFinite(soakMs) && soakMs >= 0 && soakMs <= 600_000, 'P0_SYNC_SOAK_MS must be between 0 and 600000');
    if (runResilience) assert.ok(isLoopbackUrl(sessionsUrl), 'P0_MEDIAMTX_API_URL must use a loopback HTTP(S) origin');
    const url = new URL(playerUrl);
    url.searchParams.set('sync', '1');
    url.searchParams.set('autoconnect', '1');
    if (scenario !== 'base') {
      url.searchParams.delete('metadata_delay_ms');
      url.searchParams.delete('video_delay_ms');
      url.searchParams.set(`${scenario}_delay_ms`, '700');
    }
    const scenarioDir = path.join(outputDir, scenario);
    await mkdir(scenarioDir, { recursive: true });
    const profileDir = await mkdtemp(path.join(tmpdir(), 'p0-sync-browser-'));
    const report = {
      schemaVersion: 1, timestamp: new Date().toISOString(), scenario, url: url.href,
      mode: 'real-headless-browser', mediaMocked: false, metadataMocked: false,
      executablePath, profile: { temporary: true, path: profileDir, removed: false },
      expected: { initialSameFramePresentations: 30, markerMismatches: 0, canvasVideoWidth: 1240,
        markerLayout: 'capture32_crc32', markerBits: 64 },
      identity: { status: 'not-verified', independentSamples: [] },
      controls: [], performance: { status: 'not-measured', thresholdsApplied: false },
      resilience: { requested: runResilience, status: runResilience ? 'not-verified' : 'not-requested',
        scope: 'Explicit real WHEP disconnect/reconnect only; does not simulate UDP loss or one-sided receive suspension.',
        expectedCycles: 2, minimumNewPresentationsPerReconnect: 30, cycles: [], apiSamples: [] },
      soak: { requestedDurationMs: runSoakMs, status: runSoakMs > 0 ? 'not-verified' : 'not-requested',
        sampleIntervalMs: 100, scope: 'Independent CRC for each sampled canvas presentation, not every source/decoded frame; FPS uses the presentation counter.' },
      requests: [], responses: [], pageErrors: [], console: [],
    };
    let context;
    let page;

    async function sample(label) {
      // No await inside this browser callback: metadata and canvas pixels are sampled atomically.
      const value = await page.evaluate(() => {
        const stats = structuredClone(window.p0Stats ?? {});
        const metadata = stats.last_metadata ?? null;
        const canvas = document.querySelector('#syncCanvas');
        const video = document.querySelector('video');
        const nativeWidth = video?.videoWidth ?? 0;
        const pixelSamples = [];
        let confident = Boolean(metadata?.marker_layout === 'capture32_crc32' && nativeWidth > 0 &&
          metadata.marker_pixels?.length === 64 && canvas);
        if (confident) {
          const draw = canvas.getContext('2d');
          const scale = 1240 / nativeWidth;
          metadata.marker_pixels.forEach(([sourceX, sourceY], bit) => {
            const x = Math.round(sourceX * scale);
            const y = Math.round(sourceY * scale);
            if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) { confident = false; return; }
            const rgba = [...draw.getImageData(x, y, 1, 1).data];
            const readable = Math.abs(rgba[1] - rgba[0]) >= 30;
            if (!readable) confident = false;
            pixelSamples.push({ bit, sourceX, sourceY, canvasX: x, canvasY: y, rgba, readable });
          });
        }
        return {
          observedWallMs: Date.now(), nativeWidth,
          canvas: canvas ? { width: canvas.width, height: canvas.height } : null,
          metadata, confident, pixelSamples,
          stats: {
            mode: stats.mode, metadata_frames: stats.metadata_frames, sync_presented_frames: stats.sync_presented_frames,
            marker_mismatches: stats.marker_mismatches, last_marker_failure: stats.last_marker_failure,
            decoder_configured: stats.decoder_configured, encoded_stats: stats.encoded_stats,
            last_control: stats.last_control, barrier_request_id: stats.barrier_request_id,
            errors: stats.errors, last_presentation_wall_ms: stats.last_presentation_wall_ms,
          },
        };
      });
      // Only the pixels captured in that atomic browser callback enter this separate oracle.
      value.stamp = decodeCaptureStamp(value.pixelSamples.map(pixel => pixel.rgba));
      value.independentMarker = value.stamp.captureId;
      value.label = label;
      report.identity.independentSamples.push(value);
      return value;
    }

    function assertIdentity(value) {
      assert.equal(value.stats.marker_mismatches, 0, `client rejected mismatching frame: ${JSON.stringify(value.stats.last_marker_failure)}`);
      assert.ok(value.stats.metadata_frames > 0, 'SEI metadata must actually arrive');
      assert.equal(value.metadata?.sei_schema, 'p0.snapshot.v1', 'presented metadata must carry the SEI schema');
      assert.ok(value.stats.decoder_configured?.codec?.startsWith('avc1.'), 'H264 VideoDecoder must be configured from the encoded stream');
      assert.equal(value.metadata.marker_layout, 'capture32_crc32');
      assert.equal(value.confident, true, 'all 64 independently sampled canvas bits must be readable');
      assert.equal(value.pixelSamples.length, 64);
      assert.equal(value.stamp.confident, true);
      assert.equal(value.stamp.crcValid, true,
        `canvas CRC32 must validate the four big-endian capture-ID bytes: ${JSON.stringify(value.stamp)}`);
      assert.equal(value.independentMarker, value.metadata.capture_id, 'actual final canvas capture ID must exactly match the selected SEI snapshot');
      assert.equal(value.metadata.render_seq, value.metadata.capture_id, 'render sequence and capture identity must agree');
    }

    async function waitNewPresentation(previous, label, minimumWallSeconds = 0, minimumCaptureAdvance = 1) {
      await page.waitForFunction(({ capture, wall, minimumWallSeconds, minimumCaptureAdvance }) => {
        const stats = window.p0Stats;
        return stats?.marker_mismatches > 0 || (stats?.last_metadata?.capture_id >= capture + minimumCaptureAdvance &&
          stats.last_metadata.captured_wall_s - wall >= minimumWallSeconds);
      }, { capture: previous.metadata.capture_id, wall: previous.metadata.captured_wall_s, minimumWallSeconds, minimumCaptureAdvance }, { timeout: controlTimeout });
      const value = await sample(label);
      assertIdentity(value);
      return value;
    }

    async function control(command, check) {
      const before = await sample(`before-${command.action}`);
      assertIdentity(before);
      const started = Date.now();
      const accepted = await page.evaluate(async command => {
        await window.p0Control(command);
        return structuredClone(window.p0Stats.last_control?.control ?? null);
      }, command);
      const record = { command, startedWallMs: started, accepted, status: 'accepted-only' };
      report.controls.push(record);
      assert.ok(Number.isSafeInteger(accepted?.request_id) && accepted.request_id > (before.stats.last_control?.control?.request_id ?? 0),
        'control must receive a new safe integer request_id; HTTP acceptance alone is insufficient');
      assert.equal(accepted.action, command.action);
      await page.waitForFunction(({ requestId, capture }) => {
        const stats = window.p0Stats;
        return stats?.marker_mismatches > 0 || (stats?.last_metadata?.request_id >= requestId && stats.last_metadata.capture_id > capture);
      }, { requestId: accepted.request_id, capture: before.metadata.capture_id }, { timeout: controlTimeout });
      const shown = await sample(`presented-${command.action}-${accepted.request_id}`);
      record.presented = shown;
      record.requestToPresentedMs = Date.now() - started;
      assertIdentity(shown);
      assert.ok(shown.metadata.request_id >= accepted.request_id, 'matching control generation must be presented');
      assert.ok(shown.metadata.capture_id > before.metadata.capture_id, 'the acknowledgement must be on a new displayed frame');
      assert.ok(shown.metadata.epoch >= before.metadata.epoch, 'presented epoch must not regress');
      if (check) check(shown.metadata, before.metadata);
      record.status = 'presented-and-verified';
      return shown;
    }

    async function pauseAndVerify() {
      const stopped = await control({ action: 'pause' }, metadata => assert.equal(metadata.state, 'paused'));
      const later = await waitNewPresentation(stopped, 'pause-remains-stable', 0.25, 2);
      near(later.metadata.log_time_s, stopped.metadata.log_time_s, 'paused log position');
      assert.equal(later.metadata.state, 'paused');
      return later;
    }

    async function seek(position) {
      return control({ action: 'seek', time_s: position }, (metadata, before) => {
        near(metadata.log_time_s, position, 'seek presented position');
        assert.equal(metadata.state, 'paused');
        assert.ok(metadata.epoch > before.epoch, 'seek must advance epoch');
      });
    }

    async function speedPlayback(speed) {
      await control({ action: 'speed', speed }, (metadata, before) => {
        near(metadata.speed, speed, 'presented speed');
        near(metadata.log_time_s, before.log_time_s, 'speed change while paused');
        assert.equal(metadata.state, 'paused');
      });
      const playing = await control({ action: 'play' }, metadata => {
        assert.ok(['playing', 'ended'].includes(metadata.state));
        near(metadata.speed, speed, 'play speed');
      });
      const later = await waitNewPresentation(playing, `progress-${speed}x`, 0.45, 2);
      const sourceSeconds = later.metadata.captured_wall_s - playing.metadata.captured_wall_s;
      const expectedPosition = Math.min(12, playing.metadata.log_time_s + sourceSeconds * speed);
      near(later.metadata.log_time_s, expectedPosition, `${speed}x displayed progression`, Math.max(0.03, sourceSeconds * speed * 0.2));
      assert.ok(later.metadata.log_time_s > playing.metadata.log_time_s || playing.metadata.log_time_s === 12,
        'playback position must progress, with the documented end clamp at 12 seconds');
      report.controls.push({ command: { action: 'measure-progression', speed }, status: 'presented-and-verified',
        sourceSeconds, logSeconds: later.metadata.log_time_s - playing.metadata.log_time_s, expectedPosition });
      await pauseAndVerify();
    }

    async function readSessions(stage) {
      const response = await fetch(sessionsUrl, { redirect: 'error', signal: AbortSignal.timeout(5000) });
      assert.ok(response.ok, `MediaMTX API must be available: HTTP ${response.status}`);
      const body = await response.json();
      assert.ok(Array.isArray(body.items), 'MediaMTX sessions list must contain items[]');
      const count = body.itemCount ?? body.items.length;
      assert.ok(Number.isInteger(count) && count >= body.items.length, 'MediaMTX session count must be valid');
      const value = { stage, observedWallMs: Date.now(), count, ids: body.items.map(item => item.id) };
      report.resilience.apiSamples.push(value);
      return value;
    }

    async function waitForNoSessions(stage) {
      const deadline = Date.now() + 15_000;
      let value;
      do {
        value = await readSessions(stage);
        if (value.count === 0) return value;
        await pause(250);
      } while (Date.now() < deadline);
      assert.equal(value.count, 0, `${stage}: MediaMTX sessions must be released within 15 seconds`);
    }

    async function connectionSnapshot(stage, previousIndex = -1) {
      const connections = await page.evaluate(() => window.__p0SyncConnections.map((connection, index) => ({
        index, state: connection.connectionState, iceState: connection.iceConnectionState,
      })));
      const active = connections.filter(connection => connection.state === 'connected');
      assert.equal(active.length, 1, `${stage}: exactly one native PC must be connected`);
      assert.ok(active[0].index > previousIndex, `${stage}: reconnect must create a new native PC`);
      assert.ok(connections.every(connection => connection.index === active[0].index || connection.state === 'closed'),
        `${stage}: all superseded native PCs must be closed`);
      const creation = report.responses.filter(response => response.method === 'POST' && response.status === 201 && response.location).at(-1);
      assert.ok(creation, `${stage}: WHEP POST must return a resource Location`);
      const resourceUrl = new URL(creation.location, creation.url).href;
      assert.ok(isLoopbackUrl(resourceUrl), 'WHEP resource must remain local');
      const sessions = await readSessions(stage);
      assert.equal(sessions.count, 1, `${stage}: exactly one MediaMTX session is expected`);
      return { connections, connectionIndex: active[0].index, resourceUrl, sessions };
    }

    async function frozenCanvasSnapshot() {
      // Both the complete canvas PNG and metadata are captured in one synchronous callback.
      const value = await page.evaluate(() => ({
        png: document.querySelector('#syncCanvas').toDataURL('image/png'),
        metadata: structuredClone(window.p0Stats.last_metadata),
        presentations: window.p0Stats.sync_presented_frames,
        markerMismatches: window.p0Stats.marker_mismatches,
      }));
      const { png, ...snapshot } = value;
      return { ...snapshot, canvasSha256: createHash('sha256').update(png).digest('hex') };
    }

    async function resilience() {
      let connected = await connectionSnapshot('resilience-initial');
      for (let round = 1; round <= 2; round += 1) {
        const cycle = { round, status: 'not-verified', previous: connected };
        report.resilience.cycles.push(cycle);
        const requestOffset = report.requests.length;
        const responseOffset = report.responses.length;
        await page.evaluate(async () => { await window.p0Disconnect(); });
        cycle.freeze = { baseline: await frozenCanvasSnapshot(), observations: [] };
        cycle.disconnectedBrowser = await page.evaluate(() => ({
          connections: window.__p0SyncConnections.map((connection, index) => ({ index, state: connection.connectionState })),
          videoDetached: document.querySelector('video').srcObject === null,
        }));
        assert.ok(cycle.disconnectedBrowser.connections.length > 0);
        assert.ok(cycle.disconnectedBrowser.connections.every(connection => connection.state === 'closed'),
          `round ${round}: disconnect must close all native PCs`);
        assert.equal(cycle.disconnectedBrowser.videoDetached, true, 'disconnect must release video srcObject');
        cycle.deletes = report.requests.slice(requestOffset).filter(request => request.method === 'DELETE');
        assert.ok(cycle.deletes.some(request => request.url === connected.resourceUrl), 'disconnect must DELETE the active WHEP resource');
        cycle.deleteResponses = report.responses.slice(responseOffset).filter(response => response.method === 'DELETE' && response.url === connected.resourceUrl);
        assert.ok(cycle.deleteResponses.some(response => response.status >= 200 && response.status < 300), 'WHEP DELETE must succeed');
        cycle.disconnectedSessions = await waitForNoSessions(`round-${round}-disconnected`);
        for (let observation = 1; observation <= 4; observation += 1) {
          await pause(300);
          const frozen = await frozenCanvasSnapshot();
          cycle.freeze.observations.push(frozen);
          assert.deepEqual(frozen, cycle.freeze.baseline, 'complete canvas pixels, metadata and presentation count must freeze while disconnected');
        }
        await page.evaluate(async () => { await window.p0Connect(); });
        await page.waitForFunction(({ count, capture }) => {
          const stats = window.p0Stats;
          return stats.marker_mismatches > 0 || (stats.sync_presented_frames >= count + 30 && stats.last_metadata?.capture_id > capture);
        }, { count: cycle.freeze.baseline.presentations, capture: cycle.freeze.baseline.metadata.capture_id }, { timeout: readyTimeout });
        let shown = await sample(`reconnected-${round}-30-presentations`);
        assertIdentity(shown);
        assert.ok(shown.stats.sync_presented_frames >= cycle.freeze.baseline.presentations + 30,
          'reconnect must produce at least thirty new synchronized presentations');
        assert.equal(shown.metadata.session_id, cycle.freeze.baseline.metadata.session_id, 'reconnect must still consume the same running producer');
        assert.ok(shown.metadata.capture_id > cycle.freeze.baseline.metadata.capture_id, 'reconnect must resume with newer capture IDs');
        for (let index = 1; index <= 5; index += 1) shown = await waitNewPresentation(shown, `reconnected-${round}-independent-${index}`);
        cycle.reconnected = await connectionSnapshot(`round-${round}-reconnected`, connected.connectionIndex);
        assert.notEqual(cycle.reconnected.resourceUrl, connected.resourceUrl, 'reconnect must create a new WHEP resource');
        cycle.finalSample = shown;
        cycle.status = 'passed';
        connected = cycle.reconnected;
      }
      report.resilience.status = 'passed';
    }

    async function soak() {
      // At 0.1x the requested sixty-second run still shows motion in the twelve-second source.
      await control({ action: 'speed', speed: 0.1 }, metadata => near(metadata.speed, 0.1, 'soak speed'));
      await seek(0);
      await control({ action: 'play' }, metadata => assert.equal(metadata.state, 'playing'));
      const first = await sample('soak-start');
      assertIdentity(first);
      let previous = first;
      let uniqueSamples = 1;
      let sampleCount = 1;
      let maximumSampleIntervalMs = 0;
      const started = performance.now();
      while (performance.now() - started < runSoakMs) {
        await pause(Math.min(report.soak.sampleIntervalMs, Math.max(1, runSoakMs - (performance.now() - started))));
        const value = await sample(`soak-${sampleCount}`);
        assertIdentity(value);
        assert.equal(value.metadata.session_id, first.metadata.session_id, 'producer session must remain unchanged during soak');
        assert.ok(value.metadata.capture_id >= previous.metadata.capture_id, 'soak capture ID must never regress');
        assert.ok(value.metadata.epoch >= previous.metadata.epoch, 'soak epoch must never regress');
        assert.ok(value.stats.sync_presented_frames >= previous.stats.sync_presented_frames, 'presentation counter must never regress');
        if (value.stats.sync_presented_frames > previous.stats.sync_presented_frames) {
          assert.ok(value.metadata.capture_id > previous.metadata.capture_id, 'every observed new presentation must have a newer capture ID');
          uniqueSamples += 1;
        } else {
          assert.equal(value.metadata.capture_id, previous.metadata.capture_id, 'capture ID may change only when a new frame is presented');
        }
        maximumSampleIntervalMs = Math.max(maximumSampleIntervalMs, value.observedWallMs - previous.observedWallMs);
        sampleCount += 1;
        previous = value;
      }
      const elapsedMs = performance.now() - started;
      const newPresentations = previous.stats.sync_presented_frames - first.stats.sync_presented_frames;
      assert.ok(newPresentations > 0, 'real synchronized media must advance during the soak');
      report.soak = { ...report.soak, status: 'passed', elapsedMs, sampleCount, uniqueSamples,
        firstCaptureId: first.metadata.capture_id, lastCaptureId: previous.metadata.capture_id,
        newPresentations, maximumSampleIntervalMs, markerMismatches: previous.stats.marker_mismatches,
        steadyPresentationFps: newPresentations * 1000 / elapsedMs, performanceThresholdsApplied: false };
      await pauseAndVerify();
    }

    try {
      if (runResilience) report.resilience.baselineSessions = await waitForNoSessions('baseline-before-browser');
      report.launchArgs = [
        '--disable-gpu', '--disable-software-rasterizer', '--use-gl=disabled',
        '--disable-accelerated-video-decode', '--disable-accelerated-video-encode',
        '--disable-background-networking', '--autoplay-policy=no-user-gesture-required',
      ];
      context = await chromium.launchPersistentContext(profileDir, {
        executablePath, headless: true, viewport: { width: 1740, height: 1100 },
        serviceWorkers: 'block', args: report.launchArgs,
        ...(process.env.P0_SYNC_RECORD_VIDEO === '1' ? { recordVideo: { dir: scenarioDir, size: { width: 1740, height: 1100 } } } : {}),
      });
      report.browserVersion = context.browser().version();
      await context.route('**/*', async route => {
        const requestedUrl = route.request().url();
        const allowed = isLoopbackUrl(requestedUrl);
        report.requests.push({ method: route.request().method(), url: requestedUrl, allowed });
        if (allowed) await route.continue();
        else await route.abort('blockedbyclient');
      });
      if (runResilience) await context.addInitScript(() => {
        // Observe actual native connections without replacing their networking or state.
        const NativePeerConnection = window.RTCPeerConnection;
        const connections = [];
        Object.defineProperty(window, '__p0SyncConnections', { value: connections });
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
        method: response.request().method(), status: response.status(), url: response.url(), location: response.headers().location ?? null,
      }));
      const response = await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: readyTimeout });
      assert.ok(response?.ok(), `player must load successfully: HTTP ${response?.status()}`);
      await page.waitForFunction(() => typeof window.p0Control === 'function' && window.p0Stats);
      await page.waitForFunction(() => window.p0Stats.sync_presented_frames >= 30 || window.p0Stats.marker_mismatches > 0,
        undefined, { timeout: readyTimeout });
      let first = await sample('initial-30-presentations');
      assertIdentity(first);
      assert.ok(first.stats.sync_presented_frames >= 30, 'fewer than 30 synchronized presentations is a failure');
      for (let index = 0; index < 5; index += 1) first = await waitNewPresentation(first, `independent-live-sample-${index + 1}`);
      report.initialPerformance = performanceSummary(await page.evaluate(() => structuredClone(window.p0Stats)));
      report.initialScreenshot = path.join(scenarioDir, 'sync-initial.png');
      await page.screenshot({ path: report.initialScreenshot, fullPage: true });

      await pauseAndVerify();
      await seek(3);
      await control({ action: 'step', direction: 1 }, (metadata, before) => {
        near(metadata.log_time_s, 3 + 1 / 60, 'forward one source frame');
        assert.equal(metadata.state, 'paused');
        assert.ok(metadata.epoch > before.epoch);
      });
      await control({ action: 'step', direction: -1 }, metadata => {
        near(metadata.log_time_s, 3, 'backward one source frame');
        assert.equal(metadata.state, 'paused');
      });
      if (scenario === 'base') {
        await speedPlayback(0.1);
        await speedPlayback(10);
      }
      for (const position of [8, 1, 6]) await seek(position);
      await control({ action: 'stop' }, (metadata, before) => {
        near(metadata.log_time_s, 0, 'stop resets presented position');
        assert.equal(metadata.state, 'ready');
        assert.ok(metadata.epoch > before.epoch);
      });
      const replay = await control({ action: 'play' }, metadata => {
        assert.ok(['playing', 'ended'].includes(metadata.state), 'replay must present playing/ended state');
        assert.ok(metadata.log_time_s >= 0 && metadata.log_time_s <= 12);
      });
      const afterReplay = await waitNewPresentation(replay, 'replay-progression', 0.25);
      assert.ok(afterReplay.metadata.log_time_s > replay.metadata.log_time_s || replay.metadata.log_time_s === 12, 'replay must advance displayed log time');
      await pauseAndVerify();
      if (runResilience) await resilience();
      if (runSoakMs > 0) await soak();
      assert.ok(report.requests.every(request => request.allowed), 'all page requests must remain on loopback origins');
      report.identity.status = 'passed';
      report.controlStatus = 'passed';
      report.status = 'passed';
      report.passScope = 'Frame identity, presented controls and any requested reconnect/soak assertions; performance has no acceptance threshold.';
    } catch (error) {
      report.status = 'failed';
      report.error = { name: error.name, message: error.message };
      throw error;
    } finally {
      // A pending business exception from catch must remain the test's primary failure.
      const hadBusinessFailure = report.status === 'failed' && Boolean(report.error);
      const reportPath = path.join(scenarioDir, 'sync-presentation.json');
      let finalizationError = null;
      let contextClosed = !context;
      report.finalization = { status: 'in-progress', contextClosed };
      const recordFinalizationError = (stage, error) => {
        finalizationError ??= error;
        report.status = 'failed';
        report.cleanupError ??= error.message;
        report.cleanupErrors ??= [];
        report.cleanupErrors.push({ stage, name: error.name, message: error.message });
      };
      const saveReport = async stage => {
        report.finalization.stage = stage;
        try { await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`); }
        catch (error) {
          recordFinalizationError(`write-${stage}`, error);
          console.error(`P0 sync report write failed (${stage}): ${error.message}`);
        }
      };
      if (page && !page.isClosed()) {
        try {
          report.finalStats = await page.evaluate(() => structuredClone(window.p0Stats ?? {}));
          report.presentations = report.finalStats.presentations ?? [];
          report.latencies_ms = report.finalStats.latencies_ms ?? [];
          report.performance = performanceSummary(report.finalStats);
          // Preserve the useful measurements before screenshot/video finalization can fail.
          await saveReport('stats-checkpoint');
          report.finalSample = await sample('final-diagnostic');
          report.screenshot = path.join(scenarioDir, 'sync-final.png');
          await page.screenshot({ path: report.screenshot, fullPage: true, timeout: 5000 });
          report.pageText = (await page.locator('body').innerText({ timeout: 5000 })).slice(0, 6000);
          if (page.video()) report.recording = await page.video().path();
          await page.evaluate(async () => { if (typeof window.p0Disconnect === 'function') await window.p0Disconnect(); });
        } catch (error) { report.captureOrCleanupError = error.message; }
      }
      await saveReport('before-context-close');
      try {
        await context?.close();
        contextClosed = true;
      } catch (error) {
        recordFinalizationError('context-close', error);
      } finally {
        report.finalization.contextClosed = contextClosed;
        if (contextClosed) {
          try {
            await rm(profileDir, { recursive: true, force: true });
            report.profile.removed = true;
          } catch (error) { recordFinalizationError('remove-profile', error); }
        } else {
          report.profile.removed = false;
          report.profile.cleanupDeferred = 'Context closure was not confirmed; retain profile for supervisor process cleanup.';
        }
        report.finalization.status = finalizationError ? 'failed' : 'completed';
        await saveReport('final');
      }
      console.log(`P0 sync browser report: ${reportPath}`);
      console.log(`P0 sync ${scenario} status: ${report.status}; performance: ${report.performance.status}`);
      if (finalizationError && !hadBusinessFailure) throw finalizationError;
    }
  });
}
