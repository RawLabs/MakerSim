"""Generate the small, nonstandard cantilever calibration kit without a CAD dependency.

Run from the repository root with:
    .venv/bin/python -m scripts.make_calibration_coupon --output-dir /tmp/my-coupon-kit

The physical coupon includes the clamped region and a 10 mm unloaded tail.
Analysis solids contain only the measured free span, ending at the load line.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from backend.agent_schemas import LocalTestSpec
from backend.geometry import read_stl, write_binary_stl


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / "backend/data/calibration"
LENGTH_MM, WIDTH_MM, THICKNESS_MM = 130.0, 18.0, 6.0
LOAD_LINE_MM = 120.0
SPANS_MM = (60, 90)
PRINT_SETTINGS = {"walls": 3, "top_layers": 5, "bottom_layers": 5,
                  "infill": 100, "orientation": "z"}
MEASUREMENT_COLUMNS = ("coupon_id", "span_mm", "cycle", "phase", "force_n", "deflection_mm",
                       "hold_seconds", "width_mm", "thickness_mm", "temperature_c",
                       "scale_reading", "scale_unit", "notes")


def rectangular_bar(length_mm, width_mm=WIDTH_MM, thickness_mm=THICKNESS_MM):
    """Closed outward STL, x=0..length, y=±width/2, z=0..thickness, in mm."""
    dimensions = np.asarray([length_mm, width_mm, thickness_mm], dtype=float)
    if not np.isfinite(dimensions).all() or np.any(dimensions <= 0):
        raise ValueError("Bar dimensions must be positive and finite.")
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
    vertices = vertices*dimensions + [0, -width_mm/2, 0]
    quads = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
             [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    triangles = np.array([vertices[[q[0], q[1], q[2]]] for q in quads]
                         + [vertices[[q[0], q[2], q[3]]] for q in quads])
    data = write_binary_stl(triangles)
    mesh = read_stl(data)
    if not mesh.is_watertight or not mesh.has_consistent_winding or mesh.volume <= 0:
        raise RuntimeError("Generated coupon is not a closed outward solid.")
    return data


def test_spec(span_mm):
    """An ideal clamp and full-section resultant, with a directional probe."""
    if span_mm not in SPANS_MM:
        raise ValueError("The supplied kit uses 60 mm and 90 mm spans.")
    # Twelve millimetres covers the 18×6 mm end section and coarse mesh corners.
    fixture = {"point": [0, 0, THICKNESS_MM/2], "normal": [-1, 0, 0], "radius": 12}
    loaded_end = {"point": [span_mm, 0, THICKNESS_MM/2], "normal": [1, 0, 0], "radius": 12}
    spec = {"schema_version": 1, "name": f"Coupon: ideal {span_mm} mm cantilever, 1 N reference",
            "coordinate_frame": "source_mm", "stl_unit": "mm", "material_id": "generic-pla",
            "print_settings": PRINT_SETTINGS,
            "fixtures": [fixture],
            "load_cases": [{"name": "downward_1N", "load": loaded_end,
                            "direction": [0, 0, -1], "magnitude": 1, "unit": "N"}],
            "surface_probes": [{"name": "load_line", **loaded_end}]}
    return LocalTestSpec.model_validate(spec).model_dump(mode="json")


def json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()


def setup_drawing():
    """A dimensioned guide, intentionally separate from the unnotched STL."""
    return b'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500" viewBox="0 0 1000 500" role="img" aria-labelledby="title desc">
<title id="title">MakerSim cantilever coupon: two clamp positions</title>
<desc id="desc">A 130 by 18 by 6 millimetre bar. Mark clamp exits at 30 and 60 millimetres from the left end. Put the load line at 120 millimetres. Free spans are 90 and 60 millimetres. Measure downward movement at the load line relative to the clamp.</desc>
<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto-start-reverse"><path d="M0 0L7 3.5L0 7" fill="#294153"/></marker></defs>
<rect width="1000" height="500" fill="#fff"/>
<g font-family="sans-serif" font-size="17" fill="#172839">
<text x="60" y="35" font-size="24" font-weight="bold">Print one plain bar; use two clamp positions</text>
<text x="60" y="62">All dimensions in mm. Mark with ink after printing; do not notch the bar.</text>
<text x="60" y="105" font-weight="bold">TOP VIEW</text>
<rect x="110" y="130" width="780" height="108" fill="#d8e8f4" stroke="#294153" stroke-width="2"/>
<g stroke-width="2" stroke-dasharray="5 4"><path d="M290 118V248M470 118V248" stroke="#137c62"/><path d="M830 118V248" stroke="#b4571d"/></g>
<text x="110" y="272" text-anchor="middle">0</text><text x="290" y="272" text-anchor="middle">30</text><text x="470" y="272" text-anchor="middle">60</text><text x="830" y="272" text-anchor="middle">120</text><text x="890" y="272" text-anchor="middle">130</text>
<text x="290" y="101" text-anchor="middle" fill="#137c62">90 mm span clamp</text><text x="493" y="122" text-anchor="middle" fill="#137c62">60 mm span clamp</text>
<text x="830" y="101" text-anchor="middle" fill="#b4571d">Load line</text>
<path d="M930 132V236" stroke="#294153" marker-start="url(#arrow)" marker-end="url(#arrow)"/><text x="945" y="190">18</text>
<path d="M112 292H888" stroke="#294153" marker-start="url(#arrow)" marker-end="url(#arrow)"/><text x="500" y="316" text-anchor="middle">130 total length</text>
<text x="60" y="350" font-weight="bold">SIDE VIEW: 90 mm setup</text>
<rect x="110" y="385" width="780" height="36" fill="#d8e8f4" stroke="#294153" stroke-width="2"/>
<path d="M110 368H290V385H110ZM110 421H290V438H110Z" fill="#879ba8" stroke="#294153"/>
<circle cx="276" cy="370" r="4" fill="#137c62"/><text x="58" y="468">Track clamp datum</text><path d="M227 453L272 376" stroke="#137c62"/>
<path d="M830 380V453" stroke="#b4571d" stroke-width="3" marker-end="url(#arrow)"/><text x="738" y="478">Force and movement at x=120</text>
<path d="M294 363H826" stroke="#294153" marker-start="url(#arrow)" marker-end="url(#arrow)"/><text x="560" y="353" text-anchor="middle">90 free span</text>
<text x="911" y="409">6 thick</text><text x="375" y="462">30 mm jaw contact at either clamp position</text>
</g></svg>'''


def kit_files():
    """Return reproducible geometry, inputs and empty recording sheets."""
    files = {"coupon-130x18x6.stl": rectangular_bar(LENGTH_MM), "setup.svg": setup_drawing()}
    configurations = []
    for span in SPANS_MM:
        model_name, spec_name = f"beam-span-{span}.stl", f"test-span-{span}.json"
        files[model_name] = rectangular_bar(span)
        files[spec_name] = json_bytes(test_spec(span))
        configurations.append({"span_mm": span, "clamp_exit_x_mm": LOAD_LINE_MM-span,
                               "analysis_stl": model_name, "test_spec": spec_name,
                               "probe": "load_line", "deflection_component": "negative_mean_z_mm"})
    sheet = io.StringIO(newline="")
    csv.writer(sheet, lineterminator="\n").writerow(MEASUREMENT_COLUMNS)
    files["measurements.csv"] = sheet.getvalue().encode()
    files["correlation-setup.json"] = json_bytes({
        "schema_version": 1,
        "references": [{"span_mm": span, "report_path": f"reference-span-{span}.json",
                        "load_case": "downward_1N", "probe": "load_line"} for span in SPANS_MM],
        "force_resolution_n": None,
        "displacement_resolution_mm": None,
        "metadata": {"filament_brand_color_lot": "", "printer_nozzle": "",
                     "slicer_profile": "", "print_batch_and_date": "", "conditioning": "",
                     "scale_model_and_accuracy": "", "deflection_measurement_method": "",
                     "clamp_and_load_line": "", "mesh_convergence_reports": [],
                     "held_out_validation": "Not performed."},
    })
    manifest = {
        "schema_version": 1,
        "purpose": "Nonstandard small-deflection bending calibration; no strength or safe-load claim.",
        "physical_coupon": {"stl": "coupon-130x18x6.stl", "length_mm": LENGTH_MM,
                            "width_mm": WIDTH_MM, "thickness_mm": THICKNESS_MM,
                            "load_line_x_mm": LOAD_LINE_MM, "unloaded_tail_mm": LENGTH_MM-LOAD_LINE_MM,
                            "print_count": 3},
        "initial_print_settings": {**PRINT_SETTINGS, "material_id": "generic-pla", "layer_height_mm": .2,
                                   "line_width_mm": .45, "raster": "alternating +45/-45 degrees to X",
                                   "calibrated": False},
        "configurations": configurations,
        "correlation_setup": "correlation-setup.json",
        "measurement_convention": {"force_n": "Actual measured total downward force at the load line, N.",
                                   "deflection_mm": "Positive downward load-line displacement relative to the clamp datum, from the unloaded baseline.",
                                   "hold_seconds": "Time since the present force plateau was reached.",
                                   "scale_reading": "Optional unconverted displayed reading; provide scale_unit and leave force_n blank for automatic conversion.",
                                   "phases": ["loading", "unloading", "hold", "recovery"],
                                   "comparison": "Use -surface_probes[name=load_line].mean_displacement_vector_mm[2], not whole-part max displacement."},
        "documentation": "docs/physical-testing.md",
        "files_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    files["kit.json"] = json_bytes(manifest)
    return files


def write_kit(directory):
    """Use a new directory so regeneration cannot erase recorded measurements."""
    directory = Path(directory).expanduser()
    files = kit_files()
    directory.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        with (directory/name).open("xb") as target:
            target.write(data)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIRECTORY,
                        help="New directory for the coupon kit. Existing directories are preserved.")
    parser.add_argument("--check", action="store_true", help="Verify an unmodified generated kit without writing.")
    args = parser.parse_args()
    if args.check:
        different = [name for name, data in kit_files().items()
                     if not (args.output_dir/name).is_file() or (args.output_dir/name).read_bytes() != data]
        if different:
            parser.error("Generated files differ or are missing: " + ", ".join(different))
        print(f"Calibration kit matches the generator: {args.output_dir}")
    else:
        try:
            directory = write_kit(args.output_dir)
        except FileExistsError:
            parser.error("Output directory already exists. Choose a new directory; recorded measurements are never overwritten.")
        print(f"Wrote the calibration kit to {directory}")


if __name__ == "__main__":
    main()
