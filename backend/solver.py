"""Coarse, conforming linear tetrahedral elasticity, in mm / N / MPa.

Closed STL -> filled voxel grid -> six tets per cell -> sparse elasticity.
The staircase volume is an approximation, not a geometry-conforming mesh.
No artificial stiffness, point loads, or unconstrained floating components.
"""
from dataclasses import dataclass
import warnings

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import MatrixRankWarning, spsolve

from .schemas import Patch, PrintSettings, SimulationRequest, force_newtons, normalized_vector
from .geometry import TriangleMesh, voxelize

MAX_CELLS = 4800
CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
# Same face diagonals in adjacent cells, for conforming tetrahedra.
TETS = np.array([[0, 1, 2, 6], [0, 2, 3, 6], [0, 3, 7, 6],
                 [0, 7, 4, 6], [0, 4, 5, 6], [0, 5, 1, 6]])
FACES = [(0, -1, [0, 3, 7, 4]), (0, 1, [1, 2, 6, 5]),
         (1, -1, [0, 1, 5, 4]), (1, 1, [3, 2, 6, 7]),
         (2, -1, [0, 1, 2, 3]), (2, 1, [4, 5, 6, 7])]


class SimulationError(ValueError):
    pass


@dataclass
class VolumeMesh:
    nodes: np.ndarray
    cells: np.ndarray
    tets: np.ndarray
    surface: np.ndarray
    node_labels: np.ndarray
    components: int
    pitch: float
    b: np.ndarray
    volumes: np.ndarray


def strain_matrix(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Linear shape gradients; Voigt order xx yy zz xy yz xz (engineering shear)."""
    interpolation = np.concatenate([np.ones((*points.shape[:2], 1)), points], axis=2)
    gradients = np.linalg.inv(interpolation)[:, 1:, :]
    b = np.zeros((len(points), 6, 12))
    for i in range(4):
        dx, dy, dz = gradients[:, :, i].T
        j = 3 * i
        b[:, 0, j] = dx
        b[:, 1, j+1] = dy
        b[:, 2, j+2] = dz
        b[:, 3, j] = dy
        b[:, 3, j+1] = dx
        b[:, 4, j+1] = dz
        b[:, 4, j+2] = dy
        b[:, 5, j] = dz
        b[:, 5, j+2] = dx
    volume = np.abs(np.linalg.det(points[:, 1:] - points[:, :1])) / 6
    return b, volume


def create_volume(mesh: TriangleMesh, resolution: int = 28) -> VolumeMesh:
    if not mesh.is_watertight:
        raise SimulationError("This STL has an open surface. Repair it before simulating.")
    if not mesh.has_consistent_winding:
        raise SimulationError("This STL has inconsistent triangle directions. Repair its normals in your slicer and export again before simulating.")
    try:
        mesh.validate_single_solid()
    except ValueError as error:
        raise SimulationError(str(error)) from error
    pitch = float(mesh.extents.max()) / resolution
    # Adaptive bounded meshing.
    for _ in range(8):
        occupancy, origin = voxelize(mesh, pitch)
        count = int(occupancy.sum())
        if count <= MAX_CELLS:
            break
        pitch *= max(1.12, (count / MAX_CELLS) ** (1/3) * 1.02)
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
        occupancy, origin = voxelize(mesh, pitch)
        count = int(occupancy.sum())
        if count > MAX_CELLS:
            raise SimulationError("Resolving this part's thin walls or connections exceeds the quick mesh limit. Try a thicker version or a simpler part.")
    indices = np.argwhere(occupancy)
    lattice, inverse = np.unique((indices[:, None, :] + CORNERS).reshape(-1, 3), axis=0, return_inverse=True)
    cells = inverse.reshape(-1, 8)
    nodes = origin + lattice * pitch
    tets = cells[:, TETS].reshape(-1, 4)
    boundary = []
    for axis, direction, corner_ids in FACES:
        neighbors = indices.copy()
        neighbors[:, axis] += direction
        valid = np.all((neighbors >= 0) & (neighbors < np.array(occupancy.shape)), axis=1)
        exposed = np.ones(len(indices), dtype=bool)
        exposed[valid] = ~occupancy[tuple(neighbors[valid].T)]
        boundary.append(cells[exposed][:, corner_ids].ravel())
    surface = np.unique(np.concatenate(boundary))
    b, volumes = strain_matrix(nodes[tets])
    return VolumeMesh(nodes, cells, tets, surface, np.ones(len(nodes), dtype=int), components, pitch, b, volumes)


def printed_elasticity(material: dict, settings: PrintSettings, mesh: TriangleMesh) -> tuple[np.ndarray, dict]:
    if not mesh.has_consistent_winding:
        raise SimulationError("This STL has inconsistent triangle directions. Repair its normals in your slicer and export again before simulating.")
    # Fixed 0.45 mm extrusion / 0.20 mm layers in v1, explained in the UI.
    characteristic_thickness = max(0.5, 2 * abs(mesh.volume) / mesh.area)
    shell = min(1.0, (2 * settings.walls * 0.45 + (settings.top_layers + settings.bottom_layers) * 0.2) / characteristic_thickness)
    fraction = shell + (1-shell) * settings.infill / 100
    stiffness_factor = 0.1 + 0.9 * fraction ** 1.8
    physics = material["physics"]
    e_xy = physics["E_xy_mpa"] * stiffness_factor
    # Generic entries have no measured Z modulus. This is an explicit heuristic.
    z_ratio = min(1.0, physics["E_z_mpa"] / physics["E_xy_mpa"])
    axis = {"x": 0, "y": 1, "z": 2}[settings.orientation]
    e = np.full(3, e_xy)
    e[axis] *= z_ratio
    nu = min(physics["nu"], 0.45)
    # Symmetric positive definite orthotropic compliance; pairwise couplings
    # use geometric mean moduli. Shear involving build axis is likewise reduced.
    compliance = np.zeros((6, 6))
    for i in range(3):
        compliance[i, i] = 1 / e[i]
        for j in range(i+1, 3):
            compliance[i, j] = compliance[j, i] = -nu / np.sqrt(e[i]*e[j])
    for k, (i, j) in enumerate([(0, 1), (1, 2), (0, 2)], start=3):
        g = physics["G_xy_mpa"] * stiffness_factor
        if axis in (i, j):
            g *= np.sqrt(z_ratio)
        compliance[k, k] = 1 / g
    return np.linalg.inv(compliance), {"solid_fraction": fraction, "modulus_xy_mpa": e_xy, "modulus_build_mpa": e[axis]}


def patch_nodes(volume: VolumeMesh, patch: Patch) -> tuple[np.ndarray, float]:
    points = volume.nodes[volume.surface]
    center = np.array(patch.point)
    normal = np.array(normalized_vector(patch.normal, "A selected area needs a finite, nonzero surface normal."))
    delta = points - center
    depth = delta @ normal
    tangent = np.linalg.norm(delta - depth[:, None] * normal, axis=1)
    # At least a cell face, rather than silently collapsing to a single node.
    radius = max(patch.radius, volume.pitch * 1.1)
    selected = (tangent <= radius) & (np.abs(depth) <= volume.pitch * 0.85)
    ids = volume.surface[selected]
    if len(ids) < 3:
        raise SimulationError("That selected area missed the quick mesh. Paint a larger area on the part.")
    if np.linalg.norm(delta, axis=1).min() > volume.pitch * 1.8:
        raise SimulationError("A selected area is outside the part. Place it on the model surface.")
    return ids, radius


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


def solve(volume: VolumeMesh, mesh: TriangleMesh, material: dict, request: SimulationRequest) -> dict:
    d, properties = printed_elasticity(material, request.print_settings, mesh)
    fixed_sets = [patch_nodes(volume, patch)[0] for patch in request.fixtures]
    fixed = np.unique(np.concatenate(fixed_sets))
    check_supports(volume.nodes, fixed)
    loaded, actual_radius = patch_nodes(volume, request.load)
    # A force on an immobile node disappears directly into the constraint.
    # Reject overlaps instead of producing a misleading near-zero heatmap.
    if np.intersect1d(fixed, loaded).size:
        raise SimulationError("The pull area overlaps a held area on the quick mesh. Move the pull farther away or use a smaller hold.")
    direction = np.array(normalized_vector(request.direction, "The pull needs a finite, nonzero direction."))
    force_n = force_newtons(request.magnitude, request.unit)
    delta = volume.nodes[loaded] - np.array(request.load.point)
    weights = np.maximum(0.08, 1 - np.linalg.norm(delta, axis=1) / (actual_radius + volume.pitch))
    weights /= weights.sum()
    dofs = (volume.tets[:, :, None] * 3 + np.arange(3)).reshape(-1, 12)
    k_local = np.einsum('eai,ab,ebj,e->eij', volume.b, d, volume.b, volume.volumes, optimize=True)
    rows = np.broadcast_to(dofs[:, :, None], k_local.shape).ravel()
    cols = np.broadcast_to(dofs[:, None, :], k_local.shape).ravel()
    n_dof = 3 * len(volume.nodes)
    stiffness = coo_matrix((k_local.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()
    force = np.zeros((len(volume.nodes), 3))
    force[loaded] = weights[:, None] * direction * force_n
    fixed_dof = (fixed[:, None] * 3 + np.arange(3)).ravel()
    free = np.ones(n_dof, dtype=bool)
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
    if not np.isfinite(relative_residual) or not np.isfinite(relative_force_balance) or max(relative_residual, relative_force_balance) > 1e-5:
        raise SimulationError("The solver couldn't balance the load reliably. Try larger held areas.")
    strain = np.einsum('eij,ej->ei', volume.b, u[dofs])
    stress = strain @ d.T
    xx, yy, zz, xy, yz, xz = stress.T
    von_mises = np.sqrt(np.maximum(0, 0.5*((xx-yy)**2+(yy-zz)**2+(zz-xx)**2) + 3*(xy**2+yz**2+xz**2)))
    sums = np.bincount(volume.tets.ravel(), weights=np.repeat(von_mises * volume.volumes, 4), minlength=len(volume.nodes))
    volumes = np.bincount(volume.tets.ravel(), weights=np.repeat(volume.volumes, 4), minlength=len(volume.nodes))
    nodal_stress = sums / volumes
    # Surface-node fields are interpolated onto the original STL client-side.
    ids = volume.surface
    displacement = u.reshape(-1, 3)
    max_u = float(np.linalg.norm(displacement, axis=1).max())
    scale = max(float(np.percentile(nodal_stress[ids], 98)), 1e-12)
    messages = ["Quick mesh: details smaller than about two mesh cells may be lost.",
                "Print settings use a heuristic effective material, not individual infill strands."]
    if material["family"].startswith(("TPU", "TPC", "PEBA")):
        messages.append("Flexible materials often need a nonlinear model; this is only a small-strain preview.")
    if max_u > mesh.extents.max() * 0.02:
        messages.append("Movement is large for a linear model. Use a smaller load to explore this shape.")
    if actual_radius > request.load.radius:
        messages.append("The pull patch was enlarged to cover several mesh nodes.")
    return {
        "positions": volume.nodes[ids].tolist(),
        "stress": nodal_stress[ids].tolist(),
        "displacement": displacement[ids].tolist(),
        "heatmap_scale_mpa": scale,
        "max_displacement_mm": max_u,
        "force_newtons": force_n,
        "mesh": {"nodes": len(volume.nodes), "elements": len(volume.tets), "cell_mm": volume.pitch},
        "patches": {"fixed_nodes": len(fixed), "loaded_nodes": len(loaded), "load_radius_mm": actual_radius},
        "checks": {"relative_residual": float(relative_residual), "relative_force_balance": relative_force_balance, "reaction_newtons": reaction.tolist()},
        "effective_material": properties,
        "notes": messages,
    }
