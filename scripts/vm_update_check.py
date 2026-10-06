"""Dependency-free guest regression for connected solids with enclosed voids."""
import numpy as np
from backend.geometry import TriangleMesh, read_stl, write_binary_stl
from backend.materials import MATERIALS
from backend.schemas import Patch, SimulationRequest
from backend.solver import SimulationError, create_volume, solve


def box(size):
    vertices = np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],
                         [-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]],dtype=float)*np.array(size)/2
    faces = np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                      [3,7,6],[3,6,2],[0,4,7],[0,7,3],[1,2,6],[1,6,5]])
    return TriangleMesh(vertices,faces)


def check():
    outer,inner = box((60,20,20)),box((20,6,6))
    mesh = read_stl(write_binary_stl(np.concatenate([outer.triangles,inner.triangles[:,::-1]])))
    assert mesh.components == 2 and mesh.is_watertight
    volume = create_volume(mesh)
    centers = volume.nodes[volume.cells].mean(axis=1)
    assert volume.components == 1
    assert not np.any(np.all(np.abs(centers) < [8,2,2],axis=1))
    request = SimulationRequest(model_id='update-check',material_id='generic-petg',
        fixtures=[Patch(point=(-30,0,0),normal=(-1,0,0),radius=14)],
        load=Patch(point=(30,0,0),normal=(1,0,0),radius=14),
        magnitude=5,unit='N',direction=(1,0,0))
    result = solve(volume,mesh,MATERIALS['generic-petg'],request)
    assert result['checks']['relative_force_balance'] < 1e-8
    # Even an oppositely wound second solid outside the exterior must fail.
    inner.vertices[:,0] += 50.1
    separate = read_stl(write_binary_stl(np.concatenate([outer.triangles,inner.triangles[:,::-1]])))
    try:
        create_volume(separate)
    except SimulationError as error:
        assert 'separate pieces' in str(error)
    else:
        raise AssertionError('Disconnected solid was accepted')
    print('MAKERSIM_CAVITY_CHECKS_PASSED',flush=True)


if __name__ == '__main__':
    check()
