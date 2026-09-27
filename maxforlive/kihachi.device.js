/*
 * KIHACHI Live Device -- Max for Live bridge to the Live Object Model.
 *
 * Load this into a [js kihachi.device.js] object inside a Max for Live device.
 * See maxforlive/README.md for the patch layout and the .amxd packaging steps.
 *
 * Contract (docs/MAXFORLIVE.md):
 *   in  0  a JSON request string from [udpreceive]
 *   out 0  a JSON response string for [udpsend 127.0.0.1 <reply_port>]
 *   out 1  a status symbol for the device UI
 *
 * Safety rules enforced here, independently of the Python planner:
 *   - the session token must match the handshake file
 *   - preconditions are re-checked against the live Set before mutating
 *   - a refused operation reports a failure and changes nothing
 *   - nothing is retried
 *   - the Set is never saved
 *
 * Written for the Max [js] engine, which is ES5. Do not use let, const,
 * arrow functions, or JSON5.
 */

autowatch = 1;
inlets = 1;
outlets = 2;

var PROTOCOL_NAME = "kihachi.live";
var PROTOCOL_VERSION = 1;
var SCHEMA_VERSION = 1;
var DEVICE_VERSION = "kihachi-live-device/0.1.0";

var MANAGED_MARKER = "[KIHACHI]";
var MANAGED_CLIP_PREFIX = "[K:";

var sessionToken = null;
var seenRequestIds = {};

/* ----------------------------------------------------------------- utilities */

function log(message) {
    outlet(1, "status", message);
}

function liveApi(path) {
    return new LiveAPI(function () {}, path);
}

function songApi() {
    return liveApi("live_set");
}

function getProperty(api, name) {
    var value = api.get(name);
    if (value === null || value === undefined) {
        return null;
    }
    if (value instanceof Array) {
        return value.length === 1 ? value[0] : value;
    }
    return value;
}

function countChildren(api, child) {
    var value = api.getcount(child);
    return value === null ? 0 : value;
}

function isManagedName(name) {
    if (!name) {
        return false;
    }
    return name.indexOf(MANAGED_MARKER) >= 0 || name.indexOf(MANAGED_CLIP_PREFIX) >= 0;
}

/* --------------------------------------------------------------- token check */

/*
 * The handshake file is written by the Python side with 0600 permissions. The
 * token is read once per request so restarting the server does not require
 * reloading the device.
 */
function handshakePath() {
    var candidates = [];
    var home = null;
    try {
        home = max.env && max.env.HOME ? max.env.HOME : null;
    } catch (error) {
        home = null;
    }
    if (home) {
        candidates.push(home + "/Library/Application Support/KIHACHI/live-bridge.json");
    }
    var localAppData = null;
    try {
        localAppData = max.env && max.env.LOCALAPPDATA ? max.env.LOCALAPPDATA : null;
    } catch (error) {
        localAppData = null;
    }
    if (localAppData) {
        candidates.push(localAppData + "\\KIHACHI\\live-bridge.json");
    }
    return candidates;
}

function readHandshake() {
    var candidates = handshakePath();
    for (var index = 0; index < candidates.length; index += 1) {
        var file = null;
        try {
            file = new File(candidates[index], "read", "TEXT");
        } catch (error) {
            file = null;
        }
        if (file && file.isopen) {
            var text = "";
            while (file.position < file.eof) {
                text += file.readline();
            }
            file.close();
            try {
                return JSON.parse(text);
            } catch (error) {
                return null;
            }
        }
    }
    return null;
}

function tokenIsValid(candidate) {
    if (sessionToken === null) {
        var handshake = readHandshake();
        if (handshake === null || !handshake.token) {
            return false;
        }
        sessionToken = handshake.token;
    }
    return candidate === sessionToken;
}

function forgetToken() {
    sessionToken = null;
}

/* ------------------------------------------------------------- state reading */

function timeSignature(song) {
    return {
        numerator: getProperty(song, "signature_numerator") || 4,
        denominator: getProperty(song, "signature_denominator") || 4
    };
}

function trackKind(api) {
    if (getProperty(api, "has_midi_input")) {
        return "midi";
    }
    return "audio";
}

function readTracks(song) {
    var tracks = [];
    var total = countChildren(song, "tracks");
    for (var index = 0; index < total; index += 1) {
        var api = liveApi("live_set tracks " + index);
        var devices = [];
        var deviceCount = countChildren(api, "devices");
        for (var d = 0; d < deviceCount; d += 1) {
            var device = liveApi("live_set tracks " + index + " devices " + d);
            devices.push(String(getProperty(device, "name") || ""));
        }
        tracks.push({
            index: index,
            name: String(getProperty(api, "name") || ""),
            track_type: trackKind(api),
            color: String(getProperty(api, "color") || ""),
            is_armed: getProperty(api, "arm") ? true : false,
            is_frozen: getProperty(api, "is_frozen") ? true : false,
            device_names: devices
        });
    }
    return tracks;
}

function readScenes(song) {
    var scenes = [];
    var total = countChildren(song, "scenes");
    for (var index = 0; index < total; index += 1) {
        var api = liveApi("live_set scenes " + index);
        scenes.push({
            index: index,
            name: String(getProperty(api, "name") || ""),
            color: String(getProperty(api, "color") || "")
        });
    }
    return scenes;
}

function readSessionClips(trackCount, sceneCount) {
    var clips = [];
    for (var track = 0; track < trackCount; track += 1) {
        for (var scene = 0; scene < sceneCount; scene += 1) {
            var slot = liveApi(
                "live_set tracks " + track + " clip_slots " + scene
            );
            if (!getProperty(slot, "has_clip")) {
                continue;
            }
            var clip = liveApi(
                "live_set tracks " + track + " clip_slots " + scene + " clip"
            );
            clips.push({
                track_index: track,
                scene_index: scene,
                name: String(getProperty(clip, "name") || ""),
                length_beats: Number(getProperty(clip, "length") || 0),
                note_count: countNotes(clip),
                is_midi: getProperty(clip, "is_midi_clip") ? true : false,
                looping: getProperty(clip, "looping") ? true : false
            });
        }
    }
    return clips;
}

function countNotes(clip) {
    if (!getProperty(clip, "is_midi_clip")) {
        return 0;
    }
    var length = Number(getProperty(clip, "length") || 0);
    var notes = clip.call("get_notes_extended", 0, 128, 0, length);
    if (!notes) {
        return 0;
    }
    try {
        var parsed = JSON.parse(notes);
        return parsed && parsed.notes ? parsed.notes.length : 0;
    } catch (error) {
        return 0;
    }
}

function readArrangementClips(trackCount) {
    var clips = [];
    for (var track = 0; track < trackCount; track += 1) {
        var api = liveApi("live_set tracks " + track);
        var total = countChildren(api, "arrangement_clips");
        for (var index = 0; index < total; index += 1) {
            var clip = liveApi(
                "live_set tracks " + track + " arrangement_clips " + index
            );
            clips.push({
                track_index: track,
                name: String(getProperty(clip, "name") || ""),
                start_beats: Number(getProperty(clip, "start_time") || 0),
                length_beats: Number(getProperty(clip, "length") || 0),
                note_count: countNotes(clip)
            });
        }
    }
    return clips;
}

/*
 * Device availability. Live exposes no supported API for enumerating the
 * browser from [js], so the device reports the stock devices it has been told
 * this installation has. Keep this list in sync with the Live edition actually
 * installed; the Python side treats it as authoritative and refuses anything
 * absent rather than substituting.
 */
var AVAILABLE_DEVICES = [
    "Drum Rack",
    "Simpler",
    "Operator",
    "Wavetable",
    "Drift",
    "Auto Filter",
    "EQ Eight",
    "Compressor",
    "Saturator",
    "Echo",
    "Hybrid Reverb"
];

function readDevices() {
    var devices = [];
    for (var index = 0; index < AVAILABLE_DEVICES.length; index += 1) {
        devices.push({
            name: AVAILABLE_DEVICES[index],
            available: true,
            category: ""
        });
    }
    return devices;
}

function readState() {
    var song = songApi();
    var trackCount = countChildren(song, "tracks");
    var sceneCount = countChildren(song, "scenes");
    return {
        schema_version: SCHEMA_VERSION,
        live_version: String(getProperty(liveApi("live_app"), "version") || ""),
        set_name: String(getProperty(song, "name") || ""),
        set_path: String(getProperty(song, "file_path") || ""),
        tempo: Number(getProperty(song, "tempo") || 0),
        time_signature: timeSignature(song),
        is_playing: getProperty(song, "is_playing") ? true : false,
        is_recording: getProperty(song, "record_mode") ? true : false,
        observed_at: new Date().toISOString(),
        tracks: readTracks(song),
        scenes: readScenes(song),
        session_clips: readSessionClips(trackCount, sceneCount),
        arrangement_clips: readArrangementClips(trackCount),
        devices: readDevices()
    };
}

/* ------------------------------------------------------------- preconditions */

function OperationRefused(message) {
    this.message = message;
}

function refuse(message) {
    throw new OperationRefused(message);
}

function checkPreconditions(operation) {
    var song = songApi();
    var conditions = operation.preconditions || [];
    for (var index = 0; index < conditions.length; index += 1) {
        var condition = conditions[index];
        var args = condition.arguments || {};
        var kind = condition.kind;

        if (kind === "not_recording" && getProperty(song, "record_mode")) {
            refuse("Live is recording");
        }
        if (kind === "transport_stopped" && getProperty(song, "is_playing")) {
            refuse("Live transport is running");
        }
        if (kind === "track_name_at_index") {
            var track = liveApi("live_set tracks " + args.track_index);
            if (String(getProperty(track, "name") || "") !== args.name) {
                refuse("track " + args.track_index + " is not '" + args.name + "'");
            }
        }
        if (kind === "track_missing") {
            var total = countChildren(song, "tracks");
            for (var t = 0; t < total; t += 1) {
                var existing = liveApi("live_set tracks " + t);
                if (String(getProperty(existing, "name") || "") === args.name) {
                    refuse("track '" + args.name + "' already exists");
                }
            }
        }
        if (kind === "scene_name_at_index") {
            var scene = liveApi("live_set scenes " + args.scene_index);
            if (String(getProperty(scene, "name") || "") !== args.name) {
                refuse("scene " + args.scene_index + " is not '" + args.name + "'");
            }
        }
        if (kind === "session_slot_empty") {
            var slot = liveApi(
                "live_set tracks " + args.track_index +
                " clip_slots " + args.scene_index
            );
            if (getProperty(slot, "has_clip")) {
                refuse(
                    "session slot (" + args.track_index + ", " +
                    args.scene_index + ") already holds a clip"
                );
            }
        }
        if (kind === "clip_is_managed") {
            var ownedSlot = liveApi(
                "live_set tracks " + args.track_index +
                " clip_slots " + args.scene_index
            );
            if (!getProperty(ownedSlot, "has_clip")) {
                refuse("session slot holds no clip");
            }
            var ownedClip = liveApi(
                "live_set tracks " + args.track_index +
                " clip_slots " + args.scene_index + " clip"
            );
            if (!isManagedName(String(getProperty(ownedClip, "name") || ""))) {
                refuse("session slot is not KIHACHI owned");
            }
        }
        if (kind === "device_available") {
            if (AVAILABLE_DEVICES.indexOf(args.device_name) < 0) {
                refuse(
                    "device '" + args.device_name +
                    "' is not available in this Live installation"
                );
            }
        }
        if (kind === "arrangement_range_free") {
            requireFreeRange(args);
        }
    }
}

function requireFreeRange(args) {
    var track = liveApi("live_set tracks " + args.track_index);
    var total = countChildren(track, "arrangement_clips");
    var start = Number(args.start_beats);
    var end = start + Number(args.length_beats);
    for (var index = 0; index < total; index += 1) {
        var clip = liveApi(
            "live_set tracks " + args.track_index + " arrangement_clips " + index
        );
        var clipStart = Number(getProperty(clip, "start_time") || 0);
        var clipEnd = clipStart + Number(getProperty(clip, "length") || 0);
        if (start < clipEnd && clipStart < end) {
            refuse(
                "arrangement range overlaps '" +
                String(getProperty(clip, "name") || "") + "'"
            );
        }
    }
}

/* ------------------------------------------------------------------ mutation */

function applyOperation(operation) {
    var op = operation.op;
    var target = operation.target || {};
    var args = operation.arguments || {};
    var song = songApi();

    if (op === "set_tempo") {
        song.set("tempo", args.tempo);
        return { tempo: Number(getProperty(song, "tempo")) };
    }
    if (op === "create_midi_track" || op === "create_audio_track") {
        return createTrack(song, op, args);
    }
    if (op === "set_track_name") {
        var named = liveApi("live_set tracks " + target.track_index);
        named.set("name", args.name);
        return {
            track_index: target.track_index,
            name: String(getProperty(named, "name") || "")
        };
    }
    if (op === "set_track_color") {
        var colored = liveApi("live_set tracks " + target.track_index);
        colored.set("color", args.color);
        return {
            track_index: target.track_index,
            color: String(getProperty(colored, "color") || "")
        };
    }
    if (op === "create_scene") {
        return createScene(song, args);
    }
    if (op === "create_session_clip") {
        return createSessionClip(target, args);
    }
    if (op === "replace_clip_notes") {
        return replaceClipNotes(target, args);
    }
    if (op === "load_live_device") {
        return loadDevice(target, args);
    }
    if (op === "create_locator") {
        return createLocator(song, args);
    }
    if (op === "place_arrangement_clip") {
        return placeArrangementClip(target, args);
    }
    refuse("unsupported operation '" + op + "'");
}

function createTrack(song, op, args) {
    var index = countChildren(song, "tracks");
    if (op === "create_midi_track") {
        song.call("create_midi_track", index);
    } else {
        song.call("create_audio_track", index);
    }
    var api = liveApi("live_set tracks " + index);
    api.set("name", args.name);
    if (args.color) {
        /*
         * Live expects an integer color index. A non-numeric colour name from
         * the Brain is left to Live's default rather than guessed at.
         */
        var parsed = parseInt(args.color, 10);
        if (!isNaN(parsed)) {
            api.set("color_index", parsed);
        }
    }
    return {
        track_index: index,
        name: String(getProperty(api, "name") || ""),
        track_type: trackKind(api)
    };
}

function createScene(song, args) {
    var index = countChildren(song, "scenes");
    song.call("create_scene", index);
    var api = liveApi("live_set scenes " + index);
    api.set("name", args.name);
    return {
        scene_index: index,
        name: String(getProperty(api, "name") || "")
    };
}

function createSessionClip(target, args) {
    var slot = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index
    );
    slot.call("create_clip", args.length_beats);
    var clip = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index + " clip"
    );
    clip.set("name", args.name);
    clip.set("looping", args.looping ? 1 : 0);
    return {
        track_index: target.track_index,
        scene_index: target.scene_index,
        name: String(getProperty(clip, "name") || ""),
        length_beats: Number(getProperty(clip, "length") || 0),
        looping: getProperty(clip, "looping") ? true : false
    };
}

function replaceClipNotes(target, args) {
    var clip = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index + " clip"
    );
    var notes = args.notes || [];
    var payload = { notes: [] };
    for (var index = 0; index < notes.length; index += 1) {
        payload.notes.push({
            pitch: notes[index].pitch,
            start_time: notes[index].start,
            duration: notes[index].duration,
            velocity: notes[index].velocity,
            mute: 0
        });
    }
    /*
     * remove_notes_extended clears only this clip's own notes, which is why
     * replace_clip_notes is only ever planned for a KIHACHI-owned clip.
     */
    clip.call("remove_notes_extended", 0, 128, 0, Number(getProperty(clip, "length")));
    clip.call("add_new_notes", JSON.stringify(payload));
    return {
        track_index: target.track_index,
        scene_index: target.scene_index,
        note_count: countNotes(clip)
    };
}

function loadDevice(target, args) {
    var track = liveApi("live_set tracks " + target.track_index);
    var before = countChildren(track, "devices");
    /*
     * Device loading is driven from the patch, not from [js]: the JS side
     * cannot browse. The patch wires this return value into the browser
     * loader, which reports back through report_device_loaded.
     */
    outlet(1, "load_device", target.track_index, args.device_name);
    var after = countChildren(track, "devices");
    if (after <= before) {
        refuse(
            "device '" + args.device_name +
            "' was not loaded; see maxforlive/README.md device loader setup"
        );
    }
    var device = liveApi(
        "live_set tracks " + target.track_index + " devices " + (after - 1)
    );
    return {
        track_index: target.track_index,
        device_name: String(getProperty(device, "name") || ""),
        device_index: after - 1
    };
}

function createLocator(song, args) {
    var previous = getProperty(song, "current_song_time");
    song.set("current_song_time", args.beats);
    song.call("set_or_delete_cue");
    var total = countChildren(song, "cue_points");
    var created = null;
    for (var index = 0; index < total; index += 1) {
        var cue = liveApi("live_set cue_points " + index);
        if (Number(getProperty(cue, "time")) === Number(args.beats)) {
            cue.set("name", args.name);
            created = {
                name: String(getProperty(cue, "name") || ""),
                beats: Number(getProperty(cue, "time"))
            };
        }
    }
    song.set("current_song_time", previous);
    if (created === null) {
        refuse("locator at beat " + args.beats + " was not created");
    }
    return created;
}

function placeArrangementClip(target, args) {
    var slot = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index
    );
    if (!getProperty(slot, "has_clip")) {
        refuse("source session clip is missing");
    }
    var track = liveApi("live_set tracks " + target.track_index);
    var before = countChildren(track, "arrangement_clips");
    slot.call("duplicate_clip_to", "live_set tracks " + target.track_index, args.start_beats);
    var after = countChildren(track, "arrangement_clips");
    if (after <= before) {
        refuse("arrangement clip was not created");
    }
    var clip = liveApi(
        "live_set tracks " + target.track_index +
        " arrangement_clips " + (after - 1)
    );
    clip.set("name", args.name);
    return {
        track_index: target.track_index,
        name: String(getProperty(clip, "name") || ""),
        start_beats: Number(getProperty(clip, "start_time") || 0),
        length_beats: Number(getProperty(clip, "length") || 0),
        note_count: countNotes(clip)
    };
}

/* ------------------------------------------------------------------ dispatch */

function respondOk(requestId, result) {
    outlet(
        0,
        JSON.stringify({
            protocol: PROTOCOL_NAME,
            version: PROTOCOL_VERSION,
            request_id: requestId,
            ok: true,
            result: result
        })
    );
}

function respondError(requestId, code, message) {
    outlet(
        0,
        JSON.stringify({
            protocol: PROTOCOL_NAME,
            version: PROTOCOL_VERSION,
            request_id: requestId || "",
            ok: false,
            error: { code: code, message: message }
        })
    );
}

function handle(request) {
    var requestId = request.request_id || "";

    if (request.protocol !== PROTOCOL_NAME) {
        respondError(requestId, "protocol", "unknown protocol");
        return;
    }
    if (Number(request.version) !== PROTOCOL_VERSION) {
        respondError(
            requestId,
            "protocol",
            "device speaks protocol " + PROTOCOL_VERSION
        );
        return;
    }
    if (!tokenIsValid(request.token)) {
        /* Never echo the supplied value back; it may be a real token. */
        respondError(requestId, "unauthorized", "session token rejected");
        return;
    }
    if (seenRequestIds[requestId]) {
        respondError(requestId, "duplicate_request", "request_id already handled");
        return;
    }
    seenRequestIds[requestId] = true;

    if (request.method === "ping") {
        respondOk(requestId, {
            live_version: String(getProperty(liveApi("live_app"), "version") || ""),
            protocol_version: PROTOCOL_VERSION,
            device_version: DEVICE_VERSION
        });
        return;
    }
    if (request.method === "get_state") {
        respondOk(requestId, readState());
        return;
    }
    if (request.method === "apply_operation") {
        applyAndRespond(requestId, request);
        return;
    }
    respondError(requestId, "protocol", "unsupported method");
}

function applyAndRespond(requestId, request) {
    var payload = request.payload || {};
    var operation = payload.operation || {};
    try {
        checkPreconditions(operation);
        var observed = applyOperation(operation);
        respondOk(requestId, {
            operation_id: operation.operation_id,
            op: operation.op,
            observed: observed
        });
    } catch (error) {
        var message = error && error.message ? error.message : String(error);
        respondError(requestId, "operation_failed", message);
    }
}

/* -------------------------------------------------------------- Max entry points */

function anything() {
    var text = messagename;
    for (var index = 0; index < arguments.length; index += 1) {
        text += " " + arguments[index];
    }
    dispatch(text);
}

function msg_string(text) {
    dispatch(text);
}

function dispatch(text) {
    var request = null;
    try {
        request = JSON.parse(text);
    } catch (error) {
        respondError("", "protocol", "request is not JSON");
        return;
    }
    if (!request || typeof request !== "object") {
        respondError("", "protocol", "request is not an object");
        return;
    }
    handle(request);
}

/* Called from the patch when the server restarts and rotates its token. */
function reload() {
    forgetToken();
    seenRequestIds = {};
    log("reloaded");
}

function bang() {
    log("kihachi live device ready");
}
