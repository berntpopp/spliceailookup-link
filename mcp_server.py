#!/usr/bin/env python
"""MCP STDIO server for spliceailookup-link.

Backwards-compatible STDIO entrypoint for AI assistants such as Claude Desktop.
Thin wrapper over the unified server architecture.
"""

import asyncio
import sys

from spliceailookup_link.config import ServerConfig
from spliceailookup_link.server_manager import UnifiedServerManager


def main() -> None:
    try:
        config = ServerConfig(
            transport="stdio",
            host="127.0.0.1",
            port=8030,
            mcp_path="/mcp",
            enable_docs=False,
            log_level="WARNING",
        )
        manager = UnifiedServerManager()
        asyncio.run(manager.start_stdio_server(config))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"MCP server error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
