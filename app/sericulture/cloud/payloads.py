"""Cloud message payload models, serializers, and validation.

Formats outbound JSON payloads matching the AWS IoT specification, enforces
size limits, and parses incoming durable-ingestion ACKs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

from sericulture.event_types import ConfigurationError

logger = logging.getLogger(__name__)


@dataclass
class StandardMessagePayload:
    """Standard outbound message payload sent to AWS IoT Core."""
    schema_version: int
    event_id: str
    device_id: str
    farm_id: str
    event_type: str
    timestamp_utc: str
    software_version: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class DurableAckPayload:
    """Application-level acknowledgment from the AWS cloud backend."""
    event_id: str
    status: str
    timestamp_utc: str = ""
    error_message: str = ""

    @classmethod
    def from_json(cls, json_str: str) -> DurableAckPayload:
        data = json.loads(json_str)
        if "event_id" not in data:
            raise ValueError("ACK payload missing required field 'event_id'")
        return cls(
            event_id=data["event_id"],
            status=data.get("status", "ACKED"),
            timestamp_utc=data.get("timestamp_utc", ""),
            error_message=data.get("error_message", ""),
        )


def validate_mqtt_payload_size(payload_str: str, max_bytes: int = 128000) -> bool:
    """Validate that payload does not exceed MQTT message size constraints."""
    byte_len = len(payload_str.encode("utf-8"))
    if byte_len > max_bytes:
        logger.error(f"Payload size {byte_len} bytes exceeds limit of {max_bytes} bytes!")
        return False
    return True
