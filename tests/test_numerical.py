"""Agent decisions need bounded queries, comparable samples and honest mesh evidence."""
import copy
import json
import subprocess

import numpy as np
import pytest

from backend.local import AgentError, compare_runs, get_result, read_json, run_test, sampling_metadata, write_json
from backend.numerical import check_convergence, convergence_summary, evaluate_goals, query_field
from tests.test_local import ROOT, STL, TEST


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    root = tmp_path_factory.mktemp("numerical")
    path = root/"baseline.json"
    report = run_test(STL, read_json(TEST), path, include_fields=True)
    return root, path, report


def test_compact_view_and_bounded_region_queries_preserve_saved_fields(saved):
    _, path, report = saved
    original = path.read_bytes()
    compact = get_result(path, "metrics")
    case = report["load_cases"][0]
    assert "fields" not in compact["load_cases"][0]
    assert compact["load_cases"][0]["field_summary"]["available"]
    assert len(json.dumps(compact)) < len(json.dumps(report))*.2
    page = query_field(path, case["name"], "surface_stress_mpa", limit=7)
    assert page["returned"] == page["next_offset"] == 7
    second = query_field(path, case["name"], "surface_stress_mpa", offset=7, limit=7)
    assert second["samples"][0]["surface_index"] == 7
    assert second["samples"][0]["value"] == case["fields"]["surface_stress_mpa"][7]
    bounds = case["surface_probes"][0]["sample_bounds_mm"]
    region = query_field(path, case["name"], "displacement_mm", bounds_mm=bounds)
    assert region["returned"] > 0
    for sample in region["samples"]:
        assert np.all(np.array(sample["position_mm"]) >= bounds[0])
        assert np.all(np.array(sample["position_mm"]) <= bounds[1])
    assert path.read_bytes() == original
    with pytest.raises(AgentError, match="limit"):
        query_field(path, case["name"], "surface_stress_mpa", limit=1001)
    with pytest.raises(AgentError, match="Bounds"):
        query_field(path, case["name"], "surface_stress_mpa", bounds_mm=[[1, 1, 1], [0, 0, 0]])
    with pytest.raises(AgentError) as error:
        query_field(path, "missing", "surface_stress_mpa")
    assert error.value.code == "unknown_case"


def test_relative_tip_and_volume_goals_are_separate_from_absolute_criteria(saved):
    root, path, report = saved
    candidate = copy.deepcopy(report)
    candidate["model"]["solid_volume_mm3"] *= 1.2
    candidate["load_cases"][0]["surface_probes"][0]["max_displacement_mm"] *= .7
    candidate_path = root/"candidate.json"
    write_json(candidate_path, candidate)
    goals = {"requirements": [
        {"metric": "surface_probe_max_displacement_mm", "load_case": report["load_cases"][0]["name"],
         "probe": report["load_cases"][0]["surface_probes"][0]["name"], "change_percent": -30},
        {"metric": "solid_volume_mm3", "change_percent": 20}]}
    evaluated = evaluate_goals(path, candidate_path, goals, root/"evaluated.json")
    assert evaluated["goal_status"] == "met"
    assert evaluated["requirements"][0]["change_percent"] == pytest.approx(-30)
    assert evaluated["requirements"][1]["change_percent"] == pytest.approx(20)
    assert get_result(candidate_path)["criteria_status"] == report["criteria_status"]
    assert evaluated["numerical_confidence"] == "mesh_convergence_not_verified"
    with pytest.raises(AgentError) as error:
        evaluate_goals(path, candidate_path, goals, root/"evaluated.json")
    assert error.value.code == "output_exists"
    goals["requirements"][0]["change_percent"] = -40
    assert evaluate_goals(path, candidate_path, goals)["goal_status"] == "not_met"
    goals["requirements"][0].update(operator="at_least", change_percent=-40)
    assert evaluate_goals(path, candidate_path, goals)["goal_status"] == "met"
    goals["requirements"][0]["probe"] = "nonexistent"
    with pytest.raises(AgentError) as error:
        evaluate_goals(path, candidate_path, goals)
    assert error.value.code == "unknown_probe"


def test_zero_baseline_is_not_assessed_and_invalid_goals_are_errors(saved):
    root, _, report = saved
    zero = copy.deepcopy(report)
    zero["load_cases"][0]["max_displacement_mm"] = 0
    path = root/"zero.json"
    write_json(path, zero)
    goals = {"requirements": [{"metric": "max_displacement_mm", "load_case": zero["load_cases"][0]["name"], "change_percent": -30}]}
    assert evaluate_goals(path, path, goals)["goal_status"] == "not_assessed"
    goals["requirements"][0].pop("load_case")
    with pytest.raises(AgentError) as error:
        evaluate_goals(path, path, goals)
    assert error.value.code == "invalid_goals"


def test_sampling_changes_include_interior_nodes_and_legacy_uncertainty(saved):
    root, path, report = saved
    same = compare_runs(path, path)
    assert same["sampling_status"] == "unchanged"
    coordinates = np.array([[0., 0, 0], [1, 1, 1], [.25, .5, .5]])
    a = sampling_metadata(coordinates, 1)
    assert a == sampling_metadata(coordinates[::-1], 1)
    coordinates[2, 0] += .1
    b = sampling_metadata(coordinates, 1)
    assert a["sample_bounds_mm"] == b["sample_bounds_mm"]
    assert a["coordinates_sha256"] != b["coordinates_sha256"]
    changed = copy.deepcopy(report)
    changed["load_cases"][0]["surface_probes"][0]["coordinates_sha256"] = b["coordinates_sha256"]
    changed["load_cases"][0]["effective_material"]["modulus_xy_mpa"] *= 1.034
    altered = root/"sampling-changed.json"
    write_json(altered, changed)
    compared = compare_runs(path, altered)
    assert compared["sampling_status"] == "changed"
    assert compared["load_cases"][0]["surface_probes"][0]["sampling"]["coordinates_changed"]
    assert compared["load_cases"][0]["effective_material"]["modulus_xy_mpa"]["change_percent"] == pytest.approx(3.4)
    legacy = copy.deepcopy(report)
    for case in legacy["load_cases"]:
        del case["patches"]["load_sampling"]
        for sample in case["surface_probes"]+case["patches"]["holds"]:
            del sample["coordinates_sha256"]
    legacy_path = root/"legacy.json"
    write_json(legacy_path, legacy)
    assert compare_runs(legacy_path, legacy_path)["sampling_status"] == "unknown"


def test_real_refinement_preserves_physical_test_and_reports_actual_mesh(tmp_path):
    spec = read_json(TEST)
    result = check_convergence(STL, spec, tmp_path/"study", [20, 25, 32], max_cells=12000)
    assert result["strictly_refined"]
    assert len(result["metrics"]) == 6  # Global movement, stress, probe for two cases.
    reports = [get_result(path) for path in result["report_paths"]]
    assert len({r["test_sha256"] for r in reports}) == 1
    assert all(not report["mesh"]["coarsened_for_budget"] for report in reports)
    assert all(c["sampling_status"] == "changed" for c in result["sampling_comparisons"])
    assert read_json(result["summary_path"])["status"] == result["status"]
    with pytest.raises(AgentError) as error:
        check_convergence(STL, spec, tmp_path/"study")
    assert error.value.code == "output_exists"
    with pytest.raises(AgentError, match="budget"):
        check_convergence(STL, spec, tmp_path/"too-fine", max_cells=4)
    assert not (tmp_path/"too-fine").exists()


def test_convergence_needs_two_actual_refinement_steps_and_separate_stress(saved):
    report = saved[2]
    levels = [copy.deepcopy(report) for _ in range(3)]
    for level, pitch in zip(levels, [3, 2, 1]):
        level["mesh"]["cell_mm"] = pitch
    assert convergence_summary(levels, 5, 10)["status"] == "within_tolerance"
    levels[1]["load_cases"][0]["peak_surface_stress_mpa"] *= 2
    result = convergence_summary(levels, 5, 10)
    assert result["status"] == "not_converged"
    assert result["metrics"][0]["last_two_steps_within_tolerance"]
    assert not result["metrics"][1]["last_two_steps_within_tolerance"]
    levels[2]["mesh"]["cell_mm"] = 2
    assert convergence_summary(levels, 5, 10)["status"] == "not_assessed"


def test_cli_queries_and_goals_work_outside_checkout(saved, tmp_path):
    _, path, report = saved
    command = str(ROOT/".venv/bin/makersim")
    result = subprocess.run([command, "result", str(path), "--view", "metrics"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "fields" not in json.loads(result.stdout)["load_cases"][0]
    page = subprocess.run([command, "field", str(path), "--case", report["load_cases"][0]["name"],
        "--field", "surface_stress_mpa", "--limit", "2"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert page.returncode == 0 and json.loads(page.stdout)["returned"] == 2
