"""Install the dedicated VM and interface-scoped firewall after administrator authentication."""
import argparse
import hashlib
import ipaddress
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

ROOT = Path(__file__).resolve().parents[1]
IMAGE_HASH = '6a81c37564db9b1ee84e141922625e1d7c5b389b99bb3c572e0243607d5bb4d2'
DISKS = Path('/var/lib/libvirt/images/makersim')


def ensure_vm_forwarding():
    """Add only this guest's permitted egress to an active UFW firewall."""
    if shutil.which('ufw') is None:
        return
    status = run('ufw', 'status', capture=True).stdout
    if 'Status: active' not in status:
        return
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'default', capture=True).stdout)
    uplinks = {route['dev'] for route in routes if 'dev' in route}
    if len(uplinks) != 1:
        raise SystemExit('Expected one IPv4 uplink; review VM forwarding before proceeding.')
    uplink = uplinks.pop()
    prefix = ['route', 'allow', 'in', 'on', 'virbr-msim', 'out', 'on', uplink,
              'from', '192.168.130.2', 'to']
    rules = [prefix + [resolver, 'port', '53', 'proto', protocol,
                       'comment', 'MakerSim VM DNS']
             for resolver in ('1.1.1.1', '1.0.0.1') for protocol in ('udp', 'tcp')]
    rules.extend([
        prefix + ['any', 'port', '80,443,7844', 'proto', 'tcp', 'comment', 'MakerSim VM web and tunnel'],
        prefix + ['any', 'port', '7844', 'proto', 'udp', 'comment', 'MakerSim VM tunnel'],
    ])
    # Adding rules preserves existing UFW chains; never enable/reload/reset it.
    for rule in rules:
        run('ufw', '--dry-run', *rule, capture=True)
    for rule in rules:
        run('ufw', *rule)
    print(f'Added guest-only UFW egress via {uplink}; existing policies and SSH rules preserved.', flush=True)


def write_status(directory, phase, content='', host_checks=None):
    """Export only bounded, filtered progress; no guest seed or credential contents."""
    account = pwd.getpwnam('legion')
    lines = content.splitlines()[-100:]
    lines = [re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line) for line in lines]
    lines = [line if len(line) < 600 and not re.search(r'password|secret|credential|token|BEGIN .*KEY', line, re.I)
             else '[sensitive or long log line omitted]' for line in lines]
    descriptor, name = tempfile.mkstemp(prefix='.status-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'phase': phase, 'serial_tail': lines, 'updated_at': time.time(),
                       'host_checks': host_checks or {}}, output)
        os.chmod(name, 0o600)
        os.chown(name, account.pw_uid, account.pw_gid)
        os.replace(name, directory/'status.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def resume_failed_vm(args, nft):
    """Re-seed only the failed initial installation, preserving its disk."""
    if Path('/etc/makersim/ready').exists():
        raise SystemExit('Refusing to re-seed an already deployed VM.')
    if Path('/etc/makersim/isolation.nft').read_text() != nft:
        raise SystemExit('Existing isolation differs; review before resuming.')
    run('nft', 'list', 'table', 'inet', 'makersim_vm_isolation', capture=True)
    domain = ET.fromstring(run('virsh', '-c', 'qemu:///system', 'dumpxml', 'makersim', capture=True).stdout)
    disks = domain.findall('./devices/disk')
    assert {disk.find('source').get('file') for disk in disks} == {
        str(DISKS/'system.qcow2'), str(DISKS/'seed.iso')}, 'Unexpected VM disks'
    assert domain.find('./devices/interface/source').get('network') == 'makersim-net'
    assert not domain.findall('./devices/filesystem') and not domain.findall('./devices/hostdev')
    log = Path('/var/log/libvirt/qemu/makersim-serial.log')
    previous = log.read_text(errors='replace')
    if 'MAKERSIM_VM_PROVISION_FAILED' not in previous or '\nMAKERSIM_VM_READY' in previous.replace('\r', ''):
        raise SystemExit('Resume requires a failed initial provisioning log.')
    offset = log.stat().st_size
    write_status(args.directory, 'repairing guest forwarding')
    ensure_vm_forwarding()
    state = run('virsh', '-c', 'qemu:///system', 'domstate', 'makersim', capture=True).stdout.strip()
    if state == 'running':
        run('virsh', '-c', 'qemu:///system', 'shutdown', 'makersim')
        for _ in range(40):
            state = run('virsh', '-c', 'qemu:///system', 'domstate', 'makersim', capture=True).stdout.strip()
            if state == 'shut off':
                break
            time.sleep(1)
        else:
            run('virsh', '-c', 'qemu:///system', 'destroy', 'makersim')
    elif state != 'shut off':
        raise SystemExit(f'Unexpected VM state: {state}')
    replacement = DISKS/'.seed-new.iso'
    shutil.copyfile(args.directory/'seed.iso', replacement)
    replacement.chmod(0o600)
    os.replace(replacement, DISKS/'seed.iso')
    run('virsh', '-c', 'qemu:///system', 'start', 'makersim')
    return offset


def wait_for_guest(args, offset=0):
    print('VM started with host isolation. Waiting for guest checks; DNS has not been published.', flush=True)
    log = Path('/var/log/libvirt/qemu/makersim-serial.log')
    for _ in range(240):
        content = ''
        if log.exists():
            with log.open('rb') as source:
                source.seek(offset if log.stat().st_size >= offset else 0)
                content = source.read().decode(errors='replace')
        write_status(args.directory, 'waiting for guest checks', content)
        if 'MAKERSIM_VM_PROVISION_FAILED' in content:
            write_status(args.directory, 'guest provisioning failed', content)
            raise SystemExit('Guest provisioning failed. Filtered diagnostics are in status.json.')
        if '\nMAKERSIM_VM_READY' in content.replace('\r', ''):
            run('virsh', '-c', 'qemu:///system', 'change-media', 'makersim', 'sda', '--eject', '--live', '--config')
            (DISKS/'seed.iso').unlink()
            run('virsh', '-c', 'qemu:///system', 'autostart', 'makersim', '--disable')
            install('/etc/makersim/ready', 'MAKERSIM_VM_READY\n')
            checks = {
                'firewall': json.loads(run('nft', '-j', 'list', 'table', 'inet', 'makersim_vm_isolation', capture=True).stdout),
                'domain': run('virsh', '-c', 'qemu:///system', 'dumpxml', 'makersim', capture=True).stdout,
                'host_tunnel_active': run('systemctl', 'is-active', '--quiet', 'cloudflared', check=False).returncode == 0,
                'seed_removed': not (DISKS/'seed.iso').exists(),
            }
            if shutil.which('ufw'):
                checks['ufw'] = run('ufw', 'status', 'verbose', capture=True).stdout
            write_status(args.directory, 'ready; seed detached; manual VM startup', content, checks)
            print('MAKERSIM_VM_READY: checks passed, seed detached, VM uses manual startup after host reboot.', flush=True)
            return
        time.sleep(5)
    write_status(args.directory, 'guest provisioning timed out', content)
    raise SystemExit('Guest provisioning timed out. Review status.json before publishing DNS.')


def run(*args, capture=False, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=capture)


def install(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SystemExit(f'Refusing symlink at {path}')
    if path.exists() and path.read_text() != content:
        raise SystemExit(f'Existing different file at {path}; review before replacing.')
    descriptor, name = tempfile.mkstemp(prefix='.makersim-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(content)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('/tmp/makersim-vm'))
    parser.add_argument('--public-gateway', required=True)
    parser.add_argument('--resume', action='store_true', help='Re-seed only a failed initial VM installation')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run in a visible terminal with sudo for administrator authentication.')
    public = ipaddress.IPv4Address(args.public_gateway)
    if not public.is_global:
        raise SystemExit('Expected the current public IPv4 gateway.')
    image = args.directory/'ubuntu-noble-amd64.img'
    with image.open('rb') as source:
        assert hashlib.file_digest(source,'sha256').hexdigest() == IMAGE_HASH, 'Ubuntu image checksum mismatch'
    nft = (ROOT/'deploy/makersim-isolation.nft').read_text().replace('@PUBLIC_GATEWAY@',str(public))
    assert (args.directory/'seed.iso').is_file()
    if args.resume:
        offset = resume_failed_vm(args, nft)
        wait_for_guest(args, offset)
        return
    if DISKS.exists():
        raise SystemExit('A MakerSim disk directory already exists; refusing to overwrite a VM.')
    # Retire the old host process without changing the existing Legion SSH tunnel.
    run('systemctl','disable','--now','makersim.service',check=False)
    config = Path('/etc/cloudflared/config.yml')
    if 'hostname: makersim.rawcastdigital.com' in config.read_text():
        raise SystemExit('Unexpected old MakerSim ingress; remove it with the retired-host cleanup first.')
    install('/etc/makersim/isolation.nft',nft,0o600)
    install('/usr/local/libexec/makersim-firewall', '''#!/usr/bin/python
import subprocess
from pathlib import Path
content = Path('/etc/makersim/isolation.nft').read_text()
exists = subprocess.run(['/usr/bin/nft','list','table','inet','makersim_vm_isolation'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
batch = ('delete table inet makersim_vm_isolation\\n' if exists else '') + content
subprocess.run(['/usr/bin/nft','--check','-f','-'],input=batch,text=True,check=True)
subprocess.run(['/usr/bin/nft','-f','-'],input=batch,text=True,check=True)
''',0o755)
    install('/etc/systemd/system/makersim-isolation.service',(ROOT/'deploy/makersim-isolation.service').read_text())
    install('/etc/systemd/system/libvirtd.service.d/50-makersim-isolation.conf','[Unit]\nRequires=makersim-isolation.service\nAfter=makersim-isolation.service\n')
    install('/etc/libvirt/hooks/qemu.d/50-makersim-isolation', '''#!/bin/sh
if [ "$1" = makersim ]; then
  case "$2" in
    prepare|start|restore|reconnect)
      /usr/bin/nft list table inet makersim_vm_isolation >/dev/null 2>&1 || {
        echo 'MakerSim refuses to run without host isolation rules.' >&2
        exit 1
      }
      ;;
  esac
fi
''',0o755)
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now','makersim-isolation.service')
    run('systemctl','enable','--now','libvirtd.service')
    if run('virsh','-c','qemu:///system','dominfo','makersim',capture=True,check=False).returncode == 0:
        raise SystemExit('An existing MakerSim VM is defined; refusing to replace it.')
    if run('virsh','-c','qemu:///system','net-info','makersim-net',capture=True,check=False).returncode == 0:
        raise SystemExit('An existing MakerSim network is defined; inspect before changing it.')
    DISKS.mkdir(mode=0o711)
    shutil.copyfile(image,DISKS/'ubuntu-base.qcow2')
    (DISKS/'ubuntu-base.qcow2').chmod(0o444)
    run('qemu-img','create','-f','qcow2','-F','qcow2','-b',str(DISKS/'ubuntu-base.qcow2'),str(DISKS/'system.qcow2'),'25G')
    shutil.copyfile(args.directory/'seed.iso',DISKS/'seed.iso')
    (DISKS/'seed.iso').chmod(0o600)
    run('virsh','-c','qemu:///system','net-define',str(ROOT/'deploy/makersim-network.xml'))
    run('virsh','-c','qemu:///system','net-start','makersim-net')
    run('virsh','-c','qemu:///system','net-autostart','makersim-net')
    ensure_vm_forwarding()
    run('virsh','-c','qemu:///system','define',str(ROOT/'deploy/makersim-domain.xml'))
    run('virsh','-c','qemu:///system','start','makersim')
    wait_for_guest(args)


if __name__ == '__main__':
    main()
