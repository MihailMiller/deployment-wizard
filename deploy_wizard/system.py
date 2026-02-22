"""
Idempotent OS-level setup for generic deployments.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from deploy_wizard.log import die, sh


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
