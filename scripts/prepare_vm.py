"""Make a private NoCloud seed. Never puts credentials or private files in the checkout."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
TUNNEL = '0e589462-afe2-47fa-9966-b48d735f4bbf'
IMAGE_HASH = '6a81c37564db9b1ee84e141922625e1d7c5b389b99bb3c572e0243607d5bb4d2'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('/tmp/makersim-vm'))
    parser.add_argument('--credential', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    image = directory/'ubuntu-noble-amd64.img'
    assert hashlib.file_digest(image.open('rb'), 'sha256').hexdigest() == IMAGE_HASH, 'Ubuntu image checksum mismatch'
    credential = json.loads(args.credential.read_text())
    assert credential['TunnelID'] == TUNNEL, 'Only the dedicated MakerSim connector belongs in this VM'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for target in ['backend', 'frontend/dist', 'pyproject.toml', 'uv.lock']:
            source = ROOT/target
            paths = sorted(source.rglob('*')) if source.is_dir() else [source]
            for path in paths:
                if path.is_file() and '__pycache__' not in path.parts:
                    if path.is_symlink():
                        raise SystemExit(f'Source bundle refuses a symlink: {path}')
                    archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
        from tests.test_solver import bracket_request
        data = json.dumps(bracket_request().model_dump()).encode()
        entry = tarfile.TarInfo('readiness-request.json')
        entry.size = len(data)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(data))
    bundle = buffer.getvalue()
    # JSON is valid YAML; cloud-init consumes the standard #cloud-config header.
    files = []
    def write(path, content, permissions='0644'):
        files.append({'path':path,'permissions':permissions,'encoding':'b64','content':base64.b64encode(content.encode() if isinstance(content,str) else content).decode()})
    write('/root/makersim-source.tar.gz', bundle, '0600')
    write('/root/vm_guest_check.py', (ROOT/'scripts/vm_guest_check.py').read_bytes(), '0600')
    write('/etc/systemd/system/makersim.service', (ROOT/'deploy/makersim-guest.service').read_bytes())
    write('/etc/systemd/system/cloudflared.service', (ROOT/'deploy/cloudflared-guest.service').read_bytes())
    write(f'/etc/cloudflared/{TUNNEL}.json', json.dumps(credential), '0600')
    write('/etc/cloudflared/config.yml', f'tunnel: {TUNNEL}\ncredentials-file: /etc/cloudflared/{TUNNEL}.json\nedge-ip-version: "4"\nmetrics: 127.0.0.1:20241\ningress:\n  - hostname: makersim-playground.rawcastdigital.com\n    service: http://127.0.0.1:8000\n  - service: http_status:404\n')
    write('/etc/apt/apt.conf.d/99makersim-ipv4', 'Acquire::ForceIPv4 "true";\n')
    write('/etc/sysctl.d/90-makersim.conf', 'net.ipv6.conf.all.disable_ipv6=1\nnet.ipv6.conf.default.disable_ipv6=1\n')
    provision = '''#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/makersim-provision.log /dev/ttyS0) 2>&1
trap 'echo MAKERSIM_VM_PROVISION_FAILED' ERR
systemctl disable --now ssh.service ssh.socket || true
systemctl mask ssh.service ssh.socket
sysctl --system >/dev/null
mkdir -p /opt/makersim
python3 - <<'PY'
import tarfile
with tarfile.open('/root/makersim-source.tar.gz') as archive:
    archive.extractall('/opt/makersim', filter='data')
PY
python3 -m venv /opt/makersim-uv
/opt/makersim-uv/bin/pip install 'uv==0.12.7'
cd /opt/makersim
/opt/makersim-uv/bin/uv sync --frozen --no-dev --python /usr/bin/python3
curl --fail --silent --show-error --location https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes cloudflared
chown -R cloudflared:cloudflared /etc/cloudflared
chmod 0700 /etc/cloudflared
chown -R root:root /opt/makersim
chmod -R a+rX /opt/makersim
systemd-analyze verify /etc/systemd/system/makersim.service /etc/systemd/system/cloudflared.service
systemctl daemon-reload
systemctl enable --now makersim.service
/opt/makersim/.venv/bin/python /root/vm_guest_check.py
cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
systemctl restart makersim.service
systemctl enable --now cloudflared.service
rm /root/makersim-source.tar.gz
echo MAKERSIM_VM_READY
'''
    write('/root/provision-makersim.sh', provision, '0700')
    config = {
        'hostname':'makersim-vm','ssh_pwauth':False,'disable_root':True,
        'users':[{'name':name,'system':True,'shell':'/usr/sbin/nologin','lock_passwd':True} for name in ['makersim','cloudflared']],
        'bootcmd':[['systemctl','mask','--now','ssh.service','ssh.socket']],
        'package_update':True,'package_upgrade':True,'packages':['python3-venv','curl','ca-certificates'],
        'write_files':files,'runcmd':[['bash','/root/provision-makersim.sh']]
    }
    network = {'version':2,'ethernets':{'guest':{'match':{'macaddress':'52:54:00:4d:53:01'},'set-name':'eth0','dhcp4':False,'dhcp6':False,'accept-ra':False,'addresses':['192.168.130.2/24'],'routes':[{'to':'default','via':'192.168.130.1'}],'nameservers':{'addresses':['1.1.1.1','1.0.0.1']}}}}
    os.umask(0o077)
    (directory/'user-data').write_text('#cloud-config\n'+json.dumps(config))
    (directory/'meta-data').write_text(json.dumps({'instance-id':'makersim-'+str(uuid.uuid4()),'local-hostname':'makersim-vm'}))
    (directory/'network-config').write_text(json.dumps(network))
    subprocess.run(['cloud-localds','--network-config',str(directory/'network-config'),str(directory/'seed.iso'),str(directory/'user-data'),str(directory/'meta-data')],check=True)
    (directory/'seed.iso').chmod(0o600)
    print('Private seed ready. Source bundle excludes host keys, repository metadata, and local configuration.')


if __name__ == '__main__':
    main()
