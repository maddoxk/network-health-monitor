"""Probe implementations and the type -> probe registry."""

from __future__ import annotations

from nethealth.probes.base import Probe, ProbeResult
from nethealth.probes.dns import DnsProbe
from nethealth.probes.http import HttpProbe
from nethealth.probes.icmp import IcmpProbe
from nethealth.probes.tcp import TcpProbe
from nethealth.probes.tls import TlsProbe


def default_probes() -> dict[str, Probe]:
    return {
        "icmp": IcmpProbe(),
        "tcp": TcpProbe(),
        "dns": DnsProbe(),
        "http": HttpProbe(),
        "tls": TlsProbe(),
    }


__all__ = ["Probe", "ProbeResult", "default_probes"]
