"""Disable the retired direct-host service; public hosting belongs in the VM."""
import argparse
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-service', action='store_true')
    parser.add_argument('--install-ingress', action='store_true')
    parser.add_argument('--disable-direct-host', action='store_true')
    args = parser.parse_args()
    if args.install_service or args.install_ingress:
        raise SystemExit('Direct host deployment is retired. Use the isolated VM deployment.')
    if not args.disable_direct_host:
        print('Use scripts/prepare_vm.py and scripts/install_vm.py. See deploy/README.md.')
        return
    if os.geteuid() != 0:
        raise SystemExit('Disabling the system service requires administrator authentication.')
    subprocess.run(['systemctl', 'disable', '--now', 'makersim.service'], check=True)
    # The host connector supplies existing SSH access. Never modify or restart it.


if __name__ == '__main__':
    main()
