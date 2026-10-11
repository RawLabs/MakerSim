"""Initialize any stdio launch command and exercise directory introspection."""
import argparse
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def inspect(command):
    server = StdioServerParameters(command=command[0], args=command[1:])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = []
            cursor = None
            while True:
                page = await session.list_tools(cursor=cursor)
                tools.extend(tool.name for tool in page.tools)
                cursor = page.nextCursor
                if cursor is None:
                    break
            resources = await session.list_resources()
            templates = await session.list_resource_templates()
            prompts = await session.list_prompts()
            if not tools:
                raise RuntimeError("The server did not expose any tools.")
            print(json.dumps({
                "server": initialized.serverInfo.model_dump(),
                "protocol_version": initialized.protocolVersion,
                "tool_count": len(tools),
                "tools": tools,
                "resource_count": len(resources.resources),
                "resource_template_count": len(templates.resourceTemplates),
                "prompt_count": len(prompts.prompts),
            }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("supply a stdio server launch command after --")
    asyncio.run(asyncio.wait_for(inspect(command), timeout=60))


if __name__ == "__main__":
    main()
