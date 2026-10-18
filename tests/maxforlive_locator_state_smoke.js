/* Exercise get_state's locator list against a minimal LiveAPI model. No Live write. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
    path.join(__dirname, "..", "maxforlive", "kihachi.device.js"), "utf8"
);
const cues = [
    { name: "Intro [KIHACHI]", time: 0 },
    { name: "Drop [KIHACHI]", time: 64 }
];
const writes = [];

class LiveAPI {
    constructor(_callback, objectPath) { this.path = objectPath; }
    getcount(child) {
        if (this.path === "live_set" && child === "cue_points") return cues.length;
        return 0;
    }
    get(property) {
        const match = /^live_set cue_points (\d+)$/.exec(this.path);
        if (match && property === "name") return [cues[Number(match[1])].name];
        if (match && property === "time") return [cues[Number(match[1])].time];
        if (this.path === "live_set" && property === "tempo") return [120];
        return null;
    }
    set(property, value) { writes.push([this.path, property, value]); }
    call(method, value) {
        if (this.path === "live_app" && method === "get_version_string") return "12.4.5";
        writes.push([this.path, method, value]);
        return null;
    }
}

const context = { LiveAPI, outlet() {}, post() {} };
vm.createContext(context);
vm.runInContext(source, context);

const withArrangement = JSON.parse(JSON.stringify(
    context.readState({ include_arrangement: true })
));
assert.deepEqual(withArrangement.locators, [
    { name: "Intro [KIHACHI]", beats: 0 },
    { name: "Drop [KIHACHI]", beats: 64 }
]);

const byDefault = JSON.parse(JSON.stringify(context.readState({})));
assert.deepEqual(byDefault.locators, withArrangement.locators);

const withoutArrangement = JSON.parse(JSON.stringify(
    context.readState({ include_arrangement: false })
));
assert.equal("locators" in withoutArrangement, false);

cues.length = 0;
const emptySet = JSON.parse(JSON.stringify(context.readState({})));
assert.deepEqual(emptySet.locators, []);

assert.deepEqual(writes, []);
