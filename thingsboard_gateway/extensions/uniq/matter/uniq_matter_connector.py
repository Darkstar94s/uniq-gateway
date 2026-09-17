# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# Production UNIQ Matter Connector for ThingsBoard Gateway 3.8+

import json
import logging
import os
import threading
import time
from threading import Thread
from typing import Dict, Any, Optional

from thingsboard_gateway.connectors.connector import Connector
from thingsboard_gateway.gateway.entities.converted_data import ConvertedData
from thingsboard_gateway.gateway.entities.telemetry_entry import TelemetryEntry

from .matter_client import MatterClient
from .device_mapper import MatterDeviceMapper
from .commission_service import MatterCommissionService

log = logging.getLogger("UniqMatterConnector")


class UniqMatterConnector(Connector, Thread):
    """
    Modular Matter Connector for ThingsBoard Gateway.
    Integrates with matterjs-server over WebSocket, auto-discovers Matter Wi-Fi devices
    and Matter Bridges (isolating bridged endpoints), streams state telemetry via events,
    handles ThingsBoard Cloud RPC, and provides local setup commissioning.
    """

    def __init__(self, gateway, config: Dict[str, Any], connector_type: str):
        super().__init__()
        self._gateway = gateway
        self._config = config
        self._connector_type = connector_type
        self._id = self._config.get("id", "uniq_matter_connector")
        self.name = self._config.get("name", "UNIQ Matter Connector")

        self.server_url = self._config.get("server_url", "ws://127.0.0.1:5580/ws")
        self.reconnect_interval = int(self._config.get("reconnect_interval_sec", 5))

        # Persistent registry file location (stored in gateway storage directory)
        storage_folder = self._config.get("storage_path", "./data")
        os.makedirs(storage_folder, exist_ok=True)
        registry_path = os.path.join(storage_folder, "matter_device_registry.json")

        self.mapper = MatterDeviceMapper(registry_path)

        # Statistics tracker
        self.statistics = {"MessagesReceived": 0, "MessagesSent": 0}
        self._connected = False
        self._stopped = False

        # Matter WebSocket Client
        self.client = MatterClient(
            server_url=self.server_url,
            on_node_event=self._on_matter_node_event,
            on_attribute_event=self._on_matter_attribute_event,
            on_connection_change=self._on_matter_connection_change,
            reconnect_interval_sec=self.reconnect_interval
        )

        # Local Commissioning Microservice
        commission_cfg = self._config.get("commissioning_api", {})
        self.commission_enabled = bool(commission_cfg.get("enabled", True))
        self.commission_port = int(commission_cfg.get("port", 8282))
        self.commission_host = commission_cfg.get("host", "0.0.0.0")

        self.commission_service = MatterCommissionService(
            host=self.commission_host,
            port=self.commission_port,
            matter_connector=self
        ) if self.commission_enabled else None

    # =========================================================================
    # Connector Lifecycle Interfaces
    # =========================================================================

    def open(self):
        log.info(f"Opening UNIQ Matter Connector [{self.name}]...")
        self._stopped = False
        self.start()

    def run(self):
        log.info(f"Starting Matter WebSocket client to {self.server_url}...")
        self.client.start()

        if self.commission_service:
            self.commission_service.start()

        # Heartbeat loop
        while not self._stopped:
            time.sleep(1)

    def close(self):
        log.info(f"Closing UNIQ Matter Connector [{self.name}]...")
        self._stopped = True
        self._connected = False

        if self.commission_service:
            try:
                self.commission_service.stop()
            except Exception as e:
                log.error(f"Error stopping commission service: {e}")

        try:
            self.client.stop()
        except Exception as e:
            log.error(f"Error stopping Matter client: {e}")

        log.info(f"UNIQ Matter Connector [{self.name}] closed successfully.")

    def get_id(self):
        return self._id

    def get_name(self):
        return self.name

    def get_type(self):
        return self._connector_type

    def get_config(self):
        return self._config

    def is_connected(self):
        return self._connected

    def is_stopped(self):
        return self._stopped

    # =========================================================================
    # Matter Events & Synchronization
    # =========================================================================

    def _on_matter_connection_change(self, is_connected: bool):
        self._connected = is_connected
        if is_connected:
            log.info("Matter client connected. Triggering initial nodes synchronization in background thread...")
            threading.Thread(target=self.sync_all_nodes, daemon=True, name="MatterNodeSync").start()

    def sync_all_nodes(self):
        """Fetches all currently commissioned nodes from matterjs-server and registers in ThingsBoard."""
        resp = self.client.get_nodes(timeout=10.0)
        if not resp.get("success"):
            log.warning(f"Could not fetch nodes from Matter controller: {resp.get('error')}")
            return

        nodes = resp.get("result", [])
        if isinstance(nodes, dict):
            # Dict of nodes indexed by node_id
            nodes = list(nodes.values())

        log.info(f"Synchronizing {len(nodes)} Matter nodes with ThingsBoard...")
        for node in nodes:
            self._sync_single_node(node)

    def sync_node_by_id(self, node_id: int):
        """Fetches details of a specific node and synchronizes it."""
        resp = self.client.get_node(node_id, timeout=10.0)
        if resp.get("success"):
            node_data = resp.get("result", {})
            self._sync_single_node(node_data)
        else:
            log.warning(f"Failed to fetch node {node_id} details: {resp.get('error')}")

    def _sync_single_node(self, node_data: Dict[str, Any]):
        try:
            devices = self.mapper.parse_node_topology(node_data)
            for dev in devices:
                device_name = dev["device_name"]
                device_type = dev["device_type"]
                attributes = dev.get("attributes", {})

                converted_data = ConvertedData(
                    device_name=device_name,
                    device_type=device_type
                )
                converted_data.add_to_attributes(attributes)

                # Send device attributes to ThingsBoard storage queue
                self._gateway.send_to_storage(self.get_name(), self.get_id(), converted_data)
                self.statistics["MessagesSent"] += 1
                log.info(f"Registered ThingsBoard Device: [{device_name}] (Type: {device_type})")

                # Extract any initial telemetry states present in node data
                self._extract_initial_telemetry(node_data, dev["node_id"], dev["endpoint_id"], device_name)

        except Exception as e:
            log.error(f"Error synchronizing Matter node: {e}", exc_info=True)

    def _extract_initial_telemetry(self, node_data: Dict[str, Any], node_id: int, endpoint_id: int, device_name: str):
        """Checks if the node payload contains initial cluster states and sends as telemetry."""
        endpoints = node_data.get("endpoints", {})
        ep_content = {}
        if isinstance(endpoints, dict):
            ep_content = endpoints.get(str(endpoint_id)) or endpoints.get(endpoint_id, {})
        elif isinstance(endpoints, list):
            for ep in endpoints:
                if ep.get("endpoint_id") == endpoint_id:
                    ep_content = ep
                    break

        clusters = ep_content.get("clusters", {})
        telemetry_payload = {}

        # OnOff Cluster (6)
        onoff_cluster = clusters.get("6") or clusters.get(6, {})
        if onoff_cluster:
            state_val = onoff_cluster.get("onOff") if "onOff" in onoff_cluster else onoff_cluster.get("0")
            if state_val is not None:
                telemetry_payload["state"] = "ON" if state_val in [True, 1, "true", "True"] else "OFF"
                telemetry_payload["onOff"] = bool(state_val)

        # LevelControl (8)
        level_cluster = clusters.get("8") or clusters.get(8, {})
        if level_cluster:
            lvl = level_cluster.get("currentLevel") if "currentLevel" in level_cluster else level_cluster.get("0")
            if lvl is not None:
                telemetry_payload["brightness"] = int(round((int(lvl) / 254.0) * 100))

        # Temperature (1026)
        temp_cluster = clusters.get("1026") or clusters.get(1026, {})
        if temp_cluster:
            t = temp_cluster.get("measuredValue") if "measuredValue" in temp_cluster else temp_cluster.get("0")
            if t is not None:
                telemetry_payload["temperature"] = round(float(t) / 100.0, 2)

        # Humidity (1029)
        hum_cluster = clusters.get("1029") or clusters.get(1029, {})
        if hum_cluster:
            h = hum_cluster.get("measuredValue") if "measuredValue" in hum_cluster else hum_cluster.get("0")
            if h is not None:
                telemetry_payload["humidity"] = round(float(h) / 100.0, 2)

        if telemetry_payload:
            converted = ConvertedData(device_name=device_name, device_type="Smart Device")
            converted.add_to_telemetry(TelemetryEntry(telemetry_payload, int(time.time() * 1000)))
            self._gateway.send_to_storage(self.get_name(), self.get_id(), converted)
            self.statistics["MessagesSent"] += 1

    def _on_matter_node_event(self, event_type: str, data: Dict[str, Any]):
        log.info(f"Matter node event received: {event_type}")
        if event_type in ["node_added", "node_updated"]:
            self._sync_single_node(data)
        elif event_type == "node_removed":
            node_id = data.get("node_id")
            log.info(f"Matter node removed: {node_id}")

    def _on_matter_attribute_event(self, node_id: int, endpoint_id: int, cluster_id: int, attribute_id: int, value: Any):
        """Processes real-time Matter attribute updates and forwards them to ThingsBoard."""
        self.statistics["MessagesReceived"] += 1
        res = self.mapper.convert_attribute_update(node_id, endpoint_id, cluster_id, attribute_id, value)
        if not res:
            return

        device_name, telemetry, attributes = res
        converted_data = ConvertedData(device_name=device_name, device_type="Smart Device")

        if telemetry:
            converted_data.add_to_telemetry(TelemetryEntry(telemetry, int(time.time() * 1000)))
        if attributes:
            converted_data.add_to_attributes(attributes)

        # Send to ThingsBoard storage queue (supports offline buffering)
        self._gateway.send_to_storage(self.get_name(), self.get_id(), converted_data)
        self.statistics["MessagesSent"] += 1
        log.debug(f"Streamed Matter Telemetry to ThingsBoard -> [{device_name}]: {telemetry}")

    # =========================================================================
    # Bidirectional RPC & Cloud Attribute Handler
    # =========================================================================

    def on_attributes_update(self, content):
        """Handles ThingsBoard cloud shared/client attribute updates."""
        log.info(f"Matter Connector received cloud attribute update: {content}")

    def server_side_rpc_handler(self, content):
        """
        Translates ThingsBoard Cloud RPC into Matter Cluster Commands.
        Format: { "device": "Matter - Smart Plug (Node 2)", "data": { "id": 1, "method": "setState", "params": true } }
        """
        device = content.get("device", "")
        data = content.get("data", {})
        method = data.get("method")
        params = data.get("params")

        log.info(f"Executing RPC for Matter device [{device}]: method='{method}', params={params}")

        # Map RPC to Matter Cluster command
        mapping = self.mapper.map_rpc_to_matter_command(device, method, params)
        if not mapping:
            error_msg = f"Unknown or unsupported Matter RPC method '{method}' for device '{device}'"
            log.warning(error_msg)
            return {"error": error_msg}

        node_id, endpoint_id, cluster_id, command_name, command_args = mapping

        # Execute command through Matter WebSocket Client
        resp = self.client.device_command(
            node_id=node_id,
            endpoint_id=endpoint_id,
            cluster_id=cluster_id,
            command_name=command_name,
            command_args=command_args,
            timeout=10.0
        )

        if resp.get("success"):
            log.info(f"Matter command '{command_name}' executed successfully on Node {node_id} (EP {endpoint_id})")

            # Optimistic telemetry update back to ThingsBoard
            if method in ["setState", "setValue", "writeState"]:
                is_on = params in [True, 1, "ON", "true", "True", "on"]
                opt_data = ConvertedData(device_name=device, device_type="Smart Device")
                opt_data.add_to_telemetry(TelemetryEntry({"state": "ON" if is_on else "OFF", "onOff": is_on}, int(time.time() * 1000)))
                self._gateway.send_to_storage(self.get_name(), self.get_id(), opt_data)

            return {"success": True, "result": resp.get("result")}
        else:
            err = resp.get("error", "Unknown error")
            log.error(f"Failed to execute Matter command on Node {node_id}: {err}")
            return {"error": f"Matter execution failed: {err}"}
