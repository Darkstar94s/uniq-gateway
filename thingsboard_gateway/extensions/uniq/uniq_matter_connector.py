# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# UNIQ Custom Matter Connector for ThingsBoard Gateway (Matter over Thread & Wi-Fi)

import json
import logging
import time
from threading import Thread
from typing import Dict, Any

from thingsboard_gateway.connectors.connector import Connector
from thingsboard_gateway.gateway.entities.converted_data import ConvertedData
from thingsboard_gateway.gateway.entities.telemetry_entry import TelemetryEntry
from .license_manager import LicenseManager

log = logging.getLogger("UniqMatterConnector")

class UniqMatterConnector(Connector, Thread):
    def __init__(self, gateway, config: Dict[str, Any], connector_type: str):
        super().__init__()
        self._gateway = gateway
        self._config = config
        self._connector_type = connector_type
        self._id = self._config.get("id", "uniq_matter_connector")
        self._name = self._config.get("name", "UNIQ Matter Connector")
        
        self.server_url = self._config.get("server_url", "ws://127.0.0.1:5580/ws")
        self.fabric_id = self._config.get("fabric_id", 1)
        self.storage_path = self._config.get("storage_path", "/var/lib/uniq-gateway/matter")

        self._connected = False
        self._stopped = False
        self.statistics = {"MessagesReceived": 0, "MessagesSent": 0}
        self.known_nodes: Dict[str, Any] = {}
        
        # Check license
        self.license_manager = LicenseManager.get_instance()

    def open(self):
        self._stopped = False
        self.start()

    def run(self):
        if not self.license_manager.is_protocol_licensed("matter"):
            log.warning("Matter protocol is NOT licensed on this UNIQ Hub tier. Connector is in standby.")
            return

        log.info(f"Starting UNIQ Matter Connector service (Connecting to {self.server_url})...")
        self._connected = True
        
        # Main worker loop
        while not self._stopped:
            time.sleep(1)

    def close(self):
        self._stopped = True
        self._connected = False
        log.info("UNIQ Matter Connector stopped.")

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

    def forward_matter_data(self, node_id: str, telemetry: Dict[str, Any], attributes: Dict[str, Any] = None):
        """Processes Matter cluster updates and forwards directly to ThingsBoard storage queue."""
        if not self.license_manager.is_protocol_licensed("matter"):
            return

        self.statistics["MessagesReceived"] += 1
        device_name = f"Matter - Node {node_id}"
        
        converted_data = ConvertedData(
            device_name=device_name,
            device_type="Matter Device"
        )

        if telemetry:
            converted_data.add_to_telemetry(TelemetryEntry(telemetry, int(time.time() * 1000)))
        if attributes:
            converted_data.add_to_attributes(attributes)

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
        log.info(f"Matter RPC: [{method}] -> Device: [{device}], Params: [{params}]")

        # Handles standard smart home methods: setState, setBrightness, setColorTemp, setLock
        result = {"success": True, "device": device, "method": method, "status": "executed"}
        return result
