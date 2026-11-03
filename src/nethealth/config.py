"""YAML configuration: loading and validation.

We validate by hand (no pydantic) so the rules are easy to read and every error
message can name the exact target and field that is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROBE_TYPES = ("icmp", "tcp", "dns", "http", "tls")
DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA")


class ConfigError(ValueError):
    """Raised when the config file is missing, malformed, or semantically wrong."""


@dataclass(frozen=True)
class Target:
    name: str
    type: str
    interval: float
    timeout: float
    retries: int
    # Which of the fields below are used depends on `type` (checked in _build_target).
    host: str | None = None
    port: int | None = None
    url: str | None = None
    query: str | None = None
    record_type: str = "A"
    nameserver: str | None = None
    expect_records: tuple[str, ...] = ()
    expect_status: tuple[int, ...] = ()
    verify_tls: bool = True


@dataclass(frozen=True)
class Config:
    targets: tuple[Target, ...] = field(default_factory=tuple)
    max_concurrency: int = 20


def _num(raw: dict[str, Any], key: str, default: float, where: str) -> float:
    value = raw.get(key, default)
    # bool is a subclass of int in Python, so exclude it explicitly.
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{where}: '{key}' must be a positive number, got {value!r}")
    return float(value)


def _int(raw: dict[str, Any], key: str, default: int, where: str, minimum: int = 0) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{where}: '{key}' must be an integer >= {minimum}, got {value!r}")
    return value


def _str(raw: dict[str, Any], key: str, where: str, required: bool = False) -> str | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise ConfigError(f"{where}: missing required field '{key}'")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: '{key}' must be a non-empty string, got {value!r}")
    return value.strip()


def _port(raw: dict[str, Any], where: str, default: int | None) -> int:
    if "port" not in raw and default is not None:
        return default
    if "port" not in raw:
        raise ConfigError(f"{where}: missing required field 'port'")
    port = _int(raw, "port", 0, where, minimum=1)
    if port > 65535:
        raise ConfigError(f"{where}: 'port' must be 1-65535, got {port}")
    return port


def _str_list(raw: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: '{key}' must be a list of strings")
    return tuple(value)


def _build_target(raw: Any, index: int, defaults: dict[str, Any]) -> Target:
    if not isinstance(raw, dict):
        raise ConfigError(f"targets[{index}]: must be a mapping, got {type(raw).__name__}")
    name = _str(raw, "name", f"targets[{index}]", required=True)
    assert name is not None
    where = f"target '{name}'"
    ptype = _str(raw, "type", where, required=True)
    if ptype not in PROBE_TYPES:
        raise ConfigError(f"{where}: unknown type {ptype!r}; expected one of {PROBE_TYPES}")

    merged = {**defaults, **raw}
    common: dict[str, Any] = {
        "name": name,
        "type": ptype,
        "interval": _num(merged, "interval", 30, where),
        "timeout": _num(merged, "timeout", 5, where),
        "retries": _int(merged, "retries", 1, where),
    }
    if common["timeout"] > common["interval"]:
        raise ConfigError(f"{where}: timeout ({common['timeout']}s) exceeds interval")

    if ptype == "icmp":
        host = _str(raw, "host", where, required=True)
        return Target(**common, host=host)
    if ptype == "tcp":
        host = _str(raw, "host", where, required=True)
        return Target(**common, host=host, port=_port(raw, where, None))
    if ptype == "tls":
        host = _str(raw, "host", where, required=True)
        return Target(**common, host=host, port=_port(raw, where, 443))
    if ptype == "dns":
        query = _str(raw, "query", where, required=True)
        rtype = (_str(raw, "record_type", where) or "A").upper()
        if rtype not in DNS_RECORD_TYPES:
            raise ConfigError(f"{where}: unsupported record_type {rtype!r}")
        return Target(
            **common,
            query=query,
            record_type=rtype,
            nameserver=_str(raw, "nameserver", where),
            expect_records=_str_list(raw, "expect_records", where),
        )
    # http
    url = _str(raw, "url", where, required=True)
    assert url is not None
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"{where}: 'url' must start with http:// or https://")
    statuses = raw.get("expect_status", [])
    if isinstance(statuses, int) and not isinstance(statuses, bool):
        statuses = [statuses]
    if not isinstance(statuses, list) or not all(
        isinstance(s, int) and not isinstance(s, bool) and 100 <= s <= 599 for s in statuses
    ):
        raise ConfigError(f"{where}: 'expect_status' must be an int or list of ints (100-599)")
    return Target(
        **common,
        url=url,
        expect_status=tuple(statuses),
        verify_tls=bool(raw.get("verify_tls", True)),
    )


def parse_config(data: Any) -> Config:
    """Validate an already-parsed YAML document."""
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping with a 'targets' list")
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError("'defaults' must be a mapping")
    raw_targets = data.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigError("'targets' must be a non-empty list")

    targets = tuple(_build_target(t, i, defaults) for i, t in enumerate(raw_targets))
    seen: set[str] = set()
    for t in targets:
        if t.name in seen:
            raise ConfigError(f"duplicate target name {t.name!r}")
        seen.add(t.name)

    max_conc = _int(data, "max_concurrency", 20, "config", minimum=1)
    return Config(targets=targets, max_concurrency=max_conc)


def load_config(path: str | Path) -> Config:
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    return parse_config(data)
