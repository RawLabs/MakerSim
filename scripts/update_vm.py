"""Apply only geometry/solver changes through the VM's temporary CD-ROM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

DOMAIN = 'makersim'
UUID = 'dfbfb31a-fa8f-43a9-9623-a3a707f7b437'
DISKS = Path('/var/lib/libvirt/images/makersim')


def run(*args):
    return subprocess.run(args,check=True,text=True,capture_output=True)


def vm(*args):
    return run('virsh','-c','qemu:///system',*args)


def host_boundary():
    config = Path('/etc/cloudflared/config.yml')
    return {'ssh_tunnel_pid':run('systemctl','show','cloudflared','--property=MainPID','--value').stdout.strip(),
        'ssh_tunnel_config':hashlib.sha256(config.read_bytes()).hexdigest(),
        'isolation_config':hashlib.sha256(Path('/etc/makersim/isolation.nft').read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run this script with sudo in a visible terminal.')
    directory = args.directory
    info = json.loads((directory/'update.json').read_text())
    tag = info['tag']
    assert re.fullmatch('[a-f0-9]{32}',tag)
    assert directory == Path(f'/tmp/makersim-app-update-{tag}')
    account = pwd.getpwnam('legion')
    assert directory.stat().st_uid == account.pw_uid and not directory.is_symlink()
    source = directory/'update.iso'
    assert not source.is_symlink()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == info['iso_sha256']
    assert Path('/etc/makersim/ready').is_file()
    run('nft','list','table','inet','makersim_vm_isolation')
    domain = ET.fromstring(vm('dumpxml',DOMAIN).stdout)
    assert domain.findtext('uuid') == UUID
    assert not any(domain.findall(f'./devices/{kind}') for kind in ('filesystem','hostdev','channel','graphics'))
    assert len(domain.findall('./devices/interface')) == 1
    assert domain.find('./devices/interface/source').get('network') == 'makersim-net'
    disks = domain.findall('./devices/disk')
    assert len(disks) == 2
    assert domain.find('./devices/disk[@device="disk"]/source').get('file') == str(DISKS/'system.qcow2')
    cdrom = domain.find('./devices/disk[@device="cdrom"]')
    assert cdrom.find('target').get('dev') == 'sda' and cdrom.find('readonly') is not None
    assert cdrom.find('source') is None or cdrom.find('source').get('file') is None
    before = host_boundary()
    assert before['ssh_tunnel_pid'] != '0'
    log = Path('/var/log/libvirt/qemu/makersim-serial.log')
    offset = log.stat().st_size
    attached = False
    copied = DISKS/f'update-{tag}.iso'
    assert not copied.exists()

    def status(phase,**details):
        descriptor,name = tempfile.mkstemp(dir=directory,prefix='.update-status-')
        with os.fdopen(descriptor,'w') as output:
            json.dump({'phase':phase,'updated_at':time.time(),**details},output)
        os.chown(name,account.pw_uid,account.pw_gid)
        os.replace(name,directory/'status.json')
        print(phase,flush=True)

    try:
        state = vm('domstate',DOMAIN).stdout.strip()
        assert state in ('running','shut off'),state
        status('Stopping MakerSim VM for the two-file update')
        if state == 'running':
            vm('shutdown',DOMAIN)
            for _ in range(40):
                if vm('domstate',DOMAIN).stdout.strip() == 'shut off':
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Graceful shutdown timed out; update was not attached')
        shutil.copyfile(source,copied)
        copied.chmod(0o600)
        vm('change-media',DOMAIN,'sda',str(copied),'--insert','--config')
        attached = True
        vm('start',DOMAIN)
        status('Waiting for guest regression and isolation checks')
        prefix = f'MAKERSIM_UPDATE_{tag}_'
        for _ in range(150):
            with log.open('rb') as stream:
                stream.seek(offset if log.stat().st_size >= offset else 0)
                content = stream.read().decode(errors='replace')
            if prefix+'FAILED' in content:
                raise RuntimeError('Guest checks failed; the guest attempted to restore the previous source')
            if prefix+'READY' in content:
                break
            time.sleep(2)
        else:
            raise RuntimeError('Guest update timed out; review the guest update log before retrying')
        vm('change-media',DOMAIN,'sda','--eject','--live','--config')
        attached = False
        copied.unlink()
        after = host_boundary()
        assert before == after,'Existing host SSH connector or isolation configuration changed during the update'
        status('passed',host_tunnel_unchanged=True,seed_removed=True,source_sha256=info['files'])
    except BaseException as error:
        if attached:
            state = vm('domstate',DOMAIN).stdout.strip()
            flags = ['--live','--config'] if state == 'running' else ['--config']
            vm('change-media',DOMAIN,'sda','--eject',*flags)
        if copied.exists():
            copied.unlink()
        status('failed',reason=str(error))
        raise


if __name__ == '__main__':
    main()
