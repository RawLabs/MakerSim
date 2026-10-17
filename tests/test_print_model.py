from types import SimpleNamespace

import numpy as np
import pytest

from backend.print_model import cell_print_model
from backend.schemas import PrintSettings


CORNERS = np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],
                    [0,0,1],[1,0,1],[1,1,1],[0,1,1]])


def grid_volume(shape, cell_size=1., occupied=None):
    size = np.broadcast_to(np.asarray(cell_size,dtype=float),(3,)).copy()
    indices = np.argwhere(np.ones(shape,dtype=bool) if occupied is None else occupied)
    lattice,inverse = np.unique((indices[:,None]+CORNERS).reshape(-1,3),axis=0,return_inverse=True)
    return SimpleNamespace(nodes=lattice*size, cells=inverse.reshape(-1,8),
                           cell_size=size, pitch=float(size.max())),indices


def expected_box_fraction(extents, settings):
    remaining = np.asarray(extents,dtype=float).copy()
    axis = {'x':0,'y':1,'z':2}[settings.orientation]
    remaining -= 2*settings.walls*settings.line_width_mm
    remaining[axis] = extents[axis]-(settings.top_layers+settings.bottom_layers)*settings.layer_height_mm
    return 1-np.maximum(remaining,0).prod()/np.prod(extents)


@pytest.mark.parametrize('shape', [(3,2,2),(12,4,4),(30,10,10)])
@pytest.mark.parametrize('orientation', ['x','y','z'])
def test_box_shell_volume_is_analytic_and_independent_of_cell_size(shape, orientation):
    extents = np.array([60.,20.,20.])
    volume,_ = grid_volume(shape,extents/shape)
    settings = PrintSettings(orientation=orientation)
    model = cell_print_model(volume,settings)
    expected_shell = expected_box_fraction(extents,settings)
    expected_solid = expected_shell+(1-expected_shell)*settings.infill/100
    assert model.shell_fraction.mean() == pytest.approx(expected_shell,abs=1e-14)
    assert model.metadata['shell_fraction'] == pytest.approx(expected_shell)
    assert model.metadata['solid_fraction'] == pytest.approx(expected_solid)
    assert model.metadata['estimated_printed_volume_mm3'] == pytest.approx(expected_solid*np.prod(extents))
    np.testing.assert_allclose(model.stiffness_factor,model.solid_fraction)


def test_shell_stiffness_is_spatial_and_top_bottom_are_distinct():
    volume,indices = grid_volume((10,10,10))
    settings = PrintSettings(walls=1,line_width_mm=1,layer_height_mm=1,
                             top_layers=1,bottom_layers=2,infill=20)
    model = cell_print_model(volume,settings)
    by_cell = {tuple(index):value for index,value in zip(indices,model.stiffness_factor)}
    assert by_cell[(0,5,5)] == 1
    assert by_cell[(5,5,0)] == 1
    assert by_cell[(5,5,1)] == 1
    assert by_cell[(5,5,2)] == .2
    assert by_cell[(5,5,8)] == .2
    assert by_cell[(5,5,9)] == 1
    assert model.metadata['core_stiffness_source'] == 'voigt_upper_bound'


def test_build_orientation_moves_layer_thickness_to_the_selected_axis():
    volume,indices = grid_volume((10,10,10))
    settings = PrintSettings(walls=1,line_width_mm=1,layer_height_mm=1,
                             top_layers=2,bottom_layers=3,infill=0)
    z = cell_print_model(volume,settings)
    x = cell_print_model(volume,settings.model_copy(update={'orientation':'x'}))
    along_x = np.flatnonzero(np.all(indices == [1,5,5],axis=1))[0]
    along_z = np.flatnonzero(np.all(indices == [5,5,1],axis=1))[0]
    assert z.shell_fraction[along_x] == 0
    assert x.shell_fraction[along_x] == 1
    assert z.shell_fraction[along_z] == 1
    assert x.shell_fraction[along_z] == 0
    assert x.metadata['shell_fraction'] == z.metadata['shell_fraction']


def test_subcell_shells_have_nonzero_exact_fraction_and_resolution_warning():
    volume,_ = grid_volume((1,1,1),10)
    settings = PrintSettings(walls=1,line_width_mm=.1,layer_height_mm=.05,
                             top_layers=1,bottom_layers=1,infill=0)
    model = cell_print_model(volume,settings)
    assert model.shell_fraction[0] == pytest.approx(1-9.8**2*9.9/1000)
    assert 0 < model.stiffness_factor[0] < .1
    assert model.metadata['shell_resolution_limited']
    assert model.metadata['min_shell_cells'] == .005
    assert any('bending behavior is not spatially resolved' in note for note in model.metadata['notes'])


def test_opposing_layers_fill_a_thin_part_without_double_counting():
    volume,_ = grid_volume((5,5,1),(2,2,1))
    settings = PrintSettings(infill=0,top_layers=4,bottom_layers=4,layer_height_mm=.2)
    model = cell_print_model(volume,settings)
    np.testing.assert_array_equal(model.shell_fraction,1)
    np.testing.assert_array_equal(model.stiffness_factor,1)
    assert model.metadata['estimated_printed_volume_mm3'] == 100
    assert model.metadata['shell_resolution_limited']


def test_local_cavity_creates_perimeters_and_directional_top_layers():
    occupied = np.ones((7,7,7),dtype=bool)
    occupied[3,3,3] = False
    volume,indices = grid_volume(occupied.shape,occupied=occupied)
    settings = PrintSettings(walls=1,line_width_mm=.5,layer_height_mm=1,
                             top_layers=1,bottom_layers=0,infill=0)
    model = cell_print_model(volume,settings)
    by_cell = {tuple(index):value for index,value in zip(indices,model.shell_fraction)}
    assert by_cell[(2,3,3)] == .5
    assert by_cell[(3,2,3)] == .5
    assert by_cell[(3,3,2)] == 1  # Roof below the cavity, toward positive build.
    assert by_cell[(3,3,4)] == 0  # No bottom layers requested above the cavity.
    assert by_cell[(1,3,3)] == 0


def test_zero_infill_has_no_artificial_core_stiffness():
    volume,_ = grid_volume((6,6,6))
    settings = PrintSettings(walls=1,line_width_mm=1,top_layers=0,bottom_layers=0,infill=0)
    model = cell_print_model(volume,settings)
    assert model.stiffness_factor.min() == 0
    assert model.stiffness_factor.max() == 1
    assert model.metadata['zero_stiffness_cell_count'] == 4*4*6
    np.testing.assert_array_equal(model.stiffness_factor,model.shell_fraction)


def test_full_infill_is_full_stiffness_everywhere():
    volume,_ = grid_volume((6,6,6))
    model = cell_print_model(volume,PrintSettings(infill=100))
    np.testing.assert_array_equal(model.stiffness_factor,1)
    np.testing.assert_array_equal(model.solid_fraction,1)


def test_supplied_core_stiffness_changes_stiffness_without_changing_material_volume():
    volume,_ = grid_volume((10,10,10))
    settings = PrintSettings(walls=1,line_width_mm=1,top_layers=0,bottom_layers=0,infill=20)
    bound = cell_print_model(volume,settings)
    supplied = cell_print_model(volume,settings,core_stiffness_ratio=.08)
    np.testing.assert_array_equal(supplied.solid_fraction,bound.solid_fraction)
    np.testing.assert_array_equal(supplied.shell_fraction,bound.shell_fraction)
    np.testing.assert_allclose(supplied.stiffness_factor[supplied.shell_fraction == 0],.08)
    np.testing.assert_allclose(supplied.stiffness_factor[supplied.shell_fraction == 1],1)
    assert supplied.metadata['core_stiffness_source'] == 'supplied_relative_core_stiffness'
    assert any('does not establish' in note for note in supplied.metadata['notes'])


@pytest.mark.parametrize('ratio', [-.01,.200001,float('nan'),float('inf')])
def test_supplied_core_stiffness_cannot_exceed_the_density_bound(ratio):
    volume,_ = grid_volume((2,2,2))
    with pytest.raises(ValueError,match='upper bound'):
        cell_print_model(volume,PrintSettings(infill=20),core_stiffness_ratio=ratio)


@pytest.mark.parametrize('update', [{'walls':4},{'top_layers':5},{'bottom_layers':5},{'infill':50}])
def test_added_material_never_decreases_cell_stiffness(update):
    volume,_ = grid_volume((10,10,10))
    settings = PrintSettings()
    first = cell_print_model(volume,settings)
    second = cell_print_model(volume,settings.model_copy(update=update))
    assert np.all(second.stiffness_factor >= first.stiffness_factor)
    assert np.any(second.stiffness_factor > first.stiffness_factor)


def test_cell_order_and_world_translation_do_not_change_material_assignment():
    volume,_ = grid_volume((6,7,8),(.8,1.1,1.6))
    settings = PrintSettings(top_layers=3,bottom_layers=5)
    first = cell_print_model(volume,settings)
    order = np.random.default_rng(3).permutation(len(volume.cells))
    volume.cells = volume.cells[order]
    volume.nodes += [104.5,-37.2,84.9]
    second = cell_print_model(volume,settings)
    np.testing.assert_allclose(second.stiffness_factor,first.stiffness_factor[order])


def test_cell_size_metadata_uses_rectangular_spacing_not_largest_pitch():
    volume,_ = grid_volume((3,4,5),(4,2,.5))
    model = cell_print_model(volume,PrintSettings())
    assert model.metadata['shell_cells']['walls'] == pytest.approx(1.35/4)
    assert model.metadata['shell_cells']['top'] == pytest.approx(.8/.5)
    assert model.metadata['shell_cells']['bottom'] == pytest.approx(.8/.5)
    assert model.metadata['min_shell_cells'] == pytest.approx(1.35/4)


def test_features_below_print_dimensions_are_reported_not_claimed_printable():
    volume,_ = grid_volume((5,5,1),(2,2,.15))
    layer_model = cell_print_model(volume,PrintSettings(orientation='z',layer_height_mm=.2))
    assert layer_model.metadata['sub_layer_height_cell_count'] == 25
    assert layer_model.metadata['sub_line_width_cell_count'] == 0
    line_model = cell_print_model(volume,PrintSettings(orientation='x',line_width_mm=.45))
    assert line_model.metadata['sub_line_width_cell_count'] == 25
    assert line_model.metadata['sub_layer_height_cell_count'] == 0
    assert any('slicer may omit' in note for note in line_model.metadata['notes'])
