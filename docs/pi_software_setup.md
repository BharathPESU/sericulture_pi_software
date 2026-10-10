# Raspberry Pi 5 Software Setup & Commissioning Guide

Complete, step-by-step technical guide to provisioning, configuring, and operating the **Sericulture Edge-AI Pi 5 Software** on a physical Raspberry Pi 5 device.

---

## 1. System Overview & Hardware Architecture

The controller runs on **Raspberry Pi OS Lite (64-bit Bookworm)** and coordinates real-time environmental monitoring, local Edge-AI vision inference, relay load control, offline SQLite outbox buffering, and AWS IoT Core synchronization.

```
                              +-------------------------------------------+
                              |         Raspberry Pi 5 (8GB / 4GB)        |
                              |      64-bit Raspberry Pi OS (Bookworm)    |
                              +-------------------------------------------+
                                   |              |             |
                 +-----------------+              |             +------------------+
                 | I2C (Bus 1)                    | PCIe Gen 3                     | GPIO Outputs
                 v                                v                                v
+---------------------------------+  +-------------------------+  +---------------------------------+
|     Sensirion SHT40 Sensor      |  |    Hailo-8 AI HAT+      |  | Waveshare 6-Channel Relay Board |
|     (Temp & Relative Humidity)  |  |   (26 TOPS NPU Vision)  |  | Ch1: Fan | Ch2: Mist | Ch3: Light|
+---------------------------------+  +-------------------------+  +---------------------------------+
                 |                                                                 |
                 | Digital Inputs                                                  | Acoustic Alarm
                 v                                                                 v
+---------------------------------+                               +---------------------------------+
| - Magnetic Float Switch (Water) |                               |     Active Piezo Buzzer         |
| - Isolated Mains AC Loss Sensor |                               |  (Distinctive Fault Beep Codes) |
+---------------------------------+                               +---------------------------------+
```

---

## 2. Hardware Bill of Materials & Wiring Table

| Component | Interface / Pins | Physical Pin | BCM GPIO | Electrical Direction | Logic Mode / Polarity | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Relay Ch 1 (Fan)** | Output | Pin 29 | **BCM 5** | Output | Active-Low (`0` = ON, `1` = OFF) | Ventilation exhaust fan |
| **Relay Ch 2 (Mist)** | Output | Pin 31 | **BCM 6** | Output | Active-Low (`0` = ON, `1` = OFF) | Mist pump / ultrasonic sprayer |
| **Relay Ch 3 (Light)** | Output | Pin 33 | **BCM 13** | Output | Active-Low (`0` = ON, `1` = OFF) | Day/night photoperiod lamp |
| **Relay Ch 4 (Aux)** | Output | Pin 36 | **BCM 16** | Output | Active-Low (`0` = ON, `1` = OFF) | Auxiliary / heater spare |
| **Relay Ch 5 (Aux)** | Output | Pin 35 | **BCM 19** | Output | Active-Low (`0` = ON, `1` = OFF) | Expansion spare |
| **Relay Ch 6 (Aux)** | Output | Pin 37 | **BCM 26** | Output | Active-Low (`0` = ON, `1` = OFF) | Expansion spare |
| **Piezo Buzzer** | Output | Pin 32 | **BCM 12** | Output | Active-High (`1` = BEEP, `0` = SILENT) | Acoustic alarm patterns |
| **Float Switch** | Digital Input | Pin 18 | **BCM 24** | Input | Internal Pull-Up (`0` = OK, `1` = LOW) | Tank water level monitor |
| **Mains Detector** | Digital Input | Pin 22 | **BCM 25** | Input | Optocoupler (`1` = PRESENT, `0` = LOST) | AC power loss interlock |
| **SHT40 (SDA)** | I²C Data | Pin 3 | **BCM 2** | I²C Bus 1 | Standard I²C 3.3V | Address `0x44` |
| **SHT40 (SCL)** | I²C Clock | Pin 5 | **BCM 3** | I²C Bus 1 | Standard I²C 3.3V | Address `0x44` |
| **Hailo-8 HAT+** | PCIe 1x | PCIe Ribbon | PCIe Gen 2/3 | PCIe x1 | 26 TOPS NPU | Device `/dev/hailo0` |
| **UVC Camera** | USB Host | USB 3.0 / 2.0 | USB | V4L2 Device | 1080p @ 15–30 FPS | `/dev/video*` |

> [!CAUTION]
> **Relay Power Supply:** NEVER power the relay board coil supply (5V/12V) directly from the Raspberry Pi GPIO 5V pin. The relay board requires an independent, isolated power supply with a common ground to prevent inductive flyback spikes from resetting the Pi.

---

## 3. Raspberry Pi OS Preparation & Kernel Configuration

### 3.1 Base Operating System Installation
1. Flash **Raspberry Pi OS Lite (64-bit)** (Debian Bookworm) using the Raspberry Pi Imager.
2. In the OS customization settings:
   - Configure hostname (e.g. `sericulture-pi-01`).
   - Create local admin user (e.g. `bharath`).
   - Configure Wi-Fi or Ethernet networking.
   - Enable SSH.

### 3.2 Enable Hardware Interfaces (I²C and PCIe)
Boot the Pi, SSH into the machine, and edit `/boot/firmware/config.txt`:

```bash
sudo nano /boot/firmware/config.txt
```

Ensure the following configuration lines are present at the bottom of the file:

```ini
# Enable I2C Bus 1 for SHT40 sensor
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=100000

# Enable PCIe Gen 3 for Raspberry Pi AI HAT+ (Hailo-8)
dtparam=pciex1
dtparam=pciex1_gen=3

# Camera auto-detect
camera_auto_detect=1
```

Save the file and reboot:
```bash
sudo reboot
```

### 3.3 Install System Hardware Tools & Drivers
After rebooting, install core packages:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv python3-dev git i2c-tools libgl1 libglib2.0-0 v4l-utils
```

### 3.4 Install Hailo-8 AI HAT+ Drivers
Install the Raspberry Pi AI HAT+ driver stack:

```bash
sudo apt install -y hailo-all
```

Verify that the Hailo NPU is detected:
```bash
hailortcli fw-control identify
```
*Expected output: Firmware version and Hailo-8 device status.*

### 3.5 Verify I²C Sensor Bus
Check if the SHT40 temperature/humidity sensor responds on address `0x44`:

```bash
sudo i2cdetect -y 1
```
*You should see `44` at row `40`, column `4`.*

---

## 4. Software Installation

Clone the repository to your Raspberry Pi:

```bash
cd ~
git clone https://github.com/novabion/sericulture_pi_software.git
cd sericulture_pi_software
```

### Option A: Production Automated Installation (Recommended)
Run the root installer script:

```bash
sudo ./scripts/install.sh
```

**What `scripts/install.sh` automatically performs:**
1. Creates unprivileged service user `sericulture` and assigns it to groups `gpio`, `i2c`, `video`, `dialout`.
2. Creates hardened directories:
   - Application: `/opt/sericulture` (mode 750)
   - Database / Outbox: `/var/lib/sericulture` (mode 750)
   - Configuration / TLS: `/etc/sericulture` (mode 700)
3. Creates a Python virtual environment `/opt/sericulture/venv` and installs all dependencies (`gpiozero`, `lgpio`, `sensirion-i2c`, `awsiotsdk`, `opencv-python-headless`, etc.).
4. Copies application source code into `/opt/sericulture/sericulture/`.
5. Deploys `/etc/sericulture/device.yaml` and `/etc/sericulture/sericulture.env`.
6. Installs and enables the `systemd` service (`/etc/systemd/system/sericulture.service`) to start on every boot.

---

### Option B: Local Development / Manual Setup
If you want to test and develop without system service installation:

```bash
# Create local virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install --upgrade pip
pip install -e ".[dev]"

# Run test suite to verify installation
python3 -m pytest
```

---

## 5. Configuration & Environment Variables

The software uses a dual configuration system:
- **`device.yaml`**: Hardware pin maps, environmental thresholds, and timing parameters.
- **`.env` (or `/etc/sericulture/sericulture.env`)**: Device identity, AWS IoT Core endpoints, and X.509 certificate paths.

### 5.1 Setting Up Environment Variables

Create your local `.env` file from the provided template:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set the values:

```ini
# ==============================================================================
# Sericulture Edge-AI Pi 5 Controller — Environment Variables
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Device & Farm Identity
# ------------------------------------------------------------------------------
# Must match your AWS IoT Core Thing Name exactly
SERICULTURE_DEVICE_ID=SERI-PI5-001
SERICULTURE_FARM_ID=FARM-KAR-042
SERICULTURE_SITE_NAME=Rearing-Shed-A

# ------------------------------------------------------------------------------
# 2. AWS IoT Core Connection & Endpoints
# ------------------------------------------------------------------------------
AWS_IOT_ENABLED=true
AWS_IOT_ENDPOINT=a141iw69pz5kqa-ats.iot.ap-south-1.amazonaws.com
AWS_IOT_REGION=ap-south-1
AWS_IOT_CLIENT_ID=SERI-PI5-001

# ------------------------------------------------------------------------------
# 3. AWS IoT Core X.509 TLS Certificates
# ------------------------------------------------------------------------------
# Absolute paths to certificate files on the Raspberry Pi
AWS_IOT_CERT_PATH=/etc/sericulture/certs/device.cert.pem
AWS_IOT_KEY_PATH=/etc/sericulture/certs/device.private.key
AWS_IOT_ROOT_CA_PATH=/etc/sericulture/certs/AmazonRootCA1.pem

# ------------------------------------------------------------------------------
# 4. MQTT Topics & Sync Options
# ------------------------------------------------------------------------------
AWS_IOT_TOPIC_ROOT=novabion/sericulture/v1
AWS_IOT_QOS=1
AWS_IOT_ACK_TIMEOUT_S=30.0
AWS_IOT_BATCH_SIZE=20
AWS_IOT_BATCH_INTERVAL_S=15.0
AWS_IOT_MAX_RETRY_COUNT=50

# ------------------------------------------------------------------------------
# 5. Local Storage & Database Configuration
# ------------------------------------------------------------------------------
SERICULTURE_DB_PATH=/var/lib/sericulture/sericulture.db
SERICULTURE_MIN_FREE_DISK_MB=200

# ------------------------------------------------------------------------------
# 6. Configuration File & Runtime Settings
# ------------------------------------------------------------------------------
SERICULTURE_CONFIG_PATH=/etc/sericulture/device.yaml
SERICULTURE_LOG_LEVEL=INFO

# ------------------------------------------------------------------------------
# 7. Hardware & Fault Behavior Flags
# ------------------------------------------------------------------------------
# If true, offline network status will not prevent local actuator control
SERICULTURE_OPTIONAL_AWS_IOT=true
SERICULTURE_OPTIONAL_DISPLAY=true
```

> [!TIP]
> Setting `SERICULTURE_OPTIONAL_AWS_IOT=true` ensures that if the rearing shed temporarily loses internet connectivity, the controller continues monitoring, cooling, and misting locally without locking into a fault state.

---

### 5.2 Certificate Provisioning

Copy your 3 AWS IoT Core certificate files to the secure certificates directory on the Pi:

```bash
sudo mkdir -p /etc/sericulture/certs

# Copy the files:
sudo cp device.cert.pem /etc/sericulture/certs/device.cert.pem
sudo cp device.private.key /etc/sericulture/certs/device.private.key
sudo cp AmazonRootCA1.pem /etc/sericulture/certs/AmazonRootCA1.pem

# Restrict permissions
sudo chown -R sericulture:sericulture /etc/sericulture/certs
sudo chmod 700 /etc/sericulture/certs
sudo chmod 600 /etc/sericulture/certs/*
```

---

### 5.3 Reviewing Environmental Control Thresholds (`device.yaml`)

Inspect `/etc/sericulture/device.yaml` (or `config/device.example.yaml`):

```yaml
control:
  fan_on_temp_c: 28.5            # Turn exhaust fan ON above 28.5°C
  fan_off_temp_c: 26.0           # Turn exhaust fan OFF below 26.0°C (2.5°C hysteresis)
  mist_on_humidity_pct: 70.0     # Turn mist sprayer ON below 70% RH
  mist_off_humidity_pct: 85.0    # Turn mist sprayer OFF above 85% RH (15% hysteresis)
  light_on_hour: 18              # 18:00 UTC (Night inspection light ON)
  light_off_hour: 6              # 06:00 UTC (Night inspection light OFF)
  min_on_time_s: 60.0            # Anti-chatter: minimum 60s runtime once turned on
  min_off_time_s: 60.0           # Anti-chatter: minimum 60s cooldown once turned off
  max_continuous_run_s: 1800.0   # Thermal safety limit: 30 minutes max continuous run
  cooldown_after_max_run_s: 300.0 # 5 minutes forced rest after max run
  persistence_window_s: 15.0     # Condition must persist for 15s before actuation
  provisional: true              # Change to false after domain agronomist approval
```

---

## 6. Testing, First Boot & Diagnostics

### 6.1 Execute Automated Test Suite
Run the 39 unit, integration, and fault recovery tests:

```bash
python3 -m pytest
```
*All 39 tests must pass.*

---

### 6.2 Test Diagnostics Without Activating Loads
Run a standalone hardware diagnostic probe:

```bash
python3 -m sericulture.main --config config/device.example.yaml --log-level DEBUG
```

**Check the console log output for each component:**
- `sht40: PASS — Valid temperature and humidity reading`
- `float_switch: PASS — Contact state WATER_OK`
- `mains_detector: PASS — Mains power PRESENT`
- `camera: PASS — Video capture device operational`
- `hailo_device: PASS — Hailo-8 accelerator hardware detected on PCIe`
- `relay_board: UNVERIFIABLE — GPIO control path OK, all channels commanded OFF`
- `sqlite: PASS — Local database WAL mode operational`
- `aws_iot: PASS — Connected to AWS IoT Core`

---

### 6.3 Understanding Buzzer Acoustic Signals
If any component fails during boot, the buzzer driver sounds a distinct non-blocking pattern:

| Beep Pattern | Fault Category | Meaning / Action Required |
| :--- | :--- | :--- |
| **1 long beep** | SHT40 Sensor | Check I²C wiring on pins 3 & 5 (`i2cdetect -y 1`). |
| **2 short beeps** | UVC Camera | Check USB camera connection (`lsusb`, `/dev/video0`). |
| **3 short beeps** | Hailo-8 NPU | Check PCIe ribbon cable and `lspci`. |
| **1 long + 2 short** | Safety Inputs | Water tank low (`WATER_LOW`) or AC mains power disconnected. |
| **2 long beeps** | Local Storage | Disk space full (`< 200MB free`) or SQLite database locked. |
| **4 short beeps** | GPIO / Config | Pin assignment conflict or missing configuration. |
| **1 brief beep** | Cloud Status | AWS IoT Core disconnected (informational). |

---

## 7. Service Management (24/7 Production Operation)

The application runs as a systemd service under the `sericulture` user:

```bash
# 1. Start the service
sudo systemctl start sericulture.service

# 2. Check current status
sudo systemctl status sericulture.service

# 3. View live streaming application logs
sudo journalctl -u sericulture.service -f

# 4. Safely stop the service (forces all relays OFF before exit)
sudo systemctl stop sericulture.service

# 5. Restart the service
sudo systemctl restart sericulture.service
```

---

## 8. Physical Commissioning & Safety Checklist

Complete this checklist in order before placing live silkworms in the rearing shed:

- [ ] **1. Power-Off Continuity Check:** With power disconnected, verify with a multimeter that all relay channels are open-circuit (`COM` to `NO` resistance = $\infty$).
- [ ] **2. Single-Wire Disconnect Test:** Disconnect the relay signal ribbon cable while relay board is powered. Verify no relays energize.
- [ ] **3. Boot Chatter Verification:** Power on the Raspberry Pi 5. Watch relay LEDs. Confirm that zero relay clicks or LED flickers occur while Linux boots.
- [ ] **4. Float Switch Interlock:** With the mist water reservoir empty, confirm the diagnostic status reports `WATER_LOW`. Attempting manual mist actuation must be rejected with `SAFETY BLOCKED`. Fill the reservoir and confirm status transitions to `WATER_OK`.
- [ ] **5. Mains Power Loss Emergency Shutoff:** Connect a 12V test light to Relay Channel 1 (Fan). Energize the fan. Disconnect AC mains input to the optocoupler. Verify the relay turns OFF in `< 50ms`. Reconnect mains and confirm the fan does not restart until the 5-second stabilization delay elapses.
- [ ] **6. Camera & Inference Check:** Verify the vision pipeline maintains $\ge 15\text{ FPS}$ on Hailo-8 without throttling or thermal warnings (`cpu_temp < 75°C`).
- [ ] **7. Agronomist Signoff:** Once environmental thresholds are verified, change `control.provisional: false` in `/etc/sericulture/device.yaml`.

---

## 9. Troubleshooting & FAQ

### Q1: The service exits immediately with code 2.
**Cause:** Hardware diagnostic check failed for one of the required components.  
**Fix:** Run `sudo journalctl -u sericulture.service -n 50 --no-pager` and look for `Boot diagnostics failed! Required components unhealthy: ...`. Check the physical wiring of the reported component.

### Q2: SHT40 sensor shows `READ_ERROR` or CRC failure.
**Cause:** Loose I²C wires or missing pull-up resistors on long cable runs.  
**Fix:** Keep I²C cable length under 1 meter. Verify connection on Pin 3 (SDA) and Pin 5 (SCL). Test with `i2cdetect -y 1`.

### Q3: Hailo-8 HAT+ is not recognized.
**Cause:** PCIe Gen 3 overlay missing in `/boot/firmware/config.txt` or PCIe ribbon cable seated incorrectly.  
**Fix:** Ensure `dtparam=pciex1` and `dtparam=pciex1_gen=3` are present in `/boot/firmware/config.txt`. Reseat the PCIe ribbon connector.

### Q4: AWS IoT Core connection fails with TLS Handshake Error.
**Cause:** Invalid certificate path, missing Root CA 1, or system clock out of sync.  
**Fix:**
1. Verify system time is synchronized via NTP: `timedatectl status`.
2. Verify paths in `.env` match `/etc/sericulture/certs/`.
3. Verify the certificate has been activated in the AWS IoT Core Console and the policy is attached.

### Q5: How do I completely remove the software?
Run the automated uninstaller:
```bash
sudo ./scripts/uninstall.sh
```
This stops the service, disables auto-boot, safely de-energizes all relays, and removes `/opt/sericulture`.
