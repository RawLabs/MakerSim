"""Synthetic values exercise analysis math; no test data ships as measurements."""
import copy
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from backend.correlation import correlate_measurements
from backend.local import AgentError, run_test
from scripts.make_calibration_coupon import DEFAULT_DIRECTORY, MEASUREMENT_COLUMNS, test_spec as coupon_spec


@pytest.fixture(scope="module")
def reference_report():
    return run_test(DEFAULT_DIRECTORY/"beam-span-60.stl", coupon_spec(60), resolution=30,
                    max_cells=20000, strict_pitch=True)


@pytest.fixture
def files(tmp_path, reference_report):
    report = tmp_path/"reference.json"
    report.write_text(json.dumps(reference_report))
    setup = {"schema_version": 1,
             "references": [{"span_mm": 60, "report_path": report.name,
                              "load_case": "downward_1N", "probe": "load_line"}],
             "force_resolution_n": .1, "displacement_resolution_mm": .01,
             "metadata": {"filament": "test fixture only", "printer": "not a physical observation"}}
    setup_path = tmp_path/"setup.json"
    setup_path.write_text(json.dumps(setup))
    return setup_path, tmp_path/"measurements.csv", report


def write_rows(path, rows):
    with path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=MEASUREMENT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def loading_rows(slope=.1, intercept=.02, coupon="A", cycle=1):
    return [{"coupon_id": coupon, "span_mm": 60, "cycle": cycle, "phase": "loading",
             "force_n": force, "deflection_mm": slope*force+intercept, "hold_seconds": 10,
             "width_mm": 18, "thickness_mm": 6}
            for force in (0, 2, 4, 6, 8)]


def update_setup(path, **values):
    setup = json.loads(path.read_text())
    setup.update(values)
    path.write_text(json.dumps(setup))


def test_fit_uses_directional_mean_not_tip_maximum_and_retains_provenance(files, reference_report):
    setup, sheet, report_path = files
    report = copy.deepcopy(reference_report)
    # An unrelated vector component and maximum cannot change the axial projection.
    report["load_cases"][0]["surface_probes"][0]["mean_displacement_vector_mm"][0] = 500
    report["load_cases"][0]["max_displacement_mm"] = 1000
    report_path.write_text(json.dumps(report))
    write_rows(sheet, loading_rows())
    result = correlate_measurements(setup, sheet)
    group = result["groups"][0]
    fit = group["loading_fit"]
    reference = -report["load_cases"][0]["surface_probes"][0]["mean_displacement_vector_mm"][2]
    assert fit["compliance_mm_per_n"] == pytest.approx(.1)
    assert fit["intercept_mm"] == pytest.approx(.02)
    assert fit["r_squared"] == pytest.approx(1)
    assert fit["rmse_mm"] == pytest.approx(0, abs=1e-12)
    assert group["proposed_stiffness_multiplier"] == pytest.approx(reference/.1)
    assert result["references"][0]["report_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert result["metadata"]["filament"] == "test fixture only"
    assert result["measurements_sha256"] == hashlib.sha256(sheet.read_bytes()).hexdigest()
    assert result["calibration_status"] == "candidate_not_validated"
    assert result["material_updated"] is result["strength_inferred"] is False


def test_repeatability_counts_coupons_not_repeated_cycles(files):
    setup, sheet, _ = files
    rows = loading_rows(.1, coupon="A") + loading_rows(.1, coupon="A", cycle=2)
    rows += loading_rows(.2, coupon="B") + loading_rows(.3, coupon="C")
    write_rows(sheet, rows)
    result = correlate_measurements(setup, sheet)
    summary = result["repeatability"][0]
    assert summary["coupon_count"] == 3 and summary["fitted_cycles"] == 4
    assert summary["mean_compliance_mm_per_n"] == pytest.approx(.2)
    assert summary["between_coupon_standard_deviation_mm_per_n"] == pytest.approx(.1)
    assert not summary["flags"]


def test_repeatability_retains_warnings_from_three_nonlinear_coupons(files):
    setup, sheet, _ = files
    rows = []
    for coupon, curvature in (("A", .01), ("B", .012), ("C", .014)):
        observations = loading_rows(coupon=coupon)
        for row in observations:
            row["deflection_mm"] = .02 + curvature*row["force_n"]**2
            if coupon == "A":
                row["width_mm"] = 17.9
        rows.extend(observations)
        if coupon == "C":
            rows.extend({**observations[0], "phase": "hold", "force_n": 4,
                         "hold_seconds": time, "deflection_mm": movement}
                        for time, movement in ((10, .2), (60, .3)))
    write_rows(sheet, rows)
    result = correlate_measurements(setup, sheet)
    summary = result["repeatability"][0]
    assert summary["coupon_count"] == summary["fitted_cycles"] == 3
    assert all(group["loading_fit"]["r_squared"] < .98 for group in result["groups"])
    assert summary["flags"] == sorted({
        "loading_linearity_needs_review",
        "width_mm_differs_from_reference_geometry_bias_not_corrected",
        "hold_motion_exceeds_two_displacement_increments",
    })
    assert summary["mean_compliance_mm_per_n"] == pytest.approx(.096)
    reference = result["references"][0]["compliance_mm_per_n"]
    assert summary["proposed_stiffness_multiplier"] == pytest.approx(reference/.096)
    assert len(result["measurements"]) == len(rows)


def test_geometry_bias_is_explicit_and_dimensions_are_preserved(files):
    setup, sheet, _ = files
    rows = loading_rows()
    rows[1]["thickness_mm"] = 5.8
    for row in rows:
        row.pop("width_mm")
    write_rows(sheet, rows)
    group = correlate_measurements(setup, sheet)["groups"][0]
    assert group["cross_section"]["thickness_mm"] == {"reference": 6, "recorded_range": [5.8, 6]}
    assert "thickness_mm_differs_from_reference_geometry_bias_not_corrected" in group["flags"]
    assert "thickness_mm_varies_within_coupon" in group["flags"]
    assert "width_mm_not_recorded_nominal_reference_used" in group["flags"]


def test_mixed_reference_physics_are_flagged_as_nontransferable(files):
    setup, sheet, report_path = files
    second = report_path.with_name("second.json")
    report = json.loads(report_path.read_text())
    report["solver_version"] = "different-version-for-test"
    second.write_text(json.dumps(report))
    config = json.loads(setup.read_text())
    config["references"].append({**config["references"][0], "span_mm": 90, "report_path": second.name})
    setup.write_text(json.dumps(config))
    write_rows(sheet, loading_rows())
    result = correlate_measurements(setup, sheet)
    assert any("factors_not_transferable" in flag for flag in result["flags"])
    assert any("factors_not_transferable" in flag for flag in result["groups"][0]["flags"])
    assert any("factors_not_transferable" in flag for flag in result["repeatability"][0]["flags"])


def test_matching_unloading_recovery_and_constant_force_hold(files):
    setup, sheet, _ = files
    rows = loading_rows()
    for force in (7, 5, 3, 1):
        rows.append({**rows[0], "phase": "unloading", "force_n": force,
                     "deflection_mm": .1*force+.02+.05})
    rows += [{**rows[0], "phase": "hold", "force_n": 4, "hold_seconds": seconds,
              "deflection_mm": movement} for seconds, movement in ((10, .42), (60, .50))]
    rows += [{**rows[0], "phase": "recovery", "force_n": 0, "hold_seconds": seconds,
              "deflection_mm": movement} for seconds, movement in ((10, .06), (60, .04))]
    write_rows(sheet, rows)
    group = correlate_measurements(setup, sheet)["groups"][0]
    assert group["hysteresis"]["matched_points"] == 4
    assert group["hysteresis"]["max_absolute_difference_mm"] == pytest.approx(.05)
    assert group["holds"][0]["creep_change_mm"] == pytest.approx(.08)
    assert group["holds"][0]["elapsed_seconds"] == 50
    assert group["recovery"]["last_residual_mm"] == .04
    assert len(group["flags"]) == 3


def test_force_drift_is_not_reported_as_creep(files):
    setup, sheet, _ = files
    rows = loading_rows()
    rows += [{**rows[0], "phase": "hold", "force_n": force, "hold_seconds": seconds,
              "deflection_mm": movement} for seconds, force, movement in ((10, 4, .42), (60, 4.15, .50))]
    write_rows(sheet, rows)
    group = correlate_measurements(setup, sheet)["groups"][0]
    assert group["holds"][0]["creep_change_mm"] is None
    assert "hold_force_changed_cannot_attribute_motion_to_creep" in group["flags"]


def test_separate_hold_plateaus_are_not_combined(files):
    setup, sheet, _ = files
    rows = loading_rows()
    rows += [{**rows[0], "phase": "hold", "force_n": force, "hold_seconds": seconds,
              "deflection_mm": movement} for seconds, force, movement in
             ((10, 4, .42), (60, 4, .50), (10, 6, .62), (60, 6, .68))]
    write_rows(sheet, rows)
    holds = correlate_measurements(setup, sheet)["groups"][0]["holds"]
    assert len(holds) == 2
    assert [hold["creep_change_mm"] for hold in holds] == pytest.approx([.08, .06])


@pytest.mark.parametrize("unit,factor", [("kg", 9.80665), ("lb", 4.4482216152605), ("N", 1)])
def test_raw_scale_units_convert_explicitly(files, unit, factor):
    setup, sheet, _ = files
    rows = loading_rows()
    for row in rows:
        row["scale_reading"] = row.pop("force_n")/factor
        row["scale_unit"] = unit
    write_rows(sheet, rows)
    result = correlate_measurements(setup, sheet)
    assert result["groups"][0]["loading_fit"]["compliance_mm_per_n"] == pytest.approx(.1)
    assert result["measurements"][-1]["force_n"] == pytest.approx(8)


@pytest.mark.parametrize("field,value", [("force_n", "nan"), ("deflection_mm", "1e999"),
    ("width_mm", -1), ("cycle", 1.5), ("phase", "failure"), ("span_mm", 61),
    ("hold_seconds", -1), ("scale_unit", "kg")])
def test_invalid_measurements_have_actionable_errors(files, field, value):
    setup, sheet, _ = files
    rows = loading_rows()
    rows[0][field] = value
    write_rows(sheet, rows)
    with pytest.raises(AgentError) as error:
        correlate_measurements(setup, sheet)
    assert error.value.code == "invalid_measurements"
    assert "row" in str(error.value).lower()


def test_conflicting_newtons_and_raw_scale_are_rejected(files):
    setup, sheet, _ = files
    rows = loading_rows()
    rows[1].update(scale_reading=2, scale_unit="kg")
    write_rows(sheet, rows)
    with pytest.raises(AgentError, match="conflicts"):
        correlate_measurements(setup, sheet)


@pytest.mark.parametrize("change,flag", [
    ({"force_resolution_n": 2}, "loading_force_range_below_five_instrument_increments"),
    ({"displacement_resolution_mm": .2}, "loading_displacement_range_below_five_instrument_increments"),
])
def test_instrument_limits_prevent_candidate_factor(files, change, flag):
    setup, sheet, _ = files
    update_setup(setup, **change)
    write_rows(sheet, loading_rows())
    result = correlate_measurements(setup, sheet)
    assert result["analysis_status"] == "insufficient_measurements"
    assert result["groups"][0]["proposed_stiffness_multiplier"] is None
    assert flag in result["groups"][0]["flags"]


def test_tiny_force_variations_do_not_make_fake_independent_levels(files):
    setup, sheet, _ = files
    rows = loading_rows()
    for row, force in zip(rows, (0, .01, .02, .03, 8)):
        row["force_n"] = force
    write_rows(sheet, rows)
    group = correlate_measurements(setup, sheet)["groups"][0]
    assert group["loading_fit"] is None
    assert "need_four_resolved_loading_force_levels" in group["flags"]


def test_negative_slope_and_overflow_never_generate_a_factor(files):
    setup, sheet, _ = files
    write_rows(sheet, loading_rows(-.1))
    result = correlate_measurements(setup, sheet)
    assert result["groups"][0]["proposed_stiffness_multiplier"] is None
    rows = loading_rows()
    for row in rows:
        row["deflection_mm"] *= 1e307
    write_rows(sheet, rows)
    result = correlate_measurements(setup, sheet)
    assert result["groups"][0]["proposed_stiffness_multiplier"] is None


def test_empty_template_has_no_fabricated_result(files):
    setup, sheet, _ = files
    write_rows(sheet, [])
    with pytest.raises(AgentError) as error:
        correlate_measurements(setup, sheet)
    assert error.value.code == "no_measurements"


@pytest.mark.parametrize("resolution", [None, 0, -1, "nan"])
def test_instrument_resolution_must_be_supplied_and_positive(files, resolution):
    setup, sheet, _ = files
    update_setup(setup, force_resolution_n=resolution)
    write_rows(sheet, loading_rows())
    with pytest.raises(AgentError) as error:
        correlate_measurements(setup, sheet)
    assert error.value.code == "invalid_correlation_setup"


def test_bad_reference_probe_or_projection_rejected(files):
    setup, sheet, report_path = files
    write_rows(sheet, loading_rows())
    report = json.loads(report_path.read_text())
    report["load_cases"][0]["surface_probes"][0]["mean_displacement_vector_mm"] = [0, 0, .1]
    report_path.write_text(json.dumps(report))
    with pytest.raises(AgentError) as error:
        correlate_measurements(setup, sheet)
    assert error.value.code == "invalid_reference"


def test_malformed_reference_dimensions_have_an_actionable_error(files):
    setup, sheet, report_path = files
    write_rows(sheet, loading_rows())
    report = json.loads(report_path.read_text())
    report["model"]["dimensions_mm"] = [60, 18]
    report_path.write_text(json.dumps(report))
    with pytest.raises(AgentError) as error:
        correlate_measurements(setup, sheet)
    assert error.value.code == "invalid_reference"


def test_output_is_complete_json_and_cannot_overwrite_existing_analysis(files, tmp_path):
    setup, sheet, _ = files
    write_rows(sheet, loading_rows())
    output = tmp_path/"analysis.json"
    result = correlate_measurements(setup, sheet, output)
    assert json.loads(output.read_text()) == result
    saved = output.read_bytes()
    with pytest.raises(AgentError) as error:
        correlate_measurements(setup, sheet, output)
    assert error.value.code == "output_exists"
    assert output.read_bytes() == saved


def test_cli_correlation_roundtrip(files, tmp_path):
    setup, sheet, _ = files
    write_rows(sheet, loading_rows())
    executable = Path(__file__).resolve().parents[1]/".venv/bin/makersim"
    output = tmp_path/"cli-correlation.json"
    command = [str(executable), "correlate", str(setup), str(sheet), "--output", str(output)]
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr or process.stdout
    assert json.loads(process.stdout) == json.loads(output.read_text())
    repeated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert repeated.returncode == 2
    assert json.loads(repeated.stdout)["error"]["code"] == "output_exists"


def test_stdio_mcp_correlation_roundtrip(files, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    setup, sheet, _ = files
    write_rows(sheet, loading_rows())
    root = Path(__file__).resolve().parents[1]

    async def workflow():
        server = StdioServerParameters(command=str(root/".venv/bin/makersim-mcp"), cwd=str(tmp_path),
                                       env={**os.environ, "MAKERSIM_SESSION_DIR": str(tmp_path/"sessions")})
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                available = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert "correlate_measurements" in available
                arguments = {"setup_path": str(setup), "measurements_path": str(sheet)}
                result = await session.call_tool("correlate_measurements", arguments)
                assert not result.isError
                assert result.structuredContent["groups"][0]["loading_fit"]["compliance_mm_per_n"] == pytest.approx(.1)
                write_rows(sheet, [])
                empty = await session.call_tool("correlate_measurements", arguments)
                assert empty.isError
                assert "no_measurements" in empty.content[0].text

    asyncio.run(workflow())
