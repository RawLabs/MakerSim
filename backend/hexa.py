"""Wilson's nine-mode incompatible brick for rectangular, axis-aligned cells.

The 8 compatible trilinear nodes retain three translational DOFs each. Three
quadratic functions (1-xi², 1-eta², 1-zeta²), in each displacement direction,
add nine element-local modes that are eliminated by static condensation.
The full 2x2x2 Gauss rule integrates the stiffness exactly for a rectangular
cell with constant elasticity. No reduced integration or stabilization is used.

For these constant-Jacobian cells the incompatible gradients have zero volume
mean, so an affine displacement field passes the constant-strain patch test.
Do not reuse this implementation for distorted bricks: those require corrected
incompatible gradients. This is the Wilson nine-mode element, not the thirteen-
mode Abaqus C3D8I formulation, and it is not a hybrid incompressible element.

Reference: E. L. Wilson, Three-Dimensional Static and Dynamic Analysis of
Structures, 3rd ed., chapter 6, especially sections 6.4 and 6.7 (2002):
https://edwilson.org/bookshelf/threedimensional.pdf
"""

from dataclasses import dataclass

import numpy as np


ELEMENT_TYPE = "hexa8_wilson9"
# Same corner order as the voxel mesh; components are global x, y, z.
NATURAL_CORNERS = np.array([
    [-1., -1., -1.], [1., -1., -1.], [1., 1., -1.], [-1., 1., -1.],
    [-1., -1., 1.], [1., -1., 1.], [1., 1., 1.], [-1., 1., 1.],
])


@dataclass(frozen=True)
class HexaKernel:
    """One reusable constant-material kernel; engineering shear order is xy,yz,xz.

    ``gauss_b`` and ``corner_b`` include the recovered internal modes. For an
    element displacement vector u, strain is ``b @ u``, stress is ``d @ strain``
    and the condensed modal amplitudes are ``internal_recovery @ u``. Corner
    stresses are element-side extrapolated fields, not additional global DOFs.
    Uniformly scaling d scales stiffness but leaves the recovery matrices fixed.
    """

    stiffness: np.ndarray
    gauss_b: np.ndarray
    corner_b: np.ndarray
    quadrature_weights: np.ndarray
    internal_recovery: np.ndarray
    gauss_points: np.ndarray
    cell_size: np.ndarray


def _strain_matrix(gradients):
    """Convert shape gradients [point,shape,xyz] to engineering-strain matrices."""
    b = np.zeros((len(gradients), 6, gradients.shape[1]*3))
    for node in range(gradients.shape[1]):
        dx, dy, dz = gradients[:, node].T
        column = 3*node
        b[:, 0, column] = dx
        b[:, 1, column+1] = dy
        b[:, 2, column+2] = dz
        b[:, 3, column], b[:, 3, column+1] = dy, dx
        b[:, 4, column+1], b[:, 4, column+2] = dz, dy
        b[:, 5, column], b[:, 5, column+2] = dz, dx
    return b


def _kinematics(points, cell_size):
    factors = 1+points[:, None, :]*NATURAL_CORNERS[None]
    gradients = np.empty_like(factors)
    for axis in range(3):
        others = [i for i in range(3) if i != axis]
        gradients[:, :, axis] = (NATURAL_CORNERS[:, axis]
            * factors[:, :, others[0]] * factors[:, :, others[1]] / (4*cell_size[axis]))
    incompatible = np.zeros((len(points), 3, 3))
    for axis in range(3):
        incompatible[:, axis, axis] = -4*points[:, axis]/cell_size[axis]
    return _strain_matrix(gradients), _strain_matrix(incompatible)


def incompatible_hex(cell_size, d):
    """Build a 24-DOF rectangular Wilson brick with constant SPD elasticity.

    ``cell_size`` is a positive scalar or three lengths in mm. ``d`` is the
    symmetric positive-definite 6x6 elasticity matrix in MPa with Voigt order
    xx, yy, zz, xy, yz, xz and engineering shear strains. Constant surface
    traction uses the compatible bilinear face functions (area/4 per corner).
    There are no externally loaded internal DOFs in this assumed-strain model.
    """
    size = np.asarray(cell_size, dtype=float)
    if size.ndim == 0:
        size = np.repeat(size, 3)
    if size.shape != (3,) or not np.isfinite(size).all() or np.any(size <= 0):
        raise ValueError("A brick needs three finite, positive edge lengths.")
    elasticity = np.asarray(d, dtype=float)
    if (elasticity.shape != (6, 6) or not np.isfinite(elasticity).all()
            or not np.allclose(elasticity, elasticity.T, rtol=1e-12, atol=0)):
        raise ValueError("Brick elasticity must be a finite symmetric 6x6 matrix.")
    try:
        np.linalg.cholesky(elasticity)
    except np.linalg.LinAlgError as error:
        raise ValueError("Brick elasticity must be positive definite.") from error
    points = NATURAL_CORNERS/np.sqrt(3)
    weights = np.full(8, np.prod(size)/8)
    compatible, incompatible = _kinematics(points, size)
    k_aa = np.einsum('qai,ab,qbj,q->ij', incompatible, elasticity, incompatible, weights)
    k_au = np.einsum('qai,ab,qbj,q->ij', incompatible, elasticity, compatible, weights)
    recovery = -np.linalg.solve(k_aa, k_au)
    gauss_b = compatible+incompatible @ recovery
    # This Gram form equals K_uu-K_ua inv(K_aa) K_au, while avoiding
    # cancellation between two large stiffness matrices after condensation.
    stiffness = np.einsum('qai,ab,qbj,q->ij', gauss_b, elasticity, gauss_b, weights)
    stiffness = (stiffness+stiffness.T)/2
    corner_compatible, corner_incompatible = _kinematics(NATURAL_CORNERS, size)
    corner_b = corner_compatible+corner_incompatible @ recovery
    return HexaKernel(stiffness, gauss_b, corner_b, weights, recovery, points, size.copy())
