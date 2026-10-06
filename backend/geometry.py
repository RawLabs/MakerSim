"""Bounded STL parsing and scanline voxel meshing using NumPy only.

Voxel centres are classified by triangle ray intersections. Surface geometry
is retained exactly for the viewer; only the solver volume is coarse.
"""
from dataclasses import dataclass
import re
import struct

import numpy as np

MAX_TRIANGLES = 120000
MAX_SHELL_CHECKS = 64_000_000


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
    def has_consistent_winding(self):
        # Shared edges must run in opposite directions on adjacent faces.
        edges = self.faces[:, [[0,1],[1,2],[2,0]]].reshape(-1,2)
        _, inverse = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True)
        signs = np.where(edges[:,0] < edges[:,1], 1, -1)
        return bool(np.all(np.bincount(inverse, weights=signs) == 0))

    @property
    def components(self):
        return self._component_labels()[0]

    def _component_labels(self):
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        edges = self.faces[:, [[0,1],[1,2],[2,0]]].reshape(-1,2)
        graph = coo_matrix((np.ones(len(edges)), (edges[:,0],edges[:,1])),shape=(len(self.vertices),len(self.vertices)))
        return connected_components(graph,directed=False)

    def validate_single_solid(self):
        """Allow closed cavity surfaces belonging to one connected solid.

        Surface connectivity alone counts an enclosed void as a second part.
        Require opposite winding, strict containment and no surface crossings;
        keep the original surfaces so voxel meshing retains the voids.
        """
        count, labels = self._component_labels()
        if count == 1:
            return
        separate = 'The STL contains separate pieces. Upload one connected, solid part.'
        if count > 65:
            raise ValueError('This STL has too many enclosed surfaces for a quick mesh. Try a simpler part.')
        triangles = self.triangles
        face_labels = labels[self.faces[:,0]]
        signed = np.einsum('ij,ij->i',triangles[:,0],np.cross(triangles[:,1],triangles[:,2]))/6
        volumes = np.bincount(face_labels,weights=signed,minlength=count)
        outer_id = int(np.argmax(np.abs(volumes)))
        cavity_ids = np.delete(np.arange(count),outer_id)
        if np.any(volumes[cavity_ids]*volumes[outer_id] >= 0):
            raise ValueError(separate)
        outer = triangles[face_labels == outer_id]
        other_faces = int(np.sum(face_labels != outer_id))
        other_vertices = int(np.sum(labels != outer_id))
        face_counts = np.bincount(face_labels,minlength=count)[cavity_ids]
        pair_checks = (other_faces**2-int(face_counts@face_counts))//2
        pair_checks += max(0,count-2)*other_faces
        if len(outer)*(other_faces+other_vertices)+pair_checks > MAX_SHELL_CHECKS:
            raise ValueError('Checking this STL\'s enclosed surfaces exceeds the quick mesh limit. Try a lower-detail export.')
        tolerance = float(self.extents.max())*1e-8
        bounds = np.array([outer.min(axis=(0,1)),outer.max(axis=(0,1))])
        outer_min, outer_max = outer.min(axis=1), outer.max(axis=1)
        cavities = []
        for component in cavity_ids:
            points = self.vertices[labels == component]
            if np.any(points <= bounds[0]+tolerance) or np.any(points >= bounds[1]-tolerance):
                raise ValueError(separate)
            if not _points_inside_surface(points,outer,tolerance):
                raise ValueError(separate)
            cavity = triangles[face_labels == component]
            if _surfaces_intersect(cavity,outer,outer_min,outer_max,tolerance):
                raise ValueError('The STL has intersecting or touching enclosed surfaces. Repair it before simulating.')
            cavities.append(cavity)
        # Cavities must not overlap or nest: nested inward shells create a
        # floating island under the scanline parity rule.
        for index,cavity in enumerate(cavities):
            for previous in cavities[:index]:
                if (np.any(cavity.min(axis=(0,1)) > previous.max(axis=(0,1)))
                        or np.any(cavity.max(axis=(0,1)) < previous.min(axis=(0,1)))):
                    continue
                if (_surfaces_intersect(cavity,previous,previous.min(axis=1),previous.max(axis=1),tolerance)
                        or _points_inside_surface(cavity[:1,0],previous,tolerance)
                        or _points_inside_surface(previous[:1,0],cavity,tolerance)):
                    raise ValueError('The STL has overlapping enclosed surfaces. Repair it before simulating.')


def _points_inside_surface(points,triangles,tolerance):
    direction = np.array([1.,.371,.529])
    direction /= np.linalg.norm(direction)
    edge1, edge2 = triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0]
    h = np.cross(direction,edge2)
    determinant = np.einsum('ij,ij->i',edge1,h)
    valid = np.abs(determinant) > 1e-12*np.linalg.norm(np.cross(edge1,edge2),axis=1)
    base, edge1, edge2, h, determinant = [array[valid] for array in (triangles[:,0],edge1,edge2,h,determinant)]
    for point in points:
        delta = point-base
        u = np.einsum('ij,ij->i',delta,h)/determinant
        q = np.cross(delta,edge1)
        v = q@direction/determinant
        distance = np.einsum('ij,ij->i',edge2,q)/determinant
        hits = distance[(u >= -1e-9)&(v >= -1e-9)&(u+v <= 1+1e-9)]
        if np.any(np.abs(hits) <= tolerance):
            return False
        hits = np.sort(hits[hits > tolerance])
        hits = hits[np.r_[True,np.diff(hits) > tolerance]] if len(hits) else hits
        if len(hits)%2 != 1:
            return False
    return True


def _segments_hit_triangles(start,end,triangles):
    direction = end-start
    edge1, edge2 = triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0]
    h = np.cross(direction,edge2)
    determinant = np.einsum('ij,ij->i',edge1,h)
    valid = np.abs(determinant) > 1e-12*np.linalg.norm(direction,axis=-1)*np.linalg.norm(np.cross(edge1,edge2),axis=1)
    inverse = np.divide(1,determinant,out=np.zeros_like(determinant),where=valid)
    delta = start-triangles[:,0]
    u = np.einsum('ij,ij->i',delta,h)*inverse
    q = np.cross(delta,edge1)
    v = np.einsum('...i,...i->...',direction,q)*inverse
    distance = np.einsum('ij,ij->i',edge2,q)*inverse
    return valid&(u >= -1e-9)&(v >= -1e-9)&(u+v <= 1+1e-9)&(distance >= -1e-9)&(distance <= 1+1e-9)


def _surfaces_intersect(first,second,second_min,second_max,tolerance):
    for triangle in first:
        candidates = np.all(second_min <= triangle.max(axis=0)+tolerance,axis=1)&np.all(second_max >= triangle.min(axis=0)-tolerance,axis=1)
        others = second[candidates]
        if not len(others):
            continue
        for edge in range(3):
            if np.any(_segments_hit_triangles(triangle[edge],triangle[(edge+1)%3],others)):
                return True
            if np.any(_segments_hit_triangles(others[:,edge],others[:,(edge+1)%3],np.broadcast_to(triangle,others.shape))):
                return True
        normal = np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0])
        coplanar = np.all(np.abs((others-triangle[0])@normal) <= tolerance*np.linalg.norm(normal),axis=1)
        if np.any(coplanar):
            # Separating-axis test for coplanar triangle overlap, including contact.
            axes = np.delete(np.arange(3),np.argmax(np.abs(normal)))
            a,b = triangle[:,axes],others[coplanar][:,:,axes]
            separated = np.zeros(len(b),dtype=bool)
            for edge in range(3):
                for vector in (a[(edge+1)%3]-a[edge],b[:,(edge+1)%3]-b[:,edge]):
                    axis = np.stack([-vector[...,1],vector[...,0]],axis=-1)
                    axis = np.broadcast_to(axis,(len(b),2))
                    pa = np.einsum('vi,ni->nv',a,axis)
                    pb = np.einsum('nvi,ni->nv',b,axis)
                    margin = tolerance*np.linalg.norm(axis,axis=-1)
                    separated |= (pa.max(axis=-1) < pb.min(axis=-1)-margin)|(pb.max(axis=-1) < pa.min(axis=-1)-margin)
            if np.any(~separated):
                return True
    return False


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
