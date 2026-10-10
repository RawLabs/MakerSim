# Start MakerSim

## Local instance

Run `./scripts/start.sh`, then open the local URL printed by the launcher.
It starts at port 8000 and chooses another available port when needed.

See [installation and quick start](README.md#quick-start) for the full setup.

## Local MCP / CLI agent

For CLI or MCP access without the viewer:

```bash
rtk uv sync
rtk .venv/bin/makersim workflow
rtk .venv/bin/makersim session --help
```

Configure your MCP client to launch this checkout's
`.venv/bin/makersim-mcp` using its absolute path. See
[MCP setup](README.md#connect-a-local-mcp-client) for the configuration.

For live design work, start the local viewer and choose **Agent**. Save a session,
give its ID to a compatible MCP/CLI agent, then tell that agent when to start.
The viewer preserves geometry, tests, explanations, and questions; CAD work runs
in the connected client. Saving a session or reopening the window does not start
work. Clients keep reading window messages and heartbeat while waiting for you.

The viewer and agent must share the same session directory. It defaults to
`.makersim/sessions/` in this checkout; set the same absolute
`MAKERSIM_SESSION_DIR` in both environments when overriding it. See
[live engineering sessions](README.md#live-engineering-sessions) for commands,
pause/resume, saved history, and recovery. These sessions are local-only.

## Demo VM

Run these commands in a terminal on Legion.

Check whether the VM is running (read-only):

```bash
rtk virsh --readonly -c qemu:///system domstate makersim
```

`running` means the VM is on; `shut off` means it is off.

Start the VM:

```bash
rtk sudo virsh -c qemu:///system start makersim
```

Stop the VM gracefully:

```bash
rtk sudo virsh -c qemu:///system shutdown makersim
```

Shutdown can take a moment. Repeat the status check until it reports `shut off`.
VM autostart is disabled, so start it manually after a Legion reboot.

The [web demo](https://makersim.rawcastdigital.com/) shows server availability.
After starting the VM, allow a moment for the
[playground](https://makersim-playground.rawcastdigital.com/) to become ready.
