"""Bound incoming bytes before multipart spooling or JSON accumulation."""
from starlette.responses import JSONResponse


class PayloadTooLarge(Exception):
    pass


class UploadLimitMiddleware:
    def __init__(self, app, *, file_bytes):
        self.app, self.file_bytes = app, file_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        upload = scope.get('path', '').startswith('/api/v1/sessions/') and scope.get('path', '').endswith('/logs')
        limit = self.file_bytes + 512 * 1024 if upload else 65536
        headers = dict(scope.get('headers', []))
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            declared = limit + 1
        if declared > limit:
            return await JSONResponse(status_code=413, content={'error': {'code': 'REQUEST_TOO_LARGE',
                'message': 'Request body exceeds the configured limit', 'details': {}}})(scope, receive, send)
        used = 0
        async def limited_receive():
            nonlocal used
            message = await receive()
            if message['type'] == 'http.request':
                used += len(message.get('body', b''))
                if used > limit:
                    # The pinned multipart parser closes partial files on every
                    # BaseException; this retains 413 instead of parsing as 400.
                    raise PayloadTooLarge('Request body exceeds the configured limit')
            return message
        return await self.app(scope, limited_receive, send)
