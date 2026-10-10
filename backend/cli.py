"""Machine-readable local entry point: python -m backend.cli or makersim."""
import argparse
import json

from .agent_schemas import ComparisonGoals, LocalTestSpec
from .local import AgentError, compare_runs, get_result, inspect_model, list_materials, read_json, run_test, validate_test
from .capture import capture_report, create_review
from .workflow import get_workflow
from .numerical import check_convergence, evaluate_goals, query_field
from .session_cli import add_commands as add_session_commands, execute as execute_session


def add_camera_options(parser):
    parser.add_argument("--elevation", type=float, default=24)
    parser.add_argument("--azimuth", type=float, default=-60)
    parser.add_argument("--zoom", type=float, default=1.15)
    parser.add_argument("--projection", choices=("ortho", "persp"), default="ortho")
    parser.add_argument("--views", nargs="+", help="Geometry/stress/movement view names; see README for underside and detail views.")
    parser.add_argument("--detail-bounds", nargs=6, type=float, metavar="MM", help="xmin ymin zmin xmax ymax zmax in report coordinates.")


def camera_arguments(args):
    return {"camera": {"elevation": args.elevation, "azimuth": args.azimuth, "zoom": args.zoom, "projection": args.projection},
            "views": args.views, "detail_bounds_mm": [args.detail_bounds[:3], args.detail_bounds[3:]] if args.detail_bounds else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run MakerSim locally for an AI agent. Successful commands print JSON.")
    commands = parser.add_subparsers(dest="command", required=True)
    add_session_commands(commands)
    commands.add_parser("schema", help="Print the JSON schema for a reusable test specification.")
    commands.add_parser("goal-schema", help="Print the JSON schema for relative comparison goals.")
    commands.add_parser("workflow", help="Read the agent design, test, capture, review and branching workflow.")
    materials = commands.add_parser("materials", help="List material IDs, or inspect one material's properties.")
    materials.add_argument("--id", dest="material_id")
    inspect = commands.add_parser("inspect", help="Inspect an STL and check whether the quick mesh can resolve it.")
    inspect.add_argument("stl_path")
    inspect.add_argument("--unit", choices=("mm", "inch"), default="mm")
    validate = commands.add_parser("validate", help="Validate a JSON test specification without solving.")
    validate.add_argument("test_path")
    run = commands.add_parser("run", help="Run all load cases in a saved test directly through the solver.")
    run.add_argument("stl_path")
    run.add_argument("--test", dest="test_path", required=True)
    run.add_argument("--output", dest="output_path", help="Save a report to a new file; existing files are preserved.")
    run.add_argument("--fields", action="store_true", help="Include full surface displacement and stress fields.")
    run.add_argument("--revision", help="Optional CAD revision reference recorded alongside the STL hash.")
    run.add_argument("--images", dest="images_dir", help="Save geometry, stress and movement PNGs to a new directory.")
    run.add_argument("--stress-max", dest="stress_max_mpa", type=float, help="Fixed stress legend maximum in MPa; use the same value across revisions.")
    run.add_argument("--movement-scale", type=float, default=1, help="Movement image display multiplier; default 1 (actual scale).")
    run.add_argument("--parent-report", help="Preserve the chosen parent revision's report reference and hash.")
    run.add_argument("--goal", help="Record intent; absolute criteria belong in the test, relative criteria in comparison goals.")
    run.add_argument("--branch", help="Record a branch label, for example r4-appearance.")
    run.add_argument("--resolution", type=int, default=28, help="Target cells along the longest dimension (8–100).")
    run.add_argument("--max-cells", type=int, default=4800, help="Bound occupied cells (up to 20000).")
    run.add_argument("--strict-pitch", action="store_true", help="Fail rather than coarsen a requested resolution for the cell budget.")
    add_camera_options(run)
    result = commands.add_parser("result", help="Read a saved report.")
    result.add_argument("path")
    result.add_argument("--view", choices=("full", "metrics"), default="full", help="metrics omits saved field arrays.")
    field = commands.add_parser("field", help="Query bounded pages of saved surface fields, optionally within a region.")
    field.add_argument("report_path")
    field.add_argument("--case", dest="load_case", required=True)
    field.add_argument("--field", required=True, choices=("positions_mm", "displacement_mm", "surface_stress_mpa"))
    field.add_argument("--offset", type=int, default=0)
    field.add_argument("--limit", type=int, default=100)
    field.add_argument("--bounds", nargs=6, type=float, metavar="MM")
    compare = commands.add_parser("compare", help="Compare two reports from the same test specification.")
    compare.add_argument("baseline_path")
    compare.add_argument("candidate_path")
    goals = commands.add_parser("evaluate", help="Evaluate relative numerical goals against a saved baseline.")
    goals.add_argument("baseline_path")
    goals.add_argument("candidate_path")
    goals.add_argument("--goals", dest="goals_path", required=True)
    goals.add_argument("--output", help="Save a new evaluation artifact.")
    converge = commands.add_parser("converge", help="Run a bounded mesh refinement study with immutable reports.")
    converge.add_argument("stl_path")
    converge.add_argument("--test", dest="test_path", required=True)
    converge.add_argument("--output-dir", required=True)
    converge.add_argument("--resolutions", nargs="+", type=int, default=[28, 35, 44])
    converge.add_argument("--max-cells", type=int, default=12000)
    converge.add_argument("--displacement-tolerance", type=float, default=5)
    converge.add_argument("--stress-tolerance", type=float, default=10)
    capture = commands.add_parser("capture", help="Capture an existing report with full fields, without rerunning the solver.")
    capture.add_argument("report_path")
    capture.add_argument("--output-dir", required=True)
    capture.add_argument("--stl", dest="stl_path", help="Original STL path if the saved file has moved; its hash must match.")
    capture.add_argument("--stress-max", dest="stress_max_mpa", type=float)
    capture.add_argument("--movement-scale", type=float, default=1)
    add_camera_options(capture)
    review = commands.add_parser("review", help="Create a standalone visual history from revision reports with captures.")
    review.add_argument("report_paths", nargs="+")
    review.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'session':
            output = execute_session(args)
        elif args.command == "schema":
            output = LocalTestSpec.model_json_schema()
        elif args.command == "goal-schema":
            output = ComparisonGoals.model_json_schema()
        elif args.command == "workflow":
            output = get_workflow()
        elif args.command == "materials":
            output = list_materials(args.material_id)
        elif args.command == "inspect":
            output = inspect_model(args.stl_path, args.unit)
        elif args.command == "validate":
            spec = validate_test(read_json(args.test_path, limit=1024*1024))
            output = {"valid": True, "test_spec": spec.model_dump(mode="json")}
        elif args.command == "run":
            output = run_test(args.stl_path, read_json(args.test_path, limit=1024*1024), args.output_path, args.fields, args.revision,
                images_dir=args.images_dir, stress_max_mpa=args.stress_max_mpa, movement_scale=args.movement_scale,
                parent_report=args.parent_report, goal=args.goal, branch=args.branch,
                resolution=args.resolution, max_cells=args.max_cells, strict_pitch=args.strict_pitch, **camera_arguments(args))
        elif args.command == "result":
            output = get_result(args.path, args.view)
        elif args.command == "field":
            output = query_field(args.report_path, args.load_case, args.field, args.offset, args.limit,
                                 [args.bounds[:3], args.bounds[3:]] if args.bounds else None)
        elif args.command == "compare":
            output = compare_runs(args.baseline_path, args.candidate_path)
        elif args.command == "evaluate":
            output = evaluate_goals(args.baseline_path, args.candidate_path, read_json(args.goals_path, limit=1024*1024), args.output)
        elif args.command == "converge":
            output = check_convergence(args.stl_path, read_json(args.test_path, limit=1024*1024), args.output_dir,
                args.resolutions, args.max_cells, args.displacement_tolerance, args.stress_tolerance)
        elif args.command == "capture":
            output = capture_report(args.report_path, args.output_dir, args.stl_path, args.stress_max_mpa, args.movement_scale,
                                    **camera_arguments(args))
        else:
            output = create_review(args.report_paths, args.output)
    except AgentError as error:
        print(json.dumps(error.as_dict(), allow_nan=False))
        return 2
    print(json.dumps(output, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
