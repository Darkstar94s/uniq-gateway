# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# Local HTTP Commissioning Microservice & Web Dashboard Server for UNIQ Hub

import json
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional

from .hub_auth import HubAuthManager

log = logging.getLogger("UniqMatterCommissionService")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
INDEX_HTML_PATH = os.path.join(WEB_DIR, "index.html")


class CommissionRequestHandler(BaseHTTPRequestHandler):
    """
    Handles local Hub web dashboard, authentication, device control, and commissioning.
    """

    def log_message(self, format, *args):
        # Silence default HTTP access logs into standard python logger
        log.debug("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args))

    def _send_json_response(self, status_code: int, data: Any):
        response_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(response_bytes)

    def _send_html_response(self, html_content: str):
        response_bytes = html_content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _get_auth_token(self) -> Optional[str]:
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            return auth_hdr.replace("Bearer ", "").strip()
        return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        service = self.server.service  # type: ignore

        # 1. Web Dashboard (Home Page)
        if self.path in ["/", "/index.html"]:
            if os.path.exists(INDEX_HTML_PATH):
                with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
                    self._send_html_response(f.read())
            else:
                self._send_html_response("<h1>UNIQ Smart Hub Web UI</h1><p>Dashboard template not found.</p>")
            return

        # 2. System Status API
        elif self.path in ["/matter/status", "/status", "/api/status"]:
            status_data = service.get_status()
            self._send_json_response(200, status_data)
            return

        # 3. Live Devices API (Protected)
        elif self.path in ["/api/devices"]:
            token = self._get_auth_token()
            if not service.auth_manager.validate_token(token):
                self._send_json_response(401, {"error": "Unauthorized. Please login with valid Hub PIN."})
                return

            devices_data = service.get_live_devices()
            self._send_json_response(200, {"devices": devices_data})
            return

        else:
            self._send_json_response(404, {"error": "Not Found", "path": self.path})

    def do_POST(self):
        service = self.server.service  # type: ignore
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._send_json_response(400, {"status": "error", "error": "Empty request body"})
            return

        try:
            post_body = self.rfile.read(content_length)
            req_data = json.loads(post_body.decode("utf-8"))
        except Exception as e:
            self._send_json_response(400, {"status": "error", "error": f"Invalid JSON body: {e}"})
            return

        # 1. Admin PIN Login
        if self.path == "/api/login":
            pin = req_data.get("pin", "")
            token = service.auth_manager.verify_pin(pin)
            if token:
                self._send_json_response(200, {"success": True, "token": token})
            else:
                self._send_json_response(401, {"success": False, "error": "Invalid PIN"})
            return

        # 2. Local Device RPC Control
        elif self.path == "/api/control":
            token = self._get_auth_token()
            if not service.auth_manager.validate_token(token):
                self._send_json_response(401, {"error": "Unauthorized"})
                return

            device_name = req_data.get("device")
            method = req_data.get("method")
            params = req_data.get("params")

            result = service.control_device(device_name, method, params)
            self._send_json_response(200, result)
            return

        # 3. Change PIN API
        elif self.path == "/api/settings/pin":
            token = self._get_auth_token()
            if not service.auth_manager.validate_token(token):
                self._send_json_response(401, {"error": "Unauthorized"})
                return

            cur = req_data.get("current_pin", "")
            new_p = req_data.get("new_pin", "")
            ok = service.auth_manager.change_pin(cur, new_p)
            if ok:
                self._send_json_response(200, {"success": True, "message": "PIN updated successfully"})
            else:
                self._send_json_response(400, {"success": False, "error": "Current PIN is incorrect or new PIN is too short"})
            return

        # 4. Matter Commissioning Endpoint
        elif self.path in ["/matter/commission", "/commission", "/api/commission"]:
            code = req_data.get("code")
            if not code:
                self._send_json_response(400, {
                    "status": "error",
                    "error": "Missing required 'code' parameter (e.g. Matter QR code string 'MT:...' or setup PIN)."
                })
                return

            network_only = bool(req_data.get("network_only", True))
            wifi_ssid = req_data.get("wifi_ssid")
            wifi_password = req_data.get("wifi_password")

            result = service.commission_device(
                code=code,
                network_only=network_only,
                wifi_ssid=wifi_ssid,
                wifi_password=wifi_password
            )

            status_code = 200 if result.get("status") == "success" else 400
            self._send_json_response(status_code, result)
            return

        else:
            self._send_json_response(404, {"error": "Not Found", "path": self.path})


class MatterCommissionService:
    """
    Runs the lightweight local REST API and Web Dashboard on UNIQ Hub.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8282, matter_connector: Any = None):
        self.host = host
        self.port = port
        self.connector = matter_connector
        self.auth_manager = HubAuthManager()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

    def start(self):
        if self._is_running:
            return
        self._is_running = True
        try:
            self._server = HTTPServer((self.host, self.port), CommissionRequestHandler)
            self._server.service = self  # type: ignore
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="MatterCommissionHttpServer"
            )
            self._thread.start()
            log.info(f"UNIQ Hub Local Web Dashboard & API listening on http://{self.host}:{self.port}")
        except Exception as e:
            log.error(f"Failed to start UNIQ Hub Local HTTP Service: {e}")

    def stop(self):
        self._is_running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        log.info("UNIQ Hub Local Web Dashboard stopped.")

    def get_status(self) -> dict:
        is_connected = False
        registered_count = 0
        server_url = "unknown"

        if self.connector:
            if hasattr(self.connector, "client") and self.connector.client:
                is_connected = self.connector.client.is_connected
                server_url = self.connector.client.server_url
            if hasattr(self.connector, "mapper") and self.connector.mapper:
                registered_count = len(self.connector.mapper._registry)

        return {
            "status": "online" if self._is_running else "offline",
            "matterjs_connected": is_connected,
            "matterjs_server_url": server_url,
            "commissioned_devices_count": registered_count,
            "hub": "UNIQ Smart Home Hub",
            "version": "2.4.0-uniq",
            "local_web_gui": f"http://{self.host}:{self.port}/"
        }

    def get_live_devices(self) -> list:
        if not self.connector or not hasattr(self.connector, "mapper"):
            return []

        devices = []
        registry = self.connector.mapper._registry
        for key, dev in registry.items():
            device_name = dev.get("device_name")
            is_bridged = dev.get("is_bridged", False)
            node_id = dev.get("node_id")
            endpoint_id = dev.get("endpoint_id")
            dev_type = dev.get("device_type", "Smart Device")

            # Check if device has OnOff capability
            has_onoff = "Light" in dev_type or "Relay" in dev_type or "Socket" in dev_type or "Plug" in dev_type or "Switch" in dev_type

            dev_info = {
                "device_name": device_name,
                "device_type": dev_type,
                "node_id": node_id,
                "endpoint_id": endpoint_id,
                "vendor": dev.get("vendor_name", "Matter"),
                "model": dev.get("product_name", ""),
                "is_bridged": is_bridged,
                "has_onoff": has_onoff,
                "state": "OFF"
            }
            devices.append(dev_info)

        return devices

    def control_device(self, device_name: str, method: str, params: Any) -> dict:
        if not self.connector:
            return {"success": False, "error": "Connector not initialized"}

        rpc_request = {
            "device": device_name,
            "data": {
                "id": 1,
                "method": method,
                "params": params
            }
        }
        res = self.connector.server_side_rpc_handler(rpc_request)
        return res

    def commission_device(
        self,
        code: str,
        network_only: bool = True,
        wifi_ssid: Optional[str] = None,
        wifi_password: Optional[str] = None
    ) -> dict:
        log.info(f"Received commissioning request for code: {code[:12]}... (network_only={network_only})")

        if not self.connector or not self.connector.client:
            return {"status": "error", "error": "Matter Connector is not initialized or offline."}

        client = self.connector.client
        if not client.is_connected:
            return {
                "status": "error",
                "error": "Cannot commission: UNIQ Hub is not currently connected to matterjs-server."
            }

        resp = client.commission_with_code(
            code=code,
            network_only=network_only,
            wifi_ssid=wifi_ssid,
            wifi_password=wifi_password,
            timeout=60.0
        )

        if not resp.get("success"):
            return {
                "status": "error",
                "error": resp.get("error", "Commissioning failed in matterjs-server.")
            }

        result_data = resp.get("result", {})
        node_id = result_data.get("node_id") if isinstance(result_data, dict) else None

        if node_id and hasattr(self.connector, "sync_node_by_id"):
            self.connector.sync_node_by_id(int(node_id))

        return {
            "status": "success",
            "node_id": node_id,
            "result": result_data,
            "message": f"Device successfully commissioned on Matter Fabric with Node ID {node_id}."
        }
