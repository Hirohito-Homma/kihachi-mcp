/* Exercise the Max device against a minimal LiveAPI model. No Live write. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
    path.join(__dirname, "..", "maxforlive", "kihachi.device.js"), "utf8"
);
const rackPath = "live_set tracks 1 devices 0";
const padPath = rackPath + " drum_pads 0";
const chainPath = rackPath + " chains 0";
const samplePath = "/tmp/kihachi-kick.wav";
const calls = [];
let chains = 0;
let inNote = -1;
let devices = 0;
let loadedSample = "";

class LiveAPI {
    constructor(_callback, objectPath) { this.path = objectPath; }
    getcount(child) {
        if (this.path === "live_set tracks 1" && child === "devices") return 1;
        if (this.path === rackPath && child === "drum_pads") return 1;
        if (this.path === rackPath && child === "chains") return chains;
        if (this.path === padPath && child === "chains") return inNote === 36 ? 1 : 0;
        if (this.path === chainPath && child === "devices") return devices;
        if (this.path === padPath + " chains 0" && child === "devices") return devices;
        return 0;
    }
    get(property) {
        if (this.path === rackPath && property === "can_have_drum_pads") return 1;
        if (this.path === padPath && property === "note") return 36;
        if (this.path === padPath + " chains 0 devices 0" &&
            property === "sample_file_path") {
            return loadedSample;
        }
        return null;
    }
    set(property, value) {
        calls.push([this.path, "set", property, value]);
        if (this.path === chainPath && property === "in_note") inNote = value;
    }
    call(method, value) {
        calls.push([this.path, "call", method, value]);
        if (this.path === "live_app" && method === "get_version_string") return "12.4.5";
        if (this.path === rackPath && method === "insert_chain") chains++;
        else if (this.path === chainPath && method === "insert_device") devices++;
        else if (this.path.endsWith(" devices 0") && method === "replace_sample") {
            loadedSample = value;
        } else if (method === "insert_chain") {
            throw new Error("insert_chain is not a DrumPad method");
        }
    }
}

const context = { LiveAPI, outlet() {}, post() {} };
vm.createContext(context);
vm.runInContext(source, context);
const result = context.loadDrumPadSample(
    { track_index: 1 }, { note: 36, sample_path: samplePath }
);
assert.equal(result.occupied, true);
assert.equal(result.sample_path, samplePath);
assert.deepEqual(calls.slice(-4), [
    [rackPath, "call", "insert_chain", undefined],
    [chainPath, "set", "in_note", 36],
    [chainPath, "call", "insert_device", "Simpler"],
    [chainPath + " devices 0", "call", "replace_sample", samplePath],
]);
