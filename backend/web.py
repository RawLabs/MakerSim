"""Bound request bodies and isolate temporary browser workspaces."""
import asyncio
from collections import OrderedDict
from http.cookies import SimpleCookie, CookieError
import ipaddress
import re
import secrets
import time

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response

COOKIE = 'makersim_workspace'
MAX_REQUEST_BYTES = 20 * 1024 * 1024 + 64 * 1024


class WorkspaceMiddleware:
    def __init__(self, app, hosted=False):
        self.app = app
        self.hosted = hosted
        self.cookie_name = '__Host-' + COOKIE if hosted else COOKIE
        self.uploads = asyncio.Semaphore(2)
        self.rates = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path, method = scope['path'], scope['method']
        api = path.startswith('/api/')
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('cookie', ''))
        except CookieError:
            cookie = SimpleCookie()
        token = cookie[self.cookie_name].value if self.cookie_name in cookie else ''
        new_workspace = re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None
        if new_workspace:
            token = secrets.token_urlsafe(32)
        scope.setdefault('state', {})['workspace_id'] = token

        async def send_headers(message):
            if message['type'] == 'http.response.start':
                extra = [
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'same-origin'),
                    (b'permissions-policy', b'camera=(), microphone=(), geolocation=()'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"),
                ]
                if api:
                    extra.append((b'cache-control', b'no-store'))
                # The static landing page may only read readiness, never a workspace.
                if path == '/api/health' and method == 'GET' and headers.get('origin') == 'https://makersim.rawcastdigital.com':
                    extra.extend([(b'access-control-allow-origin', b'https://makersim.rawcastdigital.com'), (b'vary', b'Origin')])
                if api and new_workspace:
                    response = Response()
                    response.set_cookie(self.cookie_name, token, max_age=7200, httponly=True, secure=self.hosted, samesite='lax', path='/')
                    extra.append((b'set-cookie', response.headers['set-cookie'].encode('latin-1')))
                message = {**message, 'headers': list(message.get('headers', [])) + extra}
            await send(message)

        async def reject(status, message):
            response = JSONResponse({'detail': message}, status_code=status, headers={'Retry-After': '10'} if status == 429 else None)
            await response(scope, receive, send_headers)

        if method == 'POST' and api:
            origin = headers.get('origin')
            allowed = {f'https://{headers.get("host")}', f'http://{headers.get("host")}'}
            if origin and origin not in allowed:
                return await reject(403, 'Open MakerSim directly to upload a part or run a simulation.')
            try:
                length = int(headers.get('content-length', '0'))
            except ValueError:
                return await reject(400, 'The request has an invalid content length.')
            limit = MAX_REQUEST_BYTES if path == '/api/models' else 128 * 1024
            if length < 0 or length > limit:
                return await reject(413, 'This request is too large. STL files can be up to 20 MB.')
            if self.hosted:
                address = headers.get('cf-connecting-ip', scope.get('client', ('unknown',))[0])
                try:
                    address = str(ipaddress.ip_address(address))
                except ValueError:
                    address = scope.get('client', ('unknown',))[0]
                now = time.monotonic()
                key = (address, path)
                start, count = self.rates.get(key, (now, 0))
                if now - start >= 60:
                    start, count = now, 0
                if count >= (10 if path == '/api/models' else 20):
                    return await reject(429, 'This preview has a short request limit. Please try again in a minute.')
                self.rates[key] = (start, count + 1)
                self.rates.move_to_end(key)
                while len(self.rates) > 1024:
                    self.rates.popitem(last=False)
            read = 0
            original_receive = receive

            async def receive_limited():
                nonlocal read
                message = await original_receive()
                if message['type'] == 'http.request':
                    read += len(message.get('body', b''))
                    if read > limit:
                        raise HTTPException(413, 'This request is too large. STL files can be up to 20 MB.')
                return message

            receive = receive_limited

        if method == 'POST' and path == '/api/models':
            if self.uploads.locked():
                return await reject(429, 'The preview is reading other parts. Please try again shortly.')
            async with self.uploads:
                return await self.app(scope, receive, send_headers)
        return await self.app(scope, receive, send_headers)
