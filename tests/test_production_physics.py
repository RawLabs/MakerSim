"""Analytical and conservation checks through the actual mesh/print/solve path."""

import copy

import numpy as np
import pytest

from backend.schemas import Patch, PrintSettings, SimulationRequest
from backend.print_model import cell_print_model
from backend.solver import SimulationError, check_printed_components, create_volume, patch_faces, patch_load, solve
from tests.test_physics_reference import ISOTROPIC
from tests.test_solver import box_mesh, cavity_mesh


def bar_request(length=80, radius=10, *, print_settings=None, direction=(0, 0, -1)):
    return SimulationRequest(model_id="production-reference", fixtures=[
        Patch(point=(-length/2, 0, 0), normal=(-1, 0, 0), radius=radius)],
        load=Patch(point=(length/2, 0, 0), normal=(1, 0, 0), radius=radius),
        direction=direction, magnitude=1, unit="N",
        print_settings=print_settings or PrintSettings(infill=100))


@pytest.mark.parametrize("dimensions", [(80, 10, 10), (61, 13, .7), (23, 19, 17)])
def test_aligned_rectangular_mesh_preserves_physical_dimensions_and_volume(dimensions):
    mesh = box_mesh(dimensions)
    volume = create_volume(mesh, resolution=28)
    assert np.ptp(volume.nodes, axis=0) == pytest.approx(dimensions)
    assert len(volume.cells)*np.prod(volume.cell_size) == pytest.approx(np.prod(dimensions))


def test_default_mesh_bending_is_within_two_percent_of_beam_reference():
    mesh = box_mesh((80, 10, 10))
    volume = create_volume(mesh)
    result = solve(volume, mesh, ISOTROPIC, bar_request())
    points, u = np.asarray(result["positions"]), np.asarray(result["displacement"])
    tip = points[:, 0] == 40
    # All end-face points have essentially the same transverse displacement;
    # the 3D clamp, Poisson warping and shear correction are approximate here.
    reference = 80**3/(3*1000*(10*10**3/12))+80/((5/6)*400*100)
    assert -u[tip, 2].mean() == pytest.approx(reference, rel=.02)
    assert result["mesh"]["element_type"] == "hexa8_wilson9"
    assert result["mesh"]["relative_volume_error"] == pytest.approx(0)
    assert result["checks"]["relative_moment_balance"] < 1e-8


def test_eccentric_end_load_torsion_converges_to_square_bar_reference():
    mesh = box_mesh((80, 10, 10))
    request = bar_request(direction=(0, 0, 1))
    request.load = Patch(point=(40, 3, 0), normal=(1, 0, 0), radius=2)
    # Saint-Venant square torsion constant, J=0.140577...*side^4, from
    # the rectangular Prandtl stress-function series (not the polar moment).
    # The twist gradient is measured in the middle half, away from end warping.
    odd = np.arange(1, 1000, 2)
    torsion_constant = 10**4/3*(1-192/np.pi**5*np.sum(np.tanh(odd*np.pi/2)/odd**5))
    errors = []
    for resolution in (24, 48):
        volume = create_volume(mesh, resolution=resolution)
        result = solve(volume, mesh, ISOTROPIC, request)
        points, u = np.asarray(result["positions"]), np.asarray(result["displacement"])
        sections = np.unique(points[:, 0])
        sections = sections[(sections >= -20)&(sections <= 20)]
        angles = []
        for x in sections:
            ids = np.isclose(points[:, 0], x)
            y, z = points[ids, 1:].T
            uy, uz = u[ids, 1:].T
            angles.append((y@uz-z@uy)/(y@y+z@z))
        measured_gradient = np.polyfit(sections, angles, 1)[0]
        # The coarse contact footprint changes; compare to its actual moment.
        torque = result["patches"]["load_centroid_mm"][1]*result["force_newtons"]
        assert torque > 0
        expected_gradient = torque/(400*torsion_constant)
        errors.append(abs(measured_gradient/expected_gradient-1))
        assert result["checks"]["relative_moment_balance"] < 1e-8
    assert errors[1] < errors[0]
    assert errors[1] < .03


@pytest.mark.parametrize("orientation,modulus", [("x", 500), ("z", 1000)])
def test_build_direction_changes_exact_axial_response(orientation, modulus):
    mesh = box_mesh((24, 6, 6))
    volume = create_volume(mesh, resolution=8)
    material = {"family": "Reference", "physics": {
        "E_xy_mpa": 1000., "E_z_mpa": 500., "G_xy_mpa": 400., "nu": 0.,
    }}
    request = bar_request(24, print_settings=PrintSettings(infill=100, orientation=orientation), direction=(1, 0, 0))
    result = solve(volume, mesh, material, request)
    points, u = np.asarray(result["positions"]), np.asarray(result["displacement"])
    expected = np.zeros_like(u)
    expected[:, 0] = (points[:, 0]+12)/(modulus*36)
    assert u == pytest.approx(expected, abs=1e-12)
    assert np.asarray(result["stress"]) == pytest.approx(np.full(len(points), 1/36), abs=1e-10)


def test_uniform_material_scaling_preserves_force_controlled_stress():
    mesh = box_mesh((40, 10, 10))
    volume = create_volume(mesh, resolution=20)
    request = bar_request(40, direction=(1, .3, -.4), print_settings=PrintSettings(infill=20))
    scaled = copy.deepcopy(ISOTROPIC)
    factor = 3.1
    for key in ("E_xy_mpa", "E_z_mpa", "G_xy_mpa"):
        scaled["physics"][key] *= factor
    a, b = solve(volume, mesh, ISOTROPIC, request), solve(volume, mesh, scaled, request)
    assert np.asarray(b["displacement"])*factor == pytest.approx(np.asarray(a["displacement"]), abs=1e-11)
    assert np.asarray(b["stress"]) == pytest.approx(np.asarray(a["stress"]), abs=1e-10)
    assert b["checks"]["strain_energy_n_mm"]*factor == pytest.approx(a["checks"]["strain_energy_n_mm"], rel=1e-9)


def test_zero_infill_void_nodes_are_excluded_without_an_artificial_floor():
    mesh = box_mesh((40, 20, 20))
    volume = create_volume(mesh, resolution=20)
    request = bar_request(40, 16, print_settings=PrintSettings(infill=0))
    result = solve(volume, mesh, ISOTROPIC, request)
    assert result["mesh"]["void_cells"] > 0
    assert result["mesh"]["active_nodes"] < len(volume.nodes)
    assert result["checks"]["reaction_newtons"] == pytest.approx([0, 0, 1], abs=1e-9)
    assert result["checks"]["relative_energy_error"] < 1e-9
    assert np.isfinite(result["displacement"]).all()


def test_pull_on_open_zero_infill_core_is_rejected():
    mesh = box_mesh((40, 20, 20))
    volume = create_volume(mesh, resolution=20)
    settings = PrintSettings(walls=1, top_layers=0, bottom_layers=0, infill=0, orientation="x")
    request = bar_request(40, 16, print_settings=settings)
    request.load.radius = 1
    with pytest.raises(SimulationError, match="empty printed"):
        solve(volume, mesh, ISOTROPIC, request)


def test_zero_infill_isolated_cavity_skin_requires_its_own_support():
    mesh = cavity_mesh(box_mesh((40, 40, 40)), [box_mesh((12, 12, 12))])
    volume = create_volume(mesh, resolution=20, max_cells=10000)
    request = bar_request(40, 28, print_settings=PrintSettings(infill=0))
    with pytest.raises(SimulationError, match="connected|held|secure|freely"):
        solve(volume, mesh, ISOTROPIC, request)


@pytest.mark.parametrize("offset", [(1, 1, 0), (1, 1, 1)])
def test_active_regions_that_share_only_an_edge_or_corner_are_not_welded(offset):
    volume = create_volume(box_mesh((8, 8, 8)), resolution=8)
    indices = np.rint(volume.nodes[volume.cells[:, 0]]-volume.nodes.min(axis=0)).astype(int)
    selected = np.all(indices == 0, axis=1) | np.all(indices == offset, axis=1)
    cells = volume.cells[selected]
    assert len(cells) == 2
    # Give both regions adequate holds so this exercises the accidental-weld
    # guard, rather than the separate unsupported-component guard above.
    with pytest.raises(SimulationError, match="edge or corner"):
        check_printed_components(volume, cells, np.unique(cells))


@pytest.mark.parametrize("offset,radius,expected_area,expected_y", [(0,10,72,0), (3,.1,45,1.8)])
def test_hollow_end_traction_uses_printed_face_area_even_when_void_corners_are_active(offset,radius,expected_area,expected_y):
    mesh = box_mesh((24,9,9))
    volume = create_volume(mesh,resolution=8)
    settings = PrintSettings(walls=1,top_layers=0,bottom_layers=0,infill=0,orientation="x")
    request = bar_request(24,10,print_settings=settings)
    request.load = Patch(point=(12,offset,0),normal=(1,0,0),radius=radius)
    active = cell_print_model(volume,settings).stiffness_factor > 0
    geometric_faces,_ = patch_faces(volume,request.load)
    # The empty center face shares all four corners with the surrounding ring:
    # checking loaded-node membership alone cannot distinguish this void face.
    assert np.isin(volume.boundary_faces[geometric_faces],np.unique(volume.cells[active])).all()
    assert np.any(~active[volume.boundary_cells[geometric_faces]])
    loaded,weights,_,area = patch_load(volume,request.load,active_cells=active)
    expected_weights = np.zeros(len(volume.nodes))
    for y in (-3,0,3):
        for z in (-3,0,3):
            if (y == z == 0) or (offset == 3 and y == -3):
                continue
            corners = ((volume.nodes[:,0] == 12)
                & np.isclose(np.abs(volume.nodes[:,1]-y),1.5)
                & np.isclose(np.abs(volume.nodes[:,2]-z),1.5))
            expected_weights[corners] += 9/4
    expected_weights /= expected_area
    assert area == pytest.approx(expected_area)
    assert weights == pytest.approx(expected_weights[loaded])
    assert weights @ volume.nodes[loaded] == pytest.approx([12,expected_y,0])
    result = solve(volume,mesh,ISOTROPIC,request)
    assert result['patches']['load_area_mm2'] == pytest.approx(expected_area)
    assert result['patches']['load_centroid_mm'] == pytest.approx([12,expected_y,0])
    assert result['checks']['reaction_newtons'] == pytest.approx([0,0,1],abs=1e-9)
    assert result['checks']['relative_moment_balance'] < 1e-9
    # A cached volume can serve different print settings without changing the
    # original STL boundary or retaining the previous material's void mask.
    solid_request = request.model_copy(deep=True)
    solid_request.print_settings.infill = 100
    solid = solve(volume,mesh,ISOTROPIC,solid_request)
    assert solid['patches']['load_area_mm2'] == pytest.approx(expected_area+9)
    assert patch_load(volume,request.load)[3] == pytest.approx(expected_area+9)


def test_hold_entirely_over_void_cannot_clamp_shared_neighbor_nodes():
    mesh = box_mesh((40,25,25))
    volume = create_volume(mesh,resolution=8)
    settings = PrintSettings(walls=1,top_layers=0,bottom_layers=0,infill=0,orientation="x")
    request = bar_request(40,.1,print_settings=settings)
    request.load = Patch(point=(17.5,-12.5,0),normal=(0,-1,0),radius=.1)
    active = cell_print_model(volume,settings).stiffness_factor > 0
    faces,_ = patch_faces(volume,request.fixtures[0])
    assert not active[volume.boundary_cells[faces]].any()
    assert np.isin(volume.boundary_faces[faces],np.unique(volume.cells[active])).any()
    with pytest.raises(SimulationError,match="empty printed"):
        solve(volume,mesh,ISOTROPIC,request)


@pytest.mark.parametrize("side,offset,radius,expected_area", [(9,3,.1,45), (15,0,15,144)])
def test_local_report_contact_and_probe_sampling_match_printed_faces(tmp_path,side,offset,radius,expected_area):
    from backend.geometry import write_binary_stl
    from backend.local import canonical_hash, run_test
    from backend.solver import patch_nodes

    mesh = box_mesh((24,side,side))
    stl = tmp_path/'hollow.stl'
    stl.write_bytes(write_binary_stl(mesh.triangles))
    volume = create_volume(mesh,resolution=8)
    settings = PrintSettings(walls=1,top_layers=0,bottom_layers=0,infill=0,orientation="x")
    request = bar_request(24,15,print_settings=settings)
    request.load = Patch(point=(12,offset,0),normal=(1,0,0),radius=radius)
    spec = {'coordinate_frame':'centered_mm','print_settings':settings.model_dump(),
        'fixtures':[patch.model_dump() for patch in request.fixtures],
        'load_cases':[{'name':'pull','load':request.load.model_dump(),
            'direction':request.direction,'magnitude':1,'unit':'N'}],
        'surface_probes':[{'name':'tip',**request.load.model_dump()}]}
    case = run_test(stl,spec,resolution=8,include_fields=True)['load_cases'][0]
    active = cell_print_model(volume,settings).stiffness_factor > 0
    loaded,weights,_,_ = patch_load(volume,request.load,active_cells=active)
    held,_ = patch_nodes(volume,request.fixtures[0],active_cells=active)
    load_positions = volume.nodes[loaded]
    order = np.lexsort(np.round(load_positions,9).T[::-1])
    assert case['patches']['load_area_mm2'] == pytest.approx(expected_area)
    assert case['patches']['load_centroid_mm'] == pytest.approx(weights @ load_positions)
    assert case['patches']['load_sampling']['weights_sha256'] == canonical_hash(np.round(weights[order],12).tolist())
    assert case['patches']['holds'][0]['sampled_nodes'] == len(held)
    assert case['patches']['load_sampling']['sampled_nodes'] == len(loaded)
    assert case['surface_probes'][0]['sampled_nodes'] == len(loaded)
    u = np.asarray(case['fields']['displacement_mm'])
    indices = np.flatnonzero(np.isin(volume.surface,loaded))
    assert case['surface_probes'][0]['mean_displacement_vector_mm'] == pytest.approx(u[indices].mean(axis=0))
