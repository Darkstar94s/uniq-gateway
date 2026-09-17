# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# Local Hub Security & PIN Authentication Manager

import json
import logging
import os
import secrets
import threading
import time
from typing import Optional, Set

log = logging.getLogger("UniqHubAuth")

DEFAULT_PIN = "849201"  # Default Factory PIN printed on Hub physical label
TOKEN_EXPIRY_SECONDS = 86400  # 24 hours


class HubAuthManager:
    """
    Manages local Hub administrative authentication via factory PIN.
    """

    def __init__(self, credentials_path: str = "./data/hub_credentials.json"):
        self.credentials_path = credentials_path
        self._lock = threading.Lock()
        self._pin = DEFAULT_PIN
        self._active_tokens: Set[str] = set()
        self._load_credentials()

    def _load_credentials(self):
        with self._lock:
            if os.path.exists(self.credentials_path):
                try:
                    with open(self.credentials_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._pin = str(data.get("pin", DEFAULT_PIN))
                        log.info("Loaded custom admin PIN from credentials file.")
                except Exception as e:
                    log.error(f"Failed to load credentials file ({e}), using factory PIN.")
                    self._pin = DEFAULT_PIN
            else:
                self._save_credentials()

    def _save_credentials(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.credentials_path)), exist_ok=True)
            with open(self.credentials_path, "w", encoding="utf-8") as f:
                json.dump({"pin": self._pin, "updated_at": int(time.time())}, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save credentials file: {e}")

    def verify_pin(self, entered_pin: str) -> Optional[str]:
        """Verifies PIN and returns session token if correct, else None."""
        with self._lock:
            if str(entered_pin).strip() == str(self._pin).strip():
                token = secrets.token_hex(16)
                self._active_tokens.add(token)
                log.info("Admin authentication successful. Issued session token.")
                return token
            log.warning("Admin authentication failed: invalid PIN entered.")
            return None

    def validate_token(self, token: Optional[str]) -> bool:
        """Validates whether a session token is currently authenticated."""
        if not token:
            return False
        with self._lock:
            return token in self._active_tokens

    def revoke_token(self, token: Optional[str]):
        if token:
            with self._lock:
                self._active_tokens.discard(token)

    def change_pin(self, current_pin: str, new_pin: str) -> bool:
        with self._lock:
            if str(current_pin).strip() != str(self._pin).strip():
                return False
            if len(str(new_pin).strip()) < 4:
                return False
            self._pin = str(new_pin).strip()
            self._save_credentials()
            log.info("Admin PIN successfully updated.")
            return True
