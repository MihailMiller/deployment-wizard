"""
Idempotent OS-level setup for generic deployments.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from deploy_wizard.log import die, log_line, sh


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
    - Set fallback DNS servers when none are configured.
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
    if not merged.get("dns"):
        merged["dns"] = ["1.1.1.1", "8.8.8.8"]

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
