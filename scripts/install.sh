#!/usr/bin/env bash
# ==============================================================================
# Sericulture Edge-AI Pi 5 Software — Smart Automated Installer
# ==============================================================================
# Usage:
#   sudo ./scripts/install.sh                  # Interactive all-in-one setup
#   sudo ./scripts/install.sh --non-interactive # Automated setup using existing .env
#   sudo ./scripts/install.sh --skip-apt       # Skip APT package installation
#   sudo ./scripts/install.sh --editable       # Development install (editable venv)
# ==============================================================================

set -euo pipefail

# ANSI color codes for terminal output
if [ -t 1 ]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  BLUE='\033[0;34m'
  CYAN='\033[0;36m'
  BOLD='\033[1m'
  NC='\033[0m' # No Color
else
  RED=''
  GREEN=''
  YELLOW=''
  BLUE=''
  CYAN=''
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
    *"system packages"*|*"build tools"*|*"APT"*)
      echo "  * Check your internet connection or apt repository configuration:" >&2
      echo "      sudo apt update" >&2
      echo "  * If another package manager or unattended-upgrades process is running, wait for it or run:" >&2
      echo "      sudo fuser -vki -k /var/lib/dpkg/lock-frontend" >&2
      ;;
    *"hardware kernel"*|*"config.txt"*)
      echo "  * Check filesystem permissions on /boot/firmware/config.txt or /boot/config.txt." >&2
      ;;
    *"prerequisites"*)
      echo "  * Ensure Python 3.11+ is installed on your Raspberry Pi: python3 --version" >&2
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
    *"virtual environment"*|*"dependencies"*)
      echo "  * If pip failed compiling lgpio, verify swig and liblgpio-dev are installed:" >&2
      echo "      sudo apt update && sudo apt install -y swig liblgpio-dev python3-dev build-essential" >&2
      echo "  * If pip failed downloading packages, verify your internet connection on the Pi." >&2
      echo "  * If OpenCV or NumPy wheels fail, install system libraries:" >&2
      echo "      sudo apt install -y libgl1 libglib2.0-0" >&2
      ;;
    *"certificates"*)
      echo "  * Place your 3 certificate files in ~/Downloads or /etc/sericulture/certs/." >&2
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

# Parse CLI arguments
EDITABLE_INSTALL=false
SKIP_APT=false
NON_INTERACTIVE=false

for arg in "$@"; do
  case "$arg" in
    --editable|-e)
      EDITABLE_INSTALL=true
      ;;
    --skip-apt)
      SKIP_APT=true
      ;;
    --non-interactive|--yes|-y)
      NON_INTERACTIVE=true
      ;;
  esac
done

# ------------------------------------------------------------------------------
# 1. System Packages & Build Tools (APT)
# ------------------------------------------------------------------------------
if [ "$SKIP_APT" = false ] && command -v apt-get &>/dev/null; then
  CURRENT_STEP="Installing system packages and build tools via APT"
  log_step "[1/8] Installing system packages and hardware build tools (apt)..."
  
  export DEBIAN_FRONTEND=noninteractive
  log_info "Updating APT package index..."
  apt-get update -y || log_warn "apt-get update completed with warnings; continuing..."
  
  log_info "Installing compiler, SWIG, curl, and Python development headers..."
  apt-get install -y swig python3-dev build-essential python3-venv python3-pip libgl1 libglib2.0-0 curl
  
  # Install Raspberry Pi GPIO hardware packages if available in apt repositories
  for pkg in liblgpio-dev python3-lgpio i2c-tools; do
    if apt-cache show "$pkg" &>/dev/null; then
      log_info "Installing hardware package: $pkg..."
      apt-get install -y "$pkg" || log_warn "Could not install $pkg; continuing..."
    fi
  done
  log_success "System build tools and hardware packages installed successfully."
elif [ "$SKIP_APT" = true ]; then
  log_step "[1/8] Skipping APT package installation (--skip-apt requested)..."
else
  log_step "[1/8] Non-Debian system detected; skipping APT package installation..."
fi

# ------------------------------------------------------------------------------
# 2. Hardware Overlays & Kernel Configuration (/boot/firmware/config.txt)
# ------------------------------------------------------------------------------
CURRENT_STEP="Configuring hardware kernel overlays"
BOOT_CONFIG=""
if [ -f "/boot/firmware/config.txt" ]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
elif [ -f "/boot/config.txt" ]; then
  BOOT_CONFIG="/boot/config.txt"
fi

REBOOT_RECOMMENDED=false
if [ -n "$BOOT_CONFIG" ]; then
  log_step "[2/8] Checking Raspberry Pi hardware overlays ($BOOT_CONFIG)..."
  
  APPEND_ENTRIES=()
  grep -q "^dtparam=i2c_arm=on" "$BOOT_CONFIG" || APPEND_ENTRIES+=("dtparam=i2c_arm=on" "dtparam=i2c_arm_baudrate=100000")
  grep -q "^dtparam=pciex1" "$BOOT_CONFIG" || APPEND_ENTRIES+=("dtparam=pciex1")
  grep -q "^dtparam=pciex1_gen=3" "$BOOT_CONFIG" || APPEND_ENTRIES+=("dtparam=pciex1_gen=3")
  grep -q "^camera_auto_detect=1" "$BOOT_CONFIG" || APPEND_ENTRIES+=("camera_auto_detect=1")
  
  if [ "${#APPEND_ENTRIES[@]}" -gt 0 ]; then
    log_info "Enabling hardware interfaces (I2C, PCIe Gen3, Camera) in $BOOT_CONFIG..."
    {
      echo ""
      echo "# Sericulture Edge-AI Pi 5 Hardware Overlays"
      for entry in "${APPEND_ENTRIES[@]}"; do
        echo "$entry"
      done
    } >> "$BOOT_CONFIG"
    REBOOT_RECOMMENDED=true
    log_success "Hardware overlays enabled. (Reboot recommended after install)."
  else
    log_success "Hardware overlays (I2C, PCIe Gen 3, Camera) already active in $BOOT_CONFIG."
  fi
else
  log_step "[2/8] Boot config not found (non-Raspberry Pi environment); skipping overlay setup."
fi

# ------------------------------------------------------------------------------
# Prerequisite Verification
# ------------------------------------------------------------------------------
CURRENT_STEP="Validating system prerequisites"
log_step "Validating system prerequisites..."

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

# ------------------------------------------------------------------------------
# 3. System User & Hardware Groups
# ------------------------------------------------------------------------------
CURRENT_STEP="Setting up service user and hardware groups"
log_step "[3/8] Setting up service user and hardware groups..."
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

# Add invoking sudo user (e.g. pi) to sericulture and hardware groups for easy manual inspection
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  usermod -aG "$SERVICE_USER" "$SUDO_USER" 2>/dev/null || true
  for grp in gpio i2c video dialout; do
    if getent group "$grp" &>/dev/null; then
      usermod -aG "$grp" "$SUDO_USER" 2>/dev/null || true
    fi
  done
  log_info "Added user '$SUDO_USER' to group '$SERVICE_USER' and hardware groups."
fi

# ------------------------------------------------------------------------------
# 4. Hardened Directories
# ------------------------------------------------------------------------------
CURRENT_STEP="Setting up directories and permissions"
log_step "[4/8] Setting up directories and permissions..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$CONFIG_DIR/certs"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$DATA_DIR" "$CONFIG_DIR"
chmod 755 "$INSTALL_DIR"
chmod 750 "$DATA_DIR"
chmod 700 "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR/certs"
log_success "Directories configured: $INSTALL_DIR (755), $DATA_DIR (750), $CONFIG_DIR (700)."

# ------------------------------------------------------------------------------
# 5. Python Virtual Environment & Dependencies
# ------------------------------------------------------------------------------
CURRENT_STEP="Setting up Python virtual environment and dependencies"
log_step "[5/8] Setting up Python virtual environment and dependencies..."
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

log_info "Installing sericulture-edge package and requirements from pyproject.toml..."
if [ "$EDITABLE_INSTALL" = true ]; then
  log_info "Running editable install (-e)..."
  "$INSTALL_DIR/venv/bin/pip" install -e .
else
  log_info "Running production install..."
  "$INSTALL_DIR/venv/bin/pip" install .
fi
log_success "Python package dependencies installed successfully."

# ------------------------------------------------------------------------------
# 6. Copy Application Source
# ------------------------------------------------------------------------------
CURRENT_STEP="Synchronizing application source files"
log_step "[6/8] Synchronizing application source files..."
cp -r "$REPO_DIR/app/sericulture" "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
log_success "Source files deployed to $INSTALL_DIR."

# ------------------------------------------------------------------------------
# 7. Automated Certificate Discovery & Configuration Wizard
# ------------------------------------------------------------------------------
CURRENT_STEP="Configuring certificates and environment"
log_step "[7/8] Provisioning TLS certificates and environment configuration..."

# 7.1 Auto-Discover and Install Certificates from Downloads or Repository
CERT_DIR="$CONFIG_DIR/certs"
USER_HOME=$(eval echo "~${SUDO_USER:-$USER}")
SEARCH_DIRS=("$USER_HOME/Downloads" "/home/pi/Downloads" "$REPO_DIR/certificates" "$REPO_DIR")

log_info "Checking for AWS IoT Core certificates..."

# Look for device certificate (*.cert.pem or *certificate.pem.crt)
if [ ! -f "$CERT_DIR/device.cert.pem" ]; then
  for dir in "${SEARCH_DIRS[@]}"; do
    if [ -d "$dir" ]; then
      MATCH=$(find "$dir" -maxdepth 1 \( -name "device.cert.pem" -o -name "*certificate.pem.crt" -o -name "*.cert.pem" \) 2>/dev/null | head -n 1 || true)
      if [ -n "$MATCH" ] && [ -f "$MATCH" ]; then
        log_info "Auto-detected device certificate: $MATCH"
        install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 600 "$MATCH" "$CERT_DIR/device.cert.pem"
        break
      fi
    fi
  done
fi

# Look for private key (*.private.key or *private.pem.key)
if [ ! -f "$CERT_DIR/device.private.key" ]; then
  for dir in "${SEARCH_DIRS[@]}"; do
    if [ -d "$dir" ]; then
      MATCH=$(find "$dir" -maxdepth 1 \( -name "device.private.key" -o -name "*private.pem.key" -o -name "*.private.key" \) 2>/dev/null | head -n 1 || true)
      if [ -n "$MATCH" ] && [ -f "$MATCH" ]; then
        log_info "Auto-detected private key: $MATCH"
        install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 600 "$MATCH" "$CERT_DIR/device.private.key"
        break
      fi
    fi
  done
fi

# Look for AmazonRootCA1.pem (or auto-download from Amazon Trust Services)
if [ ! -f "$CERT_DIR/AmazonRootCA1.pem" ]; then
  for dir in "${SEARCH_DIRS[@]}"; do
    if [ -d "$dir" ]; then
      MATCH=$(find "$dir" -maxdepth 1 \( -name "AmazonRootCA1.pem" -o -name "Amazon-root-CA-1.pem" -o -name "*RootCA1*.pem" \) 2>/dev/null | head -n 1 || true)
      if [ -n "$MATCH" ] && [ -f "$MATCH" ]; then
        log_info "Auto-detected Root CA: $MATCH"
        install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 600 "$MATCH" "$CERT_DIR/AmazonRootCA1.pem"
        break
      fi
    fi
  done
  
  # Auto-download Amazon Root CA if missing and internet is accessible
  if [ ! -f "$CERT_DIR/AmazonRootCA1.pem" ] && command -v curl &>/dev/null; then
    log_info "Downloading AmazonRootCA1.pem from Amazon Trust Services..."
    if curl -sSL -o "$CERT_DIR/AmazonRootCA1.pem" "https://www.amazontrust.com/repository/AmazonRootCA1.pem"; then
      chown "$SERVICE_USER:$SERVICE_USER" "$CERT_DIR/AmazonRootCA1.pem"
      chmod 600 "$CERT_DIR/AmazonRootCA1.pem"
      log_success "Downloaded and secured AmazonRootCA1.pem."
    fi
  fi
fi

# Check certificate installation status
if [ -f "$CERT_DIR/device.cert.pem" ] && [ -f "$CERT_DIR/device.private.key" ] && [ -f "$CERT_DIR/AmazonRootCA1.pem" ]; then
  log_success "All 3 AWS IoT Core TLS certificate files are in place (/etc/sericulture/certs/)."
else
  log_warn "One or more certificates were not found in ~/Downloads. You can copy them later to $CERT_DIR/."
fi

# 7.2 Deploy default device.yaml configuration
if [ ! -f "$CONFIG_DIR/device.yaml" ]; then
  cp "$REPO_DIR/config/device.example.yaml" "$CONFIG_DIR/device.yaml"
  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/device.yaml"
  chmod 600 "$CONFIG_DIR/device.yaml"
  log_success "Deployed default hardware configuration to $CONFIG_DIR/device.yaml."
else
  log_info "Preserved existing $CONFIG_DIR/device.yaml."
fi

# 7.3 Smart / Interactive Environment Configuration Wizard
if [ ! -f "$CONFIG_DIR/sericulture.env" ] || grep -q "changeme-ats" "$CONFIG_DIR/sericulture.env" 2>/dev/null; then
  # Determine base values from existing .env if available
  BASE_ENV="$REPO_DIR/.env"
  [ ! -f "$BASE_ENV" ] && BASE_ENV="$REPO_DIR/.env.example"

  DEF_DEV_ID="SERI-PI5-001"
  DEF_FARM_ID="FARM-KAR-042"
  DEF_SITE="Rearing-Shed-A"
  DEF_ENDPOINT=""
  DEF_REGION="ap-south-1"

  if [ -f "$REPO_DIR/.env" ]; then
    DEF_DEV_ID=$(grep -E "^SERICULTURE_DEVICE_ID=" "$REPO_DIR/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "SERI-PI5-001")
    DEF_FARM_ID=$(grep -E "^SERICULTURE_FARM_ID=" "$REPO_DIR/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "FARM-KAR-042")
    DEF_SITE=$(grep -E "^SERICULTURE_SITE_NAME=" "$REPO_DIR/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "Rearing-Shed-A")
    DEF_ENDPOINT=$(grep -E "^AWS_IOT_ENDPOINT=" "$REPO_DIR/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "")
    DEF_REGION=$(grep -E "^AWS_IOT_REGION=" "$REPO_DIR/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "ap-south-1")
    # Ignore placeholder in default
    [[ "$DEF_ENDPOINT" == *"changeme"* ]] && DEF_ENDPOINT=""
  fi

  # Interactive prompt if terminal attached and not non-interactive
  if [ "$NON_INTERACTIVE" = false ] && [ -t 0 ]; then
    echo ""
    echo -e "${CYAN}${BOLD}----------------------------------------------------------------------${NC}"
    echo -e "${CYAN}${BOLD}           Interactive Configuration Wizard (30 Seconds)              ${NC}"
    echo -e "${CYAN}${BOLD}----------------------------------------------------------------------${NC}"
    echo -e "Press [Enter] to accept default values in brackets.\n"

    read -r -p " [?] Device ID [$DEF_DEV_ID]: " INPUT_DEV_ID
    INPUT_DEV_ID="${INPUT_DEV_ID:-$DEF_DEV_ID}"

    read -r -p " [?] Farm ID [$DEF_FARM_ID]: " INPUT_FARM_ID
    INPUT_FARM_ID="${INPUT_FARM_ID:-$DEF_FARM_ID}"

    read -r -p " [?] Shed / Site Name [$DEF_SITE]: " INPUT_SITE
    INPUT_SITE="${INPUT_SITE:-$DEF_SITE}"

    DEF_PROMPT="e.g., a141iw69pz5kqa-ats.iot.ap-south-1.amazonaws.com"
    [ -n "$DEF_ENDPOINT" ] && DEF_PROMPT="$DEF_ENDPOINT"
    read -r -p " [?] AWS IoT Core Data-ATS Endpoint [$DEF_PROMPT]: " INPUT_ENDPOINT
    INPUT_ENDPOINT="${INPUT_ENDPOINT:-$DEF_ENDPOINT}"

    read -r -p " [?] AWS Region [$DEF_REGION]: " INPUT_REGION
    INPUT_REGION="${INPUT_REGION:-$DEF_REGION}"
  else
    INPUT_DEV_ID="$DEF_DEV_ID"
    INPUT_FARM_ID="$DEF_FARM_ID"
    INPUT_SITE="$DEF_SITE"
    INPUT_ENDPOINT="$DEF_ENDPOINT"
    INPUT_REGION="$DEF_REGION"
  fi

  [ -z "$INPUT_ENDPOINT" ] && INPUT_ENDPOINT="changeme-ats.iot.ap-south-1.amazonaws.com"

  # Write /etc/sericulture/sericulture.env
  cat <<EOF > "$CONFIG_DIR/sericulture.env"
# ==============================================================================
# Sericulture Edge-AI Pi 5 Controller — Environment Variables
# Auto-generated by install.sh on $(date -u +"%Y-%m-%d %H:%M:%SZ")
# ==============================================================================

# Device & Farm Identity
SERICULTURE_DEVICE_ID=${INPUT_DEV_ID}
SERICULTURE_FARM_ID=${INPUT_FARM_ID}
SERICULTURE_SITE_NAME=${INPUT_SITE}

# AWS IoT Core Connection
AWS_IOT_ENABLED=true
AWS_IOT_ENDPOINT=${INPUT_ENDPOINT}
AWS_IOT_REGION=${INPUT_REGION}
AWS_IOT_CLIENT_ID=${INPUT_DEV_ID}

# TLS Certificate Paths
AWS_IOT_CERT_PATH=/etc/sericulture/certs/device.cert.pem
AWS_IOT_KEY_PATH=/etc/sericulture/certs/device.private.key
AWS_IOT_ROOT_CA_PATH=/etc/sericulture/certs/AmazonRootCA1.pem

# MQTT Topics & Sync Options
AWS_IOT_TOPIC_ROOT=novabion/sericulture/v1
AWS_IOT_QOS=1
AWS_IOT_ACK_TIMEOUT_S=30.0
AWS_IOT_BATCH_SIZE=20
AWS_IOT_BATCH_INTERVAL_S=15.0
AWS_IOT_MAX_RETRY_COUNT=50

# Storage & Runtime Paths
SERICULTURE_DB_PATH=/var/lib/sericulture/sericulture.db
SERICULTURE_MIN_FREE_DISK_MB=200
SERICULTURE_CONFIG_PATH=/etc/sericulture/device.yaml
SERICULTURE_LOG_LEVEL=INFO

# Safety & Autonomous Fallback Flags
SERICULTURE_OPTIONAL_AWS_IOT=true
SERICULTURE_OPTIONAL_DISPLAY=true
EOF

  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/sericulture.env"
  chmod 600 "$CONFIG_DIR/sericulture.env"
  log_success "Generated configuration at $CONFIG_DIR/sericulture.env (mode 600)."
else
  log_info "Preserved existing configuration $CONFIG_DIR/sericulture.env."
fi

# ------------------------------------------------------------------------------
# 8. Systemd Service Registration & Start
# ------------------------------------------------------------------------------
CURRENT_STEP="Registering systemd service"
log_step "[8/8] Registering systemd service..."
if command -v systemctl &>/dev/null; then
  cp "$REPO_DIR/systemd/sericulture.service" /etc/systemd/system/sericulture.service
  chmod 644 /etc/systemd/system/sericulture.service
  systemctl daemon-reload
  systemctl enable sericulture.service
  log_success "Service 'sericulture.service' installed and enabled on boot."

  # Attempt start
  log_info "Starting sericulture.service..."
  systemctl restart sericulture.service || true
else
  log_warn "systemctl not found. Skipped systemd service registration."
fi

# ------------------------------------------------------------------------------
# Post-Installation Hardware & Health Smoke Test
# ------------------------------------------------------------------------------
CURRENT_STEP="Post-installation verification"
echo ""
echo -e "${CYAN}${BOLD}======================================================================${NC}"
echo -e "${CYAN}${BOLD}           System & Hardware Smoke Test Diagnostics                   ${NC}"
echo -e "${CYAN}${BOLD}======================================================================${NC}"

# 1. Python Venv Import Test
if "$INSTALL_DIR/venv/bin/python3" -c "import sericulture" &>/dev/null; then
  echo -e "  [${GREEN}OK${NC}] Python Runtime:       Package 'sericulture' imports cleanly"
else
  echo -e "  [${RED}FAIL${NC}] Python Runtime:     Could not import 'sericulture' in venv"
fi

# 2. TLS Certificates Check
CERT_COUNT=0
[ -f "$CONFIG_DIR/certs/device.cert.pem" ] && ((CERT_COUNT++)) || true
[ -f "$CONFIG_DIR/certs/device.private.key" ] && ((CERT_COUNT++)) || true
[ -f "$CONFIG_DIR/certs/AmazonRootCA1.pem" ] && ((CERT_COUNT++)) || true

if [ "$CERT_COUNT" -eq 3 ]; then
  echo -e "  [${GREEN}OK${NC}] TLS Certificates:     All 3 certificates verified in $CONFIG_DIR/certs/"
else
  echo -e "  [${YELLOW}WARN${NC}] TLS Certificates:   $CERT_COUNT/3 certs present in $CONFIG_DIR/certs/"
fi

# 3. AWS Endpoint Configuration
if grep -q "changeme-ats" "$CONFIG_DIR/sericulture.env" 2>/dev/null; then
  echo -e "  [${YELLOW}WARN${NC}] AWS IoT Endpoint:   Still contains placeholder; edit $CONFIG_DIR/sericulture.env"
else
  ENDPOINT_VAL=$(grep -E "^AWS_IOT_ENDPOINT=" "$CONFIG_DIR/sericulture.env" | cut -d'=' -f2- || echo "")
  echo -e "  [${GREEN}OK${NC}] AWS IoT Endpoint:     $ENDPOINT_VAL"
fi

# 4. SHT40 I2C Hardware Bus Check
if command -v i2cdetect &>/dev/null; then
  if i2cdetect -y 1 2>/dev/null | grep -q " 44 "; then
    echo -e "  [${GREEN}OK${NC}] SHT40 Sensor:         Detected on I2C bus 1 at address 0x44"
  else
    echo -e "  [${YELLOW}WARN${NC}] SHT40 Sensor:       Not detected on I2C address 0x44 (verify pin 3 & 5 wiring)"
  fi
else
  echo -e "  [${BLUE}INFO${NC}] SHT40 Sensor:       i2cdetect not installed; skipped live hardware check"
fi

# 5. UVC Video Camera Check
if [ -e "/dev/video0" ]; then
  echo -e "  [${GREEN}OK${NC}] Camera:               Video device found at /dev/video0"
else
  echo -e "  [${YELLOW}WARN${NC}] Camera:             No camera detected at /dev/video0 (connect USB/CSI camera)"
fi

# 6. Service Status
if command -v systemctl &>/dev/null; then
  if systemctl is-active --quiet sericulture.service; then
    echo -e "  [${GREEN}OK${NC}] Background Service:  sericulture.service is ACTIVE & RUNNING"
  else
    echo -e "  [${YELLOW}INFO${NC}] Background Service:  Installed and enabled (Run: sudo systemctl start sericulture)"
  fi
fi

echo ""
echo -e "${GREEN}======================================================================${NC}"
echo -e "${GREEN}${BOLD}     Sericulture Edge-AI Software Installation Complete!             ${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo ""
echo -e "  ${BOLD}Installed Paths:${NC}"
echo -e "    * Installation:   ${INSTALL_DIR}"
echo -e "    * Python venv:    ${INSTALL_DIR}/venv"
echo -e "    * Configuration:  ${CONFIG_DIR}/device.yaml"
echo -e "    * Environment:    ${CONFIG_DIR}/sericulture.env"
echo -e "    * Certificates:   ${CONFIG_DIR}/certs/"
echo -e "    * Local Storage:  ${DATA_DIR}/sericulture.db"
echo ""
echo -e "  ${BOLD}Service Management Commands:${NC}"
echo -e "    * View live logs:   ${BLUE}sudo journalctl -u sericulture -f${NC}"
echo -e "    * Service status:   ${BLUE}sudo systemctl status sericulture${NC}"
echo -e "    * Restart service:  ${BLUE}sudo systemctl restart sericulture${NC}"
echo -e "    * Stop service:     ${BLUE}sudo systemctl stop sericulture${NC}"
echo ""

if [ "$REBOOT_RECOMMENDED" = true ]; then
  echo -e "${YELLOW}${BOLD}[NOTICE] Hardware overlays were added to $BOOT_CONFIG.${NC}"
  echo -e "${YELLOW}Please reboot your Raspberry Pi to activate I2C & PCIe Gen 3: ${BOLD}sudo reboot${NC}\n"
fi
