# MCP directory publication

The canonical source is <https://github.com/RawLabs/MakerSim>. The local stdio
server starts without credentials, a browser, a model download, or a hosted
endpoint. The temporary website is not an MCP endpoint.

## Glama submission

1. Sign in through GitHub at <https://glama.ai/mcp/servers> and choose Add Server.
   Glama verifies the submitting account's write or admin access to the repo.
2. Submit `https://github.com/RawLabs/MakerSim`, named **MakerSim**, with this
   description:

   > Local MCP tools for experimental load-path analysis of 3D-printed STL
   > parts, CAD revision comparisons, visual reviews, and persistent
   > human-guided design sessions.

3. Paste the complete contents of the root [Dockerfile](../Dockerfile) into
   Glama's Dockerfile/build settings directly, as required by the directory PR.
   Use the repository root as the build context and the image's default stdio
   entrypoint. No environment credentials or exposed HTTP port are required.
4. Run Glama's checks and wait for a successful build and introspection. MakerSim
   exposes 27 tools and empty resource, resource-template, and prompt lists.
   Review every reported check; a local smoke test does not establish Glama's
   evaluation result. The project's license remains unspecified; do not select
   a license in a directory that the repository has not adopted.
5. Confirm the listing's actual path and append its score badge after the
   description in the [directory PR](https://github.com/punkpeye/awesome-mcp-servers/pull/16164).
   For a listing at `RawLabs/MakerSim`, the badge is:

   ```markdown
   [![RawLabs/MakerSim MCP server](https://glama.ai/mcp/servers/RawLabs/MakerSim/badges/score.svg)](https://glama.ai/mcp/servers/RawLabs/MakerSim)
   ```

6. Mark the PR ready after the Glama checks and directory requirements pass.

The root [glama.json](../glama.json) identifies the repository maintainer.
Glama's [indexing methodology](https://glama.ai/mcp/methodology) describes
maintainer verification, container builds, and protocol introspection.

## Local inspection

From the repository root, build the image and inspect its default entrypoint:

```bash
docker build -t makersim-mcp .
.venv/bin/python scripts/mcp-introspection.py -- docker run --rm -i --network none makersim-mcp
```

Use `-i` to keep stdin open. The inspection sends MCP initialization followed
by tool, resource, resource-template, and prompt discovery. It exits on a
protocol error or timeout. It does not run a simulation or create a session.

The [MCP container workflow](../.github/workflows/mcp-container.yml) builds and
checks the actual image on GitHub, including discovery without network access
and inspection of the bundled STL. It runs when the container or Python server
changes and can also be started manually.

To check an installed local server with the same requests:

```bash
.venv/bin/python scripts/mcp-introspection.py -- .venv/bin/makersim-mcp
```

The image installs production dependencies from `uv.lock` and includes the
bundled STL, test specification, and materials. Its session directory is
`/data/sessions`; it is ephemeral unless persisted with a volume. Tool file
arguments refer to paths inside the container. To analyze your own files,
mount their directory and supply the corresponding container paths.
