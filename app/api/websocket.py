"""Validated per-connection WS protocol with bounded delivery and send deadlines."""
import asyncio
import anyio

from starlette.websockets import WebSocketDisconnect
from app.core.session_manager import SessionError
from app.schemas.ws import WS_CLIENT_ADAPTER, WS_SERVER_ADAPTER
from app.sync.outbox import ConnectionClosed


async def serve(websocket, service, sid):
    origin = websocket.headers.get('origin')
    own_origin = ('https://' if websocket.url.scheme == 'wss' else 'http://') + websocket.headers.get('host', '')
    if origin and origin not in service.allowed_origins and origin != own_origin:
        await websocket.close(code=1008, reason='ORIGIN_NOT_ALLOWED')
        return
    try:
        client = service.subscribe(sid)
    except SessionError as exc:
        await websocket.close(code=1013 if exc.code == 'WS_CAPACITY' else 1008, reason=exc.code)
        return
    tasks = []
    async def send():
        while True:
            value = await client.next()
            try:
                payload = WS_SERVER_ADAPTER.validate_python(value).model_dump_json()
            except ValueError as exc:
                raise ConnectionClosed(1011, 'SERVER_MESSAGE_INVALID') from exc
            try:
                await asyncio.wait_for(websocket.send_text(payload), timeout=1.)
            except TimeoutError as exc:
                raise ConnectionClosed(1013, 'SLOW_CONSUMER') from exc

    async def receive():
        last_seq = -1
        while True:
            event = await websocket.receive()
            if event['type'] == 'websocket.disconnect':
                raise WebSocketDisconnect(event.get('code', 1000))
            raw = event.get('text')
            if raw is None:
                raise ConnectionClosed(1003, 'TEXT_MESSAGES_REQUIRED')
            if len(raw.encode('utf-8')) > 65536:
                raise ConnectionClosed(1009, 'MESSAGE_TOO_LARGE')
            try:
                message = WS_CLIENT_ADAPTER.validate_json(raw)
                if message.session_id != sid or message.connection_id != client.connection_id or message.seq <= last_seq:
                    raise ValueError('Client envelope has stale identity or sequence')
            except ValueError as exc:
                raise ConnectionClosed(1008, 'INVALID_CLIENT_MESSAGE') from exc
            last_seq = message.seq
            if message.type == 'ping':
                client.offer('pong', message.data.model_dump(mode='json'))
            else:
                client.rate = message.data.hz
                client.next_health = 0.
                client.offer('telemetry_rate_changed', message.data.model_dump(mode='json'))

    try:
        await websocket.accept()
        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except ConnectionClosed as exc:
        try:
            await asyncio.wait_for(websocket.close(code=exc.code, reason=exc.reason), timeout=1.)
        except (TimeoutError, RuntimeError, WebSocketDisconnect):
            pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    except asyncio.CancelledError:
        pass
    finally:
        service.unsubscribe(sid, client)
        for task in tasks:
            task.cancel()
        # Starlette disconnect may cancel the outer AnyIO scope while the native
        # asyncio pumps are being joined. Finish their bounded cleanup first.
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
