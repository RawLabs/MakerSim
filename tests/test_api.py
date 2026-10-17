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
    import backend.app as api
    monkeypatch.setattr(api,'models',api.OrderedDict())
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


@pytest.mark.parametrize('change',[{'magnitude':0},{'direction':[0,0,0]},{'fixtures':[]},{'unit':'pounds'},{'print_settings':{'walls':-1}},{'mesh_detail':'unbounded'}])
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
    assert client.post('/api/models',content=b'x',headers={'Content-Length':str(65*1024*1024)}).status_code == 413
    assert client.post('/api/simulate',content=b'x',headers={'Content-Length':str(129*1024)}).status_code == 413
    assert client.post('/api/models',files={'file':('part.stl',b'x')},headers={'Origin':'https://unrelated.example'}).status_code == 403


def test_large_upload_over_old_byte_and_triangle_limits():
    from tests.test_geometry_limits import dense_box
    from backend.schemas import Patch, SimulationRequest
    data = write_binary_stl(dense_box(8))
    assert len(data) > 20*1024*1024
    browser = InProcessClient()
    uploaded = browser.post('/api/models',files={'file':('dense.stl',data)})
    assert uploaded.status_code == 200,uploaded.text
    assert uploaded.json()['triangles'] == 786432
    assert uploaded.json()['simulation_ready']
    request = SimulationRequest(model_id=uploaded.json()['model_id'],
        fixtures=[Patch(point=(-30,0,0),normal=(-1,0,0),radius=14)],
        load=Patch(point=(30,0,0),normal=(1,0,0),radius=14),
        direction=(1,0,0),magnitude=5,unit='N')
    solved = browser.post('/api/simulate',json=request.model_dump())
    assert solved.status_code == 200,solved.text
    assert solved.json()['checks']['relative_force_balance'] < 1e-8


def test_fine_mesh_changes_resolution_and_cache_tracks_detail():
    browser = InProcessClient()
    data = (Path(__file__).parents[1]/'backend/data/backpack-bracket.stl').read_bytes()
    uploaded = browser.post('/api/models',files={'file':('bracket.stl',data)})
    request = bracket_request().model_dump()
    request['model_id'] = uploaded.json()['model_id']
    fields = []
    for detail in ('standard','fine','standard'):
        request['mesh_detail'] = detail
        response = browser.post('/api/simulate',json=request)
        assert response.status_code == 200,response.text
        field = response.json()
        assert field['mesh']['detail'] == detail
        assert field['mesh']['elements'] <= 6*field['mesh']['max_cells']
        assert field['checks']['relative_force_balance'] < 1e-8
        fields.append(field)
    assert fields[1]['mesh']['cell_mm'] < fields[0]['mesh']['cell_mm']
    assert fields[1]['mesh']['elements'] > fields[0]['mesh']['elements']
    assert fields[2]['mesh'] == fields[0]['mesh']
    assert fields[2]['max_displacement_mm'] == fields[0]['max_displacement_mm']


@pytest.mark.parametrize('worker_fails', [False, True])
def test_cancelled_request_keeps_solver_lock_until_worker_finishes(monkeypatch, worker_fails):
    import backend.app as api

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        lock = asyncio.Lock()
        monkeypatch.setattr(api, 'solver_lock', lock)

        async def slow_worker(function, *args):
            started.set()
            await release.wait()
            if worker_fails:
                raise ValueError('worker finished after the client left')
            return 42

        monkeypatch.setattr(asyncio, 'to_thread', slow_worker)
        task = asyncio.create_task(api.run_worker(lambda: None))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert lock.locked(), 'Cancelling the request cannot release a running worker'
        task.cancel()  # A second cancellation must not bypass the cleanup wait.
        await asyncio.sleep(0)
        assert lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not lock.locked()

    asyncio.run(run())
