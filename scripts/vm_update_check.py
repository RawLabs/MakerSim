"""Guest regressions using only hosted runtime dependencies."""
import numpy as np
import sys
from backend.geometry import TriangleMesh, read_stl, write_binary_stl
from backend.materials import MATERIALS
from backend.schemas import Patch, PrintSettings, SimulationRequest
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
    assert result['checks']['relative_moment_balance'] < 1e-8
    assert result['checks']['relative_energy_error'] < 1e-8
    assert result['mesh']['element_type'] == 'hexa8_wilson9'
    assert result['effective_material']['print_model']['model'] == 'directional_voxel_shell_core_v1'
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
    # A solid cantilever is independent of infill assumptions and exposes a
    # missing enhanced-mode kernel or a silently incompatible schema bundle.
    beam = box((80,10,10))
    material = {'family':'Reference','physics':{
        'E_xy_mpa':1000.,'E_z_mpa':1000.,'G_xy_mpa':400.,'nu':.25}}
    beam_request = SimulationRequest(model_id='update-beam-check',
        fixtures=[Patch(point=(-40,0,0),normal=(-1,0,0),radius=10)],
        load=Patch(point=(40,0,0),normal=(1,0,0),radius=10),
        magnitude=1,unit='N',direction=(0,0,-1),
        print_settings=PrintSettings(infill=100,line_width_mm=.5,layer_height_mm=.25))
    beam_result = solve(create_volume(beam),beam,material,beam_request)
    points,u = np.asarray(beam_result['positions']),np.asarray(beam_result['displacement'])
    reference = 80**3/(3*1000*(10*10**3/12))+80/((5/6)*400*100)
    assert abs(-u[np.isclose(points[:,0],40),2].mean()/reference-1) < .02
    assert beam_result['surface_quads']
    assert beam_result['checks']['relative_energy_error'] < 1e-8
    print('MAKERSIM_ENHANCED_BRICK_CHECKS_PASSED',flush=True)
    if '--preview' in sys.argv:
        from backend.app import Model, run_solver, materials
        assert materials()['limits'] == {'stl_bytes':64*1024*1024,'triangles':1000000}
        mesh = box((60,20,20))
        triangles = mesh.triangles
        for _ in range(8):
            a,b,c = triangles[:,0],triangles[:,1],triangles[:,2]
            ab,bc,ca = (a+b)/2,(b+c)/2,(c+a)/2
            triangles = np.concatenate([np.stack(p,axis=1) for p in
                                        [(a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca)]])
        data = write_binary_stl(triangles)
        assert len(data) > 20*1024*1024
        dense = read_stl(data)
        del data,triangles
        assert len(dense.faces) == 786432 and dense.is_watertight
        standard = run_solver(Model(dense,0),request,MATERIALS['generic-petg'])
        request.mesh_detail = 'fine'
        fine = run_solver(Model(dense,0),request,MATERIALS['generic-petg'])
        assert fine['mesh']['cell_mm'] < standard['mesh']['cell_mm']
        assert fine['mesh']['element_type'] == 'hexa8_wilson9'
        assert fine['mesh']['elements'] <= 8000
        assert fine['checks']['relative_force_balance'] < 1e-8
        print('MAKERSIM_DENSE_MESH_CHECKS_PASSED',flush=True)


if __name__ == '__main__':
    check()
