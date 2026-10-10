# Master Prompt: Build Reliable Sericulture Edge-AI Software for Raspberry Pi 5

> **Purpose:** Give this entire prompt to a coding agent or software engineer to build the Raspberry Pi software for the Sericulture Edge-AI controller. Treat the requirements below as the project specification. Do not skip safety behavior to make a demo appear to work.

---

## 1. Role and primary objective

Act as a **senior embedded Linux, Python, Edge-AI, IoT, and functional-safety-minded software engineer**. Design and implement production-minded software for a Raspberry Pi 5 running 64-bit Raspberry Pi OS Lite. The device monitors sericulture conditions, performs local vision inference, controls the fan/mist sprayer/night light through a relay module, logs data locally, and synchronizes events to AWS IoT Core whenever connectivity is available.

The software must prioritize these requirements, in this order:

1. **Electrical and actuator safety.** Default load-control outputs to a known safe OFF state, prevent unsafe automatic operation during boot or a fault, and never depend on cloud availability for local safety.
2. **Correct, honest diagnostics.** Detect faults that software can genuinely observe. Do not claim that hardware has passed a physical test if no feedback signal exists.
3. **Reliable local operation.** Keep appropriate local monitoring, local decision logic, and local logging working during internet/4G outages.
4. **Durable data synchronization.** Store each event locally before attempting cloud transmission. Retry failures without losing events or creating duplicate logical records in the cloud.
5. **Maintainability.** Use small modules, clear interfaces, configuration validation, typed code where useful, structured logs, tests, and documented recovery procedures.

Build a working repository, not just example snippets. If a hardware/API detail is unknown, isolate it behind a configuration/interface, state the assumption, and provide a commissioning check. Never silently invent hardware capabilities.

## 2. Hardware and fixed wiring assumptions

Target hardware:

- Raspberry Pi 5, **4 GB RAM**.
- Official Raspberry Pi AI HAT+ **26 TOPS / Hailo-8** accelerator. Do not confuse this with the 13-TOPS Hailo-8L variant or AI HAT+ 2 / Hailo-10H.
- Sensirion SHT40 temperature/humidity sensor using I²C.
- Arducam OV2710 2 MP USB UVC camera, 1080p.
- Waveshare 6-channel industrial relay module with isolation/protection, exact board revision and input specifications to be confirmed against its datasheet.
- Magnetic float level switch, 2-wire NO/NC.
- 3.3 V–5 V active buzzer module.
- Fan, mist sprayer/pump, and night light controlled by relay channels.
- Isolated AC/mains-presence detector whose **safe low-voltage logic output** is connected to a Pi GPIO. Never connect mains voltage directly to a GPIO.
- Raspberry Pi Active Cooler, adequate power supply, and backup battery/UPS that keeps the Pi and necessary monitoring/control electronics powered after mains loss.
- Optional 5-inch HDMI display for local status/diagnostics.

### 2.1 Fixed Raspberry Pi 40-pin header mapping

Keep the following BCM and physical pin assignments as the **V1 fixed pin map**. Do not casually reassign them in application code. Store them in one central configuration module and document them. If the actual relay-board datasheet or electrical review proves that a mapping is incompatible, STOP and request a reviewed hardware revision; do not silently change pins.

| Physical pin | BCM / pin type | Assignment |
|---:|---|---|
| 1 | 3.3 V power | SHT40 VCC, only if the sensor breakout accepts 3.3 V |
| 3 | GPIO2 / I²C SDA | SHT40 SDA |
| 5 | GPIO3 / I²C SCL | SHT40 SCL |
| 6 | GND | SHT40 ground |
| 9 | GND | Float-switch contact return, if wired for the configured pull-up circuit |
| 15 | GPIO22 / digital input | Float-switch state |
| 29 | GPIO5 / digital output | Relay CH1 — fan |
| 31 | GPIO6 / digital output | Relay CH2 — mist sprayer/pump |
| 33 | GPIO13 / digital output | Relay CH3 — night light |
| 36 | GPIO16 / digital output | Relay CH4 — reserved, remain OFF |
| 35 | GPIO19 / digital output | Relay CH5 — reserved, remain OFF |
| 38 | GPIO20 / digital output | Relay CH6 — reserved, remain OFF |
| 32 | GPIO12 / digital output | Active buzzer control, use a suitable transistor/driver if required by buzzer current |
| 37 | GPIO26 / digital input | Isolated mains-presence detector logic output |
| 39 | GND | Isolated detector logic ground, only if appropriate for the detector output design |
| 27, 28 | HAT ID / reserved | Keep reserved for HAT identification; do not allocate to application functions |

Additional connections:

- USB camera: Raspberry Pi USB-A port; discover via V4L2/udev rather than assuming `/dev/video0` never changes.
- AI HAT+: install using the official Raspberry Pi mounting/PCIe ribbon arrangement and supplied GPIO stacking header. Confirm mechanical clearance and cooling.
- Active Cooler: use the Pi 5 fan connector and official/compatible supported control method; do not consume a general GPIO for it without explicit hardware reason.
- Optional HDMI display: micro-HDMI output; power it according to its own specification.
- Do not use GPIO 5 V pins to power 220 V loads or assume the relay board coil supply can be powered from the Pi. Use the relay board's specified separate supply and verify input logic compatibility.

**GPIO safety assumptions that must be confirmed at commissioning:** exact relay board model/revision, per-channel active-high/active-low behavior, relay default state on power-up, isolation, trigger voltage/current, separate supply, and contact ratings. Store validated channel polarity in configuration. Never assume active-low or active-high by guess. At boot, the hardware and wiring must bias load circuits to OFF before Python begins. Prefer normally-open contacts where appropriate and use correctly rated protection/contactor arrangements for inductive fan/pump loads.

### 2.2 Honest hardware observability

The software must distinguish these states, not treat all of them as “OK”:

- `PASS`: tested successfully with observable evidence.
- `FAIL`: test failed or measured value is invalid.
- `MISSING`: a device/interface expected to be enumerated is not present.
- `UNVERIFIABLE`: there is no feedback hardware to verify physical operation.
- `OPTIONAL_MISSING`: optional part is unavailable and the configured system policy permits operation without it.
- `NOT_CHECKED`: not yet tested.

Examples:

- SHT40: check I²C address/communication and validate temperature/humidity readings.
- USB camera: enumerate the expected UVC device by stable identity, open it, read a frame, validate frame shape and freshness.
- Hailo: run the appropriate runtime/device check and load/validate the configured compiled model.
- Float switch: read configured input state and check that transitions are plausible where safely testable. A simple two-wire NO/NC switch may not let software distinguish an open switch from a broken wire. If wire-break detection is a requirement, request an end-of-line/supervised circuit or other suitable hardware; do not pretend it can be detected from a plain input.
- Relay board: GPIO access and output writes do not prove that the relay contacts physically switched. If physical verification is required, add auxiliary contacts or suitable load/current feedback. Without this, label it `UNVERIFIABLE` after control-path checks.
- Buzzer: writing a GPIO does not prove that it made sound. Verify using external feedback if required; otherwise record `COMMAND_SENT/UNVERIFIABLE`.
- Fan/pump/light: a relay command does not prove the load is operating. Add contact/current/flow feedback if such monitoring is a requirement.
- Mains detector: validate its low-voltage interface, logic polarity, debounce, and startup/plausibility behavior. Loss of its signal is not automatically equivalent to loss of mains unless the detector circuit design supports that interpretation.

Do not energize actual mains loads during automated boot self-tests. Use software/dry-run tests and safe low-voltage commissioning tests. Any physical load tests must be deliberate, supervised, and performed with the correct electrical protections.

## 3. Required software stack

Use the following baseline unless a verified compatibility issue forces a documented adjustment:

- OS: current supported **64-bit Raspberry Pi OS Lite** release compatible with the chosen AI HAT+ software.
- Language: Python 3, using a virtual environment and a pinned/reproducible dependency file.
- GPIO: GPIO Zero with the Pi 5-compatible `lgpio` pin factory, or a clearly justified maintained alternative verified for Raspberry Pi 5. Do not mix two GPIO libraries to control the same pins.
- Sensor: appropriate SHT4x/Sensirion I²C driver.
- Camera: Linux V4L2 and GStreamer for robust UVC capture, with OpenCV only where needed for image operations.
- AI: official Raspberry Pi/Hailo stack and HailoRT-compatible API for the exact **AI HAT+ 26 TOPS / Hailo-8** board. Install the correct package family for this board; do not install Hailo-8L/AI Kit or Hailo-10H-only components by assumption. Confirm the installation with the official tool such as `hailortcli fw-control identify` and validate runtime compatibility.
- Tracking: ByteTrack or another explicitly supported tracker, isolated behind a tracker interface.
- Local persistence: SQLite through Python's `sqlite3`, using WAL mode, foreign keys, transactions, busy timeout, schema migrations, and durable outbox records.
- Cloud: AWS IoT Device SDK for Python v2 (`awsiotsdk`) using MQTT over TLS and a per-device X.509 certificate/private key. Use QoS 1 for important events, but understand that MQTT `PUBACK` only acknowledges broker receipt—not necessarily successful database persistence in the cloud.
- Service manager: `systemd` with least-privilege service user, restart policy, startup ordering where appropriate, and journal logging.
- Logging: Python `logging` with structured, timestamped records; journald for process logs and SQLite for operational events/telemetry.
- Tests: `pytest`, mocks/fakes for GPIO, camera, I²C, Hailo, database and MQTT, plus separate hardware-in-the-loop acceptance tests.

Do not add Docker, Kubernetes, Node.js, or unrelated frameworks to the Pi application unless a documented requirement justifies them. Keep the Pi software small enough for 4 GB RAM and continuous service operation.

## 4. Required services/modules and ownership

Create a modular Python repository similar to this layout (you may improve names while preserving the separation of responsibilities):

```text
sericulture-edge/
├── pyproject.toml                 # or requirements + lock/pins
├── README.md
├── .env.example                  # placeholders only, no secrets
├── config/
│   ├── device.example.yaml
│   └── logging.yaml
├── systemd/
│   └── sericulture.service
├── app/
│   └── sericulture/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── state_machine.py
│       ├── boot_supervisor.py
│       ├── diagnostics.py
│       ├── health.py
│       ├── event_types.py
│       ├── hardware/
│       │   ├── pin_map.py
│       │   ├── relay_manager.py
│       │   ├── sht40.py
│       │   ├── float_switch.py
│       │   ├── mains_monitor.py
│       │   ├── buzzer.py
│       │   └── camera_uvc.py
│       ├── vision/
│       │   ├── pipeline.py
│       │   ├── hailo_inference.py
│       │   ├── tracking.py
│       │   └── movement_analysis.py
│       ├── control/
│       │   ├── decision_engine.py
│       │   ├── safety_layer.py
│       │   └── actuator_policy.py
│       ├── storage/
│       │   ├── database.py
│       │   ├── migrations.py
│       │   └── event_repository.py
│       ├── cloud/
│       │   ├── iot_client.py
│       │   ├── outbox_sync.py
│       │   └── payloads.py
│       └── monitoring/
│           ├── watchdog.py
│           └── metrics.py
└── tests/
    ├── unit/
    ├── integration/
    ├── fault_injection/
    └── hardware_acceptance/
```

**GPIO ownership rule:** exactly one hardware-control component/process is allowed to own and write actuator GPIOs. Vision, cloud sync, and UI/diagnostic modules must request actions through the safety/decision layer rather than writing relay pins directly. This prevents conflicting relay writes.

The normal Python application must not start automatic-control or AI workloads until the boot supervisor has validated the required components and safety conditions. A minimal supervisor/diagnostic path must still run to identify and report faults; interpret “do not run programs while components are missing” as “do not start the main AI/automatic-control workload.” If the supervisor itself cannot establish safe GPIO defaults, remain locked out and raise the highest-priority alert possible.

## 5. Safe-boot state machine — mandatory

Implement an explicit state machine, persist state transitions to logs, and do not use ad-hoc boolean checks scattered through the code.

Required states:

- `BOOTING`
- `SAFE_OUTPUTS`
- `DIAGNOSTICS`
- `FAULT_LOCKOUT`
- `SELF_TEST`
- `MANUAL_READY`
- `AUTO_READY`
- `MAINS_LOST`
- `OFFLINE_OPERATION`
- `SHUTTING_DOWN`
- `RECOVERY_PENDING`

Expected boot sequence:

1. OS starts and systemd launches the minimal supervisor.
2. Load and validate configuration. If configuration is missing/invalid, do not start automatic control.
3. Immediately establish relay safe-OFF state using the verified board polarity and safe hardware bias. CH1–CH6 must be commanded OFF; do not pulse them on initialization. Do not run the vision model or automatic decision logic yet.
4. Initialize SQLite enough to record the boot session and diagnostics. If storage cannot be written, do not silently proceed as if logging works.
5. Verify the GPIO backend and pin access.
6. Check SHT40 communication and reading validity.
7. Discover and open the expected USB UVC camera; acquire a valid frame and verify frame freshness.
8. Verify Hailo device/runtime and load the configured model. Do not infer that a model exists just because Hailo hardware is found.
9. Read the float switch, mains detector, manual/auto state if hardware is available, and other required safety inputs. Check expected states/plausibility without actuating mains loads.
10. Verify disk space, SQLite schema, permissions, time state, and that credentials/configuration needed for enabled functions are syntactically valid. Cloud connectivity itself must not be a boot prerequisite for local operation.
11. Run self-tests that are safe without turning on real loads.
12. If any **required** test fails, transition to `FAULT_LOCKOUT`, command all relays OFF, identify the faulty/missing/unverifiable required component, log it, play its configured beep pattern, and repeat diagnostics on a bounded retry interval. No automatic control or AI workload may run in lockout.
13. If required tests pass, enter `MANUAL_READY` or `AUTO_READY` according to the physical selector and validated policy. Automatic control is never enabled simply because the process restarted.
14. Record boot ID, software version, model version, diagnostic results, fault list and state transition.

Only components classified `OPTIONAL_MISSING` by explicit configuration may be absent without lockout. Do not make the camera, SHT40, Hailo/model, safety inputs, SQLite, or relay-control path optional by default.

## 6. Fault alerts and active-buzzer behavior

Use the active buzzer to signal fault categories through distinctive **beep patterns** (an active buzzer typically produces one pitch; do not promise multiple pitches unless the hardware is changed to a passive buzzer or a tone-capable device).

Make beep patterns configurable and documented. Example defaults:

| Fault category | Example pattern |
|---|---|
| SHT40 missing/invalid | 1 long beep, pause, repeat |
| Camera missing/not opening | 2 short beeps, pause, repeat |
| Hailo unavailable/model invalid | 3 short beeps, pause, repeat |
| Float/mains safety-input fault | 1 long + 2 short, pause, repeat |
| SQLite/storage fault | 2 long beeps, pause, repeat |
| Configuration/GPIO/relay control-path fault | 4 short beeps, pause, repeat |
| Internet/cloud disconnected only | A low-priority pattern no more than once per configured interval, or visual/log alert only; never make it sound like a critical hardware failure |

Do not block the whole program with a long `sleep()` just to sound a pattern. Use a non-blocking state/timer loop or a dedicated buzzer worker. Prioritize critical faults, rate-limit repeats, and prevent buzzer faults from crashing the supervisor. Keep outputs safe if the buzzer cannot be used.

## 7. Relay control and safety layer

Map relay channels permanently:

| Relay | Fixed GPIO | Physical pin | Controlled load |
|---|---:|---:|---|
| CH1 | GPIO5 | 29 | Fan |
| CH2 | GPIO6 | 31 | Mist sprayer/pump |
| CH3 | GPIO13 | 33 | Night light |
| CH4 | GPIO16 | 36 | Spare, OFF by default |
| CH5 | GPIO19 | 35 | Spare, OFF by default |
| CH6 | GPIO20 | 38 | Spare, OFF by default |

All output changes must pass through a single safety API, for example `request_actuator_state(load, desired_state, reason)`. The API must validate:

- The system is in an allowed state.
- The required sensors are healthy and fresh.
- The float-switch/mist water safety rule allows misting.
- The mains-presence state permits the operation.
- Manual override/selector has priority over automatic requests.
- Configured temperature/humidity thresholds, hysteresis and persistence timers are valid.
- Maximum run time, minimum ON/OFF time, and cooldown restrictions are respected where configured.
- A fault, stale input, reboot recovery or lockout prevents unsafe switching.

Default states for every load are OFF. A missing/invalid SHT40 reading must never be replaced with an arbitrary value that might turn on a load. Avoid relay chatter using hysteresis, debounce, minimum run intervals, and persistence windows.

Do not invent sericulture threshold values. Place target temperature/humidity, hysteresis, inactivity windows, light schedule and permitted actuator rules in validated configuration. Mark provisional values explicitly and require domain/commissioning approval before enabling automatic mode.

### Mains power loss

- The Pi is expected to stay powered through a correctly sized UPS/battery while mains status is monitored.
- On a stable mains-loss signal, promptly request OFF for fan, mist sprayer and night light; also turn off unused controlled outputs.
- Record a `MAINS_LOST` event locally, including detector state, timestamp, commands, and any available feedback.
- Do not attempt a normal automatic restart of loads when mains returns. Debounce/stabilize the detector, re-run relevant safety checks, record `MAINS_RESTORED`, and require the configured recovery policy/manual approval before returning to automatic operation.
- On UPS low-battery/shutdown warning (if observable), flush/commit SQLite, stop AI/camera gracefully, keep outputs in the safe state and shut down cleanly. Use an independent hardware watchdog/failsafe or electrical interlock where software cannot guarantee actuator shutdown.
- Software is not a substitute for correctly rated contactors/relays, fuses/MCB/RCBO, enclosure, electrical isolation, and qualified 220 V wiring.

### Relay failure

A GPIO call cannot establish whether the physical relay contacts switched. Use auxiliary-contact/current/flow feedback if actual operation confirmation is needed. When feedback is available and disagrees with the requested state, raise a critical fault, enter the configured safe state, log the mismatch, and do not retry switching indefinitely.

## 8. Sensor, camera, and AI requirements

### SHT40

- Read through I²C using a supported driver.
- Check presence, communication exceptions, reading ranges, NaN/infinite values, repeated/stuck readings, and staleness.
- Use monotonic time for intervals and UTC wall-clock timestamps for persisted events.
- Handle temporary read errors with bounded retries/backoff. After a defined failure threshold, mark the sensor unhealthy and apply the safety policy.
- Never crash the full supervisor for a single read error.

### USB UVC camera

- Find the expected device using a stable udev path/serial or validated identity, not only `/dev/video0`.
- Validate that it is the intended camera, can open, returns correctly shaped frames, and produces fresh frames.
- Use GStreamer/V4L2 where suitable; release the camera safely on shutdown/error and reopen using bounded exponential backoff.
- Do not interpret missing frames as silkworm inactivity. If frames become stale or camera disconnects, suspend movement/inactivity outputs and mark vision unavailable.
- Avoid unnecessary raw-frame disk writes. Save only selected frames/clips if enabled, and store the media path/S3 object reference in metadata rather than embedding media in SQLite or MQTT telemetry.

### Hailo AI / model

- Verify Hailo runtime/device availability and the configured model file format/compatibility before entering ready state.
- Model path, model version, class names, preprocessing configuration, input size, thresholds and expected outputs must be configurable and validated.
- Distinguish hardware unavailable, model missing, model load failure, inference timeout, malformed output, and low-confidence/no-detection results.
- Add inference timeout and bounded recovery. Do not spin forever or reboot the Pi for every single inference error.
- If inference becomes unavailable, mark AI as degraded; block decisions that require AI, and apply the approved local fallback/safe policy.
- Do not claim the silkworm/Jwara/disease classifier is accurate without a validated, domain-labeled dataset and documented metrics. The detector/tracker and event thresholds must be treated as configurable until validated.

### Tracking and movement/inactivity

- Use timestamps and track IDs; handle dropped/out-of-order frames and track resets explicitly.
- Calculate movement/velocity only from adequate valid observations.
- Inactivity must be based on a domain-approved duration and valid tracked observations, not a single empty frame or camera outage.
- Log input freshness, frame sequence, track counts, movement scores, and inference/model status as appropriate without flooding the database.

## 9. Local SQLite logging and offline-first behavior

SQLite is the local source of truth for unsynchronized telemetry/events. **Every event must be committed locally before it is queued for cloud delivery.** Cloud failure must not block safe local control.

Use a durable outbox pattern with at least these fields:

- `event_id`: UUID generated on the Pi, globally unique and immutable.
- `device_id` and `farm_id`/`tenant_id` from configuration, never guessed from a topic or client input.
- `event_type`, `schema_version`, UTC event timestamp, monotonic/boot-session context when useful.
- `payload_json` with validated values.
- `sync_status` (e.g. `PENDING`, `IN_FLIGHT`, `ACKED`, `RETRY`).
- `attempt_count`, `next_attempt_at`, `last_error`, `created_at`, `acked_at`.

Recommended local tables (use migrations):

- `telemetry`: temperature, humidity, sensor quality and timestamp.
- `device_events`: boot, reboot, startup diagnostics, mains outage/restore, component states, connectivity, software errors and recovery events.
- `actuator_events`: requested state, reason/rule, physical feedback if available, result, timestamps.
- `ai_events`: model version, detections/classification, confidence, movement/velocity/inactivity metrics and inference status.
- `health_snapshots`: CPU/RAM/disk/temp/uptime/service/camera/Hailo/SQLite status at a rate-limited interval.
- `outbox`: pending cloud deliveries and acknowledgment/retry state.
- `schema_migrations`: database schema version.

Implementation rules:

1. Set appropriate SQLite pragmas (including WAL mode, foreign keys, and busy timeout) and use parameterized statements.
2. Use transactions for writes that need to succeed together. Handle disk full, I/O errors, locked database, schema migration failure, corruption, and permission problems explicitly.
3. Keep the database under an appropriate directory such as `/var/lib/sericulture/`, not the source tree or `/tmp`; set owner and restrictive permissions.
4. Check available disk space and database growth. Define retention/rotation/export behavior without deleting unsynced records. Never silently drop the oldest pending data to make space.
5. Batch high-frequency sensor samples sensibly to limit writes, but never delay safety-critical events. Do not allow a full queue to block the safety path indefinitely.
6. Use schema migrations that are repeatable and backed up before destructive changes. Never auto-delete data because a migration fails.
7. If SQLite cannot persist a required safety/fault event, raise a critical storage fault and apply a documented conservative policy. Keep hardware safety logic working as far as possible; avoid a secondary failure that leaves relays energized.
8. Use event IDs and idempotent cloud writes to prevent duplicate logical records when MQTT QoS 1 causes redelivery.
9. Do not log secrets, private keys, certificate contents, passwords, or sensitive raw media.

### Required logs / telemetry fields

At minimum record:

- Device ID, farm/tenant ID, boot ID, software version, model version and schema version.
- Timestamp in UTC; uptime/monotonic context when relevant.
- Temperature and humidity, validity/status and sampling time.
- Float-switch/water-level state and health.
- Mains-present/lost/restored state, UPS/battery state if hardware exposes it.
- Fan, mist sprayer and night-light commanded status; relay command reason; actual feedback status if hardware provides it.
- AI inference/model status; detections/predictions and confidence where relevant; tracked silkworm count, movement/velocity/inactivity results and algorithm version.
- Camera connect/disconnect, frame freshness, capture errors and FPS/processing metrics.
- Hailo availability/inference errors.
- Boot/self-test results and each component's `PASS/FAIL/MISSING/UNVERIFIABLE` result.
- State-machine transitions, safety interlock activation, manual override, and operator-initiated changes where available.
- Internet status, AWS IoT connect/disconnect, sync batch size, success/failure/ack IDs, retry count and last cloud error.
- Application exceptions, service restarts, watchdog actions, disk space, CPU temperature and health snapshots.

Do not publish a new telemetry message for every video frame. Keep video and images out of MQTT messages; if optional cloud media uploads are implemented, upload selected media to S3 and sync only the object key/metadata.

## 10. AWS IoT Core connectivity and synchronization

Implement secure outbound MQTT communication to AWS IoT Core. Do not connect directly from the Pi to RDS, and do not store database credentials on the Pi.

### Security

- Use a unique AWS IoT Thing identity and X.509 certificate per device.
- Store private key and certificate under a root-/service-owned directory with restrictive file permissions; never commit them to source control.
- Configure least-privilege IoT policy so the device can connect with its own client ID, publish only under its device-specific topics, and subscribe only to its permitted acknowledgment/configuration topics.
- Use TLS, verify server certificates, validate endpoint/region configuration, and handle expired/revoked certificates distinctly from temporary network outage.
- Never embed AWS access keys or RDS credentials in source code or `.env.example`.

### Example topics (make root and IDs configurable)

```text
novabion/sericulture/v1/devices/{device_id}/telemetry
novabion/sericulture/v1/devices/{device_id}/events
novabion/sericulture/v1/devices/{device_id}/health
novabion/sericulture/v1/devices/{device_id}/acks
novabion/sericulture/v1/devices/{device_id}/commands
```

Use JSON payloads with `schema_version`, `event_id`, `device_id`, `farm_id`, `event_type`, `timestamp_utc`, `software_version`, and `payload`. Validate all payloads and cap payload size. Never place images/video in MQTT telemetry.

### Connection and retries

- The app must boot safely and operate locally without the internet or an AWS endpoint.
- Reconnect in the background with exponential backoff and jitter; cap retry intervals and reset backoff after a stable connection.
- Avoid tight loops, blocking callbacks, unbounded thread creation, and log floods.
- Keep data queued in SQLite during connection errors. Never mark an event synced just because `publish()` was called or because the MQTT broker returned `PUBACK`.
- Use QoS 1 for important messages and accept that delivery can be repeated. Cloud ingestion must be idempotent using `event_id`.
- Implement an **application-level acknowledgment**: the AWS-side pipeline should acknowledge a given `event_id` only after it has durably persisted the event at its intended destination. The Pi subscribes to the allowed ACK topic; only when it receives a matching durable-ingestion acknowledgment does it mark the local outbox record `ACKED`/synced.
- If the cloud ingestion path cannot yet generate a durable ACK, document this as an integration dependency and leave events pending; do not claim end-to-end delivery confirmation.
- Process a bounded number of pending events per batch; support retries, per-event status, and resumption after crash/reboot. Do not lose the queue if power fails mid-sync.
- Handle duplicate ACKs, late ACKs, duplicate publishes, malformed messages, TLS failure, authorization failure, broker disconnect and clock/time-certificate problems.

### Example payload

```json
{
  "schema_version": 1,
  "event_id": "UUID-GENERATED-LOCALLY",
  "device_id": "configured-device-id",
  "farm_id": "configured-farm-id",
  "event_type": "telemetry",
  "timestamp_utc": "ISO-8601-UTC",
  "software_version": "semantic-version-or-build-id",
  "payload": {
    "temperature_c": 28.4,
    "relative_humidity_pct": 84.7,
    "fan_commanded_on": false,
    "mist_commanded_on": false,
    "night_light_commanded_on": true,
    "float_switch_state": "configured-state",
    "mains_present": true,
    "camera_status": "OK",
    "hailo_status": "OK"
  }
}
```

The numbers above are examples only, not control thresholds. Add explicit quality/status fields when readings are invalid; do not encode missing values as zero.

### Cloud-side contract

Document the expected AWS IoT Rule/backend behavior: validate topic identity, validate schema, persist with an idempotent `event_id`, then publish a durable-ingestion ACK to the device-specific ACK topic. Cloud rules may route to Lambda/queue/database, but the Pi must not assume that an IoT Rule or MQTT PUBACK equals a committed RDS row. If media upload to S3 is implemented, upload the file first, then enqueue an event containing the S3 object key and metadata; do not store full video blobs in RDS or MQTT.

## 11. Internet/4G blackout and recovery

During internet loss:

- Keep valid SHT40 sampling and local safety controls running.
- Continue local Hailo inference and camera processing only while those components are healthy and the system is otherwise in a ready state.
- Keep local control decisions deterministic and bounded; never depend on a cloud response for an urgent local safety action.
- Persist measurements, predictions, state changes, alerts and actuator changes to SQLite.
- Mark connectivity as offline, raise a low-priority/logged notification, and retry in background.
- Do not turn off the local system solely because AWS is unavailable.

On recovery:

1. Confirm network reachability and re-establish MQTT/TLS securely.
2. Publish queued records in bounded batches with a rate limit.
3. Wait for per-event durable cloud ACKs before marking rows synchronized.
4. Handle duplicate/incomplete/reordered ACKs idempotently.
5. Record sync completion, failed records and last successful sync time.
6. Resume live telemetry in a controlled manner so backlog transfer does not starve sensor/relay safety work.

## 12. Process supervision and recovery

Provide a hardened systemd unit and install script. The service should:

- Start at boot with appropriate service ordering but must not depend on `network-online.target` to begin local safe diagnostics/control.
- Run as a dedicated non-root service account where possible. Grant only the groups/device permissions needed for GPIO, I²C, video, and Hailo. Use narrowly scoped privileges rather than running the full app as root by default.
- Use restart policy for process crashes, `RestartSec`, reasonable start-rate limits, restrictive filesystem settings where compatible, and journal logging.
- Perform graceful SIGTERM/SIGINT handling: request actuator OFF, stop accepting new work, stop camera/inference, commit SQLite, persist shutdown reason, disconnect MQTT if possible, close GPIO resources and exit.
- Avoid infinite restart loops. If repeated startup failures occur, keep load circuits safe, record/announce failure, and enter lockout/retry policy rather than repeatedly toggling hardware.
- Include a local watchdog/health loop that checks task liveness and timeouts. Do not let a blocked camera/inference/network callback block the safety loop.
- Keep separate timeouts for sensor reads, frame reads, AI inference, SQLite operations and MQTT callbacks.
- Never let a cloud-sync problem crash the main control logic. Never let vision overload block GPIO safety or mains-loss response.

Do not allow multiple services to control relay GPIO. If multiple processes are needed, have one control service own pins and expose a local authenticated IPC/API for other modules to request validated actions.

## 13. Error handling standards

Use typed domain exceptions or explicit error results for recoverable hardware and cloud failures. Apply try/except at module boundaries with logging and recovery, not a blanket `except Exception: pass`.

Each failure path must specify:

1. What is detected and by which evidence.
2. Severity (`INFO`, `WARNING`, `ERROR`, `CRITICAL`).
3. Whether the device enters degraded mode or lockout.
4. Which actuators are commanded OFF and why.
5. Whether the error is retried, retry interval/backoff, and maximum attempt policy.
6. Which local event is persisted.
7. Whether an audible/visual/cloud alert is possible.
8. The condition for recovery and the state transition.
9. What the system does if logging or alerting itself fails.

Never:

- Hide exceptions or report success after a failed action.
- Treat an unavailable camera as inactivity or a missing reading as zero.
- Enable actuators automatically following a restart merely because a previous state was ON.
- Let an AWS outage disable independent local safety controls.
- Claim a relay or passive electrical component is physically verified when no feedback exists.
- Allow an invalid config, stale sensor, invalid clock required for TLS, failed database initialization, or unknown relay polarity to silently fall back to unsafe defaults.

## 14. Configuration requirements

All site/device-specific values belong in validated configuration, not scattered constants. Include a typed config schema and safe example configuration containing placeholders—not real secrets—for:

- `device_id`, `farm_id`/tenant ID, deployment/site name.
- Fixed BCM pin map from this prompt; reject conflicting overrides in production mode.
- Relay polarity and default state, only after electrical validation.
- Float-switch contact type (NO/NC), pull-up/down, expected safe state and debounce interval.
- Mains detector polarity, debounce, plausible state transitions, and outage policy.
- SHT40 reading intervals, validity bounds, and stale-data timeout.
- Camera identity, resolution/frame-rate cap, frame-freshness timeout, GStreamer pipeline options.
- Hailo model path, model/version metadata, input format, thresholds and expected labels.
- Movement/inactivity timings; temperature/humidity thresholds, hysteresis and actuator durations approved by the domain owner.
- Telemetry intervals, log levels, database paths, queue limits, retention strategy and retry settings.
- AWS IoT endpoint, region, topic root, certificate/key paths, client ID, QoS, ACK timeout and batch limits.
- Beep patterns, severity rate limits, display options and optional components.
- Watchdog thresholds, health reporting interval, shutdown policy.

Validate types/ranges and unknown keys. If required configuration is absent or invalid, refuse auto-ready and issue a diagnostic error. Do not silently replace a missing safety setting with an arbitrary value.

## 15. Testing requirements

Provide automated tests that run without real hardware and hardware acceptance procedures for the physical device.

### Unit tests

- Fixed pin map and configuration validation.
- Relay active-high/active-low abstraction and safe-OFF behavior with mocked GPIO.
- State machine transitions and lockout logic.
- SHT40 invalid/stale/exception handling.
- Camera absent, open failure, stale frame, malformed frame, disconnect/reconnect.
- Hailo missing, model file absent, model load failure, timeout, malformed inference output.
- Float switch debounce, NO/NC configuration and invalid configuration.
- Mains-loss debounce, mains restore stabilization, repeated transitions.
- Buzzer patterns do not block other tasks.
- SQLite transaction/retry, migration failure, locked database, disk-full simulation, duplicate event IDs.
- MQTT outage, TLS/auth failure, reconnect, `PUBACK` without durable ACK, durable ACK, duplicate/late ACK, message redelivery.
- Service shutdown and power-loss event recording.
- Stale telemetry is never treated as a valid reading.

### Integration/fault-injection tests

- Boot with all required components available.
- Boot with each required component missing, one at a time.
- Boot with invalid config or unknown relay polarity.
- Boot without internet.
- Internet disappears while ready; local control and logging continue.
- Internet returns after an extended outage; backlog drains without losing records.
- Pi process crashes/restarts while relays were previously commanded ON; loads return to safe OFF and require safety revalidation.
- Mains fails while Pi stays on UPS; outputs are commanded OFF and the outage is persisted.
- UPS low-battery signal triggers orderly safe shutdown, if hardware supports it.
- SQLite read/write error while a mains-loss event occurs.
- Camera disconnect during tracking; no false inactivity event is generated.
- Hailo disappears or inference fails during operation; AI-dependent decisions are blocked.
- Relay feedback disagrees with commanded state if feedback hardware is installed.
- CPU stress and long-duration test; verify bounded memory growth, database growth, thermal monitoring, and responsiveness of safety handling.

### Hardware acceptance tests

Document exact steps, expected signals and rollback for testing GPIO, I²C, camera, Hailo, relay logic, float switch, buzzer, mains detector, UPS, and each load. Test logic first with low-voltage safe loads. Do not switch exposed 220 V circuits during unattended tests.

## 16. Acceptance criteria — do not mark project complete until met

1. The documented BCM/physical pin mapping is used centrally and nowhere else is hardcoded.
2. All controlled outputs enter verified OFF state as early as safely possible, and hardware defaults keep the loads OFF before application code starts.
3. No automatic control or AI workload starts if a required component/configuration check fails.
4. Fault lockout identifies the specific observable fault and uses its configured beep pattern when the buzzer path is available.
5. The software distinguishes verified, missing, failed, optional, and physically unverifiable hardware correctly.
6. A single failing sensor/camera/AI/cloud module does not crash unrelated safety monitoring.
7. Mains loss while the Pi is backed up causes the configured loads to be commanded OFF, and the event is durably logged.
8. Mains restoration never blindly restores the previous ON state; recovery checks run first.
9. When offline, valid local sensing/safety behavior and SQLite logging continue.
10. All outbound records are committed locally before sending.
11. Records remain pending until the defined durable cloud ACK is received; MQTT PUBACK alone is not treated as RDS persistence confirmation.
12. Backlog retries survive service restart and avoid duplicate logical cloud records through idempotent event IDs.
13. Camera gaps/stale frames cannot trigger a false inactivity alert.
14. Invalid/stale temperature/humidity readings cannot cause unsafe automated actuation.
15. The control path does not block on cloud network calls or video inference.
16. Graceful shutdown commands safe states and commits local data, while recognizing that software is not a substitute for hardware fail-safe design.
17. The system has a usable README, hardware wiring notes, pin map, configuration guide, install/update/uninstall steps, service commands, logs/troubleshooting guide, and a recovery procedure.
18. Tests pass under simulation, and real-hardware acceptance tests are documented with results.

## 17. Required deliverables from the coding agent

Deliver all of the following in the repository:

1. Complete, runnable Python source code with modules and type annotations where practical.
2. `README.md` with architecture, prerequisites, hardware assumptions, setup, configuration, run, test and troubleshooting steps.
3. One authoritative fixed pin map file and a readable pin configuration table.
4. Validated example YAML configuration and `.env.example` with placeholders only.
5. SQLite schema and versioned migration system.
6. Safe boot/diagnostic state machine and beep-pattern implementation.
7. Relay safety layer, mains monitor, float-switch monitor, sensor reader and camera/Hailo interfaces.
8. Offline outbox and AWS IoT sync implementation including durable-ACK contract and mock mode.
9. systemd unit, least-privilege user/group setup instructions and install/update scripts.
10. Unit, integration, and fault-injection tests using simulated hardware.
11. Manual hardware acceptance checklist, including missing-device tests and mains-loss/UPS test steps.
12. Example structured logs and sample SQLite rows/payloads.
13. A table of **what is verified by software vs. what needs external feedback hardware**.
14. A known-limitations section: do not claim verified performance, validated AI accuracy, load feedback, or durable cloud persistence until measured/tested.

### Implementation workflow

Work in stages and report each stage's test results:

- Stage A: clarify/document unresolved hardware electrical details, verify OS/Hailo software compatibility, and provide the architecture and pin map.
- Stage B: implement configuration, state machine, logging and GPIO simulation; pass unit tests without energizing hardware.
- Stage C: implement safe hardware interfaces and diagnostics; test with real hardware, no mains loads.
- Stage D: implement camera/Hailo inference and movement tracking; measure frame rate, CPU/RAM, temperatures and frame freshness.
- Stage E: implement SQLite outbox and simulated MQTT/ACK behavior; prove no records are lost through restarts/outages.
- Stage F: implement AWS IoT Core connection, certificate permissions, cloud-side ACK integration contract and end-to-end sync test.
- Stage G: perform supervised hardware acceptance tests for relay logic, float switch, mains detector, UPS and actual loads with proper protection.
- Stage H: run a long-duration soak test and deliver the final documentation and known limitations.

At every stage, keep actuator defaults safe. Never skip tests because the code “looks correct.” Do not call the system production-ready until the acceptance criteria and supervised hardware tests are complete.

## 18. Official technical references

Use current official instructions and check compatibility at implementation time rather than pinning old commands blindly:

- Raspberry Pi AI HAT+ hardware and Hailo-8/8L variants: https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html
- Raspberry Pi AI software installation and runtime checks: https://www.raspberrypi.com/documentation/computers/ai.html
- AWS IoT Core MQTT, QoS and delivery semantics: https://docs.aws.amazon.com/iot/latest/developerguide/mqtt.html
- AWS IoT Device SDK for Python v2: https://github.com/aws/aws-iot-device-sdk-python-v2
- GPIO Zero pin-factory documentation: https://gpiozero.readthedocs.io/en/stable/api_pins.html
- Sensirion SHT40 product documentation: https://sensirion.com/products/catalog/SHT40

**Final instruction:** Build for safe, predictable operation on one Raspberry Pi 5 with 4 GB RAM and one USB camera first. Prefer a small implementation with clear safety boundaries over a clever but fragile architecture. Ask for confirmation when an unresolved electrical detail affects safe operation, and never guess it.
