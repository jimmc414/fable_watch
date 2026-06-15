"""Configuration loading for fable_watch.

Layered precedence (lowest -> highest):
  1. built-in DEFAULTS (below)
  2. config.toml          -- gitignored; your local, non-secret toggles
  3. .env / environment   -- gitignored; private targets + secrets

Nothing in this module contains personal data, so the repository is safe to
share. Your email address, SMTP app-password and ntfy topic are read only from
.env / the environment -- never from a committed file.
"""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

# Built-in defaults. config.example.toml mirrors these; keep them in sync.
DEFAULTS = {
    "watch": {
        "model": "claude-fable-5",
        "interval_seconds": 300,
        "probe_prompt": "Reply with exactly: OK",
        "probe_timeout_seconds": 60,
        # A probe is OFFLINE if its reply contains any of these (case-insensitive).
        "offline_sentinels": ["currently unavailable", "fable-mythos-access"],
        "stop_when_online": True,
    },
    "notify": {
        "terminal": {"enabled": True},
        "desktop": {
            "enabled": True,
            # app_id is Windows/WSL only. The PowerShell AppUserModelID makes
            # script-fired toasts reliably appear (shown as "Windows PowerShell").
            # Ignored on macOS (osascript) and Linux (notify-send).
            "app_id": r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe",
        },
        "email": {
            "enabled": False,
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "username": "",
            "password": "",
            "from_addr": "",
            "to_addr": "",
            "subject": "Claude Fable 5 is BACK ONLINE",
        },
        "ntfy": {
            "enabled": False,
            "server": "https://ntfy.sh",
            "topic": "",
            "priority": "urgent",
            "tags": "rocket",
        },
    },
}

# Environment variable -> nested config path. Set (non-empty) values win over
# both the defaults and config.toml. This is where secrets/targets come from.
ENV_OVERRIDES = {
    "FABLE_WATCH_MODEL": ("watch", "model"),
    "FABLE_WATCH_INTERVAL": ("watch", "interval_seconds"),
    "FABLE_WATCH_EMAIL_TO": ("notify", "email", "to_addr"),
    "FABLE_WATCH_EMAIL_FROM": ("notify", "email", "from_addr"),
    "FABLE_WATCH_SMTP_USERNAME": ("notify", "email", "username"),
    "FABLE_WATCH_SMTP_PASSWORD": ("notify", "email", "password"),
    "FABLE_WATCH_SMTP_HOST": ("notify", "email", "smtp_host"),
    "FABLE_WATCH_SMTP_PORT": ("notify", "email", "smtp_port"),
    "FABLE_WATCH_NTFY_TOPIC": ("notify", "ntfy", "topic"),
    "FABLE_WATCH_NTFY_SERVER": ("notify", "ntfy", "server"),
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ (without overriding existing)."""
    path = Path(path) if path else PROJECT_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _coerce(template, raw: str):
    if isinstance(template, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(template, int):
        try:
            return int(raw)
        except ValueError:
            return template
    return raw


def _get_path(cfg: dict, path: tuple):
    node = cfg
    for key in path:
        node = node[key]
    return node


def _set_path(cfg: dict, path: tuple, value) -> None:
    node = cfg
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def load_config(config_path: str | Path | None = None) -> dict:
    """Build the effective config from defaults + config.toml + environment."""
    load_dotenv()
    cfg: dict = copy.deepcopy(DEFAULTS)

    path = Path(
        config_path
        or os.environ.get("FABLE_WATCH_CONFIG")
        or PROJECT_DIR / "config.toml"
    )
    if path.exists():
        with open(path, "rb") as handle:
            cfg = _deep_merge(cfg, tomllib.load(handle))

    for env_var, dest in ENV_OVERRIDES.items():
        raw = os.environ.get(env_var, "")
        if raw != "":
            _set_path(cfg, dest, _coerce(_get_path(DEFAULTS, dest), raw))

    cfg["_config_path"] = str(path)
    cfg["_config_exists"] = path.exists()
    return cfg
