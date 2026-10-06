"""Bounded STL parsing and scanline voxel meshing using NumPy only.

Voxel centres are classified by triangle ray intersections. Surface geometry
is retained exactly for the viewer; only the solver volume is coarse.
"""
from dataclasses import dataclass
import re
import struct

import numpy as np

MAX_TRIANGLES = 120000


@dataclass
class TriangleMesh:
    vertices: np.ndarray
    faces: np.ndarray

    @property
    def triangles(self):
        return self.vertices[self.faces]

    @property
    def bounds(self):
        return np.array([self.vertices.min(axis=0), self.vertices.max(axis=0)])

    @property
    def extents(self):
        return np.ptp(self.vertices, axis=0)

    @property
    def area(self):
        t = self.triangles
        return float(np.linalg.norm(np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0]), axis=1).sum()/2)

    @property
    def volume(self):
        t = self.triangles
        return float(np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()/6)

    @property
    def is_watertight(self):
        edges = np.sort(self.faces[:, [[0,1],[1,2],[2,0]]].reshape(-1,2), axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        return bool(np.all(counts == 2))

    @property
    def components(self):
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        edges = self.faces[:, [[0,1],[1,2],[2,0]]].reshape(-1,2)
        graph = coo_matrix((np.ones(len(edges)), (edges[:,0],edges[:,1])),shape=(len(self.vertices),len(self.vertices)))
        return connected_components(graph,directed=False,return_labels=False)


def read_stl(data: bytes, unit: str = 'mm') -> TriangleMesh:
    if len(data) >= 84 and 84 + struct.unpack_from('<I', data, 80)[0]*50 == len(data):
        count = struct.unpack_from('<I', data, 80)[0]
        if count > MAX_TRIANGLES:
            raise ValueError(f'Use an STL with fewer than {MAX_TRIANGLES:,} triangles. Export at a lower detail level.')
        dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3,3)), ('attribute', '<u2')])
        triangles = np.frombuffer(data, dtype=dtype, count=count, offset=84)['vertices'].astype(float)
    else:
        try:
            content = data.decode('ascii')
            values = re.findall(r'\bvertex\s+(\S+)\s+(\S+)\s+(\S+)', content)
            if len(values) % 3 or not values or len(values) > MAX_TRIANGLES*3:
                raise ValueError('Invalid triangle count')
            triangles = np.array(values, dtype=float).reshape(-1,3,3)
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError('This file could not be read as an ASCII or binary STL.') from error
    if len(triangles) < 4 or not np.isfinite(triangles).all():
        raise ValueError('The STL must contain a finite 3D surface.')
    triangles *= 25.4 if unit == 'inch' else 1
    span = np.ptp(triangles.reshape(-1,3), axis=0)
    if span.min() <= 1e-5 or span.max() > 5000 or span.max() < 0.1:
        raise ValueError('The part needs 3D thickness and a size between 0.1 mm and 5 m. Check the STL units.')
    # Float STL exports often repeat vertices with slight rounding differences.
    decimals = max(0, int(8 - np.log10(span.max())))
    vertices, inverse = np.unique(np.round(triangles.reshape(-1,3), decimals), axis=0, return_inverse=True)
    vertices -= (vertices.min(axis=0) + vertices.max(axis=0))/2
    faces = inverse.reshape(-1,3)
    t = vertices[faces]
    valid = np.linalg.norm(np.cross(t[:,1]-t[:,0], t[:,2]-t[:,0]), axis=1) > span.max()**2 * 1e-12
    return TriangleMesh(vertices, faces[valid])


def voxelize(mesh: TriangleMesh, pitch: float) -> tuple[np.ndarray, np.ndarray]:
    # Slight irrational offset avoids rays through shared edges/vertices.
    origin = mesh.bounds[0] - pitch*np.array([0.17,0.19317,0.21731])
    shape = np.ceil((mesh.bounds[1]-origin)/pitch).astype(int) + 1
    y_values = origin[1] + (np.arange(shape[1])+0.5)*pitch
    z_values = origin[2] + (np.arange(shape[2])+0.5)*pitch
    intersections = [[] for _ in range(shape[1]*shape[2])]
    for tri in mesh.triangles:
        yz = tri[:, 1:]
        edge1, edge2 = yz[1]-yz[0], yz[2]-yz[0]
        determinant = edge1[0]*edge2[1]-edge1[1]*edge2[0]
        if abs(determinant) < pitch*pitch*1e-12:
            continue
        lo = np.maximum(0, np.ceil((yz.min(axis=0)-origin[1:])/pitch-0.5).astype(int))
        hi = np.minimum(shape[1:]-1, np.floor((yz.max(axis=0)-origin[1:])/pitch-0.5).astype(int))
        if np.any(hi < lo):
            continue
        j, k = np.meshgrid(np.arange(lo[0],hi[0]+1),np.arange(lo[1],hi[1]+1), indexing='ij')
        dy, dz = y_values[j]-yz[0,0], z_values[k]-yz[0,1]
        a = (dy*edge2[1]-dz*edge2[0])/determinant
        b = (dz*edge1[0]-dy*edge1[1])/determinant
        inside = (a >= -1e-10) & (b >= -1e-10) & (a+b <= 1+1e-10)
        x = tri[0,0]+a*(tri[1,0]-tri[0,0])+b*(tri[2,0]-tri[0,0])
        for jj, kk, xx in zip(j[inside],k[inside],x[inside]):
            intersections[int(jj*shape[2]+kk)].append(float(xx))
    occupancy = np.zeros(shape, dtype=bool)
    x_values = origin[0]+(np.arange(shape[0])+0.5)*pitch
    for ray, hits in enumerate(intersections):
        if not hits:
            continue
        hits = np.array(sorted(hits))
        hits = hits[np.r_[True,np.diff(hits)>pitch*1e-7]]
        if len(hits)%2:
            raise ValueError('The STL has an open or intersecting surface. Repair it in your slicer and export again.')
        j, k = divmod(ray,shape[2])
        for entry, leave in hits.reshape(-1,2):
            occupancy[:,j,k] |= (x_values >= entry) & (x_values < leave)
    return occupancy, origin


def write_binary_stl(triangles: np.ndarray) -> bytes:
    t = np.asarray(triangles,dtype=float)
    normals = np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0])
    normals /= np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
    dtype = np.dtype([('normal','<f4',(3,)),('vertices','<f4',(3,3)),('attribute','<u2')])
    records = np.zeros(len(t),dtype=dtype)
    records['normal'],records['vertices'] = normals,t
    return b'MakerSim example'.ljust(80,b'\0')+struct.pack('<I',len(t))+records.tobytes()
