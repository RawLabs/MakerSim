from pathlib import Path

import numpy as np
import pytest

from backend.geometry import TriangleMesh, read_stl, voxelize, write_binary_stl
from backend.materials import MATERIALS
from backend.schemas import Patch, PrintSettings, SimulationRequest, force_newtons
from backend.solver import MAX_CELLS, SimulationError, check_supports, create_volume, patch_nodes, printed_elasticity, solve, strain_matrix


def box_mesh(extents=(60,12,12)):
    vertices = np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]],dtype=float)*np.array(extents)/2
    faces = np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[3,7,6],[3,6,2],[0,4,7],[0,7,3],[1,2,6],[1,6,5]])
    return TriangleMesh(vertices,faces)


def thin_channel_mesh():
    # A closed U-shaped part: two 6 mm posts joined by a 0.5 mm floor.
    profile = np.array([[0,0],[60,0],[60,6],[58,6],[58,.5],[2,.5],[2,6],[0,6]])
    sides = [np.column_stack([profile[:,0],np.full(8,y),profile[:,1]]) for y in (-6,6)]
    cap = np.array([[0,1,4],[0,4,5],[1,2,3],[1,3,4],[0,5,6],[0,6,7]])
    triangles = [*sides[0][cap], *sides[1][cap[:,::-1]]]
    for i in range(8):
        j = (i+1)%8
        triangles.extend([[sides[0][i],sides[1][i],sides[1][j]],
                          [sides[0][i],sides[1][j],sides[0][j]]])
    return read_stl(write_binary_stl(np.array(triangles)))


@pytest.fixture(scope='module')
def bracket():
    mesh=read_stl((Path(__file__).parents[1]/'backend/data/backpack-bracket.stl').read_bytes())
    return mesh,create_volume(mesh)


def bracket_request(magnitude=25):
    return SimulationRequest(model_id='test',fixtures=[
        Patch(point=(-25.5,-8,-7),normal=(0,-1,0),radius=7),
        Patch(point=(-25.5,-8,14),normal=(0,-1,0),radius=7)],
        load=Patch(point=(32.5,0,-17),normal=(0,0,1),radius=6),
        direction=(0,0,-1),magnitude=magnitude)


def test_tetrahedron_reproduces_affine_strain():
    points=np.array([[[0,0,0],[2,0,0],[0,3,0],[0,0,4]]],dtype=float)
    b,volume=strain_matrix(points)
    gradient=np.array([[.01,.02,.03],[.04,.05,.06],[.07,.08,.09]])
    displacement=points@gradient.T+np.array([4,7,-2])
    assert volume[0]==pytest.approx(4)
    assert b[0]@displacement.ravel()==pytest.approx([.01,.05,.09,.06,.14,.10])


def test_bracket_balance_and_linear_load_scaling(bracket):
    mesh,volume=bracket
    a=solve(volume,mesh,MATERIALS['generic-pla'],bracket_request(25))
    b=solve(volume,mesh,MATERIALS['generic-pla'],bracket_request(50))
    assert a['patches']['loaded_nodes']>=3
    assert a['checks']['relative_residual']<1e-8
    assert a['checks']['reaction_newtons']==pytest.approx([0,0,force_newtons(25,'lbf')],abs=1e-6)
    assert np.array(b['displacement'])==pytest.approx(np.array(a['displacement'])*2,abs=1e-9)
    assert np.array(b['stress'])==pytest.approx(np.array(a['stress'])*2,abs=1e-9)
    # Normalisation changes with the load, so this is not a failure scale.
    assert b['heatmap_scale_mpa']==pytest.approx(a['heatmap_scale_mpa']*2)


def test_axial_bar_matches_simple_elastic_reference():
    mesh=box_mesh();volume=create_volume(mesh,resolution=24)
    request=SimulationRequest(model_id='bar',material_id='generic-pla',
        fixtures=[Patch(point=(-30,0,0),normal=(-1,0,0),radius=10)],
        load=Patch(point=(30,0,0),normal=(1,0,0),radius=10),direction=(1,0,0),magnitude=100,unit='N',
        print_settings=PrintSettings(infill=100,orientation='z'))
    result=solve(volume,mesh,MATERIALS['generic-pla'],request)
    # Mesh cross section and length include centre-based voxel rounding.
    length=np.ptp(volume.nodes[:,0]);area=np.ptp(volume.nodes[:,1])*np.ptp(volume.nodes[:,2])
    expected=100*length/(3500*area)
    loaded=result['positions'];displacement=np.array(result['displacement'])
    tip=np.array(loaded)[:,0]>max(p[0] for p in loaded)-1e-5
    assert displacement[tip,0].mean()==pytest.approx(expected,rel=.12)


def test_orientation_and_print_settings_change_stiffness(bracket):
    mesh,_=bracket;material=MATERIALS['generic-petg']
    z,_=printed_elasticity(material,PrintSettings(infill=100,orientation='z'),mesh)
    x,_=printed_elasticity(material,PrintSettings(infill=100,orientation='x'),mesh)
    s_z=np.linalg.inv(z);s_x=np.linalg.inv(x)
    assert 1/s_z[2,2]==pytest.approx(1470)
    assert 1/s_z[0,0]==pytest.approx(2100)
    assert 1/s_x[0,0]==pytest.approx(1470)
    assert np.linalg.eigvalsh(z).min()>0
    low,_=printed_elasticity(material,PrintSettings(walls=1,top_layers=0,bottom_layers=0,infill=10),mesh)
    high,_=printed_elasticity(material,PrintSettings(walls=5,top_layers=8,bottom_layers=8,infill=80),mesh)
    assert high[0,0]>low[0,0]


def test_overlapping_patches_are_rejected(bracket):
    mesh,volume=bracket;request=bracket_request();request.load=request.fixtures[0]
    with pytest.raises(SimulationError,match='overlaps'):
        solve(volume,mesh,MATERIALS['generic-pla'],request)


def test_tiny_click_becomes_patch(bracket):
    _,volume=bracket
    ids,radius=patch_nodes(volume,Patch(point=(32.5,0,-17),normal=(0,0,1),radius=.01))
    assert len(ids)>=3
    assert radius>=volume.pitch


def test_voxelisation_keeps_holes(bracket):
    mesh,_=bracket
    occupancy,origin=voxelize(mesh,1)
    # Hole centres are empty, adjacent mounting material remains solid.
    for world,expected in [((-25.5,0,-7),False),((-18.5,0,-7),True),((32.5,0,-21),True)]:
        index=np.floor((np.array(world)-origin)).astype(int)
        assert bool(occupancy[tuple(index)]) is expected


def test_stl_units_and_invalid_inputs():
    mesh=box_mesh();data=write_binary_stl(mesh.triangles)
    assert read_stl(data,'inch').extents==pytest.approx(mesh.extents*25.4)
    assert read_stl(data).is_watertight
    with pytest.raises(ValueError):read_stl(b'not an STL')


@pytest.mark.parametrize('unit,magnitude,expected',[('N',10,10),('lbf',25,111.2055403815),('kg',1,9.80665),('stone',1,14*4.4482216152605)])
def test_maker_force_units(unit,magnitude,expected):
    assert force_newtons(magnitude,unit)==pytest.approx(expected)


def test_two_point_supports_leave_a_free_rotation():
    mesh=box_mesh()
    with pytest.raises(SimulationError,match='freely'):
        check_supports(mesh.vertices,np.array([0,1]))
    check_supports(mesh.vertices,np.array([0,1,3]))


def test_separate_solids_are_not_accidentally_joined_by_meshing():
    a=box_mesh();b=box_mesh();b.vertices+=np.array([60.1,0,0])
    mesh=TriangleMesh(np.vstack([a.vertices,b.vertices]),np.vstack([a.faces,b.faces+len(a.vertices)]))
    assert mesh.is_watertight
    with pytest.raises(SimulationError,match='separate pieces'):
        create_volume(mesh)


def test_thin_connected_part_is_refined_before_rejecting():
    from scipy import ndimage
    mesh = thin_channel_mesh()
    assert mesh.is_watertight and mesh.components == 1
    coarse_pitch = mesh.extents.max()/28
    occupancy, _ = voxelize(mesh, coarse_pitch)
    assert ndimage.label(occupancy)[1] == 2
    volume = create_volume(mesh)
    assert volume.components == 1
    assert volume.pitch < coarse_pitch
    assert len(volume.cells) <= MAX_CELLS
    # Refinement must recover the floor joining the posts.
    centers = volume.nodes[volume.cells].mean(axis=1)
    assert np.any((np.abs(centers[:,0]) < 5) & (centers[:,2] < -2))
    # Several held surface patches still solve on the recovered volume.
    request = SimulationRequest(model_id='thin-channel', fixtures=[
        Patch(point=(-30,y,0),normal=(-1,0,0),radius=2) for y in (-4,0,4)],
        load=Patch(point=(30,0,0),normal=(1,0,0),radius=2),
        magnitude=5,unit='N',direction=(0,0,-1))
    result = solve(volume,mesh,MATERIALS['generic-pla'],request)
    assert result['checks']['relative_residual'] < 1e-8
    assert result['checks']['reaction_newtons'] == pytest.approx([0,0,5],abs=1e-6)


def test_missing_thickness_is_refined():
    mesh = box_mesh((60,12,.5))
    occupancy, _ = voxelize(mesh,mesh.extents.max()/28)
    assert not occupancy.any()
    volume = create_volume(mesh)
    assert volume.components == 1
    assert 4 <= len(volume.cells) <= MAX_CELLS


def test_refinement_respects_budget_and_explains_resolution_limit(monkeypatch):
    monkeypatch.setattr('backend.solver.MAX_CELLS',100)
    with pytest.raises(SimulationError,match='thin walls or connections exceeds the quick mesh limit'):
        create_volume(thin_channel_mesh())


def test_unresolved_thickness_reports_mesh_limit():
    with pytest.raises(SimulationError,match='cannot resolve'):
        create_volume(box_mesh((60,12,.01)))
