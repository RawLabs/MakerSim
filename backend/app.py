import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import time
import uuid
from threading import Lock
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
import numpy as np

from .geometry import TriangleMesh, read_stl
from .limits import MAX_STL_BYTES, MAX_TRIANGLES
from .materials import MATERIALS
from .schemas import SimulationRequest
from .solver import SOLVER_VERSION, SimulationError, VolumeMesh, create_volume, solve
from .web import WorkspaceMiddleware

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = MAX_STL_BYTES
HOSTED = os.getenv('MAKERSIM_HOSTED') == '1'
if not HOSTED:
    from .session_api import router as session_router
    from .sessions import session_store
MAX_MODELS = 12 if HOSTED else 6
MODEL_TTL_SECONDS = 15*60 if HOSTED else 2*60*60
MAX_STORED_MESH_BYTES = 192 * 1024 * 1024
MESH_DETAILS = {'standard': (28, 4800), 'fine': (40, 8000)}

@asynccontextmanager
async def lifespan(app):
    if not HOSTED:
        session_store().viewer_started()
    async def expire_parts():
        while True:
            await asyncio.sleep(60)
            with models_lock:
                now = time.monotonic()
                for key in list(models):
                    if now - models[key].created > MODEL_TTL_SECONDS:
                        del models[key]
    cleanup = asyncio.create_task(expire_parts())
    try:
        yield
    finally:
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup


app = FastAPI(title="MakerSim", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(WorkspaceMiddleware, hosted=HOSTED)
if not HOSTED:
    app.include_router(session_router)
if HOSTED:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv('MAKERSIM_ALLOWED_HOSTS', 'makersim.rawcastdigital.com,127.0.0.1,localhost').split(','))


@dataclass
class Model:
    mesh: TriangleMesh
    created: float
    volume: VolumeMesh | None = None
    owner: str | None = None
    volume_detail: str | None = None


models: OrderedDict[str, Model] = OrderedDict()
models_lock = Lock()
solver_lock = asyncio.Lock()


async def run_worker(function, *args):
    """Keep the admission lock until CPU work finishes, even on cancellation."""
    async with solver_lock:
        worker = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Cancelling to_thread's await does not stop the underlying thread.
            # Releasing the lock early would allow two sparse factorizations.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()  # Retrieve a late worker error after cancellation.
            raise


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Pydantic error inputs may contain NaN/Infinity, which JSON cannot encode.
    details = [{key: item[key] for key in ('loc', 'msg', 'type')} for item in error.errors()]
    return JSONResponse(status_code=422, content={'detail': details})


@app.get('/api/health')
def health():
    return {"status": "ready", "solver": "enhanced-brick", "solver_version": SOLVER_VERSION,
            "version": "0.1.0", "mode": "hosted" if HOSTED else "local"}


@app.get('/api/materials')
def materials():
    return {"materials": list(MATERIALS.values()), "workspace": {"hosted": HOSTED, "retention_minutes": MODEL_TTL_SECONDS // 60},
            "limits": {"stl_bytes": MAX_BYTES, "triangles": MAX_TRIANGLES}}


@app.get('/api/example')
def example():
    return FileResponse(ROOT/'backend/data/backpack-bracket.stl', media_type='application/octet-stream', filename='backpack-bracket.stl')


def store_model(data: bytes, unit: str, owner: str | None = None) -> dict:
    try:
        mesh = read_stl(data, unit)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if len(mesh.faces) < 4:
        raise HTTPException(422, 'The STL has no usable solid surface.')
    now = time.monotonic()
    with models_lock:
        for key in list(models):
            if now-models[key].created > MODEL_TTL_SECONDS:
                del models[key]
        owned = [key for key, model in models.items() if model.owner == owner]
        while len(owned) >= 2:
            del models[owned.pop(0)]
        stored_bytes = sum(m.mesh.vertices.nbytes+m.mesh.faces.nbytes for m in models.values())
        if len(models) >= MAX_MODELS or stored_bytes+mesh.vertices.nbytes+mesh.faces.nbytes > MAX_STORED_MESH_BYTES:
            raise HTTPException(429, 'The preview workspace is full. Please try again in a few minutes.')
        model_id = uuid.uuid4().hex
        models[model_id] = Model(mesh, now, owner=owner)
    closed = mesh.is_watertight
    problem = None
    if not closed:
        problem = 'This STL has an open surface. You can view it, but repair it before simulating.'
    elif not mesh.has_consistent_winding:
        problem = 'This STL has inconsistent triangle directions. Repair its normals in your slicer before simulating.'
    return {"model_id": model_id, "dimensions_mm": mesh.extents.tolist(), "triangles": len(mesh.faces), "watertight": closed,
            "simulation_ready": problem is None, "notes": [problem] if problem else []}


@app.post('/api/models')
async def upload(request: Request, file: UploadFile = File(...), unit: str = Form('mm')):
    if unit not in ('mm', 'inch'):
        raise HTTPException(422, 'Choose millimetres or inches for the STL units.')
    if not (file.filename or '').lower().endswith('.stl'):
        raise HTTPException(422, 'Choose an .stl file.')
    data = await file.read(MAX_BYTES+1)
    await file.close()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, 'This STL is over 64 MB. Use a binary STL or export at a lower detail level.')
    if HOSTED:
        # Dense parsing and sparse factorisation share the guest's memory cap.
        if solver_lock.locked():
            raise HTTPException(429, 'The preview is processing another part. Please try again shortly.')
        return await run_worker(store_model, data, unit, request.state.workspace_id)
    return await asyncio.to_thread(store_model, data, unit, request.state.workspace_id)


def run_solver(model: Model, request: SimulationRequest, material: dict) -> dict:
    if not model.mesh.is_watertight:
        raise SimulationError('This STL has an open surface. Repair it in your slicer and export again before simulating.')
    resolution, max_cells = MESH_DETAILS[request.mesh_detail]
    if HOSTED:
        # Avoid retaining a full solver mesh for every public visitor.
        volume = create_volume(model.mesh, resolution, max_cells=max_cells)
    else:
        if model.volume is None or model.volume_detail != request.mesh_detail:
            model.volume = None
            model.volume = create_volume(model.mesh, resolution, max_cells=max_cells)
            model.volume_detail = request.mesh_detail
        volume = model.volume
    result = solve(volume, model.mesh, material, request)
    coarsened = volume.pitch > float(model.mesh.extents.max())/resolution * 1.001
    result['mesh'].update(detail=request.mesh_detail, requested_resolution=resolution,
                          max_cells=max_cells, coarsened_for_budget=coarsened)
    if coarsened:
        result['notes'].append('Mesh cells were enlarged to stay within the preview memory budget. The actual cell size is shown below.')
    result['material_provenance'] = material['provenance']
    return result


@app.post('/api/simulate')
async def simulate(request: SimulationRequest, http_request: Request):
    with models_lock:
        model = models.get(request.model_id)
    if model is None or model.owner != http_request.state.workspace_id or time.monotonic()-model.created > MODEL_TTL_SECONDS:
        raise HTTPException(404, 'This part has expired from the local workspace. Upload it again.')
    material = MATERIALS.get(request.material_id)
    if material is None or not material.get('supported'):
        raise HTTPException(422, 'This material needs usable stiffness data before it can be simulated.')
    # Bound memory and avoid concurrent sparse factorisations in this local app.
    if solver_lock.locked():
        raise HTTPException(429, 'A simulation is already running. Wait for it to finish.')
    try:
        return await run_worker(run_solver, model, request, material)
    except (SimulationError, ValueError) as error:
        raise HTTPException(422, str(error)) from error
    except np.linalg.LinAlgError as error:
        raise HTTPException(422, 'The quick mesh could not be solved. Try a simpler solid part.') from error


# A built frontend makes the whole app available on a single local port.
if (ROOT/'frontend/dist').exists():
    app.mount('/', StaticFiles(directory=ROOT/'frontend/dist', html=True), name='workspace')
