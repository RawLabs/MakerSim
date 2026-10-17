import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from deploy.apply_update import BACKEND, PATCH_BACKEND, apply_update, validate_manifest
from scripts.prepare_vm_update import backend_sources

ROOT = Path(__file__).resolve().parents[1]


def staged_update(tmp_path, preview=True):
    root,stage,backup = [tmp_path/name for name in ('app','stage','backup')]
    files = {f'backend/{name}':f'new {name}'.encode() for name in (BACKEND if preview else PATCH_BACKEND)}
    if preview:
        files.update({'frontend/dist/index.html':b'new index',
                      'frontend/dist/favicon.svg':b'icon',
                      'frontend/dist/assets/index-new.js':b'new browser'})
    for name,data in files.items():
        path = stage/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    for name in BACKEND-{'limits.py','hexa.py','print_model.py'}:
        path = root/'backend'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(f'old {name}')
    (root/'frontend/dist/assets').mkdir(parents=True)
    (root/'frontend/dist/index.html').write_text('old index')
    (root/'frontend/dist/assets/index-old.js').write_text('old browser')
    manifest = {'format':2,'tag':'a'*32,'preview':preview,
                'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    return root,stage,backup,manifest


def snapshot(root):
    return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_preview_installs_only_manifest_and_preserves_backup(tmp_path):
    root,stage,backup,manifest = staged_update(tmp_path)
    phases=[]
    apply_update(stage,manifest,root,backup,command_runner=lambda *a,**kw:None,mark_writer=phases.append)
    assert set(snapshot(root)) == set(manifest['files'])
    assert (backup/'dist/index.html').read_text() == 'old index'
    assert not stage.exists()
    assert phases[-1] == 'READY'
    assert (root/'frontend/dist/index.html').stat().st_mode & 0o777 == 0o644
    assert (root/'frontend/dist/assets').stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize('failed_check',['vm_update_check.py','vm_guest_check.py'])
@pytest.mark.parametrize('preview',[False,True])
def test_failed_checks_restore_backend_and_browser_together(tmp_path,failed_check,preview):
    root,stage,backup,manifest = staged_update(tmp_path,preview)
    before=snapshot(root)
    phases=[]
    def fail(args,**kwargs):
        if any(str(arg).endswith(failed_check) for arg in args):
            raise subprocess.CalledProcessError(1,args)
    with pytest.raises(subprocess.CalledProcessError):
        apply_update(stage,manifest,root,backup,command_runner=fail,mark_writer=phases.append)
    assert snapshot(root) == before
    assert not (root/'backend/hexa.py').exists()
    assert not (root/'backend/print_model.py').exists()
    assert phases[-2:] == ['ROLLED_BACK','FAILED']


@pytest.mark.parametrize('name',['backend/sessions.py','backend/correlation.py','../config','frontend/dist/../../config','frontend/dist/assets/../../config.js'])
def test_update_rejects_out_of_scope_paths(tmp_path,name):
    _,_,_,manifest = staged_update(tmp_path)
    manifest['files'][name] = '0'*64
    with pytest.raises(AssertionError):
        validate_manifest(manifest)


def test_backend_patch_preserves_browser_and_adds_solver_dependencies(tmp_path):
    root,stage,backup,manifest = staged_update(tmp_path,False)
    before = snapshot(root/'frontend')
    apply_update(stage,manifest,root,backup,command_runner=lambda *a,**kw:None,mark_writer=lambda phase:None)
    assert snapshot(root/'frontend') == before
    for name in ('hexa.py','print_model.py','schemas.py'):
        assert (root/'backend'/name).read_text() == f'new {name}'


@pytest.mark.parametrize('preview',[False,True])
@pytest.mark.parametrize('missing',['hexa.py','print_model.py','schemas.py'])
def test_missing_solver_dependency_is_rejected(tmp_path,preview,missing):
    _,_,_,manifest = staged_update(tmp_path,preview)
    del manifest['files'][f'backend/{missing}']
    with pytest.raises(AssertionError):
        validate_manifest(manifest)


@pytest.mark.parametrize('preview',[False,True])
def test_prepared_backend_runs_with_only_hosted_dependencies(tmp_path,preview):
    sources = backend_sources(preview)
    expected = BACKEND if preview else PATCH_BACKEND
    assert set(sources) == {f'backend/{name}' for name in expected}
    # These package/data files already exist in the VM and are deliberately
    # outside the one-time update. No local session/correlation modules exist.
    backend = tmp_path/'backend'
    backend.mkdir()
    shutil.copy2(ROOT/'backend/__init__.py',backend/'__init__.py')
    shutil.copy2(ROOT/'backend/materials.py',backend/'materials.py')
    shutil.copytree(ROOT/'backend/data',backend/'data')
    for name,source in sources.items():
        shutil.copy2(source,tmp_path/name)
    script = "from runpy import run_path\nimport sys\n"
    if preview:
        script += "from backend.app import HOSTED, app\nassert HOSTED\n"
    script += f"run_path({str(ROOT/'scripts/vm_update_check.py')!r},run_name='__main__')\n"
    script += "assert 'backend.correlation' not in sys.modules\nassert 'backend.sessions' not in sys.modules\n"
    result = subprocess.run([sys.executable,'-c',script],cwd=tmp_path,
        env={**os.environ,'PYTHONPATH':str(tmp_path),'MAKERSIM_HOSTED':'1',
             'PYTHONDONTWRITEBYTECODE':'1','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},
        capture_output=True,text=True,timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'MAKERSIM_ENHANCED_BRICK_CHECKS_PASSED' in result.stdout
