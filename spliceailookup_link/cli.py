"""Command line interface for the spliceailookup-link server."""

from __future__ import annotations

import argparse
import sys

import httpx

from .config import ServerConfig, settings


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="spliceailookup-link unified server (REST /health + MCP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Transport Options:
  unified  - FastAPI /health + MCP HTTP (default)
  http     - same as unified (FastAPI host + MCP HTTP)
  stdio    - MCP STDIO only (for AI assistants)

Examples:
  uv run python server.py --transport unified --port 8030
  uv run python server.py --transport stdio
""",
    )
    parser.add_argument("--transport", choices=["unified", "http", "stdio"], default="unified")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--mcp-path", default="/mcp")
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.add_parser("config", help="Show configuration")
    health_parser = subparsers.add_parser("health", help="Check server health")
    health_parser.add_argument("--url", default="http://127.0.0.1:8030")
    return parser


def create_config_from_args(args: argparse.Namespace) -> ServerConfig:
    return ServerConfig(
        transport=args.transport,
        host=args.host,
        port=args.port,
        mcp_path=args.mcp_path,
        log_level=args.log_level,
    )


def handle_config_command(args: argparse.Namespace) -> None:
    config = create_config_from_args(args)
    print("=== spliceailookup-link Configuration ===")
    print(f"Transport: {config.transport}")
    print(f"Host: {config.host}")
    print(f"Port: {config.port}")
    print(f"MCP Path: {config.mcp_path}")
    print(f"Log Level: {config.log_level}")
    print()
    print("=== Upstream Settings ===")
    print(f"SpliceAI URL (hg38): {settings.spliceai_url('GRCh38')}")
    print(f"Pangolin URL (hg38): {settings.pangolin_url('GRCh38')}")
    print(f"Ensembl (GRCh38): {settings.ENSEMBL_GRCH38_URL}")
    print(f"REQUEST_TIMEOUT: {settings.REQUEST_TIMEOUT}s")
    print(f"MAX_CONCURRENCY: {settings.MAX_CONCURRENCY}")
    print(f"CACHE_SIZE / TTL_MIN: {settings.CACHE_SIZE} / {settings.CACHE_TTL_MINUTES}")


def handle_health_command(args: argparse.Namespace) -> None:
    try:
        response = httpx.get(f"{args.url}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print("Server is healthy")
            print(f"Transport: {data.get('transport', 'unknown')}")
            print(f"Status: {data.get('status', 'unknown')}")
        else:
            print(f"Server returned status {response.status_code}")
            sys.exit(1)
    except httpx.HTTPError as e:
        print(f"Failed to connect to server: {e}")
        sys.exit(1)


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()
    if args.command == "config":
        handle_config_command(args)
    elif args.command == "health":
        handle_health_command(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
