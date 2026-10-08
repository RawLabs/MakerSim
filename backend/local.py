"""Local agent operations. No HTTP server, browser session, or model store."""
import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from pydantic import ValidationError

from .agent_schemas import LocalTestSpec
from .geometry import read_stl
from .materials import MATERIALS
from .schemas import Patch, SimulationRequest
from .solver import SimulationError, create_volume, patch_nodes, solve

SCHEMA_VERSION = 1
SOLVER_VERSION = "0.1.0"
MAX_STL_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024 * 1024


class AgentError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"error": {"code": self.code, "message": str(self), "details": self.details}}


def read_bytes(path, limit):
    try:
        with Path(path).expanduser().open("rb") as source:
            data = source.read(limit + 1)
    except OSError as error:
        raise AgentError("file_error", f"Cannot read {path}: {error.strerror}") from error
    if len(data) > limit:
        raise AgentError("file_too_large", f"{path} exceeds the {limit} byte limit.")
    return data


def read_json(path, limit=MAX_JSON_BYTES):
    def reject_constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    def finite_float(value):
        number = float(value)
        if not np.isfinite(number):
            raise ValueError(f"Nonfinite JSON number: {value}")
        return number
    try:
        return json.loads(read_bytes(path, limit), parse_constant=reject_constant, parse_float=finite_float)
    except (ValueError, UnicodeDecodeError) as error:
        if isinstance(error, AgentError):
            raise
        raise AgentError("invalid_json", f"Cannot read JSON from {path}: {error}") from error


def validate_test(spec):
    try:
        return LocalTestSpec.model_validate(spec)
    except ValidationError as error:
        details = [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]
        raise AgentError("invalid_test", "The test specification is invalid.", details) from error


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sampling_metadata(positions, radius):
    # Hash coordinates, not node IDs: node numbering can change between CAD revisions.
    positions = np.asarray(positions)
    coordinates = np.round(positions, 9)
    coordinates[coordinates == 0] = 0  # Canonicalize negative zero.
    coordinates = coordinates[np.lexsort(coordinates.T[::-1])]
    return {"sampled_nodes": len(coordinates), "actual_radius_mm": radius,
            "sample_bounds_mm": [positions.min(axis=0).tolist(), positions.max(axis=0).tolist()],
            "coordinates_sha256": canonical_hash(coordinates.tolist()), "coordinate_precision_mm": 1e-9}


def sampling_comparison(before, after):
    metadata_keys = ("sampled_nodes", "actual_radius_mm", "sample_bounds_mm", "coordinates_sha256", "weights_sha256", "coordinate_precision_mm")
    before = {key: before[key] for key in metadata_keys if key in before}
    after = {key: after[key] for key in metadata_keys if key in after}
    hashes = before.get("coordinates_sha256"), after.get("coordinates_sha256")
    coordinates_changed = hashes[0] != hashes[1] if all(hashes) else None
    keys = ("sampled_nodes", "actual_radius_mm", "sample_bounds_mm", "weights_sha256")
    comparable = [key for key in keys if key in before and key in after]
    coverage_changed = any(before[key] != after[key] for key in comparable) if comparable else None
    status = "changed" if coordinates_changed or coverage_changed else "unchanged" if coordinates_changed is False else "unknown"
    return {"status": status, "coordinates_changed": coordinates_changed,
            "coverage_changed": coverage_changed, "baseline": before, "candidate": after}


def metric_change(before, after):
    return {"baseline": before, "candidate": after, "change": after-before,
            "change_percent": (after/before-1)*100 if before else None}


def load_mesh(stl_path, unit):
    if unit not in ("mm", "inch"):
        raise AgentError("invalid_unit", "STL units must be mm or inch.")
    path = Path(stl_path).expanduser().resolve()
    if path.suffix.lower() != ".stl":
        raise AgentError("invalid_stl", "Choose an .stl file.")
    data = read_bytes(path, MAX_STL_BYTES)
    try:
        mesh = read_stl(data, unit)
    except ValueError as error:
        raise AgentError("invalid_stl", str(error)) from error
    return mesh, {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "stl_unit": unit}


def mesh_metadata(mesh):
    return {
        "dimensions_mm": mesh.extents.tolist(),
        "centered_bounds_mm": mesh.bounds.tolist(),
        "source_bounds_mm": (mesh.bounds + mesh.source_center_mm).tolist(),
        "source_center_mm": mesh.source_center_mm.tolist(),
        "coordinate_transform": "centered_mm = source_mm - source_center_mm; vectors are unchanged",
        "surface_area_mm2": mesh.area,
        "triangles": len(mesh.faces),
        "watertight": mesh.is_watertight,
        "consistent_winding": mesh.has_consistent_winding,
    }


def inspect_model(stl_path, unit="mm"):
    mesh, source = load_mesh(stl_path, unit)
    metadata = mesh_metadata(mesh)
    try:
        volume = create_volume(mesh)
    except (SimulationError, ValueError) as error:
        return {"source": source, **metadata, "simulation_ready": False, "notes": [str(error)]}
    return {"source": source, **metadata, "simulation_ready": True,
            "solid_volume_mm3": abs(mesh.volume),
            "mesh": {"nodes": len(volume.nodes), "elements": len(volume.tets), "cell_mm": volume.pitch},
            "notes": ["Quick voxel mesh; mesh convergence is not established."]}


def list_materials(material_id=None):
    if material_id is not None:
        material = MATERIALS.get(material_id)
        if material is None:
            raise AgentError("unknown_material", f"Unknown material: {material_id}")
        return material
    return {"materials": [{key: value[key] for key in ("id", "brand", "name", "family", "supported")}
                          for value in MATERIALS.values()]}


def centered_patch(patch, spec, mesh):
    point = np.array(patch.point)
    if spec.coordinate_frame == "source_mm":
        point -= mesh.source_center_mm
    return Patch(point=tuple(point), normal=patch.normal, radius=patch.radius)


def write_json(path, value):
    """Publish a complete report without overwriting an existing artifact."""
    target = Path(path).expanduser().resolve()
    temp = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as output:
            temp = Path(output.name)
            json.dump(value, output, indent=2, allow_nan=False)
            output.write("\n")
        # Hard-link publication is atomic and fails if the destination exists.
        target.hardlink_to(temp)
    except FileExistsError as error:
        raise AgentError("output_exists", f"{target} already exists. Choose a new report filename.") from error
    except OSError as error:
        raise AgentError("file_error", f"Cannot write {target}: {error.strerror}") from error
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return str(target)


def run_test(stl_path, spec, output_path=None, include_fields=False, revision=None, *,
             images_dir=None, stress_max_mpa=None, movement_scale=1,
             parent_report=None, goal=None, branch=None, resolution=28, max_cells=4800,
             strict_pitch=False, camera=None, views=None, detail_bounds_mm=None):
    spec = validate_test(spec)
    material = MATERIALS.get(spec.material_id)
    if material is None or not material.get("supported"):
        raise AgentError("unsupported_material", "Choose a material with supported stiffness data.")
    mesh, source = load_mesh(stl_path, spec.stl_unit)
    source["revision"] = revision
    if output_path is not None and Path(output_path).expanduser().exists():
        raise AgentError("output_exists", "Choose a new report filename; this path already exists.")
    parent = None
    if parent_report is not None:
        previous = get_result(parent_report)
        parent_path = Path(parent_report).expanduser().resolve()
        parent = {"report_path": str(parent_path), "report_sha256": hashlib.sha256(read_bytes(parent_path, MAX_JSON_BYTES)).hexdigest(),
                  "revision": previous["source"].get("revision"), "source_sha256": previous["source"]["sha256"],
                  "test_sha256": previous["test_sha256"]}
    for value, limit, label in ((goal, 5000, "goal"), (branch, 200, "branch")):
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > limit):
            raise AgentError("invalid_iteration", f"Iteration {label} must contain 1–{limit} characters.")
    if images_dir is not None:
        from .capture import capture_options, camera_options
        capture_options(stress_max_mpa, movement_scale)
        camera_options(camera, views, detail_bounds_mm)
        if Path(images_dir).expanduser().exists():
            raise AgentError("output_exists", "Choose a new capture directory; this path already exists.")
    try:
        volume = create_volume(mesh, resolution, max_cells=max_cells, strict_pitch=strict_pitch)
        fixtures = [centered_patch(patch, spec, mesh) for patch in spec.fixtures]
        probe_nodes = [(probe, *patch_nodes(volume, centered_patch(probe, spec, mesh))) for probe in spec.surface_probes]
        # The result fields contain all surface nodes, in this same order.
        surface_index = {int(node): index for index, node in enumerate(volume.surface)}
        cases = []
        for case in spec.load_cases:
            request = SimulationRequest(model_id="local", material_id=spec.material_id,
                print_settings=spec.print_settings, fixtures=fixtures,
                load=centered_patch(case.load, spec, mesh), direction=case.direction,
                magnitude=case.magnitude, unit=case.unit)
            result = solve(volume, mesh, material, request)
            positions = np.array(result["positions"])
            if spec.coordinate_frame == "source_mm":
                positions += mesh.source_center_mm
            displacement = np.array(result["displacement"])
            hotspot = int(np.argmax(result["stress"]))
            requirements = []
            if case.max_displacement_mm is not None:
                requirements.append({"metric": "max_displacement_mm", "value": result["max_displacement_mm"],
                    "limit": case.max_displacement_mm, "met": result["max_displacement_mm"] <= case.max_displacement_mm})
            probes = []
            for probe, nodes, radius in probe_nodes:
                indices = [surface_index[int(node)] for node in nodes]
                maximum = float(np.linalg.norm(displacement[indices], axis=1).max())
                probes.append({"name": probe.name, "requested_point_mm": list(probe.point),
                    **sampling_metadata(positions[indices], radius),
                    "max_displacement_mm": maximum, "mean_displacement_vector_mm": displacement[indices].mean(axis=0).tolist()})
                if probe.max_displacement_mm is not None:
                    requirements.append({"metric": "surface_probe_max_displacement_mm", "probe": probe.name,
                        "value": maximum, "limit": probe.max_displacement_mm, "met": maximum <= probe.max_displacement_mm})
            actual_holds = []
            for patch in fixtures:
                nodes, radius = patch_nodes(volume, patch)
                hold_positions = volume.nodes[nodes] + (mesh.source_center_mm if spec.coordinate_frame == "source_mm" else 0)
                actual_holds.append({"nodes": len(nodes), **sampling_metadata(hold_positions, radius)})
            loaded_nodes = patch_nodes(volume, request.load)[0]
            loaded_positions = volume.nodes[loaded_nodes] + (mesh.source_center_mm if spec.coordinate_frame == "source_mm" else 0)
            load_sampling = sampling_metadata(loaded_positions, result["patches"]["load_radius_mm"])
            delta = volume.nodes[loaded_nodes] - np.asarray(request.load.point)
            weights = np.maximum(.08, 1-np.linalg.norm(delta, axis=1)/(result["patches"]["load_radius_mm"]+volume.pitch))
            weights /= weights.sum()
            order = np.lexsort(np.round(loaded_positions, 9).T[::-1])
            load_sampling["weights_sha256"] = canonical_hash(np.round(weights[order], 12).tolist())
            summary = {
                "name": case.name, "analysis_status": "solved",
                "criteria_status": "not_assessed" if not requirements else "met" if all(r["met"] for r in requirements) else "not_met",
                "force_newtons": result["force_newtons"], "max_displacement_mm": result["max_displacement_mm"],
                "peak_surface_stress_mpa": max(result["stress"]), "stress_hotspot_mm": positions[hotspot].tolist(),
                "surface_probes": probes, "requirements": requirements, "checks": result["checks"],
                "patches": {**result["patches"], "holds": actual_holds,
                    "load_sampling": load_sampling,
                    "loaded_sample_bounds_mm": [loaded_positions.min(axis=0).tolist(), loaded_positions.max(axis=0).tolist()]},
                "effective_material": result["effective_material"],
                "validity": {"small_displacement": result["max_displacement_mm"] <= float(mesh.extents.max())*.02,
                    "mesh_convergence": "not_checked"}, "notes": result["notes"],
            }
            if include_fields or images_dir is not None:
                summary["fields"] = {"positions_mm": positions.tolist(), "displacement_mm": result["displacement"],
                                     "surface_stress_mpa": result["stress"]}
            cases.append(summary)
    except (SimulationError, ValueError, np.linalg.LinAlgError) as error:
        if isinstance(error, AgentError):
            raise
        raise AgentError("simulation_failed", str(error)) from error
    specification = spec.model_dump(mode="json")
    report = {
        "schema_version": SCHEMA_VERSION, "solver_version": SOLVER_VERSION, "source": source,
        "test_spec": specification, "test_sha256": canonical_hash(specification),
        "material_physics_sha256": canonical_hash(material["physics"]), "material_provenance": material["provenance"],
        "model": {**mesh_metadata(mesh), "solid_volume_mm3": abs(mesh.volume)},
        "mesh": {"nodes": len(volume.nodes), "elements": len(volume.tets), "cell_mm": volume.pitch,
                 "requested_resolution": resolution, "max_cells": max_cells,
                 "requested_cell_mm": float(mesh.extents.max())/resolution,
                 "coarsened_for_budget": volume.pitch > float(mesh.extents.max())/resolution * (1+1e-9)},
        "analysis_status": "solved",
        "criteria_status": "not_met" if any(c["criteria_status"] == "not_met" for c in cases)
            else "met" if all(c["criteria_status"] == "met" for c in cases)
            else "partially_assessed" if any(c["criteria_status"] == "met" for c in cases) else "not_assessed",
        "load_cases": cases,
        "limitations": ["Linear-elastic preview; criteria refer to calculated displacement, not failure or safe working loads.",
                        "Coarse mesh and heuristic print properties; physical accuracy and mesh convergence are not established."],
    }
    if parent is not None or goal is not None or branch is not None:
        report["iteration"] = {"goal": goal or spec.name, "branch": branch, "parent": parent,
                               "parent_test_matches": parent["test_sha256"] == report["test_sha256"] if parent else None}
    if images_dir is not None:
        from .capture import export_captures
        report["captures"] = export_captures(report, images_dir, stl_path, stress_max_mpa, movement_scale,
                                             camera=camera, views=views, detail_bounds_mm=detail_bounds_mm)
        if not include_fields:
            for case in cases:
                del case["fields"]
    if output_path is not None:
        report["report_path"] = str(Path(output_path).expanduser().resolve())
        write_json(output_path, report)
    return report


def get_result(path, view="full"):
    if view not in ("full", "metrics"):
        raise AgentError("invalid_query", "Report view must be full or metrics.")
    report = read_json(path)
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise AgentError("invalid_report", "Expected a MakerSim schema version 1 report.")
    try:
        spec = validate_test(report["test_spec"])
        if canonical_hash(report["test_spec"]) != report["test_sha256"]:
            raise AgentError("invalid_report", "The report test specification hash does not match.")
        if [case["name"] for case in report["load_cases"]] != [case.name for case in spec.load_cases]:
            raise AgentError("invalid_report", "The report load cases do not match its test specification.")
        for case in report["load_cases"]:
            if not isinstance(case["validity"], dict) or not isinstance(case["patches"], dict):
                raise AgentError("invalid_report", "Invalid validity or patch metadata.")
            for metric in ("max_displacement_mm", "force_newtons", "peak_surface_stress_mpa"):
                if type(case[metric]) not in (int, float) or not np.isfinite(case[metric]) or case[metric] < 0:
                    raise AgentError("invalid_report", f"Invalid report metric: {metric}")
            if case["criteria_status"] not in ("met", "not_met", "not_assessed"):
                raise AgentError("invalid_report", "Invalid case criteria status.")
            if [probe["name"] for probe in case["surface_probes"]] != [probe.name for probe in spec.surface_probes]:
                raise AgentError("invalid_report", "Report probes do not match the test specification.")
            for probe in case["surface_probes"]:
                bounds = np.asarray(probe["sample_bounds_mm"], dtype=float)
                if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
                    raise AgentError("invalid_report", "Invalid probe coordinates.")
                value = probe["max_displacement_mm"]
                if type(value) not in (int, float) or not np.isfinite(value) or value < 0:
                    raise AgentError("invalid_report", "Invalid probe displacement.")
        for key in ("solver_version", "material_physics_sha256", "source", "model", "mesh", "criteria_status"):
            report[key]
        value = report["model"]["solid_volume_mm3"]
        if type(value) not in (int, float) or not np.isfinite(value) or value <= 0:
            raise AgentError("invalid_report", "Invalid solid volume.")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, AgentError) and error.code == "invalid_report":
            raise
        raise AgentError("invalid_report", "The report is incomplete or malformed.") from error
    if view == "metrics":
        for case in report["load_cases"]:
            fields = case.pop("fields", None)
            case["field_summary"] = {"available": fields is not None,
                "fields": list(fields) if isinstance(fields, dict) else [],
                "surface_nodes": len(fields.get("positions_mm", [])) if isinstance(fields, dict) else 0}
    return report


def compare_runs(baseline_path, candidate_path):
    baseline, candidate = get_result(baseline_path), get_result(candidate_path)
    differences = [key for key in ("test_sha256", "solver_version", "material_physics_sha256") if baseline[key] != candidate[key]]
    if differences:
        raise AgentError("incompatible_runs", "Run both revisions with the same test, material properties, and solver version.", differences)
    cases = []
    for before, after in zip(baseline["load_cases"], candidate["load_cases"]):
        metrics = {}
        for key in ("max_displacement_mm", "peak_surface_stress_mpa"):
            a, b = before[key], after[key]
            metrics[key] = metric_change(a, b)
        probes = []
        for old, new in zip(before["surface_probes"], after["surface_probes"]):
            a, b = old["max_displacement_mm"], new["max_displacement_mm"]
            probes.append({"name": old["name"], "max_displacement_mm": {"baseline": a, "candidate": b,
                "change": b-a, "change_percent": (b/a-1)*100 if a else None},
                "baseline_sample_bounds_mm": old["sample_bounds_mm"], "candidate_sample_bounds_mm": new["sample_bounds_mm"],
                "sampling": sampling_comparison(old, new)})
        def load_metadata(case):
            return case["patches"].get("load_sampling", {"sampled_nodes": case["patches"].get("loaded_nodes"),
                "actual_radius_mm": case["patches"].get("load_radius_mm"),
                "sample_bounds_mm": case["patches"].get("loaded_sample_bounds_mm")})
        def hold_metadata(hold):
            return {**hold, "sampled_nodes": hold.get("sampled_nodes", hold.get("nodes"))}
        old_holds, new_holds = before["patches"].get("holds", []), after["patches"].get("holds", [])
        sampling = {"load": sampling_comparison(load_metadata(before), load_metadata(after)),
            "fixtures": [sampling_comparison(hold_metadata(a), hold_metadata(b)) for a, b in zip(old_holds, new_holds)],
            "fixture_count_changed": len(old_holds) != len(new_holds)}
        stiffness = {key: metric_change(before["effective_material"][key], after["effective_material"][key])
                     for key in ("modulus_xy_mpa", "modulus_build_mpa", "solid_fraction")
                     if key in before.get("effective_material", {}) and key in after.get("effective_material", {})}
        cases.append({"name": before["name"], "metrics": metrics, "surface_probes": probes,
                      "sampling": sampling, "effective_material": stiffness,
                      "baseline_validity": before["validity"], "candidate_validity": after["validity"],
                      "baseline_patches": before["patches"], "candidate_patches": after["patches"],
                      "baseline_criteria_status": before["criteria_status"],
                      "candidate_criteria_status": after["criteria_status"]})
    sampling_states = [item["status"] for case in cases for item in
        [case["sampling"]["load"], *case["sampling"]["fixtures"], *[probe["sampling"] for probe in case["surface_probes"]]]]
    return {"conditions_match": True, "mesh_convergence_verified": False,
        "sampling_status": "changed" if "changed" in sampling_states or any(c["sampling"]["fixture_count_changed"] for c in cases)
            else "unknown" if not sampling_states or "unknown" in sampling_states else "unchanged",
        "baseline_source": baseline["source"], "candidate_source": candidate["source"],
        "baseline_mesh": baseline["mesh"], "candidate_mesh": candidate["mesh"],
        "solid_volume_change_mm3": candidate["model"]["solid_volume_mm3"] - baseline["model"]["solid_volume_mm3"],
        "solid_volume_mm3": metric_change(baseline["model"]["solid_volume_mm3"], candidate["model"]["solid_volume_mm3"]),
        "mesh_pitch_changed": baseline["mesh"]["cell_mm"] != candidate["mesh"]["cell_mm"],
        "load_cases": cases,
        "notes": ["Changes are numerical comparisons; changed mesh sampling and patch coverage can affect the result.",
                  "Check each report's validity, surface probes and notes before accepting a design improvement."]}
