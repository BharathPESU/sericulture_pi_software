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

## 3. Fast-Track Automated Setup (Recommended)

The automated installer eliminates manual copy-pasting and configures everything through a single command.

### Step 1: Place AWS Certificates in `~/Downloads`
When you create your AWS IoT Thing and download your certificates on the Raspberry Pi:
* `device.cert.pem` (or `*certificate.pem.crt`)
* `device.private.key` (or `*private.pem.key`)
* `AmazonRootCA1.pem` *(Optional: If missing, the installer will automatically download it from Amazon Trust Services)*

Leave them in `~/Downloads` (or `/home/pi/Downloads`).

---

### Step 2: Run the Smart Automated Installer
Clone the repository and run `scripts/install.sh`:

```bash
cd ~
git clone https://github.com/BharathPESU/sericulture_pi_software.git
cd sericulture_pi_software
sudo ./scripts/install.sh
```

#### What `scripts/install.sh` Automatically Handles:
1. **APT System Dependencies**: Installs `swig`, `liblgpio-dev`, `python3-dev`, `build-essential`, `python3-venv`, `libgl1`, `libglib2.0-0`, and `i2c-tools`.
2. **Hardware Kernel Overlays**: Automatically configures `/boot/firmware/config.txt` for I²C (`dtparam=i2c_arm=on`), PCIe Gen 3 (`dtparam=pciex1_gen=3`), and camera auto-detection.
3. **User & Group Security**: Creates service user `sericulture`, assigns hardware groups (`gpio`, `i2c`, `video`, `dialout`), and adds your user (`pi`) for easy terminal debugging.
4. **Hardened Directories**: Sets up `/opt/sericulture` (mode 755), `/var/lib/sericulture` (mode 750), and `/etc/sericulture` (mode 700).
5. **Python Virtual Environment**: Installs `sericulture-edge` package and compiles `lgpio` in `/opt/sericulture/venv`.
6. **Certificate Auto-Discovery**: Finds the 3 certificate files in `~/Downloads`, copies them to `/etc/sericulture/certs/`, and applies `chmod 600`.
7. **Interactive 30-Second Config Wizard**: Prompts you for your **AWS IoT Core Endpoint** and Device/Farm identifiers, then writes `/etc/sericulture/sericulture.env`.
8. **Systemd Service Registration & Start**: Installs, enables, and starts `sericulture.service` automatically.
9. **Live Smoke Test Diagnostics**: Verifies the Python runtime, TLS certificates, I²C SHT40 sensor (`0x44`), video camera (`/dev/video0`), and service status.

---

## 4. Configuration Reference

### 4.1 Environment Variables (`/etc/sericulture/sericulture.env`)
The installer prompts you for core settings, but you can inspect or edit them at any time:

```bash
sudo nano /etc/sericulture/sericulture.env
```

```ini
# ==============================================================================
# Device & Farm Identity
# ==============================================================================
SERICULTURE_DEVICE_ID=SERI-PI5-001
SERICULTURE_FARM_ID=FARM-KAR-042
SERICULTURE_SITE_NAME=Rearing-Shed-A

# ==============================================================================
# AWS IoT Core Connection
# ==============================================================================
AWS_IOT_ENABLED=true
AWS_IOT_ENDPOINT=a141iw69pz5kqa-ats.iot.ap-south-1.amazonaws.com
AWS_IOT_REGION=ap-south-1
AWS_IOT_CLIENT_ID=SERI-PI5-001

# ==============================================================================
# Certificate Paths
# ==============================================================================
AWS_IOT_CERT_PATH=/etc/sericulture/certs/device.cert.pem
AWS_IOT_KEY_PATH=/etc/sericulture/certs/device.private.key
AWS_IOT_ROOT_CA_PATH=/etc/sericulture/certs/AmazonRootCA1.pem

# ==============================================================================
# Storage & Autonomous Safety Flags
# ==============================================================================
SERICULTURE_DB_PATH=/var/lib/sericulture/sericulture.db
SERICULTURE_CONFIG_PATH=/etc/sericulture/device.yaml
SERICULTURE_LOG_LEVEL=INFO
SERICULTURE_OPTIONAL_AWS_IOT=true
SERICULTURE_OPTIONAL_DISPLAY=true
```

> [!TIP]
> Setting `SERICULTURE_OPTIONAL_AWS_IOT=true` ensures that if the rearing shed temporarily loses internet connectivity, the controller continues monitoring, cooling, and misting locally without locking into a fault state.

---

### 4.2 Environmental Control Thresholds (`/etc/sericulture/device.yaml`)

Control thresholds and timing parameters are defined in `/etc/sericulture/device.yaml`:

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

## 5. Service Operation & Live Monitoring

### 5.1 Service Management Commands

```bash
# View live telemetry and diagnostic logs
sudo journalctl -u sericulture -f

# Check service status
sudo systemctl status sericulture

# Restart service (e.g. after editing device.yaml or .env)
sudo systemctl restart sericulture

# Stop service
sudo systemctl stop sericulture
```

---

### 5.2 Manual Terminal Debug Mode

To run the controller directly in your terminal with real-time debug output (without systemd):

```bash
sudo /opt/sericulture/venv/bin/python3 -m sericulture.main --config /etc/sericulture/device.yaml --log-level DEBUG
```

---

### 5.3 Acoustic Buzzer Fault Signals

If any hardware component encounters a fault, the piezo buzzer sounds a distinct non-blocking acoustic pattern:

| Beep Pattern | Fault Category | Meaning / Action Required |
| :--- | :--- | :--- |
| **1 long beep** | SHT40 Sensor | Check I²C wiring on pins 3 & 5 (`sudo i2cdetect -y 1`). |
| **2 short beeps** | UVC Camera | Check USB camera connection (`lsusb`, `/dev/video0`). |
| **3 short beeps** | Hailo-8 NPU | Check PCIe ribbon cable and `lspci`. |
| **1 long + 2 short** | Safety Inputs | Water tank low (`WATER_LOW`) or AC mains power disconnected. |
| **2 long beeps** | Local Storage | Disk space full (`< 200MB free`) or SQLite database locked. |
| **4 short beeps** | GPIO / Config | Pin assignment conflict or missing configuration. |
| **1 brief beep** | Cloud Status | AWS IoT Core disconnected (informational). |

---

## 6. Troubleshooting & Diagnostics

### I²C SHT40 Sensor Not Detected (`i2cdetect` shows empty)
1. Verify wiring:
   * SHT40 `VIN` -> Pi Pin 1 (3.3V)
   * SHT40 `GND` -> Pi Pin 9 (Ground)
   * SHT40 `SDA` -> Pi Pin 3 (BCM 2)
   * SHT40 `SCL` -> Pi Pin 5 (BCM 3)
2. Run live I²C detection:
   ```bash
   sudo i2cdetect -y 1
   ```
   *Expected: Address `44` displayed at row `40`, column `4`.*

### Camera Not Detected
1. Check USB device enumeration:
   ```bash
   lsusb
   v4l2-ctl --list-devices
   ```
2. Ensure `/dev/video0` is present.

### Hailo-8 AI HAT+ Not Detected
1. Verify PCIe connection:
   ```bash
   lspci
   ```
   *Should show: `Co-processor: Hailo Technologies Ltd. Hailo-8 AI Processor`.*
2. Check Hailo runtime status:
   ```bash
   hailortcli fw-control identify
   ```

### AWS IoT Core Connection Issues
1. Test outbound TLS port 8883 connectivity:
   ```bash
   nc -zv <your-ats-endpoint>.iot.ap-south-1.amazonaws.com 8883
   ```
2. Verify certificate permissions:
   ```bash
   sudo ls -la /etc/sericulture/certs
   ```
   *Files should be owned by `sericulture:sericulture` with mode `-rw-------` (`600`).*
