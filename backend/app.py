import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import numpy as np

from .geometry import TriangleMesh, read_stl
from .materials import MATERIALS
from .schemas import SimulationRequest
from .solver import SimulationError, VolumeMesh, create_volume, solve

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 20*1024*1024
MAX_MODELS = 6
MODEL_TTL_SECONDS = 2*60*60

app = FastAPI(title="MakerSim", docs_url=None, redoc_url=None)


@dataclass
class Model:
    mesh: TriangleMesh
    created: float
    volume: VolumeMesh | None = None


models: OrderedDict[str, Model] = OrderedDict()
solver_lock = asyncio.Lock()


@app.get('/api/health')
def health():
    return {"status": "ready", "solver": "linear-tetrahedral", "version": "0.1.0"}


@app.get('/api/materials')
def materials():
    return {"materials": list(MATERIALS.values())}


@app.get('/api/example')
def example():
    return FileResponse(ROOT/'backend/data/backpack-bracket.stl', media_type='application/octet-stream', filename='backpack-bracket.stl')


def store_model(data: bytes, unit: str) -> dict:
    try:
        mesh = read_stl(data, unit)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if len(mesh.faces) < 4:
        raise HTTPException(422, 'The STL has no usable solid surface.')
    now = time.monotonic()
    for key in list(models):
        if now-models[key].created > MODEL_TTL_SECONDS:
            del models[key]
    while len(models) >= MAX_MODELS:
        models.popitem(last=False)
    model_id = uuid.uuid4().hex
    models[model_id] = Model(mesh, now)
    closed = mesh.is_watertight
    return {"model_id": model_id, "dimensions_mm": mesh.extents.tolist(), "triangles": len(mesh.faces), "watertight": closed,
            "notes": [] if closed else ['This STL has an open surface. You can view it, but repair it before simulating.']}


@app.post('/api/models')
async def upload(file: UploadFile = File(...), unit: str = Form('mm')):
    if unit not in ('mm', 'inch'):
        raise HTTPException(422, 'Choose millimetres or inches for the STL units.')
    if not (file.filename or '').lower().endswith('.stl'):
        raise HTTPException(422, 'Choose an .stl file.')
    data = await file.read(MAX_BYTES+1)
    await file.close()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, 'This STL is over 20 MB. Export a lower-detail version.')
    return await asyncio.to_thread(store_model, data, unit)


def run_solver(model: Model, request: SimulationRequest, material: dict) -> dict:
    if not model.mesh.is_watertight:
        raise SimulationError('This STL has an open surface. Repair it in your slicer and export again before simulating.')
    if model.volume is None:
        model.volume = create_volume(model.mesh)
    result = solve(model.volume, model.mesh, material, request)
    result['material_provenance'] = material['provenance']
    return result


@app.post('/api/simulate')
async def simulate(request: SimulationRequest):
    model = models.get(request.model_id)
    if model is None or time.monotonic()-model.created > MODEL_TTL_SECONDS:
        raise HTTPException(404, 'This part has expired from the local workspace. Upload it again.')
    material = MATERIALS.get(request.material_id)
    if material is None or not material.get('supported'):
        raise HTTPException(422, 'This material needs usable stiffness data before it can be simulated.')
    # Bound memory and avoid concurrent sparse factorisations in this local app.
    if solver_lock.locked():
        raise HTTPException(429, 'A simulation is already running. Wait for it to finish.')
    async with solver_lock:
        try:
            return await asyncio.to_thread(run_solver, model, request, material)
        except (SimulationError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        except np.linalg.LinAlgError as error:
            raise HTTPException(422, 'The quick mesh could not be solved. Try a simpler solid part.') from error


# A built frontend makes the whole app available on a single local port.
if (ROOT/'frontend/dist').exists():
    app.mount('/', StaticFiles(directory=ROOT/'frontend/dist', html=True), name='workspace')
