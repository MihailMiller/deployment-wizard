"""
Idempotent OS-level setup for generic deployments.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, List

from deploy_wizard.log import die, log_line, sh

_FALLBACK_DNS = ("1.1.1.1", "8.8.8.8")


def _is_loopback_dns(value: str) -> bool:
    return value.startswith("127.") or value in ("::1", "0.0.0.0")


def _normalize_dns_entries(raw: Any) -> List[str]:
    if raw is None:
        values: List[Any] = []
    elif isinstance(raw, list):
        values = raw
    else:
        values = [raw]

    out: List[str] = []
    for item in values:
        value = str(item).strip()
        if not value or _is_loopback_dns(value):
            continue
        if value not in out:
            out.append(value)
    return out


def _merged_dns(raw: Any) -> List[str]:
    merged = _normalize_dns_entries(raw)
    for fallback in _FALLBACK_DNS:
        if fallback not in merged:
            merged.append(fallback)
    return merged


def require_root_reexec() -> None:
    if os.geteuid() == 0:
        return
    if shutil.which("sudo") is None:
        die("Must run as root (sudo not found).")
    import sys

    print("[INFO] Re-executing via sudo...", flush=True)
    os.execvp("sudo", ["sudo", sys.executable, *sys.argv])


def detect_ubuntu() -> None:
    osr = Path("/etc/os-release")
    if not osr.exists():
        die("/etc/os-release not found.")
    txt = osr.read_text(encoding="utf-8", errors="ignore").lower()
    if "ubuntu" not in txt:
        die("This deploy wizard currently supports Ubuntu hosts.")


def ensure_base_packages() -> None:
    sh("export DEBIAN_FRONTEND=noninteractive; apt-get update -y")
    sh(
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get install -y ca-certificates curl gnupg"
    )


def ensure_docker() -> None:
    if shutil.which("docker"):
        rc = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=False,
        ).returncode
        if rc == 0:
            return
    sh("curl -fsSL https://get.docker.com | bash")


def ensure_docker_daemon_tuning() -> None:
    """
    Tune Docker daemon for unstable registry connections.

    - Limit concurrent downloads/uploads to reduce connection resets.
    - Ensure safe, non-loopback DNS resolvers are configured.
    """
    daemon_path = Path("/etc/docker/daemon.json")
    current: dict = {}
    if daemon_path.exists():
        try:
            current = json.loads(daemon_path.read_text(encoding="utf-8"))
        except Exception:
            # Preserve operability: start from empty if daemon.json is unreadable.
            current = {}

    merged = dict(current)
    merged["max-concurrent-downloads"] = 1
    merged["max-concurrent-uploads"] = 1
    merged["dns"] = _merged_dns(merged.get("dns"))

    if merged == current:
        return

    daemon_path.parent.mkdir(parents=True, exist_ok=True)
    if daemon_path.exists():
        backup = daemon_path.with_suffix(".json.bak")
        backup.write_text(daemon_path.read_text(encoding="utf-8"), encoding="utf-8")
        log_line(f"[DOCKER] Backed up daemon config: {backup}")
    daemon_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    log_line("[DOCKER] Updated daemon.json with registry retry hardening.")
    sh("systemctl restart docker")
