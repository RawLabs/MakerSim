"""Bounded result queries, relative goals, and independent mesh refinement studies."""
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from pydantic import ValidationError

from .agent_schemas import ComparisonGoals
from .local import AgentError, compare_runs, get_result, run_test, write_json


def query_field(report_path, load_case, field, offset=0, limit=100, bounds_mm=None):
    """Return selected surface samples; node indices refer to the saved field order."""
    if field not in ("displacement_mm", "surface_stress_mpa", "positions_mm"):
        raise AgentError("invalid_query", "Choose displacement_mm, surface_stress_mpa or positions_mm.")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise AgentError("invalid_query", "Offset must be nonnegative and limit between 1 and 1000.")
    report = get_result(report_path)
    case = next((case for case in report["load_cases"] if case["name"] == load_case), None)
    if case is None:
        raise AgentError("unknown_case", f"Unknown load case: {load_case}")
    from .capture import surface_fields
    positions, displacement, stress = surface_fields(case)
    values = {"positions_mm": positions, "displacement_mm": displacement, "surface_stress_mpa": stress}[field]
    indices = np.arange(len(positions))
    if bounds_mm is not None:
        try:
            bounds = np.asarray(bounds_mm, dtype=float)
            if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or (bounds[0] > bounds[1]).any():
                raise ValueError()
        except (ValueError, TypeError) as error:
            raise AgentError("invalid_query", "Bounds must be finite [[xmin,ymin,zmin],[xmax,ymax,zmax]] in report coordinates.") from error
        indices = indices[np.all((positions >= bounds[0]) & (positions <= bounds[1]), axis=1)]
    selected = indices[offset:offset+limit]
    return {"source_sha256": report["source"]["sha256"], "test_sha256": report["test_sha256"],
            "load_case": load_case, "field": field, "coordinate_frame": report["test_spec"]["coordinate_frame"],
            "total_matching_nodes": len(indices), "offset": offset, "returned": len(selected),
            "next_offset": offset+len(selected) if offset+len(selected) < len(indices) else None,
            "samples": [{"surface_index": int(i), "position_mm": positions[i].tolist(), "value": values[i].tolist()}
                        for i in selected]}


def evaluate_goals(baseline_path, candidate_path, goals, output_path=None):
    try:
        goals = ComparisonGoals.model_validate(goals)
    except ValidationError as error:
        raise AgentError("invalid_goals", "Invalid comparison goals.",
                         [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]) from error
    comparison = compare_runs(baseline_path, candidate_path)
    requirements = []
    for rule in goals.requirements:
        metric = comparison["solid_volume_mm3"]
        if rule.metric != "solid_volume_mm3":
            case = next((case for case in comparison["load_cases"] if case["name"] == rule.load_case), None)
            if case is None:
                raise AgentError("unknown_case", f"Unknown goal load case: {rule.load_case}")
            if rule.probe is not None:
                probe = next((probe for probe in case["surface_probes"] if probe["name"] == rule.probe), None)
                if probe is None:
                    raise AgentError("unknown_probe", f"Unknown goal probe: {rule.probe}")
                metric = probe["max_displacement_mm"]
            else:
                metric = case["metrics"][rule.metric]
        change = metric["change_percent"]
        met = None if change is None else (abs(change-rule.change_percent) <= 1e-9 or
            (change <= rule.change_percent if rule.operator == "at_most" else change >= rule.change_percent))
        requirements.append({**rule.model_dump(mode="json"), **metric, "limit_change_percent": rule.change_percent,
            "status": "not_assessed" if met is None else "met" if met else "not_met", "met": met,
            "reason": "Relative change is undefined for a zero baseline." if met is None else None})
    statuses = [requirement["status"] for requirement in requirements]
    result = {"goal_status": "not_met" if "not_met" in statuses else "not_assessed" if "not_assessed" in statuses else "met",
            "goals": goals.model_dump(mode="json"), "requirements": requirements, "comparison": comparison,
            "assessment": "calculated_relative_goals", "numerical_confidence": "mesh_convergence_not_verified"}
    if output_path is not None:
        result["evaluation_path"] = str(Path(output_path).expanduser().resolve())
        write_json(output_path, result)
    return result


def convergence_summary(reports, displacement_tolerance_percent, stress_tolerance_percent):
    if not reports:
        raise AgentError("invalid_refinement", "A convergence summary needs saved refinement levels.")
    pitches = [report["mesh"]["cell_mm"] for report in reports]
    refined = len(reports) >= 3 and all(b < a*(1-1e-9) for a, b in zip(pitches, pitches[1:]))
    metrics = []
    for case_index, case in enumerate(reports[0]["load_cases"]):
        series = [("max_displacement_mm", None, [r["load_cases"][case_index]["max_displacement_mm"] for r in reports], displacement_tolerance_percent),
                  ("peak_surface_stress_mpa", None, [r["load_cases"][case_index]["peak_surface_stress_mpa"] for r in reports], stress_tolerance_percent)]
        for probe_index, probe in enumerate(case["surface_probes"]):
            series.append(("surface_probe_max_displacement_mm", probe["name"],
                [r["load_cases"][case_index]["surface_probes"][probe_index]["max_displacement_mm"] for r in reports], displacement_tolerance_percent))
        for name, probe, values, tolerance in series:
            changes = [0.0 if a == b == 0 else (b/a-1)*100 if a else None for a, b in zip(values, values[1:])]
            stable = refined and all(change is not None and abs(change) <= tolerance for change in changes[-2:])
            metrics.append({"load_case": case["name"], "metric": name, "probe": probe, "values": values,
                "successive_change_percent": changes, "tolerance_percent": tolerance, "last_two_steps_within_tolerance": stable})
    return {"status": "not_assessed" if not refined else "within_tolerance" if all(m["last_two_steps_within_tolerance"] for m in metrics) else "not_converged",
            "actual_cell_mm": pitches, "strictly_refined": refined, "metrics": metrics,
            "notes": ["Observed stability requires two successive refinement steps within each metric's tolerance.",
                      "Fixture/load/probe sampling changes with refinement; inspect the saved comparisons.",
                      "Mesh stability does not validate heuristic print properties or establish physical safety."]}


def check_convergence(stl_path, spec, output_dir, resolutions=None, max_cells=12000,
                      displacement_tolerance_percent=5, stress_tolerance_percent=10):
    resolutions = [28, 35, 44] if resolutions is None else resolutions
    if (not isinstance(resolutions, list) or not 3 <= len(resolutions) <= 6
            or any(type(r) is not int or not 8 <= r <= 100 for r in resolutions)
            or any(b <= a for a, b in zip(resolutions, resolutions[1:]))):
        raise AgentError("invalid_refinement", "Choose 3–6 strictly increasing integer resolutions between 8 and 100.")
    for tolerance in (displacement_tolerance_percent, stress_tolerance_percent):
        if type(tolerance) not in (int, float) or not np.isfinite(tolerance) or not 0 < tolerance <= 100:
            raise AgentError("invalid_refinement", "Convergence tolerances must be finite percentages greater than 0 and at most 100.")
    target = Path(output_dir).expanduser().resolve()
    if target.exists():
        raise AgentError("output_exists", "Choose a new convergence directory.")
    # Strict requests fail at the budget instead of silently coarsening a refinement level.
    reports = [run_test(stl_path, spec, resolution=r, max_cells=max_cells, strict_pitch=True,
                        revision=f"mesh-resolution-{r}") for r in resolutions]
    if any(report["source"]["sha256"] != reports[0]["source"]["sha256"] for report in reports):
        raise AgentError("source_changed", "The STL changed during the study. Use an immutable revision STL.")
    summary = convergence_summary(reports, displacement_tolerance_percent, stress_tolerance_percent)
    summary.update({"source": reports[0]["source"], "test_sha256": reports[0]["test_sha256"],
                    "requested_resolutions": resolutions, "max_cells": max_cells,
                    "report_paths": [str(target/f"level-{i:02d}.json") for i in range(1, len(reports)+1)],
                    "summary_path": str(target/"convergence.json")})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".makersim-refinement-", dir=target.parent) as temporary:
            stage = Path(temporary)
            for i, report in enumerate(reports, 1):
                report["report_path"] = summary["report_paths"][i-1]
                write_json(stage/f"level-{i:02d}.json", report)
            summary["sampling_comparisons"] = [compare_runs(stage/f"level-{i:02d}.json", stage/f"level-{i+1:02d}.json")
                                               for i in range(1, len(reports))]
            write_json(stage/"convergence.json", summary)
            target.mkdir()
            for path in stage.iterdir():
                (target/path.name).hardlink_to(path)
    except FileExistsError as error:
        raise AgentError("output_exists", "Choose a new convergence directory.") from error
    except OSError as error:
        raise AgentError("file_error", f"Cannot save convergence study: {error.strerror}") from error
    return summary
