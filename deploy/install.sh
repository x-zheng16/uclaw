#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing uclaw systemd service..."
sudo cp "$SCRIPT_DIR/uclaw.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable uclaw
echo "Done."
echo "  Start:  sudo systemctl start uclaw"
echo "  Logs:   journalctl -u uclaw -f"
echo "  Status: systemctl status uclaw"
