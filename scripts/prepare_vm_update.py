"""Prepare a credential-free, narrowly scoped one-time MakerSim update."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    tag = uuid.uuid4().hex
    directory = Path(f'/tmp/makersim-app-update-{tag}')
    os.umask(0o077)
    directory.mkdir(mode=0o700)
    stage = f'/root/makersim-update/{tag}'
    files = []
    hashes = {}
    sources = {
        'geometry.py':ROOT/'backend/geometry.py',
        'solver.py':ROOT/'backend/solver.py',
        'vm_update_check.py':ROOT/'scripts/vm_update_check.py',
        'vm_guest_check.py':ROOT/'scripts/vm_guest_check.py',
        'apply_update.py':ROOT/'deploy/apply_update.py',
    }
    def add(name,data):
        files.append({'path':f'{stage}/{name}','permissions':'0600','encoding':'b64',
                      'content':base64.b64encode(data).decode()})
    for name,path in sources.items():
        if path.is_symlink():
            raise SystemExit(f'Update refuses a source symlink: {path}')
        data = path.read_bytes()
        add(name,data)
        if name in ('geometry.py','solver.py'):
            hashes[name] = hashlib.sha256(data).hexdigest()
    add('manifest.json',json.dumps({'tag':tag,'files':hashes}).encode())
    # Override first-boot modules so a new NoCloud ID only stages and runs this
    # update. No users, packages, host keys, tunnel settings, or credentials.
    config = {
        'users':[],'ssh_pwauth':False,'disable_root':True,
        'cloud_init_modules':['write_files'],
        'cloud_config_modules':['runcmd'],'cloud_final_modules':['scripts_user'],
        'write_files':files,'runcmd':[['/usr/bin/python3',f'{stage}/apply_update.py',stage]],
    }
    network = {'version':2,'ethernets':{'guest':{'match':{'macaddress':'52:54:00:4d:53:01'},
        'set-name':'eth0','dhcp4':False,'dhcp6':False,'accept-ra':False,
        'addresses':['192.168.130.2/24'],'routes':[{'to':'default','via':'192.168.130.1'}],
        'nameservers':{'addresses':['1.1.1.1','1.0.0.1']}}}}
    (directory/'user-data').write_text('#cloud-config\n'+json.dumps(config))
    (directory/'meta-data').write_text(json.dumps({'instance-id':f'makersim-update-{tag}','local-hostname':'makersim-vm'}))
    (directory/'network-config').write_text(json.dumps(network))
    subprocess.run(['cloud-localds','--network-config',str(directory/'network-config'),
        str(directory/'update.iso'),str(directory/'user-data'),str(directory/'meta-data')],check=True)
    digest = hashlib.sha256((directory/'update.iso').read_bytes()).hexdigest()
    (directory/'update.json').write_text(json.dumps({'tag':tag,'iso_sha256':digest,'files':hashes}))
    print(directory)


if __name__ == '__main__':
    main()
