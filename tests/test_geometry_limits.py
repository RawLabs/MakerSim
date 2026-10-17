import numpy as np
import pytest

from backend.geometry import read_stl, voxelize, write_binary_stl
from backend.local import load_mesh
from tests.test_solver import box_mesh


def dense_box(levels=7):
    triangles = box_mesh((60,20,20)).triangles
    for _ in range(levels):
        a,b,c = triangles[:,0],triangles[:,1],triangles[:,2]
        ab,bc,ca = (a+b)/2,(b+c)/2,(c+a)/2
        triangles = np.concatenate([np.stack(points,axis=1) for points in
                                    [(a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca)]])
    return triangles


def ascii_stl(triangles):
    return ('solid test\n'+'\n'.join('vertex '+' '.join(map(str,p))
                                   for p in triangles.reshape(-1,3))+'\nendsolid test').encode()


@pytest.mark.parametrize('encode',[write_binary_stl,ascii_stl])
def test_triangle_limit_is_inclusive_and_explained(monkeypatch,encode):
    monkeypatch.setattr('backend.geometry.MAX_TRIANGLES',12)
    triangles = box_mesh().triangles
    assert len(read_stl(encode(triangles)).faces) == 12
    with pytest.raises(ValueError,match='at most 12 triangles'):
        read_stl(encode(np.concatenate([triangles,triangles[:1]])))


def test_dense_export_preserves_the_same_solid_and_voxels(tmp_path):
    triangles = dense_box()
    path = tmp_path/'dense.stl'
    path.write_bytes(write_binary_stl(triangles))
    mesh, _ = load_mesh(path,'mm')
    assert len(mesh.faces) == 196608
    assert mesh.is_watertight and mesh.has_consistent_winding
    mesh.validate_single_solid()
    assert mesh.volume == pytest.approx(60*20*20)
    coarse,origin = voxelize(box_mesh((60,20,20)),2)
    dense,dense_origin = voxelize(mesh,2)
    np.testing.assert_array_equal(dense_origin,origin)
    np.testing.assert_array_equal(dense,coarse)


def test_stl_byte_limit_applies_to_direct_parser(monkeypatch):
    monkeypatch.setattr('backend.geometry.MAX_STL_BYTES',100)
    with pytest.raises(ValueError,match='over 64 MB'):
        read_stl(b'x'*101)
