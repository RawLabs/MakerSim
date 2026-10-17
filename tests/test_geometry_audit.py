"""Regression checks for geometry correctness and bounded sampling."""
import numpy as np
import pytest

from backend.geometry import read_stl, voxelize, write_binary_stl
from backend.solver import create_volume
from tests.test_solver import box_mesh


def test_discarded_degenerate_facets_do_not_change_part_bounds_or_components():
    original = box_mesh()
    line = np.array([[[100., 100, 100], [101, 101, 101], [102, 102, 102]]])
    mesh = read_stl(write_binary_stl(np.concatenate([original.triangles, line])))
    assert mesh.components == 1
    np.testing.assert_allclose(mesh.bounds, original.bounds)
    np.testing.assert_allclose(mesh.source_center_mm, [0, 0, 0])
    assert len(create_volume(mesh).cells) == len(create_volume(original).cells)


def test_all_degenerate_facets_are_rejected_during_parsing():
    points = np.array([[0., 0, 0], [1, 2, 3], [2, 4, 6]])
    with pytest.raises(ValueError, match='3D surface'):
        read_stl(write_binary_stl(np.repeat(points[None], 4, axis=0)))


@pytest.mark.parametrize('pitch', [0, -1, float('nan'), float('inf')])
def test_voxel_size_must_be_finite_and_positive(pitch):
    with pytest.raises(ValueError, match='cell size'):
        voxelize(box_mesh(), pitch)


def test_voxel_grid_is_bounded_before_allocating(monkeypatch):
    monkeypatch.setattr('backend.geometry.MAX_VOXEL_CELLS', 100)
    with pytest.raises(ValueError, match='quick mesh limit'):
        voxelize(box_mesh(), 1)


def test_unrepresentably_small_voxels_are_rejected_without_overflow():
    with np.errstate(all='raise'):
        with pytest.raises(ValueError, match='quick mesh limit'):
            voxelize(box_mesh(), 1e-300)


def test_voxel_projection_work_is_bounded(monkeypatch):
    monkeypatch.setattr('backend.geometry.MAX_VOXEL_CHECKS', 1)
    with pytest.raises(ValueError, match='quick mesh limit'):
        voxelize(box_mesh(), 1)


@pytest.mark.parametrize('vertex', [(-30.1, 0, 0), (0, -6.1, 0), (0, 0, -6.1)])
def test_folded_closed_manifold_is_rejected(vertex):
    mesh = box_mesh()
    mesh.vertices[6] = vertex
    assert mesh.is_watertight and mesh.has_consistent_winding
    with pytest.raises(ValueError, match='intersecting or touching surfaces'):
        create_volume(mesh)


def test_solids_touching_at_one_vertex_are_separate_pieces():
    first, second = box_mesh(), box_mesh()
    second.vertices += [60, 12, 12]
    mesh = read_stl(write_binary_stl(np.concatenate([first.triangles, second.triangles])))
    assert mesh.is_watertight and mesh.has_consistent_winding
    assert mesh.components == 2
    with pytest.raises(ValueError, match='separate pieces'):
        create_volume(mesh)


@pytest.mark.parametrize('second, faces, expected', [
    ([[2, 2, 0], [3, 2, 0], [2, 3, 0]], [3, 4, 5], False),
    ([[2, 0, 0], [0, 0, 0], [0, -2, 0]], [1, 0, 3], False),
    ([[2, 0, 0], [0, 0, 0], [2, 2, 0]], [1, 0, 3], True),
    ([[0, 0, 0], [-2, 0, 0], [0, -2, 0]], [0, 3, 4], False),
    ([[0, 0, 0], [-1, 1, 0], [-2, 0, 0]], [0, 3, 4], False),
    ([[0, 0, 0], [1, .5, 0], [.5, 1, 0]], [0, 3, 4], True),
    ([[0, 0, 0], [1, 0, 0], [0, -2, 0]], [0, 3, 4], True),
    ([[0, 0, 0], [0, 0, 2], [-1, -1, 2]], [0, 3, 4], False),
    ([[0, 0, 0], [1, 1, -1], [1, 1, 1]], [0, 3, 4], True),
    ([[0, 0, 0], [1, 0, 0], [0, 0, 2]], [0, 3, 4], True),
    ([[0, 0, 0], [-2, 0, 0], [0, -2, 0]], [3, 4, 5], True),
    ([[2, 0, 0], [0, 0, 0], [0, 0, 2]], [1, 0, 3], False),
    ([[0, 2, 0], [2, 0, 0], [0, 0, 0]], [2, 1, 0], True),
])
def test_surface_contacts_allow_only_shared_mesh_topology(second, faces, expected):
    from backend.geometry import _triangle_pairs_cross
    first = np.array([[[0., 0, 0], [2, 0, 0], [0, 2, 0]]])
    second = np.array([second], dtype=float)
    # Rotation must not affect the geometric contact classification.
    rotation, _ = np.linalg.qr(np.random.default_rng(7).normal(size=(3, 3)))
    for transform in (np.eye(3), rotation):
        assert _triangle_pairs_cross(first @ transform, second @ transform,
                                     np.array([[0, 1, 2]]), np.array([faces]), 1e-8) == expected


def test_surface_intersection_work_is_bounded(monkeypatch):
    monkeypatch.setattr('backend.geometry.MAX_SURFACE_CHECKS', 1)
    with pytest.raises(ValueError, match='intersecting surfaces exceeds the quick mesh limit'):
        box_mesh().validate_single_solid()


def test_rotated_subdivided_surface_and_reversed_winding_are_valid():
    from tests.test_geometry_limits import dense_box
    rotation, _ = np.linalg.qr(np.random.default_rng(13).normal(size=(3, 3)))
    # Retain exact shared coordinates rather than rounding the binary export.
    mesh = read_stl(write_binary_stl(dense_box(3)))
    mesh.vertices = mesh.vertices @ rotation
    mesh.validate_single_solid()
    mesh.faces = mesh.faces[:, ::-1]
    mesh.validate_single_solid()


def test_voxel_ray_storage_is_bounded(monkeypatch):
    monkeypatch.setattr('backend.geometry.MAX_VOXEL_RAYS', 100)
    with pytest.raises(ValueError, match='quick mesh limit'):
        voxelize(box_mesh(), 1)


def test_nearly_coplanar_long_facets_meet_only_at_their_shared_vertex():
    from backend.geometry import _triangle_pairs_cross
    # Rounded STL fan facets: intersecting the incident edges numerically used
    # to move their known common endpoint by 4.5 microns and reject the part.
    first = np.array([[[-18.108292, -4.631748, 15.7713205],
                       [-15.550247, 2.716633, -12.0191775],
                       [-15.779472, 2.398762, -12.1161075]]])
    second = np.array([[[-18.108292, -4.631748, 15.7713205],
                        [-16.642715, 1.214192, -12.5760885],
                        [-16.843842, .94138, -12.7074215]]])
    assert not _triangle_pairs_cross(first, second, np.array([[0, 1, 2]]),
                                     np.array([[0, 3, 4]]), 8.255e-7)


def test_slender_triangle_contact_does_not_emit_numeric_warnings():
    from backend.geometry import _triangle_pairs_cross
    first = np.array([[[0., 0, 0], [1, 0, 0], [1, 1e-8, 0]]])
    second = np.array([[[0., 0, 0], [.5, 2.5e-9, 0], [0, 0, 1]]])
    with np.errstate(all='raise'):
        assert _triangle_pairs_cross(first, second, np.array([[0, 1, 2]]),
                                     np.array([[0, 3, 4]]), 1e-8)


def test_successful_validation_cache_detects_mutated_geometry(monkeypatch):
    import backend.geometry as geometry
    mesh = box_mesh()
    original = geometry._validate_surface_intersections
    calls = []
    def recorded(mesh):
        calls.append(True)
        return original(mesh)
    monkeypatch.setattr(geometry, '_validate_surface_intersections', recorded)
    mesh.validate_single_solid()
    mesh.validate_single_solid()
    assert len(calls) == 1
    mesh.vertices[6] = [-30.1, 0, 0]
    with pytest.raises(ValueError, match='intersecting or touching'):
        mesh.validate_single_solid()
    assert len(calls) == 2
    mesh.vertices[6] = [30, 6, 6]
    mesh.faces = mesh.faces[:, ::-1]
    mesh.validate_single_solid()
    assert len(calls) == 3
