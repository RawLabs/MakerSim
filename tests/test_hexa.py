"""Independent patch, energy, nullspace and beam references for the Wilson brick."""

import numpy as np
import pytest
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

from backend.hexa import NATURAL_CORNERS, incompatible_hex


def isotropic_elasticity(young=1000., poisson=.25):
    shear = young/(2*(1+poisson))
    lame = young*poisson/((1+poisson)*(1-2*poisson))
    d = np.diag([2*shear]*3+[shear]*3)
    d[:3, :3] += lame
    return d


def brick_grid(counts, size, d):
    """Independent structured assembly for element reference tests."""
    shape = np.asarray(counts)+1
    lattice = np.indices(shape).reshape(3, -1).T
    corners = ((NATURAL_CORNERS+1)/2).astype(int)
    origins = np.indices(counts).reshape(3, -1).T
    cells = np.ravel_multi_index((origins[:, None, :]+corners).transpose(2, 0, 1), shape)
    nodes = lattice*np.asarray(size)
    kernel = incompatible_hex(size, d)
    dofs = (cells[:, :, None]*3+np.arange(3)).reshape(-1, 24)
    rows = np.broadcast_to(dofs[:, :, None], (len(cells), 24, 24)).ravel()
    columns = np.broadcast_to(dofs[:, None, :], (len(cells), 24, 24)).ravel()
    stiffness = coo_matrix((np.tile(kernel.stiffness.ravel(), len(cells)), (rows, columns)),
                          shape=(len(nodes)*3, len(nodes)*3)).tocsr()
    return nodes, cells, stiffness, kernel


@pytest.mark.parametrize("size", [2., [8., 3., 5.]])
def test_affine_strain_and_uniform_energy_are_exact(size):
    random = np.random.default_rng(713).normal(size=(6, 6))
    d = random.T@random+np.eye(6)*100  # General SPD anisotropy, including couplings.
    kernel = incompatible_hex(size, d)
    points = NATURAL_CORNERS*kernel.cell_size/2
    gradient = np.array([[.01, .02, .03], [.04, .05, .06], [.07, .08, .09]])
    u = (points@gradient.T+[1., 2., 3.]).ravel()
    expected = np.array([.01, .05, .09, .06, .14, .10])
    assert kernel.internal_recovery@u == pytest.approx(np.zeros(9), abs=1e-14)
    assert kernel.gauss_b@u == pytest.approx(np.tile(expected, (8, 1)), abs=1e-14)
    assert kernel.corner_b@u == pytest.approx(np.tile(expected, (8, 1)), abs=1e-14)
    reference = .5*np.prod(kernel.cell_size)*expected@d@expected
    assert .5*u@kernel.stiffness@u == pytest.approx(reference, rel=1e-11)


@pytest.mark.parametrize("poisson", [0., .25, .48, .4999])
def test_only_six_rigid_modes_have_zero_energy(poisson):
    kernel = incompatible_hex([4., 2., 3.], isotropic_elasticity(poisson=poisson))
    eigenvalues = np.linalg.eigvalsh(kernel.stiffness)
    tolerance = eigenvalues[-1]*1e-10
    assert np.abs(eigenvalues[:6]).max() < tolerance
    assert eigenvalues[6] > tolerance
    assert eigenvalues.min() > -tolerance
    points = NATURAL_CORNERS*kernel.cell_size/2
    for axis in np.eye(3):
        for rigid in (np.broadcast_to(axis, points.shape), np.cross(axis, points)):
            assert kernel.gauss_b@rigid.ravel() == pytest.approx(np.zeros((8, 6)), abs=1e-12)
            assert kernel.stiffness@rigid.ravel() == pytest.approx(np.zeros(24), abs=1e-7)


@pytest.mark.parametrize("longitudinal,bending", [(0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1)])
def test_pure_bending_recovers_linear_stress_and_exact_energy(longitudinal, bending):
    young, poisson, curvature = 1000., .25, .02
    d = isotropic_elasticity(young, poisson)
    kernel = incompatible_hex([8., 3., 5.], d)
    points = NATURAL_CORNERS*kernel.cell_size/2
    remaining = 3-longitudinal-bending
    x, y, z = points[:, [longitudinal, bending, remaining]].T
    u = np.zeros((8, 3))
    u[:, longitudinal] = -curvature*x*y
    u[:, bending] = .5*curvature*(x*x+poisson*(y*y-z*z))
    u[:, remaining] = poisson*curvature*y*z
    strain = kernel.gauss_b@u.ravel()
    stress = strain@d.T
    expected = np.zeros((8, 6))
    expected[:, longitudinal] = -young*curvature*kernel.gauss_points[:, bending]*kernel.cell_size[bending]/2
    assert stress == pytest.approx(expected, abs=1e-10)
    corner_stress = (kernel.corner_b@u.ravel())@d.T
    expected[:, longitudinal] = -young*curvature*points[:, bending]
    assert corner_stress == pytest.approx(expected, abs=1e-10)
    reference = young*curvature**2*np.prod(kernel.cell_size)*kernel.cell_size[bending]**2/24
    assert .5*u.ravel()@kernel.stiffness@u.ravel() == pytest.approx(reference, rel=1e-12)
    assert .5*np.einsum('qi,qi,q->', strain, stress, kernel.quadrature_weights) == pytest.approx(reference)


def test_condensation_recovery_is_invariant_under_uniform_modulus_scaling():
    d = isotropic_elasticity()
    a, b = incompatible_hex([2., 3., 4.], d), incompatible_hex([2., 3., 4.], d*.07)
    assert b.stiffness == pytest.approx(a.stiffness*.07)
    assert b.gauss_b == pytest.approx(a.gauss_b, abs=1e-14)
    assert b.corner_b == pytest.approx(a.corner_b, abs=1e-14)
    assert b.internal_recovery == pytest.approx(a.internal_recovery, abs=1e-14)


def test_assembled_rectangular_mesh_passes_affine_patch_test():
    nodes, _, stiffness, _ = brick_grid((2, 2, 2), [3., 2., 4.], isotropic_elasticity())
    gradient = np.array([[.01, .03, -.02], [.04, -.01, .02], [.03, -.04, .01]])
    expected = (nodes@gradient.T+[1., 2., 3.]).ravel()
    boundary = np.any((nodes == 0)|(nodes == nodes.max(axis=0)), axis=1)
    free = np.repeat(~boundary, 3)
    u = expected.copy()
    u[free] = spsolve(stiffness[free][:, free], -stiffness[free][:, ~free]@expected[~free])
    assert u == pytest.approx(expected, abs=1e-12)


def test_oblique_bending_energy_converges_under_refinement():
    # The exact nodal samples of a quadratic bending field do not determine
    # its complete interior on one coarse incompatible cell. Unlike bending
    # along cell axes, oblique bending is not claimed to be reproduced exactly.
    # Its energy must approach the continuum energy on a fixed physical domain.
    rotation = np.array([[1, 1, 0], [-1, 1, 0], [0, 0, np.sqrt(2)]])/np.sqrt(2)
    reference = 1000*8*4/24  # .5 E integral(y_local²) dV, curvature=1.
    errors = []
    for count in (2, 4, 8):
        nodes, _, stiffness, _ = brick_grid((count,)*3, 2/count, isotropic_elasticity())
        x, y, z = ((nodes-1)@rotation.T).T
        local_u = np.column_stack([-x*y, .5*(x*x+.25*(y*y-z*z)), .25*y*z])
        u = (local_u@rotation).ravel()
        energy = .5*u@stiffness@u
        errors.append(abs(energy/reference-1))
    assert errors[2] < errors[1] < errors[0]
    assert errors[-1] < .02


def cantilever(counts):
    dimensions = np.array([80., 10., 10.])
    d = isotropic_elasticity()
    nodes, cells, stiffness, kernel = brick_grid(counts, dimensions/np.array(counts), d)
    # Exact integration of constant traction on each bilinear x-max face.
    tip_faces = cells[np.all(nodes[cells[:, [1, 2, 6, 5]], 0] == dimensions[0], axis=1)][:, [1, 2, 6, 5]]
    weights = np.bincount(tip_faces.ravel(), minlength=len(nodes)).astype(float)
    weights /= weights.sum()
    force = np.zeros_like(nodes)
    force[:, 2] = -weights
    fixed = nodes[:, 0] == 0
    free = np.repeat(~fixed, 3)
    u = np.zeros(nodes.size)
    u[free] = spsolve(stiffness[free][:, free], force.ravel()[free])
    residual = (stiffness@u-force.ravel()).reshape(-1, 3)
    reaction = residual[fixed].sum(axis=0)
    moment = np.cross(nodes[fixed], residual[fixed]).sum(axis=0)+np.cross(nodes, force).sum(axis=0)
    return -weights@u.reshape(-1, 3)[:, 2], reaction, moment, .5*u@stiffness@u, u@force.ravel()


def test_thin_cantilever_matches_beam_reference_without_stiffness_correction():
    # Timoshenko estimate for L/b=8, with k=5/6 and the stated E and nu.
    # A continuum clamp has local 3D effects, so this is an approximate
    # benchmark. The element's pure-bending energy test above is exact.
    reference = 80**3/(3*1000*(10*10**3/12))+80/((5/6)*400*100)
    for counts in ((8, 1, 1), (16, 2, 2), (32, 4, 4)):
        tip, reaction, moment, strain_energy, external_work = cantilever(counts)
        assert tip == pytest.approx(reference, rel=.03)
        assert reaction == pytest.approx([0, 0, 1], abs=1e-9)
        assert moment == pytest.approx(np.zeros(3), abs=1e-8)
        assert 2*strain_energy == pytest.approx(external_work, rel=1e-9)


@pytest.mark.parametrize("size", [0, -1, np.nan, [1, 2], [1, np.inf, 3]])
def test_invalid_cell_sizes_are_rejected(size):
    with pytest.raises(ValueError, match="positive edge lengths"):
        incompatible_hex(size, isotropic_elasticity())


@pytest.mark.parametrize("d", [np.eye(3), np.eye(6)*-1, np.eye(6)*np.nan, np.triu(np.ones((6, 6)))])
def test_invalid_elasticity_is_rejected(d):
    with pytest.raises(ValueError, match="elasticity"):
        incompatible_hex(1., d)
