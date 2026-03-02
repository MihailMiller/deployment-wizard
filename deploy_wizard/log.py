"""
Logging and subprocess helpers for deploy_wizard.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

LOG_PATH = Path("/var/log/deploy_wizard.log")
FALLBACK_LOG_PATH = Path("./deploy_wizard.log")

REDACT_PATTERNS = [
    re.compile(r"(Authorization:\s*Bearer\s+)[^\s\"']+", re.IGNORECASE),
    re.compile(r"(x-api-key:\s*)[^\s\"']+", re.IGNORECASE),
]


def redact(s: str) -> str:
    out = s
    for pat in REDACT_PATTERNS:
        out = pat.sub(r"\1<REDACTED>", out)
    return out


def log_line(s: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(s + "\n")
        return
    except Exception:
        pass
    try:
        with FALLBACK_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(s + "\n")
    except Exception:
        return


def die(msg: str, code: int = 1) -> None:
    try:
        from tqdm import tqdm

        tqdm.write(f"[FATAL] {msg}")
    except Exception:
        print(f"[FATAL] {msg}", file=sys.stderr, flush=True)
    log_line(f"[FATAL] {msg}")
    sys.exit(code)


def sh(
    cmd: str,
    *,
    check: bool = True,
    env: Optional[Dict[str, str]] = None,
) -> int:
    try:
        from tqdm import tqdm as _tqdm

        write = _tqdm.write
    except Exception:
        write = lambda s: print(s, flush=True)  # noqa: E731

    safe_cmd = redact(cmd)
    write(f"\n$ {safe_cmd}")
    log_line(f"\n$ {safe_cmd}")

    popen_kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if os.name == "nt":
        popen_kwargs["args"] = ["powershell", "-NoProfile", "-Command", cmd]
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["args"] = ["bash", "-lc", cmd]
        popen_kwargs["preexec_fn"] = os.setsid

    proc = subprocess.Popen(**popen_kwargs)

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            write(line)
            log_line(redact(line))
    except KeyboardInterrupt:
        write("[WARN] Ctrl-C received. Terminating command...")
        if os.name == "nt":
            try:
                proc.terminate()
            except Exception:
                pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                pass
        raise

    rc = proc.wait()
    if check and rc != 0:
        die(f"Command failed (exit {rc}): {safe_cmd}")
    return rc

