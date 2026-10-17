"""Spatial shell/core estimates on the solver's rectangular voxel cells.

Perimeters occupy the two axes perpendicular to the build axis. Top and
bottom solid layers occupy separate distances along that axis. Contiguous
solid runs include local boundaries and enclosed cavities, not only the
model's outer bounding box. Clipped intervals integrate the directional
shell fraction within each cell analytically, including sub-cell shells.

This is a staircase, axis-direction approximation to a slicer's contours.
It does not reconstruct extrusion paths, corner offsets, overlap, variable
line widths, extra perimeters, or solid infill added by a slicer.

The default core and mixed-cell stiffness use the uniform-strain (Voigt)
upper-bound assumption, D_core = infill_fraction * D_solid. This is not a
fitted infill law. Actual pattern-dependent stiffness needs representative
testing or homogenization; no positive stiffness floor is added to voids.

Sources:
https://help.prusa3d.com/article/layers-and-perimeters_1748
https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication960-11.pdf
https://pmc.ncbi.nlm.nih.gov/articles/PMC8468210/
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CellPrintModel:
    """Arrays follow volume.cells; metadata contains JSON-safe scalar summaries."""

    shell_fraction: np.ndarray
    solid_fraction: np.ndarray
    stiffness_factor: np.ndarray
    metadata: dict


def _run_distances(indices: np.ndarray, axis: int, cell_size: float):
    """Distances from each cell's two faces to the ends of its solid run."""
    transverse = [candidate for candidate in range(3) if candidate != axis]
    order = np.lexsort((indices[:,axis],indices[:,transverse[1]],indices[:,transverse[0]]))
    sorted_indices = indices[order]
    continuation = ((np.diff(sorted_indices[:,axis]) == 1)
                    & np.all(np.diff(sorted_indices[:,transverse],axis=0) == 0,axis=1))
    starts = np.flatnonzero(np.r_[True,~continuation])
    ends = np.r_[starts[1:]-1,len(indices)-1]
    lengths = ends-starts+1
    coordinate = sorted_indices[:,axis]
    lower = np.empty(len(indices))
    upper = np.empty(len(indices))
    lower[order] = (coordinate-np.repeat(coordinate[starts],lengths))*cell_size
    upper[order] = (np.repeat(coordinate[ends],lengths)-coordinate)*cell_size
    return lower,upper


def cell_print_model(volume, settings, *, core_stiffness_ratio: float | None = None) -> CellPrintModel:
    """Return spatial volume fractions and scalar multipliers of solid D.

    ``core_stiffness_ratio`` optionally supplies an externally measured or
    assumed core modulus relative to the solid material. It is restricted to
    [0, infill_fraction], the uniform-strain bound for that same material and
    nominal density. Supplying a number alone does not establish calibration.
    The returned factor multiplies all entries of the material stiffness
    tensor; infill-specific anisotropy is not inferred from a scalar.
    """
    size = np.asarray(getattr(volume,'cell_size',np.full(3,volume.pitch)),dtype=float)
    if size.shape != (3,) or not np.isfinite(size).all() or np.any(size <= 0):
        raise ValueError('Print modeling requires three finite, positive mesh cell sizes.')
    points = volume.nodes[volume.cells]
    if not len(points):
        raise ValueError('Print modeling requires at least one solid mesh cell.')
    lower = points.min(axis=1)
    if not np.allclose(points.max(axis=1)-lower,size,rtol=1e-8,atol=1e-10):
        raise ValueError('Print modeling requires uniformly sized rectangular mesh cells.')
    lattice = (lower-lower.min(axis=0))/size
    indices = np.rint(lattice).astype(np.int64)
    if not np.allclose(lattice,indices,rtol=0,atol=1e-7):
        raise ValueError('Print modeling requires cells on one rectangular grid.')

    build_axis = {'x':0,'y':1,'z':2}[settings.orientation]
    line_width = float(settings.line_width_mm)
    layer_height = float(settings.layer_height_mm)
    if not np.isfinite([line_width,layer_height]).all() or min(line_width,layer_height) <= 0:
        raise ValueError('Line width and layer height must be finite and positive.')
    wall = settings.walls*line_width
    top = settings.top_layers*layer_height
    bottom = settings.bottom_layers*layer_height
    density = float(settings.infill)/100
    if not np.isfinite([wall,top,bottom,density]).all() or min(wall,top,bottom) < 0 or not 0 <= density <= 1:
        raise ValueError('Print thicknesses and infill must be finite and within their allowed ranges.')
    supplied = core_stiffness_ratio is not None
    core_ratio = density if not supplied else float(core_stiffness_ratio)
    if not np.isfinite(core_ratio) or not 0 <= core_ratio <= density:
        raise ValueError('Relative core stiffness must be between zero and the infill fraction (the uniform-strain upper bound).')

    core = np.ones(len(indices))
    sub_line_width = np.zeros(len(indices),dtype=bool)
    sub_layer_height = np.zeros(len(indices),dtype=bool)
    for axis in range(3):
        distance_lower,distance_upper = _run_distances(indices,axis,size[axis])
        run_width = distance_lower+size[axis]+distance_upper
        if axis == build_axis:
            sub_layer_height |= run_width < layer_height*(1-1e-10)
        else:
            sub_line_width |= run_width < line_width*(1-1e-10)
        thickness_lower,thickness_upper = (bottom,top) if axis == build_axis else (wall,wall)
        # The remaining interval is exact even when two opposing shells meet
        # inside a cell. Multiplication avoids double-counting edge/corner shells.
        start = np.maximum(0,thickness_lower-distance_lower)
        end = np.minimum(size[axis],size[axis]+distance_upper-thickness_upper)
        core *= np.clip((end-start)/size[axis],0,1)
    shell = 1-core
    solid = shell+core*density
    stiffness = shell+core*core_ratio

    thicknesses = {'walls':wall,'top':top,'bottom':bottom}
    planar_size = max(size[axis] for axis in range(3) if axis != build_axis)
    cells_across = {'walls':wall/planar_size,'top':top/size[build_axis],
                    'bottom':bottom/size[build_axis]}
    positive_shell_cells = [cells_across[key] for key,value in thicknesses.items() if value > 0]
    min_shell_cells = min(positive_shell_cells) if positive_shell_cells else None
    underresolved = min_shell_cells is not None and min_shell_cells < 2
    cell_volume = float(np.prod(size))
    notes = [
        'Shells follow directional boundaries of the voxel mesh; slicer paths, extrusion overlap and added solid regions are not reconstructed.',
        'Shell and core stiffness are averaged inside each cell with a uniform-strain assumption; infill-pattern anisotropy is not resolved.',
    ]
    if supplied:
        notes.append('Relative core stiffness was supplied externally; this value alone does not establish experimental calibration.')
    else:
        notes.append('Core stiffness uses the Voigt uniform-strain upper bound for the nominal infill fraction, not a calibrated infill law.')
    if underresolved:
        notes.append('At least one nonzero shell thickness spans fewer than two mesh cells; its volume is integrated, but its bending behavior is not spatially resolved.')
    if np.any(stiffness == 0):
        notes.append('Zero-infill interior cells have zero stiffness and must be excluded from the elastic solve; no artificial stiffness floor is applied.')
    if sub_line_width.any():
        notes.append('Some local in-plane sections are thinner than the requested line width; a slicer may omit them or change the extrusion width.')
    if sub_layer_height.any():
        notes.append('Some local build-direction sections are thinner than one requested layer; a slicer may omit them or change their thickness.')
    metadata = {
        'model':'directional_voxel_shell_core_v1',
        'build_axis':settings.orientation,
        'cell_mixing':'voigt_uniform_strain',
        'core_stiffness_source':'supplied_relative_core_stiffness' if supplied else 'voigt_upper_bound',
        'core_stiffness_ratio':float(core_ratio),
        'infill_fraction':density,
        'infill_fraction_source':'nominal_slicer_setting',
        'wall_thickness_model':'perimeter_count_times_nominal_line_width',
        'wall_thickness_mm':float(wall),
        'top_thickness_mm':float(top),
        'bottom_thickness_mm':float(bottom),
        'shell_fraction':float(shell.mean()),
        'solid_fraction':float(solid.mean()),
        'mean_stiffness_factor':float(stiffness.mean()),
        'estimated_shell_volume_mm3':float(shell.sum()*cell_volume),
        'estimated_printed_volume_mm3':float(solid.sum()*cell_volume),
        'voxel_volume_mm3':float(len(indices)*cell_volume),
        'mixed_cell_count':int(np.count_nonzero((shell > 0)&(shell < 1))),
        'zero_stiffness_cell_count':int(np.count_nonzero(stiffness == 0)),
        'sub_line_width_cell_count':int(sub_line_width.sum()),
        'sub_layer_height_cell_count':int(sub_layer_height.sum()),
        'shell_cells':{key:float(value) for key,value in cells_across.items()},
        'min_shell_cells':float(min_shell_cells) if min_shell_cells is not None else None,
        'shell_resolution_limited':bool(underresolved),
        'notes':notes,
    }
    return CellPrintModel(shell,solid,stiffness,metadata)
