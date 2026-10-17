"""Independent elasticity identities and boundary-condition reference cases.

These tests validate the discrete equations, not the empirical print model.
The axial reference uses Maxwell--Betti reciprocity: the area-mean end
displacement is F L / (E A) for a zero-Poisson-ratio prismatic bar, even
when the applied end traction is not spatially uniform.
"""

import numpy as np
import pytest

from backend.materials import MATERIALS
from backend.hexa import NATURAL_CORNERS, incompatible_hex
from backend.schemas import Patch, PrintSettings, SimulationRequest
from backend.solver import create_volume, patch_faces, patch_nodes, printed_elasticity, solve
from tests.test_solver import box_mesh


ISOTROPIC = {
    "family": "Analytical reference",
    "physics": {"E_xy_mpa": 1000., "E_z_mpa": 1000., "G_xy_mpa": 400., "nu": .25},
}


@pytest.mark.parametrize("sign", [-1, 1])
def test_end_hold_selects_one_exposed_face_without_clamping_an_extra_layer(sign):
    volume = create_volume(box_mesh((60, 12, 12)), resolution=20)
    ids, _ = patch_nodes(volume, Patch(point=(sign*30, 0, 0), normal=(sign, 0, 0), radius=10))
    extreme = volume.nodes[:, 0].min() if sign < 0 else volume.nodes[:, 0].max()
    assert np.allclose(volume.nodes[ids, 0], extreme)
    assert len(ids) == np.count_nonzero(np.isclose(volume.nodes[:, 0], extreme))


def test_top_patch_on_underresolved_plate_does_not_select_its_bottom_surface():
    volume = create_volume(box_mesh((60, 12, .5)))
    ids, _ = patch_nodes(volume, Patch(point=(0, 0, .25), normal=(0, 0, 1), radius=4))
    assert len(np.unique(volume.nodes[:, 2])) == 2
    assert np.allclose(volume.nodes[ids, 2], volume.nodes[:, 2].max())


def test_sloped_surface_patch_uses_both_outward_staircase_face_directions():
    mesh = box_mesh((60, 12, 12))
    rotation = np.array([[1, -1, 0], [1, 1, 0], [0, 0, np.sqrt(2)]])/np.sqrt(2)
    mesh.vertices = mesh.vertices @ rotation.T
    volume = create_volume(mesh)
    patch = Patch(point=rotation @ [30, 0, 0], normal=rotation @ [1, 0, 0], radius=3)
    faces, _ = patch_faces(volume, patch)
    directions = set(map(tuple, volume.boundary_normals[faces]))
    assert directions == {(1, 0, 0), (0, 1, 0)}


def test_engineering_shear_has_correct_stress_and_elastic_energy():
    d, _ = printed_elasticity(ISOTROPIC, PrintSettings(infill=100), box_mesh())
    kernel = incompatible_hex([2., 3., 4.], d)
    points = NATURAL_CORNERS*kernel.cell_size/2
    gamma = .013
    displacement = np.zeros_like(points)
    displacement[:, 0] = gamma*points[:, 1]
    strain = kernel.gauss_b @ displacement.ravel()
    stress = strain @ d.T
    assert strain == pytest.approx(np.tile([0, 0, 0, gamma, 0, 0], (8, 1)), abs=1e-14)
    assert stress == pytest.approx(np.tile([0, 0, 0, 400*gamma, 0, 0], (8, 1)), abs=1e-12)
    energy = .5*np.einsum('qi,qi,q->', strain, stress, kernel.quadrature_weights)
    assert energy == pytest.approx(.5*400*gamma**2*np.prod(kernel.cell_size), rel=1e-12)


@pytest.mark.parametrize("material_id", ["stratasys-antero-800na", "hp-mjf-pa12-gb"])
@pytest.mark.parametrize("axis,orientation", list(enumerate(("x", "y", "z"))))
def test_recorded_build_modulus_is_preserved_when_larger_than_in_plane_modulus(material_id, axis, orientation):
    material = MATERIALS[material_id]
    expected = material["physics"]["E_z_mpa"]
    assert expected > material["physics"]["E_xy_mpa"]
    d, properties = printed_elasticity(material, PrintSettings(infill=100, orientation=orientation), box_mesh())
    assert 1/np.linalg.inv(d)[axis, axis] == pytest.approx(expected)
    assert properties["modulus_build_mpa"] == pytest.approx(expected)
    assert np.linalg.eigvalsh(d).min() > 0


def test_poisson_ratio_is_not_silently_reduced_to_change_compressibility():
    material = {"family": "Analytical reference", "physics": {
        "E_xy_mpa": 1000., "E_z_mpa": 1000., "G_xy_mpa": 1000/(2*1.48), "nu": .48,
    }}
    d, _ = printed_elasticity(material, PrintSettings(infill=100), box_mesh())
    hydrostatic_strain = np.array([.0001, .0001, .0001, 0, 0, 0])
    # For isotropic elasticity, sigma = E*epsilon/(1-2*nu) in each direction.
    assert d @ hydrostatic_strain == pytest.approx([2.5, 2.5, 2.5, 0, 0, 0])


def _end_integration_weights(volume):
    """Tensor-product trapezoidal integration on a rectangular end face."""
    tip = np.flatnonzero(np.isclose(volume.nodes[:, 0], volume.nodes[:, 0].max()))
    weights = np.ones(len(tip))
    for axis in (1, 2):
        coordinates, inverse = np.unique(volume.nodes[tip, axis], return_inverse=True)
        intervals = np.diff(coordinates)
        axis_weights = (np.r_[0, intervals]+np.r_[intervals, 0])/2
        weights *= axis_weights[inverse]
    return np.bincount(tip, weights=weights, minlength=len(volume.nodes))


def test_boundary_quads_and_triangles_are_outward_and_cover_the_box():
    mesh = box_mesh((24, 6, 6))
    volume = create_volume(mesh, resolution=8)
    quads = volume.nodes[volume.boundary_faces]
    quad_vectors = np.cross(quads[:, 1]-quads[:, 0], quads[:, 3]-quads[:, 0])
    quad_areas = np.linalg.norm(quad_vectors, axis=1)
    assert np.einsum('ij,ij->i', quad_vectors, volume.boundary_normals).min() > 0
    assert quad_areas == pytest.approx(volume.boundary_areas)
    assert quad_areas.sum() == pytest.approx(mesh.area)
    triangles = volume.nodes[volume.boundary_triangles]
    vectors = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    assert np.einsum('ij,ij->i', vectors, np.repeat(volume.boundary_normals, 2, axis=0)).min() > 0
    assert np.linalg.norm(vectors, axis=1).reshape(-1, 2).sum(axis=1)/2 == pytest.approx(quad_areas)
    edges = np.sort(volume.boundary_triangles[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    assert np.all(np.unique(edges, axis=0, return_counts=True)[1] == 2)
    assert np.unique(volume.boundary_triangles) == pytest.approx(volume.surface)


def test_axial_bar_area_mean_matches_exact_reciprocity_solution():
    mesh = box_mesh((24, 6, 6))
    volume = create_volume(mesh, resolution=8)
    material = {"family": "Analytical reference", "physics": {
        "E_xy_mpa": 1000., "E_z_mpa": 1000., "G_xy_mpa": 500., "nu": 0.,
    }}
    request = SimulationRequest(model_id="reference", fixtures=[
        Patch(point=(-12, 0, 0), normal=(-1, 0, 0), radius=10)],
        load=Patch(point=(12, 0, 0), normal=(1, 0, 0), radius=10),
        direction=(1, 0, 0), magnitude=10, unit="N", print_settings=PrintSettings(infill=100))
    result = solve(volume, mesh, material, request)
    weights = _end_integration_weights(volume)[volume.surface]
    average_tip = weights @ np.asarray(result["displacement"])[:, 0] / weights.sum()
    length, width, height = np.ptp(volume.nodes, axis=0)
    assert weights.sum() == pytest.approx(width*height)
    assert average_tip == pytest.approx(10*length/(1000*width*height), rel=1e-10)
    assert result["checks"]["reaction_newtons"] == pytest.approx([-10, 0, 0], abs=1e-10)
    # Uniform traction must reproduce the complete affine field, not only its
    # area mean. This also tests the compatible bilinear-face nodal loads.
    expected = np.zeros_like(result["displacement"])
    expected[:, 0] = 10*(np.asarray(result["positions"])[:, 0]-volume.nodes[:, 0].min())/(1000*width*height)
    assert np.asarray(result["displacement"]) == pytest.approx(expected, abs=1e-12)
    checks = result["checks"]
    assert checks["relative_moment_balance"] < 1e-10
    assert checks["strain_energy_n_mm"] > 0
    assert 2*checks["strain_energy_n_mm"] == pytest.approx(checks["external_work_n_mm"], rel=1e-10)
    assert checks["strain_energy_n_mm"] == pytest.approx(10**2*length/(2*1000*width*height), rel=1e-10)


def test_cantilever_refinement_approaches_beam_theory_without_calibration():
    # An aspect-ratio-eight rectangular cantilever is close to beam theory,
    # with residual 3D clamp effects. Use a Timoshenko shear correction with
    # k=5/6. This is an approximate continuum benchmark, unlike the exact
    # axial and strain identities above. No print heuristics are calibrated.
    # Beam equations: MIT 2.080J Structural Mechanics, Lecture 8 (2013).
    # The element remains approximate; the kernel tests separately check
    # exact aligned pure bending and oblique-field refinement.
    mesh = box_mesh((80, 10, 10))
    request = SimulationRequest(model_id="beam-reference", fixtures=[
        Patch(point=(-40, 0, 0), normal=(-1, 0, 0), radius=10)],
        load=Patch(point=(40, 0, 0), normal=(1, 0, 0), radius=10),
        direction=(0, 0, -1), magnitude=1, unit="N", print_settings=PrintSettings(infill=100))
    errors = []
    for resolution in (16, 32, 64):
        volume = create_volume(mesh, resolution=resolution, max_cells=6000, strict_pitch=True)
        length, width, height = np.ptp(volume.nodes, axis=0)
        assert [length, width, height] == pytest.approx([80, 10, 10])
        inertia = width*height**3/12
        reference = length**3/(3*1000*inertia) + length/((5/6)*400*width*height)
        result = solve(volume, mesh, ISOTROPIC, request)
        weights = _end_integration_weights(volume)[volume.surface]
        tip = -weights @ np.asarray(result["displacement"])[:, 2]/weights.sum()
        errors.append(abs(tip/reference-1))
        assert result["checks"]["relative_moment_balance"] < 1e-8
    assert errors[2] < errors[1] < errors[0]
    assert max(errors) < .02
