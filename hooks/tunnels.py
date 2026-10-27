"""Local ports held by tunnels: traffic sent to them leaves this machine.

A pilot run (2026-10-22) sent simulator traffic to 127.0.0.1:8090, which an SSH tunnel to a
production server held; Docker's own mapping of 8090 lost to it. The guard cannot see this (it
reads commands, and this was a default port), so the run is told which ports are taken.
"""
from __future__ import annotations

import subprocess

# lsof truncates command names to 9 characters.
FORWARDERS = ("ssh", "autossh", "kubectl", "cloudflar", "socat")
# Docker VMs (colima, lima) publish container ports over ssh; those stay on this machine.
LOCAL_VM = ("/.colima/", "/_lima/", "/.lima/")


def _listeners(run) -> dict[int, list[int]]:
    """pid -> listening TCP ports, for forwarding programs only."""
    out = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn"],
              capture_output=True, text=True, timeout=5).stdout
    found: dict[int, list[int]] = {}
    pid, keep = 0, False
    for line in out.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            pid = int(value)
        elif tag == "c":
            keep = value.startswith(FORWARDERS)
        elif tag == "n" and keep:
            port = value.rsplit(":", 1)[-1]
            if port.isdigit() and int(port) not in found.setdefault(pid, []):
                found[pid].append(int(port))
    return found


def held_ports(run=subprocess.run) -> list[tuple[int, str]]:
    """(port, command line) for each local port a tunnel listens on. Empty when it cannot tell."""
    try:
        listeners = _listeners(run)
        held = []
        for pid, ports in listeners.items():
            command = run(["ps", "-o", "command=", "-p", str(pid)],
                          capture_output=True, text=True, timeout=5).stdout.strip()
            if not command or any(m in command for m in LOCAL_VM):
                continue
            held += [(port, command[:80]) for port in ports]
        return sorted(held)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def warning(run=subprocess.run) -> str:
    held = held_ports(run)
    if not held:
        return ""
    ports = ", ".join(f"{port} ({command})" for port, command in held)
    return (f"local ports held by tunnels: {ports}. Traffic sent to them leaves this machine: "
            "give test services and their clients ports nothing holds")
