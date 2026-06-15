# fable_watch

**Watch for Claude Fable 5 coming back online — and get a loud alert the instant it returns.**

`fable_watch` quietly probes the suspended model on a schedule and fires every notification channel you enable (terminal, desktop toast, email, phone push) the moment it answers normally again. Cross-platform (Linux / macOS / WSL), uses only the Python standard library, and stays on your Claude Max subscription — no API key, no third-party packages.

---

## Background — why this is needed

In June 2026, Anthropic shipped and then lost its most capable model in the span of three days:

- **June 9, 2026** — Anthropic released [**Claude Fable 5**](https://www.anthropic.com/news/claude-fable-5-mythos-5), the first publicly available, guardrailed sibling of its frontier **Mythos** model. It outperformed Claude Opus 4.8 by more than 10% on some benchmarks, shipped with a 1M-token context window, and was made available across the Claude API, AWS, Microsoft Foundry, and the Claude Code CLI ([TechCrunch](https://techcrunch.com/2026/06/09/anthropics-claude-fable-5-is-a-version-of-mythos-the-public-can-access-today/), [InfoQ](https://www.infoq.com/news/2026/06/claude-5-release/)).

- **June 12, 2026** — A U.S. government **export-control directive** ordered Anthropic to cut off access for any foreign national, citing national-security concerns after a reported jailbreak of the model. Anthropic reviewed the demonstrated technique and judged it minor — comparable to issues in other widely available models — but rather than enforce a citizenship-based restriction it considered unworkable, it [pulled both Fable 5 and Mythos 5 entirely offline, worldwide](https://www.bloomberg.com/news/articles/2026-06-13/anthropic-says-us-limits-foreign-access-to-fable-5-mythos-5). It was the **first time the U.S. government has forced a commercial AI product completely offline**, and Anthropic began [refunding customers](https://www.techtimes.com/articles/318342/20260613/us-government-pulls-anthropics-fable-5-offline-now-come-refunds-vanished-ai.htm) who had paid for a product that vanished overnight.

- **June 15, 2026** — Anthropic flew senior and technical staff to Washington to [meet with White House officials](https://www.axios.com/2026/06/14/anthropic-white-house-mythos-fable) and resolve the dispute. White House AI adviser David Sacks framed the path forward plainly: the hope is that *"Anthropic remediates the safety issue, the export control is lifted, and Fable goes back into general release."*

That last sentence is the entire reason this tool exists. The suspension is widely expected to be **temporary**, but no one has announced *when* it lifts. If Fable is your daily-driver model, you don't want to find out hours later — you want to know the instant it's back so you can switch straight back to it (`/model fable`). `fable_watch` is that early-warning system.

## How it detects "back online"

While the model is suspended, the Claude CLI returns a sentinel message **at exit code `0`** — so the exit code alone can't tell you anything:

```
$ claude --model claude-fable-5 -p "Reply with exactly: OK"
Claude Fable 5 is currently unavailable. Learn more: https://www.anthropic.com/news/fable-mythos-access
```

So `fable_watch` matches on the response **content** instead:

| Result    | Condition                              | Action                       |
| --------- | -------------------------------------- | ---------------------------- |
| `online`  | normal reply, no "unavailable" sentinel | **fire all notifications**   |
| `offline` | sentinel present                        | keep waiting                 |
| `error`   | timeout / CLI missing / empty output    | keep waiting (no false alarm) |

Only an explicit `online` result triggers an alert, so transient network errors and OAuth token refreshes never produce a false positive. Each probe runs `claude --model <model> -p <prompt>` using your **Max subscription** credentials; `ANTHROPIC_API_KEY` is stripped from the probe's environment so billing always stays on the subscription, never on the API.

## Requirements

- **Python 3.11+** (uses the stdlib `tomllib`; **no pip dependencies**)
- **Claude Code CLI ≥ 2.1.170** on `PATH`, logged in to a **Max** subscription (`~/.claude/.credentials.json`). Run `claude` once and authenticate; do **not** set `ANTHROPIC_API_KEY`.
- For **desktop notifications**, an auto-detected backend per platform:
  - **WSL / Windows** → PowerShell (built-in)
  - **macOS** → `osascript` (built-in)
  - **Linux** → `notify-send` (`sudo apt install libnotify-bin`)

The probe, email, ntfy, terminal banner, and the background daemon are all OS-independent. Only the desktop channel is platform-specific, and it degrades gracefully — if no backend is available it simply skips with a logged reason.

## Setup

```bash
git clone https://github.com/jimmc414/fable_watch.git
cd fable_watch
cp config.example.toml config.toml     # local toggles (gitignored)
cp .env.example .env                    # secrets + targets (gitignored)
```

Terminal and desktop alerts work immediately — no configuration needed. Email and phone push are optional and described below.

## How to run

```bash
python fable_watch.py start         # start the background daemon (recommended)
python fable_watch.py status        # daemon state + which channels are active
python fable_watch.py stop          # stop the daemon
python fable_watch.py run           # watch in the foreground (Ctrl-C to stop)
python fable_watch.py once          # single probe; exit 0=online 2=offline 3=error
python fable_watch.py test-notify   # fire a test alert through every active channel
```

The typical flow:

```bash
python fable_watch.py test-notify   # confirm your channels actually reach you
python fable_watch.py start         # detach and watch in the background
python fable_watch.py status        # check on it any time
```

The daemon logs to `fable_watch.log`, tracks progress in `state.json`, and records its PID in `fable_watch.pid`. By default (`stop_when_online = true`) it sends one round of notifications and exits the moment Fable returns — so a single alert means "go use it," not a stream of repeats.

> **Native Windows:** the daemon needs POSIX `fork`, so use `python fable_watch.py run` (foreground) under Task Scheduler instead of `start`. macOS, Linux, and WSL all support `start`.

## Enabling email

Email uses plain SMTP. For Gmail:

1. Turn on **2-Step Verification** for your Google account.
2. Create an **App Password**: Google Account → Security → 2-Step Verification → **App passwords** (or visit <https://myaccount.google.com/apppasswords>). Name it `fable_watch` and copy the 16-character code — this is **not** your normal password.
3. Fill in `.env`:
   ```bash
   FABLE_WATCH_SMTP_USERNAME=you@gmail.com
   FABLE_WATCH_EMAIL_TO=you@gmail.com          # where to send the alert
   FABLE_WATCH_SMTP_PASSWORD=your-16-char-app-password
   ```
4. Make sure `[notify.email]` has `enabled = true` in `config.toml` (it is by default).
5. Verify: `python fable_watch.py test-notify` → you should receive an email.

Other providers work too — set `FABLE_WATCH_SMTP_HOST` / `FABLE_WATCH_SMTP_PORT` in `.env` (defaults are Gmail's `smtp.gmail.com:587`, STARTTLS).

## Enabling phone push (ntfy)

[ntfy](https://ntfy.sh) is a free, no-account push service — perfect for "tell me when X happens."

1. Install the **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)), or use the web app at <https://ntfy.sh/app>.
2. Choose a **long, unguessable topic name** — anyone who knows it can read your alerts, so treat it like a password (e.g. `fable-watch-7h3k9q2x`).
3. In the app, **subscribe** to that exact topic.
4. Put it in `.env`:
   ```bash
   FABLE_WATCH_NTFY_TOPIC=fable-watch-7h3k9q2x
   ```
5. Make sure `[notify.ntfy]` has `enabled = true` in `config.toml` (it is by default).
6. Verify: `python fable_watch.py test-notify` → your phone should buzz.

## Configuration

Non-secret settings live in **`config.toml`**; secrets and personal targets live in **`.env`**. Environment variables override both. Key knobs:

| Setting | File | Default | Meaning |
| --- | --- | --- | --- |
| `watch.model` | config.toml | `claude-fable-5` | model to probe |
| `watch.interval_seconds` | config.toml | `300` | seconds between probes |
| `watch.stop_when_online` | config.toml | `true` | exit after the first success |
| `watch.offline_sentinels` | config.toml | `["currently unavailable", "fable-mythos-access"]` | substrings meaning "still down" |
| `notify.<channel>.enabled` | config.toml | varies | toggle terminal / desktop / email / ntfy |

Run `python fable_watch.py status` any time to see which channels are **ON**, **off**, or **enabled but INACTIVE** — with the reason (e.g. a missing `.env` value).

## Safe to share

This repository contains **no personal data**. `config.toml` and `.env` are gitignored, and the committed `*.example` templates ship with email and ntfy disabled and blank. Your email address, SMTP app-password, and ntfy topic never leave your machine.

## Troubleshooting

- **No desktop notification appears** — *WSL/Windows:* confirm `powershell.exe` is on `PATH` and Focus Assist isn't suppressing it. *macOS:* allow notifications for your terminal app in System Settings → Notifications. *Linux:* ensure a notification daemon is running. `test-notify` prints the failure reason if the call errored.
- **Email rejected** — Gmail requires an **App Password** with 2-Step Verification enabled; your normal password will not authenticate over SMTP.
- **No phone push** — make sure you subscribed to the *exact* topic name in `.env`, and that `[notify.ntfy]` is enabled.
- **WSL background lifetime** — the daemon survives a closed terminal but not a full WSL shutdown. Keep a WSL session alive, or relaunch with `start` after resume.
- **Want faster checks while the situation is live?** — lower `interval_seconds` (e.g. `120`). Each probe is a tiny call on your Max subscription.

## License

[MIT](LICENSE) © 2026 Jim McMillan ([@jimmc414](https://github.com/jimmc414))

---

*Sources: [Anthropic — Claude Fable 5 & Mythos 5](https://www.anthropic.com/news/claude-fable-5-mythos-5) · [Bloomberg](https://www.bloomberg.com/news/articles/2026-06-13/anthropic-says-us-limits-foreign-access-to-fable-5-mythos-5) · [Axios](https://www.axios.com/2026/06/14/anthropic-white-house-mythos-fable) · [InfoQ](https://www.infoq.com/news/2026/06/claude-5-release/) · [TechTimes](https://www.techtimes.com/articles/318342/20260613/us-government-pulls-anthropics-fable-5-offline-now-come-refunds-vanished-ai.htm)*
