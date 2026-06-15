"""Notification channels for fable_watch.

Every channel is independent and degrades gracefully: if it is enabled but not
fully configured (or the platform can't support it), it logs one clear reason
and is skipped. One failing channel never breaks the watcher or the others.

Channels:
  * terminal  -- ASCII banner + terminal bell (printed to stdout/the log)
  * desktop   -- native OS notification: Windows/WSL toast, macOS osascript,
                 or Linux notify-send (auto-detected)
  * email     -- SMTP (e.g. Gmail with an app-password)
  * ntfy      -- HTTP push to your phone via ntfy.sh
"""
from __future__ import annotations

import base64
import shutil
import smtplib
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

BELL = "\a"


@dataclass
class Notification:
    title: str
    message: str
    url: str = ""


def build_banner(note: "Notification") -> str:
    rule = "=" * 64
    body = "\n".join("  " + line for line in note.message.splitlines())
    parts = ["", rule, f"  *** {note.title} ***", rule, body]
    if note.url:
        parts.append(f"  {note.url}")
    parts += [rule, ""]
    return "\n".join(parts)


# --- desktop backend detection -----------------------------------------------

def _powershell_exe() -> str | None:
    # Present on WSL (Windows interop) and native Windows.
    return shutil.which("powershell.exe") or shutil.which("powershell")


def desktop_backend() -> tuple[str | None, str]:
    """Pick a desktop-notification backend for this OS.

    Returns (backend, reason). backend is one of 'windows' | 'macos' | 'linux',
    or None with a human-readable reason when nothing usable is available.
    PowerShell is checked first so WSL shows real Windows toasts.
    """
    if _powershell_exe():
        return ("windows", "")
    if sys.platform == "darwin":
        if shutil.which("osascript"):
            return ("macos", "")
        return (None, "osascript not found")
    if sys.platform.startswith("linux"):
        if shutil.which("notify-send"):
            return ("linux", "")
        return (None, "notify-send not found (apt install libnotify-bin)")
    return (None, f"no desktop backend for platform {sys.platform!r}")


# --- channel readiness -------------------------------------------------------

def channel_status(config: dict) -> dict:
    """Return {name: (enabled, active, reason)} for every channel.

    enabled = turned on in config; active = enabled AND usable right now.
    """
    n = config["notify"]
    status: dict[str, tuple[bool, bool, str]] = {}

    term = n["terminal"]["enabled"]
    status["terminal"] = (term, term, "")

    dt = n["desktop"]["enabled"]
    backend, reason = desktop_backend()
    status["desktop"] = (dt, dt and backend is not None, reason)

    e = n["email"]
    missing = [k for k in ("username", "password", "to_addr") if not e.get(k)]
    status["email"] = (
        e["enabled"], e["enabled"] and not missing,
        f"missing {', '.join(missing)}" if missing else "",
    )

    f = n["ntfy"]
    status["ntfy"] = (
        f["enabled"], f["enabled"] and bool(f.get("topic")),
        "" if f.get("topic") else "missing topic",
    )
    return status


# --- senders -----------------------------------------------------------------

def _send_terminal(cfg: dict, note: "Notification", logger) -> None:
    # Bell + banner. For the daemon, stdout is redirected into the log file.
    print(BELL + build_banner(note), flush=True)


def _toast_script(app_id: str, title: str, message: str) -> str:
    t = title.replace("'", "''")
    m = message.replace("'", "''")
    a = app_id.replace("'", "''")
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null\n"
        "$tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
        "$texts = $tpl.GetElementsByTagName('text')\n"
        f"$texts.Item(0).AppendChild($tpl.CreateTextNode('{t}')) | Out-Null\n"
        f"$texts.Item(1).AppendChild($tpl.CreateTextNode('{m}')) | Out-Null\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)\n"
        f"$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{a}')\n"
        "$notifier.Show($toast)\n"
    )


def _toast_windows(app_id: str, title: str, message: str) -> None:
    exe = _powershell_exe()
    if not exe:
        raise RuntimeError("powershell executable not found")
    script = _toast_script(app_id, title, message)
    # -EncodedCommand takes base64 of UTF-16LE; avoids all shell quoting issues.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, timeout=30, check=True,
    )


def _notify_macos(title: str, message: str) -> None:
    # Notifications render on one line; collapse newlines and escape for AppleScript.
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20, check=True)


def _notify_linux(title: str, message: str) -> None:
    # Args are passed as argv (no shell), so no escaping needed.
    subprocess.run(
        ["notify-send", "-u", "critical", title, message],
        capture_output=True, timeout=20, check=True,
    )


def _send_desktop(cfg: dict, note: "Notification", logger) -> None:
    backend, reason = desktop_backend()
    if backend == "windows":
        _toast_windows(cfg.get("app_id", "Fable Watch"), note.title, note.message)
    elif backend == "macos":
        _notify_macos(note.title, note.message)
    elif backend == "linux":
        _notify_linux(note.title, note.message)
    else:
        raise RuntimeError(reason or "no desktop backend available")


def _send_email(cfg: dict, note: "Notification", logger) -> None:
    msg = EmailMessage()
    msg["Subject"] = cfg.get("subject") or note.title
    msg["From"] = cfg.get("from_addr") or cfg["username"]
    msg["To"] = cfg["to_addr"]
    body = note.message + (f"\n\n{note.url}" if note.url else "")
    msg.set_content(body)
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as server:
        server.starttls()
        server.login(cfg["username"], cfg["password"])
        server.send_message(msg)


def _send_ntfy(cfg: dict, note: "Notification", logger) -> None:
    url = f"{cfg['server'].rstrip('/')}/{cfg['topic']}"
    request = urllib.request.Request(url, data=note.message.encode("utf-8"), method="POST")
    request.add_header("Title", note.title)
    request.add_header("Priority", str(cfg.get("priority", "default")))
    if cfg.get("tags"):
        request.add_header("Tags", cfg["tags"])
    if note.url:
        request.add_header("Click", note.url)
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


# section in config["notify"], sender function
_SENDERS = {
    "terminal": ("terminal", _send_terminal),
    "desktop": ("desktop", _send_desktop),
    "email": ("email", _send_email),
    "ntfy": ("ntfy", _send_ntfy),
}


def dispatch(config: dict, note: "Notification", logger) -> dict:
    """Fire every active channel. Returns {name: bool_success}."""
    status = channel_status(config)
    results: dict[str, bool] = {}
    for name, (section, sender) in _SENDERS.items():
        enabled, active, reason = status[name]
        if not enabled:
            continue
        if not active:
            logger.warning("notify[%s] skipped: %s", name, reason)
            results[name] = False
            continue
        try:
            sender(config["notify"][section], note, logger)
            logger.info("notify[%s] sent", name)
            results[name] = True
        except Exception as exc:  # never let one channel break the rest
            logger.error("notify[%s] failed: %s", name, exc)
            results[name] = False
    return results
