/**
 * ==============================================================================
 * UNIQ Hub - Mock matterjs-server (Matter Controller Simulation)
 * Built with pure Node.js (zero external dependencies).
 * Implements the Matter Controller WebSocket RPC protocol on port 5580.
 * ==============================================================================
 */

const http = require('http');
const crypto = require('crypto');

const PORT = 5580;

// Internal database of simulated Matter devices
const NODES = {
  1: {
    node_id: 1,
    available: true,
    endpoints: {
      0: {
        endpoint_id: 0,
        device_types: [{ device_type: 0x0016 }], // Root Node
        clusters: {
          40: { // BasicInformation (0x0028)
            vendorName: "UNIQ",
            productName: "Smart Plug Wi-Fi",
            serialNumber: "UNIQ-PLUG-9921"
          }
        }
      },
      1: {
        endpoint_id: 1,
        device_types: [{ device_type: 0x010A }], // On/Off Plug-in Unit
        clusters: {
          6: { onOff: true }, // OnOff Cluster
          1: { batteryPercentRemaining: 190 }, // PowerSource Cluster (95%)
          2820: { activePower: 45.2, rmsVoltage: 228.5 } // ElectricalMeasurement
        }
      }
    }
  },
  2: {
    node_id: 2,
    available: true,
    endpoints: {
      0: {
        endpoint_id: 0,
        device_types: [{ device_type: 0x000E }], // Aggregator / Matter Bridge
        clusters: {
          40: { // BasicInformation
            vendorName: "SONOFF",
            productName: "Zigbee Matter Bridge Pro",
            serialNumber: "SN-ZB-BRDG-01"
          }
        }
      },
      1: {
        endpoint_id: 1,
        device_types: [{ device_type: 0x0013 }, { device_type: 0x0100 }], // Bridged Node + On/Off Light
        clusters: {
          57: { // BridgedDeviceBasicInformation (0x0039)
            vendorName: "SONOFF",
            productName: "Smart Relay ZBMINI",
            nodeLabel: "Living Room Light Relay",
            serialNumber: "SONOFF-RELAY-01"
          },
          6: { onOff: false }
        }
      },
      2: {
        endpoint_id: 2,
        device_types: [{ device_type: 0x0013 }, { device_type: 0x0302 }], // Bridged Node + Temp Sensor
        clusters: {
          57: {
            vendorName: "SONOFF",
            productName: "Climate Sensor SNZB-02",
            nodeLabel: "Bedroom Temperature",
            serialNumber: "SONOFF-CLIM-02"
          },
          1026: { measuredValue: 2350 }, // 23.50 °C
          1029: { measuredValue: 4800 }, // 48.00 %
          1: { batteryPercentRemaining: 180 } // 90%
        }
      },
      3: {
        endpoint_id: 3,
        device_types: [{ device_type: 0x0013 }, { device_type: 0x0015 }], // Bridged Contact Sensor
        clusters: {
          57: {
            vendorName: "SONOFF",
            productName: "Door Sensor SNZB-04",
            nodeLabel: "Front Door Contact",
            serialNumber: "SONOFF-DOOR-03"
          },
          1030: { occupancy: 1 } // Open / Active
        }
      }
    }
  }
};

let nextNodeId = 3;
const clients = new Set();

// Create HTTP server for WebSocket upgrade
const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/plain' });
  res.end('UNIQ Mock matterjs-server running on ws://127.0.0.1:' + PORT + '/ws\n');
});

// WebSocket Protocol Handshake & Framing (Pure standard library)
server.on('upgrade', (req, socket) => {
  const key = req.headers['sec-websocket-key'];
  if (!key) {
    socket.destroy();
    return;
  }
  const digest = crypto.createHash('sha1')
    .update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
    .digest('base64');

  const headers = [
    'HTTP/1.1 101 Switching Protocols',
    'Upgrade: websocket',
    'Connection: Upgrade',
    `Sec-WebSocket-Accept: ${digest}`
  ];
  socket.write(headers.join('\r\n') + '\r\n\r\n');

  clients.add(socket);
  console.log('[MockMatterServer] Client connected via WebSocket.');

  socket.on('data', (buffer) => handleWsFrame(socket, buffer));
  socket.on('close', () => {
    clients.delete(socket);
    console.log('[MockMatterServer] Client disconnected.');
  });
  socket.on('error', () => clients.delete(socket));
});

function sendWsMessage(socket, obj) {
  const payload = Buffer.from(JSON.stringify(obj), 'utf-8');
  const len = payload.length;
  let header;

  if (len < 126) {
    header = Buffer.from([0x81, len]);
  } else if (len <= 65535) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }

  try {
    socket.write(Buffer.concat([header, payload]));
  } catch (e) {}
}

function broadcastEvent(event, data) {
  for (const client of clients) {
    sendWsMessage(client, { event, data });
  }
}

function handleWsFrame(socket, buf) {
  if (buf.length < 2) return;
  const isMasked = (buf[1] & 0x80) !== 0;
  let payloadLength = buf[1] & 0x7f;
  let offset = 2;

  if (payloadLength === 126) {
    payloadLength = buf.readUInt16BE(2);
    offset = 4;
  } else if (payloadLength === 127) {
    payloadLength = Number(buf.readBigUInt64BE(2));
    offset = 10;
  }

  let maskKey = null;
  if (isMasked) {
    maskKey = buf.slice(offset, offset + 4);
    offset += 4;
  }

  const payload = buf.slice(offset, offset + payloadLength);
  if (isMasked && maskKey) {
    for (let i = 0; i < payload.length; i++) {
      payload[i] ^= maskKey[i % 4];
    }
  }

  try {
    const jsonStr = payload.toString('utf-8');
    const msg = JSON.parse(jsonStr);
    handleRpcCommand(socket, msg);
  } catch (e) {
    console.error('[MockMatterServer] Parse error:', e.message);
  }
}

function handleRpcCommand(socket, msg) {
  const { message_id, command, args } = msg;
  console.log(`[MockMatterServer] Received command: '${command}' (msgId: ${message_id})`);

  if (command === 'start_listening') {
    // Reply success
    sendWsMessage(socket, { message_id, result: { listening: true } });
    // Stream initial nodes
    for (const node of Object.values(NODES)) {
      sendWsMessage(socket, { event: 'node_added', data: node });
    }
  } else if (command === 'get_nodes') {
    sendWsMessage(socket, { message_id, result: Object.values(NODES) });
  } else if (command === 'get_node') {
    const node = NODES[args.node_id];
    if (node) {
      sendWsMessage(socket, { message_id, result: node });
    } else {
      sendWsMessage(socket, { message_id, error: `Node ${args.node_id} not found` });
    }
  } else if (command === 'device_command') {
    const { node_id, endpoint_id, cluster_id, command_name, args: cmdArgs } = args;
    console.log(`[MockMatterServer] Cluster command: Node ${node_id}, EP ${endpoint_id}, Cluster ${cluster_id} -> ${command_name}`);

    const node = NODES[node_id];
    if (!node) {
      sendWsMessage(socket, { message_id, error: `Node ${node_id} not found` });
      return;
    }

    const ep = node.endpoints[endpoint_id];
    if (!ep) {
      sendWsMessage(socket, { message_id, error: `Endpoint ${endpoint_id} not found` });
      return;
    }

    // Process cluster command
    if (cluster_id === 6) { // OnOff
      let newState = false;
      if (command_name === 'On') newState = true;
      else if (command_name === 'Off') newState = false;
      else if (command_name === 'Toggle') {
        const cur = ep.clusters[6] ? ep.clusters[6].onOff : false;
        newState = !cur;
      }
      ep.clusters[6] = { onOff: newState };
      sendWsMessage(socket, { message_id, result: { success: true, onOff: newState } });

      // Emit real-time attribute update
      broadcastEvent('attribute_updated', {
        node_id,
        endpoint_id,
        cluster_id: 6,
        attribute_id: 0,
        value: newState
      });
    } else if (cluster_id === 8) { // LevelControl
      const level = cmdArgs.level || 128;
      ep.clusters[8] = { currentLevel: level };
      sendWsMessage(socket, { message_id, result: { success: true, level } });

      broadcastEvent('attribute_updated', {
        node_id,
        endpoint_id,
        cluster_id: 8,
        attribute_id: 0,
        value: level
      });
    } else {
      sendWsMessage(socket, { message_id, result: { success: true } });
    }
  } else if (command === 'commission_with_code') {
    const { code } = args;
    console.log(`[MockMatterServer] Commissioning new device with code: ${code}`);

    const newNodeId = nextNodeId++;
    const newNode = {
      node_id: newNodeId,
      available: true,
      endpoints: {
        0: {
          endpoint_id: 0,
          device_types: [{ device_type: 0x0016 }],
          clusters: {
            40: {
              vendorName: "SONOFF",
              productName: "Smart Light Strip L3",
              serialNumber: `SN-LIGHT-${newNodeId}`
            }
          }
        },
        1: {
          endpoint_id: 1,
          device_types: [{ device_type: 0x0101 }], // Dimmable Light
          clusters: {
            6: { onOff: true },
            8: { currentLevel: 200 }
          }
        }
      }
    };

    NODES[newNodeId] = newNode;
    sendWsMessage(socket, {
      message_id,
      result: {
        status: 'success',
        node_id: newNodeId,
        device_type: 'Dimmable Light'
      }
    });

    // Notify listeners that new node was added
    broadcastEvent('node_added', newNode);
  } else {
    sendWsMessage(socket, { message_id, error: `Unknown command '${command}'` });
  }
}

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[MockMatterServer] Ready & listening on ws://127.0.0.1:${PORT}/ws`);
});
