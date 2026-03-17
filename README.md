<p align="center">
  <img src="logo.png" width="200" alt="uclaw logo">
</p>

<h1 align="center">uclaw</h1>

<p align="center">Turn your Claude Code into OpenClaw.</p>

uclaw is an ultra-lightweight daemon that bridges Telegram and Feishu to Claude Code via the official [Python Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).
Claude Code is the brain — uclaw just routes messages and schedules tasks.

## How It Works

```
You (Telegram / Feishu)          Cron Scheduler
  |  Bot API                      |  (heartbeat, reminders, ...)
  v                               v
┌───────────────────────────────────────┐
│         uclaw daemon (asyncio)        │
│                                       │
│  [inbound queue]  →  Session Router   │
│  [outbound queue] ←  Claude Invoker   │
└───────────────────────────────────────┘
                    |
                    |  Claude Agent SDK
                    v
          Claude Code CLI
          reads/writes your codebase
                    |
                    v
          Response streamed back
          to Telegram / Feishu
```

Send a message from your phone or let cron trigger a task — Claude Code executes it on your server with full tool access (Read, Edit, Bash, etc.), skills, CLAUDE.md, and memory.
Responses stream back to your chat.

## Comparison

| | [uclaw](https://github.com/x-zheng16/uclaw) | [nanobot](https://github.com/HKUDS/nanobot) | [Claude-to-IM-skill](https://github.com/op7418/Claude-to-IM-skill) |
|---|---|---|---|
| **Agent brain** | Claude Code CLI | Own agent loop | Claude Code CLI |
| **Language** | Python | Python | TypeScript |
| **Source LOC** | ~1,000 | ~15,000 | ~500 + upstream lib |
| **Channels** | 2 (Telegram, Feishu) | 11 | 4 |
| **Cron scheduler** | Built-in | Built-in | No |
| **Heartbeat** | Cron job | Separate service | No |
| **Session resume** | Yes | Own session files | Yes |
| **Tool access** | Full Claude Code toolset | Own tool system | Full Claude Code toolset |
| **Skills/CLAUDE.md** | Yes (native) | No | Yes (native) |
| **Auto-compact** | Yes (Claude Code built-in) | No | Yes (Claude Code built-in) |
| **Dependencies** | claude-agent-sdk + 3 libs | 15+ libs | claude-to-im npm + SDK |

uclaw was designed after studying both nanobot and Claude-to-IM-skill.
We adopted nanobot's async message bus and single-timer cron scheduler patterns, and Claude-to-IM-skill's approach of delegating all intelligence to Claude Code.

## Features

- **Telegram + Feishu** — long polling and WebSocket, no public IP needed
- **Persistent sessions** — multi-turn conversations with full history, auto-compacted
- **Cron scheduler** — recurring tasks, one-shot reminders, cron expressions with timezone
- **Heartbeat** — periodic check of HEARTBEAT.md (implemented as a cron job)
- **Hot-reload** — edit jobs.json externally, daemon picks it up within 60 seconds
- **Session resume** — conversations survive daemon restarts
- **Auto-approve** — no permission prompts, designed for personal servers

## Prerequisites

- Python >= 3.11 with [uv](https://github.com/astral-sh/uv)
- [Claude Code CLI](https://claude.ai/install.sh) installed and authenticated
- Node.js >= 20 (required by Claude Code CLI)
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- (Optional) Feishu app with Bot capability and WebSocket event mode

## Quick Start

```bash
# Clone
git clone https://github.com/x-zheng16/uclaw.git
cd uclaw

# Install
uv sync

# Configure
mkdir -p ~/.uclaw
cp config.example.json ~/.uclaw/config.json
# Edit ~/.uclaw/config.json with your bot token and user ID

# Run
uv run python -m uclaw
```

Then send a message to your bot on Telegram.

## Configuration

`~/.uclaw/config.json`:

```json
{
  "telegram": {
    "enabled": true,
    "token": "YOUR_BOT_TOKEN",
    "allowed_users": ["YOUR_USER_ID"]
  },
  "feishu": {
    "enabled": false,
    "app_id": "",
    "app_secret": "",
    "allowed_users": []
  },
  "claude": {
    "workspace": "~/workspace",
    "permission_mode": "bypassPermissions",
    "cli_path": null
  }
}
```

Set `cli_path` if the `claude` binary isn't on PATH (e.g., `"/home/user/.local/bin/claude"`).

## Cron Jobs

`~/.uclaw/cron/jobs.json`:

```json
{
  "jobs": [
    {
      "id": "heartbt",
      "name": "Heartbeat",
      "schedule": {"kind": "every", "everyMs": 1800000},
      "message": "Read ~/workspace/HEARTBEAT.md and act on any active tasks.",
      "channel": "telegram",
      "chatId": "YOUR_USER_ID",
      "enabled": true,
      "deleteAfterRun": false
    }
  ]
}
```

Schedule types:
- `{"kind": "every", "everyMs": 1800000}` — every 30 minutes
- `{"kind": "at", "atMs": 1710000000000}` — one-shot at epoch timestamp
- `{"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai"}` — cron expression

Claude Code can also create cron jobs by editing `jobs.json` directly — the daemon hot-reloads changes.

## Deploy as systemd Service

```bash
bash deploy/install.sh
sudo systemctl start uclaw
journalctl -u uclaw -f
```

## Process Management

```bash
# Using the CLI
uv run python -m uclaw              # foreground
uclaw start                         # background daemon
uclaw stop                          # graceful shutdown
uclaw restart                       # stop + start
uclaw status                        # check if running
```

## Architecture

```
uclaw/
├── __main__.py      # entry point, wiring, signal handlers
├── bus.py           # two asyncio.Queue message bus
├── config.py        # JSON config loader
├── router.py        # session router + ClaudeSDKClient lifecycle
├── cli.py           # start/stop/restart process management
├── channels/
│   ├── base.py      # BaseChannel ABC
│   ├── manager.py   # channel orchestration + outbound dispatch
│   ├── telegram.py  # Telegram long polling adapter
│   └── feishu.py    # Feishu WebSocket adapter
└── cron/
    ├── types.py     # CronJob, CronSchedule, CronStore
    └── service.py   # single-timer cron scheduler
```

## Acknowledgments

uclaw was built after deep-diving into two projects that pioneered IM-to-AI-agent bridging:

- [nanobot](https://github.com/HKUDS/nanobot) by HKUDS — the ultra-lightweight OpenClaw that inspired our async message bus, single-timer cron scheduler, and channel adapter patterns.
- [Claude-to-IM-skill](https://github.com/op7418/Claude-to-IM-skill) by op7418 — the Claude Code skill that proved delegating intelligence to Claude Code CLI is the right architecture.

## License

MIT
