import pytest

from backend.sessions import session_store
from tests.test_api import InProcessClient, in_process_threads


@pytest.fixture(autouse=True)
def local_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv('MAKERSIM_SESSION_DIR', str(tmp_path / 'sessions'))
    monkeypatch.delenv('MAKERSIM_HOSTED', raising=False)


def test_browser_control_scoping_and_reopening():
    alice, bob = InProcessClient(), InProcessClient()
    response = alice.post('/api/sessions', json={'objective': 'Explain the rib on this part.'})
    assert response.status_code == 200
    session = response.json()
    assert session['status'] == 'paused'
    assert 'Max-Age=31536000' in response.headers['set-cookie']
    assert bob.get('/api/sessions/' + session['id']).status_code == 404
    assert bob.post('/api/sessions/' + session['id'] + '/control', json={'action': 'continue'}).status_code == 404
    assert bob.get('/api/sessions').json()['sessions'] == []
    assert alice.post('/api/sessions/' + session['id'] + '/control', json={'action': 'continue'}).status_code == 409
    connected = session_store().connect_agent(session['id'], 'Workshop agent', True)
    session_store().authorize_run(session['id'], connected['run_id'], 'Start working on this rib.')
    assert alice.post('/api/sessions/' + session['id'] + '/control', json={'action': 'interrupt', 'message': 'Why here?'}).status_code == 200
    reloaded = InProcessClient()
    reloaded.cookies.update(alice.cookies)
    assert reloaded.get('/api/sessions/' + session['id']).json()['pause_reason'] == 'Why here?'
    events = reloaded.get('/api/sessions/' + session['id'] + '/events?after=1').json()['events']
    assert [event['kind'] for event in events] == ['agent_connected', 'run_started', 'intervention']
    assert reloaded.post('/api/sessions/' + session['id'] + '/fork', json={'sequence': 1}).json()['status'] == 'paused'


def test_viewing_connection_saves_feedback_without_claiming_execution_control():
    browser = InProcessClient()
    session = browser.post('/api/sessions', json={'objective': 'Explain a rib.'}).json()
    store = session_store()
    connected = store.connect_agent(session['id'], 'My CAD assistant')
    store.authorize_run(session['id'], connected['run_id'], 'Start explaining this rib.')
    for action in ('pause', 'continue', 'recover', 'interrupt'):
        assert browser.post(f"/api/sessions/{session['id']}/control", json={'action': action}).status_code == 409
    response = browser.post(f"/api/sessions/{session['id']}/control", json={'action': 'feedback', 'message': 'Leave room for bolts.'})
    assert response.status_code == 200 and response.json()['status'] == 'running'
    assert store.context(session['id'], 'bolts')['memory'][0]['kind'] == 'feedback'


def test_hosted_sessions_are_disabled_and_cross_origin_controls_rejected(monkeypatch):
    browser = InProcessClient()
    assert browser.post('/api/sessions', json={'objective': 'Test'}, headers={'Origin': 'https://other.example'}).status_code == 403
    monkeypatch.setenv('MAKERSIM_HOSTED', '1')
    assert browser.get('/api/sessions').status_code == 404
    assert browser.post('/api/sessions', json={'objective': 'Test'}).status_code == 404


def test_connect_cli_session_and_validate_controls():
    browser = InProcessClient()
    session = session_store().create({'objective': 'Connect the CLI client.'})
    assert browser.post('/api/sessions/connect', json={'session_id': session['id']}).status_code == 200
    assert browser.get('/api/sessions/' + session['id']).status_code == 200
    assert browser.post('/api/sessions/' + session['id'] + '/control', json={'action': 'settings', 'settings': {'mode': 'invalid'}}).status_code == 422
    assert browser.get('/api/sessions/' + session['id'] + '/events?after=-1').status_code == 422
