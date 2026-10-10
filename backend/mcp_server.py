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
from .sessions import session_store

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


@mcp.tool(annotations=RUN)
def create_design_session(objective: str, project: str = 'My project', part: str = 'New part',
                          mode: Literal['interactive', 'auto', 'teaching'] = 'interactive',
                          source: Literal['selected', 'copy', 'scratch'] = 'copy',
                          max_iterations: int = 4, max_seconds: float = 1800, max_tokens: int | None = None) -> dict[str, Any]:
    """Save isolated design context. Connect a named agent separately; saving or reopening never starts work."""
    return call_local(session_store().create, {'objective': objective, 'project': project, 'part': part,
        'mode': mode, 'source': source, 'budgets': {'max_iterations': max_iterations, 'max_seconds': max_seconds, 'max_tokens': max_tokens}})


@mcp.tool(annotations=READ_ONLY)
def get_design_session(session_id: str, after: int = 0, limit: int = 100) -> dict[str, Any]:
    """Read saved context, agent connection and human messages. A live pause requires an explicit human resume."""
    return call_local(session_store().events, session_id, after, limit)


@mcp.tool(annotations=READ_ONLY)
def recall_design_context(session_id: str, query: str = '', limit: int = 30) -> dict[str, Any]:
    """Retrieve this part/branch's lessons, corrections and results. No other project or sibling branch is searched."""
    return call_local(session_store().context, session_id, query, limit)


@mcp.tool(annotations=RUN)
def connect_design_agent(session_id: str, agent_name: str, supports_controls: bool = False) -> dict[str, Any]:
    """Announce any compatible agent app by name. Connection alone never starts or resumes design work.

    Set supports_controls only if the client honors MakerSim pause/continue gates before every CAD/test step.
    The default is a viewing/feedback connection; work is authorized from the human's chosen agent app.
    Keep the returned run_id for this connection. A different run cannot inherit its execution authority.
    """
    return call_local(session_store().connect_agent, session_id, agent_name, supports_controls)


@mcp.tool(annotations=RUN)
def update_design_connection(session_id: str, run_id: str,
                             action: Literal['heartbeat', 'disconnect'] = 'heartbeat',
                             seen_sequence: int | None = None) -> dict[str, Any]:
    """Refresh agent presence every 30 seconds, including while paused; disconnect when the client stops.

    Browser streaming connectivity is separate from agent presence. A service restart requires a fresh heartbeat.
    Disconnection never erases saved geometry/history. Late updates from replaced connections are rejected.
    For disconnect, window-control clients must supply seen_sequence: the last event cursor read and handled.
    New human input after that cursor blocks disconnection. Read and handle it, then retry with the updated cursor.
    """
    return call_local(session_store().agent_signal, session_id, run_id, action, seen_sequence)


@mcp.tool(annotations=RUN)
def authorize_design_run(session_id: str, run_id: str, human_instruction: str,
                         action: Literal['start', 'resume'] = 'start') -> dict[str, Any]:
    """Relay an explicit human start/resume instruction from the chosen agent app. Never manufacture approval.

    Call start only for a newly connected run that the human asked to begin. Read scoped history first.
    Resume an interrupted live run only after the human explicitly resumes it. New instructions/questions,
    mode changes, reconnecting or reopening MakerSim are not approval. Recorded text is the trusted client's
    attestation of human intent, not independent proof. Starting a new run supersedes unfinished old leases.
    """
    return call_local(session_store().authorize_run, session_id, run_id, human_instruction, action)


@mcp.tool(annotations=RUN)
def publish_design_event(session_id: str, kind: Literal['plan', 'prediction', 'explanation', 'decision', 'lesson', 'checkpoint'],
                         message: str, phase: Literal['plan', 'do', 'check', 'act'] | None = None,
                         data: dict[str, Any] | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Explain visible work or save scoped memory. Lessons require data.evidence_type (hypothesis/simulation/human_observation/physical_test).

    Include evidence, conditions and rejected approaches. Explanations can answer questions while paused;
    they never restart work. Checkpoints pause when the human enables manual checkpoint continuation.
    """
    return call_local(session_store().publish, session_id, kind, message, data, phase, run_id)


@mcp.tool(annotations=RUN)
def report_design_activity(session_id: str, kind: Literal['thinking', 'explaining', 'waiting', 'working'],
                           message: str, operation_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Keep the human informed before text reasoning/explanations or while awaiting input. Never resumes a pause.

    Say what you are considering in plain language; do not expose private chain of thought.
    Refresh long activities every 30 seconds. Use working plus the active operation_id for CAD progress;
    sketching/simulation labels come from the operation itself. Use waiting when text work ends.
    Completed explanation events also clear explaining activity. Stale operation IDs are rejected.
    """
    return call_local(session_store().report_activity, session_id, kind, message, operation_id, run_id)


@mcp.tool(annotations=RUN)
def begin_design_step(session_id: str, description: str, phase: Literal['plan', 'do', 'check', 'act'] = 'do',
                       run_id: str | None = None) -> dict[str, Any]:
    """Acquire permission for ONE connected CAD operation. Call before each edit; stop on session_paused or budget_reached.

    Use the returned operation_id in finish_design_step. An already-running edit may reach its safe boundary
    after an interruption, but the next edit is blocked. Keep operations short for responsive intervention.
    """
    return call_local(session_store().begin_step, session_id, description, phase, run_id=run_id)


@mcp.tool(annotations=RUN)
def finish_design_step(session_id: str, operation_id: str, explanation: str, stl_path: str | None = None,
                        unit: Literal['mm', 'inch'] = 'mm', revision: str | None = None,
                        cad_reference: str | None = None, tokens_used: int = 0, error: str | None = None) -> dict[str, Any]:
    """Preserve a completed CAD edit and export a live STL snapshot to the viewport. Works at a paused safe boundary.

    The snapshot is immutable. cad_reference identifies the editable CAD revision. Report incremental token
    usage when available; it is accounting, not a provider cap. This tool never resumes a human pause.
    """
    return call_local(session_store().finish_step, session_id, operation_id, explanation, stl_path,
                      unit, revision, cad_reference, tokens_used, error)


@mcp.tool(annotations=RUN)
def run_session_test(session_id: str, test_path: str, stl_path: str | None = None,
                      revision: str | None = None, resolution: int = 28, max_cells: int = 4800,
                      run_id: str | None = None) -> dict[str, Any]:
    """Run the existing solver within session checkpoints/budgets; save immutable geometry/test/report and stream results.

    Omit stl_path to test the current saved revision. Units must match the test. Check scoped context and
    publish a prediction first. A solver in progress finishes safely after interruption, then remains paused.
    """
    return call_local(session_store().run_simulation, session_id, test_path, stl_path, revision, resolution, max_cells, run_id)


@mcp.tool(annotations=RUN)
def branch_design_session(session_id: str, sequence: int | None = None, objective: str | None = None) -> dict[str, Any]:
    """Create an isolated paused branch from an exact timeline event. Later parent work cannot bleed into its memory."""
    return call_local(session_store().fork, session_id, sequence, objective)


@mcp.tool(annotations=RUN)
def complete_design_session(session_id: str, summary: str, run_id: str | None = None) -> dict[str, Any]:
    """Stop with Candidate ready for review. Explain numerical outcomes, fit/print concerns and remaining physical validation."""
    return call_local(session_store().complete, session_id, summary, run_id)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
