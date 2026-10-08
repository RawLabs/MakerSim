"""Image provenance, physical field mapping, visual review and branch lineage."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
import pytest

from backend.capture import capture_report, create_review, export_captures, interpolate, refine_surface
from backend.geometry import write_binary_stl
from backend.local import AgentError, get_result, read_json, run_test, write_json
from tests.test_solver import box_mesh


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    root = tmp_path_factory.mktemp("captures")
    stl = root/"bar.stl"
    stl.write_bytes(write_binary_stl(box_mesh().triangles))
    spec = {"name": "Cantilever", "fixtures": [{"point": [-30, 0, 0], "normal": [-1, 0, 0], "radius": 10}],
            "load_cases": [{"name": "load", "load": {"point": [30, 0, 0], "normal": [1, 0, 0], "radius": 10},
                            "direction": [0, 0, -1], "magnitude": 5}]}
    path = root/"r1.json"
    report = run_test(stl, spec, path, revision="R1", images_dir=root/"r1-images",
                      stress_max_mpa=1, movement_scale=100, goal="Stiffness", branch="engineering")
    return root, stl, spec, path, report


def test_refinement_preserves_surface_and_interpolation_scales_with_load():
    mesh = box_mesh()
    vertices, faces = refine_surface(mesh.triangles, 2)
    assert len(faces) > len(mesh.faces)
    assert vertices.min(axis=0) == pytest.approx(mesh.bounds[0])
    assert vertices.max(axis=0) == pytest.approx(mesh.bounds[1])
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    displacement = np.array([[0, 0, -1], [0, 0, -2], [0, 0, -3], [0, 0, -4]], dtype=float)
    stress = np.array([1, 2, 3, 4], dtype=float)
    points = np.array([[.25, .25, .25], [.2, .3, .1]])
    u, s = interpolate(points, positions, displacement, stress, 1)
    large_u, large_s = interpolate(points, positions, displacement*10, stress*10, 1)
    assert large_u == pytest.approx(u*10)
    assert large_s == pytest.approx(s*10)


def test_run_images_are_real_pngs_without_bloating_compact_report(captured):
    root, _, _, path, report = captured
    assert "fields" not in report["load_cases"][0]
    assert "fields" not in get_result(path)["load_cases"][0]
    captures = report["captures"]
    assert captures["stress_max_mpa"] == 1 and captures["movement_display_scale"] == 100
    assert {image["view"] for image in captures["images"]} == {"geometry", "stress_isometric", "stress_side", "movement"}
    for image in captures["images"]:
        file = Path(image["path"])
        assert hashlib.sha256(file.read_bytes()).hexdigest() == image["sha256"]
        with Image.open(file) as picture:
            assert picture.format == "PNG" and picture.size == (1200, 840)
            rgb = np.asarray(picture.convert("RGB"))
            assert np.unique(rgb.reshape(-1, 3), axis=0).shape[0] > 100
    document = Path(captures["review_path"]).read_text()
    assert "data:image/png;base64," in document and "Stiffness" in document
    assert captures["report_path"] in document and "Unsaved report" not in document
    manifest = read_json(captures["manifest_path"])
    assert manifest["source_sha256"] == report["source"]["sha256"]
    archived = get_result(captures["report_path"])
    assert "fields" in archived["load_cases"][0]
    assert archived["source"] == report["source"]
    assert not list(root.glob(".makersim-capture-*"))


def test_branch_records_any_chosen_parent_and_supports_changed_goal(captured):
    root, stl, spec, path, baseline = captured
    branch_path = root/"appearance.json"
    branch = run_test(stl, spec, branch_path, revision="R1-A1", parent_report=path,
                      branch="appearance", goal="Prefer rounded exterior edges")
    assert branch["iteration"]["parent"]["source_sha256"] == baseline["source"]["sha256"]
    assert branch["iteration"]["parent"]["report_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert branch["iteration"]["parent_test_matches"]
    assert get_result(path)["iteration"]["goal"] == "Stiffness"
    changed = copy.deepcopy(spec)
    changed["load_cases"][0]["magnitude"] = 10
    other = run_test(stl, changed, parent_report=path, goal="Carry twice the force")
    assert not other["iteration"]["parent_test_matches"]
    assert other["test_sha256"] != baseline["test_sha256"]


def test_visual_review_escapes_goal_and_preserves_existing_output(captured):
    root, _, _, _, report = captured
    edited = copy.deepcopy(report)
    edited["iteration"]["goal"] = '<script>alert("x")</script>'
    edited_path = root/"escaped.json"
    write_json(edited_path, edited)
    output = root/"history.html"
    create_review([edited_path], output)
    text = output.read_text()
    assert "&lt;script&gt;" in text and '<script>alert("x")</script>' not in text
    original = output.read_bytes()
    with pytest.raises(AgentError) as error:
        create_review([edited_path], output)
    assert error.value.code == "output_exists" and output.read_bytes() == original


def test_capture_rejects_missing_fields_and_changed_stl(captured):
    root, stl, spec, path, _ = captured
    with pytest.raises(AgentError) as error:
        capture_report(path, root/"missing-fields")
    assert error.value.code == "fields_required" and not (root/"missing-fields").exists()
    fields = run_test(stl, spec, include_fields=True)
    changed = root/"changed.stl"
    changed.write_bytes(write_binary_stl(box_mesh((60, 12, 24)).triangles))
    with pytest.raises(AgentError) as error:
        export_captures(fields, root/"wrong-stl", changed)
    assert error.value.code == "source_changed"
    fields["load_cases"][0]["fields"]["surface_stress_mpa"][0] = float("nan")
    with pytest.raises(AgentError) as error:
        export_captures(fields, root/"bad-field")
    assert error.value.code == "invalid_fields"


def test_existing_captures_are_preserved(captured):
    root, stl, spec, _, report = captured
    first = Path(report["captures"]["images"][0]["path"])
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    with pytest.raises(AgentError) as error:
        run_test(stl, spec, images_dir=root/"r1-images")
    assert error.value.code == "output_exists" and hashlib.sha256(first.read_bytes()).hexdigest() == digest


def test_cli_recaptures_full_field_copy_and_builds_review(captured):
    root, _, _, _, report = captured
    command = str(Path(__file__).resolve().parents[1]/".venv/bin/makersim")
    captured_again = subprocess.run([command, "capture", report["captures"]["report_path"],
        "--output-dir", str(root/"recapture"), "--stress-max", "1", "--movement-scale", "100"],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert captured_again.returncode == 0, captured_again.stderr+captured_again.stdout
    output = json.loads(captured_again.stdout)
    assert output["source_sha256"] == report["source"]["sha256"]
    assert len(output["images"]) == 4
    reviewed = subprocess.run([command, "review", output["report_path"], "--output", str(root/"recaptured-review.html")],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert reviewed.returncode == 0, reviewed.stderr+reviewed.stdout
    assert Path(json.loads(reviewed.stdout)["review_path"]).exists()


@pytest.mark.parametrize("options", [{"stress_max_mpa": -1}, {"stress_max_mpa": float("nan")}, {"movement_scale": 0}, {"movement_scale": float("inf")}])
def test_invalid_display_options_are_actionable(captured, options):
    root, stl, spec, _, _ = captured
    with pytest.raises(AgentError) as error:
        run_test(stl, spec, images_dir=root/"invalid", **options)
    assert error.value.code == "invalid_capture" and not (root/"invalid").exists()


def test_camera_underside_and_detail_views_have_reproducible_settings(captured):
    root, _, _, _, report = captured
    full = report["captures"]["report_path"]
    options = {"camera": {"elevation": 32, "azimuth": -120, "zoom": 1.25},
               "views": ["geometry_underside", "stress_underside", "stress_detail"],
               "detail_bounds_mm": [[-15, -7, -7], [15, 7, 7]]}
    captures = capture_report(full, root/"detail-views", **options)
    assert captures["camera"]["azimuth"] == -120
    assert captures["detail_bounds_mm"] == options["detail_bounds_mm"]
    assert [image["view"] for image in captures["images"]] == options["views"]
    for entry in captures["images"]:
        with Image.open(entry["path"]) as image:
            assert image.size == (1200, 840)
    with pytest.raises(AgentError, match="camera"):
        capture_report(full, root/"invalid-camera", camera={"zoom": 100})
    with pytest.raises(AgentError, match="needs"):
        capture_report(full, root/"invalid-detail", views=["stress_detail"])
    assert not (root/"invalid-camera").exists() and not (root/"invalid-detail").exists()
