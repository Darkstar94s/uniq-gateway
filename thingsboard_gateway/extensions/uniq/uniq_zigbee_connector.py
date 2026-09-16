# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# UNIQ Custom Zigbee Connector for ThingsBoard Gateway

import json
import logging
import time
from threading import Thread
from typing import Dict, Any

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

from thingsboard_gateway.connectors.connector import Connector
from thingsboard_gateway.gateway.entities.converted_data import ConvertedData
from thingsboard_gateway.gateway.entities.telemetry_entry import TelemetryEntry
from .license_manager import LicenseManager

log = logging.getLogger("UniqZigbeeConnector")

class UniqZigbeeConnector(Connector, Thread):
    def __init__(self, gateway, config: Dict[str, Any], connector_type: str):
        super().__init__()
        self._gateway = gateway
        self._config = config
        self._connector_type = connector_type
        self._id = self._config.get("id", "uniq_zigbee_connector")
        self._name = self._config.get("name", "UNIQ Zigbee Connector")
        
        self.broker_host = self._config.get("broker_host", "127.0.0.1")
        self.broker_port = int(self._config.get("broker_port", 1883))
        self.base_topic = self._config.get("base_topic", "zigbee2mqtt")
        
        self._connected = False
        self._stopped = False
        self.statistics = {"MessagesReceived": 0, "MessagesSent": 0}
        
        # Check license
        self.license_manager = LicenseManager.get_instance()
        self.mqtt_client = mqtt.Client(client_id=f"uniq_zb_{self._id}") if mqtt else None

    def open(self):
        self._stopped = False
        self.start()

    def run(self):
        if not self.license_manager.is_protocol_licensed("zigbee"):
            log.warning("Zigbee protocol is NOT licensed on this UNIQ Hub tier. Connector is in standby.")
            return

        if not self.mqtt_client:
            log.warning("Paho-MQTT not installed. Running in stub mode.")
            self._connected = True
            return

        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        try:
            log.info(f"Connecting to Zigbee broker at {self.broker_host}:{self.broker_port}...")
            self.mqtt_client.connect(self.broker_host, self.broker_port, 60)
            self.mqtt_client.loop_start()
            self._connected = True
        except Exception as e:
            log.error(f"Failed to connect to Zigbee broker: {e}")

    def close(self):
        self._stopped = True
        self._connected = False
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception as e:
                log.error(f"Error disconnecting Zigbee client: {e}")

    def get_id(self):
        return self._id

    def get_name(self):
        return self._name

    def get_type(self):
        return self._connector_type

    def get_config(self):
        return self._config

    def is_connected(self):
        return self._connected

    def is_stopped(self):
        return self._stopped

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("UNIQ Zigbee Connector connected to local mesh bus.")
            client.subscribe(f"{self.base_topic}/#")
        else:
            log.error(f"Zigbee broker connection failed with code: {rc}")

    def _on_message(self, client, userdata, msg):
        if not self.license_manager.is_protocol_licensed("zigbee"):
            return

        topic = msg.topic
        if topic.startswith(f"{self.base_topic}/bridge"):
            return

        device_name = topic.replace(f"{self.base_topic}/", "").strip()
        if not device_name or "/" in device_name:
            return

        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        self.statistics["MessagesReceived"] += 1
        smart_device_name = f"Zigbee - {device_name}"
        
        converted_data = ConvertedData(
            device_name=smart_device_name,
            device_type="Smart Device"
        )

        telemetry_dict = {}
        attributes_dict = {}

        for key, value in payload.items():
            if key in ["state", "brightness", "color_temp", "temperature", "humidity", "occupancy", "contact", "power"]:
                telemetry_dict[key] = value
            elif key in ["linkquality", "battery", "voltage"]:
                attributes_dict[key] = value

        if telemetry_dict:
            converted_data.add_to_telemetry(TelemetryEntry(telemetry_dict, int(time.time() * 1000)))
        if attributes_dict:
            converted_data.add_to_attributes(attributes_dict)

        # Send to ThingsBoard storage queue (guarantees offline persistence)
        self._gateway.send_to_storage(self.get_name(), self.get_id(), converted_data)
        self.statistics["MessagesSent"] += 1

    def on_attributes_update(self, content):
        attrs = content.get("device_attributes", {}) or content.get("client_attributes", {})
        if attrs:
            self.license_manager.update_license_from_cloud(attrs)

    def server_side_rpc_handler(self, content):
        method = content.get("data", {}).get("method")
        params = content.get("data", {}).get("params")
        device = content.get("device", "")
        log.info(f"Zigbee RPC: [{method}] -> Device: [{device}], Params: [{params}]")

        if not self.mqtt_client:
            return {"success": False, "error": "Zigbee broker not connected"}

        raw_device = device.replace("Zigbee - ", "").strip()
        target_topic = f"{self.base_topic}/{raw_device}/set"

        payload = {}
        if method == "setState":
            payload["state"] = "ON" if params in [True, 1, "ON", "true"] else "OFF"
        elif method == "setBrightness":
            payload["brightness"] = int(params)
        elif method == "setColorTemp":
            payload["color_temp"] = int(params)
        elif method == "setLock":
            payload["state"] = "LOCK" if params in [True, 1, "LOCK"] else "UNLOCK"
        else:
            payload = params if isinstance(params, dict) else {"state": params}

        self.mqtt_client.publish(target_topic, json.dumps(payload))
        return {"success": True, "published": payload}
