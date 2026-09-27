/* Raw loopback UDP bridge for the KIHACHI Max for Live device. */
"use strict";

const dgram = require("dgram");
const fs = require("fs");
const os = require("os");
const path = require("path");
const maxApi = require("max-api");

const LOOPBACK = "127.0.0.1";
const DEVICE_PORT = 17771;
const REPLY_PORT = 17772;
const MAX_DATAGRAM_BYTES = 60000;
const BRIDGE_VERSION = 2;
const socket = dgram.createSocket("udp4");

function handshakePath() {
  if (process.platform === "win32") {
    const base = process.env.LOCALAPPDATA || process.env.APPDATA;
    return base ? path.join(base, "KIHACHI", "live-bridge.json") : "";
  }
  return path.join(os.homedir(), "Library", "Application Support", "KIHACHI", "live-bridge.json");
}

function authenticatedRequest(buffer) {
  const request = JSON.parse(buffer.toString("utf8"));
  const handshake = JSON.parse(fs.readFileSync(handshakePath(), "utf8"));
  if (!request.token || request.token !== handshake.token) {
    maxApi.outlet("status", `authentication mismatch using ${handshakePath()}`);
    return null;
  }
  delete request.token;
  request.bridge_authenticated = true;
  return request;
}

function sendUnauthorized(buffer) {
  let requestId = "";
  try {
    requestId = String(JSON.parse(buffer.toString("utf8")).request_id || "");
  } catch (_error) {
    requestId = "";
  }
  const response = JSON.stringify({
    protocol: "kihachi.live",
    version: 1,
    request_id: requestId,
    ok: false,
    error: { code: "unauthorized", message: "session token rejected" },
  });
  socket.send(Buffer.from(response, "utf8"), REPLY_PORT, LOOPBACK);
}

socket.on("error", (error) => {
  maxApi.outlet("status", `udp error: ${error.message}`);
});

socket.on("message", (buffer, remote) => {
  if (remote.address !== LOOPBACK && remote.address !== "::ffff:127.0.0.1") {
    maxApi.outlet("status", "discarded non-loopback datagram");
    return;
  }
  if (buffer.length > MAX_DATAGRAM_BYTES) {
    maxApi.outlet("status", "discarded oversized datagram");
    return;
  }
  try {
    const request = authenticatedRequest(buffer);
    if (!request) {
      sendUnauthorized(buffer);
      return;
    }
    // The token is removed before the request enters the Max patch.
    maxApi.outlet("request", JSON.stringify(request));
  } catch (error) {
    maxApi.outlet("status", `request rejected: ${error.message}`);
    sendUnauthorized(buffer);
  }
});

maxApi.addHandler("response", (jsonText) => {
  const payload = Buffer.from(String(jsonText), "utf8");
  if (payload.length > MAX_DATAGRAM_BYTES) {
    maxApi.outlet("status", "response exceeds UDP safety limit");
    return;
  }
  socket.send(payload, REPLY_PORT, LOOPBACK);
});

socket.bind(DEVICE_PORT, LOOPBACK, () => {
  maxApi.outlet(
    "status",
    `raw UDP bridge v${BRIDGE_VERSION} ready on ${LOOPBACK}:${DEVICE_PORT}`,
  );
});

process.on("SIGTERM", () => socket.close());
