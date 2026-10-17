import hashlib
import subprocess

import pytest

from deploy.apply_update import BACKEND, apply_update, validate_manifest


def staged_update(tmp_path):
    root,stage,backup = [tmp_path/name for name in ('app','stage','backup')]
    files = {f'backend/{name}':f'new {name}'.encode() for name in BACKEND}
    files.update({'frontend/dist/index.html':b'new index',
                  'frontend/dist/favicon.svg':b'icon',
                  'frontend/dist/assets/index-new.js':b'new browser'})
    for name,data in files.items():
        path = stage/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    for name in BACKEND-{'limits.py'}:
        path = root/'backend'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(f'old {name}')
    (root/'frontend/dist/assets').mkdir(parents=True)
    (root/'frontend/dist/index.html').write_text('old index')
    (root/'frontend/dist/assets/index-old.js').write_text('old browser')
    manifest = {'format':2,'tag':'a'*32,'preview':True,
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
def test_failed_checks_restore_backend_and_browser_together(tmp_path,failed_check):
    root,stage,backup,manifest = staged_update(tmp_path)
    before=snapshot(root)
    phases=[]
    def fail(args,**kwargs):
        if any(str(arg).endswith(failed_check) for arg in args):
            raise subprocess.CalledProcessError(1,args)
    with pytest.raises(subprocess.CalledProcessError):
        apply_update(stage,manifest,root,backup,command_runner=fail,mark_writer=phases.append)
    assert snapshot(root) == before
    assert phases[-2:] == ['ROLLED_BACK','FAILED']


@pytest.mark.parametrize('name',['backend/sessions.py','../config','frontend/dist/../../config','frontend/dist/assets/../../config.js'])
def test_update_rejects_out_of_scope_paths(tmp_path,name):
    _,_,_,manifest = staged_update(tmp_path)
    manifest['files'][name] = '0'*64
    with pytest.raises(AssertionError):
        validate_manifest(manifest)
