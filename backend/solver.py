"""Rectangular-brick elasticity with enhanced bending, in mm / N / MPa.

Closed STL -> bounded voxel grid -> condensed Wilson bricks -> sparse elasticity.
The staircase volume is an approximation, not a geometry-conforming mesh.
No artificial stiffness, point loads, or unconstrained floating components.
"""
from dataclasses import dataclass
import warnings

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, spsolve
from scipy.spatial import cKDTree

from .schemas import Patch, PrintSettings, SimulationRequest, force_newtons, normalized_vector
from .geometry import TriangleMesh, voxelize
from .hexa import ELEMENT_TYPE, incompatible_hex
from .print_model import cell_print_model

SOLVER_VERSION = "0.3.0"
MAX_CELLS = 4800
CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
FACES = [(0, -1, [0, 4, 7, 3]), (0, 1, [1, 2, 6, 5]),
         (1, -1, [0, 1, 5, 4]), (1, 1, [3, 7, 6, 2]),
         (2, -1, [0, 3, 2, 1]), (2, 1, [4, 5, 6, 7])]


class SimulationError(ValueError):
    pass


@dataclass
class VolumeMesh:
    nodes: np.ndarray
    cells: np.ndarray
    surface: np.ndarray
    components: int
    pitch: float
    boundary_faces: np.ndarray
    boundary_normals: np.ndarray
    boundary_triangles: np.ndarray
    cell_size: np.ndarray
    boundary_areas: np.ndarray
    boundary_cells: np.ndarray


def create_volume(mesh: TriangleMesh, resolution: int = 28, *, max_cells: int | None = None,
                  strict_pitch: bool = False) -> VolumeMesh:
    budget = MAX_CELLS if max_cells is None else max_cells
    if type(resolution) is not int or not 8 <= resolution <= 100:
        raise SimulationError("Mesh resolution must be an integer between 8 and 100.")
    if type(budget) is not int or not 4 <= budget <= 20000:
        raise SimulationError("Mesh cell budget must be an integer between 4 and 20000.")
    if not mesh.is_watertight:
        raise SimulationError("This STL has an open surface. Repair it before simulating.")
    if not mesh.has_consistent_winding:
        raise SimulationError("This STL has inconsistent triangle directions. Repair its normals in your slicer and export again before simulating.")
    try:
        mesh.validate_single_solid()
    except ValueError as error:
        raise SimulationError(str(error)) from error
    pitch = float(mesh.extents.max()) / resolution
    def sample(pitch):
        try:
            return voxelize(mesh, pitch)
        except ValueError as error:
            raise SimulationError(str(error)) from error
    # Adaptive bounded meshing.
    for _ in range(8):
        occupancy, origin = sample(pitch)
        count = int(occupancy.sum())
        if count <= budget:
            break
        if strict_pitch:
            raise SimulationError("Requested refinement exceeds the cell budget. Increase max_cells or reduce resolution.")
        pitch *= max(1.12, (count / budget) ** (1/3) * 1.02)
    else:
        raise SimulationError("This part is too complex for a quick mesh. Try a simpler STL.")
    # A connected STL can lose thin walls or bridges when sampled coarsely.
    # Retry with smaller cells before blaming the original geometry. Keep all
    # occupied cells and six-connectivity: no filling gaps or discarding pieces.
    for attempt in range(9):
        _, components = ndimage.label(occupancy)
        if components == 1 and count >= 4:
            break
        if attempt == 8:
            raise SimulationError("The quick mesh cannot resolve this part's thin walls or connections. Try a thicker version or a simpler part.")
        pitch /= 1.2
        occupancy, origin = sample(pitch)
        count = int(occupancy.sum())
        if count > budget:
            raise SimulationError("Resolving this part's thin walls or connections exceeds the quick mesh limit. Try a thicker version or a simpler part.")
    indices = np.argwhere(occupancy)
    lattice, inverse = np.unique((indices[:, None, :] + CORNERS).reshape(-1, 3), axis=0, return_inverse=True)
    cells = inverse.reshape(-1, 8)
    cell_size = mesh.extents / np.asarray(occupancy.shape)
    pitch = float(cell_size.max())
    nodes = origin + lattice * cell_size
    boundary, normals, triangles, areas, owners = [], [], [], [], []
    for axis, direction, corner_ids in FACES:
        neighbors = indices.copy()
        neighbors[:, axis] += direction
        valid = np.all((neighbors >= 0) & (neighbors < np.array(occupancy.shape)), axis=1)
        exposed = np.ones(len(indices), dtype=bool)
        exposed[valid] = ~occupancy[tuple(neighbors[valid].T)]
        faces = cells[exposed][:, corner_ids]
        normal = np.zeros(3)
        normal[axis] = direction
        boundary.append(faces)
        owners.append(np.flatnonzero(exposed))
        areas.append(np.full(len(faces), np.prod(np.delete(cell_size, axis))))
        normals.append(np.tile(normal, (len(faces), 1)))
        # Outward triangles cover each bilinear boundary quad for rendering.
        face_triangles = faces[:, [[0, 1, 2], [0, 2, 3]]].reshape(-1, 3)
        if len(face_triangles):
            points = nodes[face_triangles[0]]
            if np.dot(np.cross(points[1]-points[0], points[2]-points[0]), normal) < 0:
                face_triangles = face_triangles[:, [0, 2, 1]]
        triangles.append(face_triangles)
    boundary = np.concatenate(boundary)
    surface = np.unique(boundary)
    # Connectivity alone misses thin appendages that vanish without splitting
    # the remaining part. Do not return fields for a remote, unresolved surface.
    distance, _ = cKDTree(nodes[surface]).query(mesh.vertices)
    if distance.max() > 1.8*pitch:
        raise SimulationError("The quick mesh misses part of this surface. Try finer detail or a thicker feature.")
    return VolumeMesh(nodes, cells, surface, components,
                      pitch, boundary, np.concatenate(normals), np.concatenate(triangles),
                      cell_size, np.concatenate(areas), np.concatenate(owners))


def printed_elasticity(material: dict, settings: PrintSettings, mesh: TriangleMesh,
                       volume: VolumeMesh | None = None) -> tuple[np.ndarray, dict]:
    if not mesh.has_consistent_winding:
        raise SimulationError("This STL has inconsistent triangle directions. Repair its normals in your slicer and export again before simulating.")
    # Public effective-modulus summary; the actual solve applies this model
    # spatially, rather than replacing every cell with its average stiffness.
    volume = create_volume(mesh) if volume is None else volume
    print_model = cell_print_model(volume, settings)
    fraction = float(print_model.solid_fraction.mean())
    stiffness_factor = float(print_model.stiffness_factor.mean())
    d, properties = solid_elasticity(material, settings.orientation)
    return d*stiffness_factor, {"solid_fraction": fraction,
        **{key:value*stiffness_factor for key,value in properties.items()}}


def solid_elasticity(material: dict, orientation: str) -> tuple[np.ndarray, dict]:
    """Solid printed material; shell/core distribution is applied separately."""
    physics = material["physics"]
    e_xy = physics["E_xy_mpa"]
    # Generic entries have no measured Z modulus. This is an explicit heuristic.
    z_ratio = physics["E_z_mpa"] / physics["E_xy_mpa"]
    axis = {"x": 0, "y": 1, "z": 2}[orientation]
    e = np.full(3, e_xy)
    e[axis] *= z_ratio
    nu = physics["nu"]
    # Symmetric positive definite orthotropic compliance; pairwise couplings
    # use geometric mean moduli. Shear involving build axis is likewise scaled.
    compliance = np.zeros((6, 6))
    for i in range(3):
        compliance[i, i] = 1 / e[i]
        for j in range(i+1, 3):
            compliance[i, j] = compliance[j, i] = -nu / np.sqrt(e[i]*e[j])
    for k, (i, j) in enumerate([(0, 1), (1, 2), (0, 2)], start=3):
        g = physics["G_xy_mpa"]
        if axis in (i, j):
            g *= np.sqrt(z_ratio)
        compliance[k, k] = 1 / g
    return np.linalg.inv(compliance), {"modulus_xy_mpa": e_xy, "modulus_build_mpa": e[axis]}


def patch_faces(volume: VolumeMesh, patch: Patch, *, active_cells: np.ndarray | None = None) -> tuple[np.ndarray, float]:
    """Select outward boundary faces intersecting the contact disk projection.

    A surface-node depth slab can silently clamp a second layer or the back of a
    thin wall. Only faces pointing toward the selected surface are eligible.
    Whole intersected cell faces are used; the reported coverage includes their
    actual bounds and area rather than claiming an exact circular contact.
    When supplied, active_cells excludes faces owned by empty printed cells;
    sharing their corner nodes with a printed neighbor does not create a face.
    """
    center = np.array(patch.point)
    normal = np.array(normalized_vector(patch.normal, "A selected area needs a finite, nonzero surface normal."))
    delta = volume.nodes[volume.boundary_faces] - center
    depth = delta @ normal
    candidates = ((volume.boundary_normals @ normal > 1e-8)
                  & (np.abs(depth.mean(axis=1)) <= volume.pitch * .85))
    face_ids = np.flatnonzero(candidates)
    projected = delta[candidates] - depth[candidates, :, None] * normal
    radius = max(patch.radius, volume.pitch * 1.1)
    # Distance to the projected convex quad, including its interior. This also
    # handles small disks near a face corner without relying on face centroids.
    edges = np.roll(projected, -1, axis=1) - projected
    edge_length2 = np.einsum('fij,fij->fi', edges, edges)
    along = np.clip(-np.einsum('fij,fij->fi', projected, edges)
                    / np.maximum(edge_length2, 1e-30), 0, 1)
    closest = projected + along[:, :, None] * edges
    distance2 = np.einsum('fij,fij->fi', closest, closest).min(axis=1)
    sides = np.cross(edges, -projected) @ normal
    inside = np.all(sides >= -1e-12, axis=1) | np.all(sides <= 1e-12, axis=1)
    selected = inside | (distance2 <= radius**2)
    ids = face_ids[selected]
    if not len(ids):
        raise SimulationError("That selected area missed the quick mesh. Paint a larger area on the part.")
    if np.linalg.norm(volume.nodes[volume.surface] - center, axis=1).min() > volume.pitch * 1.8:
        raise SimulationError("A selected area is outside the part. Place it on the model surface.")
    if active_cells is not None:
        ids = ids[active_cells[volume.boundary_cells[ids]]]
        if not len(ids):
            raise SimulationError("The selected area covers an empty printed region. Select a wall or add solid layers.")
    return ids, radius


def patch_nodes(volume: VolumeMesh, patch: Patch, *, active_cells: np.ndarray | None = None) -> tuple[np.ndarray, float]:
    faces, radius = patch_faces(volume, patch, active_cells=active_cells)
    return np.unique(volume.boundary_faces[faces]), radius


def patch_load(volume: VolumeMesh, patch: Patch, *, active_cells: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Consistent nodal forces for uniform traction on selected boundary faces."""
    faces, radius = patch_faces(volume, patch, active_cells=active_cells)
    # The compatible face functions are bilinear: a constant traction gives
    # area/4 at each corner, including rectangular cells and inclined patches.
    selected = volume.boundary_faces[faces]
    areas = volume.boundary_areas[faces]
    nodes, inverse = np.unique(selected, return_inverse=True)
    weights = np.bincount(inverse.ravel(), weights=np.repeat(areas/4, 4))
    return nodes, weights / weights.sum(), radius, float(areas.sum())


def check_supports(nodes: np.ndarray, fixed: np.ndarray) -> None:
    # Check whether supports remove all three translations and rotations.
    positions = nodes[fixed] - nodes.mean(axis=0)
    positions /= max(float(np.ptp(nodes, axis=0).max()), 1e-6)
    rigid = np.zeros((len(fixed), 3, 6))
    rigid[:, :, :3] = np.eye(3)
    x, y, z = positions.T
    rigid[:, 0, 4], rigid[:, 0, 5] = z, -y
    rigid[:, 1, 3], rigid[:, 1, 5] = -z, x
    rigid[:, 2, 3], rigid[:, 2, 4] = y, -x
    if np.linalg.matrix_rank(rigid.reshape(-1, 6), tol=1e-8) < 6:
        raise SimulationError("The part can still move freely. Hold a larger area or add another hold.")


def check_printed_components(volume: VolumeMesh, cells: np.ndarray, fixed: np.ndarray) -> None:
    """Every face-connected printed region must have its own stable support.

    Zero infill can separate a cavity skin from the outside shell. An unloaded
    floating region may evade residual checks, so detect it before solving.
    """
    indices = np.rint((volume.nodes[cells[:, 0]]-volume.nodes.min(axis=0))
                      / volume.cell_size).astype(int)
    lookup = {tuple(index): i for i, index in enumerate(indices)}
    rows, columns = [], []
    for i, index in enumerate(indices):
        for axis in range(3):
            neighbor = index.copy()
            neighbor[axis] += 1
            j = lookup.get(tuple(neighbor))
            if j is not None:
                rows.append(i)
                columns.append(j)
    graph = coo_matrix((np.ones(len(rows)), (rows, columns)), shape=(len(cells), len(cells)))
    count, labels = connected_components(graph, directed=False)
    if count == 1:
        return
    owners = np.full(len(volume.nodes), -1, dtype=int)
    for component in range(count):
        node_ids = np.unique(cells[labels == component])
        if np.any(owners[node_ids] != -1):
            raise SimulationError("Printed shells touch only at a mesh edge or corner. "
                                  "Refine the mesh or connect the shells with infill.")
        owners[node_ids] = component
        local_fixed = np.flatnonzero(np.isin(node_ids, fixed))
        try:
            if not len(local_fixed):
                raise SimulationError("No support")
            check_supports(volume.nodes[node_ids], local_fixed)
        except SimulationError as error:
            raise SimulationError("The printed shells are disconnected at these infill settings. "
                                  "Each region needs a stable hold; add infill or connect the shells.") from error


def solve(volume: VolumeMesh, mesh: TriangleMesh, material: dict, request: SimulationRequest) -> dict:
    d, solid_properties = solid_elasticity(material, request.print_settings.orientation)
    print_model = cell_print_model(volume, request.print_settings)
    factors = print_model.stiffness_factor
    mean_factor = float(factors.mean())
    properties = {"solid_fraction": float(print_model.solid_fraction.mean()),
                  **{key:value*mean_factor for key,value in solid_properties.items()},
                  "summary": "volume_mean_cell_moduli", "print_model": print_model.metadata}
    active = factors > 0
    cells = volume.cells[active]
    active_nodes = np.unique(cells)
    holds = [patch_nodes(volume, patch, active_cells=active) for patch in request.fixtures]
    fixed_sets = [nodes for nodes, _ in holds]
    fixed = np.unique(np.concatenate(fixed_sets))
    check_supports(volume.nodes, fixed)
    if not active.all():
        check_printed_components(volume, cells, fixed)
    loaded, weights, actual_radius, load_area = patch_load(volume, request.load, active_cells=active)
    # A force on an immobile node disappears directly into the constraint.
    # Reject overlaps instead of producing a misleading near-zero heatmap.
    if np.intersect1d(fixed, loaded).size:
        raise SimulationError("The pull area overlaps a held area on the quick mesh. Move the pull farther away or use a smaller hold.")
    direction = np.array(normalized_vector(request.direction, "The pull needs a finite, nonzero direction."))
    force_n = force_newtons(request.magnitude, request.unit)
    kernel = incompatible_hex(volume.cell_size, d)
    dofs = (cells[:, :, None] * 3 + np.arange(3)).reshape(-1, 24)
    k_local = factors[active, None, None]*kernel.stiffness
    rows = np.broadcast_to(dofs[:, :, None], k_local.shape).ravel()
    cols = np.broadcast_to(dofs[:, None, :], k_local.shape).ravel()
    n_dof = 3 * len(volume.nodes)
    stiffness = coo_matrix((k_local.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()
    # Release the large assembly arrays before sparse factorization peaks.
    del k_local, rows, cols
    force = np.zeros((len(volume.nodes), 3))
    force[loaded] = weights[:, None] * direction * force_n
    fixed_dof = (fixed[:, None] * 3 + np.arange(3)).ravel()
    free = np.zeros(n_dof, dtype=bool)
    free[(active_nodes[:, None]*3+np.arange(3)).ravel()] = True
    free[fixed_dof] = False
    u = np.zeros(n_dof)
    with warnings.catch_warnings():
        warnings.simplefilter("error", MatrixRankWarning)
        try:
            u[free] = spsolve(stiffness[free][:, free], force.ravel()[free])
        except MatrixRankWarning as error:
            raise SimulationError("The part isn't held securely enough to solve. Paint a larger hold.") from error
    if not np.isfinite(u).all():
        raise SimulationError("The solver couldn't find a stable result. Try larger held areas.")
    residual = stiffness @ u - force.ravel()
    relative_residual = np.linalg.norm(residual[free] / force_n)
    reaction = residual.reshape(-1, 3)[fixed].sum(axis=0)
    relative_force_balance = float(np.linalg.norm(reaction / force_n + direction))
    moment_origin = volume.nodes.mean(axis=0)
    reaction_moment = np.cross(volume.nodes[fixed]-moment_origin, residual.reshape(-1, 3)[fixed]).sum(axis=0)
    applied_moment = np.cross(volume.nodes-moment_origin, force).sum(axis=0)
    moment_scale = force_n * float(np.ptp(volume.nodes, axis=0).max())
    relative_moment_balance = float(np.linalg.norm(reaction_moment+applied_moment) / moment_scale)
    strain_energy = float(.5 * u @ (residual + force.ravel()))
    external_work = float(u @ force.ravel())
    relative_energy_error = abs(2*strain_energy-external_work) / max(abs(external_work), np.finfo(float).tiny)
    checks = [relative_residual, relative_force_balance, relative_moment_balance, relative_energy_error]
    if not np.isfinite(checks).all() or max(checks) > 1e-5 or strain_energy <= 0:
        raise SimulationError("The solver couldn't balance the load reliably. Try larger held areas.")
    # Recover each cell's enhanced corner strains, then average the resulting
    # scalar invariant at shared nodes. Material interfaces remain approximate.
    strain = np.einsum('qij,ej->eqi', kernel.corner_b, u[dofs])
    stress = (strain @ d.T)*factors[active, None, None]
    xx, yy, zz, xy, yz, xz = np.moveaxis(stress, -1, 0)
    von_mises = np.sqrt(np.maximum(0, 0.5*((xx-yy)**2+(yy-zz)**2+(zz-xx)**2) + 3*(xy**2+yz**2+xz**2)))
    sums = np.bincount(cells.ravel(), weights=von_mises.ravel(), minlength=len(volume.nodes))
    counts = np.bincount(cells.ravel(), minlength=len(volume.nodes))
    nodal_stress = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    # Surface-node fields are interpolated onto the original STL client-side.
    ids = volume.surface
    surface_index = np.empty(len(volume.nodes), dtype=int)
    surface_index[ids] = np.arange(len(ids))
    displacement = u.reshape(-1, 3)
    max_u = float(np.linalg.norm(displacement, axis=1).max())
    scale = max(float(np.percentile(nodal_stress[ids], 98)), 1e-12)
    messages = ["Quick mesh: details smaller than about two mesh cells may be lost.",
                "Enhanced brick elements improve bending; compare finer meshes for this geometry and contact setup.",
                "Contacts use whole mesh faces; sampled areas can extend beyond the painted circles.",
                "Holds fix selected mesh nodes; screw contact, slip and wall flexibility are not modeled."]
    messages.extend(print_model.metadata["notes"])
    if material["family"].startswith(("TPU", "TPC", "PEBA")):
        messages.append("Flexible materials often need a nonlinear model; this is only a small-strain preview.")
    if max_u > mesh.extents.max() * 0.02:
        messages.append("Movement is large for a linear model. Use a smaller load to explore this shape.")
    if actual_radius > request.load.radius:
        messages.append("The pull patch was enlarged to cover several mesh nodes.")
    if any(radius > patch.radius for (_, radius), patch in zip(holds, request.fixtures)):
        messages.append("At least one hold patch was enlarged to cover several mesh nodes.")
    if material["physics"]["nu"] > .45:
        messages.append("Nearly incompressible material: this displacement formulation can still be artificially stiff.")
    voxel_volume = float(len(volume.cells)*np.prod(volume.cell_size))
    relative_volume_error = (voxel_volume / abs(mesh.volume)) - 1
    if abs(relative_volume_error) > .2:
        messages.append("The quick mesh changes solid volume by more than 20%; refine before interpreting these values.")
    if mesh.extents.min() < 2*volume.pitch:
        messages.append("The part's smallest overall dimension spans fewer than two cells; thickness and bending are poorly resolved.")
    return {
        "positions": volume.nodes[ids].tolist(),
        "surface_triangles": surface_index[volume.boundary_triangles].tolist(),
        "surface_quads": surface_index[volume.boundary_faces].tolist(),
        "stress": nodal_stress[ids].tolist(),
        "displacement": displacement[ids].tolist(),
        "heatmap_scale_mpa": scale,
        "max_displacement_mm": max_u,
        "force_newtons": force_n,
        "mesh": {"nodes": len(volume.nodes), "elements": len(cells), "cell_mm": volume.pitch,
                 "cell_size_mm": volume.cell_size.tolist(), "element_type": ELEMENT_TYPE,
                 "active_nodes": len(active_nodes), "void_cells": int((~active).sum()),
                 "voxel_volume_mm3": voxel_volume, "relative_volume_error": relative_volume_error},
        "patches": {"fixed_nodes": len(fixed), "loaded_nodes": len(loaded), "load_radius_mm": actual_radius,
                    "hold_radii_mm": [radius for _, radius in holds], "load_area_mm2": load_area,
                    "load_centroid_mm": (weights @ volume.nodes[loaded]).tolist()},
        "checks": {"relative_residual": float(relative_residual), "relative_force_balance": relative_force_balance,
                   "relative_moment_balance": relative_moment_balance, "reaction_newtons": reaction.tolist(),
                   "strain_energy_n_mm": strain_energy, "external_work_n_mm": external_work,
                   "relative_energy_error": relative_energy_error},
        "effective_material": properties,
        "notes": messages,
    }
