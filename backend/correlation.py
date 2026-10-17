"""Compare recorded force/deflection slopes with saved directional probe results.

This produces candidates for further validation; it neither updates materials nor
infers failure strength. Repeated cycles are summarized within each coupon before
between-coupon spread is calculated.
"""
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .local import AgentError, get_result, read_bytes, read_json, write_json
from .schemas import force_newtons


Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PHASES = {"loading", "unloading", "hold", "recovery"}
REQUIRED = {"coupon_id", "span_mm", "cycle", "phase", "deflection_mm"}
OPTIONAL = {"force_n", "scale_reading", "scale_unit", "hold_seconds", "width_mm",
            "thickness_mm", "temperature_c", "notes"}


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    span_mm: Positive
    report_path: str = Field(min_length=1)
    load_case: str = Field(min_length=1)
    probe: str = Field(min_length=1)


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    references: list[Reference] = Field(min_length=1, max_length=100)
    force_resolution_n: Positive
    displacement_resolution_mm: Positive
    metadata: dict = Field(default_factory=dict)


def number(text, field, line):
    try:
        value = float(text)
    except (TypeError, ValueError) as error:
        raise AgentError("invalid_measurements", f"CSV row {line}: {field} needs a finite number.") from error
    if not math.isfinite(value):
        raise AgentError("invalid_measurements", f"CSV row {line}: {field} must be finite.")
    return value


def measurements(data):
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        headers = reader.fieldnames or []
        if (len(headers) != len(set(headers)) or not REQUIRED <= set(headers)
                or set(headers) - REQUIRED - OPTIONAL
                or not ("force_n" in headers or {"scale_reading", "scale_unit"} <= set(headers))):
            raise AgentError("invalid_measurements", "CSV needs unique supported column names, coupon_id, span_mm, cycle, phase, deflection_mm and force_n (or scale_reading + scale_unit).")
        rows = []
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise AgentError("invalid_measurements", f"CSV row {line}: column count does not match its header.")
            row = {key: value.strip() for key, value in row.items()}
            if not any(row.values()):
                continue
            if not row["coupon_id"] or row["phase"] not in PHASES:
                raise AgentError("invalid_measurements", f"CSV row {line}: provide coupon_id and phase loading, unloading, hold or recovery.")
            for field in (REQUIRED | OPTIONAL) - {"coupon_id", "phase", "scale_unit", "notes"}:
                if row.get(field, "") != "":
                    row[field] = number(row[field], field, line)
                else:
                    row.pop(field, None)
            if any(field not in row for field in ("span_mm", "cycle", "deflection_mm")):
                raise AgentError("invalid_measurements", f"CSV row {line}: span_mm, cycle and deflection_mm are required.")
            if (row["span_mm"] <= 0 or row["cycle"] <= 0 or not row["cycle"].is_integer()
                    or any(row.get(field, 1) <= 0 for field in ("width_mm", "thickness_mm"))
                    or row.get("hold_seconds", 0) < 0):
                raise AgentError("invalid_measurements", f"CSV row {line}: dimensions must be positive, cycle a positive integer and hold_seconds nonnegative.")
            row["cycle"] = int(row["cycle"])
            raw = row.get("scale_reading")
            unit = row.get("scale_unit", "")
            if (raw is None) != (not unit):
                raise AgentError("invalid_measurements", f"CSV row {line}: scale_reading and scale_unit must be supplied together.")
            if raw is not None:
                unit = {"lb": "lbf", "lbs": "lbf"}.get(unit, unit)
                if unit not in {"N", "kg", "lbf", "stone"} or raw < 0:
                    raise AgentError("invalid_measurements", f"CSV row {line}: scale_unit must be N, kg, lb/lbf or stone, with nonnegative reading.")
                converted = force_newtons(raw, unit)
                if not math.isfinite(converted):
                    raise AgentError("invalid_measurements", f"CSV row {line}: converted force must be finite.")
                # Preserve precise raw conversion; explicit duplicate values may differ only by roundoff.
                if "force_n" in row and not math.isclose(row["force_n"], converted, rel_tol=1e-5, abs_tol=1e-5):
                    raise AgentError("invalid_measurements", f"CSV row {line}: force_n conflicts with scale_reading/scale_unit; leave force_n blank to convert automatically.")
                row["force_n"] = converted
            if "force_n" not in row or row["force_n"] < 0:
                raise AgentError("invalid_measurements", f"CSV row {line}: supply a nonnegative force_n or scale reading.")
            row["source_row"] = line
            rows.append(row)
            if len(rows) > 20000:
                raise AgentError("invalid_measurements", "Limit each comparison to 20,000 recorded rows.")
        if not rows:
            raise AgentError("no_measurements", "The measurement template is empty. Record actual loading force and load-line deflection before running correlation.")
        return rows
    except (UnicodeDecodeError, csv.Error) as error:
        raise AgentError("invalid_measurements", "Measurements must be a valid UTF-8 CSV file.") from error


def reference_result(reference, directory):
    path = (directory / reference.report_path).resolve()
    report = get_result(path, "metrics")
    try:
        index = [case["name"] for case in report["load_cases"]].index(reference.load_case)
        case = report["load_cases"][index]
        probe = next(p for p in case["surface_probes"] if p["name"] == reference.probe)
        vector = np.asarray(probe["mean_displacement_vector_mm"], dtype=float)
        dimensions = np.asarray(report["model"]["dimensions_mm"], dtype=float)
        direction = np.asarray(report["test_spec"]["load_cases"][index]["direction"], dtype=float)
        direction /= np.max(np.abs(direction))
        direction /= np.linalg.norm(direction)
        if (vector.shape != (3,) or not np.isfinite(vector).all() or case["force_newtons"] <= 0
                or dimensions.shape != (3,) or not np.isfinite(dimensions).all() or np.any(dimensions <= 0)):
            raise ValueError("invalid directional probe")
        compliance = float(vector @ direction / case["force_newtons"])
        if not math.isfinite(compliance) or compliance <= 0:
            raise ValueError("nonpositive compliance")
    except (KeyError, ValueError, StopIteration, TypeError) as error:
        raise AgentError("invalid_reference", f"Reference {path} needs the named load case/probe and a finite positive displacement along its force direction.") from error
    return {**reference.model_dump(), "report_path": str(path),
            "report_sha256": hashlib.sha256(read_bytes(path, 128_000_000)).hexdigest(),
            "compliance_mm_per_n": compliance,
            "solver_version": report["solver_version"], "test_sha256": report["test_sha256"],
            "material_physics_sha256": report["material_physics_sha256"],
            "test_spec": report["test_spec"], "mesh": report["mesh"],
            "model": report["model"], "validity": case["validity"]}


def diagnostics(rows, force_resolution, displacement_resolution):
    loading = [r for r in rows if r["phase"] == "loading"]
    unloading = [r for r in rows if r["phase"] == "unloading"]
    flags = []
    hysteresis = None
    if len({r["force_n"] for r in loading}) >= 2 and unloading:
        forces = sorted({r["force_n"] for r in loading})
        values = [np.mean([r["deflection_mm"] for r in loading if r["force_n"] == force]) for force in forces]
        matched = [r for r in unloading if forces[0] <= r["force_n"] <= forces[-1]]
        differences = [r["deflection_mm"] - float(np.interp(r["force_n"], forces, values)) for r in matched]
        if differences:
            hysteresis = {"matched_points": len(differences), "mean_unloading_minus_loading_mm": float(np.mean(differences)),
                          "max_absolute_difference_mm": max(abs(x) for x in differences)}
            if hysteresis["max_absolute_difference_mm"] > 2*displacement_resolution:
                flags.append("unloading_difference_exceeds_two_displacement_increments")
    recovery_rows = [r for r in rows if r["phase"] == "recovery"]
    recovery = None
    if recovery_rows:
        zero = [r for r in recovery_rows if r["force_n"] <= force_resolution]
        if len(zero) != len(recovery_rows):
            flags.append("recovery_contains_nonzero_force")
        if zero:
            last = zero[-1]
            recovery = {"last_residual_mm": last["deflection_mm"], "hold_seconds": last.get("hold_seconds"),
                        "force_n": last["force_n"], "points": len(zero)}
            if abs(last["deflection_mm"]) > 2*displacement_resolution:
                flags.append("recovery_residual_exceeds_two_displacement_increments")
    # Hold rows are consecutive observations of one plateau; a time reset starts another.
    plateaus = []
    current = None
    for row in rows:
        if row["phase"] != "hold":
            current = None
            continue
        if "hold_seconds" not in row:
            flags.append("hold_time_missing")
            current = None
            continue
        if current is None or row["hold_seconds"] <= current[-1]["hold_seconds"]:
            current = []
            plateaus.append(current)
        current.append(row)
    holds = []
    for plateau in plateaus:
        if len(plateau) < 2:
            flags.append("hold_needs_two_timed_points")
            continue
        first, last = plateau[0], plateau[-1]
        variation = max(r["force_n"] for r in plateau)-min(r["force_n"] for r in plateau)
        change = last["deflection_mm"]-first["deflection_mm"]
        stable = variation <= force_resolution
        holds.append({"elapsed_seconds": last["hold_seconds"]-first["hold_seconds"],
                      "first_hold_seconds": first["hold_seconds"], "last_hold_seconds": last["hold_seconds"],
                      "mean_force_n": float(np.mean([r["force_n"] for r in plateau])),
                      "force_range_n": variation, "deflection_change_mm": change,
                      "stable_force": stable, "creep_change_mm": change if stable else None})
        if not stable:
            flags.append("hold_force_changed_cannot_attribute_motion_to_creep")
        elif abs(change) > 2*displacement_resolution:
            flags.append("hold_motion_exceeds_two_displacement_increments")
    return {"hysteresis": hysteresis, "recovery": recovery, "holds": holds, "flags": sorted(set(flags))}


def fit_group(key, rows, reference, setup):
    loading = [r for r in rows if r["phase"] == "loading"]
    result = {"coupon_id": key[0], "span_mm": key[1], "cycle": key[2],
              "status": "insufficient_measurements", "loading_fit": None,
              "proposed_stiffness_multiplier": None,
              **diagnostics(rows, setup.force_resolution_n, setup.displacement_resolution_mm)}
    result["cross_section"] = {}
    for field, axis in (("width_mm", 1), ("thickness_mm", 2)):
        nominal = reference["model"]["dimensions_mm"][axis]
        recorded = [r[field] for r in rows if field in r]
        result["cross_section"][field] = {"reference": nominal,
                "recorded_range": [min(recorded), max(recorded)] if recorded else None}
        if not recorded:
            result["flags"].append(f"{field}_not_recorded_nominal_reference_used")
        elif any(not math.isclose(value, nominal, rel_tol=1e-3, abs_tol=1e-4) for value in recorded):
            result["flags"].append(f"{field}_differs_from_reference_geometry_bias_not_corrected")
    forces = np.asarray([r["force_n"] for r in loading])
    displacements = np.asarray([r["deflection_mm"] for r in loading])
    # Count resolved levels, not tiny variations within a displayed force increment.
    levels = []
    for force in sorted(forces):
        if not levels or force-levels[-1] >= setup.force_resolution_n*(1-1e-9):
            levels.append(force)
    reasons = []
    if len(levels) < 4:
        reasons.append("need_four_resolved_loading_force_levels")
    if not len(forces) or np.ptp(forces) < 5*setup.force_resolution_n*(1-1e-9):
        reasons.append("loading_force_range_below_five_instrument_increments")
    if not len(displacements) or np.ptp(displacements) < 5*setup.displacement_resolution_mm*(1-1e-9):
        reasons.append("loading_displacement_range_below_five_instrument_increments")
    if reasons:
        result["flags"] += reasons
        return result
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        dx, dy = forces-forces.mean(), displacements-displacements.mean()
        slope = float(dx @ dy / (dx @ dx))
        intercept = float(displacements.mean()-slope*forces.mean())
        residual = displacements-(slope*forces+intercept)
        rmse = float(np.sqrt(np.mean(residual**2)))
        r_squared = float(1-residual @ residual/(dy @ dy))
        multiplier = reference["compliance_mm_per_n"]/slope if slope else math.inf
    if not all(math.isfinite(v) for v in (slope, intercept, rmse, r_squared, multiplier)) or slope <= 0:
        result["flags"].append("loading_fit_needs_finite_positive_compliance")
        return result
    result.update(status="fit", loading_fit={"points": len(loading), "resolved_force_levels": len(levels),
                  "compliance_mm_per_n": slope, "intercept_mm": intercept,
                  "r_squared": r_squared, "rmse_mm": rmse,
                  "force_range_n": [float(forces.min()), float(forces.max())],
                  "deflection_range_mm": [float(displacements.min()), float(displacements.max())],
                  "reference_compliance_mm_per_n": reference["compliance_mm_per_n"],
                  "compliance_bias_percent": 100*(slope/reference["compliance_mm_per_n"]-1)},
                  proposed_stiffness_multiplier=multiplier)
    if r_squared < .98 or rmse > 2*setup.displacement_resolution_mm:
        result["flags"].append("loading_linearity_needs_review")
    return result


def correlate_measurements(setup_path, csv_path, output_path=None):
    """Read real measurements and reference reports; optionally publish a new JSON artifact."""
    setup_path, csv_path = Path(setup_path).expanduser().resolve(), Path(csv_path).expanduser().resolve()
    try:
        setup = Setup.model_validate(read_json(setup_path))
    except ValidationError as error:
        raise AgentError("invalid_correlation_setup", "Provide reference reports and positive finite force/displacement instrument resolutions in the setup JSON.", error.errors(include_url=False)) from error
    if len({ref.span_mm for ref in setup.references}) != len(setup.references):
        raise AgentError("invalid_correlation_setup", "Each span must have exactly one reference report.")
    data = read_bytes(csv_path, 8_000_000)
    rows = measurements(data)
    references = [reference_result(ref, setup_path.parent) for ref in setup.references]
    reference_by_span = {r["span_mm"]: r for r in references}
    profiles = {json.dumps([r["solver_version"], r["material_physics_sha256"],
                 r["test_spec"]["material_id"], r["test_spec"]["print_settings"]], sort_keys=True) for r in references}
    flags = ["references_use_different_solver_material_or_print_profiles_factors_not_transferable"] if len(profiles)>1 else []
    grouped = defaultdict(list)
    coupon_dimensions = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["span_mm"] not in reference_by_span:
            raise AgentError("invalid_measurements", f"CSV row {row['source_row']}: span_mm has no matching reference; generate a reference for the measured span.")
        grouped[(row["coupon_id"], row["span_mm"], row["cycle"])].append(row)
        for field in ("width_mm", "thickness_mm"):
            if field in row:
                coupon_dimensions[row["coupon_id"]][field].append(row[field])
    groups = [fit_group(key, values, reference_by_span[key[1]], setup) for key, values in sorted(grouped.items())]
    for group in groups:
        group["flags"] += flags
        for field in ("width_mm", "thickness_mm"):
            recorded = coupon_dimensions[group["coupon_id"]][field]
            if recorded and not math.isclose(min(recorded), max(recorded), rel_tol=1e-3, abs_tol=1e-4):
                group["flags"].append(f"{field}_varies_within_coupon")
    summaries = []
    for span, reference in reference_by_span.items():
        by_coupon = defaultdict(list)
        summary_flags = set()
        for group in groups:
            if group["span_mm"] == span and group["status"] == "fit":
                by_coupon[group["coupon_id"]].append(group["loading_fit"]["compliance_mm_per_n"])
                summary_flags.update(group["flags"])
        if not by_coupon:
            continue
        coupon_means = {coupon: float(np.mean(values)) for coupon, values in sorted(by_coupon.items())}
        values = list(coupon_means.values())
        mean = float(np.mean(values))
        if len(values) < 3:
            summary_flags.add("fewer_than_three_independent_coupons")
        summaries.append({"span_mm": span, "coupon_count": len(values), "fitted_cycles": sum(map(len, by_coupon.values())),
                          "coupon_mean_compliance_mm_per_n": coupon_means,
                          "mean_compliance_mm_per_n": mean,
                          "between_coupon_standard_deviation_mm_per_n": float(np.std(values, ddof=1)) if len(values)>1 else None,
                          "mean_compliance_bias_percent": 100*(mean/reference["compliance_mm_per_n"]-1),
                          "proposed_stiffness_multiplier": reference["compliance_mm_per_n"]/mean,
                          "flags": sorted(summary_flags)})
    result = {"schema_version": 1, "analysis_status": "analyzed" if summaries else "insufficient_measurements",
              "calibration_status": "candidate_not_validated", "material_updated": False, "strength_inferred": False,
              "setup_path": str(setup_path), "setup_sha256": hashlib.sha256(read_bytes(setup_path, 8_000_000)).hexdigest(),
              "measurements_path": str(csv_path), "measurements_sha256": hashlib.sha256(data).hexdigest(),
              "measurement_count": len(rows), "metadata": setup.metadata,
              "instrument_resolution": {"force_n": setup.force_resolution_n, "displacement_mm": setup.displacement_resolution_mm},
              "references": references, "measurements": rows, "groups": groups, "repeatability": summaries, "flags": flags,
              "limitations": ["Instrument resolution is not instrument accuracy; five increments is a minimum screening threshold.",
                              "Candidate stiffness factors require mesh convergence and an independent held-out test; none are applied automatically.",
                              "Fixture compliance, geometry, timing, print structure and instrument error can cause the measured bias.",
                              "Reference geometry is used without rescaling to recorded dimensions; bending compliance is especially sensitive to thickness cubed.",
                              "Loading R-squared and repeated cycles do not establish strength, a safe load, or independent sample count.",
                              "Diagnostic flags use two displacement increments and R-squared 0.98 as review heuristics, not certification criteria."]}
    try:
        # Validate the complete result, including derived values and user metadata, before publication.
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise AgentError("invalid_measurements", "Measurement magnitudes produced nonfinite derived values.") from error
    if output_path is not None:
        result["report_path"] = str(Path(output_path).expanduser().resolve())
        write_json(output_path, result)
    return result
