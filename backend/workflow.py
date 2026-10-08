"""Discoverable guidance for CAD agents using the local MakerSim test bed."""

WORKFLOW_INSTRUCTIONS = """MakerSim is a local STL test bed; use the connected CAD tool to create and edit parts.
Follow the user's existing instructions, preferences and iteration budget. For a new part-design task:
1. Establish the functional goal, dimensions, mounting, loads, material/print settings and appearance preferences.
   Ask only for missing information that materially affects the design; record reasonable assumptions.
2. Use get_test_schema, inspect_model and list_materials. Save a versioned JSON test specification in source_mm
   coordinates. Keep the CAD origin, export axes, units and functional interfaces consistent across revisions.
3. Save baseline R0 as editable CAD plus an STL and a numerical report. For EVERY tested revision, use run_test
   with a new output_path, images_dir, revision, branch and goal. Preserve failures and explain proposed changes.
   Set a common stress_max_mpa and movement_scale for the branch so pictures can be compared meaningfully.
4. Iterate in CAD, save each revision, test it and use compare_runs against the baseline under identical conditions.
   Use get_goal_schema and evaluate_goals for relative targets such as 30% less tip movement and at most 20% more volume.
   Prefer get_result(view="metrics"); use query_field for bounded pages or a region around a hotspot.
   Read sampling_status and per-fixture/load/probe changes, effective_material changes, equilibrium and validity notes.
   Older reports without coordinate fingerprints have unknown sampling; do not claim identical samples from bounds alone.
   Before relying on small gains, use check_convergence on the leading designs and compare at common mesh settings.
   Inspect actual cell sizes, sampling changes and separate displacement/probe/stress stability. Budget errors require
   a larger bounded max_cells or a less demanding resolution; never claim that a coarsened mesh is a refinement.
   Use camera controls, geometry_underside/stress_underside views and stress_detail with detail_bounds_mm as needed.
   Keep design dimensions, fit, clearance, appearance,
   printability and numerical behavior in the decision; a lower displacement alone does not choose a design.
5. Use the user's iteration budget. If none was supplied, use a baseline and up to three useful candidate revisions
   for the first review checkpoint, stopping sooner when the goal is met or further changes need user direction.
   Use create_review to present the options with geometry/stress/movement images, tradeoffs and saved CAD references.
6. Wait for the user's design selection, acceptance or further directions at that checkpoint. A timeout or absent
   response is not a selection. Do not automatically accept a design or discard unselected or earlier revisions.
7. If the user picks any earlier revision (for example R4), retrieve that exact saved CAD revision, read its report
   and create a NEW branch from it. Record parent_report, a new branch label and the updated goal on each new run.
   If the test conditions or numerical limits change, rerun the selected parent's STL with the new test as the
   branch baseline; compare children to that new baseline. Keep the original test and all branches intact.
8. On acceptance, identify the chosen CAD/STL revision, its reports and known limitations. Acceptance of a numerical
   design is separate from physical validation; do not claim failure capacity or safe working loads.

Reports and captures are immutable artifacts: use new file/directory names. A goal string records design intent;
absolute numerical criteria belong in the test file; relative criteria belong in structured comparison goals.
Surface von Mises images show stress concentration, not
directional stress-flow lines. Linear elasticity, coarse voxel meshing and heuristic print properties limit accuracy.
If a requested CAD connection is unavailable, report the blocker. A permitted fallback must be clearly labeled;
do not describe locally generated CAD as created or saved in Onshape. No browser or web service is needed to solve.
"""


def get_workflow():
    return {"workflow_version": 2, "instructions": WORKFLOW_INSTRUCTIONS,
            "review_checkpoint": "user_selects_revision_or_accepts",
            "default_first_review": {"baseline_revisions": 1, "maximum_candidate_revisions": 3},
            "run_artifacts": ["editable_cad", "stl", "test_spec", "report", "captures", "revision_notes"],
            "branch_metadata": ["parent_report", "branch", "goal"]}
