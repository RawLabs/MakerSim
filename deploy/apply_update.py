"""Run inside the VM from a one-time read-only update seed."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    stage = Path(sys.argv[1])
    manifest = json.loads((stage/'manifest.json').read_text())
    tag = manifest['tag']
    assert re.fullmatch('[a-f0-9]{32}',tag)
    assert str(stage) == f'/root/makersim-update/{tag}' and os.geteuid() == 0
    assert set(manifest['files']) == {'geometry.py','solver.py'}
    backup = Path(f'/root/makersim-update-backup/{tag}')
    backup.mkdir(mode=0o700,parents=True,exist_ok=False)
    log = backup/'check.log'
    os.umask(0o077)
    env = {**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','PYTHONPATH':'/opt/makersim'}
    changed = False

    def mark(phase):
        message = f'MAKERSIM_UPDATE_{tag}_{phase}\n'
        print(message,end='',flush=True)
        with Path('/dev/ttyS0').open('a') as serial:
            serial.write(message)

    def run(*args):
        with log.open('a') as output:
            subprocess.run(args,cwd='/opt/makersim',env=env,stdout=output,stderr=output,check=True,timeout=180)

    try:
        mark('START')
        for name,digest in manifest['files'].items():
            source,destination = stage/name,Path('/opt/makersim/backend')/name
            assert not source.is_symlink() and not destination.is_symlink()
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
            shutil.copy2(destination,backup/name)
        # These service operations execute inside the guest only.
        run('systemctl','stop','cloudflared.service','makersim.service')
        changed = True
        for name in manifest['files']:
            destination = Path('/opt/makersim/backend')/name
            temporary = destination.with_suffix('.py.update')
            shutil.copyfile(stage/name,temporary)
            temporary.chmod(0o644)
            os.replace(temporary,destination)
        mark('SOURCE_APPLIED')
        python = '/opt/makersim/.venv/bin/python'
        run(python,str(stage/'vm_update_check.py'))
        run('systemctl','start','makersim.service')
        run(python,str(stage/'vm_guest_check.py'))
        # Clear the synthetic readiness workspace before public traffic resumes.
        run('systemctl','restart','makersim.service')
        run('systemctl','start','cloudflared.service')
        run('systemctl','is-active','--quiet','makersim.service','cloudflared.service')
        shutil.rmtree(stage)
        mark('READY')
    except BaseException:
        if changed:
            for name in manifest['files']:
                saved = backup/name
                if saved.exists():
                    destination = Path('/opt/makersim/backend')/name
                    temporary = destination.with_suffix('.py.rollback')
                    shutil.copyfile(saved,temporary)
                    temporary.chmod(0o644)
                    os.replace(temporary,destination)
            try:
                run('systemctl','restart','makersim.service','cloudflared.service')
            except Exception:
                pass
        mark('FAILED')
        raise


if __name__ == '__main__':
    main()
