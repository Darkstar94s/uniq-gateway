# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# Local HTTP Commissioning Microservice for UNIQ Hub

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional

log = logging.getLogger("UniqMatterCommissionService")


class CommissionRequestHandler(BaseHTTPRequestHandler):
    """
    Handles local Hub commissioning requests from UNIQ mobile app or installer tools.
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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path in ["/matter/status", "/status"]:
            service = self.server.service  # type: ignore
            status_data = service.get_status()
            self._send_json_response(200, status_data)
        else:
            self._send_json_response(404, {"error": "Not Found", "path": self.path})

    def do_POST(self):
        if self.path in ["/matter/commission", "/commission"]:
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

            service = self.server.service  # type: ignore
            result = service.commission_device(
                code=code,
                network_only=network_only,
                wifi_ssid=wifi_ssid,
                wifi_password=wifi_password
            )

            status_code = 200 if result.get("status") == "success" else 400
            self._send_json_response(status_code, result)
        else:
            self._send_json_response(404, {"error": "Not Found", "path": self.path})


class MatterCommissionService:
    """
    Runs the lightweight local REST API on UNIQ Hub to commission Matter devices.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8282, matter_connector: Any = None):
        self.host = host
        self.port = port
        self.connector = matter_connector
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
            log.info(f"Matter Commissioning REST API listening on http://{self.host}:{self.port}")
        except Exception as e:
            log.error(f"Failed to start Matter Commissioning HTTP Service: {e}")

    def stop(self):
        self._is_running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        log.info("Matter Commissioning REST API stopped.")

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
            "version": "1.0.0-prototype"
        }

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

        # Dispatch commissioning command to matterjs-server
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

        # Trigger immediate sync in connector if node_id is returned
        if node_id and hasattr(self.connector, "sync_node_by_id"):
            self.connector.sync_node_by_id(int(node_id))

        return {
            "status": "success",
            "node_id": node_id,
            "result": result_data,
            "message": f"Device successfully commissioned on Matter Fabric with Node ID {node_id}."
        }
