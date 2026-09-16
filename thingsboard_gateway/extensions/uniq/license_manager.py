# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# UNIQ Protocol Licensing Engine for ThingsBoard Gateway

import logging
from typing import Dict, List, Set, Callable, Optional

log = logging.getLogger("UniqLicenseManager")

class HubTier:
    LITE = "LITE"    # Matter, Wi-Fi
    PRO = "PRO"      # Matter, Wi-Fi, Zigbee, BLE
    ULTRA = "ULTRA"  # Matter, Wi-Fi, Zigbee, BLE, Thread

TIER_DEFAULT_PROTOCOLS = {
    HubTier.LITE: {"wifi", "matter"},
    HubTier.PRO: {"wifi", "matter", "zigbee", "ble"},
    HubTier.ULTRA: {"wifi", "matter", "zigbee", "ble", "thread"}
}

class LicenseManager:
    _instance: Optional['LicenseManager'] = None

    @classmethod
    def get_instance(cls, hub_serial: str = "UNIQ-HUB-001", tier: str = HubTier.PRO) -> 'LicenseManager':
        if cls._instance is None:
            cls._instance = cls(hub_serial, tier)
        return cls._instance

    def __init__(self, hub_serial: str, tier: str = HubTier.PRO):
        self.hub_serial = hub_serial
        self.tier = tier.upper() if tier else HubTier.PRO
        self._licensed_protocols: Set[str] = set(TIER_DEFAULT_PROTOCOLS.get(self.tier, {"wifi", "matter"}))
        self._listeners: List[Callable[[List[str]], None]] = []

    def is_protocol_licensed(self, protocol_name: str) -> bool:
        return protocol_name.lower() in self._licensed_protocols

    def get_licensed_protocols(self) -> List[str]:
        return sorted(list(self._licensed_protocols))

    def on_license_changed(self, callback: Callable[[List[str]], None]):
        if callback not in self._listeners:
            self._listeners.append(callback)

    def update_license_from_cloud(self, attributes: Dict):
        changed = False

        if "tier" in attributes:
            new_tier = str(attributes["tier"]).upper()
            if new_tier in TIER_DEFAULT_PROTOCOLS and new_tier != self.tier:
                self.tier = new_tier
                self._licensed_protocols.update(TIER_DEFAULT_PROTOCOLS[self.tier])
                changed = True
                log.info(f"Hub Hardware Tier upgraded to: {self.tier}")

        if "licensedProtocols" in attributes:
            protocols_val = attributes["licensedProtocols"]
            new_protocols = set()
            if isinstance(protocols_val, list):
                new_protocols = {p.lower().strip() for p in protocols_val}
            elif isinstance(protocols_val, str):
                new_protocols = {p.lower().strip() for p in protocols_val.split(",")}

            if new_protocols and new_protocols != self._licensed_protocols:
                self._licensed_protocols = new_protocols
                changed = True
                log.info(f"Licensed protocols updated remotely: {self._licensed_protocols}")

        if changed:
            active = self.get_licensed_protocols()
            log.info(f"Active licensed protocols for Hub: {active}")
            for listener in self._listeners:
                try:
                    listener(active)
                except Exception as e:
                    log.error(f"Error executing license change callback: {e}")
