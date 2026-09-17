# -*- coding: utf-8 -*-
# Copyright 2026 UNIQ Smart Home
# Mock matterjs-server Controller in Python (WebSocket Port 5580)

import asyncio
import json
import logging
import websockets

logging.basicConfig(level=logging.INFO, format="[MockMatterServer] %(message)s")
log = logging.getLogger("MockMatterServer")

PORT = 5580

NODES = {
    1: {
        "node_id": 1,
        "available": True,
        "endpoints": {
            0: {
                "endpoint_id": 0,
                "device_types": [{"device_type": 0x0016}],
                "clusters": {
                    40: {
                        "vendorName": "UNIQ",
                        "productName": "Smart Plug Wi-Fi",
                        "serialNumber": "UNIQ-PLUG-9921"
                    }
                }
            },
            1: {
                "endpoint_id": 1,
                "device_types": [{"device_type": 0x010A}],
                "clusters": {
                    6: {"onOff": True},
                    1: {"batteryPercentRemaining": 190},
                    2820: {"activePower": 45.2, "rmsVoltage": 228.5}
                }
            }
        }
    },
    2: {
        "node_id": 2,
        "available": True,
        "endpoints": {
            0: {
                "endpoint_id": 0,
                "device_types": [{"device_type": 0x000E}],
                "clusters": {
                    40: {
                        "vendorName": "SONOFF",
                        "productName": "Zigbee Matter Bridge Pro",
                        "serialNumber": "SN-ZB-BRDG-01"
                    }
                }
            },
            1: {
                "endpoint_id": 1,
                "device_types": [{"device_type": 0x0013}, {"device_type": 0x0100}],
                "clusters": {
                    57: {
                        "vendorName": "SONOFF",
                        "productName": "Smart Relay ZBMINI",
                        "nodeLabel": "Living Room Light Relay",
                        "serialNumber": "SONOFF-RELAY-01"
                    },
                    6: {"onOff": False}
                }
            },
            2: {
                "endpoint_id": 2,
                "device_types": [{"device_type": 0x0013}, {"device_type": 0x0302}],
                "clusters": {
                    57: {
                        "vendorName": "SONOFF",
                        "productName": "Climate Sensor SNZB-02",
                        "nodeLabel": "Bedroom Temperature",
                        "serialNumber": "SONOFF-CLIM-02"
                    },
                    1026: {"measuredValue": 2350},
                    1029: {"measuredValue": 4800},
                    1: {"batteryPercentRemaining": 180}
                }
            },
            3: {
                "endpoint_id": 3,
                "device_types": [{"device_type": 0x0013}, {"device_type": 0x0015}],
                "clusters": {
                    57: {
                        "vendorName": "SONOFF",
                        "productName": "Door Sensor SNZB-04",
                        "nodeLabel": "Front Door Contact",
                        "serialNumber": "SONOFF-DOOR-03"
                    },
                    1030: {"occupancy": 1}
                }
            }
        }
    }
}

next_node_id = 3
active_connections = set()


async def handler(websocket):
    active_connections.add(websocket)
    log.info("Client connected to mock Matter WebSocket.")
    try:
        async for message in websocket:
            try:
                msg = json.loads(message)
            except Exception as e:
                log.error(f"Invalid JSON: {e}")
                continue

            msg_id = msg.get("message_id")
            command = msg.get("command")
            args = msg.get("args", {})
            log.info(f"Command '{command}' (msgId: {msg_id})")

            if command == "start_listening":
                await websocket.send(json.dumps({"message_id": msg_id, "result": {"listening": True}}))
                for node in list(NODES.values()):
                    await websocket.send(json.dumps({"event": "node_added", "data": node}))

            elif command == "get_nodes":
                await websocket.send(json.dumps({"message_id": msg_id, "result": list(NODES.values())}))

            elif command == "get_node":
                node_id = int(args.get("node_id", 0))
                node = NODES.get(node_id)
                if node:
                    await websocket.send(json.dumps({"message_id": msg_id, "result": node}))
                else:
                    await websocket.send(json.dumps({"message_id": msg_id, "error": f"Node {node_id} not found"}))

            elif command == "device_command":
                node_id = int(args.get("node_id", 0))
                endpoint_id = int(args.get("endpoint_id", 0))
                cluster_id = int(args.get("cluster_id", 0))
                command_name = args.get("command_name")
                cmd_args = args.get("args", {})

                node = NODES.get(node_id)
                if not node:
                    await websocket.send(json.dumps({"message_id": msg_id, "error": f"Node {node_id} not found"}))
                    continue

                ep = node["endpoints"].get(endpoint_id)
                if not ep:
                    await websocket.send(json.dumps({"message_id": msg_id, "error": f"Endpoint {endpoint_id} not found"}))
                    continue

                if cluster_id == 6:  # OnOff
                    new_state = True if command_name == "On" else (False if command_name == "Off" else not ep["clusters"].get(6, {}).get("onOff", False))
                    ep["clusters"][6] = {"onOff": new_state}
                    await websocket.send(json.dumps({"message_id": msg_id, "result": {"success": True, "onOff": new_state}}))

                    # Broadcast real-time attribute update
                    event_payload = json.dumps({
                        "event": "attribute_updated",
                        "data": {
                            "node_id": node_id,
                            "endpoint_id": endpoint_id,
                            "cluster_id": 6,
                            "attribute_id": 0,
                            "value": new_state
                        }
                    })
                    for conn in active_connections:
                        await conn.send(event_payload)

                elif cluster_id == 8:  # LevelControl
                    level = cmd_args.get("level", 128)
                    ep["clusters"][8] = {"currentLevel": level}
                    await websocket.send(json.dumps({"message_id": msg_id, "result": {"success": True, "level": level}}))

                    event_payload = json.dumps({
                        "event": "attribute_updated",
                        "data": {
                            "node_id": node_id,
                            "endpoint_id": endpoint_id,
                            "cluster_id": 8,
                            "attribute_id": 0,
                            "value": level
                        }
                    })
                    for conn in active_connections:
                        await conn.send(event_payload)
                else:
                    await websocket.send(json.dumps({"message_id": msg_id, "result": {"success": True}}))

            elif command == "commission_with_code":
                global next_node_id
                code = args.get("code")
                log.info(f"Commissioning new device with code: {code}")
                new_id = next_node_id
                next_node_id += 1

                new_node = {
                    "node_id": new_id,
                    "available": True,
                    "endpoints": {
                        0: {
                            "endpoint_id": 0,
                            "device_types": [{"device_type": 0x0016}],
                            "clusters": {
                                40: {
                                    "vendorName": "SONOFF",
                                    "productName": "Smart Light Strip L3",
                                    "serialNumber": f"SN-LIGHT-{new_id}"
                                }
                            }
                        },
                        1: {
                            "endpoint_id": 1,
                            "device_types": [{"device_type": 0x0101}],
                            "clusters": {
                                6: {"onOff": True},
                                8: {"currentLevel": 200}
                            }
                        }
                    }
                }
                NODES[new_id] = new_node

                await websocket.send(json.dumps({
                    "message_id": msg_id,
                    "result": {
                        "status": "success",
                        "node_id": new_id,
                        "device_type": "Dimmable Light"
                    }
                }))

                # Broadcast node_added
                event_payload = json.dumps({"event": "node_added", "data": new_node})
                for conn in active_connections:
                    await conn.send(event_payload)

            else:
                await websocket.send(json.dumps({"message_id": msg_id, "error": f"Unknown command {command}"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        active_connections.remove(websocket)
        log.info("Client disconnected from mock Matter WebSocket.")


async def main():
    log.info(f"Mock matterjs-server starting on ws://127.0.0.1:{PORT}/ws ...")
    async with websockets.serve(handler, "127.0.0.1", PORT):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
