"""
Interactive wizard for generic service deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

from deploy_wizard.config import (
    AccessMode,
    Config,
    SourceKind,
    detect_source_kind,
    find_compose_file,
    list_compose_services,
)


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


def _choose_access_mode() -> AccessMode:
    options = [
        (AccessMode.LOCALHOST.value, "Bind to loopback only"),
        (AccessMode.TAILSCALE.value, "Bind to Tailscale interface IP"),
        (AccessMode.PUBLIC.value, "Bind to all interfaces (0.0.0.0)"),
    ]
    idx = _choose(options, default=1)
    return AccessMode(options[idx - 1][0])


def _choose_services(services: List[str]) -> Optional[Tuple[str, ...]]:
    print("Compose services found:")
    for idx, name in enumerate(services, 1):
        print(f"  [{idx}] {name}")
    print("Enter comma-separated numbers or names, or press Enter to deploy all services.")

    while True:
        raw = _prompt("Services", "")
        if not raw:
            return None

        chosen: List[str] = []
        tokens = [token.strip() for token in raw.split(",") if token.strip()]
        for token in tokens:
            if token.isdigit():
                idx = int(token)
                if not (1 <= idx <= len(services)):
                    chosen = []
                    break
                name = services[idx - 1]
            else:
                if token not in services:
                    chosen = []
                    break
                name = token
            if name not in chosen:
                chosen.append(name)

        if chosen:
            return tuple(chosen)
        print("Invalid selection. Use listed numbers or exact service names.")


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
    access_mode = AccessMode.LOCALHOST
    compose_services: Optional[Tuple[str, ...]] = None
    discovered_services: List[str] = []
    domain: Optional[str] = None
    certbot_email: Optional[str] = None
    auth_token: Optional[str] = None
    proxy_upstream_service: Optional[str] = None
    proxy_upstream_port: Optional[int] = None

    print("Access mode:")
    access_mode = _choose_access_mode()

    if source_kind == SourceKind.COMPOSE:
        compose_path = find_compose_file(source_dir)
        if compose_path is not None:
            discovered_services = list_compose_services(compose_path)
            if discovered_services:
                compose_services = _choose_services(discovered_services)

    if source_kind == SourceKind.DOCKERFILE:
        if _confirm("Expose a host port for this service?", default=False):
            container_port = _prompt_int("Container port", 8080)
            host_port = _prompt_int("Host port", container_port)
            if access_mode == AccessMode.PUBLIC:
                bind_host = _prompt("Bind host", "0.0.0.0")
            elif access_mode == AccessMode.TAILSCALE:
                bind_host = _prompt("Bind host (Tailscale IP, optional)", "127.0.0.1")
            else:
                bind_host = _prompt("Bind host", "127.0.0.1")

    if _confirm("Enable nginx reverse proxy with Let's Encrypt?", default=False):
        domain = _prompt("Public domain", "api.example.com").lower()
        certbot_email = _prompt("Let's Encrypt email", "ops@example.com").lower()
        access_mode = AccessMode.PUBLIC

    if _confirm("Require bearer token authentication at proxy?", default=False):
        auth_token = _prompt("Bearer token", "") or None

    if (
        source_kind == SourceKind.COMPOSE
        and access_mode != AccessMode.LOCALHOST
        and domain is None
        and auth_token is None
    ):
        print("Compose source with non-local access requires managed proxy mode.")
        if _confirm("Enable bearer token authentication now?", default=True):
            auth_token = _prompt("Bearer token", "") or None
        else:
            access_mode = AccessMode.LOCALHOST

    proxy_enabled = domain is not None or auth_token is not None
    if proxy_enabled:
        if source_kind == SourceKind.COMPOSE:
            default_service = ""
            if compose_services:
                default_service = compose_services[0]
            elif discovered_services:
                default_service = discovered_services[0]
            if default_service:
                proxy_upstream_service = _prompt("Upstream compose service", default_service)
            proxy_upstream_port = _prompt_int("Upstream container port", 80)
        else:
            if container_port is not None:
                proxy_upstream_port = container_port
            else:
                proxy_upstream_port = _prompt_int("Application container port for proxy", 8080)

    cfg = Config(
        service_name=service_name,
        source_dir=source_dir,
        source_kind=source_kind,
        base_dir=base_dir,
        host_port=host_port,
        container_port=container_port,
        bind_host=bind_host,
        access_mode=access_mode,
        compose_services=compose_services,
        domain=domain,
        certbot_email=certbot_email,
        auth_token=auth_token,
        proxy_upstream_service=proxy_upstream_service,
        proxy_upstream_port=proxy_upstream_port,
    )

    print()
    print("Review")
    print(f"  Service name : {cfg.service_name}")
    print(f"  Source dir   : {cfg.source_dir}")
    print(f"  Source kind  : {cfg.source_kind.value}")
    print(f"  Base dir     : {cfg.base_dir}")
    print(f"  Access mode  : {cfg.access_mode.value}")
    if cfg.host_port is not None:
        print(
            f"  Port mapping : "
            f"{cfg.effective_bind_host}:{cfg.host_port}->{cfg.container_port}"
        )
    else:
        print("  Port mapping : none")
    if cfg.source_kind == SourceKind.COMPOSE and cfg.compose_services:
        print(f"  Compose svcs : {', '.join(cfg.compose_services)}")
    elif cfg.source_kind == SourceKind.COMPOSE:
        print("  Compose svcs : all")
    if cfg.tls_enabled:
        print(f"  Domain       : {cfg.domain}")
        print(f"  TLS email    : {cfg.certbot_email}")
        print(
            f"  Proxy target : "
            f"{cfg.effective_proxy_upstream_service}:{cfg.effective_proxy_upstream_port}"
        )
    elif cfg.reverse_proxy_enabled:
        print(
            f"  Proxy target : "
            f"{cfg.effective_proxy_upstream_service}:{cfg.effective_proxy_upstream_port}"
        )
    if cfg.auth_token is not None:
        print("  Auth token   : enabled")
    else:
        print("  Auth token   : disabled")
    print()

    if not _confirm("Proceed with deployment?", default=True):
        print("Aborted.")
        sys.exit(0)

    return cfg
