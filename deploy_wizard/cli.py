"""
CLI for generic microservice deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from deploy_wizard.config import Config, SourceKind


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
