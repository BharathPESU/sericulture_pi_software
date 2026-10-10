"""AWS IoT Core MQTT client with X.509 TLS and mock capability.

Provides connection management with exponential backoff and jitter, publishes
QoS 1 telemetry/events, and subscribes to durable application ACKs.

SAFETY RULES:
  - Local safety and operations never depend on cloud connection.
  - Offline operation continues indefinitely; data queues in SQLite outbox.
"""

from __future__ import annotations

import logging
import os
import random
import time
from threading import Event, Lock, Thread
from typing import Callable, Protocol

from sericulture.config import CloudConfig
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
)

logger = logging.getLogger(__name__)


class MqttTransport(Protocol):
    """Protocol for MQTT transport implementations."""
    def connect(self) -> bool:
        ...

    def is_connected(self) -> bool:
        ...

    def publish(self, topic: str, payload: str, qos: int) -> bool:
        ...

    def subscribe(self, topic: str, callback: Callable[[str, str], None], qos: int) -> bool:
        ...

    def disconnect(self) -> None:
        ...


class MockMqttTransport:
    """Mock MQTT transport for simulation and offline tests."""

    def __init__(self, auto_ack: bool = False):
        self.connected = False
        self.auto_ack = auto_ack
        self.published_messages: list[tuple[str, str, int]] = []
        self._sub_callbacks: dict[str, list[Callable[[str, str], None]]] = {}
        self.fail_connect = False
        self.fail_publish = False

    def connect(self) -> bool:
        if self.fail_connect:
            self.connected = False
            return False
        self.connected = True
        return True

    def is_connected(self) -> bool:
        return self.connected

    def publish(self, topic: str, payload: str, qos: int) -> bool:
        if not self.connected or self.fail_publish:
            return False
        self.published_messages.append((topic, payload, qos))
        return True

    def subscribe(self, topic: str, callback: Callable[[str, str], None], qos: int) -> bool:
        self._sub_callbacks.setdefault(topic, []).append(callback)
        return True

    def disconnect(self) -> None:
        self.connected = False

    def simulate_incoming_message(self, topic: str, payload: str) -> None:
        """Helper for tests to simulate incoming cloud message/ACK."""
        for sub_topic, cbs in self._sub_callbacks.items():
            if sub_topic == topic or sub_topic.endswith("/#") or sub_topic == "#":
                for cb in cbs:
                    cb(topic, payload)


class RealAwsIotTransport:
    """Real MQTT over TLS connection using awsiotsdk."""

    def __init__(self, config: CloudConfig, client_id: str):
        self._config = config
        self._client_id = client_id
        self._mqtt_connection = None
        self._connected = False

    def connect(self) -> bool:
        try:
            from awscrt import io, mqtt
            from awsiot import mqtt_connection_builder

            event_loop_group = io.EventLoopGroup(1)
            host_resolver = io.DefaultHostResolver(event_loop_group)
            client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

            self._mqtt_connection = mqtt_connection_builder.mtls_from_path(
                endpoint=self._config.endpoint,
                cert_filepath=self._config.cert_path,
                pri_key_filepath=self._config.key_path,
                ca_filepath=self._config.root_ca_path if self._config.root_ca_path else None,
                client_bootstrap=client_bootstrap,
                client_id=self._client_id,
                clean_session=False,
                keep_alive_secs=30,
            )
            connect_future = self._mqtt_connection.connect()
            connect_future.result(timeout=10.0)
            self._connected = True
            logger.info(f"Connected to AWS IoT Core endpoint {self._config.endpoint}")
            return True
        except Exception as e:
            logger.warning(f"AWS IoT connection failed: {e}")
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected

    def publish(self, topic: str, payload: str, qos: int) -> bool:
        if not self._connected or not self._mqtt_connection:
            return False
        try:
            from awscrt import mqtt
            publish_future, _ = self._mqtt_connection.publish(
                topic=topic,
                payload=payload,
                qos=mqtt.QoS.AT_LEAST_ONCE if qos == 1 else mqtt.QoS.AT_MOST_ONCE,
            )
            publish_future.result(timeout=5.0)
            return True
        except Exception as e:
            logger.error(f"MQTT publish failed: {e}")
            return False

    def subscribe(self, topic: str, callback: Callable[[str, str], None], qos: int) -> bool:
        if not self._mqtt_connection:
            return False
        try:
            from awscrt import mqtt

            def on_msg(topic, payload, dup, qos, retain, **kwargs):
                callback(topic, payload.decode("utf-8"))

            sub_future, _ = self._mqtt_connection.subscribe(
                topic=topic,
                qos=mqtt.QoS.AT_LEAST_ONCE if qos == 1 else mqtt.QoS.AT_MOST_ONCE,
                callback=on_msg,
            )
            sub_future.result(timeout=5.0)
            return True
        except Exception as e:
            logger.error(f"MQTT subscribe failed for {topic}: {e}")
            return False

    def disconnect(self) -> None:
        if self._mqtt_connection:
            try:
                disconnect_future = self._mqtt_connection.disconnect()
                disconnect_future.result(timeout=5.0)
            except Exception:
                pass
            self._connected = False


class CloudMqttManager:
    """Manages AWS IoT MQTT client with topic addressing, retries, and backoff."""

    def __init__(
        self,
        config: CloudConfig,
        device_id: str,
        transport: MqttTransport | None = None,
    ):
        self._config = config
        self._device_id = device_id
        self._client_id = config.client_id or device_id
        self._transport = transport or MockMqttTransport()
        self._lock = Lock()
        self._backoff_current_s = config.reconnect_backoff_base_s
        self._on_ack_callbacks: list[Callable[[str], None]] = []

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.AWS_IOT

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected()

    def get_topic(self, channel: str) -> str:
        """Build topic name e.g. novabion/sericulture/v1/devices/{device_id}/{channel}."""
        return f"{self._config.topic_root}/devices/{self._device_id}/{channel}"

    def add_ack_listener(self, cb: Callable[[str], None]) -> None:
        """Register callback for incoming payload string on the ACK topic."""
        self._on_ack_callbacks.append(cb)

    def connect(self) -> bool:
        """Attempt connection and subscribe to device ACK topic."""
        with self._lock:
            if not self._config.enabled:
                return False

            success = self._transport.connect()
            if success:
                self._backoff_current_s = self._config.reconnect_backoff_base_s
                ack_topic = self.get_topic("acks")
                self._transport.subscribe(ack_topic, self._handle_ack_message, qos=self._config.qos)
                logger.info(f"Subscribed to durable ACK topic: {ack_topic}")
                return True
            else:
                # Calculate backoff with jitter
                jitter = random.uniform(0.0, 0.5 * self._backoff_current_s)
                self._backoff_current_s = min(
                    self._config.reconnect_backoff_max_s,
                    (self._backoff_current_s * 2.0) + jitter,
                )
                return False

    def _handle_ack_message(self, topic: str, payload_str: str) -> None:
        logger.debug(f"Received ACK payload on {topic}: {payload_str}")
        for cb in self._on_ack_callbacks:
            try:
                cb(payload_str)
            except Exception as e:
                logger.error(f"Error handling cloud ACK: {e}")

    def publish_event(self, channel: str, payload_str: str) -> bool:
        """Publish message to device topic with QoS."""
        topic = self.get_topic(channel)
        return self._transport.publish(topic, payload_str, qos=self._config.qos)

    def check(self) -> DiagnosticResult:
        """Diagnostic check for AWS IoT connectivity and configuration."""
        if not self._config.enabled:
            return DiagnosticResult(
                component=ComponentId.AWS_IOT,
                status=DiagnosticStatus.OPTIONAL_MISSING,
                message="AWS IoT Core is disabled in configuration.",
            )

        if not self._config.endpoint:
            return DiagnosticResult(
                component=ComponentId.AWS_IOT,
                status=DiagnosticStatus.FAIL,
                message="AWS IoT Core endpoint is not configured.",
            )

        # Check certificates if real transport
        if isinstance(self._transport, RealAwsIotTransport):
            if not os.path.exists(self._config.cert_path):
                return DiagnosticResult(
                    component=ComponentId.AWS_IOT,
                    status=DiagnosticStatus.FAIL,
                    message=f"X.509 Certificate not found at: {self._config.cert_path}",
                )
            if not os.path.exists(self._config.key_path):
                return DiagnosticResult(
                    component=ComponentId.AWS_IOT,
                    status=DiagnosticStatus.FAIL,
                    message=f"Private key not found at: {self._config.key_path}",
                )

        if self.is_connected:
            return DiagnosticResult(
                component=ComponentId.AWS_IOT,
                status=DiagnosticStatus.PASS,
                message=f"Connected to AWS IoT Core ({self._config.endpoint})",
            )
        else:
            return DiagnosticResult(
                component=ComponentId.AWS_IOT,
                status=DiagnosticStatus.OPTIONAL_MISSING,
                message="Not currently connected to AWS IoT Core (offline operation permitted)",
            )

    def disconnect(self) -> None:
        """Disconnect client."""
        with self._lock:
            self._transport.disconnect()
