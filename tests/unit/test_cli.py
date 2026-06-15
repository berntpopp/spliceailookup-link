"""Tests for the typer CLI (GeneFoundry Logging & CLI Standard v1)."""

from __future__ import annotations

import importlib.metadata

from typer.testing import CliRunner

from spliceailookup_link import __version__
from spliceailookup_link.cli import app

runner = CliRunner()


def test_app_is_typer() -> None:
    import typer

    assert isinstance(app, typer.Typer)
    assert app.info.name == "spliceailookup-link"


def test_help_lists_required_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("serve", "config", "health", "version"):
        assert command in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help=True -> help text, non-zero exit per typer convention.
    assert result.exit_code != 0
    assert "serve" in result.output


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_command_runs() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "spliceailookup-link Configuration" in result.output
    assert "SpliceAI URL" in result.output


def test_config_validate_ok() -> None:
    result = runner.invoke(app, ["config", "--validate"])
    assert result.exit_code == 0
    assert "Configuration is valid" in result.output


def test_serve_rejects_stdio_transport() -> None:
    result = runner.invoke(app, ["serve", "--transport", "stdio"])
    assert result.exit_code != 0
    assert "stdio" in result.output.lower()


def test_serve_accepts_unified_and_http_transport(monkeypatch) -> None:
    """serve must accept both canonical transports; we stub the blocking run."""
    started: list[str] = []

    async def _fake_start_server(self, config) -> None:
        started.append(config.transport)

    monkeypatch.setattr(
        "spliceailookup_link.server_manager.UnifiedServerManager.start_server",
        _fake_start_server,
    )

    for transport in ("unified", "http"):
        started.clear()
        result = runner.invoke(app, ["serve", "--transport", transport, "--port", "8999"])
        assert result.exit_code == 0, result.output
        assert started == [transport]


def test_serve_rejects_invalid_log_level() -> None:
    result = runner.invoke(app, ["serve", "--log-level", "TRACE"])
    assert result.exit_code != 0


def test_console_script_entry_resolves() -> None:
    """The single console-script entry point resolves to the typer app."""
    (entry,) = [
        ep
        for ep in importlib.metadata.entry_points(group="console_scripts")
        if ep.name == "spliceailookup-link"
    ]
    assert entry.value == "spliceailookup_link.cli:app"
    assert entry.load() is app
