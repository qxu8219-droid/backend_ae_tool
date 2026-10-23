import { AppStreamer, StreamType } from '@nvidia/omniverse-webrtc-streaming-library';
import './style.css';

const form = document.querySelector('#connection-form');
const host = document.querySelector('#host');
const port = document.querySelector('#port');
const connectButton = document.querySelector('#connect');
const disconnectButton = document.querySelector('#disconnect');
const status = document.querySelector('#status');
const mediaStatus = document.querySelector('#media-status');
const video = document.querySelector('#remote-video');
const log = document.querySelector('#event-log');
const params = new URLSearchParams(location.search);
const events = [];
let attempted = false;

function record(name, detail) {
  const text = detail instanceof Error
    ? `${detail.name}: ${detail.message}`
    : JSON.stringify(detail ?? '');
  events.push(`${new Date().toISOString()} ${name}: ${text}`);
  if (events.length > 60) events.shift();
  log.textContent = events.join('\n');
  log.scrollTop = log.scrollHeight;
}

function setStatus(text, kind = 'idle') {
  status.textContent = text;
  status.dataset.kind = kind;
}

function handleEvent(name, message) {
  record(name, message);
  if (message?.status === 'error') {
    setStatus('连接错误；查看事件后重新加载重试', 'error');
  }
}

function updateConnectLabel() {
  const name = host.value.trim() === '127.0.0.1' ? 'localhost' : host.value.trim();
  connectButton.textContent = `连接 ${name}:${port.value}`;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (attempted || !form.reportValidity()) return;
  const server = host.value.trim();
  if (!server || /[\s/?#]/.test(server) || server.includes('://')) {
    setStatus('请填写主机名或 IP，不包含协议、路径或空格', 'error');
    return;
  }
  attempted = true;
  connectButton.disabled = true;
  host.disabled = true;
  port.disabled = true;
  disconnectButton.disabled = false;
  setStatus('正在连接…');

  // The direct connection shape and authenticate/maxReconnects values follow
  // NVIDIA web-viewer-sample 1.5.2. mediaPort is deliberately omitted, matching
  // that sample's default null. No 6.x primaryStream settings are used here.
  const streamConfig = {
    videoElementId: 'remote-video',
    audioElementId: 'remote-audio',
    authenticate: true,
    maxReconnects: 20,
    signalingServer: server,
    signalingPort: Number(port.value),
    mediaServer: server,
    nativeTouchEvents: true,
    width: 1280,
    height: 720,
    fps: 30,
    onStart(message) {
      handleEvent('onStart', message);
      if (message?.action === 'start' && message?.status === 'success') {
        setStatus('流连接已建立；等待视频播放', 'connected');
      }
    },
    onUpdate: (message) => handleEvent('onUpdate', message),
    onCustomEvent: (message) => record('onCustomEvent（未做帧关联）', message),
    onStop(message) {
      handleEvent('onStop', message);
      if (message?.status !== 'error') setStatus('流已停止');
      mediaStatus.textContent = '视频流已停止；请重新加载后重连';
    },
    onTerminate(message) {
      handleEvent('onTerminate', message);
      if (message?.status !== 'error') setStatus('连接已断开');
      disconnectButton.disabled = true;
    },
  };
  record('connect', { server, signalingPort: streamConfig.signalingPort, sdk: '5.6.0' });
  try {
    const result = await AppStreamer.connect({ streamConfig, streamSource: StreamType.DIRECT });
    // A resolved connect promise is only a request result, not evidence that
    // a decoded video frame has reached the browser.
    handleEvent('connect result', result);
  } catch (error) {
    handleEvent('connect rejected', error);
    setStatus('连接失败；查看事件后重新加载重试', 'error');
  }
});

disconnectButton.addEventListener('click', async () => {
  disconnectButton.disabled = true;
  try {
    // Public API: false does not request remote Kit termination. Do not mutate
    // the SDK's private _stream field as the original React sample does.
    const result = await AppStreamer.terminate(false);
    record('terminate(false)', result);
    setStatus('已请求断开；重新加载可重连');
  } catch (error) {
    record('disconnect rejected', error);
    setStatus('断开请求失败；重新加载页面', 'error');
  }
});

video.addEventListener('loadeddata', () => {
  mediaStatus.textContent = `已收到视频数据：${video.videoWidth} × ${video.videoHeight}`;
  record('video loadeddata', { width: video.videoWidth, height: video.videoHeight });
});
video.addEventListener('playing', () => {
  setStatus('浏览器正在播放视频', 'connected');
  mediaStatus.textContent = `${video.videoWidth} × ${video.videoHeight}；尚未验证画面内容或 log 同步`;
  record('video playing');
});
video.addEventListener('waiting', () => {
  mediaStatus.textContent = '浏览器等待后续视频数据';
  record('video waiting');
});
video.addEventListener('error', () => {
  record('video error', { code: video.error?.code, message: video.error?.message });
  setStatus('视频播放错误', 'error');
});

document.querySelector('#reload').addEventListener('click', () => location.reload());
host.addEventListener('input', updateConnectLabel);
port.addEventListener('input', updateConnectLabel);
if (params.has('host')) host.value = params.get('host');
if (params.has('port')) port.value = params.get('port');
updateConnectLabel();
record('page ready', { sdk: '5.6.0', target: 'Isaac Sim 5.1 / Kit 107.3.3', frameSync: false });
if (params.get('autoconnect') === '1') form.requestSubmit();
