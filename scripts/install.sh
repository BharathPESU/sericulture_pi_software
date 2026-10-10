#!/usr/bin/env bash
# ==============================================================================
# Sericulture Edge-AI Pi 5 Software Installation Script
# ==============================================================================
# Usage:
#   sudo ./scripts/install.sh            # Standard production installation
#   sudo ./scripts/install.sh --editable # Development installation with editable venv
# ==============================================================================

set -euo pipefail

# ANSI color codes for terminal output
if [ -t 1 ]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  BLUE='\033[0;34m'
  BOLD='\033[1m'
  NC='\033[0m' # No Color
else
  RED=''
  GREEN=''
  YELLOW=''
  BLUE=''
  BOLD=''
  NC=''
fi

CURRENT_STEP="Initialization"

# Error trap handler for unexpected failures
error_handler() {
  local exit_code=$?
  local line_no=$1

  echo "" >&2
  echo -e "${RED}======================================================================${NC}" >&2
  echo -e "${RED}${BOLD} [INSTALLATION FAILED]${NC}" >&2
  echo -e "${RED} Step: ${CURRENT_STEP}${NC}" >&2
  echo -e "${RED} Failed at line: ${line_no} with exit code: ${exit_code}${NC}" >&2
  echo -e "${RED}======================================================================${NC}" >&2
  echo "" >&2
  echo -e "${YELLOW}${BOLD}Troubleshooting suggestions:${NC}" >&2

  case "$CURRENT_STEP" in
    *"prerequisites"*)
      echo "  * Ensure Python 3.11+ is installed on your Raspberry Pi." >&2
      echo "  * Run: sudo apt update && sudo apt install -y python3 python3-venv python3-pip python3-dev build-essential" >&2
      ;;
    *"service user"*)
      echo "  * Check system user management: id sericulture" >&2
      echo "  * Verify /etc/passwd and /etc/group permissions." >&2
      ;;
    *"directories"*)
      echo "  * Check available disk space: df -h" >&2
      echo "  * Verify filesystem permissions for /opt, /var/lib, /etc." >&2
      ;;
    *"virtual environment"*)
      echo "  * If pip failed downloading packages, verify your internet connection on the Pi." >&2
      echo "  * Ensure python3-venv and build tools are present:" >&2
      echo "      sudo apt update && sudo apt install -y python3-venv python3-pip python3-dev build-essential" >&2
      echo "  * If OpenCV or NumPy wheels fail, install system libraries:" >&2
      echo "      sudo apt install -y libgl1 libglib2.0-0" >&2
      ;;
    *"systemd"*)
      echo "  * Verify that systemd is active on your Pi: systemctl is-system-running" >&2
      echo "  * Check system logs: sudo journalctl -xe" >&2
      ;;
    *)
      echo "  * Re-run the installer in debug mode for a full execution trace:" >&2
      echo "      sudo bash -x ./scripts/install.sh" >&2
      ;;
  esac
  echo "" >&2
  echo -e "${YELLOW}Need help? Check docs/pi_software_setup.md or open an issue.${NC}" >&2
  exit "$exit_code"
}

trap 'error_handler $LINENO' ERR

log_step() {
  CURRENT_STEP="$1"
  echo -e "\n${BLUE}${BOLD}==> ${CURRENT_STEP}${NC}"
}

log_info() {
  echo -e "    ${BLUE}[INFO]${NC} $1"
}

log_warn() {
  echo -e "    ${YELLOW}[WARN]${NC} $1"
}

log_success() {
  echo -e "    ${GREEN}[OK]${NC} $1"
}

# ------------------------------------------------------------------------------
# Pre-flight Checks
# ------------------------------------------------------------------------------
CURRENT_STEP="Checking root privileges"
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}[ERROR] This installation script must be run as root.${NC}" >&2
  echo -e "Please re-run with: ${BOLD}sudo ./scripts/install.sh${NC}" >&2
  exit 1
fi

# Resolve script and repository paths safely
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR" || {
  echo -e "${RED}[ERROR] Failed to switch to repository root: $REPO_DIR${NC}" >&2
  exit 1
}

CURRENT_STEP="Validating system prerequisites"
log_step "Checking system prerequisites..."

# Check Python 3
if ! command -v python3 &>/dev/null; then
  echo -e "${RED}[ERROR] python3 is not installed.${NC}" >&2
  echo "Please install Python 3.11+: sudo apt update && sudo apt install -y python3 python3-venv python3-pip python3-dev" >&2
  exit 1
fi

# Check Python version >= 3.11
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' &>/dev/null; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "unknown")
  echo -e "${RED}[ERROR] Python 3.11 or newer is required. Detected Python ${PY_VER}.${NC}" >&2
  exit 1
fi
log_success "Python $(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")') detected."

# Check python3-venv functionality
if ! python3 -m venv --help &>/dev/null; then
  echo -e "${RED}[ERROR] python3-venv module is not installed.${NC}" >&2
  echo "Please install it: sudo apt update && sudo apt install -y python3-venv" >&2
  exit 1
fi
log_success "python3-venv module is available."

# Validate required repository files
for req_file in "pyproject.toml" "systemd/sericulture.service" "config/device.example.yaml" ".env.example"; do
  if [ ! -f "$REPO_DIR/$req_file" ]; then
    echo -e "${RED}[ERROR] Required file '$req_file' missing from $REPO_DIR!${NC}" >&2
    exit 1
  fi
done
log_success "Repository files verified."

# ------------------------------------------------------------------------------
# Configuration Variables
# ------------------------------------------------------------------------------
SERVICE_USER="sericulture"
INSTALL_DIR="/opt/sericulture"
DATA_DIR="/var/lib/sericulture"
CONFIG_DIR="/etc/sericulture"

# Parse CLI arguments
EDITABLE_INSTALL=false
for arg in "$@"; do
  case "$arg" in
    --editable|-e)
      EDITABLE_INSTALL=true
      ;;
  esac
done

# ------------------------------------------------------------------------------
# 1. System User & Hardware Groups
# ------------------------------------------------------------------------------
log_step "[1/6] Setting up service user and hardware groups..."
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" -M "$SERVICE_USER"
  log_success "Created system service user '$SERVICE_USER'."
else
  log_info "User '$SERVICE_USER' already exists."
fi

# Ensure user is in hardware groups
for grp in gpio i2c video dialout; do
  if getent group "$grp" &>/dev/null; then
    usermod -aG "$grp" "$SERVICE_USER"
    log_info "Assigned '$SERVICE_USER' to group '$grp'."
  else
    log_warn "Hardware group '$grp' not present on this system; skipping."
  fi
done

# ------------------------------------------------------------------------------
# 2. Hardened Directories
# ------------------------------------------------------------------------------
log_step "[2/6] Setting up directories and permissions..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$CONFIG_DIR/certs"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$DATA_DIR" "$CONFIG_DIR"
chmod 750 "$INSTALL_DIR"
chmod 750 "$DATA_DIR"
chmod 700 "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR/certs"
log_success "Directories configured: $INSTALL_DIR, $DATA_DIR, $CONFIG_DIR."

# ------------------------------------------------------------------------------
# 3. Python Virtual Environment & Dependencies
# ------------------------------------------------------------------------------
log_step "[3/6] Setting up Python virtual environment..."
# Check for corrupted or incomplete existing venv
if [ -d "$INSTALL_DIR/venv" ] && [ ! -x "$INSTALL_DIR/venv/bin/pip" ]; then
  log_warn "Corrupted virtual environment detected. Rebuilding..."
  rm -rf "$INSTALL_DIR/venv"
fi

if [ ! -d "$INSTALL_DIR/venv" ]; then
  log_info "Creating virtual environment at $INSTALL_DIR/venv..."
  python3 -m venv "$INSTALL_DIR/venv"
  log_success "Virtual environment created."
else
  log_info "Using existing virtual environment at $INSTALL_DIR/venv."
fi

log_info "Upgrading pip, setuptools, and wheel in virtual environment..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade --quiet pip setuptools wheel

log_info "Installing sericulture-edge package and requirements..."
if [ "$EDITABLE_INSTALL" = true ]; then
  log_info "Running editable install (-e)..."
  "$INSTALL_DIR/venv/bin/pip" install -e .
else
  log_info "Running production install..."
  "$INSTALL_DIR/venv/bin/pip" install .
fi
log_success "Python package dependencies installed successfully."

# ------------------------------------------------------------------------------
# 4. Copy Application Source
# ------------------------------------------------------------------------------
log_step "[4/6] Synchronizing application source files..."
cp -r "$REPO_DIR/app/sericulture" "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
log_success "Source files deployed to $INSTALL_DIR."

# ------------------------------------------------------------------------------
# 5. Configuration & Certificates Deployment
# ------------------------------------------------------------------------------
log_step "[5/6] Deploying device configuration and secrets..."
if [ ! -f "$CONFIG_DIR/device.yaml" ]; then
  cp "$REPO_DIR/config/device.example.yaml" "$CONFIG_DIR/device.yaml"
  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/device.yaml"
  chmod 600 "$CONFIG_DIR/device.yaml"
  log_success "Deployed default configuration to $CONFIG_DIR/device.yaml."
else
  log_info "Existing configuration $CONFIG_DIR/device.yaml preserved."
fi

if [ ! -f "$CONFIG_DIR/sericulture.env" ]; then
  if [ -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env" "$CONFIG_DIR/sericulture.env"
    log_success "Deployed environment configuration from .env to $CONFIG_DIR/sericulture.env."
  else
    cp "$REPO_DIR/.env.example" "$CONFIG_DIR/sericulture.env"
    log_success "Deployed default environment configuration from .env.example to $CONFIG_DIR/sericulture.env."
  fi
  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/sericulture.env"
  chmod 600 "$CONFIG_DIR/sericulture.env"
else
  log_info "Existing environment file $CONFIG_DIR/sericulture.env preserved."
fi

# Synchronize local certificates folder if present
if [ -d "$REPO_DIR/certificates" ] && [ -n "$(find "$REPO_DIR/certificates" -type f 2>/dev/null)" ]; then
  log_info "Found local certificates in $REPO_DIR/certificates. Copying to $CONFIG_DIR/certs/..."
  cp -r "$REPO_DIR/certificates"/* "$CONFIG_DIR/certs/" 2>/dev/null || true
  chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/certs"
  chmod 700 "$CONFIG_DIR/certs"
  chmod 600 "$CONFIG_DIR/certs"/* 2>/dev/null || true
  log_success "Certificates synchronized to $CONFIG_DIR/certs/ with 600 permissions."
else
  log_info "No local certificates in $REPO_DIR/certificates to copy."
fi

# ------------------------------------------------------------------------------
# 6. Systemd Service Registration
# ------------------------------------------------------------------------------
log_step "[6/6] Registering systemd service..."
if command -v systemctl &>/dev/null; then
  cp "$REPO_DIR/systemd/sericulture.service" /etc/systemd/system/sericulture.service
  chmod 644 /etc/systemd/system/sericulture.service
  systemctl daemon-reload
  systemctl enable sericulture.service
  log_success "Service 'sericulture.service' installed and enabled on boot."
else
  log_warn "systemctl not found. Skipped systemd service installation."
fi

# ------------------------------------------------------------------------------
# Post-installation Verification
# ------------------------------------------------------------------------------
CURRENT_STEP="Post-installation verification"
log_step "Verifying installation integrity..."
if "$INSTALL_DIR/venv/bin/python3" -c "import sericulture" &>/dev/null; then
  log_success "Verification passed: sericulture package imports cleanly in venv."
else
  log_warn "Could not import sericulture directly in venv; check /opt/sericulture permissions."
fi

echo ""
echo -e "${GREEN}======================================================================${NC}"
echo -e "${GREEN}${BOLD}     Sericulture Edge-AI Software Installation Complete!             ${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo ""
echo -e "  ${BOLD}Configured Paths:${NC}"
echo -e "    * Installation:   ${INSTALL_DIR}"
echo -e "    * Python venv:    ${INSTALL_DIR}/venv"
echo -e "    * Configuration:  ${CONFIG_DIR}/device.yaml"
echo -e "    * Environment:    ${CONFIG_DIR}/sericulture.env"
echo -e "    * Certificates:   ${CONFIG_DIR}/certs/"
echo -e "    * SQLite Storage: ${DATA_DIR}/outbox.db"
echo ""
echo -e "  ${BOLD}Next Steps:${NC}"
echo -e "    1. Update credentials in ${BOLD}/etc/sericulture/sericulture.env${NC}:"
echo -e "       ${BLUE}sudo nano /etc/sericulture/sericulture.env${NC}"
echo -e "    2. Start the background service:"
echo -e "       ${BLUE}sudo systemctl start sericulture${NC}"
echo -e "    3. View live logs and telemetry:"
echo -e "       ${BLUE}sudo journalctl -u sericulture -f${NC}"
echo -e "    4. Check service status:"
echo -e "       ${BLUE}sudo systemctl status sericulture${NC}"
echo ""
