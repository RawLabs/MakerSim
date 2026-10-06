"""Produce a real field for the no-WebGL viewer interaction test."""
import json
from pathlib import Path
import sys
from backend.geometry import read_stl
from backend.materials import MATERIALS
from backend.schemas import SimulationRequest
from backend.solver import create_volume,solve

mesh=read_stl((Path(__file__).resolve().parents[1]/'backend/data/backpack-bracket.stl').read_bytes())
request=SimulationRequest.model_validate(json.load(sys.stdin))
print(json.dumps(solve(create_volume(mesh),mesh,MATERIALS[request.material_id],request)))
