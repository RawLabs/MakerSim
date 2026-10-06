"""Exercise this VM's offline/online landing behavior and always restore it."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import time


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True, timeout=40)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--edge-ip', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo in a visible terminal.')
    assert ipaddress.IPv4Address(args.edge_ip).is_global
    assert Path('/etc/makersim/ready').read_text() == 'MAKERSIM_VM_READY\n'
    # Clear only transient local DNS cache entries after creating the hostname.
    run('resolvectl', 'flush-caches', check=False)
    tunnel_config = Path('/etc/cloudflared/config.yml')
    digest = hashlib.sha256(tunnel_config.read_bytes()).hexdigest()
    pid = run('systemctl', 'show', '--property=MainPID', '--value', 'cloudflared').stdout.strip()
    assert int(pid) > 0
    run('nft', 'list', 'table', 'inet', 'makersim_vm_isolation')
    report = {'phase': 'checking offline behavior'}
    directory = Path('/tmp/makersim-vm')
    account = pwd.getpwnam('legion')

    def save():
        descriptor, name = tempfile.mkstemp(prefix='.availability-', dir=directory)
        try:
            with os.fdopen(descriptor, 'w') as output:
                json.dump(report, output, indent=2)
            os.chmod(name, 0o600)
            os.chown(name, account.pw_uid, account.pw_gid)
            os.replace(name, directory/'availability.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def state():
        return run('virsh', '-c', 'qemu:///system', 'domstate', 'makersim').stdout.strip()

    def browser(mode):
        result = run('runuser', '-u', 'legion', '--', '/usr/bin/node',
                     '/tmp/makersim-safety-qa/check-availability.mjs', mode, args.edge_ip)
        print(result.stdout.strip(), flush=True)

    def ready():
        result = run('curl', '--silent', '--show-error', '--max-time', '10',
                     '--resolve', f'makersim-playground.rawcastdigital.com:443:{args.edge_ip}',
                     'https://makersim-playground.rawcastdigital.com/api/health', check=False)
        try:
            data = json.loads(result.stdout)
            return result.returncode == 0 and data.get('status') == 'ready' and data.get('mode') == 'hosted'
        except (ValueError, AttributeError):
            return False

    assert state() == 'running' and ready()
    save()
    try:
        run('virsh', '-c', 'qemu:///system', 'shutdown', 'makersim')
        for _ in range(30):
            if state() == 'shut off':
                break
            time.sleep(1)
        else:
            raise RuntimeError('Guest did not shut down; leaving it running.')
        browser('offline')
        report['offline'] = 'passed'
        report['phase'] = 'restarting VM'
        save()
    finally:
        if state() == 'shut off':
            run('virsh', '-c', 'qemu:///system', 'start', 'makersim')
            print('Only MakerSim VM restarted. Waiting for its public readiness.', flush=True)
    for _ in range(90):
        if ready():
            break
        time.sleep(2)
    else:
        raise RuntimeError('VM is running but public readiness did not return.')
    browser('online')
    report['online'] = 'passed'
    assert hashlib.sha256(tunnel_config.read_bytes()).hexdigest() == digest
    assert run('systemctl', 'show', '--property=MainPID', '--value', 'cloudflared').stdout.strip() == pid
    run('systemctl', 'is-active', '--quiet', 'cloudflared')
    shft = run('curl', '--silent', '--show-error', '--max-time', '15', '--output', '/dev/null',
               '--write-out', '%{http_code}', 'https://shftstate.rawcastdigital.com/')
    assert shft.stdout == '200'
    report.update({'phase': 'passed; VM online', 'host_tunnel_unchanged': True, 'shftstate_http': 200})
    save()
    print('Offline/online checks passed. VM online; existing host tunnel unchanged; ShftState HTTP 200.', flush=True)


if __name__ == '__main__':
    main()
