"""Agent-facing contracts, coordinates and actual solver results."""
import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

from backend.geometry import read_stl, write_binary_stl
from backend.local import AgentError, compare_runs, get_result, inspect_model, read_json, run_test, validate_test, write_json
from tests.test_solver import box_mesh

ROOT = Path(__file__).resolve().parents[1]
STL = ROOT / "backend/data/backpack-bracket.stl"
TEST = ROOT / "backend/data/bracket-test.json"


@pytest.fixture
def spec():
    return read_json(TEST)


@pytest.fixture(scope="module")
def example():
    return run_test(STL, read_json(TEST), include_fields=True, revision="cad-r1")


def test_load_cases_and_probe_requirements_are_absolute(spec):
    spec["surface_probes"][0]["max_displacement_mm"] = 1
    report = run_test(STL, spec)
    light, heavy = report["load_cases"]
    assert 0 < light["max_displacement_mm"] < 1 < heavy["max_displacement_mm"]
    assert heavy["max_displacement_mm"] == pytest.approx(10*light["max_displacement_mm"])
    assert heavy["peak_surface_stress_mpa"] == pytest.approx(10*light["peak_surface_stress_mpa"])
    assert light["criteria_status"] == "met"
    assert heavy["criteria_status"] == report["criteria_status"] == "not_met"
    assert len(light["requirements"]) == 2
    assert heavy["surface_probes"][0]["max_displacement_mm"] == pytest.approx(10*light["surface_probes"][0]["max_displacement_mm"])
    assert "fields" not in light
    assert not heavy["validity"]["small_displacement"]
    assert heavy["checks"]["relative_force_balance"] < 1e-8


def test_source_and_centered_frames_have_same_physics(spec, example):
    center = np.array(example["model"]["source_center_mm"])
    spec["coordinate_frame"] = "centered_mm"
    patches = spec["fixtures"] + [case["load"] for case in spec["load_cases"]] + spec["surface_probes"]
    for patch in patches:
        patch["point"] = (np.array(patch["point"])-center).tolist()
    report = run_test(STL, spec, include_fields=True)
    for before, after in zip(example["load_cases"], report["load_cases"]):
        assert after["max_displacement_mm"] == pytest.approx(before["max_displacement_mm"])
        assert np.array(before["fields"]["positions_mm"])-center == pytest.approx(np.array(after["fields"]["positions_mm"]))
        assert after["fields"]["displacement_mm"] == before["fields"]["displacement_mm"]
        assert np.asarray(before["patches"]["load_centroid_mm"])-center == pytest.approx(after["patches"]["load_centroid_mm"])
        assert before["fields"]["surface_triangles"] == after["fields"]["surface_triangles"]


def test_translated_cad_export_preserves_source_coordinates(tmp_path, spec, example):
    mesh = read_stl(STL.read_bytes())
    offset = np.array([100., -200., 300.])
    moved = tmp_path / "moved.stl"
    moved.write_bytes(write_binary_stl(mesh.triangles+mesh.source_center_mm+offset))
    for patch in spec["fixtures"] + [c["load"] for c in spec["load_cases"]] + spec["surface_probes"]:
        patch["point"] = (np.array(patch["point"])+offset).tolist()
    report = run_test(moved, spec)
    assert np.array(report["model"]["source_bounds_mm"]) == pytest.approx(np.array(example["model"]["source_bounds_mm"])+offset)
    for old, new in zip(example["load_cases"], report["load_cases"]):
        assert new["max_displacement_mm"] == pytest.approx(old["max_displacement_mm"])
        assert new["stress_hotspot_mm"] == pytest.approx(np.array(old["stress_hotspot_mm"])+offset)


def test_inspection_units_and_open_surface(tmp_path):
    mm, inch = inspect_model(STL), inspect_model(STL, "inch")
    assert mm["simulation_ready"] and inch["simulation_ready"]
    assert inch["source_center_mm"] == pytest.approx(np.array(mm["source_center_mm"])*25.4)
    mesh = read_stl(STL.read_bytes())
    opened = tmp_path / "open.stl"
    opened.write_bytes(write_binary_stl(mesh.triangles[:-1]))
    assert not inspect_model(opened)["simulation_ready"]


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(extra=True),
    lambda s: s["load_cases"][0].update(direction=[0, 0, 0]),
    lambda s: s["load_cases"][0].update(magnitude=float("inf")),
    lambda s: s["load_cases"][1].update(name=s["load_cases"][0]["name"]),
])
def test_invalid_specs_are_actionable(spec, mutation):
    mutation(spec)
    with pytest.raises(AgentError) as error:
        validate_test(spec)
    assert error.value.code == "invalid_test" and error.value.details


@pytest.mark.parametrize("number", ["NaN", "Infinity", "1e309"])
def test_nonfinite_json_is_rejected(tmp_path, number):
    path = tmp_path / "invalid.json"
    path.write_text('{"value":'+number+'}')
    with pytest.raises(AgentError, match="Nonfinite"):
        read_json(path)


def test_failed_runs_do_not_publish_reports(tmp_path, spec):
    path = tmp_path / "report.json"
    spec["load_cases"][0]["load"] = copy.deepcopy(spec["fixtures"][0])
    with pytest.raises(AgentError) as error:
        run_test(STL, spec, path)
    assert error.value.code == "simulation_failed" and not path.exists()
    spec["material_id"] = "nonexistent"
    with pytest.raises(AgentError) as error:
        run_test(STL, spec)
    assert error.value.code == "unsupported_material"


def test_reports_preserve_artifacts_and_compare_fixed_tests(tmp_path, example):
    first, second = tmp_path / "r1.json", tmp_path / "r2.json"
    write_json(first, example)
    with pytest.raises(AgentError) as error:
        write_json(first, {"replacement": True})
    assert error.value.code == "output_exists"
    assert get_result(first)["source"]["revision"] == "cad-r1"
    candidate = copy.deepcopy(example)
    candidate["source"]["revision"] = "cad-r2"
    candidate["load_cases"][0]["max_displacement_mm"] *= .8
    write_json(second, candidate)
    comparison = compare_runs(first, second)
    assert comparison["load_cases"][0]["metrics"]["max_displacement_mm"]["change_percent"] == pytest.approx(-20)
    assert comparison["load_cases"][0]["surface_probes"]
    assert not comparison["mesh_convergence_verified"]
    changed = copy.deepcopy(example)
    changed["solver_version"] = "other"
    third = tmp_path / "r3.json"
    write_json(third, changed)
    with pytest.raises(AgentError) as error:
        compare_runs(first, third)
    assert error.value.code == "incompatible_runs"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r1.json", "r2.json", "r3.json"]


def test_report_hash_does_not_renormalize_direction(tmp_path, spec):
    spec["load_cases"][0]["direction"] = [1, 2, -3]
    path = tmp_path / "angled.json"
    report = run_test(STL, spec, path)
    assert get_result(path)["test_sha256"] == report["test_sha256"]
    report["test_spec"]["load_cases"][0]["magnitude"] += 1
    corrupted = tmp_path / "corrupted.json"
    write_json(corrupted, report)
    with pytest.raises(AgentError) as error:
        get_result(corrupted)
    assert error.value.code == "invalid_report"


def test_actual_geometry_revision_can_be_compared_under_same_goal(tmp_path):
    spec = {"name": "Cantilever tip movement", "fixtures": [
        {"point": [-30, 0, 0], "normal": [-1, 0, 0], "radius": 10}],
        "load_cases": [{"name": "downward", "load": {
            "point": [30, 0, 0], "normal": [1, 0, 0], "radius": 10},
            "direction": [0, 0, -1], "magnitude": 5, "max_displacement_mm": 1}],
        "surface_probes": [{"name": "tip", "point": [30, 0, 0], "normal": [1, 0, 0], "radius": 10}]}
    reports = []
    for revision, thickness in (("r1", 12), ("r2", 24)):
        model = tmp_path / f"{revision}.stl"
        model.write_bytes(write_binary_stl(box_mesh((60, 12, thickness)).triangles))
        report = tmp_path / f"{revision}.json"
        run_test(model, spec, report, revision=revision)
        reports.append(report)
    compared = compare_runs(*reports)
    assert compared["conditions_match"]
    assert compared["baseline_source"]["sha256"] != compared["candidate_source"]["sha256"]
    assert compared["solid_volume_change_mm3"] > 0
    assert compared["load_cases"][0]["metrics"]["max_displacement_mm"]["change_percent"] < 0
    assert compared["load_cases"][0]["surface_probes"][0]["max_displacement_mm"]["change_percent"] < 0


def test_cli_works_outside_checkout_and_errors_are_json(tmp_path):
    command = str(ROOT / ".venv/bin/makersim")
    output = tmp_path / "run.json"
    completed = subprocess.run([command, "run", str(STL), "--test", str(TEST), "--output", str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["criteria_status"] == "not_met"
    compared = subprocess.run([command, "compare", str(output), str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert compared.returncode == 0 and json.loads(compared.stdout)["conditions_match"]
    failed = subprocess.run([command, "inspect", str(tmp_path / "absent.stl")], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert failed.returncode == 2 and json.loads(failed.stdout)["error"]["code"] == "file_error"
