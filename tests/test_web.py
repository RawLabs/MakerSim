import asyncio

import httpx
from fastapi import FastAPI, Request

from backend.web import WorkspaceMiddleware


def test_landing_page_can_only_read_health_cross_origin():
    app = FastAPI()
    app.add_middleware(WorkspaceMiddleware, hosted=True)
    @app.get('/api/health')
    async def health():
        return {'status': 'ready'}
    @app.get('/api/materials')
    async def materials():
        return {}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://makersim-playground.rawcastdigital.com') as client:
            origin = {'Origin': 'https://makersim.rawcastdigital.com'}
            health = await client.get('/api/health', headers=origin)
            assert health.headers['access-control-allow-origin'] == origin['Origin']
            assert 'access-control-allow-credentials' not in health.headers
            assert 'access-control-allow-origin' not in (await client.get('/api/materials', headers=origin)).headers
            assert 'access-control-allow-origin' not in (await client.get('/api/health', headers={'Origin':'https://other.example'})).headers
            assert (await client.post('/api/models', headers=origin)).status_code == 403
    asyncio.run(run())


def test_streamed_requests_are_bounded_without_content_length():
    app = FastAPI()
    app.add_middleware(WorkspaceMiddleware)
    @app.post('/api/simulate')
    async def read(request: Request):
        await request.body()
        return {'ok': True}
    async def chunks():
        for _ in range(3):
            yield b'x' * (64 * 1024)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response = await client.post('/api/simulate',content=chunks())
            assert response.status_code == 413
    asyncio.run(run())


def test_hosted_limits_cookies_and_private_response_headers():
    app = FastAPI()
    app.add_middleware(WorkspaceMiddleware,hosted=True)
    @app.post('/api/simulate')
    async def simulate(request: Request):
        return {'workspace':request.state.workspace_id}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
            response = await client.post('/api/simulate')
            cookie = response.headers['set-cookie']
            assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
            assert cookie.startswith('__Host-makersim_workspace=') and 'Path=/' in cookie and 'Domain=' not in cookie
            assert response.headers['cache-control'] == 'no-store'
            assert response.headers['x-content-type-options'] == 'nosniff'
            first = response.json()['workspace']
            for _ in range(19):
                response = await client.post('/api/simulate')
                assert response.status_code == 200 and response.json()['workspace'] == first
            response = await client.post('/api/simulate')
            assert response.status_code == 429 and 'Retry-After' in response.headers
    asyncio.run(run())


def test_only_two_upload_requests_can_be_processed_at_once():
    app = FastAPI()
    app.add_middleware(WorkspaceMiddleware)
    started = 0
    ready, release = asyncio.Event(), asyncio.Event()
    @app.post('/api/models')
    async def upload():
        nonlocal started
        started += 1
        if started == 2:
            ready.set()
        await release.wait()
        return {'ok': True}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            tasks = [asyncio.create_task(client.post('/api/models')) for _ in range(2)]
            try:
                await asyncio.wait_for(ready.wait(),2)
                assert (await client.post('/api/models')).status_code == 429
            finally:
                release.set()
            assert all(r.status_code == 200 for r in await asyncio.gather(*tasks))
    asyncio.run(run())
