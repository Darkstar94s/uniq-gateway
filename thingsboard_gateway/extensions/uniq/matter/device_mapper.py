# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# Matter to ThingsBoard Topology Parser, Cluster Converter & Device Registry

import json
import logging
import os
import threading
from typing import Dict, Any, List, Tuple, Optional

log = logging.getLogger("UniqMatterDeviceMapper")

# Standard Matter Device Types
DEVICE_TYPE_ROOT_NODE = 0x0016
DEVICE_TYPE_AGGREGATOR_BRIDGE = 0x000E
DEVICE_TYPE_BRIDGED_NODE = 0x0013
DEVICE_TYPE_ON_OFF_LIGHT = 0x0100
DEVICE_TYPE_DIMMABLE_LIGHT = 0x0101
DEVICE_TYPE_ON_OFF_PLUG = 0x010A
DEVICE_TYPE_DOOR_LOCK = 0x000A
DEVICE_TYPE_TEMP_SENSOR = 0x0302
DEVICE_TYPE_HUMIDITY_SENSOR = 0x0307
DEVICE_TYPE_OCCUPANCY_SENSOR = 0x0107
DEVICE_TYPE_CONTACT_SENSOR = 0x0015

DEVICE_TYPE_NAMES = {
    DEVICE_TYPE_ON_OFF_LIGHT: "Smart Light",
    DEVICE_TYPE_DIMMABLE_LIGHT: "Dimmable Light",
    DEVICE_TYPE_ON_OFF_PLUG: "Smart Socket",
    DEVICE_TYPE_DOOR_LOCK: "Smart Door Lock",
    DEVICE_TYPE_TEMP_SENSOR: "Temperature Sensor",
    DEVICE_TYPE_HUMIDITY_SENSOR: "Humidity Sensor",
    DEVICE_TYPE_OCCUPANCY_SENSOR: "Motion Sensor",
    DEVICE_TYPE_CONTACT_SENSOR: "Door/Window Sensor"
}

# Standard Matter Cluster IDs
CLUSTER_POWER_SOURCE = 0x0001
CLUSTER_ON_OFF = 0x0006
CLUSTER_LEVEL_CONTROL = 0x0008
CLUSTER_BASIC_INFORMATION = 0x0028
CLUSTER_BRIDGED_DEVICE_BASIC = 0x0039
CLUSTER_DOOR_LOCK = 0x0101
CLUSTER_COLOR_CONTROL = 0x0300
CLUSTER_TEMP_MEASUREMENT = 0x0402
CLUSTER_HUMIDITY_MEASUREMENT = 0x0405
CLUSTER_OCCUPANCY_SENSING = 0x0406
CLUSTER_ELECTRICAL_MEASUREMENT = 0x0B04
CLUSTER_METERING = 0x0702


class MatterDeviceMapper:
    """
    Manages persistent registry of Matter devices and converts Matter clusters into
    ThingsBoard telemetry and attributes. Correctly isolates bridged nodes from Matter bridges.
    """

    def __init__(self, registry_file_path: str):
        self.registry_file_path = registry_file_path
        self._lock = threading.Lock()
        # Mapping: "(node_id, endpoint_id)" -> device metadata dict
        self._registry: Dict[str, Dict[str, Any]] = {}
        # Reverse mapping: "ThingsBoard Device Name" -> (node_id, endpoint_id)
        self._name_to_node_ep: Dict[str, Tuple[int, int]] = {}
        self._load_registry()

    def _load_registry(self):
        with self._lock:
            if os.path.exists(self.registry_file_path):
                try:
                    with open(self.registry_file_path, "r", encoding="utf-8") as f:
                        self._registry = json.load(f)
                    for key, data in self._registry.items():
                        device_name = data.get("device_name")
                        node_id = data.get("node_id")
                        endpoint_id = data.get("endpoint_id")
                        if device_name and node_id is not None and endpoint_id is not None:
                            self._name_to_node_ep[device_name] = (int(node_id), int(endpoint_id))
                    log.info(f"Loaded {len(self._registry)} mapped Matter devices from persistent registry.")
                except Exception as e:
                    log.error(f"Failed to load Matter device registry: {e}")
                    self._registry = {}

    def _save_registry(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.registry_file_path)), exist_ok=True)
            with open(self.registry_file_path, "w", encoding="utf-8") as f:
                json.dump(self._registry, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error(f"Failed to save Matter device registry: {e}")

    def get_node_endpoint_by_device_name(self, device_name: str) -> Optional[Tuple[int, int]]:
        return self._name_to_node_ep.get(device_name)

    def parse_node_topology(self, node_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parses a Matter node object received from matterjs-server.
        Splits Matter bridges into separate ThingsBoard device definitions.
        Returns a list of device definitions to register/update in ThingsBoard.
        """
        node_id = int(node_data.get("node_id", 0))
        if not node_id:
            return []

        endpoints = node_data.get("endpoints", {})
        # Normalize endpoints dict/list
        ep_dict: Dict[int, Dict[str, Any]] = {}
        if isinstance(endpoints, dict):
            for k, v in endpoints.items():
                try:
                    ep_dict[int(k)] = v
                except ValueError:
                    pass
        elif isinstance(endpoints, list):
            for ep in endpoints:
                ep_id = ep.get("endpoint_id")
                if ep_id is not None:
                    ep_dict[int(ep_id)] = ep

        # Check root endpoint (0) for node basic info
        root_ep = ep_dict.get(0, {})
        root_clusters = root_ep.get("clusters", {})
        basic_info = root_clusters.get(str(CLUSTER_BASIC_INFORMATION), {}) or root_clusters.get(CLUSTER_BASIC_INFORMATION, {})

        vendor_name = basic_info.get("vendorName") or basic_info.get("0") or "Matter"
        product_name = basic_info.get("productName") or basic_info.get("1") or "Device"
        serial_number = basic_info.get("serialNumber") or basic_info.get("15") or f"NODE-{node_id}"

        # Determine if node is a Matter Bridge (Aggregator)
        is_bridge = False
        for ep_id, ep_content in ep_dict.items():
            device_types = ep_content.get("device_types", [])
            for dt in device_types:
                dt_id = dt.get("device_type") if isinstance(dt, dict) else dt
                if dt_id == DEVICE_TYPE_AGGREGATOR_BRIDGE:
                    is_bridge = True
                    break

        devices_to_sync = []

        if is_bridge:
            # 1. Register Bridge Root Device
            bridge_name = f"Matter Bridge - {vendor_name} {product_name} ({node_id})"
            root_device = self._get_or_create_device_entry(
                node_id=node_id,
                endpoint_id=0,
                device_name=bridge_name,
                device_type="Matter Bridge",
                vendor_name=vendor_name,
                product_name=product_name,
                serial_number=serial_number,
                is_bridged=False,
                bridge_name=None
            )
            devices_to_sync.append(root_device)

            # 2. Iterate Bridged Endpoints (1..N) and create individual ThingsBoard devices
            for ep_id, ep_content in ep_dict.items():
                if ep_id == 0:
                    continue

                clusters = ep_content.get("clusters", {})
                bridged_info = clusters.get(str(CLUSTER_BRIDGED_DEVICE_BASIC), {}) or clusters.get(CLUSTER_BRIDGED_DEVICE_BASIC, {})
                node_label = bridged_info.get("nodeLabel") or bridged_info.get("1") or ""
                b_vendor = bridged_info.get("vendorName") or bridged_info.get("2") or vendor_name
                b_product = bridged_info.get("productName") or bridged_info.get("3") or ""
                b_serial = bridged_info.get("serialNumber") or bridged_info.get("4") or f"{serial_number}-EP{ep_id}"

                # Infer device type name
                inferred_type = "Bridged Smart Device"
                for dt in ep_content.get("device_types", []):
                    dt_id = dt.get("device_type") if isinstance(dt, dict) else dt
                    if dt_id in DEVICE_TYPE_NAMES:
                        inferred_type = DEVICE_TYPE_NAMES[dt_id]
                        break

                display_title = node_label if node_label else (b_product if b_product else inferred_type)
                bridged_device_name = f"Bridged - {vendor_name} {display_title} (N{node_id}-EP{ep_id})"

                bridged_device = self._get_or_create_device_entry(
                    node_id=node_id,
                    endpoint_id=ep_id,
                    device_name=bridged_device_name,
                    device_type=inferred_type,
                    vendor_name=b_vendor,
                    product_name=b_product or display_title,
                    serial_number=b_serial,
                    is_bridged=True,
                    bridge_name=bridge_name
                )
                devices_to_sync.append(bridged_device)

        else:
            # Direct Matter Device (Wi-Fi Plug, Light, Lock, Sensor)
            primary_ep_id = 1 if 1 in ep_dict else 0
            primary_ep = ep_dict.get(primary_ep_id, {})
            
            inferred_type = "Matter Smart Device"
            for dt in primary_ep.get("device_types", []):
                dt_id = dt.get("device_type") if isinstance(dt, dict) else dt
                if dt_id in DEVICE_TYPE_NAMES:
                    inferred_type = DEVICE_TYPE_NAMES[dt_id]
                    break

            direct_device_name = f"Matter - {vendor_name} {product_name} ({node_id})"
            direct_device = self._get_or_create_device_entry(
                node_id=node_id,
                endpoint_id=primary_ep_id,
                device_name=direct_device_name,
                device_type=inferred_type,
                vendor_name=vendor_name,
                product_name=product_name,
                serial_number=serial_number,
                is_bridged=False,
                bridge_name=None
            )
            devices_to_sync.append(direct_device)

        return devices_to_sync

    def _get_or_create_device_entry(
        self,
        node_id: int,
        endpoint_id: int,
        device_name: str,
        device_type: str,
        vendor_name: str,
        product_name: str,
        serial_number: str,
        is_bridged: bool,
        bridge_name: Optional[str]
    ) -> Dict[str, Any]:
        key = f"{node_id}_{endpoint_id}"
        with self._lock:
            existing = self._registry.get(key)
            if existing:
                # Keep existing consistent name
                assigned_name = existing.get("device_name", device_name)
            else:
                assigned_name = device_name
                self._registry[key] = {
                    "node_id": node_id,
                    "endpoint_id": endpoint_id,
                    "device_name": assigned_name,
                    "device_type": device_type,
                    "vendor_name": vendor_name,
                    "product_name": product_name,
                    "serial_number": serial_number,
                    "is_bridged": is_bridged,
                    "bridge_name": bridge_name
                }
                self._save_registry()

            self._name_to_node_ep[assigned_name] = (node_id, endpoint_id)

            return {
                "node_id": node_id,
                "endpoint_id": endpoint_id,
                "device_name": assigned_name,
                "device_type": device_type,
                "attributes": {
                    "vendor": vendor_name,
                    "model": product_name,
                    "serialNumber": serial_number,
                    "nodeId": node_id,
                    "endpointId": endpoint_id,
                    "isBridged": is_bridged,
                    "bridgeName": bridge_name or "None",
                    "protocol": "Matter over Wi-Fi" if not is_bridged else "Matter Bridge"
                }
            }

    def convert_attribute_update(
        self,
        node_id: int,
        endpoint_id: int,
        cluster_id: int,
        attribute_id: int,
        value: Any
    ) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        """
        Translates a Matter cluster attribute update into ThingsBoard (device_name, telemetry, attributes).
        Returns None if attribute does not map to relevant smart home state.
        """
        key = f"{node_id}_{endpoint_id}"
        entry = self._registry.get(key)
        if not entry:
            # Auto-fallback: try matching node_id with primary endpoint
            key = f"{node_id}_1"
            entry = self._registry.get(key)

        if not entry:
            return None

        device_name = entry["device_name"]
        telemetry: Dict[str, Any] = {}
        attributes: Dict[str, Any] = {}

        # 1. OnOff Cluster (0x0006)
        if cluster_id == CLUSTER_ON_OFF:
            # Attribute 0 is onOff
            if attribute_id in [0, "onOff"]:
                state_val = "ON" if value in [True, 1, "true", "True"] else "OFF"
                telemetry["state"] = state_val
                telemetry["onOff"] = (state_val == "ON")

        # 2. LevelControl Cluster (0x0008 - Dimmers)
        elif cluster_id == CLUSTER_LEVEL_CONTROL:
            if attribute_id in [0, "currentLevel"]:
                try:
                    level_int = int(value)
                    # Matter level ranges 0..254
                    percent = int(round((level_int / 254.0) * 100))
                    telemetry["brightness"] = max(0, min(100, percent))
                except Exception:
                    pass

        # 3. Temperature Measurement (0x0402)
        elif cluster_id == CLUSTER_TEMP_MEASUREMENT:
            if attribute_id in [0, "measuredValue"]:
                try:
                    raw_temp = float(value)
                    # Matter reports temperature in 100ths of a degree Celsius (e.g. 2150 = 21.5°C)
                    telemetry["temperature"] = round(raw_temp / 100.0, 2)
                except Exception:
                    pass

        # 4. Relative Humidity Measurement (0x0405)
        elif cluster_id == CLUSTER_HUMIDITY_MEASUREMENT:
            if attribute_id in [0, "measuredValue"]:
                try:
                    raw_hum = float(value)
                    # Matter reports relative humidity in 100ths of 1% (e.g. 5500 = 55.0%)
                    telemetry["humidity"] = round(raw_hum / 100.0, 2)
                except Exception:
                    pass

        # 5. Door Lock Cluster (0x0101)
        elif cluster_id == CLUSTER_DOOR_LOCK:
            if attribute_id in [0, "lockState"]:
                # 1 = Locked, 2 = Unlocked
                is_locked = (value == 1 or value == "Locked")
                telemetry["lockState"] = "LOCKED" if is_locked else "UNLOCKED"
                telemetry["locked"] = is_locked

        # 6. Power Source Cluster (0x0001 - Battery)
        elif cluster_id == CLUSTER_POWER_SOURCE:
            if attribute_id in [12, "batteryPercentRemaining"]:
                try:
                    # Stored in half percents (0..200 represents 0..100%)
                    telemetry["battery"] = round(float(value) / 2.0, 1)
                except Exception:
                    pass

        # 7. Electrical Measurement (0x0B04)
        elif cluster_id == CLUSTER_ELECTRICAL_MEASUREMENT:
            # RMSVoltage = 0x0505, RMSCurrent = 0x0508, ActivePower = 0x050B
            if attribute_id in [0x050B, "activePower"]:
                telemetry["power"] = round(float(value), 2)
            elif attribute_id in [0x0505, "rmsVoltage"]:
                telemetry["voltage"] = round(float(value), 2)
            elif attribute_id in [0x0508, "rmsCurrent"]:
                telemetry["current"] = round(float(value), 3)

        # 8. Metering (0x0702)
        elif cluster_id == CLUSTER_METERING:
            if attribute_id in [0, "currentSummationDelivered"]:
                telemetry["energy"] = round(float(value), 3)

        # 9. Occupancy (0x0406)
        elif cluster_id == CLUSTER_OCCUPANCY_SENSING:
            if attribute_id in [0, "occupancy"]:
                telemetry["occupancy"] = bool(int(value) & 1)

        if not telemetry and not attributes:
            return None

        return (device_name, telemetry, attributes)

    def map_rpc_to_matter_command(
        self,
        device_name: str,
        method: str,
        params: Any
    ) -> Optional[Tuple[int, int, int, str, Dict[str, Any]]]:
        """
        Maps a ThingsBoard RPC command to a Matter cluster command.
        Returns: (node_id, endpoint_id, cluster_id, command_name, command_args)
        """
        node_ep = self.get_node_endpoint_by_device_name(device_name)
        if not node_ep:
            log.warning(f"Device name '{device_name}' not found in Matter registry for RPC.")
            return None

        node_id, endpoint_id = node_ep

        # A. On / Off commands
        if method in ["setState", "setValue", "writeState"]:
            is_on = params in [True, 1, "ON", "true", "True", "on"]
            cmd_name = "On" if is_on else "Off"
            return (node_id, endpoint_id, CLUSTER_ON_OFF, cmd_name, {})

        elif method in ["toggle", "toggleState"]:
            return (node_id, endpoint_id, CLUSTER_ON_OFF, "Toggle", {})

        # B. Level / Dimmer commands
        elif method in ["setBrightness", "setLevel"]:
            try:
                percent = float(params)
                level_254 = max(0, min(254, int(round((percent / 100.0) * 254))))
                return (node_id, endpoint_id, CLUSTER_LEVEL_CONTROL, "MoveToLevel", {
                    "level": level_254,
                    "transitionTime": 0
                })
            except Exception:
                pass

        # C. Door Lock commands
        elif method in ["setLock", "setDoorLock"]:
            lock = params in [True, 1, "LOCK", "locked", "true"]
            cmd_name = "LockDoor" if lock else "UnlockDoor"
            return (node_id, endpoint_id, CLUSTER_DOOR_LOCK, cmd_name, {})

        return None
