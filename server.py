#!/usr/bin/env python
"""Unified spliceailookup-link server with multiple transport support.

Single entry point supporting a FastAPI host (/health) + MCP HTTP and MCP STDIO.
"""

import asyncio
import sys

from spliceailookup_link.cli import create_config_from_args, create_parser
from spliceailookup_link.exceptions import ConfigurationError, StartupError
from spliceailookup_link.server_manager import UnifiedServerManager


async def async_main(args) -> None:
    try:
        config = create_config_from_args(args)
        manager = UnifiedServerManager()
        await manager.start_server(config)
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
        sys.exit(0)
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
    except StartupError as e:
        print(f"Startup error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.command in ("config", "health"):
        from spliceailookup_link.cli import main as cli_main

        cli_main()
        return

    if args.transport == "stdio":
        try:
            config = create_config_from_args(args)
            manager = UnifiedServerManager()
            asyncio.run(manager.start_stdio_server(config))
        except Exception as e:
            print(f"STDIO server error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
