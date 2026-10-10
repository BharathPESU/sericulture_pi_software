#!/usr/bin/env bash
# Sericulture Edge-AI Pi 5 Software Installation Script
# Run with: sudo ./scripts/install.sh

set -euo pipefail

echo "=== Installing Sericulture Edge-AI Software ==="

if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run as root (sudo ./scripts/install.sh)" >&2
  exit 1
fi

SERVICE_USER="sericulture"
INSTALL_DIR="/opt/sericulture"
DATA_DIR="/var/lib/sericulture"
CONFIG_DIR="/etc/sericulture"

# 1. Create system user and groups
echo "[1/6] Creating service user and checking hardware groups..."
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" -M "$SERVICE_USER"
  echo "Created user $SERVICE_USER."
fi

# Ensure user is in gpio, i2c, video groups
for grp in gpio i2c video dialout; do
  if getent group "$grp" &>/dev/null; then
    usermod -aG "$grp" "$SERVICE_USER"
  fi
done

# 2. Create directories with restrictive permissions
echo "[2/6] Setting up directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$CONFIG_DIR/certs"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$DATA_DIR" "$CONFIG_DIR"
chmod 750 "$INSTALL_DIR"
chmod 750 "$DATA_DIR"
chmod 700 "$CONFIG_DIR/certs"

# 3. Create Python Virtual Environment
echo "[3/6] Setting up Python virtual environment..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
  python3 -m venv "$INSTALL_DIR/venv"
fi

echo "Installing package requirements..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
if [ -f "pyproject.toml" ]; then
  "$INSTALL_DIR/venv/bin/pip" install -e .
fi

# 4. Copy application source
echo "[4/6] Copying application files..."
cp -r app/sericulture "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# 5. Setup default config if not already present
echo "[5/6] Deploying configuration..."
if [ ! -f "$CONFIG_DIR/device.yaml" ]; then
  cp config/device.example.yaml "$CONFIG_DIR/device.yaml"
  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/device.yaml"
  chmod 600 "$CONFIG_DIR/device.yaml"
  echo "Copied example config to $CONFIG_DIR/device.yaml. Please edit with real credentials!"
fi

# 6. Install systemd service
echo "[6/6] Installing systemd service..."
cp systemd/sericulture.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable sericulture.service

echo "=== Installation Complete ==="
echo "To start service: sudo systemctl start sericulture"
echo "To check logs:    sudo journalctl -u sericulture -f"
