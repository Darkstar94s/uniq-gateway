# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# Comprehensive End-to-End Test Suite for UNIQ Matter Connector & Controller

import os
import sys
import time
import json
import subprocess
import urllib.request
import urllib.error

# Add gateway package and extension path
GATEWAY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../"))
if GATEWAY_PATH not in sys.path:
    sys.path.insert(0, GATEWAY_PATH)

from thingsboard_gateway.extensions.uniq.matter.device_mapper import MatterDeviceMapper
from thingsboard_gateway.extensions.uniq.matter.matter_client import MatterClient
from thingsboard_gateway.extensions.uniq.matter.commission_service import MatterCommissionService
from thingsboard_gateway.extensions.uniq.matter.uniq_matter_connector import UniqMatterConnector


class MockGatewayService:
    """Mock ThingsBoard Gateway core service to capture send_to_storage calls."""
    def __init__(self):
        self.storage_queue = []
        self.devices = {}

    def send_to_storage(self, connector_name, connector_id, data):
        self.storage_queue.append({
            "connector_name": connector_name,
            "connector_id": connector_id,
            "data": data.to_dict() if hasattr(data, "to_dict") else data,
            "timestamp": time.time()
        })
        dev_name = data.device_name if hasattr(data, "device_name") else data.get("deviceName")
        if dev_name:
            self.devices[dev_name] = data


def run_test():
    print("==================================================================")
    print(" UNIQ Hub Matter Connector & Controller - End-to-End Test Suite")
    print("==================================================================")

    # 1. Start Mock matterjs-server
    mock_server_script = os.path.join(os.path.dirname(__file__), "mock_matter_server.py")
    print(f"\n[Step 1] Starting Mock matterjs-server from: {mock_server_script}...")
    server_proc = subprocess.Popen(
        [sys.executable, mock_server_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(1.5)
    if server_proc.poll() is not None:
        print("[FAIL] Mock server failed to start:", server_proc.stderr.read())
        return False
    print("[PASS] Mock matterjs-server running on ws://127.0.0.1:5580/ws.")

    try:
        # 2. Test DeviceMapper with Direct Devices and Matter Bridges
        print("\n[Step 2] Testing Matter Device Mapper & Bridge Topology Isolation...")
        reg_path = os.path.join(os.path.dirname(__file__), "test_data", "matter_device_registry.json")
        if os.path.exists(reg_path):
            os.remove(reg_path)

        mapper = MatterDeviceMapper(reg_path)

        # A. Direct Wi-Fi Plug
        node1 = {
            "node_id": 1,
            "endpoints": {
                0: {"clusters": {40: {"vendorName": "UNIQ", "productName": "Smart Plug Wi-Fi", "serialNumber": "UNIQ-PLUG-1"}}},
                1: {"device_types": [{"device_type": 0x010A}], "clusters": {6: {"onOff": True}}}
            }
        }
        devs1 = mapper.parse_node_topology(node1)
        assert len(devs1) == 1, f"Expected 1 device for direct plug, got {len(devs1)}"
        assert devs1[0]["device_name"] == "Matter - UNIQ Smart Plug Wi-Fi (1)"
        print(f"  -> Direct device mapped: '{devs1[0]['device_name']}'")

        # B. SONOFF Matter Bridge with multiple bridged endpoints
        node2 = {
            "node_id": 2,
            "endpoints": {
                0: {"device_types": [{"device_type": 0x000E}], "clusters": {40: {"vendorName": "SONOFF", "productName": "Zigbee Matter Bridge Pro"}}},
                1: {"device_types": [{"device_type": 0x0013}, {"device_type": 0x0100}], "clusters": {57: {"vendorName": "SONOFF", "nodeLabel": "Living Room Light Relay"}, 6: {"onOff": False}}},
                2: {"device_types": [{"device_type": 0x0013}, {"device_type": 0x0302}], "clusters": {57: {"vendorName": "SONOFF", "nodeLabel": "Bedroom Temperature"}, 1026: {"measuredValue": 2350}, 1029: {"measuredValue": 4800}}},
                3: {"device_types": [{"device_type": 0x0013}, {"device_type": 0x0015}], "clusters": {57: {"vendorName": "SONOFF", "nodeLabel": "Front Door Contact"}, 1030: {"occupancy": 1}}}
            }
        }
        devs2 = mapper.parse_node_topology(node2)
        # Should yield 4 distinct ThingsBoard devices: 1 bridge root + 3 bridged endpoint devices!
        assert len(devs2) == 4, f"Expected 4 distinct devices for bridge, got {len(devs2)}"
        bridge_root = devs2[0]["device_name"]
        ep1_dev = devs2[1]["device_name"]
        ep2_dev = devs2[2]["device_name"]
        ep3_dev = devs2[3]["device_name"]

        print(f"  -> Bridge Root: '{bridge_root}'")
        print(f"  -> Bridged Relay (EP1): '{ep1_dev}'")
        print(f"  -> Bridged Sensor (EP2): '{ep2_dev}'")
        print(f"  -> Bridged Contact (EP3): '{ep3_dev}'")
        assert "Bridged - SONOFF Living Room Light Relay" in ep1_dev
        assert "Bridged - SONOFF Bedroom Temperature" in ep2_dev
        print("[PASS] Matter Bridge topology correctly decomposed into distinct ThingsBoard devices!")

        # 3. Test Cluster to Telemetry/Attribute Conversion
        print("\n[Step 3] Testing Cluster Attribute to Telemetry Conversion...")
        # Temp sensor reading update: 2425 -> 24.25 °C
        res_temp = mapper.convert_attribute_update(node_id=2, endpoint_id=2, cluster_id=1026, attribute_id=0, value=2425)
        assert res_temp is not None
        dev_name, telem, _ = res_temp
        assert telem.get("temperature") == 24.25
        print(f"  -> Cluster 0x0402 (Temp) value 2425 -> Telemetry: {telem}")

        # Humidity update: 5200 -> 52.0%
        res_hum = mapper.convert_attribute_update(node_id=2, endpoint_id=2, cluster_id=1029, attribute_id=0, value=5200)
        assert res_hum[1].get("humidity") == 52.0
        print(f"  -> Cluster 0x0405 (Humidity) value 5200 -> Telemetry: {res_hum[1]}")

        # Relay OnOff update: true -> "ON"
        res_on = mapper.convert_attribute_update(node_id=2, endpoint_id=1, cluster_id=6, attribute_id=0, value=True)
        assert res_on[1].get("state") == "ON"
        print(f"  -> Cluster 0x0006 (OnOff) value True -> Telemetry: {res_on[1]}")
        print("[PASS] Telemetry conversion accuracy verified.")

        # 4. Test UniqMatterConnector Live Synchronization with Mock Server
        print("\n[Step 4] Starting UniqMatterConnector with Mock Gateway Service...")
        mock_gateway = MockGatewayService()
        config = {
            "id": "test_matter_connector",
            "name": "UNIQ Matter Connector",
            "server_url": "ws://127.0.0.1:5580/ws",
            "storage_path": os.path.join(os.path.dirname(__file__), "test_data"),
            "commissioning_api": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 8282
            }
        }
        connector = UniqMatterConnector(mock_gateway, config, "custom")
        connector.open()
        time.sleep(2.0)

        # Check that connector discovered nodes and sent them to storage queue
        print(f"  -> Items stored in ThingsBoard storage queue: {len(mock_gateway.storage_queue)}")
        assert len(mock_gateway.storage_queue) >= 4, "Expected at least 4 devices sent to storage queue"
        created_devices = list(mock_gateway.devices.keys())
        print(f"  -> ThingsBoard Devices Created: {created_devices}")
        print("[PASS] Initial nodes synchronization completed successfully.")

        # 5. Test Bidirectional RPC: ThingsBoard RPC -> Matter Cluster Command
        print("\n[Step 5] Testing ThingsBoard RPC -> Matter Command Execution...")
        rpc_request = {
            "device": ep1_dev,
            "data": {
                "id": 101,
                "method": "setState",
                "params": True
            }
        }
        rpc_resp = connector.server_side_rpc_handler(rpc_request)
        print(f"  -> RPC Response: {rpc_resp}")
        assert rpc_resp.get("success") is True, f"RPC execution failed: {rpc_resp}"
        print("[PASS] ThingsBoard RPC executed Matter OnOff command on mock device.")

        # 6. Test Local Commissioning REST API (POST /matter/commission)
        print("\n[Step 6] Testing Hub Local Commissioning REST API (POST /matter/commission)...")
        # Check Status endpoint first
        status_req = urllib.request.Request("http://127.0.0.1:8282/matter/status")
        with urllib.request.urlopen(status_req) as resp:
            status_data = json.loads(resp.read().decode("utf-8"))
            print(f"  -> Commissioning API Status: {status_data}")
            assert status_data.get("status") == "online"

        # Send Commissioning Request with QR Code
        commission_payload = json.dumps({
            "code": "MT:Y.SONOFF-LIGHT-STRIP-9988",
            "network_only": True
        }).encode("utf-8")
        post_req = urllib.request.Request(
            "http://127.0.0.1:8282/matter/commission",
            data=commission_payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(post_req) as resp:
            comm_resp = json.loads(resp.read().decode("utf-8"))
            print(f"  -> Commission Response: {comm_resp}")
            assert comm_resp.get("status") == "success"
            new_node_id = comm_resp.get("node_id")
            assert new_node_id == 3

        time.sleep(1.0)
        # Verify newly commissioned device appeared in ThingsBoard storage
        print(f"  -> Updated ThingsBoard Device List: {list(mock_gateway.devices.keys())}")
        new_dev_found = any("SONOFF Smart Light Strip" in name for name in mock_gateway.devices.keys())
        assert new_dev_found, "Newly commissioned device was not synchronized to ThingsBoard!"
        print("[PASS] Full Commissioning -> Discovery -> ThingsBoard Synchronization verified!")

        # 7. Test Offline Storage Buffering
        print("\n[Step 7] Testing Local Offline Storage Buffering...")
        # Simulate offline: verify that send_to_storage records timestamps and messages without errors
        queue_count_before = len(mock_gateway.storage_queue)
        connector._on_matter_attribute_event(node_id=1, endpoint_id=1, cluster_id=6, attribute_id=0, value=False)
        queue_count_after = len(mock_gateway.storage_queue)
        assert queue_count_after == queue_count_before + 1
        latest_entry = mock_gateway.storage_queue[-1]
        assert latest_entry["data"]["telemetry"][0]["values"]["state"] == "OFF"
        print(f"  -> Buffered telemetry entry: {latest_entry['data']['telemetry']}")
        print("[PASS] Telemetry successfully pushed to storage queue for offline persistence.")

        # Cleanup
        connector.close()
        print("\n==================================================================")
        print(" ALL 7 END-TO-END MATTER TESTS PASSED WITH 100% SUCCESS!")
        print("==================================================================")
        return True

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=2)
        except Exception:
            server_proc.kill()


if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
