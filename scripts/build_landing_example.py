"""Bake the bundled bracket's real solver field into a public display mesh."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from backend.geometry import read_stl
from backend.materials import MATERIALS
from backend.schemas import Patch, SimulationRequest
from backend.solver import create_volume, solve

ROOT = Path(__file__).resolve().parents[1]

def main():
    mesh = read_stl((ROOT/'backend/data/backpack-bracket.stl').read_bytes())
    request = SimulationRequest(model_id='landing-example', fixtures=[
        Patch(point=(-25.5,-8,-7), normal=(0,-1,0), radius=7),
        Patch(point=(-25.5,-8,14), normal=(0,-1,0), radius=7)],
        load=Patch(point=(32.5,0,-17), normal=(0,0,1), radius=6),
        direction=(0,0,-1), magnitude=25)
    result = solve(create_volume(mesh), mesh, MATERIALS[request.material_id], request)
    assert result['checks']['relative_force_balance'] < 1e-5
    triangles = mesh.triangles
    target = float(mesh.extents.max())/24
    for _ in range(7):
        refined, changed = [], False
        for triangle in triangles:
            lengths = np.sum((triangle-np.roll(triangle,-1,axis=0))**2,axis=1)
            edge = int(np.argmax(lengths))
            if lengths[edge] > target**2:
                a,b,c = triangle[edge],triangle[(edge+1)%3],triangle[(edge+2)%3]
                midpoint = (a+b)/2
                refined.extend([[a,midpoint,c],[midpoint,b,c]])
                changed = True
            else:
                refined.append(triangle)
        triangles = np.array(refined)
        if not changed:
            break
    points = triangles.reshape(-1,3)
    distances,neighbors = cKDTree(result['positions']).query(points,k=4)
    weights = 1/np.maximum(distances**2,(result['mesh']['cell_mm']*.15)**2)
    stress = np.sum(np.asarray(result['stress'])[neighbors]*weights,axis=1)/weights.sum(axis=1)
    relative = np.clip(stress/result['heatmap_scale_mpa'],0,1)
    palette = np.array([[int(value[index:index+2],16)/255 for index in (0,2,4)]
        for value in ('3769e8','2aa8f1','33c7b4','ead861','f69b47','ef5c52')])
    palette = np.where(palette <= .04045,palette/12.92,((palette+.055)/1.055)**2.4)
    level = relative*5
    lower = np.floor(level).astype(int)
    mix = (level-lower)[:,None]
    colors = palette[lower]*(1-mix)+palette[np.minimum(lower+1,5)]*mix
    assert np.isfinite(points).all() and np.isfinite(colors).all()
    destination = ROOT/'deploy/hero/part-data.js'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text('// Bundled bracket; real solver example; colours show relative stress.\n'
        + 'export const positions = new Float32Array('+json.dumps(np.round(points,5).ravel().tolist(),separators=(',',':'))+');\n'
        + 'export const colors = new Float32Array('+json.dumps(np.round(colors,5).ravel().tolist(),separators=(',',':'))+');\n')
    print(f'Baked {len(triangles)} display triangles; force-balance error {result["checks"]["relative_force_balance"]:.3g}.')

if __name__ == '__main__':
    main()
