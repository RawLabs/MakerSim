// Reports use exported CAD coordinates; the viewport centers each STL.
export function viewportResult(report, center) {
  const first = report.load_cases[0];
  if (!first?.fields) throw new Error('This report has no saved surface fields.');
  const offset = report.test_spec.coordinate_frame === 'source_mm' ? center : [0, 0, 0];
  const patch = selection => ({...selection, point: selection.point.map((value, axis) => value - offset[axis])});
  // Match the solver's linear 98th-percentile legend, including zero-stress fields.
  const sorted = [...first.fields.surface_stress_mpa].sort((a, b) => a - b);
  const index = (sorted.length - 1) * .98, lower = Math.floor(index), upper = Math.ceil(index);
  const scale = Math.max(sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower), 1e-12);
  return {
    positions: first.fields.positions_mm.map(point => point.map((value, axis) => value - offset[axis])),
    displacement: first.fields.displacement_mm,
    surface_triangles: first.fields.surface_triangles,
    surface_quads: first.fields.surface_quads,
    stress: first.fields.surface_stress_mpa,
    heatmap_scale_mpa: scale,
    force_newtons: first.force_newtons,
    max_displacement_mm: first.max_displacement_mm,
    mesh: report.mesh,
    notes: [`Displaying load case: ${first.name}.`, ...first.notes, ...report.limitations],
    session_test: {spec: report.test_spec, fixtures: report.test_spec.fixtures.map(patch),
      load: patch(report.test_spec.load_cases[0].load), direction: report.test_spec.load_cases[0].direction,
      unit: report.test_spec.load_cases[0].unit},
  };
}

export function eventAt(events, sequence) {
  return events.find(event => event.sequence === sequence);
}

export function unseenEvents(events, after) {
  return events.filter(event => event.sequence > after);
}
