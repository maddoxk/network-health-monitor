"""Compose smoke test: wait for the stack, then assert on the monitor, Prometheus, Grafana.

Uses only the standard library so CI needs no extra install. Run after `docker compose up -d`.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MONITOR = "http://localhost:8000"
PROM = "http://localhost:9090"
GRAFANA = "http://localhost:3000"
# Targets that only touch other containers, so they must be up even without internet.
MUST_BE_UP = ["monitor-tcp", "prometheus-dns", "prometheus-http", "grafana-http"]
MUST_EXIST = ["prometheus-icmp", "example-cert", "example-dns"]


def get(url: str, auth: str | None = None) -> str:
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return str(resp.read().decode())


def wait_for(description: str, check, timeout: float = 120.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.time() + timeout
    last: object = None
    while time.time() < deadline:
        try:
            if check():
                print(f"ok: {description}")
                return
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            last = exc
        time.sleep(2)
    sys.exit(f"FAILED: {description} (last error: {last})")


def probe_up_values() -> dict[str, float]:
    q = urllib.parse.quote("probe_up")
    data = json.loads(get(f"{PROM}/api/v1/query?query={q}"))
    return {r["metric"]["target"]: float(r["value"][1]) for r in data["data"]["result"]}


def main() -> None:
    wait_for("monitor /health", lambda: json.loads(get(f"{MONITOR}/health"))["status"] == "ok")

    def metrics_ready() -> bool:
        body = get(f"{MONITOR}/metrics")
        return all(re.search(rf'^probe_up\{{.*target="{t}"', body, re.M) for t in MUST_BE_UP)

    wait_for("monitor /metrics exposes probe_up for local targets", metrics_ready)

    def prom_targets_up() -> bool:
        data = json.loads(get(f"{PROM}/api/v1/targets"))["data"]["activeTargets"]
        return bool(data) and all(t["health"] == "up" for t in data)

    wait_for("Prometheus scrape targets are up", prom_targets_up)

    def local_targets_up() -> bool:
        values = probe_up_values()
        return all(values.get(t) == 1.0 for t in MUST_BE_UP) and all(
            t in values for t in MUST_EXIST
        )

    wait_for("Prometheus has probe_up == 1 for local targets", local_targets_up)

    def rules_loaded() -> bool:
        groups = json.loads(get(f"{PROM}/api/v1/rules"))["data"]["groups"]
        names = {r["name"] for g in groups for r in g["rules"]}
        return {"TargetDown", "HighLatency", "TlsCertExpiringSoon"} <= names

    wait_for("Prometheus alert rules loaded", rules_loaded)

    def grafana_ready() -> bool:
        ds = json.loads(get(f"{GRAFANA}/api/datasources", auth="admin:admin"))
        dash = json.loads(get(f"{GRAFANA}/api/search?query=Network", auth="admin:admin"))
        return any(d["type"] == "prometheus" for d in ds) and bool(dash)

    wait_for("Grafana datasource and dashboard provisioned", grafana_ready)
    values = probe_up_values()
    print("probe_up:", json.dumps(values, sort_keys=True))
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
