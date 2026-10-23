/**
 * Real local media fault/recovery acceptance test, driven by the Python supervisor.
 * Run through that supervisor (gateway + preview initially live, source absent).
 * P0_FAULT_DIR: unique absolute mailbox directory; P0_BROWSER_OUTPUT: evidence dir.
 * Other env: P0_WHEP_URL, P0_MEDIAMTX_API_URL, CHROME_BIN, P0_BROWSER_TIMEOUT_MS.
 * Worker delivery faults affect actual received messages, never source data. They
 * are not network packet-loss simulation. Pixel identity uses an independent CRC.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { chromium } from 'playwright';
import { decodeCaptureStamp } from './stamp-oracle.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const playerUrl = process.env.P0_WHEP_URL ?? 'http://127.0.0.1:8768/?autoconnect=1&sync=1';
const sessionsUrl = process.env.P0_MEDIAMTX_API_URL ?? 'http://127.0.0.1:9997/v3/webrtcsessions/list';
const outputDir = path.resolve(process.env.P0_BROWSER_OUTPUT ?? path.join(here, '../../../outputs/p0/sync-faults-browser'));
const mailboxDir = process.env.P0_FAULT_DIR;
const executablePath = process.env.CHROME_BIN ?? '/usr/bin/google-chrome';
const readyTimeout = Number(process.env.P0_BROWSER_TIMEOUT_MS ?? 60_000);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function isLoopbackUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  } catch { return false; }
}

// This observes native instances and changes only delivery to the application's
// onmessage callback. RTCRtpScriptTransform still receives the real Worker.
function installFaultObserver() {
  const observation = window.__p0Fault = {
    documentId: `${Date.now()}-${Math.random()}`, mode: 'pass', connections: [], workers: [],
    received: {}, delivered: {}, dropped: {}, duplicated: {}, delayed: {},
    closedDroppedFrames: 0, closedPendingFrames: 0, pending: 0, maxPending: 0,
    deliveredSequence: [], wrapperErrors: [],
  };
  const bump = (counter, type) => { counter[type] = (counter[type] ?? 0) + 1; };
  const lifecycle = event => window.__p0FaultLifecycle?.({ documentId: observation.documentId, ...event }).catch(() => {});
  const NativePeerConnection = window.RTCPeerConnection;
  window.RTCPeerConnection = new Proxy(NativePeerConnection, {
    construct(target, args, newTarget) {
      const connection = Reflect.construct(target, args, newTarget);
      const index = observation.connections.push(connection) - 1;
      const close = connection.close.bind(connection);
      connection.close = () => {
        close();
        lifecycle({ kind: 'pc-close', index, state: connection.connectionState });
      };
      return connection;
    },
  });
  if (window.webkitRTCPeerConnection === NativePeerConnection) window.webkitRTCPeerConnection = window.RTCPeerConnection;
  const NativeWorker = window.Worker;
  window.Worker = new Proxy(NativeWorker, {
    construct(target, args, newTarget) {
      const worker = Reflect.construct(target, args, newTarget);
      const record = { index: observation.workers.length, terminated: false, pending: 0, maxPending: 0,
        clearedOnTerminate: 0, received: {}, delivered: {} };
      observation.workers.push(record);
      const pending = new Map();
      let handler = null;
      const closeFrame = (message, category) => {
        if (message?.frame) { message.frame.close(); observation[category] += 1; }
      };
      const deliver = (message, originalEvent = null) => {
        if (record.terminated) { closeFrame(message, 'closedPendingFrames'); return; }
        bump(observation.delivered, message.type); bump(record.delivered, message.type);
        if (['metadata', 'decoded_frame'].includes(message.type)) {
          observation.deliveredSequence.push({ type: message.type, packet_id: message.packet_id, session_id: message.session_id });
          if (observation.deliveredSequence.length > 180) observation.deliveredSequence.shift();
        }
        if (handler) handler.call(worker, originalEvent ?? new MessageEvent('message', { data: message }));
        else closeFrame(message, 'closedDroppedFrames');
      };
      const enqueue = (message, delay) => {
        const timer = setTimeout(() => {
          pending.delete(timer); record.pending = pending.size; observation.pending -= 1;
          deliver(message);
        }, delay);
        pending.set(timer, message); record.pending = pending.size; observation.pending += 1;
        record.maxPending = Math.max(record.maxPending, record.pending);
        observation.maxPending = Math.max(observation.maxPending, observation.pending);
      };
      worker.addEventListener('message', event => {
        const message = event.data;
        const type = message?.type ?? 'unknown';
        bump(observation.received, type); bump(record.received, type);
        if (record.terminated) { closeFrame(message, 'closedPendingFrames'); return; }
        if ((observation.mode === 'drop-metadata' && type === 'metadata') ||
            (observation.mode === 'drop-video' && type === 'decoded_frame')) {
          bump(observation.dropped, type); closeFrame(message, 'closedDroppedFrames'); return;
        }
        const paired = type === 'metadata' || type === 'decoded_frame';
        if (paired && observation.mode === 'reorder' && record.received[type] % 3 === 0) {
          bump(observation.delayed, type); enqueue(message, 200); return;
        }
        if (paired && observation.mode === 'duplicate') {
          // Clone before application delivery: the real canvas path closes frames.
          try {
            const duplicate = type === 'decoded_frame' ? { ...message, frame: message.frame.clone() } : structuredClone(message);
            bump(observation.duplicated, type); enqueue(duplicate, 5);
          } catch (error) { observation.wrapperErrors.push(error.message); }
        }
        deliver(message, event);
      });
      Object.defineProperty(worker, 'onmessage', { configurable: true,
        get: () => handler, set: value => { handler = typeof value === 'function' ? value : null; } });
      const terminate = worker.terminate.bind(worker);
      worker.terminate = () => {
        record.terminated = true;
        for (const [timer, message] of pending) {
          clearTimeout(timer); closeFrame(message, 'closedPendingFrames'); record.clearedOnTerminate += 1;
        }
        observation.pending -= pending.size; pending.clear(); record.pending = 0;
        terminate(); lifecycle({ kind: 'worker-terminate', ...record });
      };
      return worker;
    },
  });
}

test('real paired canvas stays honest through delivery faults, gateway loss, reload and source death',
  { timeout: 7 * 120_000 + readyTimeout * 8 + 120_000 }, async () => {
    assert.ok(mailboxDir && path.isAbsolute(mailboxDir), 'P0_FAULT_DIR must name the supervisor\'s unique absolute mailbox directory');
    assert.ok(isLoopbackUrl(playerUrl) && isLoopbackUrl(sessionsUrl), 'player and MediaMTX API must remain on loopback');
    assert.ok(Number.isFinite(readyTimeout) && readyTimeout > 0);
    await mkdir(outputDir, { recursive: true });
    const profileDir = await mkdtemp(path.join(tmpdir(), 'p0-sync-faults-'));
    const url = new URL(playerUrl);
    url.searchParams.set('sync', '1'); url.searchParams.set('autoconnect', '1');
    url.searchParams.delete('metadata_delay_ms'); url.searchParams.delete('video_delay_ms');
    const reportPath = path.join(outputDir, 'sync-faults.json');
    const report = {
      schemaVersion: 1, timestamp: new Date().toISOString(), status: 'running', url: url.href, sessionsUrl,
      mode: 'real-headless-browser', mediaMocked: false, metadataMocked: false, networkPacketLossSimulated: false,
      injectionScope: 'Drop, delay and duplicate actual native Worker messages before application onmessage delivery.',
      executablePath, profile: { temporary: true, path: profileDir, removed: false },
      expected: { minimumRecoveryPresentations: 30, markerBits: 64, markerLayout: 'capture32_crc32', markerMismatches: 0 },
      scenarios: [], independentSamples: [], actions: [], requests: [], responses: [],
      apiSamples: [], lifecycle: [], pageErrors: [], console: [], cleanupErrors: [],
    };
    let requestId = 0, context, page, finalizationError;

    async function checkpoint(stage) {
      report.checkpoint = { stage, timestamp: new Date().toISOString() };
      const temporary = `${reportPath}.tmp`;
      await writeFile(temporary, `${JSON.stringify(report, null, 2)}\n`);
      await rename(temporary, reportPath);
    }

    async function action(name) {
      const request = { request_id: ++requestId, action: name };
      const record = { ...request, startedWallMs: Date.now(), status: 'requested' };
      report.actions.push(record);
      await checkpoint(`request-${name}`);
      const temporary = path.join(mailboxDir, `fault-request.${process.pid}.tmp`);
      await writeFile(temporary, `${JSON.stringify(request)}\n`);
      await rename(temporary, path.join(mailboxDir, 'fault-request.json'));
      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline) {
        let response;
        try { response = JSON.parse(await readFile(path.join(mailboxDir, 'fault-response.json'), 'utf8')); }
        catch (error) { if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error; }
        if (response?.request_id === request.request_id) {
          record.response = response; record.finishedWallMs = Date.now(); record.status = response.ok ? 'completed' : 'failed';
          await checkpoint(`response-${name}`);
          assert.equal(response.ok, true, `${name}: supervisor rejected action: ${response.error ?? 'no reason'}`);
          return response;
        }
        await pause(100);
      }
      throw new Error(`${name}: supervisor did not acknowledge request ${request.request_id} within 120 seconds`);
    }

    async function browserState() {
      return page.evaluate(() => ({
        stats: structuredClone(window.p0Stats ?? {}),
        status: document.querySelector('#status')?.textContent ?? '', notice: document.querySelector('#notice')?.textContent ?? '',
        instrumentation: structuredClone({ ...window.__p0Fault, connections: undefined }),
        connections: window.__p0Fault.connections.map((pc, index) => ({ index, state: pc.connectionState, iceState: pc.iceConnectionState })),
        videoDetached: document.querySelector('video')?.srcObject === null,
      }));
    }

    async function sample(label) {
      const value = await page.evaluate(() => {
        // One synchronous callback binds the selected metadata to actual canvas pixels.
        const stats = window.p0Stats, metadata = structuredClone(stats?.last_metadata ?? null);
        const canvas = document.querySelector('#syncCanvas'), nativeWidth = document.querySelector('video')?.videoWidth ?? 0;
        const pixelSamples = [];
        if (canvas && nativeWidth && metadata?.marker_pixels?.length === 64) {
          const drawing = canvas.getContext('2d'), scale = 1240 / nativeWidth;
          for (const [sourceX, sourceY] of metadata.marker_pixels) {
            const x = Math.round(sourceX * scale), y = Math.round(sourceY * scale);
            pixelSamples.push({ x, y, rgba: [...drawing.getImageData(x, y, 1, 1).data] });
          }
        }
        return { observedWallMs: Date.now(), metadata, nativeWidth, pixelSamples,
          presentations: stats?.sync_presented_frames ?? 0, metadataFrames: stats?.metadata_frames ?? 0,
          markerMismatches: stats?.marker_mismatches, lastMarkerFailure: stats?.last_marker_failure ?? null,
          decoderConfigured: structuredClone(stats?.decoder_configured ?? null), errors: [...(stats?.errors ?? [])],
          encodedStats: structuredClone(stats?.encoded_stats ?? null),
        };
      });
      value.label = label;
      value.stamp = decodeCaptureStamp(value.pixelSamples.map(pixel => pixel.rgba));
      report.independentSamples.push(value);
      return value;
    }

    function assertIdentity(value, sessionId) {
      assert.equal(value.markerMismatches, 0, `client marker mismatch: ${JSON.stringify(value.lastMarkerFailure)}`);
      assert.ok(value.metadataFrames > 0, 'actual SEI metadata must arrive');
      assert.equal(value.metadata?.sei_schema, 'p0.snapshot.v1');
      assert.equal(value.metadata?.marker_layout, 'capture32_crc32');
      assert.equal(value.pixelSamples.length, 64, 'independently read every bit of the rendered canvas stamp');
      assert.equal(value.stamp.confident, true, 'actual canvas stamp must be readable');
      assert.equal(value.stamp.crcValid, true, 'actual canvas stamp CRC32 must match the four big-endian capture ID bytes');
      assert.equal(value.stamp.captureId, value.metadata.capture_id, 'canvas capture ID must exactly match displayed metadata');
      assert.equal(value.metadata.render_seq, value.metadata.capture_id);
      assert.ok(value.decoderConfigured?.codec?.startsWith('avc1.'), 'real H264 VideoDecoder must be configured');
      assert.ok(value.errors.length <= 30, 'error history must remain bounded');
      if (value.encodedStats?.pending_decodes !== undefined) assert.ok(value.encodedStats.pending_decodes <= 90, 'decoder pending work must remain bounded');
      if (sessionId) assert.equal(value.metadata.session_id, sessionId, 'displayed source session must match the independently observed worker');
    }

    async function sourceEvidence(response, stage) {
      assert.ok(response.worker_dir && path.isAbsolute(response.worker_dir), `${stage}: supervisor must return absolute worker_dir`);
      const ready = JSON.parse(await readFile(path.join(response.worker_dir, 'ready.json'), 'utf8'));
      const snapshot = JSON.parse(await readFile(path.join(response.worker_dir, 'latest_snapshot.json'), 'utf8'));
      assert.ok(typeof snapshot.session_id === 'string' && snapshot.session_id.length > 0);
      if (response.session_id) assert.equal(snapshot.session_id, response.session_id);
      if (ready.session_id) assert.equal(snapshot.session_id, ready.session_id);
      return { workerDir: response.worker_dir, ready, snapshot, sessionId: snapshot.session_id };
    }

    async function readSessions(stage) {
      const response = await fetch(sessionsUrl, { redirect: 'error', signal: AbortSignal.timeout(5000) });
      assert.ok(response.ok, `${stage}: MediaMTX API HTTP ${response.status}`);
      const body = await response.json();
      assert.ok(Array.isArray(body.items), 'MediaMTX sessions API must contain items[]');
      const value = { stage, observedWallMs: Date.now(), count: body.itemCount ?? body.items.length, ids: body.items.map(item => item.id) };
      report.apiSamples.push(value);
      return value;
    }

    async function waitSessions(stage, expectedCount, forbiddenIds = []) {
      const deadline = Date.now() + 15_000;
      let value;
      do {
        value = await readSessions(stage);
        if (value.count === expectedCount && forbiddenIds.every(id => !value.ids.includes(id))) return value;
        await pause(250);
      } while (Date.now() < deadline);
      assert.equal(value.count, expectedCount, `${stage}: expected ${expectedCount} active MediaMTX sessions`);
      assert.ok(forbiddenIds.every(id => !value.ids.includes(id)), `${stage}: superseded MediaMTX sessions must be removed`);
      return value;
    }

    async function connectionEvidence(stage, previousIndex = -1) {
      const state = await browserState();
      const active = state.connections.filter(pc => pc.state === 'connected');
      assert.equal(active.length, 1, `${stage}: exactly one native PC must be connected`);
      assert.ok(active[0].index > previousIndex, `${stage}: a new native PC must be created`);
      assert.ok(state.connections.every(pc => pc.index === active[0].index || pc.state === 'closed'), `${stage}: superseded native PCs must be closed`);
      const creation = report.responses.filter(response => response.method === 'POST' && response.status === 201 && response.location).at(-1);
      assert.ok(creation, `${stage}: successful WHEP POST must return a Location`);
      const resourceUrl = new URL(creation.location, creation.url).href;
      assert.ok(isLoopbackUrl(resourceUrl));
      return { connectionIndex: active[0].index, resourceUrl, state, sessions: await waitSessions(stage, 1) };
    }

    async function waitFirst(stage, previousCount, sessionId) {
      await page.waitForFunction(({ previousCount, sessionId }) => {
        const stats = window.p0Stats;
        return stats?.marker_mismatches > 0 || (stats?.sync_presented_frames > previousCount &&
          (!sessionId || stats.last_metadata?.session_id === sessionId));
      }, { previousCount, sessionId }, { timeout: readyTimeout });
      const first = await sample(`${stage}-first`); assertIdentity(first, sessionId); return first;
    }

    async function presentedControl(command, sessionId) {
      const before = await sample(`before-control-${command.action}`); assertIdentity(before, sessionId);
      const accepted = await page.evaluate(async command => {
        await window.p0Control(command);
        return structuredClone(window.p0Stats.last_control?.control ?? null);
      }, command);
      assert.ok(Number.isSafeInteger(accepted?.request_id) && accepted.request_id > before.metadata.request_id,
        `${command.action}: control must receive a new request ID`);
      assert.equal(accepted.action, command.action);
      await page.waitForFunction(({ requestId, sessionId, captureId, playing }) => {
        const stats = window.p0Stats, metadata = stats?.last_metadata;
        return stats?.marker_mismatches > 0 || (metadata?.session_id === sessionId && metadata.request_id === requestId &&
          metadata.capture_id > captureId && (!playing || (metadata.state === 'playing' && metadata.log_time_s > 0)));
      }, { requestId: accepted.request_id, sessionId, captureId: before.metadata.capture_id, playing: command.action === 'play' },
      { timeout: readyTimeout });
      const presented = await sample(`presented-control-${command.action}`); assertIdentity(presented, sessionId);
      assert.equal(presented.metadata.request_id, accepted.request_id, `${command.action}: control must be acknowledged by displayed metadata`);
      return { command, accepted, before, presented };
    }

    async function verifyRecovery(stage, previousCount, sessionId, motionAfter = null) {
      await page.waitForFunction(({ previousCount, sessionId }) => {
        const stats = window.p0Stats;
        return stats?.marker_mismatches > 0 || (stats?.sync_presented_frames >= previousCount + 30 &&
          (!sessionId || stats.last_metadata?.session_id === sessionId));
      }, { previousCount, sessionId }, { timeout: readyTimeout });
      let previous = await sample(`${stage}-30-new-presentations`); assertIdentity(previous, sessionId);
      assert.ok(previous.presentations >= previousCount + 30, `${stage}: at least 30 new paired canvas presentations are required`);
      if (motionAfter) {
        assert.equal(motionAfter.session_id, sessionId, `${stage}: movement comparison must use the same source session`);
        assert.equal(previous.metadata.state, 'playing', `${stage}: recovered source must still be playing`);
        assert.equal(previous.metadata.speed, 0.1, `${stage}: recovered source must retain slow playback`);
        assert.ok(previous.metadata.log_time_s > motionAfter.log_time_s, `${stage}: recovered log time must advance beyond the pre-fault/frozen time`);
      }
      const samples = [previous];
      for (let index = 1; index <= 5; index += 1) {
        await page.waitForFunction(({ count }) => window.p0Stats?.marker_mismatches > 0 || window.p0Stats?.sync_presented_frames > count,
          { count: previous.presentations }, { timeout: readyTimeout });
        const current = await sample(`${stage}-independent-${index}`); assertIdentity(current, sessionId);
        assert.ok(current.metadata.capture_id > previous.metadata.capture_id, `${stage}: displayed capture IDs must advance within a session`);
        assert.ok(current.metadata.epoch >= previous.metadata.epoch, `${stage}: displayed epochs must not regress`);
        if (motionAfter) {
          assert.equal(current.metadata.state, 'playing', `${stage}: each independently sampled recovery frame must be playing`);
          assert.equal(current.metadata.speed, 0.1);
          assert.ok(current.metadata.log_time_s > previous.metadata.log_time_s, `${stage}: log time must advance on new displayed frames`);
        }
        samples.push(current); previous = current;
      }
      let motion = null;
      if (motionAfter) {
        const first = samples[0].metadata, last = previous.metadata;
        const poseChanged = first.joints.some((value, index) => value !== last.joints[index]) ||
          ['x', 'y', 'yaw'].some(axis => first.chassis[axis] !== last.chassis[axis]);
        assert.equal(poseChanged, true, `${stage}: displayed robot pose must change while recovered log time advances`);
        motion = { status: 'passed', sessionId, baselineLogTime: motionAfter.log_time_s,
          firstLogTime: first.log_time_s, lastLogTime: last.log_time_s, poseChanged };
      }
      const state = await browserState();
      assert.deepEqual(state.instrumentation.wrapperErrors, [], 'test wrapper must not fail while cloning real messages');
      assert.ok(state.instrumentation.maxPending <= 90, 'fault injection delay queue must remain bounded during real delivery');
      return { newPresentations: previous.presentations - previousCount, first: samples[0], last: previous, samples, state, motion };
    }

    async function frozenSnapshot() {
      const { png, ...value } = await page.evaluate(() => ({
        png: document.querySelector('#syncCanvas').toDataURL('image/png'),
        metadata: structuredClone(window.p0Stats.last_metadata ?? null), presentations: window.p0Stats.sync_presented_frames,
        markerMismatches: window.p0Stats.marker_mismatches,
        status: document.querySelector('#status').textContent, notice: document.querySelector('#notice').textContent,
      }));
      return { ...value, observedWallMs: Date.now(), canvasSha256: createHash('sha256').update(png).digest('hex') };
    }

    async function verifyFrozen(stage, waitForNotice = false) {
      if (waitForNotice) {
        await page.waitForFunction(() => /等待可配对|waiting/i.test(document.querySelector('#notice')?.textContent ?? ''),
          undefined, { timeout: 15_000 });
      } else await pause(500); // Drain already delivered/in-flight matched pairs before measuring the outage.
      const baseline = await frozenSnapshot(), observations = [];
      assert.equal(baseline.metadata?.state, 'playing', `${stage}: outage must freeze a source that was playing`);
      assert.equal(baseline.metadata.speed, 0.1);
      assert.ok(baseline.metadata.log_time_s > 0, `${stage}: outage must occur after real playback has progressed`);
      for (let index = 0; index < 4; index += 1) {
        await pause(400);
        const frozen = await frozenSnapshot(); observations.push(frozen);
        assert.equal(frozen.canvasSha256, baseline.canvasSha256, `${stage}: the entire canvas must remain frozen`);
        assert.deepEqual(frozen.metadata, baseline.metadata, `${stage}: last_metadata must remain frozen with the canvas`);
        assert.equal(frozen.presentations, baseline.presentations, `${stage}: unpaired data must never count as a presentation`);
        assert.equal(frozen.markerMismatches, 0);
      }
      assert.match(observations.at(-1).notice, /等待可配对|waiting/i, `${stage}: UI must disclose that it is waiting for a pair`);
      return { baseline, observations };
    }

    async function setMode(mode) {
      return page.evaluate(mode => { window.__p0Fault.mode = mode; return structuredClone({ ...window.__p0Fault, connections: undefined }); }, mode);
    }

    async function deliveryOutage(mode, droppedType, sessionId) {
      const scenario = { name: mode, scope: 'native Worker message delivery', status: 'running', before: await setMode(mode) };
      report.scenarios.push(scenario);
      scenario.freeze = await verifyFrozen(mode);
      scenario.after = await setMode('pass');
      assert.ok((scenario.after.dropped[droppedType] ?? 0) > (scenario.before.dropped[droppedType] ?? 0), `${mode}: real incoming messages must actually be dropped`);
      if (droppedType === 'decoded_frame') assert.ok(scenario.after.closedDroppedFrames > scenario.before.closedDroppedFrames, 'dropped real VideoFrames must be closed');
      const opposite = droppedType === 'metadata' ? 'decoded_frame' : 'metadata';
      assert.ok((scenario.after.delivered[opposite] ?? 0) > (scenario.before.delivered[opposite] ?? 0), `${mode}: the other real message side must continue`);
      scenario.recovery = await verifyRecovery(`${mode}-restored`, scenario.freeze.baseline.presentations, sessionId, scenario.freeze.baseline.metadata);
      scenario.status = 'passed'; await checkpoint(mode); return scenario;
    }

    async function disconnectAndVerify(stage, connected) {
      const offset = report.responses.length;
      await page.evaluate(async () => { await window.p0Disconnect(); });
      const state = await browserState();
      assert.ok(state.connections.length > 0 && state.connections.every(pc => pc.state === 'closed'), `${stage}: all native PCs must be closed`);
      assert.equal(state.videoDetached, true, `${stage}: video must release its MediaStream`);
      assert.ok(state.instrumentation.workers.every(worker => worker.terminated && worker.pending === 0), `${stage}: every Worker must terminate and release delayed messages`);
      assert.equal(state.instrumentation.pending, 0, `${stage}: no injected message may survive termination`);
      const deletes = report.responses.slice(offset).filter(response => response.method === 'DELETE' && response.url === connected.resourceUrl);
      assert.ok(deletes.some(response => response.status >= 200 && response.status < 300), `${stage}: normal WHEP resource DELETE must succeed`);
      return { state, deletes, sessions: await waitSessions(stage, 0) };
    }

    try {
      report.baselineSessions = await waitSessions('initial-source-absent', 0);
      report.launchArgs = ['--disable-gpu', '--disable-software-rasterizer', '--use-gl=disabled',
        '--disable-accelerated-video-decode', '--disable-accelerated-video-encode',
        '--disable-background-networking', '--autoplay-policy=no-user-gesture-required'];
      context = await chromium.launchPersistentContext(profileDir, {
        executablePath, headless: true, viewport: { width: 1740, height: 1100 }, serviceWorkers: 'block', args: report.launchArgs,
      });
      report.browserVersion = context.browser().version();
      await context.exposeBinding('__p0FaultLifecycle', (_source, event) => { report.lifecycle.push({ observedWallMs: Date.now(), ...event }); });
      await context.addInitScript(installFaultObserver);
      await context.route('**/*', async route => {
        const request = route.request(), requestedUrl = request.url(), allowed = isLoopbackUrl(requestedUrl);
        report.requests.push({ method: request.method(), url: requestedUrl, allowed });
        if (allowed) await route.continue(); else await route.abort('blockedbyclient');
      });
      page = context.pages()[0] ?? await context.newPage(); page.setDefaultTimeout(readyTimeout);
      page.on('pageerror', error => report.pageErrors.push(error.message));
      page.on('console', message => { if (report.console.length < 120) report.console.push({ type: message.type(), text: message.text() }); });
      page.on('response', response => report.responses.push({ method: response.request().method(), status: response.status(),
        url: response.url(), location: response.headers().location ?? null }));
      const response = await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: readyTimeout });
      assert.ok(response?.ok(), `player must load: HTTP ${response?.status()}`);
      await page.waitForFunction(() => typeof window.p0Connect === 'function' && typeof window.p0Disconnect === 'function' && window.p0Stats);
      await page.waitForFunction(() => /连接失败|failed/i.test(document.querySelector('#status')?.textContent ?? ''), undefined, { timeout: readyTimeout });
      const absent = { name: 'connect-with-no-source', state: await browserState(), canvas: await frozenSnapshot() };
      report.scenarios.push(absent);
      assert.equal(absent.state.stats.sync_presented_frames, 0, 'missing source must never invent a paired presentation');
      assert.equal(absent.state.stats.metadata_frames, 0, 'missing source must never invent SEI metadata');
      assert.equal(absent.state.stats.last_metadata ?? null, null, 'missing source must never invent robot metadata');
      assert.ok(absent.state.stats.errors.length > 0, 'missing source must produce an explicit connection failure');
      assert.ok(absent.state.connections.every(pc => pc.state === 'closed'), 'failed connect must close the native PC');
      absent.sessions = await waitSessions('no-source-failure', 0); absent.status = 'passed'; await checkpoint(absent.name);

      const started = await action('start_worker');
      const firstSource = await sourceEvidence(started, 'start-worker'); report.initialSource = firstSource;
      await page.evaluate(async () => { await window.p0Connect(); });
      const first = await waitFirst('source-start', 0, firstSource.sessionId);
      assert.equal(first.metadata.log_time_s, 0, 'start-paused source must show log zero');
      assert.equal(first.metadata.state, 'ready', 'start-paused source must show its initial ready state');
      const initial = { name: 'source-start-recovery', first,
        recovery: await verifyRecovery('source-start', 0, firstSource.sessionId), status: 'passed' };
      report.scenarios.push(initial);
      let connected = await connectionEvidence('source-start'); await checkpoint(initial.name);

      initial.motion = { speed: await presentedControl({ action: 'speed', speed: 0.1 }, firstSource.sessionId) };
      assert.equal(initial.motion.speed.presented.metadata.speed, 0.1);
      assert.equal(initial.motion.speed.presented.metadata.state, 'ready');
      assert.equal(initial.motion.speed.presented.metadata.log_time_s, 0, 'speed alone must not start the source');
      initial.motion.play = await presentedControl({ action: 'play' }, firstSource.sessionId);
      assert.equal(initial.motion.play.presented.metadata.state, 'playing');
      assert.ok(initial.motion.play.presented.metadata.log_time_s > 0);
      initial.motion.recovery = await verifyRecovery('moving-before-faults', initial.motion.play.presented.presentations,
        firstSource.sessionId, initial.motion.play.presented.metadata);
      await checkpoint('source-playing-before-faults');

      await deliveryOutage('drop-metadata', 'metadata', firstSource.sessionId);
      await deliveryOutage('drop-video', 'decoded_frame', firstSource.sessionId);
      for (const mode of ['reorder', 'duplicate']) {
        const scenario = { name: mode, scope: 'native Worker message delivery', status: 'running', before: await setMode(mode) };
        report.scenarios.push(scenario);
        scenario.beforeMotion = await sample(`${mode}-before-motion`); assertIdentity(scenario.beforeMotion, firstSource.sessionId);
        scenario.recoveryDuringFault = await verifyRecovery(`${mode}-active`, scenario.beforeMotion.presentations,
          firstSource.sessionId, scenario.beforeMotion.metadata);
        scenario.after = await setMode('pass');
        const counter = mode === 'reorder' ? 'delayed' : 'duplicated';
        for (const type of ['metadata', 'decoded_frame']) assert.ok((scenario.after[counter][type] ?? 0) > (scenario.before[counter][type] ?? 0), `${mode}: real ${type} must be affected`);
        await pause(250);
        scenario.recovery = await verifyRecovery(`${mode}-restored`, (await browserState()).stats.sync_presented_frames,
          firstSource.sessionId, scenario.recoveryDuringFault.last.metadata);
        scenario.status = 'passed'; await checkpoint(mode);
      }

      const gateway = { name: 'gateway-stop-start', status: 'running', previous: connected };
      report.scenarios.push(gateway);
      gateway.stop = await action('stop_gateway'); gateway.freeze = await verifyFrozen('gateway-stopped', true);
      gateway.start = await action('start_gateway');
      await page.evaluate(async () => { await window.p0Connect(); });
      gateway.recovery = await verifyRecovery('gateway-restarted', gateway.freeze.baseline.presentations,
        firstSource.sessionId, gateway.freeze.baseline.metadata);
      connected = gateway.connection = await connectionEvidence('gateway-restarted', connected.connectionIndex);
      gateway.status = 'passed'; await checkpoint(gateway.name);

      const reload = { name: 'browser-reload', status: 'running', previous: connected, previousDocument: (await browserState()).instrumentation.documentId };
      report.scenarios.push(reload);
      reload.beforeMotion = await sample('before-browser-reload'); assertIdentity(reload.beforeMotion, firstSource.sessionId);
      const reloadResponse = await page.reload({ waitUntil: 'domcontentloaded', timeout: readyTimeout });
      assert.ok(reloadResponse?.ok());
      reload.recovery = await verifyRecovery('browser-reloaded', 0, firstSource.sessionId, reload.beforeMotion.metadata);
      connected = reload.connection = await connectionEvidence('browser-reloaded');
      assert.notEqual(connected.state.instrumentation.documentId, reload.previousDocument, 'reload must create a new document');
      assert.notEqual(connected.resourceUrl, reload.previous.resourceUrl, 'reload must create a fresh WHEP resource');
      reload.sessions = await waitSessions('reload-old-sessions-released', 1, reload.previous.sessions.ids);
      reload.deleteRequests = report.requests.filter(request => request.method === 'DELETE' && request.url === reload.previous.resourceUrl);
      assert.ok(reload.deleteRequests.length > 0, 'pagehide must attempt DELETE of the pre-reload WHEP resource');
      reload.oldNativeClose = report.lifecycle.filter(event => event.documentId === reload.previousDocument && event.kind === 'pc-close');
      reload.nativeCloseEvidence = reload.oldNativeClose.length ? 'observed-native-close' : 'unload-binding-not-delivered; old-server-session-removal-verified';
      assert.ok(reload.oldNativeClose.every(event => event.state === 'closed'));
      reload.status = 'passed'; await checkpoint(reload.name);

      const restart = { name: 'source-sigkill-reload', status: 'running', previous: await sample('before-source-kill'), previousConnection: connected };
      report.scenarios.push(restart); assertIdentity(restart.previous, firstSource.sessionId);
      restart.kill = await action('crash_worker'); restart.freeze = await verifyFrozen('source-killed', true);
      restart.reload = await action('reload_worker'); restart.source = await sourceEvidence(restart.reload, 'reload-worker');
      assert.notEqual(restart.source.sessionId, firstSource.sessionId, 'reload must create a new source session');
      assert.notEqual(restart.source.workerDir, firstSource.workerDir, 'reload must create a fresh source output directory');
      // Even after a new source exists, the old connection may not silently switch sessions.
      restart.beforeExplicitReconnect = await frozenSnapshot();
      assert.deepEqual(restart.beforeExplicitReconnect.metadata, restart.freeze.baseline.metadata, 'new-session metadata must wait for explicit reconnect');
      assert.equal(restart.beforeExplicitReconnect.canvasSha256, restart.freeze.baseline.canvasSha256);
      await page.evaluate(async () => { await window.p0Connect(); });
      restart.first = await waitFirst('new-source-session', restart.freeze.baseline.presentations, restart.source.sessionId);
      assert.equal(restart.first.metadata.log_time_s, 0, 'new source must restart log time at zero');
      assert.equal(restart.first.metadata.state, 'ready', 'new source must start ready without autoplay');
      assert.ok(restart.first.metadata.capture_id < restart.previous.metadata.capture_id, 'new source capture numbering must restart independently');
      restart.recovery = await verifyRecovery('new-source-session', restart.freeze.baseline.presentations, restart.source.sessionId);
      for (const value of restart.recovery.samples) {
        assert.equal(value.metadata.log_time_s, 0, 'new source must remain at log zero without a play command');
        assert.equal(value.metadata.state, 'ready');
        assert.notEqual(value.metadata.session_id, firstSource.sessionId, 'old-source metadata must never return after new-session presentation');
      }
      connected = restart.connection = await connectionEvidence('new-source-session', connected.connectionIndex);
      restart.status = 'passed'; await checkpoint(restart.name);

      // Leave real delayed frames queued, then prove terminate cancels and closes them.
      await setMode('reorder');
      await page.waitForFunction(() => window.__p0Fault.pending > 0, undefined, { timeout: readyTimeout });
      report.finalDisconnect = await disconnectAndVerify('final-disconnect', connected);
      assert.ok(report.finalDisconnect.state.instrumentation.workers.some(worker => worker.clearedOnTerminate > 0), 'terminate must be exercised with real pending delivery');
      assert.ok(report.requests.every(request => request.allowed), 'all browser requests must stay on loopback');
      assert.deepEqual(report.pageErrors, [], 'the page must not throw uncaught errors');
      report.status = 'passed';
      report.passScope = 'Real source absence/recovery, native Worker delivery faults, gateway restart, browser reload, source SIGKILL/reload, independent canvas CRC and lifecycle cleanup.';
      await checkpoint('all-scenarios-passed');
    } catch (error) {
      report.status = 'failed'; report.error = { name: error.name, message: error.message, stack: error.stack };
      throw error;
    } finally {
      const businessFailure = Boolean(report.error);
      let contextClosed = !context;
      const cleanupFailure = (stage, error) => {
        finalizationError ??= error; report.cleanupErrors.push({ stage, name: error.name, message: error.message }); report.status = 'failed';
      };
      const save = async stage => {
        try { await checkpoint(stage); } catch (error) { cleanupFailure(`save-${stage}`, error); console.error(`P0 fault report save failed: ${error.message}`); }
      };
      // Persist both failure and current evidence before any capture/close can fail.
      await save('before-diagnostics');
      if (page && !page.isClosed()) {
        try { report.finalState = await browserState(); report.finalSample = await sample('final-diagnostic'); }
        catch (error) { cleanupFailure('capture-final-state', error); }
        await save('stats-checkpoint');
        try {
          report.screenshot = path.join(outputDir, 'sync-faults-final.png');
          await page.screenshot({ path: report.screenshot, fullPage: true, timeout: 5000 });
          report.pageText = (await page.locator('body').innerText({ timeout: 5000 })).slice(0, 6000);
        } catch (error) { cleanupFailure('capture-diagnostics', error); }
        try { await page.evaluate(async () => { if (typeof window.p0Disconnect === 'function') await window.p0Disconnect(); }); }
        catch (error) { cleanupFailure('disconnect', error); }
      }
      await save('before-context-close');
      try { await context?.close(); contextClosed = true; }
      catch (error) { cleanupFailure('context-close', error); }
      report.contextClosed = contextClosed;
      if (contextClosed) {
        try { await rm(profileDir, { recursive: true, force: true }); report.profile.removed = true; }
        catch (error) { cleanupFailure('remove-profile', error); }
      } else report.profile.cleanupDeferred = 'Context close unconfirmed; retain profile for supervisor cleanup.';
      await save('final');
      console.log(`P0 sync fault report: ${reportPath}`);
      console.log(`P0 sync fault status: ${report.status}`);
      if (finalizationError && !businessFailure) throw finalizationError;
    }
  });
