"""Tests for config, logging, and exception modules."""

from __future__ import annotations

import logging

import structlog

from spliceailookup_link.config import ServerConfig, hg_for_build, settings
from spliceailookup_link.exceptions import (
    ConfigurationError,
    MCPIntegrationError,
    StartupError,
)
from spliceailookup_link.logging_config import configure_logging


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
    assert cfg.transport in ("unified", "http")
    assert cfg.mcp_path.startswith("/")


def test_transport_literal_excludes_stdio() -> None:
    from spliceailookup_link.config import Settings

    fields = Settings.model_fields["MCP_TRANSPORT"].annotation
    assert "stdio" not in repr(fields)


def test_exceptions_carry_transport() -> None:
    for exc_cls in (ConfigurationError, StartupError, MCPIntegrationError):
        e = exc_cls("boom", transport="unified")
        assert e.transport == "unified"
        assert str(e) == "boom"


def test_configure_logging_returns_bound_logger() -> None:
    logger = configure_logging("INFO")
    assert logging.root.level == logging.INFO
    assert hasattr(logger, "info")


def test_configure_logging_emits_static_fields(capsys) -> None:
    from spliceailookup_link import __version__

    configure_logging("INFO")
    logger = structlog.get_logger("spliceailookup_link.test")
    logger.info("hello")
    captured = capsys.readouterr()
    payload = captured.out + captured.err
    assert "spliceailookup-link" in payload
    assert __version__ in payload
    assert "hello" in payload


def test_predict_soft_deadline_default() -> None:
    from spliceailookup_link.config import Settings

    assert Settings().PREDICT_SOFT_DEADLINE_SECONDS == 55
