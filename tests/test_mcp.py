"""An actual MCP client talks to the installed local stdio process."""
import asyncio
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from backend.sessions import SessionStore

ROOT = Path(__file__).resolve().parents[1]


def test_agent_stdio_workflow(tmp_path):
    async def workflow():
        session_root = tmp_path / 'sessions'
        server = StdioServerParameters(command=str(ROOT / ".venv/bin/makersim-mcp"), cwd=str(tmp_path),
                                       env={**os.environ, 'MAKERSIM_SESSION_DIR': str(session_root)})
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert "EVERY tested revision" in initialized.instructions
                assert "Wait for the user's design selection" in initialized.instructions
                available = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert set(available) == {"get_workflow", "get_test_schema", "get_goal_schema", "list_materials", "inspect_model", "validate_test", "run_test", "get_result", "query_field", "evaluate_goals", "check_convergence", "compare_runs", "correlate_measurements", "capture_result", "create_review",
                    'create_design_session', 'get_design_session', 'recall_design_context', 'publish_design_event',
                    'begin_design_step', 'finish_design_step', 'run_session_test', 'branch_design_session', 'complete_design_session', 'report_design_activity',
                    'connect_design_agent', 'update_design_connection', 'authorize_design_run'}
                workflow = await session.call_tool("get_workflow", {})
                assert not workflow.isError and workflow.structuredContent["instructions"] == initialized.instructions
                assert available["inspect_model"].annotations.readOnlyHint
                assert not available["run_test"].annotations.destructiveHint
                schema = await session.call_tool("get_test_schema", {})
                assert not schema.isError and "load_cases" in schema.structuredContent["properties"]
                stl = str(ROOT / "backend/data/backpack-bracket.stl")
                spec = str(ROOT / "backend/data/bracket-test.json")
                inspected = await session.call_tool("inspect_model", {"stl_path": stl})
                assert not inspected.isError and inspected.structuredContent["simulation_ready"]
                validated = await session.call_tool("validate_test", {"test_path": spec})
                assert not validated.isError and validated.structuredContent["valid"]
                materials = await session.call_tool("list_materials", {})
                assert any(m["id"] == "generic-pla" for m in materials.structuredContent["materials"])
                report = str(tmp_path / "mcp-report.json")
                result = await session.call_tool("run_test", {"stl_path": stl, "test_path": spec, "output_path": report, "revision": "onshape-r1"})
                assert not result.isError and result.structuredContent["criteria_status"] == "not_met"
                saved = await session.call_tool("get_result", {"report_path": report})
                assert not saved.isError and saved.structuredContent["source"]["revision"] == "onshape-r1"
                compared = await session.call_tool("compare_runs", {"baseline_path": report, "candidate_path": report})
                assert not compared.isError and compared.structuredContent["conditions_match"]
                repeated = await session.call_tool("run_test", {"stl_path": stl, "test_path": spec, "output_path": report})
                assert repeated.isError and repeated.structuredContent["error"]["code"] == "output_exists"
                missing = await session.call_tool("inspect_model", {"stl_path": str(tmp_path / "missing.stl")})
                assert missing.isError and missing.structuredContent["error"]["code"] == "file_error"
                visual = await session.call_tool("run_test", {"stl_path": stl, "test_path": spec,
                    "output_path": str(tmp_path/"visual-report.json"), "images_dir": str(tmp_path/"images"),
                    "stress_max_mpa": 10, "movement_scale": 10, "parent_report": report,
                    "revision": "R1-A1", "branch": "appearance", "goal": "Review shape"})
                assert not visual.isError
                assert len([c for c in visual.content if c.type == "image"]) == 2
                assert len(visual.structuredContent["captures"]["images"]) == 8
                assert visual.structuredContent["iteration"]["parent_test_matches"]
                full_report = visual.structuredContent["captures"]["report_path"]
                compact = await session.call_tool("get_result", {"report_path": full_report, "view": "metrics"})
                assert not compact.isError and "fields" not in compact.structuredContent["load_cases"][0]
                case_name = compact.structuredContent["load_cases"][0]["name"]
                field = await session.call_tool("query_field", {"report_path": full_report, "load_case": case_name,
                    "field": "surface_stress_mpa", "limit": 2})
                assert not field.isError and field.structuredContent["returned"] == 2
                goals = await session.call_tool("evaluate_goals", {"baseline_path": report, "candidate_path": report,
                    "goals": {"requirements": [{"metric": "solid_volume_mm3", "change_percent": 20}]}})
                assert not goals.isError and goals.structuredContent["goal_status"] == "met"
                refinement = await session.call_tool("check_convergence", {"stl_path": stl, "test_path": spec,
                    "output_dir": str(tmp_path/"refinement"), "resolutions": [20, 25, 32]})
                assert not refinement.isError and refinement.structuredContent["strictly_refined"]
                underside = await session.call_tool("capture_result", {"report_path": full_report,
                    "output_dir": str(tmp_path/"underside"), "views": ["geometry_underside", "stress_underside"],
                    "camera": {"azimuth": -120, "elevation": 30}})
                assert not underside.isError and len([c for c in underside.content if c.type == "image"]) == 2
                assert underside.structuredContent["camera"]["azimuth"] == -120
                reviewed = await session.call_tool("create_review", {"report_paths": [str(tmp_path/"visual-report.json")],
                    "output_path": str(tmp_path/"history.html")})
                assert not reviewed.isError and Path(reviewed.structuredContent["review_path"]).exists()
                design = await session.call_tool('create_design_session', {'objective': 'Explain a rib.'})
                assert not design.isError and design.structuredContent['status'] == 'paused'
                design_id = design.structuredContent['id']
                connection = await session.call_tool('connect_design_agent', {'session_id': design_id, 'agent_name': 'Test engineering agent'})
                run_id = connection.structuredContent['run_id']
                activity = await session.call_tool('report_design_activity', {'session_id': design_id,
                    'kind': 'explaining', 'message': 'Explaining how a rib carries the load.', 'run_id': run_id})
                assert not activity.isError and activity.structuredContent['state']['status'] == 'paused'
                blocked = await session.call_tool('begin_design_step', {'session_id': design_id, 'description': 'Draw rib.', 'run_id': run_id})
                assert blocked.isError and blocked.structuredContent['error']['code'] == 'session_paused'
                human_store = SessionStore(session_root)
                authorized = await session.call_tool('authorize_design_run', {'session_id': design_id, 'run_id': run_id,
                    'human_instruction': 'Start work on the rib.'})
                assert not authorized.isError
                lease = await session.call_tool('begin_design_step', {'session_id': design_id, 'description': 'Draw rib.', 'run_id': run_id})
                human_store.control(design_id, 'interrupt', 'Why this thickness?')
                finished = await session.call_tool('finish_design_step', {'session_id': design_id,
                    'operation_id': lease.structuredContent['operation_id'], 'explanation': 'Saved the current edit.',
                    'stl_path': stl, 'revision': 'R0'})
                assert not finished.isError and finished.structuredContent['status'] == 'paused'
                lesson = await session.call_tool('publish_design_event', {'session_id': design_id, 'kind': 'lesson',
                    'message': 'Thickness remains a hypothesis.', 'phase': 'act', 'data': {'evidence_type': 'hypothesis'}, 'run_id': run_id})
                assert not lesson.isError
                recalled = await session.call_tool('recall_design_context', {'session_id': design_id, 'query': 'thickness'})
                assert len(recalled.structuredContent['memory']) == 2
                resumed = await session.call_tool('authorize_design_run', {'session_id': design_id, 'run_id': run_id,
                    'human_instruction': 'Continue working.', 'action': 'resume'})
                assert not resumed.isError
                tested = await session.call_tool('run_session_test', {'session_id': design_id, 'test_path': spec, 'run_id': run_id})
                assert not tested.isError and tested.structuredContent['session']['report']['criteria_status'] == 'not_met'
                branched = await session.call_tool('branch_design_session', {'session_id': design_id, 'sequence': 1})
                assert not branched.isError and branched.structuredContent['revision'] is None
                done = await session.call_tool('complete_design_session', {'session_id': design_id,
                    'summary': 'Candidate ready for review. The heavy numerical case still exceeds the limit.', 'run_id': run_id})
                assert not done.isError and done.structuredContent['status'] == 'review'
                disconnected = await session.call_tool('update_design_connection', {'session_id': design_id, 'run_id': run_id, 'action': 'disconnect'})
                assert disconnected.structuredContent['agent_run']['state'] == 'ended'
                managed = await session.call_tool('connect_design_agent', {'session_id': design_id,
                    'agent_name': 'Interactive engineering agent', 'supports_controls': True})
                managed_id = managed.structuredContent['run_id']
                await session.call_tool('authorize_design_run', {'session_id': design_id, 'run_id': managed_id,
                    'human_instruction': 'Continue reviewing this part.'})
                read = await session.call_tool('get_design_session', {'session_id': design_id})
                cursor = read.structuredContent['next_after']
                human_store.control(design_id, 'interrupt', 'Add Victorian scrolls without moving the holes.')
                human_store.control(design_id, 'continue')
                premature = await session.call_tool('update_design_connection', {'session_id': design_id,
                    'run_id': managed_id, 'action': 'disconnect', 'seen_sequence': cursor})
                assert premature.isError and premature.structuredContent['error']['code'] == 'pending_input'
                inputs = await session.call_tool('get_design_session', {'session_id': design_id, 'after': cursor})
                assert [event['kind'] for event in inputs.structuredContent['events']] == ['intervention', 'continued']
                await session.call_tool('publish_design_event', {'session_id': design_id, 'run_id': managed_id,
                    'kind': 'explanation', 'message': 'I have read the Victorian direction and your Continue.'})
                ended = await session.call_tool('update_design_connection', {'session_id': design_id,
                    'run_id': managed_id, 'action': 'disconnect', 'seen_sequence': inputs.structuredContent['next_after']})
                assert not ended.isError and ended.structuredContent['agent_run']['state'] == 'ended'
    asyncio.run(asyncio.wait_for(workflow(), timeout=30))
