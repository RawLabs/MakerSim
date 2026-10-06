from pathlib import Path

import asyncio
import httpx
import anyio.to_thread
import pytest

from backend.app import app
from backend.geometry import write_binary_stl
from tests.test_solver import box_mesh,bracket_request

class InProcessClient:
    def __init__(self):
        self.cookies = httpx.Cookies()

    def request(self,method,path,**kwargs):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://makersim.test',cookies=self.cookies) as client:
                response = await client.request(method,path,**kwargs)
                self.cookies.update(response.cookies)
                return response
        return asyncio.run(run())
    def post(self,path,**kwargs):return self.request('POST',path,**kwargs)
    def get(self,path,**kwargs):return self.request('GET',path,**kwargs)


client=InProcessClient()


@pytest.fixture(autouse=True)
def in_process_threads(monkeypatch):
    # The execution sandbox blocks cross-thread event-loop wakeup sockets.
    # Run the same application functions inline for in-process HTTP tests.
    async def inline(function,*args,**kwargs):
        return function(*args)
    monkeypatch.setattr(asyncio,'to_thread',inline)
    monkeypatch.setattr(anyio.to_thread,'run_sync',inline)


def test_upload_to_heatmap():
    data=(Path(__file__).parents[1]/'backend/data/backpack-bracket.stl').read_bytes()
    uploaded=client.post('/api/models',files={'file':('bracket.stl',data)},data={'unit':'mm'})
    assert uploaded.status_code==200
    metadata=uploaded.json();assert metadata['watertight']
    payload=bracket_request().model_dump();payload['model_id']=metadata['model_id']
    result=client.post('/api/simulate',json=payload)
    assert result.status_code==200,result.text
    field=result.json()
    assert len(field['positions'])==len(field['stress'])==len(field['displacement'])
    assert field['mesh']['elements']>0
    assert field['material_provenance']['E_xy']=='representative_baseline'


def test_open_stl_can_be_viewed_but_cannot_solve():
    mesh=box_mesh();data=write_binary_stl(mesh.triangles[:-1])
    result=client.post('/api/models',files={'file':('open.stl',data)})
    assert result.status_code==200
    assert not result.json()['watertight']
    payload=bracket_request().model_dump();payload['model_id']=result.json()['model_id']
    assert client.post('/api/simulate',json=payload).status_code==422


@pytest.mark.parametrize('change',[{'magnitude':0},{'direction':[0,0,0]},{'fixtures':[]},{'unit':'pounds'},{'print_settings':{'walls':-1}}])
def test_bad_simulation_inputs_are_actionable(change):
    payload=bracket_request().model_dump();payload.update(change)
    assert client.post('/api/simulate',json=payload).status_code==422


def test_unknown_or_expired_model():
    payload=bracket_request().model_dump()
    assert client.post('/api/simulate',json=payload).status_code==404


def test_material_provenance_and_unsupported_entries():
    catalog=client.get('/api/materials').json()['materials']
    pla=next(m for m in catalog if m['id']=='generic-pla')
    assert pla['physics']['E_z_mpa']<pla['physics']['E_xy_mpa']
    assert pla['provenance']['url']
    assert any(not m['supported'] for m in catalog)


def test_invalid_file_is_rejected():
    assert client.post('/api/models',files={'file':('test.txt',b'foo')}).status_code==422
    assert client.post('/api/models',files={'file':('bad.stl',b'foo')}).status_code==422


@pytest.mark.parametrize('number', ['NaN', 'Infinity', '1e309'])
def test_nonfinite_json_inputs_return_validation_errors(number):
    import json
    payload = bracket_request().model_dump()
    payload['direction'] = ['overflow', 0, 0]
    body = json.dumps(payload).replace('"overflow"', number)
    response = client.post('/api/simulate', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail'][0]['type'] == 'finite_number'


def test_closed_stl_with_inconsistent_winding_is_explained_and_blocked():
    triangles = box_mesh().triangles.copy()
    triangles[:6] = triangles[:6, ::-1]
    response = client.post('/api/models',files={'file':('mixed.stl',write_binary_stl(triangles))})
    assert response.status_code == 200
    metadata = response.json()
    assert metadata['watertight'] and not metadata['simulation_ready']
    assert 'triangle directions' in metadata['notes'][0]
    payload = bracket_request().model_dump()
    payload['model_id'] = metadata['model_id']
    result = client.post('/api/simulate',json=payload)
    assert result.status_code == 422
    assert 'triangle directions' in result.json()['detail']


def test_other_workspaces_cannot_solve_or_evict_a_part():
    alice, bob = InProcessClient(), InProcessClient()
    data = (Path(__file__).parents[1]/'backend/data/backpack-bracket.stl').read_bytes()
    uploaded = alice.post('/api/models',files={'file':('part.stl',data)})
    assert uploaded.status_code == 200
    payload = bracket_request().model_dump()
    payload['model_id'] = uploaded.json()['model_id']
    assert bob.post('/api/simulate',json=payload).status_code == 404
    for _ in range(4):
        assert bob.post('/api/models',files={'file':('part.stl',data)}).status_code == 200
    assert alice.post('/api/simulate',json=payload).status_code == 200


def test_request_limits_and_origin_are_checked_before_upload():
    assert client.post('/api/models',content=b'x',headers={'Content-Length':str(21*1024*1024)}).status_code == 413
    assert client.post('/api/simulate',content=b'x',headers={'Content-Length':str(129*1024)}).status_code == 413
    assert client.post('/api/models',files={'file':('part.stl',b'x')},headers={'Origin':'https://unrelated.example'}).status_code == 403
