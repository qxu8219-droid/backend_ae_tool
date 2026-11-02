/**
 * Run: npm ci && npm run probe
 * Optional: CHROME_BIN=/path/to/chrome P0_BROWSER_OUTPUT=/absolute/output/dir npm run probe
 * Uses real headless Chrome, a fresh temporary profile and an ephemeral loopback server.
 * No user browser connection, mock browser APIs, media device access, or GPU workloads.
 */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const outputDir = path.resolve(process.env.P0_BROWSER_OUTPUT ?? path.join(here, '../../../outputs/p0/browser-capabilities'));
const executablePath = process.env.CHROME_BIN ?? '/usr/bin/google-chrome';
const html = `<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>P0 browser capabilities</title>
<style>
body { margin:32px; background:#eef2f7; color:#172235; font:16px system-ui,sans-serif; }
main { margin:auto; max-width:1120px; } h1 { font-size:30px; margin-bottom:8px; }
p { color:#43536b; } pre { background:white; border:1px solid #cdd6e2; border-radius:10px;
padding:20px; font:13px/1.5 monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
video,canvas { width:160px; height:90px; margin-right:16px; background:#13223d; }
</style><main><h1>P0 browser capabilities</h1>
<p>Real headless Chrome · isolated temporary profile · loopback HTTP · software rendering</p>
<canvas id="source" width="160" height="90"></canvas><video id="video" muted autoplay playsinline></video>
<pre id="result">Probe running…</pre></main></html>`;

test('system Chrome exposes P0 media APIs and presents a real local video frame', { timeout: 45_000 }, async () => {
  await mkdir(outputDir, { recursive: true });
  const profileDir = await mkdtemp(path.join(tmpdir(), 'p0-browser-capabilities-'));
  const server = createServer((request, response) => {
    if (request.url !== '/' && request.url !== '/favicon.ico') {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    response.end(html);
  });
  const requests = [];
  const pageErrors = [];
  let context;
  let serverStarted = false;
  const report = {
    schemaVersion: 1,
    timestamp: new Date().toISOString(),
    mode: 'real-headless-browser',
    browserApisMocked: false,
    executablePath,
    profile: { temporary: true, path: profileDir, removed: false },
    gpu: { disabledByLaunchFlags: true, videoDecodePreference: 'prefer-software' },
    scope: 'API availability, object construction, SDP generation, and local canvas video frame callback; not native WebRTC end-to-end decoding.',
    requests,
    pageErrors,
  };
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    serverStarted = true;
    const origin = `http://127.0.0.1:${server.address().port}`;
    report.origin = origin;
    report.launchArgs = [
      '--disable-gpu', '--disable-software-rasterizer', '--use-gl=disabled',
      '--disable-accelerated-video-decode', '--disable-accelerated-video-encode',
      '--disable-background-networking', '--autoplay-policy=no-user-gesture-required',
    ];
    context = await chromium.launchPersistentContext(profileDir, {
      executablePath,
      headless: true,
      viewport: { width: 1280, height: 960 },
      serviceWorkers: 'block',
      args: report.launchArgs,
    });
    report.browserVersion = context.browser().version();
    await context.route('**/*', async route => {
      const url = route.request().url();
      const allowed = new URL(url).origin === origin;
      requests.push({ url, allowed });
      if (allowed) await route.continue();
      else await route.abort('blockedbyclient');
    });
    const page = context.pages()[0] ?? await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(origin, { waitUntil: 'load' });
    report.capabilities = await page.evaluate(async () => {
      const describeError = error => ({ name: error.name, message: error.message });
      const result = {
        navigator: {
          userAgent: navigator.userAgent,
          platform: navigator.platform,
          webdriver: navigator.webdriver,
          secureContext: isSecureContext,
          mediaDevices: typeof navigator.mediaDevices === 'object',
          getUserMedia: typeof navigator.mediaDevices?.getUserMedia === 'function',
        },
        webRTC: {
          RTCPeerConnection: typeof RTCPeerConnection === 'function',
          RTCRtpReceiver: typeof RTCRtpReceiver === 'function',
          receiverCreateEncodedStreams: typeof globalThis.RTCRtpReceiver?.prototype.createEncodedStreams === 'function',
          senderCreateEncodedStreams: typeof globalThis.RTCRtpSender?.prototype.createEncodedStreams === 'function',
          RTCRtpScriptTransform: typeof RTCRtpScriptTransform === 'function',
        },
        webCodecs: {
          VideoDecoder: typeof VideoDecoder === 'function',
          EncodedVideoChunk: typeof EncodedVideoChunk === 'function',
          VideoFrame: typeof VideoFrame === 'function',
          decoderConfigs: {},
        },
        video: {
          requestVideoFrameCallback: typeof HTMLVideoElement.prototype.requestVideoFrameCallback === 'function',
          cancelVideoFrameCallback: typeof HTMLVideoElement.prototype.cancelVideoFrameCallback === 'function',
          canvasCaptureStream: typeof HTMLCanvasElement.prototype.captureStream === 'function',
        },
      };
      if (result.webRTC.RTCPeerConnection) {
        const pc = new RTCPeerConnection({ iceServers: [], encodedInsertableStreams: true });
        let worker;
        let workerUrl;
        try {
          const transceiver = pc.addTransceiver('video', { direction: 'recvonly' });
          result.webRTC.peerConnectionCreated = true;
          const offer = await pc.createOffer();
          result.webRTC.videoOfferCreated = offer.type === 'offer' && offer.sdp.includes('m=video');
          result.webRTC.receiverCodecs = RTCRtpReceiver.getCapabilities('video')?.codecs.map(codec => ({
            mimeType: codec.mimeType, clockRate: codec.clockRate, sdpFmtpLine: codec.sdpFmtpLine ?? '',
          }));
          if (result.webRTC.receiverCreateEncodedStreams) {
            try {
              const streams = transceiver.receiver.createEncodedStreams();
              result.webRTC.receiverEncodedStreamsCreated = streams.readable instanceof ReadableStream && streams.writable instanceof WritableStream;
            } catch (error) { result.webRTC.receiverEncodedStreamsError = describeError(error); }
          }
          if (result.webRTC.RTCRtpScriptTransform) {
            try {
              // Use a distinct receiver; these APIs cannot share one receiver.
              const scriptReceiver = pc.addTransceiver('video', { direction: 'recvonly' }).receiver;
              workerUrl = URL.createObjectURL(new Blob([
                'self.onrtctransform = event => { event.transformer.readable.pipeTo(event.transformer.writable); };',
              ], { type: 'text/javascript' }));
              worker = new Worker(workerUrl);
              scriptReceiver.transform = new RTCRtpScriptTransform(worker);
              result.webRTC.scriptTransformAssigned = scriptReceiver.transform instanceof RTCRtpScriptTransform;
            } catch (error) { result.webRTC.scriptTransformError = describeError(error); }
          }
        } catch (error) { result.webRTC.error = describeError(error); }
        finally {
          pc.close();
          worker?.terminate();
          if (workerUrl) URL.revokeObjectURL(workerUrl);
        }
      }
      if (result.webCodecs.VideoDecoder) {
        try {
          const decoder = new VideoDecoder({ output: frame => frame.close(), error: () => {} });
          result.webCodecs.decoderConstructed = decoder.state === 'unconfigured';
          decoder.close();
          for (const codec of ['avc1.42E01E', 'vp8', 'vp09.00.10.08']) {
            try {
              const support = await VideoDecoder.isConfigSupported({
                codec, codedWidth: 160, codedHeight: 90, hardwareAcceleration: 'prefer-software',
              });
              result.webCodecs.decoderConfigs[codec] = { supported: support.supported, config: support.config };
            } catch (error) { result.webCodecs.decoderConfigs[codec] = { error: describeError(error) }; }
          }
        } catch (error) { result.webCodecs.error = describeError(error); }
      }
      if (result.video.requestVideoFrameCallback && result.video.canvasCaptureStream) {
        const canvas = document.getElementById('source');
        const video = document.getElementById('video');
        const drawing = canvas.getContext('2d');
        let stream;
        let callbackId;
        let interval;
        let timeout;
        try {
          let frame = 0;
          const paint = () => {
            drawing.fillStyle = frame % 2 ? '#23745b' : '#2c568e';
            drawing.fillRect(0, 0, canvas.width, canvas.height);
            drawing.fillStyle = 'white';
            drawing.font = '18px sans-serif';
            drawing.fillText(`P0 frame ${++frame}`, 16, 50);
          };
          paint();
          stream = canvas.captureStream(10);
          video.srcObject = stream;
          const presented = new Promise((resolve, reject) => {
            timeout = setTimeout(() => reject(new Error('No video frame callback within 8 seconds')), 8000);
            callbackId = video.requestVideoFrameCallback((now, metadata) => resolve({
              callbackTime: now, width: metadata.width, height: metadata.height,
              presentedFrames: metadata.presentedFrames, mediaTime: metadata.mediaTime,
            }));
          });
          interval = setInterval(paint, 100);
          await video.play();
          result.video.frameCallback = await presented;
        } catch (error) { result.video.error = describeError(error); }
        finally {
          clearTimeout(timeout);
          clearInterval(interval);
          if (callbackId !== undefined) video.cancelVideoFrameCallback(callbackId);
          stream?.getTracks().forEach(track => track.stop());
        }
      }
      return result;
    });
    await page.locator('#result').evaluate((element, value) => {
      element.textContent = JSON.stringify(value, null, 2);
    }, { browserVersion: report.browserVersion, ...report.capabilities });
    report.screenshot = path.join(outputDir, 'browser-capabilities.png');
    await page.screenshot({ path: report.screenshot, fullPage: true });

    const { navigator, webRTC, webCodecs, video } = report.capabilities;
    assert.equal(navigator.secureContext, true, 'localhost must be a secure context');
    assert.match(navigator.userAgent, /HeadlessChrome\//, 'probe must run real headless Chrome');
    assert.equal(webRTC.peerConnectionCreated, true);
    assert.equal(webRTC.videoOfferCreated, true);
    assert.equal(webRTC.receiverEncodedStreamsCreated || webRTC.scriptTransformAssigned, true, 'an encoded receive transform API must be usable');
    assert.equal(webCodecs.decoderConstructed, true);
    assert.equal(video.frameCallback?.width, 160);
    assert.equal(video.frameCallback?.height, 90);
    assert.ok(video.frameCallback?.presentedFrames >= 1);
    assert.deepEqual(pageErrors, []);
    assert.ok(requests.length >= 1);
    assert.ok(requests.every(request => request.allowed), 'page must only request its local origin');
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed';
    report.error = { name: error.name, message: error.message };
    throw error;
  } finally {
    await context?.close();
    if (serverStarted) await new Promise(resolve => server.close(resolve));
    await rm(profileDir, { recursive: true, force: true });
    report.profile.removed = true;
    await writeFile(path.join(outputDir, 'browser-capabilities.json'), `${JSON.stringify(report, null, 2)}\n`);
    console.log(`P0 browser capability report: ${path.join(outputDir, 'browser-capabilities.json')}`);
    console.log(`P0 browser capability status: ${report.status}`);
  }
});
