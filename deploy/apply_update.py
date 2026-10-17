"""Run inside the VM from a one-time read-only update seed."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PATCH_BACKEND = {'geometry.py','hexa.py','limits.py','print_model.py','schemas.py','solver.py'}
BACKEND = PATCH_BACKEND | {'app.py','materials.py','web.py'}


def validate_manifest(manifest):
    assert manifest['format'] == 2
    names = set(manifest['files'])
    backend = {name for name in names if name.startswith('backend/')}
    expected = BACKEND if manifest['preview'] else PATCH_BACKEND
    assert backend == {f'backend/{name}' for name in expected}
    static = names-backend
    if manifest['preview']:
        assert 'frontend/dist/index.html' in static
        assert any(name.endswith('.js') for name in static)
        assert all(name in ('frontend/dist/index.html','frontend/dist/favicon.svg') or re.fullmatch(
            r'frontend/dist/assets/[A-Za-z0-9_.-]+\.(js|css|png|svg|woff2)',name) for name in static)
    else:
        assert not static
    assert all(re.fullmatch('[a-f0-9]{64}',digest) for digest in manifest['files'].values())


def main():
    stage = Path(sys.argv[1])
    manifest = json.loads((stage/'manifest.json').read_text())
    tag = manifest['tag']
    assert re.fullmatch('[a-f0-9]{32}',tag)
    assert str(stage) == f'/root/makersim-update/{tag}' and os.geteuid() == 0
    os.umask(0o077)
    apply_update(stage,manifest,Path('/opt/makersim'),Path(f'/root/makersim-update-backup/{tag}'))


def apply_update(stage,manifest,root,backup,*,command_runner=subprocess.run,mark_writer=None):
    validate_manifest(manifest)
    tag = manifest['tag']
    assert not any((root/name).is_symlink() for name in ('backend','frontend','frontend/dist'))
    backup.mkdir(mode=0o700,parents=True,exist_ok=False)
    log = backup/'check.log'
    env = {**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1',
           'PYTHONPATH':str(root),'MAKERSIM_HOSTED':'1','PYTHONDONTWRITEBYTECODE':'1'}
    changed = False
    previous = {}

    def mark(phase):
        message = f'MAKERSIM_UPDATE_{tag}_{phase}\n'
        if mark_writer is not None:
            mark_writer(phase)
            return
        print(message,end='',flush=True)
        with Path('/dev/ttyS0').open('a') as serial:
            serial.write(message)

    def run(*args):
        with log.open('a') as output:
            command_runner(args,cwd=root,env=env,stdout=output,stderr=output,check=True,timeout=180)

    def install(source,destination):
        temporary = destination.with_name(destination.name+'.update')
        shutil.copyfile(source,temporary)
        temporary.chmod(0o644)
        os.replace(temporary,destination)

    try:
        mark('START')
        for name,digest in manifest['files'].items():
            source,destination = stage/name,root/name
            assert not source.is_symlink() and not destination.is_symlink()
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
            previous[name] = hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
            if name.startswith('backend/') and destination.exists():
                (backup/name).parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(destination,backup/name)
        if manifest['preview']:
            shutil.copytree(root/'frontend/dist',backup/'dist')
        (backup/'manifest.json').write_text(json.dumps(manifest))
        (backup/'previous.json').write_text(json.dumps(previous))
        # These service operations execute inside the guest only.
        run('systemctl','stop','cloudflared.service','makersim.service')
        changed = True
        if manifest['preview']:
            shutil.rmtree(root/'frontend/dist')
            (root/'frontend/dist/assets').mkdir(parents=True)
            (root/'frontend/dist').chmod(0o755)
            (root/'frontend/dist/assets').chmod(0o755)
        for name in manifest['files']:
            install(stage/name,root/name)
        mark('SOURCE_APPLIED')
        python = '/opt/makersim/.venv/bin/python'
        run(python,str(stage/'vm_update_check.py'),*(['--preview'] if manifest['preview'] else []))
        run('systemctl','start','makersim.service')
        run(python,str(stage/'vm_guest_check.py'),*(['--preview'] if manifest['preview'] else []))
        # Clear synthetic workspaces before public traffic resumes.
        run('systemctl','restart','makersim.service')
        run('systemctl','start','cloudflared.service')
        run('systemctl','is-active','--quiet','makersim.service','cloudflared.service')
        shutil.rmtree(stage)
        mark('READY')
    except BaseException:
        if changed:
            # Stop readers before restoring the paired backend and frontend.
            try:
                run('systemctl','stop','cloudflared.service','makersim.service')
                for name in manifest['files']:
                    if not name.startswith('backend/'):
                        continue
                    if previous[name] is None:
                        (root/name).unlink(missing_ok=True)
                    else:
                        install(backup/name,root/name)
                if manifest['preview']:
                    shutil.rmtree(root/'frontend/dist')
                    shutil.copytree(backup/'dist',root/'frontend/dist')
                run('systemctl','start','makersim.service','cloudflared.service')
                mark('ROLLED_BACK')
            except Exception:
                mark('ROLLBACK_FAILED')
        mark('FAILED')
        raise


if __name__ == '__main__':
    main()
