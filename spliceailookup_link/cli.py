"""Typer command line interface for the spliceailookup-link server.

GeneFoundry Logging & CLI Standard v1: a single ``Typer`` app exposing
``serve`` / ``config`` / ``health`` / ``version``. Streamable HTTP only — there
is no stdio transport and no bare-serve entry point.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import httpx
import typer
from rich.console import Console

from . import __version__
from .config import ServerConfig, settings

app = typer.Typer(
    name="spliceailookup-link",
    add_completion=False,
    no_args_is_help=True,
    help="spliceailookup-link unified server (FastAPI /health + MCP Streamable HTTP).",
)
console = Console()

Transport = Annotated[str, typer.Option(help="Transport mode: unified or http.")]


@app.command()
def serve(
    transport: Annotated[
        str,
        typer.Option(help="Transport mode: 'unified' or 'http'."),
    ] = "unified",
    host: Annotated[str, typer.Option(help="Host to bind to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind to.")] = 8603,
    mcp_path: Annotated[str, typer.Option(help="MCP endpoint path.")] = "/mcp",
    log_level: Annotated[
        str,
        typer.Option(help="Log level: DEBUG, INFO, WARNING, or ERROR."),
    ] = "INFO",
    disable_docs: Annotated[
        bool,
        typer.Option("--disable-docs", help="Disable API documentation endpoints."),
    ] = False,
    dev: Annotated[
        bool,
        typer.Option("--dev", help="Development mode (verbose console logging)."),
    ] = False,
) -> None:
    """Start the unified FastAPI host (/health) with the MCP HTTP app mounted at /mcp."""
    if transport not in {"unified", "http"}:
        console.print(
            f"[red]Invalid transport '{transport}'. Choose 'unified' or 'http' "
            "(stdio is not supported).[/red]"
        )
        raise typer.Exit(code=2)
    if log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        console.print(f"[red]Invalid log level '{log_level}'.[/red]")
        raise typer.Exit(code=2)

    # Imported lazily so `--help`, `version`, etc. don't pull in uvicorn/fastmcp.
    from .exceptions import ConfigurationError, StartupError
    from .server_manager import UnifiedServerManager

    config = ServerConfig(
        transport=transport,  # type: ignore[arg-type]
        host=host,
        port=port,
        mcp_path=mcp_path,
        enable_docs=not disable_docs,
        log_level="DEBUG" if dev else log_level.upper(),
    )

    manager = UnifiedServerManager()
    try:
        asyncio.run(manager.start_server(config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutdown requested by user.[/yellow]")
        raise typer.Exit(code=0) from None
    except ConfigurationError as exc:
        console.print(f"[red]Configuration error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except StartupError as exc:
        console.print(f"[red]Startup error: {exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def config(
    validate: Annotated[
        bool,
        typer.Option("--validate", help="Validate the resolved configuration."),
    ] = False,
) -> None:
    """Show the resolved server and upstream configuration."""
    cfg = ServerConfig.from_env()

    console.print("[bold]=== spliceailookup-link Configuration ===[/bold]")
    console.print(f"Transport: {cfg.transport}")
    console.print(f"Host: {cfg.host}")
    console.print(f"Port: {cfg.port}")
    console.print(f"MCP Path: {cfg.mcp_path}")
    console.print(f"Log Level: {cfg.log_level}")
    console.print(f"Log Format: {settings.LOG_FORMAT}")
    console.print()
    console.print("[bold]=== Upstream Settings ===[/bold]")
    console.print(f"SpliceAI URL (hg38): {settings.spliceai_url('GRCh38')}")
    console.print(f"Pangolin URL (hg38): {settings.pangolin_url('GRCh38')}")
    console.print(f"Ensembl (GRCh38): {settings.ENSEMBL_GRCH38_URL}")
    console.print(f"REQUEST_TIMEOUT: {settings.REQUEST_TIMEOUT}s")
    console.print(f"MAX_CONCURRENCY: {settings.MAX_CONCURRENCY}")
    console.print(f"CACHE_SIZE / TTL_MIN: {settings.CACHE_SIZE} / {settings.CACHE_TTL_MINUTES}")

    if validate:
        console.print()
        console.print("[bold]=== Configuration Validation ===[/bold]")
        if cfg.port < 1 or cfg.port > 65535:
            console.print("[red]Invalid port number.[/red]")
            raise typer.Exit(code=1)
        if not cfg.mcp_path.startswith("/"):
            console.print("[red]MCP path must start with '/'.[/red]")
            raise typer.Exit(code=1)
        console.print("[green]Configuration is valid.[/green]")


@app.command()
def health(
    url: Annotated[
        str,
        typer.Option(help="Base server URL to probe."),
    ] = "http://127.0.0.1:8603",
) -> None:
    """Check the running server's /health endpoint."""
    try:
        response = httpx.get(f"{url}/health", timeout=5)
    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to connect to server: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if response.status_code != 200:
        console.print(f"[red]Server returned status {response.status_code}.[/red]")
        raise typer.Exit(code=1)

    data = response.json()
    console.print("[green]Server is healthy.[/green]")
    console.print(f"Transport: {data.get('transport', 'unknown')}")
    console.print(f"Status: {data.get('status', 'unknown')}")


@app.command()
def version() -> None:
    """Print the installed spliceailookup-link version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
