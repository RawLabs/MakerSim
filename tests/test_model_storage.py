from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException

import backend.app as api
from backend.geometry import write_binary_stl
from tests.test_solver import box_mesh


def test_simultaneous_uploads_cannot_exceed_global_budget(monkeypatch):
    monkeypatch.setattr(api, 'models', api.OrderedDict())
    data = write_binary_stl(box_mesh().triangles)
    for i in range(api.MAX_MODELS-1):
        api.store_model(data, 'mm', f'owner-{i}')
    original = api.read_stl
    ready = Barrier(2)
    def read_together(*args):
        mesh = original(*args)
        ready.wait(timeout=5)
        return mesh
    monkeypatch.setattr(api, 'read_stl', read_together)
    def upload(owner):
        try:
            api.store_model(data, 'mm', owner)
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(upload, ['new-a','new-b']))
    assert sorted(statuses) == [200,429]
    assert len(api.models) == api.MAX_MODELS


def test_cleanup_and_owner_replacement_preserve_other_workspaces(monkeypatch):
    monkeypatch.setattr(api, 'models', api.OrderedDict())
    data = write_binary_stl(box_mesh().triangles)
    first = api.store_model(data, 'mm', 'alice')['model_id']
    for _ in range(5):
        api.store_model(data, 'mm', 'bob')
    assert first in api.models
    assert len(api.models) == 3
    api.models[first].created -= api.MODEL_TTL_SECONDS + 1
    api.store_model(data, 'mm', 'bob')
    assert first not in api.models
