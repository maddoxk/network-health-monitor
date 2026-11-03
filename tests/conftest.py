from __future__ import annotations

from nethealth.config import Target


def make_target(**overrides: object) -> Target:
    base: dict[str, object] = {
        "name": "t",
        "type": "tcp",
        "interval": 10.0,
        "timeout": 1.0,
        "retries": 0,
    }
    base.update(overrides)
    return Target(**base)  # type: ignore[arg-type]
