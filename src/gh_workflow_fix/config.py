"""Configuratie uit omgevingsvariabelen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MARKER = "# self-heal: true"
DEFAULT_DELAYS = (15, 30, 60)


@dataclass(frozen=True)
class Config:
    gh_token: str
    gh_repo: str
    gh_oc_auto: bool
    webhook_secret: str
    self_heal_marker: str = DEFAULT_MARKER
    retry_delays_min: tuple[int, ...] = DEFAULT_DELAYS
    data_dir: Path = field(default_factory=lambda: Path("/var/lib/gh-workflow-fix"))
    opencode_bin: Path = field(default_factory=lambda: Path("/root/.opencode/bin/opencode"))
    gh_api_base: str = "https://api.github.com"
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 18080
    fix_timeout_s: int = 1800
    scheduler_tick_s: int = 60

    def delay_before(self, attempt: int) -> int:
        """Wachttijd in minuten vóór poging `attempt` (1-based)."""
        return self.retry_delays_min[attempt - 1]


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _delays(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split(",") if p.strip())


def load_config(env=None) -> Config:
    env = env if env is not None else os.environ

    def req(name: str) -> str:
        value = env.get(name)
        if not value:
            raise ValueError(f"Ontbrekende env-var {name}")
        return value

    delays = DEFAULT_DELAYS
    if "RETRY_DELAYS_MIN" in env and env["RETRY_DELAYS_MIN"].strip():
        delays = _delays(env["RETRY_DELAYS_MIN"])
        if len(delays) < 1:
            raise ValueError("RETRY_DELAYS_MIN moet minimaal 1 waarde bevatten")

    return Config(
        gh_token=req("GH_TOKEN"),
        gh_repo=req("GH_REPO"),
        gh_oc_auto=_bool(req("GH_OC_AUTO")),
        webhook_secret=req("WEBHOOK_SECRET"),
        self_heal_marker=env.get("SELF_HEAL_MARKER", DEFAULT_MARKER),
        retry_delays_min=delays,
        data_dir=Path(env.get("DATA_DIR", "/var/lib/gh-workflow-fix")),
        opencode_bin=Path(env.get("OPENCODE_BIN", "/root/.opencode/bin/opencode")),
        gh_api_base=env.get("GH_API_BASE", "https://api.github.com"),
        webhook_host=env.get("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=int(env.get("WEBHOOK_PORT", "18080")),
        fix_timeout_s=int(env.get("FIX_TIMEOUT_S", "1800")),
        scheduler_tick_s=int(env.get("SCHEDULER_TICK_S", "60")),
    )
