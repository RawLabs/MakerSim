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
When a persistent design session is attached (session_id supplied by the human or agent window):
1. Read get_design_session and recall_design_context before working. Keep all dimensions, corrections and
   lessons scoped to this project's part and branch. Do not retrieve another project's experience as a requirement.
   Connect using connect_design_agent with the actual agent app's name; this connection does not start work.
   Declare supports_controls only if your integration honors every MakerSim design-step gate. Default connections
   provide viewing and feedback. Saving/reopening a workspace never starts or resumes an agent.
   After the human explicitly asks to start, relay their actual instruction using authorize_design_run(start).
   A new run carries forward saved context, not old execution authority. Keep its run_id and pass it to
   publish_design_event, report_design_activity, begin_design_step, run_session_test and complete_design_session.
   Refresh update_design_connection every 30 seconds even while paused; disconnect when the agent stops.
   MakerSim/browser connectivity alone is not proof that an agent is connected. A service restart requires fresh
   agent evidence. Stale/replaced run IDs cannot claim activity or start design operations.
   Report thinking/explaining with report_design_activity before text work, with a brief plain-language purpose,
   not private chain of thought. Refresh long activities every 30 seconds. Report waiting when text work ends;
   publish_design_event(explanation) also clears explaining activity. Silence is never evidence of thinking.
2. Publish plan and prediction events, then begin_design_step BEFORE EACH connected CAD operation.
   Stop on session_paused, operation_active or budget_reached. A live interruption requires an explicit human resume,
   either through optional window controls or authorize_design_run(resume) relaying the human's agent-app instruction.
   Never invent authorization. Answering a question, changing mode or receiving new directions does not resume work.
   Feedback-only questions are stored without claiming to pause an external agent; read feedback events between steps.
   Human directions and Continue events can arrive in the MakerSim window without a new agent-app chat message.
   While waiting interactively, poll the session events and refresh the connection; check for pending human directions
   again before disconnecting or ending the turn. Acknowledge and handle those directions before reporting completion.
   Do not disconnect merely because the agent-app chat is quiet while the human is interacting in MakerSim.
   Disconnect using update_design_connection with seen_sequence set to the last event cursor actually read and handled.
   If new human input blocks disconnection, read and handle it before retrying; never advance the cursor blindly.
3. Finish each operation using finish_design_step with an explanation, immutable exported STL and saved editable
   CAD revision reference. Short operations and frequent exports allow live viewport updates and responsive pauses.
   Existing CAD tools perform edits; MakerSim does not supply a CAD engine or an AI model runner.
   During long CAD operations, report working progress with the active operation_id every 30 seconds.
   Simulation activity is announced automatically by run_session_test. Never claim percentage progress or an ETA
   unless the connected tool provides it. Missing updates are uncertainty, not proof of a failed operation.
4. Use run_session_test for session simulations so checkpoints, immutable artifacts and budgets apply.
   Publish Check findings and Act decisions/lessons, including failed hypotheses and evidence_type/conditions.
   Agent-reported tokens are accounting, not a hard provider spending cap; already-running work reaches a safe boundary.
5. Explanations may be published while paused to answer human questions. Never acquire new CAD work until explicit resume.
   Use branch_design_session from the exact selected timeline event. A branch inherits only the frozen parent prefix.
6. Use complete_design_session to announce Candidate ready for review, describing numerical criteria, confidence,
   fit, clearance and print concerns. Human acceptance and physical validation remain separate.
"""


def get_workflow():
    return {"workflow_version": 6, "instructions": WORKFLOW_INSTRUCTIONS,
            "review_checkpoint": "user_selects_revision_or_accepts",
            "default_first_review": {"baseline_revisions": 1, "maximum_candidate_revisions": 3},
            "run_artifacts": ["editable_cad", "stl", "test_spec", "report", "captures", "revision_notes"],
            "branch_metadata": ["parent_report", "branch", "goal"],
            "sessions": {"interrupt_requires": "explicit_human_resume", "memory_scope": "project_part_branch",
                         "default_role": "viewing_and_feedback", "controls": "connected_agent_opt_in",
                         "cad_execution": "connected_client", "completion": "candidate_ready_for_review"}}
