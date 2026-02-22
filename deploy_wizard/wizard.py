"""
Interactive wizard for generic service deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

from deploy_wizard.config import Config, SourceKind, detect_source_kind, find_compose_file


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value if value else default


def _prompt_int(msg: str, default: int, min_val: int = 1, max_val: int = 65535) -> int:
    while True:
        raw = _prompt(msg, str(default))
        try:
            value = int(raw)
            if min_val <= value <= max_val:
                return value
        except ValueError:
            pass
        print(f"Please enter a number between {min_val} and {max_val}.")


def _confirm(msg: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = _prompt(f"{msg} ({hint})", "").lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _choose(options: List[Tuple[str, str]], default: int = 1) -> int:
    for idx, (name, desc) in enumerate(options, 1):
        print(f"  [{idx}] {name} - {desc}")
    while True:
        raw = _prompt("Choice", str(default))
        try:
            value = int(raw)
            if 1 <= value <= len(options):
                return value
        except ValueError:
            pass
        print(f"Please enter a number between 1 and {len(options)}.")


def _pick_source_dir() -> Tuple[Path, SourceKind]:
    while True:
        raw = _prompt("Source directory", str(Path.cwd()))
        source_dir = Path(raw).expanduser()
        if not source_dir.exists() or not source_dir.is_dir():
            print("Directory does not exist.")
            continue

        compose_path = find_compose_file(source_dir)
        has_dockerfile = (source_dir / "Dockerfile").exists()
        if compose_path and has_dockerfile:
            print("Both docker-compose and Dockerfile found.")
            idx = _choose(
                [
                    ("Use compose file", f"{compose_path.name}"),
                    ("Use Dockerfile", "Generate managed compose under service name"),
                ],
                default=1,
            )
            return source_dir, (SourceKind.COMPOSE if idx == 1 else SourceKind.DOCKERFILE)
        if compose_path:
            return source_dir, SourceKind.COMPOSE
        if has_dockerfile:
            return source_dir, SourceKind.DOCKERFILE

        print("No docker-compose.yml/compose.yml or Dockerfile found in this directory.")
        try:
            detect_source_kind(source_dir)
        except ValueError:
            pass


def run_wizard() -> Config:
    print()
    print("Generic Service Deployment Wizard")
    print("Deploy any Docker microservice from a local directory.")
    print()

    service_name = _prompt("Service name", "my-service")
    source_dir, source_kind = _pick_source_dir()
    base_dir = Path(_prompt("Deployment base directory", "/opt/services")).expanduser()

    host_port: Optional[int] = None
    container_port: Optional[int] = None
    bind_host = "127.0.0.1"

    if source_kind == SourceKind.DOCKERFILE:
        if _confirm("Expose a host port for this service?", default=False):
            container_port = _prompt_int("Container port", 8080)
            host_port = _prompt_int("Host port", container_port)
            bind_host = _prompt("Bind host", "127.0.0.1")

    cfg = Config(
        service_name=service_name,
        source_dir=source_dir,
        source_kind=source_kind,
        base_dir=base_dir,
        host_port=host_port,
        container_port=container_port,
        bind_host=bind_host,
    )

    print()
    print("Review")
    print(f"  Service name : {cfg.service_name}")
    print(f"  Source dir   : {cfg.source_dir}")
    print(f"  Source kind  : {cfg.source_kind.value}")
    print(f"  Base dir     : {cfg.base_dir}")
    if cfg.host_port is not None:
        print(f"  Port mapping : {cfg.bind_host}:{cfg.host_port}->{cfg.container_port}")
    else:
        print("  Port mapping : none")
    print()

    if not _confirm("Proceed with deployment?", default=True):
        print("Aborted.")
        sys.exit(0)

    return cfg
