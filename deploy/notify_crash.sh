#!/usr/bin/env bash
# Sends a Telegram alert when the uclaw service crashes.
# Invoked by systemd ExecStopPost.
#
# SERVICE_RESULT values:
#   success      - clean systemctl stop/restart   -> no alert
#   timeout      - process took too long to stop  -> no alert (normal for multi-session shutdown)
#   exit-code    - Python raised unhandled exception -> alert
#   signal       - killed by OOM or external signal -> alert
#   core-dump    - segfault etc.                  -> alert

set -euo pipefail

CONFIG="${HOME}/.uclaw/config.json"
[[ -f "$CONFIG" ]] || exit 0

RESULT="${SERVICE_RESULT:-unknown}"

# Only alert on real crashes
case "$RESULT" in
    success|timeout) exit 0 ;;
    exit-code|signal|core-dump|watchdog|start-limit) ;;
    *) exit 0 ;;
esac

TOKEN=$(python3 -c "
import json
d = json.load(open('$CONFIG'))
print(d.get('telegram', {}).get('token', ''))
" 2>/dev/null) || exit 0

CHAT_IDS=$(python3 -c "
import json
d = json.load(open('$CONFIG'))
print(' '.join(d.get('telegram', {}).get('allowed_users', [])))
" 2>/dev/null) || exit 0

[[ -z "$TOKEN" || -z "$CHAT_IDS" ]] && exit 0

MSG="[uclaw] service crashed (result: ${RESULT}). Systemd restarting in 5s."

for CHAT_ID in $CHAT_IDS; do
    curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}&text=${MSG}" > /dev/null 2>&1 || true
done
