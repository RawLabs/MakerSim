"""Local stdio MCP tools over the same operations as the CLI."""
import json
import base64
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from .agent_schemas import ComparisonGoals, LocalTestSpec
from .local import AgentError, compare_runs as compare_local_runs, get_result as get_local_result
from .local import inspect_model as inspect_local_model, list_materials as list_local_materials
from .local import read_bytes, read_json, run_test as run_local_test, validate_test as validate_local_test
from .capture import capture_report as capture_local_report, create_review as create_local_review
from .workflow import WORKFLOW_INSTRUCTIONS, get_workflow as get_local_workflow
from .numerical import check_convergence as local_convergence, evaluate_goals as local_goals, query_field as local_query_field

mcp = FastMCP("MakerSim", instructions=WORKFLOW_INSTRUCTIONS)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
RUN = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def call_local(function, *args, preview=False, **kwargs):
    try:
        value = function(*args, **kwargs)
        captures = value.get("captures", value)
        if preview and captures.get("images"):
            selected = [entry for entry in captures["images"] if entry["view"] in ("geometry", "stress_isometric")][:2]
            if len(selected) < 2:
                selected += [entry for entry in captures["images"] if entry not in selected][:2-len(selected)]
            return CallToolResult(structuredContent=value, content=[TextContent(type="text", text=json.dumps(value))]+[
                ImageContent(type="image", mimeType="image/png", data=base64.b64encode(read_bytes(entry["path"], 10*1024*1024)).decode("ascii")) for entry in selected])
        return value
    except AgentError as error:
        # Preserve stable error codes for an agent to recover programmatically.
        value = error.as_dict()
        return CallToolResult(isError=True, structuredContent=value,
                             content=[TextContent(type="text", text=json.dumps(value))])


@mcp.tool(annotations=READ_ONLY)
def get_workflow() -> dict[str, Any]:
    """Read the CAD → test → capture → review → user selection → branch workflow before a design task."""
    return get_local_workflow()


@mcp.tool(annotations=READ_ONLY)
def get_test_schema() -> dict[str, Any]:
    """Get the complete JSON schema for test files, load cases and surface probes."""
    return LocalTestSpec.model_json_schema()


@mcp.tool(annotations=READ_ONLY)
def get_goal_schema() -> dict[str, Any]:
    """Get the JSON schema for percentage changes against a baseline, including named probe goals."""
    return ComparisonGoals.model_json_schema()


@mcp.tool(annotations=READ_ONLY)
def list_materials(material_id: str | None = None) -> dict[str, Any]:
    """List supported/unsupported material IDs, or get one material's stiffness and provenance."""
    return call_local(list_local_materials, material_id)


@mcp.tool(annotations=READ_ONLY)
def inspect_model(stl_path: str, unit: Literal["mm", "inch"] = "mm") -> dict[str, Any]:
    """Read a local STL, return its original coordinate bounds and check coarse-mesh readiness. unit: mm or inch."""
    return call_local(inspect_local_model, stl_path, unit)


@mcp.tool(annotations=READ_ONLY)
def validate_test(test_path: str) -> dict[str, Any]:
    """Validate a saved JSON test file and return its normalized specification without solving."""
    def validate():
        spec = validate_local_test(read_json(test_path, limit=1024*1024))
        return {"valid": True, "test_spec": spec.model_dump(mode="json")}
    return call_local(validate)


@mcp.tool(annotations=RUN)
def run_test(stl_path: str, test_path: str, output_path: str | None = None,
             include_fields: bool = False, revision: str | None = None,
             images_dir: str | None = None, stress_max_mpa: float | None = None, movement_scale: float = 1,
             parent_report: str | None = None, goal: str | None = None, branch: str | None = None,
             resolution: int = 28, max_cells: int = 4800, strict_pitch: bool = False,
             camera: dict[str, Any] | None = None, views: list[str] | None = None,
             detail_bounds_mm: list[list[float]] | None = None) -> dict[str, Any]:
    """Solve a local STL against all cases in a JSON test file. Optionally save a new report; never overwrite one.

    Reports contain absolute measurements, numerical criteria, stress coordinates, patch coverage,
    provenance and validity notes. images_dir saves geometry/stress/movement PNGs and returns
    geometry and stress previews. Use a common stress_max_mpa and movement_scale across revisions.
    parent_report, goal and branch preserve lineage; goal text does not set numerical criteria.
    Full surface fields are opt-in. Points and radii are in mm. resolution controls mesh refinement;
    strict_pitch fails instead of coarsening for max_cells. camera accepts elevation/azimuth/zoom/projection;
    views may include geometry_underside, stress_underside and stress_detail (needs detail_bounds_mm).
    """
    def run():
        return run_local_test(stl_path, read_json(test_path, limit=1024*1024), output_path, include_fields, revision,
            images_dir=images_dir, stress_max_mpa=stress_max_mpa, movement_scale=movement_scale,
            parent_report=parent_report, goal=goal, branch=branch, resolution=resolution, max_cells=max_cells,
            strict_pitch=strict_pitch, camera=camera, views=views, detail_bounds_mm=detail_bounds_mm)
    return call_local(run, preview=images_dir is not None)


@mcp.tool(annotations=READ_ONLY)
def get_result(report_path: str, view: Literal["full", "metrics"] = "full") -> dict[str, Any]:
    """Read a saved report. Prefer view=metrics to omit large field arrays; query_field retrieves bounded regions."""
    return call_local(get_local_result, report_path, view)


@mcp.tool(annotations=READ_ONLY)
def query_field(report_path: str, load_case: str, field: Literal["positions_mm", "displacement_mm", "surface_stress_mpa"],
                offset: int = 0, limit: int = 100, bounds_mm: list[list[float]] | None = None) -> dict[str, Any]:
    """Read at most 1000 saved surface samples, paged and optionally filtered by a bounding box in report coordinates."""
    return call_local(local_query_field, report_path, load_case, field, offset, limit, bounds_mm)


@mcp.tool(annotations=RUN)
def evaluate_goals(baseline_path: str, candidate_path: str, goals: dict[str, Any], output_path: str | None = None) -> dict[str, Any]:
    """Evaluate structured percentage-change goals. A -30 at_most movement limit means at least 30% less movement.

    Use get_goal_schema. This assessment is separate from absolute report criteria and mesh confidence.
    """
    return call_local(local_goals, baseline_path, candidate_path, goals, output_path)


@mcp.tool(annotations=RUN)
def check_convergence(stl_path: str, test_path: str, output_dir: str, resolutions: list[int] | None = None,
                      max_cells: int = 12000, displacement_tolerance_percent: float = 5,
                      stress_tolerance_percent: float = 10) -> dict[str, Any]:
    """Solve 3–6 increasing resolutions (default 28/35/44). Save reports and observed stability of the last two steps.

    Strict mesh budgets prevent silent coarsening. Reports include sampling changes; stability is not physical validation.
    """
    def run():
        return local_convergence(stl_path, read_json(test_path, limit=1024*1024), output_dir, resolutions,
                                 max_cells, displacement_tolerance_percent, stress_tolerance_percent)
    return call_local(run)


@mcp.tool(annotations=READ_ONLY)
def compare_runs(baseline_path: str, candidate_path: str) -> dict[str, Any]:
    """Compare two saved reports. Reject changed tests/material physics/solver versions; report mesh limitations."""
    return call_local(compare_local_runs, baseline_path, candidate_path)


@mcp.tool(annotations=RUN)
def capture_result(report_path: str, output_dir: str, stl_path: str | None = None,
                   stress_max_mpa: float | None = None, movement_scale: float = 1,
                   camera: dict[str, Any] | None = None, views: list[str] | None = None,
                   detail_bounds_mm: list[list[float]] | None = None) -> dict[str, Any]:
    """Capture a saved report with full fields into a new folder and return geometry/stress images for vision.

    No solve, browser or display server is required. A relocated STL must match the report hash.
    Output includes a standalone review HTML and an image manifest. Stress colors show concentration.
    camera: elevation/azimuth/zoom/projection. views: geometry, stress_isometric, stress_side, movement,
    geometry_underside, stress_underside, stress_detail. Detail requires a bounding box in report coordinates.
    """
    return call_local(capture_local_report, report_path, output_dir, stl_path, stress_max_mpa, movement_scale,
                      camera=camera, views=views, detail_bounds_mm=detail_bounds_mm, preview=True)


@mcp.tool(annotations=RUN)
def create_review(report_paths: list[str], output_path: str) -> dict[str, Any]:
    """Create a portable visual history from saved reports with captures. Review any revision as a branch parent."""
    return call_local(create_local_review, report_paths, output_path)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
