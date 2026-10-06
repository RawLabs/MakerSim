# MakerSim on Legion

`makersim.rawcastdigital.com` is the static landing page; the VM serves
`makersim-playground.rawcastdigital.com`. The landing page never accepts uploads.
It reads only `/api/health` without browser credentials. Its offline contact is
https://shftstate.rawcastdigital.com/#contact.

The dashboard upload created a static-assets **Worker** named `wild-salad-4bd5`.
Its verified public address is
https://wild-salad-4bd5.resolve-landing.workers.dev/.
`resolve-landing` is the account's workers.dev subdomain; preserve that account
setting. The prepared HTML and security headers are verified on this deployment.
`makersim.rawcastdigital.com` is connected and verified over HTTPS. In the current
dashboard, custom domains are managed through the Worker's **Domains > Add Domain**
tab: select the `rawcastdigital.com` zone, then enter `makersim` as the subdomain.
The local Wrangler name matches this Worker for
future authorized deployments. The VM and playground tunnel remain separate.

ShftState and existing SSH tunnels are protected deployment boundaries. Use a
new MakerSim landing-page project with only the exact `makersim.rawcastdigital.com`
hostname. Do not modify ShftState's project, use wildcard/root-domain routes,
change existing DNS records, or restart the host `cloudflared.service`. The VM
has its own connector, tunnel ID, and new playground DNS hostname. It receives
no credentials for the host's SSH tunnel or Cloudflare account administration.

The landing page can be uploaded as a separate project through the Cloudflare
dashboard without granting Wrangler persistent account-wide access. If using
API automation, review the actual token permission/resource limits: an account
Workers-edit grant may cover other Workers, even when the deployment config names
only MakerSim. A dedicated project is not itself an account permission boundary.

## Prepare the app

```bash
uv sync --locked
./scripts/check.sh
```

The checks build `frontend/dist`, which is included in the VM snapshot. The guest
needs no Node development server. Use one Python worker only.

## Prepare and install the VM

Install `qemu-system-x86 qemu-img libvirt dnsmasq nftables cloud-image-utils` using
`omarchy pkg add`. Download Ubuntu's Noble AMD64 cloud image into a private
`/tmp/makersim-vm/ubuntu-noble-amd64.img`. The installer pins its verified checksum;
review and update the pin if rebuilding with a newer Canonical image.

Create a dedicated locally managed Cloudflare tunnel named `makersim-vm`.
`prepare_vm.py` pins the dedicated ID and refuses another tunnel's credential.
Never give the guest the account certificate, existing host tunnel credential,
host SSH keys, environment files, or repository metadata.

```bash
PYTHONPATH=. .venv/bin/python scripts/prepare_vm.py \
  --credential /home/legion/.cloudflared/0e589462-afe2-47fa-9966-b48d735f4bbf.json
sudo /usr/bin/python scripts/install_vm.py --public-gateway <current-public-IPv4>
```

The sudo command belongs in a visible terminal. It disables the retired direct
host app, installs only a dedicated nftables table and libvirt configuration,
starts the VM, and waits for guest checks. It refuses existing disks/domains or
different existing configuration files. It does not overwrite other host
firewall tables or the existing SSH tunnel. No LAN bridge, host mount, guest
agent, clipboard, passthrough device, or inbound router port is added.

The VM is 2 CPU / 4 GiB / 25 GiB. It uses static IPv4 `192.168.130.2`, public DNS,
and a dedicated NAT bridge. Guest IPv6, guest-to-host, private/reserved destination
ranges, and the captured public gateway are blocked by the host. Egress is bounded
to public HTTP/HTTPS, Cloudflare tunnel ports, and the chosen DNS resolvers.

If UFW is active, the installer adds six forwarding rules limited to the guest's
IPv4 address, its bridge, and the current IPv4 uplink: DNS to the two chosen
resolvers, TCP 80/443/7844, and UDP 7844. Existing UFW policies and SSH rules remain
in place; the installer does not reload, reset, disable, or enable UFW. The
dedicated nftables rules still reject protected destinations before UFW accepts
permitted traffic. These interface-specific rules need review if the uplink changes.

For a failed initial installation, regenerate the private seed with
`prepare_vm.py`, then run the installer with `--resume` and the current public
IPv4 address. This preserves the failed VM's disk and refuses an already deployed
VM. Filtered progress is exported to `/tmp/makersim-vm/status.json` for review.

The host firewall is loaded before libvirt starts. A QEMU hook also refuses VM
startup if the dedicated isolation table is absent. Stopping the firewall service
leaves its rules in place. Do not flush the host firewall while a VM is running.
If the public gateway IP changes, stop the VM, update `/etc/makersim/isolation.nft`,
restart `makersim-isolation`, and start the VM. Host administrator changes can
override these protections; hypervisor escape risk remains.

Guest checks verify upload/solve, extreme directions and force balance, invalid
numbers, workspace ownership, origins, trusted host, landing-page readiness,
blocked protected addresses, and public HTTPS. The connector starts only after
those pass. The installer then ejects the one-time seed and disables VM autostart.
The guest has locked system users, masked SSH, and no password or key login.

Inspect `/var/log/libvirt/qemu/makersim-serial.log` locally if provisioning fails.
Treat logs as untrusted text; never execute commands pasted from them. The private
seed and its cloud-init inputs contain a connector credential: delete temporary
copies after successful provisioning and keep them out of Git and public hosting.

## Publish

Update the existing static-assets Worker `wild-salad-4bd5` through **New
deployment**. Upload the complete contents of `deploy/landing`, including the
`assets` directory, or the prepared `makersim-landing.zip`, then deploy. The ZIP
must place `index.html` at its root. Keep the existing custom domain and Worker;
the update requires no DNS, tunnel, or VM changes.

The landing hero is a rotatable 3D bracket with a baked field from the bundled
example's real solver result. It makes no solver requests and remains visible
when the VM is offline. A static rendering provides the same preview without
WebGL. To regenerate its mesh and bundled JavaScript/CSS:

```bash
rtk node scripts/build_landing.mjs
```

This uses the existing locked frontend dependencies and Python environment;
only files in `deploy/landing` belong in the public upload. The source files in
`deploy/hero` and VM provisioning files stay outside the ZIP. The preview uses
the non-sensitive included bracket, never visitors' uploads.

For a separate new installation, Pages Direct Upload is an alternative: create
`makersim-landing`, upload the same complete public directory, and connect only
`makersim.rawcastdigital.com`. Choose a single project for that hostname. The
static page can be published before the VM is ready; it will report offline and
offer the ShftState contact link. No Functions or paid hosting add-ons are required.

After `/etc/makersim/ready` exists and firewall checks pass, publish only the new
playground hostname:

```bash
cloudflared tunnel route dns 0e589462-afe2-47fa-9966-b48d735f4bbf makersim-playground.rawcastdigital.com
```

An alternative automated landing deployment is `npx wrangler deploy --config
deploy/wrangler.jsonc`; this updates the named Workers static-assets project
and requires separately reviewed Cloudflare authorization. Choose either Pages
dashboard publication or the Workers deployment, not both for the same hostname.
The static asset directory is `deploy/landing`; no app source or secret is hosted
there. The landing page is independent of Legion. When health checks fail, it
shows **PLAYGROUND OFFLINE** and the ShftState session-request link. Only readiness
has cross-origin read access; upload/solve remains same-origin.

Use edge upload/rate protections if available in the account; retain application
body limits, two upload slots, per-IP request limits, and the single solve gate
regardless. The application still receives requests below edge limits, so avoid
claiming the edge replaces app resource bounds.

## Operate and stop

```bash
sudo virsh -c qemu:///system dominfo makersim
sudo virsh -c qemu:///system shutdown makersim
```

If graceful shutdown stalls, `sudo virsh -c qemu:///system destroy makersim`
immediately stops this disposable VM (it does not delete its disk). The installer
leaves VM autostart disabled, so it stays off after a Legion reboot until manually
started. Libvirt, the dedicated NAT network, and the isolation rules persist.
Do not stop Legion's separate `cloudflared.service`; it supplies the existing SSH
tunnel. To start the playground, `sudo virsh -c qemu:///system start makersim`.

Editing the host checkout does not update the VM. A narrowly scoped geometry/solver
update can use a credential-free, temporary NoCloud disk:

```bash
rtk .venv/bin/python -m scripts.prepare_vm_update
rtk sudo /usr/bin/python scripts/update_vm.py --directory <printed-private-directory>
```

Run the sudo command in a visible terminal. It updates only `backend/geometry.py`
and `backend/solver.py`, restarts the MakerSim VM, and clears temporary workspaces.
The guest backs up those files, runs synthetic cavity and existing readiness/
isolation checks, and restores the previous source if its checks fail. The host
controller ejects the disk and verifies the host SSH connector's process ID and
configuration remained the same. The seed contains no tunnel credentials or
user uploads. Its first-boot module list is restricted to staging and running the
update; existing users and SSH configuration are preserved. Frontend, dependency,
service, or other application updates still require a separately reviewed guest
deployment/rebuild.

Revoke and recreate the dedicated tunnel
credential if the guest is suspected compromised. The account certificate stays
on the host. No permanent STL/result archive or analytics is added.

## Verified trial deployment, 2026-10-06

The landing domain and `makersim-playground.rawcastdigital.com` are connected.
The VM passed its guest readiness checks and a real Cloudflare upload/solve.
Live checks covered separate browser workspaces, secure host-only cookies,
extreme finite directions, non-finite input rejection, origin checks, and health-only
CORS. The sample's relative force-balance error was approximately `4.6e-12`.

The cavity fix was applied through a temporary update disk, then passed guest
readiness/isolation checks and a public generated hollow-part upload/solve. The
TrenchHook STL (104,552 triangles) also passed local and public API upload/solve
checks; its inner closed surface was confirmed to be a cavity and no solver
cells filled it. A separate public workspace could not access that uploaded part.
The Python suite passed all 54 tests. The update disk was ejected and removed, and
the host SSH connector's process ID and configuration remained the same.

Host firewall counters confirmed protected-address probes were rejected. The
running VM has no host filesystem mounts, device passthrough, graphical console,
or guest-agent channel. Its seed was detached and removed; autostart is disabled.
Stopping the VM produced the landing's offline/contact state, and restarting it
restored online status. The host SSH connector retained its process ID and
configuration, and ShftState still returned HTTP 200.

The public application and published landing use “isolated preview server” in
their upload notices. The new 3D landing hero is published on `wild-salad-4bd5`
and rendered successfully in a live browser while reporting the playground online.
Its browser checks cover rotation, keyboard controls,
mobile layout, strict security headers, online/offline states, and a static
fallback when WebGL is unavailable.

The numerical model remains an experimental coarse linear-elastic preview. Its
force-balance checks verify equilibrium of that model; they do not establish
failure predictions or safe working loads. Coarse-mesh bending can be understated,
and physical testing remains necessary for load-bearing designs.
