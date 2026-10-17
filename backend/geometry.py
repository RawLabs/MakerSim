"""Bounded STL parsing, surface validation and scanline voxel meshing.

Voxel centres are classified by triangle ray intersections. Surface geometry
is retained exactly for the viewer; only the solver volume is coarse.
"""
from dataclasses import dataclass, field
import hashlib
import re
import struct
from itertools import islice

import numpy as np

from .limits import MAX_STL_BYTES, MAX_TRIANGLES
MAX_SHELL_CHECKS = 64_000_000
MAX_SURFACE_CHECKS = 64_000_000
# Empty space also consumes memory while resolving thin features. Bound the
# sampled grid and projected triangle work independently of occupied cells.
MAX_VOXEL_CELLS = 8_000_000
MAX_VOXEL_RAYS = 250_000
MAX_VOXEL_CHECKS = 8_000_000


@dataclass
class TriangleMesh:
    vertices: np.ndarray
    faces: np.ndarray
    source_center_mm: np.ndarray = field(default_factory=lambda: np.zeros(3))
    _validated_signature: bytes | None = field(default=None,init=False,repr=False,compare=False)

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
        # Integer edge keys avoid sorting millions of two-column records.
        _, counts = np.unique(edges[:,0]*len(self.vertices)+edges[:,1], return_counts=True)
        return bool(np.all(counts == 2))

    @property
    def has_consistent_winding(self):
        # Shared edges must run in opposite directions on adjacent faces.
        edges = self.faces[:, [[0,1],[1,2],[2,0]]].reshape(-1,2)
        ordered = np.sort(edges, axis=1)
        _, inverse = np.unique(ordered[:,0]*len(self.vertices)+ordered[:,1], return_inverse=True)
        signs = np.where(edges[:,0] < edges[:,1], 1, -1)
        return bool(np.all(np.bincount(inverse, weights=signs) == 0))

    @property
    def components(self):
        return self._component_labels()[0]

    def _component_labels(self):
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        # Surface pieces must share an edge, not just a zero-area point contact.
        edges = np.sort(self.faces[:, [[0,1],[1,2],[2,0]]].reshape(-1,2),axis=1)
        keys = edges[:,0]*len(self.vertices)+edges[:,1]
        order = np.argsort(keys)
        adjacent = keys[order[1:]] == keys[order[:-1]]
        first, second = order[:-1][adjacent]//3, order[1:][adjacent]//3
        graph = coo_matrix((np.ones(len(first)), (first,second)),shape=(len(self.faces),len(self.faces)))
        return connected_components(graph,directed=False)

    def validate_single_solid(self):
        """Allow closed cavity surfaces belonging to one connected solid.

        Surface connectivity alone counts an enclosed void as a second part.
        Require opposite winding, strict containment and no surface crossings;
        keep the original surfaces so voxel meshing retains the voids.
        """
        # Refining the same model should not repeat spatial validation. Hash
        # content so callers that mutate public arrays cannot bypass checks.
        signature = hashlib.blake2b(digest_size=16)
        for array in (self.vertices,self.faces):
            signature.update(array.dtype.str.encode())
            signature.update(np.asarray(array.shape,dtype=np.int64).tobytes())
            signature.update(np.ascontiguousarray(array).view(np.uint8))
        digest = signature.digest()
        if digest == self._validated_signature:
            return
        count, face_labels = self._component_labels()
        if count == 1:
            _validate_surface_intersections(self)
            self._validated_signature = digest
            return
        separate = 'The STL contains separate pieces. Upload one connected, solid part.'
        if count > 65:
            raise ValueError('This STL has too many enclosed surfaces for a quick mesh. Try a simpler part.')
        triangles = self.triangles
        signed = np.einsum('ij,ij->i',triangles[:,0],np.cross(triangles[:,1],triangles[:,2]))/6
        volumes = np.bincount(face_labels,weights=signed,minlength=count)
        outer_id = int(np.argmax(np.abs(volumes)))
        cavity_ids = np.delete(np.arange(count),outer_id)
        if np.any(volumes[cavity_ids]*volumes[outer_id] >= 0):
            raise ValueError(separate)
        outer = triangles[face_labels == outer_id]
        other_faces = int(np.sum(face_labels != outer_id))
        other_vertices = len(np.unique(self.faces[face_labels != outer_id]))
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
            points = self.vertices[np.unique(self.faces[face_labels == component])]
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
        _validate_surface_intersections(self)
        self._validated_signature = digest


def _separated_triangle_pairs(first, second, axes, tolerance):
    """Separating-axis test, with per-pair length-scaled contact tolerance."""
    separated = np.zeros(len(first),dtype=bool)
    for axis in axes:
        a = np.einsum('nvi,ni->nv',first,axis)
        b = np.einsum('nvi,ni->nv',second,axis)
        margin = tolerance*np.linalg.norm(axis,axis=1)
        separated |= (a.max(1) < b.min(1)-margin)|(b.max(1) < a.min(1)-margin)
        if separated.all():
            break
    return separated


def _triangle_pairs_cross(first, second, first_faces, second_faces, tolerance):
    """Detect contact except for the pair's own shared vertices or edge."""
    shared = first_faces[:,:,None] == second_faces[:,None,:]
    common = shared.sum(axis=(1,2))
    if np.any(common == 3):
        return True
    first_edges = np.roll(first,-1,axis=1)-first
    second_edges = np.roll(second,-1,axis=1)-second
    first_normal = np.cross(first_edges[:,0],first_edges[:,1])
    second_normal = np.cross(second_edges[:,0],second_edges[:,1])
    normal_size = np.linalg.norm(first_normal,axis=1)
    coplanar = np.all(np.abs(np.einsum('nvi,ni->nv',second-first[:,:1],first_normal)) <= tolerance*normal_size[:,None],axis=1)
    # Coplanar neighbours sharing an edge are valid only on opposite sides.
    edge_pairs = (common == 2)&coplanar
    if np.any(edge_pairs):
        a, b = first[edge_pairs],second[edge_pairs]
        common_a, common_b = shared[edge_pairs].any(2),shared[edge_pairs].any(1)
        edge = a[common_a].reshape(-1,2,3)
        direction = edge[:,1]-edge[:,0]
        side_a = np.cross(direction,a[~common_a]-edge[:,0])
        side_b = np.cross(direction,b[~common_b]-edge[:,0])
        if np.any(np.einsum('ni,ni->n',side_a,side_b) > 0):
            return True
    # Unrelated triangles use all triangle/edge separating axes. In-plane
    # axes are included so coplanar intersections are detected as well.
    unrelated = common == 0
    if np.any(unrelated):
        a,b = first[unrelated],second[unrelated]
        ae,be = first_edges[unrelated],second_edges[unrelated]
        an,bn = first_normal[unrelated],second_normal[unrelated]
        axes = [np.cross(normal,edges[:,i]) for normal,edges in ((an,ae),(bn,be)) for i in range(3)]
        axes += [an,bn]
        axes += [np.cross(ae[:,i],be[:,j]) for i in range(3) for j in range(3)]
        if np.any(~_separated_triangle_pairs(a,b,axes,tolerance)):
            return True
    vertex_pairs = common == 1
    if not np.any(vertex_pairs):
        return False
    a,b = first[vertex_pairs],second[vertex_pairs]
    common_a = shared[vertex_pairs].any(2)
    common_b = shared[vertex_pairs].any(1)
    point = a[common_a]
    ae = (a-point[:,None])[~common_a].reshape(-1,2,3)
    be = (b-point[:,None])[~common_b].reshape(-1,2,3)
    flat = coplanar[vertex_pairs]
    if np.any(flat):
        # Cones at the common vertex overlap if an edge enters the opposite
        # triangle's angular sector. A common ray is invalid edge contact too.
        normal = first_normal[vertex_pairs][flat]
        unit_normal = normal/np.linalg.norm(normal,axis=1)[:,None]
        av,bv = ae[flat],be[flat]
        cross = np.einsum('nabi,ni->nab',np.cross(av[:,:,None],bv[:,None]),unit_normal)
        sign_a = np.sign(np.einsum('ni,ni->n',np.cross(av[:,0],av[:,1]),unit_normal))
        sign_b = np.sign(np.einsum('ni,ni->n',np.cross(bv[:,0],bv[:,1]),unit_normal))
        length_a,length_b = np.linalg.norm(av,axis=2),np.linalg.norm(bv,axis=2)
        margin = tolerance*np.maximum(length_a.max(1),length_b.max(1))[:,None]
        into_a = (cross[:,0]*sign_a[:,None] > margin)&(-cross[:,1]*sign_a[:,None] > margin)
        into_b = (-cross[:,:,0]*sign_b[:,None] > margin)&(cross[:,:,1]*sign_b[:,None] > margin)
        if np.any(into_a)|np.any(into_b):
            return True
        parallel = np.abs(cross) <= tolerance*np.maximum(length_a[:,:,None],length_b[:,None])
        same_direction = np.einsum('nai,nbi->nab',av,bv) > 0
        if np.any(parallel&same_direction):
            return True
    if np.any(~flat):
        # The two planes meet in a line through the common vertex. An edge
        # crossing away from that vertex proves additional surface contact.
        a,b,point = a[~flat],b[~flat],point[~flat]
        for left,right in ((a,b),(b,a)):
            edge1,edge2 = right[:,1]-right[:,0],right[:,2]-right[:,0]
            normal = np.cross(edge1,edge2)
            denominator = np.einsum('ni,ni->n',normal,normal)
            # An edge can lie in the other plane without crossing it; check
            # contained endpoints too, excluding the allowed common vertex.
            for vertex in range(3):
                delta = left[:,vertex]-right[:,0]
                u = np.einsum('ni,ni->n',np.cross(delta,edge2),normal)/denominator
                v = np.einsum('ni,ni->n',np.cross(edge1,delta),normal)/denominator
                on_plane = np.abs(np.einsum('ni,ni->n',delta,normal)) <= tolerance*np.linalg.norm(normal,axis=1)
                inside = on_plane&(u >= -1e-9)&(v >= -1e-9)&(u+v <= 1+1e-9)
                if np.any(inside&(np.linalg.norm(left[:,vertex]-point,axis=1) > tolerance)):
                    return True
            # Only the edge opposite the known shared point can bound a new
            # crossing. Testing incident edges amplifies cancellation near
            # the shared point in long, nearly coplanar export triangles.
            vertex = np.argmin(np.linalg.norm(left-point[:,None],axis=2),axis=1)
            rows = np.arange(len(left))
            start,end = left[rows,(vertex+1)%3],left[rows,(vertex+2)%3]
            direction = end-start
            h = np.cross(direction,edge2)
            determinant = np.einsum('ni,ni->n',edge1,h)
            valid = np.abs(determinant) > 1e-12*np.linalg.norm(direction,axis=1)*np.linalg.norm(normal,axis=1)
            inverse = np.divide(1,determinant,out=np.zeros_like(determinant),where=valid)
            delta = start-right[:,0]
            u = np.einsum('ni,ni->n',delta,h)*inverse
            q = np.cross(delta,edge1)
            v = np.einsum('ni,ni->n',direction,q)*inverse
            distance = np.einsum('ni,ni->n',edge2,q)*inverse
            hit = start+distance[:,None]*direction
            interior = valid&(u >= -1e-9)&(v >= -1e-9)&(u+v <= 1+1e-9)&(distance >= -1e-9)&(distance <= 1+1e-9)
            if np.any(interior&(np.linalg.norm(hit-point,axis=1) > tolerance)):
                return True
    return False


def _validate_surface_intersections(mesh):
    """Bounded spatial broadphase followed by exact triangle contact tests."""
    from scipy.spatial import cKDTree
    triangles = mesh.triangles
    low,high = triangles.min(axis=1),triangles.max(axis=1)
    scale = mesh.extents
    centers = (low+high)/(2*scale)
    radii = np.linalg.norm((high-low)/(2*scale),axis=1)
    tolerance = float(mesh.extents.max())*1e-8
    search_tolerance = tolerance/scale.min()
    # Radius bands prevent one large triangle from expanding every fine
    # triangle's search to the dimensions of the whole model.
    bands = np.floor(np.log2(np.maximum(radii,search_tolerance))).astype(int)
    groups = []
    for band in np.unique(bands):
        ids = np.flatnonzero(bands == band)
        groups.append((ids,cKDTree(centers[ids]),radii[ids].max()))

    def check_pairs(i,j):
        keep = np.all(low[i] <= high[j]+tolerance,axis=1)&np.all(low[j] <= high[i]+tolerance,axis=1)
        i,j = i[keep],j[keep]
        if len(i) and _triangle_pairs_cross(triangles[i],triangles[j],mesh.faces[i],mesh.faces[j],tolerance):
            raise ValueError('The STL has intersecting or touching surfaces. Repair it before simulating.')

    checks = 0
    for group_index,(ids,tree,radius) in enumerate(groups):
        for other_index in range(group_index+1):
            other_ids,other_tree,other_radius = groups[other_index]
            bound = radius+other_radius+search_tolerance
            pairs = tree.count_neighbors(other_tree,bound)
            checks += (pairs-len(ids))//2 if group_index == other_index else pairs
            if checks > MAX_SURFACE_CHECKS:
                raise ValueError('Checking this STL for intersecting surfaces exceeds the quick mesh limit. Try a lower-detail export.')
            if group_index == other_index and (pairs-len(ids))//2 <= 8_000_000:
                # Native pair enumeration avoids Python neighbour lists for
                # dense, uniformly tessellated exports; cap its array at128MB.
                nearby = tree.query_pairs(bound,output_type='ndarray')
                for start in range(0,len(nearby),65_536):
                    batch = ids[nearby[start:start+65_536]]
                    check_pairs(batch[:,0],batch[:,1])
                continue
            for start in range(0,len(ids),1024):
                batch = ids[start:start+1024]
                sizes = other_tree.query_ball_point(centers[batch],radii[batch]+other_radius+search_tolerance,return_length=True)
                # Bound Python neighbour lists even for concentrated facets.
                step = max(1,250_000//max(1,int(sizes.max())))
                for offset in range(0,len(batch),step):
                    part = batch[offset:offset+step]
                    hits = other_tree.query_ball_point(centers[part],radii[part]+other_radius+search_tolerance,return_sorted=False)
                    i = np.repeat(part,np.fromiter(map(len,hits),dtype=int))
                    if not len(i):
                        continue
                    j = other_ids[np.concatenate(hits).astype(int)]
                    keep = (i < j) if group_index == other_index else np.ones(len(i),dtype=bool)
                    check_pairs(i[keep],j[keep])


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
    if len(data) > MAX_STL_BYTES:
        raise ValueError('This STL is over 64 MB. Use a binary STL or export at a lower detail level.')
    triangle_limit = f'Use an STL with at most {MAX_TRIANGLES:,} triangles. Export at a lower detail level.'
    if len(data) >= 84 and 84 + struct.unpack_from('<I', data, 80)[0]*50 == len(data):
        count = struct.unpack_from('<I', data, 80)[0]
        if count > MAX_TRIANGLES:
            raise ValueError(triangle_limit)
        dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3,3)), ('attribute', '<u2')])
        triangles = np.frombuffer(data, dtype=dtype, count=count, offset=84)['vertices'].astype(float)
    else:
        try:
            content = data.decode('ascii')
            matches = re.finditer(r'\bvertex\s+(\S+)\s+(\S+)\s+(\S+)', content)
            # Bound conversion without retaining millions of Python strings.
            values = np.fromiter((float(v) for match in islice(matches,MAX_TRIANGLES*3+1)
                                  for v in match.groups()),dtype=float)
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError('This file could not be read as an ASCII or binary STL.') from error
        if values.size > MAX_TRIANGLES*9:
            raise ValueError(triangle_limit)
        if not values.size or values.size % 9:
            raise ValueError('This file could not be read as an ASCII or binary STL.')
        triangles = values.reshape(-1,3,3)
    if len(triangles) < 4 or not np.isfinite(triangles).all():
        raise ValueError('The STL must contain a finite 3D surface.')
    triangles *= 25.4 if unit == 'inch' else 1
    span = np.ptp(triangles.reshape(-1,3), axis=0)
    if span.min() <= 1e-5 or span.max() > 5000 or span.max() < 0.1:
        raise ValueError('The part needs 3D thickness and a size between 0.1 mm and 5 m. Check the STL units.')
    # Float STL exports often repeat vertices with slight rounding differences.
    decimals = max(0, int(8 - np.log10(span.max())))
    vertices, inverse = np.unique(np.round(triangles.reshape(-1,3), decimals), axis=0, return_inverse=True)
    faces = inverse.reshape(-1,3)
    t = vertices[faces]
    valid = np.linalg.norm(np.cross(t[:,1]-t[:,0], t[:,2]-t[:,0]), axis=1) > span.max()**2 * 1e-12
    faces = faces[valid]
    if len(faces) < 4:
        raise ValueError('The STL must contain a finite 3D surface.')
    # Discard the vertices of removed facets as well: otherwise they become
    # disconnected components and move the bounds and displayed source origin.
    if not np.all(valid):
        used, inverse = np.unique(faces, return_inverse=True)
        vertices, faces = vertices[used], inverse.reshape(-1,3)
    source_center = (vertices.min(axis=0) + vertices.max(axis=0))/2
    vertices -= source_center
    return TriangleMesh(vertices, faces, source_center)


def voxelize(mesh: TriangleMesh, pitch: float) -> tuple[np.ndarray, np.ndarray]:
    if not np.isfinite(pitch) or pitch <= 0:
        raise ValueError('Mesh cell size must be finite and positive.')
    mesh_limit = 'Resolving this part exceeds the quick mesh limit. Use a coarser mesh or a simpler part.'
    # Rectangular cells preserve exact overall dimensions, including thickness.
    origin = mesh.bounds[0]
    with np.errstate(over='ignore', invalid='ignore'):
        dimensions = np.maximum(1, np.ceil(mesh.extents/pitch))
    if (not np.isfinite(dimensions).all() or np.any(dimensions > MAX_VOXEL_CELLS)
            or np.prod(dimensions) > MAX_VOXEL_CELLS
            or dimensions[1]*dimensions[2] > MAX_VOXEL_RAYS):
        raise ValueError(mesh_limit)
    shape = dimensions.astype(int)
    cell_size = mesh.extents / shape
    # Perturb only classification rays, never node positions or beam thickness.
    # An asymmetric sub-micron offset avoids exact edges on symmetric exports.
    offset = np.array([.19317,.21731])*1e-6
    y_values = origin[1] + (np.arange(shape[1])+0.5+offset[0])*cell_size[1]
    z_values = origin[2] + (np.arange(shape[2])+0.5+offset[1])*cell_size[2]
    intersections = [[] for _ in range(shape[1]*shape[2])]
    triangles = mesh.triangles
    # Dense exports contain many facets between sample rays. Reject their
    # projected bounding boxes in bulk instead of looping over every facet.
    low = np.maximum(0, np.ceil((triangles[:,:,1:].min(axis=1)-origin[1:])/cell_size[1:]-0.5-offset).astype(int))
    high = np.minimum(shape[1:]-1, np.floor((triangles[:,:,1:].max(axis=1)-origin[1:])/cell_size[1:]-0.5-offset).astype(int))
    candidates = np.flatnonzero(np.all(high >= low,axis=1))
    projected = triangles[candidates,:,1:]
    edge1, edge2 = projected[:,1]-projected[:,0], projected[:,2]-projected[:,0]
    determinants = edge1[:,0]*edge2[:,1]-edge1[:,1]*edge2[:,0]
    crossing = np.abs(determinants) >= cell_size[1]*cell_size[2]*1e-12
    candidates, determinants = candidates[crossing], determinants[crossing]
    if np.prod(high[candidates]-low[candidates]+1,axis=1).sum() > MAX_VOXEL_CHECKS:
        raise ValueError(mesh_limit)
    for index, determinant in zip(candidates, determinants):
        tri = triangles[index]
        yz = tri[:, 1:]
        edge1, edge2 = yz[1]-yz[0], yz[2]-yz[0]
        lo, hi = low[index], high[index]
        j, k = np.meshgrid(np.arange(lo[0],hi[0]+1),np.arange(lo[1],hi[1]+1), indexing='ij')
        dy, dz = y_values[j]-yz[0,0], z_values[k]-yz[0,1]
        a = (dy*edge2[1]-dz*edge2[0])/determinant
        b = (dz*edge1[0]-dy*edge1[1])/determinant
        inside = (a >= -1e-10) & (b >= -1e-10) & (a+b <= 1+1e-10)
        x = tri[0,0]+a*(tri[1,0]-tri[0,0])+b*(tri[2,0]-tri[0,0])
        for jj, kk, xx in zip(j[inside],k[inside],x[inside]):
            intersections[int(jj*shape[2]+kk)].append(float(xx))
    occupancy = np.zeros(shape, dtype=bool)
    x_values = origin[0]+(np.arange(shape[0])+0.5)*cell_size[0]
    for ray, hits in enumerate(intersections):
        if not hits:
            continue
        hits = np.array(sorted(hits))
        hits = hits[np.r_[True,np.diff(hits)>cell_size[0]*1e-7]]
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
