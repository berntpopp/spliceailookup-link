"""Tests for config, CLI, logging, and exception modules."""

from __future__ import annotations

import logging

from spliceailookup_link import cli
from spliceailookup_link.config import ServerConfig, hg_for_build, settings
from spliceailookup_link.exceptions import (
    ConfigurationError,
    MCPIntegrationError,
    StartupError,
)
from spliceailookup_link.logging_config import configure_logging, get_server_logger


def test_url_builders() -> None:
    assert settings.spliceai_url("GRCh38").endswith("spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/")
    assert settings.spliceai_url("GRCh37").endswith("-37-xwkwwwxdwq-uc.a.run.app/spliceai/")
    assert "pangolin-38" in settings.pangolin_url("GRCh38")
    assert settings.ensembl_url("GRCh37") == settings.ENSEMBL_GRCH37_URL
    assert settings.ensembl_url("GRCh38") == settings.ENSEMBL_GRCH38_URL


def test_hg_for_build() -> None:
    assert hg_for_build("GRCh38") == "38"
    assert hg_for_build("GRCh37") == "37"


def test_mcp_path_validator_adds_slash() -> None:
    from spliceailookup_link.config import Settings

    s = Settings(MCP_PATH="api/mcp")
    assert s.MCP_PATH == "/api/mcp"


def test_cors_origins_list() -> None:
    from spliceailookup_link.config import Settings

    assert Settings(CORS_ORIGINS="*").cors_origins_list == ["*"]
    assert Settings(CORS_ORIGINS="https://a.com, https://b.com").cors_origins_list == [
        "https://a.com",
        "https://b.com",
    ]


def test_server_config_from_env() -> None:
    cfg = ServerConfig.from_env()
    assert cfg.transport in ("unified", "http", "stdio")
    assert cfg.mcp_path.startswith("/")


def test_cli_parser_defaults() -> None:
    parser = cli.create_parser()
    args = parser.parse_args([])
    assert args.transport == "unified"
    cfg = cli.create_config_from_args(args)
    assert isinstance(cfg, ServerConfig)
    assert cfg.port == 8603


def test_cli_config_command(capsys) -> None:
    parser = cli.create_parser()
    args = parser.parse_args(["config"])
    cli.handle_config_command(args)
    out = capsys.readouterr().out
    assert "spliceailookup-link Configuration" in out
    assert "SpliceAI URL" in out


def test_exceptions_carry_transport() -> None:
    for exc_cls in (ConfigurationError, StartupError, MCPIntegrationError):
        e = exc_cls("boom", transport="stdio")
        assert e.transport == "stdio"
        assert str(e) == "boom"


def test_configure_logging_stdio_quiets_libraries() -> None:
    configure_logging("stdio", "WARNING")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.root.handlers


def test_configure_logging_unified() -> None:
    configure_logging("unified", "INFO")
    assert logging.root.level == logging.INFO


def test_get_server_logger_tags_transport() -> None:
    logger = get_server_logger("unified")
    msg, _ = logger.process("hello", {})
    assert msg == "[unified] hello"
