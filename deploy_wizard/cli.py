"""
CLI for generic microservice deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from deploy_wizard.config import AccessMode, Config, SourceKind


def build_config(argv: Optional[List[str]] = None) -> Config:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m deploy_wizard deploy --batch",
        description="Deploy a Docker microservice from a directory with compose or Dockerfile.",
    )
    parser.add_argument("--service-name", required=True, metavar="NAME")
    parser.add_argument("--source-dir", required=True, metavar="DIR")
    parser.add_argument(
        "--source-kind",
        default=SourceKind.AUTO.value,
        choices=[k.value for k in SourceKind],
        metavar="KIND",
        help="Source format: auto, compose, or dockerfile. (default: auto)",
    )
    parser.add_argument(
        "--base-dir",
        default="/opt/services",
        metavar="DIR",
        help="Deployment state directory by service name. (default: /opt/services)",
    )
    parser.add_argument("--host-port", type=int, default=None, metavar="PORT")
    parser.add_argument("--container-port", type=int, default=None, metavar="PORT")
    parser.add_argument(
        "--bind-host",
        default="127.0.0.1",
        metavar="HOST",
        help="Host bind address used with generated compose port mappings.",
    )
    parser.add_argument(
        "--access-mode",
        default=AccessMode.LOCALHOST.value,
        choices=[k.value for k in AccessMode],
        metavar="MODE",
        help="Network exposure profile: localhost, tailscale, or public.",
    )
    parser.add_argument(
        "--registry-retries",
        type=int,
        default=4,
        metavar="N",
        help="Retry attempts for docker compose pull/build/up operations. (default: 4)",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=int,
        default=5,
        metavar="SEC",
        help="Initial retry backoff for registry/network errors. (default: 5)",
    )
    parser.add_argument(
        "--no-docker-daemon-tuning",
        action="store_true",
        help="Skip docker daemon network hardening for flaky registry connections.",
    )
    parser.add_argument(
        "--compose-service",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Compose service name to deploy. Repeat for multiple values. "
            "Default: deploy all services."
        ),
    )
    parser.add_argument(
        "--domain",
        default=None,
        metavar="DOMAIN",
        help="Enable nginx reverse proxy + certbot for this public domain.",
    )
    parser.add_argument(
        "--certbot-email",
        default=None,
        metavar="EMAIL",
        help="Email address used for Let's Encrypt registration.",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        metavar="TOKEN",
        help="Require Authorization: Bearer <token> at the managed nginx proxy.",
    )
    parser.add_argument(
        "--proxy-upstream-service",
        default=None,
        metavar="NAME",
        help=(
            "Upstream compose service for nginx proxy. "
            "Only used for compose sources."
        ),
    )
    parser.add_argument(
        "--proxy-route",
        action="append",
        default=None,
        metavar="HOST=UPSTREAM:PORT",
        help=(
            "Hostname-based proxy route. Repeat for multiple routes, e.g. "
            "--proxy-route wiki.example.com=orchestrator:8090"
        ),
    )
    parser.add_argument(
        "--proxy-upstream-port",
        type=int,
        default=None,
        metavar="PORT",
        help=(
            "Upstream container port for nginx proxy. "
            "Required in proxy mode unless --container-port is set."
        ),
    )
    parser.add_argument(
        "--proxy-http-port",
        type=int,
        default=None,
        metavar="PORT",
        help="External host HTTP port for managed nginx proxy. (default: 80)",
    )
    parser.add_argument(
        "--proxy-https-port",
        type=int,
        default=None,
        metavar="PORT",
        help="External host HTTPS port for managed nginx proxy TLS. (default: 443)",
    )

    raw = parser.parse_args(argv)
    try:
        return Config(
            service_name=raw.service_name,
            source_dir=Path(raw.source_dir).expanduser(),
            source_kind=SourceKind(raw.source_kind),
            base_dir=Path(raw.base_dir).expanduser(),
            host_port=raw.host_port,
            container_port=raw.container_port,
            bind_host=raw.bind_host,
            access_mode=AccessMode(raw.access_mode),
            registry_retries=raw.registry_retries,
            retry_backoff_seconds=raw.retry_backoff_seconds,
            tune_docker_daemon=not raw.no_docker_daemon_tuning,
            compose_services=tuple(raw.compose_service) if raw.compose_service else None,
            domain=raw.domain,
            certbot_email=raw.certbot_email,
            auth_token=raw.auth_token,
            proxy_http_port=raw.proxy_http_port,
            proxy_https_port=raw.proxy_https_port,
            proxy_routes=tuple(raw.proxy_route) if raw.proxy_route else None,
            proxy_upstream_service=raw.proxy_upstream_service,
            proxy_upstream_port=raw.proxy_upstream_port,
        )
    except ValueError as exc:
        parser.error(str(exc))


def dispatch(argv: Optional[List[str]] = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m deploy_wizard",
        description="Generic Docker microservice deployment wizard.",
        add_help=False,
    )
    parser.add_argument("subcommand", nargs="?", default=None, choices=["deploy"])
    parser.add_argument("--help", "-h", action="store_true")

    known, remaining = parser.parse_known_args(argv)
    sub = known.subcommand

    if sub is None:
        if known.help or not sys.stdin.isatty():
            _print_help()
            return
        _run_wizard()
        return

    if sub == "deploy":
        rem = remaining or []
        if known.help or "-h" in rem or "--help" in rem:
            if "--batch" in rem:
                build_config(["--help"])
            else:
                _print_help()
            return
        if "--batch" in rem:
            remaining2 = [r for r in rem if r != "--batch"]
            cfg = build_config(remaining2)
            from deploy_wizard.orchestrator import run_deploy

            run_deploy(cfg)
            return
        if sys.stdin.isatty():
            _run_wizard()
            return
        _print_help()


def _print_help() -> None:
    print(
        "Usage: python -m deploy_wizard [deploy] [options]\n\n"
        "Subcommands:\n"
        "  deploy              Interactive wizard (TTY) or --batch for non-interactive\n"
        "  deploy --batch ...  Non-interactive deployment\n\n"
        "Examples:\n"
        "  sudo python -m deploy_wizard\n"
        "  sudo python -m deploy_wizard deploy --batch "
        "--service-name my-api --source-dir /srv/my-api\n"
    )


def _run_wizard() -> None:
    from deploy_wizard.orchestrator import run_deploy
    from deploy_wizard.wizard import run_wizard

    cfg = run_wizard()
    run_deploy(cfg)
