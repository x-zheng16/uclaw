#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing claude-bridge systemd service..."
sudo cp "$SCRIPT_DIR/claude-bridge.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claude-bridge
echo "Done."
echo "  Start:  sudo systemctl start claude-bridge"
echo "  Logs:   journalctl -u claude-bridge -f"
echo "  Status: systemctl status claude-bridge"
