"""Local browser control and reconnectable server-sent session events."""
import asyncio
import json
import os
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .local import AgentError, MAX_STL_BYTES
from .sessions import SessionSpec, session_store

router = APIRouter(prefix='/api/sessions')


def local_only():
    if os.getenv('MAKERSIM_HOSTED') == '1':
        raise HTTPException(404, 'Persistent agent sessions are available in the local workspace.')


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except AgentError as error:
        status = 404 if error.code in ('session_not_found', 'file_error') else 409 if error.code in (
            'session_paused', 'operation_active', 'budget_reached', 'stale_operation', 'stale_agent', 'agent_active', 'controls_unavailable') else 422
        raise HTTPException(status, str(error)) from error


class Control(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['pause', 'interrupt', 'continue', 'feedback', 'settings', 'recover']
    message: str = Field(default='', max_length=5000)
    settings: dict | None = None


class Fork(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sequence: int | None = Field(default=None, ge=1)
    objective: str | None = Field(default=None, min_length=1, max_length=5000)


class Connect(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=32, max_length=32)


@router.get('', dependencies=[Depends(local_only)])
async def list_sessions(request: Request):
    return {'sessions': call(session_store().list, request.state.workspace_id)}


@router.post('', dependencies=[Depends(local_only)])
async def create_session(spec: SessionSpec, request: Request):
    return call(session_store().create, spec.model_dump(mode='json'), request.state.workspace_id)


@router.post('/connect', dependencies=[Depends(local_only)])
async def connect_session(connect: Connect, request: Request):
    return call(session_store().connect, connect.session_id, request.state.workspace_id)


@router.get('/{session_id}', dependencies=[Depends(local_only)])
async def get_session(session_id: str, request: Request):
    return call(session_store().get, session_id, request.state.workspace_id)


@router.post('/{session_id}/control', dependencies=[Depends(local_only)])
async def control_session(session_id: str, control: Control, request: Request):
    return call(session_store().control, session_id, control.action, control.message,
                control.settings, request.state.workspace_id)


@router.post('/{session_id}/fork', dependencies=[Depends(local_only)])
async def fork_session(session_id: str, fork: Fork, request: Request):
    return call(session_store().fork, session_id, fork.sequence, fork.objective, request.state.workspace_id)


@router.post('/{session_id}/geometry', dependencies=[Depends(local_only)])
async def attach_geometry(session_id: str, request: Request, file: UploadFile = File(...),
                          unit: str = Form('mm'), revision: str = Form('R0')):
    # Check ownership before reading a potentially large upload.
    call(session_store().get, session_id, request.state.workspace_id)
    try:
        data = await file.read(MAX_STL_BYTES + 1)
    finally:
        await file.close()
    return call(session_store().attach_baseline, session_id, data, unit, revision, request.state.workspace_id)


@router.get('/{session_id}/artifacts/{name}', dependencies=[Depends(local_only)])
async def get_artifact(session_id: str, name: str, request: Request):
    path = call(session_store().artifact, session_id, name, request.state.workspace_id)
    return FileResponse(path, media_type='application/json' if path.suffix == '.json' else 'application/octet-stream')


@router.get('/{session_id}/events', dependencies=[Depends(local_only)])
async def get_events(session_id: str, request: Request, after: int = Query(default=0, ge=0),
                     limit: int = Query(default=100, ge=1, le=500)):
    return call(session_store().events, session_id, after, limit, request.state.workspace_id)


@router.get('/{session_id}/stream', dependencies=[Depends(local_only)])
async def stream_events(session_id: str, request: Request, after: int = Query(default=0, ge=0)):
    store = session_store()
    call(store.get, session_id, request.state.workspace_id)
    try:
        cursor = max(after, int(request.headers.get('last-event-id', '0')))
    except ValueError as error:
        raise HTTPException(422, 'Invalid event cursor.') from error

    async def stream():
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            page = store.events(session_id, cursor, 100, request.state.workspace_id)
            for event in page['events']:
                cursor = event['sequence']
                yield f'id: {cursor}\nevent: session\ndata: {json.dumps(event, allow_nan=False)}\n\n'
            if page['events']:
                idle = 0
                continue
            idle += 1
            if idle % 30 == 0:
                yield ': keepalive\n\n'
            await asyncio.sleep(.5)

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
