"""Prepare a credential-free, narrowly scoped one-time MakerSim update."""
import base64
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def backend_sources(preview):
    # The solver's dependency closure includes the print-setting schema even
    # when the browser/app are left unchanged by a backend-only patch.
    names = {'geometry.py','hexa.py','limits.py','print_model.py','schemas.py','solver.py'}
    if preview:
        names.update({'app.py','materials.py','web.py'})
    return {f'backend/{name}':ROOT/'backend'/name for name in sorted(names)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview',action='store_true',help='Include the browser build and its bounded backend modules.')
    args = parser.parse_args()
    tag = uuid.uuid4().hex
    directory = Path(f'/tmp/makersim-app-update-{tag}')
    os.umask(0o077)
    directory.mkdir(mode=0o700)
    stage = f'/root/makersim-update/{tag}'
    files = []
    hashes = {}
    sources = {
        'vm_update_check.py':ROOT/'scripts/vm_update_check.py',
        'vm_guest_check.py':ROOT/'scripts/vm_guest_check.py',
        'apply_update.py':ROOT/'deploy/apply_update.py',
    }
    app_sources = backend_sources(args.preview)
    if args.preview:
        dist = ROOT/'frontend/dist'
        if not (dist/'index.html').is_file():
            raise SystemExit('Build frontend/dist with scripts/check.sh before preparing a preview update.')
        for path in sorted(dist.rglob('*')):
            if path.is_file():
                app_sources[str(path.relative_to(ROOT))] = path
    sources.update(app_sources)
    def add(name,data):
        files.append({'path':f'{stage}/{name}','permissions':'0600','encoding':'b64',
                      'content':base64.b64encode(data).decode()})
    for name,path in sources.items():
        if path.is_symlink():
            raise SystemExit(f'Update refuses a source symlink: {path}')
        data = path.read_bytes()
        add(name,data)
        if name in app_sources:
            hashes[name] = hashlib.sha256(data).hexdigest()
    add('manifest.json',json.dumps({'format':2,'tag':tag,'preview':args.preview,'files':hashes}).encode())
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
