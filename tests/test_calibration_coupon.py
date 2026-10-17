"""Geometry and loading contracts for the physical cantilever comparison."""
import csv
import hashlib
import io
import json

import numpy as np
import pytest

from backend.geometry import read_stl
from backend.local import run_test
from backend.schemas import Patch
from backend.solver import create_volume, patch_load, patch_nodes
from scripts.make_calibration_coupon import (
    DEFAULT_DIRECTORY, MEASUREMENT_COLUMNS, SPANS_MM, kit_files,
    rectangular_bar, test_spec as coupon_spec, write_kit,
)


def test_print_coupon_has_measured_axes_and_no_hidden_gauge_features():
    mesh = read_stl(rectangular_bar(130))
    assert mesh.is_watertight and mesh.has_consistent_winding
    mesh.validate_single_solid()
    assert mesh.extents == pytest.approx([130, 18, 6])
    assert mesh.volume == pytest.approx(130*18*6)
    assert mesh.bounds+mesh.source_center_mm == pytest.approx(np.array([[0, -9, 0], [130, 9, 6]]))
    assert len(mesh.faces) == 12


def test_shipped_kit_is_reproducible_and_contains_no_fabricated_measurements():
    expected = kit_files()
    for name, content in expected.items():
        assert (DEFAULT_DIRECTORY/name).read_bytes() == content, name
    manifest = json.loads(expected['kit.json'])
    for name, digest in manifest['files_sha256'].items():
        assert hashlib.sha256(expected[name]).hexdigest() == digest
    rows = list(csv.reader(io.StringIO(expected['measurements.csv'].decode())))
    assert rows == [list(MEASUREMENT_COLUMNS)]
    setup = json.loads(expected[manifest['correlation_setup']])
    assert setup['force_resolution_n'] is setup['displacement_resolution_mm'] is None
    assert [reference['span_mm'] for reference in setup['references']] == list(SPANS_MM)
    for entry in manifest['configurations']:
        assert entry['clamp_exit_x_mm']+entry['span_mm'] == 120
        assert entry['deflection_component'] == 'negative_mean_z_mm'


def test_generation_never_overwrites_a_recorded_measurement_sheet(tmp_path):
    target = write_kit(tmp_path/'coupon')
    sheet = target/'measurements.csv'
    sheet.write_text('My real measurements must be retained.\n')
    with pytest.raises(FileExistsError):
        write_kit(target)
    assert sheet.read_text() == 'My real measurements must be retained.\n'


@pytest.mark.parametrize('span', SPANS_MM)
def test_reference_model_has_the_correct_free_span_and_end_resultant(span):
    mesh = read_stl(rectangular_bar(span))
    volume = create_volume(mesh, resolution=span//2, max_cells=20000, strict_pitch=True)
    assert volume.pitch == 2
    assert np.ptp(volume.nodes, axis=0) == pytest.approx([span, 18, 6])
    spec = coupon_spec(span)
    fixture = spec['fixtures'][0]
    load = spec['load_cases'][0]['load']
    fixed, _ = patch_nodes(volume, Patch(**{**fixture, 'point': np.array(fixture['point'])-mesh.source_center_mm}))
    loaded, weights, _, area = patch_load(volume, Patch(**{**load, 'point': np.array(load['point'])-mesh.source_center_mm}))
    fixed_positions = volume.nodes[fixed]+mesh.source_center_mm
    loaded_positions = volume.nodes[loaded]+mesh.source_center_mm
    assert np.allclose(fixed_positions[:, 0], 0)
    assert np.allclose(loaded_positions[:, 0], span)
    assert weights @ loaded_positions == pytest.approx([span, 0, 3])
    assert area == pytest.approx(18*6)
    assert not set(fixed).intersection(loaded)


@pytest.mark.parametrize('span', SPANS_MM)
def test_saved_probe_measures_downward_load_line_motion_and_scales_with_force(tmp_path, span):
    path = tmp_path/'beam.stl'
    path.write_bytes(rectangular_bar(span))
    spec = coupon_spec(span)
    spec['load_cases'].append({**spec['load_cases'][0], 'name': 'downward_2N', 'magnitude': 2})
    report = run_test(path, spec, resolution=span//2, max_cells=20000, strict_pitch=True)
    first, second = report['load_cases']
    vector = first['surface_probes'][0]['mean_displacement_vector_mm']
    assert vector[2] < 0
    assert abs(vector[0]) < abs(vector[2])*.01
    assert abs(vector[1]) < abs(vector[2])*.01
    assert second['surface_probes'][0]['mean_displacement_vector_mm'] == pytest.approx(np.array(vector)*2)
    assert first['patches']['load_centroid_mm'] == pytest.approx([span, 0, 3])
    assert first['checks']['relative_force_balance'] < 1e-8
    assert first['checks']['relative_moment_balance'] < 1e-8
    assert report['criteria_status'] == 'not_assessed'
