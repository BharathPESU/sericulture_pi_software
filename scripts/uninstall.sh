#!/usr/bin/env bash
# Sericulture Edge-AI Pi 5 Software Uninstall Script
# Run with: sudo ./scripts/uninstall.sh

set -euo pipefail

echo "=== Uninstalling Sericulture Edge-AI Software ==="

if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run as root (sudo ./scripts/uninstall.sh)" >&2
  exit 1
fi

echo "Stopping and disabling service..."
systemctl stop sericulture.service || true
systemctl disable sericulture.service || true

rm -f /etc/systemd/system/sericulture.service
systemctl daemon-reload

echo "Removing application binaries at /opt/sericulture..."
rm -rf /opt/sericulture

echo "Note: SQLite database in /var/lib/sericulture and configuration in /etc/sericulture were preserved."
echo "To completely erase data: rm -rf /var/lib/sericulture /etc/sericulture"

echo "=== Uninstall Complete ==="
