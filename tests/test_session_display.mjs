import assert from 'node:assert/strict';
import { viewportResult } from '../frontend/src/session-display.js';

const patch = {point: [40, 10, 5], normal: [0, 0, 1], radius: 3};
const report = {
  test_spec: {coordinate_frame: 'source_mm', fixtures: [patch], load_cases: [{load: patch, direction: [0, 0, -1], magnitude: 5, unit: 'lbf'}]},
  load_cases: [{name: 'downward', fields: {positions_mm: [[40, 10, 5]], displacement_mm: [[0, 0, -.2]], surface_stress_mpa: [3]},
    force_newtons: 10, max_displacement_mm: .2, notes: []}],
  mesh: {elements: 10}, limitations: ['Physical validation is not established.'],
};
const centered = viewportResult(report, [30, 5, 2]);
assert.deepEqual(centered.positions, [[10, 5, 3]]);
assert.deepEqual(centered.session_test.fixtures[0].point, [10, 5, 3]);
assert.deepEqual(centered.displacement, [[0, 0, -.2]]);
assert.equal(centered.heatmap_scale_mpa, 3);
assert.equal(centered.session_test.unit, 'lbf', 'Saved test units must survive an agent preview.');
report.load_cases[0].fields.surface_triangles = [[0, 1, 2]];
assert.deepEqual(viewportResult(report, [30, 5, 2]).surface_triangles, [[0, 1, 2]], 'Centering changes coordinates, not boundary connectivity.');
report.load_cases[0].fields.surface_quads = [[0, 1, 2, 3]];
assert.deepEqual(viewportResult(report, [30, 5, 2]).surface_quads, [[0, 1, 2, 3]], 'Hex face connectivity survives a saved-report preview.');
report.test_spec.coordinate_frame = 'centered_mm';
assert.deepEqual(viewportResult(report, [30, 5, 2]).positions, [[40, 10, 5]]);
assert.deepEqual(report.load_cases[0].fields.positions_mm, [[40, 10, 5]], 'Saved reports remain immutable.');
report.load_cases[0].fields.surface_stress_mpa = [0];
assert.equal(viewportResult(report, [30, 5, 2]).heatmap_scale_mpa, 1e-12);
report.load_cases[0].fields.surface_stress_mpa = [10, 0];
assert.equal(viewportResult(report, [30, 5, 2]).heatmap_scale_mpa, 9.8);
console.log('Session viewport coordinate transforms passed.');
