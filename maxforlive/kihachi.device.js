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
var DEVICE_VERSION = "kihachi-live-device/0.3.5";
var INSERT_DEVICE_MIN_LIVE_MAJOR = 12;
var INSERT_DEVICE_MIN_LIVE_MINOR = 3;
var REPLACE_SAMPLE_MIN_LIVE_MAJOR = 12;
var REPLACE_SAMPLE_MIN_LIVE_MINOR = 4;

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

function getPropertyOrNull(api, name) {
    try {
        return getProperty(api, name);
    } catch (error) {
        return null;
    }
}

function countChildren(api, child) {
    var value = api.getcount(child);
    return value === null ? 0 : value;
}

function liveVersionString() {
    var value = liveApi("live_app").call("get_version_string");
    if (value instanceof Array) {
        value = value.length > 0 ? value[0] : "";
    }
    return String(value || "");
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

function readSessionClips(trackCount, sceneCount, countNotesInSlots) {
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
                note_count: countNotesInSlots ? countNotes(clip) : 0,
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
            var clipStart = Number(getProperty(clip, "start_time") || 0);
            var clipEnd = Number(getProperty(clip, "end_time") || clipStart);
            clips.push({
                track_index: track,
                name: String(getProperty(clip, "name") || ""),
                start_beats: clipStart,
                length_beats: clipEnd - clipStart,
                /* Full note dictionaries make large Arrangements time out. */
                note_count: 0
            });
        }
    }
    return clips;
}

function readArrangementTrack(trackIndex) {
    var track = liveApi("live_set tracks " + trackIndex);
    var total = countChildren(track, "arrangement_clips");
    var clips = [];
    for (var index = 0; index < total; index += 1) {
        var clip = liveApi(
            "live_set tracks " + trackIndex + " arrangement_clips " + index
        );
        var clipStart = Number(getProperty(clip, "start_time") || 0);
        var clipEnd = Number(getProperty(clip, "end_time") || clipStart);
        clips.push({
            track_index: trackIndex,
            name: String(getProperty(clip, "name") || ""),
            start_beats: clipStart,
            length_beats: clipEnd - clipStart,
            note_count: 0
        });
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
    "Analog",
    "Auto Filter",
    "EQ Eight",
    "Compressor",
    "Saturator",
    "Echo",
    "Hybrid Reverb",
    "Utility",
    "Glue Compressor",
    "Drum Buss",
    "Limiter"
];

function liveVersionParts() {
    var raw = liveVersionString();
    var match = /^(\d+)\.(\d+)/.exec(raw);
    if (!match) {
        return { major: 0, minor: 0 };
    }
    return { major: Number(match[1]), minor: Number(match[2]) };
}

function supportsInsertDevice() {
    var version = liveVersionParts();
    return version.major > INSERT_DEVICE_MIN_LIVE_MAJOR ||
        (version.major === INSERT_DEVICE_MIN_LIVE_MAJOR &&
         version.minor >= INSERT_DEVICE_MIN_LIVE_MINOR);
}

function supportsReplaceSample() {
    var version = liveVersionParts();
    return version.major > REPLACE_SAMPLE_MIN_LIVE_MAJOR ||
        (version.major === REPLACE_SAMPLE_MIN_LIVE_MAJOR &&
         version.minor >= REPLACE_SAMPLE_MIN_LIVE_MINOR);
}

function readDevices() {
    var devices = [];
    var insertSupported = supportsInsertDevice();
    for (var index = 0; index < AVAILABLE_DEVICES.length; index += 1) {
        devices.push({
            name: AVAILABLE_DEVICES[index],
            available: insertSupported,
            category: ""
        });
    }
    return devices;
}

function readState(options) {
    var song = songApi();
    var trackCount = countChildren(song, "tracks");
    var sceneCount = countChildren(song, "scenes");
    var includeArrangement = !options || options.include_arrangement !== false;
    var countSessionNotes = !options || options.count_session_notes !== false;
    var includeSessionClips = !options || options.include_session_clips !== false;
    return {
        schema_version: SCHEMA_VERSION,
        live_version: liveVersionString(),
        set_name: String(getProperty(song, "name") || ""),
        set_path: String(getProperty(song, "file_path") || ""),
        tempo: Number(getProperty(song, "tempo") || 0),
        time_signature: timeSignature(song),
        is_playing: getProperty(song, "is_playing") ? true : false,
        is_recording: getProperty(song, "record_mode") ? true : false,
        observed_at: new Date().toISOString(),
        tracks: readTracks(song),
        scenes: readScenes(song),
        session_clips: includeSessionClips
            ? readSessionClips(trackCount, sceneCount, countSessionNotes)
            : [],
        arrangement_clips: includeArrangement ? readArrangementClips(trackCount) : [],
        devices: readDevices(),
        master_device_names: readMasterDevices()
    };
}

function readMasterDevices() {
    var master = liveApi("live_set master_track");
    var names = [];
    var total = countChildren(master, "devices");
    for (var index = 0; index < total; index += 1) {
        names.push(String(getProperty(liveApi("live_set master_track devices " + index), "name") || ""));
    }
    return names;
}

/* The master track when target.master is true, otherwise track N. */
function trackPathOf(target) {
    return target.master === true
        ? "live_set master_track"
        : "live_set tracks " + target.track_index;
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
            if (!supportsInsertDevice()) {
                refuse(
                    "automatic stock-device insertion requires Ableton Live " +
                    "12.3 or newer"
                );
            }
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
        if (kind === "device_name_at_index") {
            var placed = liveApi(trackPathOf(args) + " devices " + args.device_index);
            if (String(getProperty(placed, "name") || "") !== args.name) {
                refuse(
                    "device " + args.device_index + " on " + trackPathOf(args) +
                    " is not '" + args.name + "'"
                );
            }
        }
        if (kind === "drum_pad_sample_path") {
            var expectedRack = findDrumRack(args.track_index);
            var expectedPad = expectedRack ? findDrumPad(expectedRack.path, args.note) : null;
            var observedPath = expectedPad ? drumPadSamplePath(expectedRack.path, expectedPad.index) : "";
            if (observedPath !== String(args.sample_path || "")) {
                refuse("drum pad " + args.note + " sample changed after preview");
            }
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
    if (op === "load_drum_pad_sample") {
        return loadDrumPadSample(target, args);
    }
    if (op === "replace_drum_pad_sample") {
        return replaceDrumPadSample(target, args);
    }
    if (op === "set_device_parameter") {
        return setDeviceParameter(target, args);
    }
    if (op === "set_track_mixer") {
        return setTrackMixer(target, args);
    }
    if (op === "set_sidechain_source") {
        return setSidechainSource(target, args);
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
    var track = liveApi(trackPathOf(target));
    var before = countChildren(track, "devices");
    if (!supportsInsertDevice()) {
        refuse(
            "automatic stock-device insertion requires Ableton Live 12.3 or newer"
        );
    }
    /* Track.insert_device is part of the official LOM from Live 12.3. */
    track.call("insert_device", args.device_name);
    var after = countChildren(track, "devices");
    if (after <= before) {
        refuse(
            "device '" + args.device_name +
            "' was not inserted by Ableton Live"
        );
    }
    var device = liveApi(trackPathOf(target) + " devices " + (after - 1));
    return {
        track_index: target.track_index,
        master: target.master === true,
        device_name: String(getProperty(device, "name") || ""),
        device_index: after - 1
    };
}

/*
 * Set one parameter of one device, found by name rather than by index so a
 * Live update that reorders parameters cannot turn a filter cutoff into a
 * volume. A continuous value arrives normalized to 0..1 of the parameter's
 * min..max; a switch arrives as one of its value_items.
 */
function setDeviceParameter(target, args) {
    var path = trackPathOf(target) + " devices " + target.device_index;
    var device = liveApi(path);
    var deviceName = String(getProperty(device, "name") || "");
    if (deviceName !== args.device_name) {
        refuse("device " + target.device_index + " is '" + deviceName + "', not '" + args.device_name + "'");
    }
    var parameter = null;
    var total = countChildren(device, "parameters");
    for (var index = 0; index < total; index += 1) {
        var candidate = liveApi(path + " parameters " + index);
        if (String(getProperty(candidate, "name") || "") === args.parameter_name) {
            parameter = candidate;
            break;
        }
    }
    if (!parameter) {
        refuse("'" + args.device_name + "' has no parameter '" + args.parameter_name + "'");
    }
    var low = Number(getProperty(parameter, "min"));
    var high = Number(getProperty(parameter, "max"));
    /*
     * Max turns an item that looks like a number into a number: Wavetable's
     * filter slope arrives as [12, 24], not ["12", "24"]. Compare as text.
     */
    var raw = parameter.get("value_items") || [];
    var items = [];
    for (var at = 0; at < raw.length; at += 1) {
        items.push(String(raw[at]));
    }
    if (args.item !== undefined && args.item !== null && args.item !== "") {
        var position = items.indexOf(String(args.item));
        if (position < 0) {
            refuse("'" + args.parameter_name + "' has no setting '" + args.item + "'");
        }
        parameter.set("value", low + position);
    } else {
        var normalized = Number(args.value);
        if (!(normalized >= 0 && normalized <= 1)) {
            refuse("normalized value must be between 0 and 1");
        }
        parameter.set("value", low + normalized * (high - low));
    }
    var value = Number(getProperty(parameter, "value"));
    var readback = {
        track_index: target.track_index,
        master: target.master === true,
        device_index: target.device_index,
        parameter_name: args.parameter_name,
        normalized_value: high > low ? Math.round((value - low) / (high - low) * 1000) / 1000 : 0
    };
    if (items.length) {
        readback.item = String(items[Math.round(value - low)] || "");
    }
    return readback;
}

function decibelsOf(text) {
    var shown = String(text || "");
    if (shown.indexOf("inf") >= 0) {
        return -1000;
    }
    var parsed = parseFloat(shown);
    return isNaN(parsed) ? -1000 : parsed;
}

/*
 * Set a track's fader in dB and its pan in -1..1. The fader curve is not
 * published, so the value is found by bisection on Live's own dB display.
 */
function setTrackMixer(target, args) {
    var path = "live_set tracks " + target.track_index + " mixer_device";
    var volume = liveApi(path + " volume");
    var panning = liveApi(path + " panning");
    var wanted = Number(args.volume_db);
    var pan = Number(args.panning);
    if (isNaN(wanted) || wanted > 6 || !(pan >= -1 && pan <= 1)) {
        refuse("volume_db must be at most +6 dB and panning within -1..1");
    }
    var low = Number(getProperty(volume, "min"));
    var high = Number(getProperty(volume, "max"));
    for (var step = 0; step < 30; step += 1) {
        var middle = (low + high) / 2;
        if (decibelsOf(volume.call("str_for_value", middle)) < wanted) {
            low = middle;
        } else {
            high = middle;
        }
    }
    volume.set("value", high);
    panning.set("value", pan);
    var shown = decibelsOf(volume.call("str_for_value", Number(getProperty(volume, "value"))));
    return {
        track_index: target.track_index,
        volume_db: Math.round(shown * 10) / 10,
        panning: Math.round(Number(getProperty(panning, "value")) * 100) / 100
    };
}

function loadDrumPadSample(target, args) {
    if (!supportsInsertDevice() || !supportsReplaceSample()) {
        refuse(
            "automatic Drum Rack sample loading requires Ableton Live 12.4 or newer"
        );
    }
    var note = Number(args.note);
    var samplePath = String(args.sample_path || "");
    if (!note || !samplePath) {
        refuse("load_drum_pad_sample needs note and sample_path");
    }
    var rack = findDrumRack(target.track_index);
    if (!rack) {
        refuse("track " + target.track_index + " has no Drum Rack");
    }
    var pad = findDrumPad(rack.path, note);
    if (!pad) {
        refuse("Drum Rack has no pad for note " + note);
    }
    if (countChildren(pad.api, "chains") > 0) {
        return drumPadReadback(target.track_index, rack.index, note, true);
    }
    var before = countChildren(pad.api, "chains");
    pad.api.call("insert_chain");
    var after = countChildren(pad.api, "chains");
    if (after <= before) {
        refuse("Drum Rack chain was not inserted");
    }
    var chainPath = rack.path + " drum_pads " + pad.index + " chains " + (after - 1);
    var chain = liveApi(chainPath);
    chain.set("in_note", note);
    chain.call("insert_device", "Simpler");
    var deviceCount = countChildren(chain, "devices");
    if (deviceCount < 1) {
        refuse("Simpler was not inserted into the Drum Rack chain");
    }
    var simpler = liveApi(
        chainPath + " devices " + (deviceCount - 1)
    );
    simpler.call("replace_sample", samplePath);
    return drumPadReadback(target.track_index, rack.index, note, false);
}

function replaceDrumPadSample(target, args) {
    if (!supportsReplaceSample()) {
        refuse("automatic sample replacement requires Ableton Live 12.4 or newer");
    }
    var note = Number(args.note);
    var samplePath = String(args.sample_path || "");
    var rack = findDrumRack(target.track_index);
    var pad = rack ? findDrumPad(rack.path, note) : null;
    if (!rack || !pad || countChildren(pad.api, "chains") !== 1) {
        refuse("target Drum Rack pad must contain exactly one chain");
    }
    var chainPath = rack.path + " drum_pads " + pad.index + " chains 0";
    var chain = liveApi(chainPath);
    if (countChildren(chain, "devices") !== 1) {
        refuse("target Drum Rack pad must contain exactly one device");
    }
    var simpler = liveApi(chainPath + " devices 0");
    if (String(getProperty(simpler, "class_name") || "") !== "Simpler") {
        refuse("target Drum Rack pad device is not Simpler");
    }
    simpler.call("replace_sample", samplePath);
    return drumPadReadback(target.track_index, rack.index, note, false);
}

function drumPadSamplePath(rackPath, padIndex) {
    var chainPath = rackPath + " drum_pads " + padIndex + " chains 0";
    var chain = liveApi(chainPath);
    if (countChildren(chain, "devices") !== 1) {
        return "";
    }
    var simpler = liveApi(chainPath + " devices 0");
    var direct = String(
        getProperty(simpler, "sample_file_path") ||
        getProperty(simpler, "file_path") ||
        ""
    );
    if (direct) {
        return direct;
    }
    var sample = liveApi(chainPath + " devices 0 sample");
    return String(getProperty(sample, "file_path") || "");
}

function findDrumRack(trackIndex) {
    var track = liveApi("live_set tracks " + trackIndex);
    var deviceCount = countChildren(track, "devices");
    for (var index = 0; index < deviceCount; index += 1) {
        var path = "live_set tracks " + trackIndex + " devices " + index;
        var device = liveApi(path);
        if (Number(getProperty(device, "can_have_drum_pads") || 0)) {
            return { api: device, path: path, index: index };
        }
    }
    return null;
}

function findDrumPad(rackPath, note) {
    var rack = liveApi(rackPath);
    var padCount = countChildren(rack, "drum_pads");
    for (var index = 0; index < padCount; index += 1) {
        var pad = liveApi(rackPath + " drum_pads " + index);
        if (Number(getProperty(pad, "note")) === Number(note)) {
            return { api: pad, index: index };
        }
    }
    return null;
}

function drumPadReadback(trackIndex, deviceIndex, note, alreadyOccupied) {
    var pad = findDrumPad(
        "live_set tracks " + trackIndex + " devices " + deviceIndex,
        note
    );
    var occupied = pad ? countChildren(pad.api, "chains") > 0 : false;
    var samplePath = "";
    if (occupied) {
        var chain = liveApi(
            "live_set tracks " + trackIndex + " devices " + deviceIndex +
            " drum_pads " + pad.index + " chains 0"
        );
        if (countChildren(chain, "devices") > 0) {
            var simpler = liveApi(
                "live_set tracks " + trackIndex + " devices " + deviceIndex +
                " drum_pads " + pad.index + " chains 0 devices 0"
            );
            samplePath = String(
                getProperty(simpler, "sample_file_path") ||
                getProperty(simpler, "file_path") ||
                ""
            );
            if (!samplePath) {
                var sample = liveApi(
                    "live_set tracks " + trackIndex + " devices " + deviceIndex +
                    " drum_pads " + pad.index + " chains 0 devices 0 sample"
                );
                samplePath = String(getProperty(sample, "file_path") || "");
            }
        }
    }
    return {
        track_index: trackIndex,
        note: note,
        occupied: occupied,
        already_occupied: alreadyOccupied ? true : false,
        sample_path: samplePath
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

function locatorAt(beats) {
    var song = songApi();
    var total = countChildren(song, "cue_points");
    for (var index = 0; index < total; index += 1) {
        var cue = liveApi("live_set cue_points " + index);
        if (Math.abs(Number(getProperty(cue, "time")) - Number(beats)) < 0.000001) {
            return cue;
        }
    }
    return null;
}

function locatorReadback(cue) {
    return {
        name: String(getProperty(cue, "name") || ""),
        beats: Number(getProperty(cue, "time"))
    };
}

function readLocatorSummary() {
    var song = songApi();
    var total = countChildren(song, "cue_points");
    var locators = [];
    for (var index = 0; index < total; index += 1) {
        var cue = liveApi("live_set cue_points " + index);
        locators.push(locatorReadback(cue));
    }
    return locators;
}

function readDrumRackSummary(trackIndex) {
    var track = liveApi("live_set tracks " + trackIndex);
    var deviceCount = countChildren(track, "devices");
    var devices = [];
    for (var deviceIndex = 0; deviceIndex < deviceCount; deviceIndex += 1) {
        var device = liveApi(
            "live_set tracks " + trackIndex + " devices " + deviceIndex
        );
        var item = {
            device_index: deviceIndex,
            name: String(getProperty(device, "name") || ""),
            class_display_name: String(
                getProperty(device, "class_display_name") || ""
            ),
            can_have_drum_pads: Boolean(
                Number(getProperty(device, "can_have_drum_pads") || 0)
            ),
            chain_count: countChildren(device, "chains"),
            occupied_pads: []
        };
        if (item.can_have_drum_pads) {
            var padCount = countChildren(device, "drum_pads");
            for (var padIndex = 0; padIndex < padCount; padIndex += 1) {
                var pad = liveApi(
                    "live_set tracks " + trackIndex + " devices " +
                    deviceIndex + " drum_pads " + padIndex
                );
                var chainCount = countChildren(pad, "chains");
                if (chainCount > 0) {
                    item.occupied_pads.push({
                        note: Number(getProperty(pad, "note")),
                        name: String(getProperty(pad, "name") || ""),
                        chain_count: chainCount
                    });
                }
            }
        }
        devices.push(item);
    }
    return {
        track_index: trackIndex,
        track_name: String(getProperty(track, "name") || ""),
        devices: devices
    };
}

/*
 * Every parameter of one device, read only: what set_device_parameter will
 * accept by name, and the value_items a switch takes. Recipes are checked
 * against this before a device knob is ever set.
 */
function readDeviceParameters(payload) {
    var base = payload.master === true
        ? "live_set master_track"
        : "live_set tracks " + Number(payload.track_index);
    var owner = liveApi(base);
    var deviceIndex = Number(payload.device_index);
    if (!(deviceIndex >= 0 && deviceIndex < countChildren(owner, "devices"))) {
        refuse("no device " + payload.device_index + " on " + base);
    }
    var path = base + " devices " + deviceIndex;
    var device = liveApi(path);
    var total = countChildren(device, "parameters");
    var parameters = [];
    for (var index = 0; index < total; index += 1) {
        var parameter = liveApi(path + " parameters " + index);
        var raw = parameter.get("value_items") || [];
        var items = [];
        for (var at = 0; at < raw.length; at += 1) {
            items.push(String(raw[at]));
        }
        var low = Number(getPropertyOrNull(parameter, "min"));
        var high = Number(getPropertyOrNull(parameter, "max"));
        var quantized = Boolean(Number(getPropertyOrNull(parameter, "is_quantized") || 0));
        var item = {
            name: String(getPropertyOrNull(parameter, "name") || ""),
            min: low,
            max: high,
            is_quantized: quantized,
            value_items: items,
            value: Number(getPropertyOrNull(parameter, "value"))
        };
        /*
         * What Live's dial shows at eleven even steps of the range, so a
         * recipe can be written in dB, Hz or ms instead of guessing the curve.
         */
        if (payload.displays === true && !quantized) {
            item.displays = [];
            for (var step = 0; step <= 10; step += 1) {
                var shown = "";
                try {
                    shown = String(parameter.call("str_for_value", low + (high - low) * step / 10));
                } catch (displayError) {
                    shown = "";
                }
                item.displays.push(shown);
            }
        }
        parameters.push(item);
    }
    var reply = {
        device_index: deviceIndex,
        device_name: String(getProperty(device, "name") || ""),
        class_name: String(getPropertyOrNull(device, "class_name") || ""),
        parameters: parameters
    };
    var sidechain = readSidechainRouting(device);
    if (sidechain) {
        reply.sidechain = sidechain;
    }
    return reply;
}

/*
 * LiveAPI hands a routing property over as a JSON string wrapped in an array,
 * keyed by the property's own name.
 */
function routingProperty(device, name) {
    var raw = device.get(name);
    if (raw === null || raw === undefined) {
        return null;
    }
    var text = raw instanceof Array ? raw.join(" ") : String(raw);
    var parsed = JSON.parse(text);
    return parsed && parsed[name] !== undefined ? parsed[name] : parsed;
}

/*
 * Route a Compressor's sidechain input from the track named args.source_name.
 * Max's JS bridge has taken a routing both as an object and as JSON text in
 * different versions, so each form is tried and only a read-back that names
 * the source counts as success.
 */
function setSidechainSource(target, args) {
    var path = trackPathOf(target) + " devices " + target.device_index;
    var device = liveApi(path);
    if (String(getProperty(device, "name") || "") !== args.device_name) {
        refuse("device " + target.device_index + " is not '" + args.device_name + "'");
    }
    var types = routingProperty(device, "available_input_routing_types") || [];
    var chosen = null;
    for (var index = 0; index < types.length; index += 1) {
        if (String(types[index].display_name || "") === args.source_name) {
            chosen = types[index];
        }
    }
    if (!chosen) {
        refuse("'" + args.source_name + "' is not offered as a sidechain source");
    }
    var attempts = [
        { identifier: chosen.identifier },
        JSON.stringify({ identifier: chosen.identifier }),
        JSON.stringify({ input_routing_type: { identifier: chosen.identifier } })
    ];
    var current = "";
    for (var at = 0; at < attempts.length && current !== args.source_name; at += 1) {
        try {
            device.set("input_routing_type", attempts[at]);
        } catch (setError) {
            /* try the next form */
        }
        current = String((routingProperty(device, "input_routing_type") || {}).display_name || "");
    }
    if (current !== args.source_name) {
        refuse("Live did not accept '" + args.source_name + "' as the sidechain source");
    }
    return {
        track_index: target.track_index,
        device_index: target.device_index,
        input_routing_type: current,
        input_routing_channel: String((routingProperty(device, "input_routing_channel") || {}).display_name || "")
    };
}

/* Compressor and other sidechain devices expose these from Live 11; null otherwise. */
function readSidechainRouting(device) {
    try {
        var types = routingProperty(device, "available_input_routing_types");
        if (!types || !types.length) {
            return null;
        }
        var names = [];
        for (var index = 0; index < types.length; index += 1) {
            names.push(String(types[index].display_name || ""));
        }
        var current = routingProperty(device, "input_routing_type") || {};
        var channel = routingProperty(device, "input_routing_channel") || {};
        return {
            available_types: names,
            input_routing_type: String(current.display_name || ""),
            input_routing_channel: String(channel.display_name || "")
        };
    } catch (routingError) {
        return null;
    }
}

function parameterValue(path) {
    var parameter = liveApi(path);
    return {
        name: String(getPropertyOrNull(parameter, "name") || ""),
        value: getPropertyOrNull(parameter, "value"),
        display_value: getPropertyOrNull(parameter, "display_value"),
        automation_state: getPropertyOrNull(parameter, "automation_state"),
        is_enabled: getPropertyOrNull(parameter, "is_enabled")
    };
}

function midiPitches(clip) {
    if (!getPropertyOrNull(clip, "is_midi_clip")) {
        return [];
    }
    var length = Number(getPropertyOrNull(clip, "length") || 0);
    var raw = clip.call("get_notes_extended", 0, 128, 0, length);
    var notes = [];
    try {
        notes = JSON.parse(raw).notes || [];
    } catch (error) {
        return [];
    }
    var seen = {};
    var pitches = [];
    for (var index = 0; index < notes.length; index += 1) {
        var pitch = Number(notes[index].pitch);
        if (!seen[pitch]) {
            seen[pitch] = true;
            pitches.push(pitch);
        }
    }
    pitches.sort(function (left, right) { return left - right; });
    return pitches;
}

function firstClipDiagnostic(trackIndex, childName) {
    var track = liveApi("live_set tracks " + trackIndex);
    var total = countChildren(track, childName);
    for (var index = 0; index < total; index += 1) {
        var path = "live_set tracks " + trackIndex + " " + childName + " " + index;
        if (childName === "clip_slots") {
            var slot = liveApi(path);
            if (!getPropertyOrNull(slot, "has_clip")) {
                continue;
            }
            path += " clip";
        }
        var clip = liveApi(path);
        return {
            name: String(getPropertyOrNull(clip, "name") || ""),
            note_count: countNotes(clip),
            pitches: midiPitches(clip)
        };
    }
    return null;
}

function readTrackPlaybackSummary(trackIndex) {
    var trackPath = "live_set tracks " + trackIndex;
    var track = liveApi(trackPath);
    var devices = [];
    var deviceCount = countChildren(track, "devices");
    for (var deviceIndex = 0; deviceIndex < deviceCount; deviceIndex += 1) {
        var devicePath = trackPath + " devices " + deviceIndex;
        var device = liveApi(devicePath);
        var deviceItem = {
            device_index: deviceIndex,
            name: String(getPropertyOrNull(device, "name") || ""),
            class_display_name: String(
                getPropertyOrNull(device, "class_display_name") || ""
            ),
            is_active: getPropertyOrNull(device, "is_active"),
            chains: []
        };
        var chainCount = countChildren(device, "chains");
        for (var chainIndex = 0; chainIndex < chainCount; chainIndex += 1) {
            var chainPath = devicePath + " chains " + chainIndex;
            var chain = liveApi(chainPath);
            deviceItem.chains.push({
                chain_index: chainIndex,
                name: String(getPropertyOrNull(chain, "name") || ""),
                mute: getPropertyOrNull(chain, "mute"),
                solo: getPropertyOrNull(chain, "solo"),
                in_note: getPropertyOrNull(chain, "in_note"),
                out_note: getPropertyOrNull(chain, "out_note"),
                chain_activator: parameterValue(
                    chainPath + " mixer_device chain_activator"
                ),
                volume: parameterValue(chainPath + " mixer_device volume"),
                device_count: countChildren(chain, "devices")
            });
        }
        devices.push(deviceItem);
    }
    return {
        track_index: trackIndex,
        track_name: String(getPropertyOrNull(track, "name") || ""),
        mute: getPropertyOrNull(track, "mute"),
        solo: getPropertyOrNull(track, "solo"),
        arm: getPropertyOrNull(track, "arm"),
        current_monitoring_state: getPropertyOrNull(
            track, "current_monitoring_state"
        ),
        track_activator: parameterValue(
            trackPath + " mixer_device track_activator"
        ),
        volume: parameterValue(trackPath + " mixer_device volume"),
        session_clip: firstClipDiagnostic(trackIndex, "clip_slots"),
        arrangement_clip: firstClipDiagnostic(trackIndex, "arrangement_clips"),
        devices: devices
    };
}

function readPlaybackContextSummary(trackIndexes) {
    var song = songApi();
    var tracks = [];
    for (var index = 0; index < trackIndexes.length; index += 1) {
        var trackIndex = Number(trackIndexes[index]);
        var trackPath = "live_set tracks " + trackIndex;
        var track = liveApi(trackPath);
        var nestedDevices = [];
        var rackCount = countChildren(track, "devices");
        for (var rackIndex = 0; rackIndex < rackCount; rackIndex += 1) {
            var rackPath = trackPath + " devices " + rackIndex;
            var rack = liveApi(rackPath);
            var chainCount = countChildren(rack, "chains");
            for (var chainIndex = 0; chainIndex < chainCount; chainIndex += 1) {
                var chainPath = rackPath + " chains " + chainIndex;
                var chain = liveApi(chainPath);
                var nestedCount = countChildren(chain, "devices");
                for (var deviceIndex = 0; deviceIndex < nestedCount; deviceIndex += 1) {
                    var nestedPath = chainPath + " devices " + deviceIndex;
                    var nested = liveApi(nestedPath);
                    var samplePath = nestedPath + " sample";
                    var sample = liveApi(samplePath);
                    nestedDevices.push({
                        rack_index: rackIndex,
                        chain_index: chainIndex,
                        device_index: deviceIndex,
                        name: String(getPropertyOrNull(nested, "name") || ""),
                        class_display_name: String(
                            getPropertyOrNull(nested, "class_display_name") || ""
                        ),
                        is_active: getPropertyOrNull(nested, "is_active"),
                        sample_file_path: String(
                            getPropertyOrNull(sample, "file_path") || ""
                        )
                    });
                }
            }
        }
        var activeArrangementClip = null;
        var songTime = Number(getPropertyOrNull(song, "current_song_time") || 0);
        var arrangementCount = countChildren(track, "arrangement_clips");
        for (var clipIndex = 0; clipIndex < arrangementCount; clipIndex += 1) {
            var clip = liveApi(trackPath + " arrangement_clips " + clipIndex);
            var clipStart = Number(getPropertyOrNull(clip, "start_time") || 0);
            var clipEnd = Number(getPropertyOrNull(clip, "end_time") || clipStart);
            if (songTime >= clipStart && songTime < clipEnd) {
                activeArrangementClip = {
                    name: String(getPropertyOrNull(clip, "name") || ""),
                    start_beats: clipStart,
                    end_beats: clipEnd,
                    muted: getPropertyOrNull(clip, "muted"),
                    is_playing: getPropertyOrNull(clip, "is_playing"),
                    pitches: midiPitches(clip)
                };
                break;
            }
        }
        tracks.push({
            track_index: trackIndex,
            name: String(getPropertyOrNull(track, "name") || ""),
            playing_slot_index: getPropertyOrNull(track, "playing_slot_index"),
            fired_slot_index: getPropertyOrNull(track, "fired_slot_index"),
            output_routing_type: getPropertyOrNull(track, "output_routing_type"),
            output_routing_channel: getPropertyOrNull(
                track, "output_routing_channel"
            ),
            output_meter_level: getPropertyOrNull(track, "output_meter_level"),
            muted_via_solo: getPropertyOrNull(track, "muted_via_solo"),
            active_arrangement_clip: activeArrangementClip,
            nested_devices: nestedDevices
        });
    }
    return {
        is_playing: Boolean(getPropertyOrNull(song, "is_playing")),
        current_song_time: Number(
            getPropertyOrNull(song, "current_song_time") || 0
        ),
        back_to_arranger: getPropertyOrNull(song, "back_to_arranger"),
        master_volume: parameterValue(
            "live_set master_track mixer_device volume"
        ),
        tracks: tracks
    };
}

function applyMaskingReduction(requestId) {
    var targets = [
        { index: 5, name: "Bass [KIHACHI]" },
        { index: 7, name: "Stab [KIHACHI]" }
    ];
    var before = [];
    var parameters = [];
    for (var index = 0; index < targets.length; index += 1) {
        var target = targets[index];
        var track = liveApi("live_set tracks " + target.index);
        var name = String(getProperty(track, "name") || "");
        if (name !== target.name) {
            respondError(requestId, "operation_failed", "mix target track mismatch");
            return;
        }
        var parameter = liveApi(
            "live_set tracks " + target.index + " mixer_device volume"
        );
        var value = Number(getProperty(parameter, "value"));
        var displayValue = Number(getProperty(parameter, "display_value"));
        var automationState = Number(
            getPropertyOrNull(parameter, "automation_state") || 0
        );
        var enabled = getPropertyOrNull(parameter, "is_enabled");
        if (
            Math.abs(value - 0.85) > 0.0001 ||
            !isFinite(displayValue) ||
            automationState !== 0 ||
            enabled === 0 ||
            enabled === false
        ) {
            respondError(
                requestId,
                "operation_failed",
                "mix target changed or its volume is automated/disabled"
            );
            return;
        }
        parameters.push(parameter);
        before.push({
            track_index: target.index,
            track_name: name,
            value: value,
            display_value: displayValue
        });
    }

    try {
        for (var setIndex = 0; setIndex < parameters.length; setIndex += 1) {
            parameters[setIndex].set(
                "display_value", before[setIndex].display_value - 1.5
            );
        }
    } catch (error) {
        for (
            var rollbackIndex = 0;
            rollbackIndex < parameters.length;
            rollbackIndex += 1
        ) {
            try {
                parameters[rollbackIndex].set(
                    "display_value", before[rollbackIndex].display_value
                );
            } catch (rollbackError) {
                /* Report the original error; the user can inspect Live directly. */
            }
        }
        respondError(
            requestId,
            "operation_failed",
            error && error.message ? error.message : String(error)
        );
        return;
    }

    var verifyTask = new Task(function () {
        var after = [];
        for (
            var verifyIndex = 0;
            verifyIndex < targets.length;
            verifyIndex += 1
        ) {
            after.push({
                track_index: targets[verifyIndex].index,
                track_name: targets[verifyIndex].name,
                value: getProperty(parameters[verifyIndex], "value"),
                display_value: getProperty(
                    parameters[verifyIndex], "display_value"
                )
            });
        }
        respondOk(requestId, {
            applied_delta_db: -1.5,
            before: before,
            after: after
        });
        arguments.callee.task.freepeer();
    }, this);
    verifyTask.schedule(100);
}

function applyLocatorAndRespond(requestId, operation) {
    var args = operation.arguments || {};
    var existing = locatorAt(args.beats);
    if (existing !== null) {
        var observed = locatorReadback(existing);
        if (observed.name !== args.name) {
            respondError(
                requestId,
                "operation_failed",
                "locator position is already occupied by '" + observed.name + "'"
            );
            return;
        }
        respondOk(requestId, {
            operation_id: operation.operation_id,
            op: operation.op,
            observed: observed
        });
        return;
    }

    var song = songApi();
    var previous = Number(getProperty(song, "current_song_time") || 0);
    song.set("current_song_time", args.beats);
    var createTask = new Task(function () {
        try {
            var positioned = Number(getProperty(song, "current_song_time") || 0);
            if (Math.abs(positioned - Number(args.beats)) >= 0.000001) {
                song.set("current_song_time", previous);
                respondError(
                    requestId,
                    "operation_failed",
                    "Live did not move to locator beat " + args.beats
                );
                return;
            }
            song.call("set_or_delete_cue");
            var verifyTask = new Task(function () {
                try {
                    var created = locatorAt(args.beats);
                    if (created === null) {
                        refuse("locator at beat " + args.beats + " was not created");
                    }
                    created.set("name", args.name);
                    var observed = locatorReadback(created);
                    song.set("current_song_time", previous);
                    respondOk(requestId, {
                        operation_id: operation.operation_id,
                        op: operation.op,
                        observed: observed
                    });
                } catch (error) {
                    song.set("current_song_time", previous);
                    var message = error && error.message ? error.message : String(error);
                    respondError(requestId, "operation_failed", message);
                }
                arguments.callee.task.freepeer();
            }, this);
            verifyTask.schedule(75);
        } catch (error) {
            song.set("current_song_time", previous);
            var message = error && error.message ? error.message : String(error);
            respondError(requestId, "operation_failed", message);
        }
        arguments.callee.task.freepeer();
    }, this);
    createTask.schedule(75);
}

function placeArrangementClip(target, args) {
    var slot = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index
    );
    if (!getProperty(slot, "has_clip")) {
        refuse("source session clip is missing");
    }
    var source = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index + " clip"
    );
    var track = liveApi("live_set tracks " + target.track_index);
    var before = countChildren(track, "arrangement_clips");
    track.call(
        "duplicate_clip_to_arrangement", "id " + source.id, args.start_beats
    );
    var after = countChildren(track, "arrangement_clips");
    if (after <= before) {
        refuse("arrangement clip was not created");
    }
    var clip = liveApi(
        "live_set tracks " + target.track_index +
        " arrangement_clips " + (after - 1)
    );
    clip.set("name", args.name);
    /*
     * The duplicated Session pattern keeps its original loop length. Moving
     * the Arrangement clip's end marker extends its right edge so that Live
     * repeats that pattern over the approved section duration.
     */
    clip.set("looping", 1);
    clip.set("end_marker", args.length_beats);
    var clipStart = Number(getProperty(clip, "start_time") || 0);
    var clipEnd = Number(getProperty(clip, "end_time") || clipStart);
    return {
        track_index: target.track_index,
        name: String(getProperty(clip, "name") || ""),
        start_beats: clipStart,
        length_beats: clipEnd - clipStart,
        note_count: countNotes(clip)
    };
}

function arrangementClipAt(trackIndex, startBeats) {
    var track = liveApi("live_set tracks " + trackIndex);
    var total = countChildren(track, "arrangement_clips");
    for (var index = 0; index < total; index += 1) {
        var clip = liveApi(
            "live_set tracks " + trackIndex + " arrangement_clips " + index
        );
        if (
            Math.abs(
                Number(getProperty(clip, "start_time")) - Number(startBeats)
            ) < 0.000001
        ) {
            return clip;
        }
    }
    return null;
}

function managedArrangementClip(trackIndex, name, startBeats) {
    var clip = arrangementClipAt(trackIndex, startBeats);
    if (clip === null) {
        return null;
    }
    var observedName = String(getProperty(clip, "name") || "");
    if (observedName !== name || !isManagedName(observedName)) {
        return null;
    }
    return clip;
}

function applyArrangementDeleteAndRespond(requestId, operation) {
    var target = operation.target || {};
    var args = operation.arguments || {};
    var clip = managedArrangementClip(
        target.track_index, args.name, args.start_beats
    );
    if (clip === null) {
        respondError(
            requestId, "operation_failed", "managed arrangement clip mismatch"
        );
        return;
    }
    var track = liveApi("live_set tracks " + target.track_index);
    track.call("delete_clip", "id " + clip.id);
    var task = new Task(function () {
        try {
            if (
                managedArrangementClip(
                    target.track_index, args.name, args.start_beats
                ) !== null
            ) {
                refuse("managed arrangement clip was not deleted");
            }
            respondOk(requestId, {
                operation_id: operation.operation_id,
                op: operation.op,
                observed: {
                    track_index: target.track_index,
                    name: args.name,
                    start_beats: args.start_beats,
                    deleted: true
                }
            });
        } catch (error) {
            var message = error && error.message ? error.message : String(error);
            respondError(requestId, "operation_failed", message);
        }
        arguments.callee.task.freepeer();
    }, this);
    task.schedule(75);
}

function applyLocatorDeleteAndRespond(requestId, operation) {
    var args = operation.arguments || {};
    var cue = locatorAt(args.beats);
    if (
        cue === null ||
        String(getProperty(cue, "name") || "") !== args.name ||
        !isManagedName(args.name)
    ) {
        respondError(requestId, "operation_failed", "managed locator mismatch");
        return;
    }
    var song = songApi();
    var previous = Number(getProperty(song, "current_song_time") || 0);
    song.set("current_song_time", args.beats);
    var moveTask = new Task(function () {
        try {
            var positioned = Number(getProperty(song, "current_song_time") || 0);
            if (Math.abs(positioned - Number(args.beats)) >= 0.000001) {
                refuse("Live did not move to locator beat " + args.beats);
            }
            song.call("set_or_delete_cue");
            var verifyTask = new Task(function () {
                try {
                    if (locatorAt(args.beats) !== null) {
                        refuse("managed locator was not deleted");
                    }
                    song.set("current_song_time", previous);
                    respondOk(requestId, {
                        operation_id: operation.operation_id,
                        op: operation.op,
                        observed: {
                            name: args.name, beats: args.beats, deleted: true
                        }
                    });
                } catch (error) {
                    song.set("current_song_time", previous);
                    var message = error && error.message ? error.message : String(error);
                    respondError(requestId, "operation_failed", message);
                }
                arguments.callee.task.freepeer();
            }, this);
            verifyTask.schedule(75);
        } catch (error) {
            song.set("current_song_time", previous);
            var message = error && error.message ? error.message : String(error);
            respondError(requestId, "operation_failed", message);
        }
        arguments.callee.task.freepeer();
    }, this);
    moveTask.schedule(75);
}

function arrangementClipReadback(clip, trackIndex) {
    var clipStart = Number(getProperty(clip, "start_time") || 0);
    var clipEnd = Number(getProperty(clip, "end_time") || clipStart);
    return {
        track_index: trackIndex,
        name: String(getProperty(clip, "name") || ""),
        start_beats: clipStart,
        length_beats: clipEnd - clipStart,
        note_count: countNotes(clip)
    };
}

function applyArrangementClipAndRespond(requestId, operation) {
    var target = operation.target || {};
    var args = operation.arguments || {};
    var slot = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index
    );
    if (!getProperty(slot, "has_clip")) {
        respondError(requestId, "operation_failed", "source session clip is missing");
        return;
    }
    var source = liveApi(
        "live_set tracks " + target.track_index +
        " clip_slots " + target.scene_index + " clip"
    );
    var track = liveApi("live_set tracks " + target.track_index);
    track.call(
        "duplicate_clip_to_arrangement", "id " + source.id, args.start_beats
    );

    var createTask = new Task(function () {
        try {
            var created = arrangementClipAt(target.track_index, args.start_beats);
            if (created === null) {
                refuse("arrangement clip was not created");
            }
            created.set("name", args.name);
            created.set("looping", 1);
            created.set("end_marker", args.length_beats);
            var verifyTask = new Task(function () {
                try {
                    var current = arrangementClipAt(
                        target.track_index, args.start_beats
                    );
                    if (current === null) {
                        refuse("arrangement clip disappeared before readback");
                    }
                    respondOk(requestId, {
                        operation_id: operation.operation_id,
                        op: operation.op,
                        observed: arrangementClipReadback(
                            current, target.track_index
                        )
                    });
                } catch (error) {
                    var message = error && error.message ? error.message : String(error);
                    respondError(requestId, "operation_failed", message);
                }
                arguments.callee.task.freepeer();
            }, this);
            verifyTask.schedule(75);
        } catch (error) {
            var message = error && error.message ? error.message : String(error);
            respondError(requestId, "operation_failed", message);
        }
        arguments.callee.task.freepeer();
    }, this);
    createTask.schedule(75);
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
    if (request.bridge_authenticated !== true) {
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
            live_version: liveVersionString(),
            protocol_version: PROTOCOL_VERSION,
            device_version: DEVICE_VERSION
        });
        return;
    }
    if (request.method === "get_state") {
        respondOk(requestId, readState(request.payload || {}));
        return;
    }
    if (request.method === "get_arrangement_summary") {
        var song = songApi();
        respondOk(requestId, {
            arrangement_clips: readArrangementClips(
                countChildren(song, "tracks")
            )
        });
        return;
    }
    if (request.method === "get_arrangement_track_summary") {
        var payload = request.payload || {};
        respondOk(requestId, {
            arrangement_clips: readArrangementTrack(
                Number(payload.track_index)
            )
        });
        return;
    }
    if (request.method === "get_locator_summary") {
        respondOk(requestId, { locators: readLocatorSummary() });
        return;
    }
    if (request.method === "get_drum_rack_summary") {
        var drumPayload = request.payload || {};
        respondOk(
            requestId,
            readDrumRackSummary(Number(drumPayload.track_index))
        );
        return;
    }
    if (request.method === "get_device_parameters") {
        try {
            respondOk(requestId, readDeviceParameters(request.payload || {}));
        } catch (error) {
            respondError(
                requestId,
                "operation_failed",
                error && error.message ? error.message : String(error)
            );
        }
        return;
    }
    if (request.method === "get_track_playback_summary") {
        var playbackPayload = request.payload || {};
        respondOk(
            requestId,
            readTrackPlaybackSummary(Number(playbackPayload.track_index))
        );
        return;
    }
    if (request.method === "get_playback_context_summary") {
        var contextPayload = request.payload || {};
        respondOk(
            requestId,
            readPlaybackContextSummary(contextPayload.track_indexes || [])
        );
        return;
    }
    if (request.method === "apply_masking_reduction") {
        applyMaskingReduction(requestId);
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
        if (operation.op === "create_locator") {
            applyLocatorAndRespond(requestId, operation);
            return;
        }
        if (operation.op === "place_arrangement_clip") {
            applyArrangementClipAndRespond(requestId, operation);
            return;
        }
        if (operation.op === "delete_arrangement_clip") {
            applyArrangementDeleteAndRespond(requestId, operation);
            return;
        }
        if (operation.op === "delete_locator") {
            applyLocatorDeleteAndRespond(requestId, operation);
            return;
        }
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
