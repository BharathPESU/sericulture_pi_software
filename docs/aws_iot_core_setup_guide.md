# AWS IoT Core Configuration & Integration Guide

This guide provides step-by-step instructions to configure **AWS IoT Core** for the **Sericulture Edge-AI Pi 5 Software**, obtain mutual TLS (mTLS) X.509 certificates, set up least-privilege security policies, and configure all environment variables in your `.env` file.

---

## 1. Architectural Overview & Cloud Contract

The Sericulture Pi 5 software operates on an **offline-first local outbox pattern**:

```
+------------------------+      Local ACID Commit      +-------------------------+
| Sensors / Vision / AI  | --------------------------> | SQLite Outbox Table     |
+------------------------+                             | (status: PENDING)       |
                                                       +-------------------------+
                                                                    |
                                                            Drains Batches
                                                                    v
+------------------------+     Publish MQTT (QoS 1)    +-------------------------+
| AWS IoT Core Broker    | <-------------------------- | OutboxSyncWorker        |
+------------------------+                             +-------------------------+
            |
    Cloud Rule / Lambda
            |
            v  Publishes ACK Payload
+------------------------+  novabion/.../devices/.../acks +-------------------------+
| AWS IoT Core Broker    | -----------------------------> | Marks event_id 'ACKED'  |
+------------------------+                                +-------------------------+
```

### MQTT Topics
All topics follow a structured namespace defined by `AWS_IOT_TOPIC_ROOT`:
- **Telemetry:** `${AWS_IOT_TOPIC_ROOT}/devices/${SERICULTURE_DEVICE_ID}/telemetry`
- **Events & Faults:** `${AWS_IOT_TOPIC_ROOT}/devices/${SERICULTURE_DEVICE_ID}/events`
- **Health Snapshots:** `${AWS_IOT_TOPIC_ROOT}/devices/${SERICULTURE_DEVICE_ID}/health`
- **Cloud ACK (Subscribed):** `${AWS_IOT_TOPIC_ROOT}/devices/${SERICULTURE_DEVICE_ID}/acks`

---

## 2. Step-by-Step AWS IoT Core Setup

### Step 1: Find Your AWS IoT Core Data-ATS Endpoint
AWS IoT Core assigns a unique endpoint to your account and region. Run this command using the AWS CLI (or check **AWS IoT Core Console -> Settings -> Device data endpoint**):

```bash
aws iot describe-endpoint --endpoint-type iot:Data-ATS --region ap-south-1
```

**Output:**
```json
{
    "endpointAddress": "a3xxxxxxxxxxxx-ats.iot.ap-south-1.amazonaws.com"
}
```
> [!NOTE]
> Always use the **ATS (Amazon Trust Services)** endpoint (`*-ats.iot.*.amazonaws.com`), as legacy Symantec root certificates have been deprecated.

---

### Step 2: Create a Thing in AWS IoT Core
1. Open the [AWS IoT Core Console](https://console.aws.amazon.com/iot/).
2. Navigate to **Manage** -> **All devices** -> **Things**.
3. Click **Create things** -> **Create single thing**.
4. Configure Thing attributes:
   - **Thing name:** `SERI-PI5-001` *(must match your `SERICULTURE_DEVICE_ID`)*
   - **Device Shadow:** *No shadow* (or Named Shadow if desired).
   - **Attributes (Optional):**
     - `farm_id`: `FARM-KAR-042`
     - `site_name`: `Rearing-Shed-A`
5. Click **Next**.

---

### Step 3: Generate & Download X.509 Certificates
1. In the **Configure device certificate** screen, select:
   - **Auto-generate a new certificate (recommended)**.
2. Click **Next**.
3. On the **Download certificates and keys** page, download the following 3 files immediately:
   - **Device certificate:** e.g., `xxxxxx-certificate.pem.crt`
   - **Private key file:** e.g., `xxxxxx-private.pem.key`
   - **RSA 2048 bit root CA:** Download **Amazon Root CA 1** directly from:
     ```bash
     curl -o AmazonRootCA1.pem https://www.amazontrust.com/repository/AmazonRootCA1.pem
     ```
4. Click **Activate** to activate the certificate.

> [!WARNING]
> The private key can only be downloaded once. Store it in a secure location.

---

### Step 4: Create the Least-Privilege IoT Policy
1. In the AWS IoT Console, go to **Manage** -> **Security** -> **Policies**.
2. Click **Create policy**.
3. Name: `SericultureEdgeDevicePolicy`.
4. Switch to the **JSON** editor and paste the policy below (replace `<REGION>` and `<ACCOUNT_ID>` with your AWS region and 12-digit Account ID):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iot:Connect",
      "Resource": "arn:aws:iot:<REGION>:<ACCOUNT_ID>:client/${iot:Connection.Thing.ThingName}"
    },
    {
      "Effect": "Allow",
      "Action": "iot:Publish",
      "Resource": [
        "arn:aws:iot:<REGION>:<ACCOUNT_ID>:topic/novabion/sericulture/v1/devices/${iot:Connection.Thing.ThingName}/telemetry",
        "arn:aws:iot:<REGION>:<ACCOUNT_ID>:topic/novabion/sericulture/v1/devices/${iot:Connection.Thing.ThingName}/events",
        "arn:aws:iot:<REGION>:<ACCOUNT_ID>:topic/novabion/sericulture/v1/devices/${iot:Connection.Thing.ThingName}/health"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "iot:Subscribe",
      "Resource": "arn:aws:iot:<REGION>:<ACCOUNT_ID>:topicfilter/novabion/sericulture/v1/devices/${iot:Connection.Thing.ThingName}/acks"
    },
    {
      "Effect": "Allow",
      "Action": "iot:Receive",
      "Resource": "arn:aws:iot:<REGION>:<ACCOUNT_ID>:topic/novabion/sericulture/v1/devices/${iot:Connection.Thing.ThingName}/acks"
    }
  ]
}
```

> [!TIP]
> The policy uses `${iot:Connection.Thing.ThingName}`, which automatically restricts the certificate to only publish and subscribe to its own device ID topics.

5. Click **Create**.
6. Attach this policy to your downloaded certificate:
   - Go to **Manage** -> **Security** -> **Certificates**.
   - Select your certificate -> **Actions** -> **Attach policy** -> Select `SericultureEdgeDevicePolicy`.
7. Attach the certificate to the Thing:
   - Select your certificate -> **Actions** -> **Attach to thing** -> Select `SERI-PI5-001`.

---

## 3. Provisioning the Raspberry Pi

### Step 5: Transfer Certificates to the Raspberry Pi
On your Raspberry Pi, create the secure certificate directory and copy the files:

```bash
# Create directory
sudo mkdir -p /etc/sericulture/certs

# Copy the 3 downloaded files
# (Renamed for clarity):
# 1. Device certificate -> /etc/sericulture/certs/device.cert.pem
# 2. Private key        -> /etc/sericulture/certs/device.private.key
# 3. Amazon Root CA 1   -> /etc/sericulture/certs/AmazonRootCA1.pem

# Apply restrictive permissions
sudo chown -R sericulture:sericulture /etc/sericulture/certs
sudo chmod 700 /etc/sericulture/certs
sudo chmod 600 /etc/sericulture/certs/*
```

---

### Step 6: Create and Populate Your `.env` File
In your repository (for local testing) or at `/etc/sericulture/sericulture.env` (for systemd service), create the `.env` file:

```bash
cp .env.example .env
nano .env
```

Set the values:

```bash
# ------------------------------------------------------------------------------
# 1. Device & Farm Identity
# ------------------------------------------------------------------------------
SERICULTURE_DEVICE_ID=SERI-PI5-001
SERICULTURE_FARM_ID=FARM-KAR-042
SERICULTURE_SITE_NAME=Rearing-Shed-A

# ------------------------------------------------------------------------------
# 2. AWS IoT Core Connection & Endpoints
# ------------------------------------------------------------------------------
AWS_IOT_ENABLED=true
AWS_IOT_ENDPOINT=a3xxxxxxxxxxxx-ats.iot.ap-south-1.amazonaws.com
AWS_IOT_REGION=ap-south-1
AWS_IOT_CLIENT_ID=SERI-PI5-001

# ------------------------------------------------------------------------------
# 3. AWS IoT Core X.509 TLS Certificates
# ------------------------------------------------------------------------------
AWS_IOT_CERT_PATH=/etc/sericulture/certs/device.cert.pem
AWS_IOT_KEY_PATH=/etc/sericulture/certs/device.private.key
AWS_IOT_ROOT_CA_PATH=/etc/sericulture/certs/AmazonRootCA1.pem

# ------------------------------------------------------------------------------
# 4. AWS IoT MQTT Topics & Outbox Sync Options
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

Lock down permissions:
```bash
chmod 600 .env
```

---

## 4. Cloud Ingestion & Durable Acknowledgment Backend

To satisfy the durable outbox contract, your cloud backend (e.g. AWS IoT Rule triggering an AWS Lambda function or AWS DynamoDB stream) must send an acknowledgment payload back on the ACK topic:

- **ACK Topic:** `novabion/sericulture/v1/devices/{device_id}/acks`
- **Expected Payload:**
```json
{
  "event_id": "97e68cfb-6f81-4475-9ab7-c93d9a1f2679",
  "device_id": "SERI-PI5-001",
  "status": "ACCEPTED",
  "timestamp_utc": "2026-10-10T07:30:00Z"
}
```

### Sample AWS Lambda Acknowledger (Python 3.12)
```python
import json
import boto3
from datetime import datetime, timezone

iot_client = boto3.client('iot-data')

def lambda_handler(event, context):
    # Event ingested from MQTT rule: SELECT * FROM 'novabion/sericulture/v1/devices/+/+'
    event_id = event.get('event_id')
    device_id = event.get('device_id')
    
    if not event_id or not device_id:
        return
    
    # 1. Process event (store in Timestream, DynamoDB, or S3)
    # ...
    
    # 2. Publish Durable ACK back to device
    ack_topic = f"novabion/sericulture/v1/devices/{device_id}/acks"
    ack_payload = {
        "event_id": event_id,
        "device_id": device_id,
        "status": "ACCEPTED",
        "timestamp_utc": datetime.now(timezone.utc).isoformat()
    }
    
    iot_client.publish(
        topic=ack_topic,
        qos=1,
        payload=json.dumps(ack_payload)
    )
```

---

## 5. End-to-End Testing & Verification

1. **Test in AWS IoT Core MQTT Client:**
   - Go to AWS IoT Console -> **Test** -> **MQTT test client**.
   - Subscribe to: `novabion/sericulture/v1/devices/#`.
2. **Start the Controller on Raspberry Pi:**
   ```bash
   python3 -m sericulture.main --config config/device.example.yaml --auto-ready
   ```
3. **Verify:**
   - Watch messages arrive on `.../telemetry` and `.../health`.
   - Publish a mock ACK message back on `novabion/sericulture/v1/devices/SERI-PI5-001/acks` with the event's `event_id`.
   - Verify in the Pi logs:
     ```
     [INFO] [sericulture.cloud.outbox_sync] Received durable cloud ACK for event: 97e68... (status: ACCEPTED)
     ```
   - Verify the event in local SQLite outbox is updated from `IN_FLIGHT` to `ACKED`.
