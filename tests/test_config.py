from __future__ import annotations

from pathlib import Path

import pytest

from nethealth.config import ConfigError, load_config, parse_config

VALID = {
    "defaults": {"interval": 15, "timeout": 3, "retries": 2},
    "max_concurrency": 5,
    "targets": [
        {"name": "ping", "type": "icmp", "host": "1.1.1.1"},
        {"name": "web", "type": "tcp", "host": "example.com", "port": 443, "interval": 60},
        {"name": "dns", "type": "dns", "query": "example.com", "expect_records": "1.2.3.4"},
        {"name": "site", "type": "http", "url": "https://example.com", "expect_status": 200},
        {"name": "cert", "type": "tls", "host": "example.com"},
    ],
}


def test_valid_config_applies_defaults_and_overrides() -> None:
    cfg = parse_config(VALID)
    assert cfg.max_concurrency == 5
    by_name = {t.name: t for t in cfg.targets}
    assert by_name["ping"].interval == 15 and by_name["ping"].retries == 2
    assert by_name["web"].interval == 60 and by_name["web"].timeout == 3
    assert by_name["dns"].expect_records == ("1.2.3.4",)
    assert by_name["site"].expect_status == (200,)
    assert by_name["cert"].port == 443  # tls default


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"targets": []}, "non-empty list"),
        ({"targets": [{"name": "x", "type": "gopher", "host": "h"}]}, "unknown type"),
        ({"targets": [{"name": "x", "type": "tcp", "host": "h"}]}, "missing required field 'port'"),
        ({"targets": [{"name": "x", "type": "tcp", "host": "h", "port": 70000}]}, "1-65535"),
        ({"targets": [{"type": "icmp", "host": "h"}]}, "missing required field 'name'"),
        ({"targets": [{"name": "x", "type": "http", "url": "ftp://h"}]}, "http:// or https://"),
        (
            {"targets": [{"name": "x", "type": "icmp", "host": "h", "timeout": -1}]},
            "positive number",
        ),
        (
            {"targets": [{"name": "x", "type": "icmp", "host": "h", "timeout": 9, "interval": 5}]},
            "exceeds interval",
        ),
        (
            {"targets": [{"name": "x", "type": "dns", "query": "a.b", "record_type": "BOGUS"}]},
            "record_type",
        ),
        (
            {"targets": [{"name": "x", "type": "icmp", "host": "h", "retries": True}]},
            "integer",
        ),
        (
            {
                "targets": [
                    {"name": "x", "type": "icmp", "host": "h"},
                    {"name": "x", "type": "icmp", "host": "h2"},
                ]
            },
            "duplicate",
        ),
        ({"targets": [{"name": "x", "type": "icmp", "host": "h"}], "max_concurrency": 0}, ">= 1"),
    ],
)
def test_invalid_configs_raise_readable_errors(mutation: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(mutation)


def test_root_must_be_mapping() -> None:
    with pytest.raises(ConfigError):
        parse_config(["not", "a", "mapping"])


def test_load_config_from_yaml_file(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("targets:\n  - {name: a, type: icmp, host: 127.0.0.1}\n")
    assert load_config(p).targets[0].name == "a"


def test_load_config_missing_file_and_bad_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("targets: [unclosed")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(bad)
