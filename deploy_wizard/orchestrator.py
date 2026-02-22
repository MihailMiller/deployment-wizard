"""
Deployment orchestrator for generic service deployments.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from deploy_wizard.log import LOG_PATH, log_line


class _TqdmStub:
    def __init__(self, total: int = 0, desc: str = "", unit: str = "") -> None:
        self.total = total

    @staticmethod
    def write(msg: str) -> None:
        print(msg, flush=True)

    def update(self, _n: int = 1) -> None:
        return

    def __enter__(self) -> "_TqdmStub":
        return self

    def __exit__(self, *_args) -> bool:
        return False


tqdm = _TqdmStub


def _ensure_tqdm() -> None:
    global tqdm
    try:
        from tqdm import tqdm as real_tqdm

        tqdm = real_tqdm
    except Exception:
        tqdm = _TqdmStub


@dataclass
class Step:
    label: str
    fn: Callable[[], Any]
    skip_if: Optional[Callable[[], bool]] = None
    result: Any = field(default=None, repr=False)


def run_steps(steps: List[Step], bar: Any) -> None:
    import time

    for step in steps:
        if step.skip_if and step.skip_if():
            tqdm.write(f"[SKIP] {step.label}")
            log_line(f"[SKIP] {step.label}")
            bar.update(1)
            continue
        tqdm.write(f"\n[STEP] {step.label}")
        log_line(f"[STEP] {step.label}")
        t0 = time.time()
        step.result = step.fn()
        elapsed = time.time() - t0
        tqdm.write(f"[DONE] {step.label} ({elapsed:.1f}s)")
        log_line(f"[DONE] {step.label} ({elapsed:.1f}s)")
        bar.update(1)


def run_deploy(cfg) -> None:
    from deploy_wizard.service import deploy_service
    from deploy_wizard.system import (
        detect_ubuntu,
        ensure_base_packages,
        ensure_docker,
        require_root_reexec,
    )

    require_root_reexec()
    detect_ubuntu()
    _ensure_tqdm()

    cfg.service_dir.mkdir(parents=True, exist_ok=True)

    log_line(f"=== START {dt.datetime.now(dt.timezone.utc).isoformat()}Z ===")
    tqdm.write(f"[INFO] Logging to: {LOG_PATH}")
    try:
        steps: List[Step] = [
            Step("Install base packages", ensure_base_packages),
            Step("Install/verify Docker", ensure_docker),
            Step("Deploy service", lambda: deploy_service(cfg)),
        ]
        with tqdm(total=len(steps), desc="Deploying service", unit="step") as bar:
            run_steps(steps, bar)
        _print_summary(cfg)
    finally:
        log_line(f"=== END {dt.datetime.now(dt.timezone.utc).isoformat()}Z ===")


def _print_summary(cfg) -> None:
    print()
    print("+----------------------------------------------------+")
    print("| Deployment complete                                |")
    print("+----------------------------------------------------+")
    print()
    print(f"Service name : {cfg.service_name}")
    print(f"Source dir   : {cfg.source_dir}")
    print(f"Source kind  : {cfg.source_kind.value}")
    print(f"Project dir  : {cfg.service_dir}")
    if cfg.source_kind.value == "dockerfile":
        print(f"Compose file : {cfg.managed_compose_path}")
    else:
        print(f"Compose file : {cfg.source_compose_path}")
    print()
    print("Useful commands:")
    compose_file = cfg.managed_compose_path if cfg.source_kind.value == "dockerfile" else cfg.source_compose_path
    print(
        "  docker compose "
        f"-p {cfg.compose_project_name} "
        f"-f {compose_file} ps"
    )
    print(
        "  docker compose "
        f"-p {cfg.compose_project_name} "
        f"-f {compose_file} logs -f"
    )
