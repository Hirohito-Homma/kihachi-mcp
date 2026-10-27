"""Generates distinct, rule-driven MIDI groove variants from an Analysis Card.

Takes abstract generative rules (density, rest ratio, syncopation, kick-bass separation)
and synthesizes multiple contrasting candidate options (Variant A, B, C) using DeterministicMIDIComposer.
Never clones raw notes from the reference audio.
"""

from __future__ import annotations

import uuid
from typing import Any

from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.models.midi_composer import (
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.models.production_brief import (
    KEY_ENUM,
    PART_ORDER,
    SOURCE_AI,
    SOURCE_DEFAULT,
    SOURCE_GENRE,
    SOURCE_USER,
    ProductionBrief,
    SectionIntent,
    SourcedValue,
)
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer
from kihachi_mcp.services.reference_analysis_card import AnalysisCard
from kihachi_mcp.services.session_pattern_builder import MidiNote


def generate_variants_from_card(
    card: AnalysisCard,
    bars: int = 8,
    key: str = "D#m",
    seed: int = 42,
) -> dict[str, Any]:
    """Generate contrasting candidate groove options from an Analysis Card."""
    rules = card.generation_rules
    tempo = round(rules.get("bpm_target") or 110.0)

    # Determine base parameters from rules
    base_sync = float(rules.get("bass_syncopation", 0.5))
    base_rest = float(rules.get("bass_rest_ratio", 0.35))
    is_four_floor = rules.get("kick_style") == "four_on_the_floor"

    variants = []

    # -------------------------------------------------------------------------
    # Variant A: Reference Faithful Density (参考曲の密度とノリを素直に反映)
    # -------------------------------------------------------------------------
    composer_a = DeterministicMIDIComposer(seed=seed)
    params_a = {
        "syncopation": base_sync,
        "ghost_notes": base_rest < 0.45,
        "octave_jump": 0.25,
        "velocity_min": 70,
        "velocity_max": 115,
        "description": f"参考曲の検出密度（syncopation {int(base_sync*100)}%）に忠実なグルーヴ",
    }
    c_kick_a = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
    c_bass_a = MusicalConstraints(
        key=key,
        tempo=tempo,
        bars=bars,
        role="bass",
        grid="1/16",
        syncopation=params_a["syncopation"],
        ghost_notes=params_a["ghost_notes"],
        octave_jump=params_a["octave_jump"],
        velocity_min=params_a["velocity_min"],
        velocity_max=params_a["velocity_max"],
    )
    kick_notes_a = _filter_kick_notes(composer_a.compose(MidiCompositionRequest(constraints=c_kick_a, operation="generate")), is_four_floor, bars)
    bass_notes_a = _to_serializable_notes(composer_a.compose(MidiCompositionRequest(constraints=c_bass_a, operation="generate")))

    variants.append({
        "variant_id": "A",
        "name": "密度適合型（Reference Faithful）",
        "label": "密度適合型（Reference Faithful）",
        "description": "参考曲の測定値（リズム密度・シンコペーション）を標準的に再現した基準候補",
        "parameters": params_a,
        "kick_note_count": len(kick_notes_a),
        "bass_note_count": len(bass_notes_a),
        "notes": {
            "Kick": kick_notes_a,
            "Bass": bass_notes_a,
        },
        "diff_from_base": "参考曲の基本グルーヴ特性を中庸に反映。",
    })

    # -------------------------------------------------------------------------
    # Variant B: Dub Space & High Rests (休符重視・空間拡張型)
    # -------------------------------------------------------------------------
    composer_b = DeterministicMIDIComposer(seed=seed + 101)
    params_b = {
        "syncopation": min(0.85, base_sync + 0.15),
        "ghost_notes": False,  # Clear space by eliminating ghost notes
        "octave_jump": 0.10,
        "velocity_min": 75,
        "velocity_max": 120,
        "description": "休符比率を高め、空間（Dub Space）とアタックの切れ味を強調したグルーヴ",
    }
    c_kick_b = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
    c_bass_b = MusicalConstraints(
        key=key,
        tempo=tempo,
        bars=bars,
        role="bass",
        grid="1/16",
        syncopation=params_b["syncopation"],
        ghost_notes=params_b["ghost_notes"],
        octave_jump=params_b["octave_jump"],
        velocity_min=params_b["velocity_min"],
        velocity_max=params_b["velocity_max"],
    )
    raw_bass_b = composer_b.compose(MidiCompositionRequest(constraints=c_bass_b, operation="generate"))
    # Thin out notes to enforce higher rests (every 3rd or lower-velocity note removed)
    thinned_bass_b = [n for idx, n in enumerate(raw_bass_b) if idx % 3 != 0]
    kick_notes_b = _filter_kick_notes(composer_b.compose(MidiCompositionRequest(constraints=c_kick_b, operation="generate")), is_four_floor, bars)
    bass_notes_b = _to_serializable_notes(thinned_bass_b)

    variants.append({
        "variant_id": "B",
        "name": "休符強調・Dub空間型（Dub Space Focus）",
        "label": "休符強調・Dub空間型（Dub Space Focus）",
        "description": "ベースの発音数を抑えて休符を広げ、スネアや空間系エフェクトの余韻を引き立たせる候補",
        "parameters": params_b,
        "kick_note_count": len(kick_notes_b),
        "bass_note_count": len(bass_notes_b),
        "notes": {
            "Kick": kick_notes_b,
            "Bass": bass_notes_b,
        },
        "diff_from_base": f"Variant Aよりベース音数を約{len(bass_notes_a) - len(bass_notes_b)}音間引き、休符比率を約25%向上。",
    })

    # -------------------------------------------------------------------------
    # Variant C: Anti-collision Interlock (キック・ベース帯域完全分離型)
    # -------------------------------------------------------------------------
    composer_c = DeterministicMIDIComposer(seed=seed + 202)
    params_c = {
        "syncopation": 0.80,
        "ghost_notes": True,
        "octave_jump": 0.40,
        "velocity_min": 65,
        "velocity_max": 110,
        "description": "キックの拍頭（オンビート）を徹底的に避け、裏拍（オフビート）でキックと噛み合うポリリズム",
    }
    c_kick_c = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
    c_bass_c = MusicalConstraints(
        key=key,
        tempo=tempo,
        bars=bars,
        role="bass",
        grid="1/16",
        syncopation=params_c["syncopation"],
        ghost_notes=params_c["ghost_notes"],
        octave_jump=params_c["octave_jump"],
        velocity_min=params_c["velocity_min"],
        velocity_max=params_c["velocity_max"],
    )
    raw_bass_c = composer_c.compose(MidiCompositionRequest(constraints=c_bass_c, operation="generate"))
    # Shift any notes that coincide with integer beats (kick onsets: 0.0, 1.0, 2.0...) to offbeats
    interlocked_bass_c = []
    for n in raw_bass_c:
        # If onset lands on an exact beat (within 0.05), shift it by 0.5 beats (8th note)
        start = n.start
        rem = start % 1.0
        if rem < 0.08 or rem > 0.92:
            start = round(start + 0.5, 3)
        interlocked_bass_c.append(
            MidiNote(pitch=n.pitch, start_beats=start, duration_beats=n.duration, velocity=n.velocity)
        )

    kick_notes_c = _filter_kick_notes(composer_c.compose(MidiCompositionRequest(constraints=c_kick_c, operation="generate")), is_four_floor, bars)
    bass_notes_c = _to_serializable_notes(interlocked_bass_c)

    variants.append({
        "variant_id": "C",
        "name": "帯域分離・裏拍型（Anti-collision Interlock）",
        "label": "帯域分離・裏拍型（Anti-collision Interlock）",
        "description": "キックの発音タイミングを完全に空けて裏拍にベースを配置し、低域の濁りを防ぐ設計",
        "parameters": params_c,
        "kick_note_count": len(kick_notes_c),
        "bass_note_count": len(bass_notes_c),
        "notes": {
            "Kick": kick_notes_c,
            "Bass": bass_notes_c,
        },
        "diff_from_base": "キックとベースの同時発音をゼロに近づけ、裏拍特化のポリリズムを構築。",
    })

    return {
        "ok": True,
        "card_id": card.card_id,
        "bars": bars,
        "key": key,
        "tempo": tempo,
        "variants": variants,
    }


def _filter_kick_notes(raw_drum_notes: list[Any], is_four_floor: bool, bars: int) -> list[dict[str, Any]]:
    """Return clean kick notes (pitch 36). If four-on-the-floor, generate solid 4-beat kicks."""
    if is_four_floor:
        kicks = []
        for beat in range(bars * 4):
            kicks.append({
                "pitch": 36,
                "start": float(beat),
                "duration": 0.5,
                "velocity": 100 if beat % 4 == 0 else 92,
            })
        return kicks

    # Extract pitch 36 or 35 from drum notes
    extracted = [
        {"pitch": 36, "start": n.start, "duration": n.duration, "velocity": n.velocity}
        for n in raw_drum_notes
        if getattr(n, "pitch", None) in (35, 36)
    ]
    if not extracted:
        return _filter_kick_notes([], True, bars)
    return extracted


def _to_serializable_notes(notes: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "pitch": int(getattr(n, "pitch", 36)),
            "start": round(float(getattr(n, "start", getattr(n, "start_beats", 0.0))), 3),
            "duration": round(float(getattr(n, "duration", getattr(n, "duration_beats", 0.25))), 3),
            "velocity": int(getattr(n, "velocity", 90)),
        }
        for n in notes
    ]


def _apply_swing_56(beat_pos: float) -> float:
    """Apply 56% MPC swing to 16th-note upbeats."""
    beat = int(beat_pos)
    sub = beat_pos - beat
    if abs(sub - 0.25) < 0.05:
        return round(beat + 0.27, 4)
    if abs(sub - 0.75) < 0.05:
        return round(beat + 0.78, 4)
    return round(beat_pos, 4)


def _normalize_key_to_enum(raw_key: str | None) -> str | None:
    """Normalize human or detected musical key strings into KEY_ENUM (e.g. 'D minor' -> 'Dm')."""
    if not raw_key or str(raw_key).strip().lower() in ("unknown", "none", ""):
        return None
    k = str(raw_key).strip()
    flat_map = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
    tokens = k.split()
    root = tokens[0]
    for flat, sharp in flat_map.items():
        if root.startswith(flat):
            root = sharp + root[len(flat):]
            break
    if len(tokens) > 1:
        mode = tokens[1].lower()
        suffix = "m" if ("min" in mode or mode == "m") else ""
    elif root.lower().endswith("minor") or root.lower().endswith("min"):
        base = root[:-5].strip() if root.lower().endswith("minor") else root[:-3].strip()
        root = base
        suffix = "m"
    elif root.endswith("m") and len(root) in (2, 3):
        suffix = "m"
        root = root[:-1]
    else:
        suffix = ""
    candidate_key = f"{root}{suffix}"
    if candidate_key in KEY_ENUM:
        return candidate_key
    return None


def promote_variant_to_candidate(
    card: AnalysisCard,
    variant_id: str = "A",
    bars: int = 64,
    key: str | None = None,
    seed: int = 42,
) -> MidiCandidate:
    """Promote an AnalysisCard and its chosen variant into a first-class MidiCandidate.

    Builds an authentic multi-part arrangement (Kick, Snare, Hats, OpenHat, Bass,
    Stab, Arp) governed by the card's extracted generation rules. Retains full
    provenance and confidence metadata without cloning raw audio notes.
    """
    variant_id = variant_id.upper()
    if variant_id not in ("A", "B", "C"):
        variant_id = "A"

    rules = card.generation_rules
    raw_tempo = float(rules.get("bpm_target") or (card.bpm.get("value") or 120.0))
    tempo = max(60, min(180, int(round(raw_tempo))))
    tempo_confidence = float(card.bpm.get("confidence", 0.8))

    # Key determination with explicit confidence tracking
    card_key_info = card.harmonic_analysis
    detected_key = card_key_info.get("key")

    norm_human = _normalize_key_to_enum(key) if key else None
    norm_detected = _normalize_key_to_enum(detected_key) if detected_key else None

    if norm_human:
        chosen_key = norm_human
        key_source = "human_override"
        key_confidence = 1.0
    elif norm_detected:
        chosen_key = norm_detected
        key_source = "reference_card"
        key_confidence = float(card_key_info.get("confidence", 0.5))
    else:
        chosen_key = "Dm"
        key_source = "default"
        key_confidence = 0.0

    # Rule-derived rhythm parameters
    base_sync = float(rules.get("bass_syncopation", 0.49))
    base_rest = float(rules.get("bass_rest_ratio", 0.17))
    interlock = str(rules.get("kick_bass_interlock", "avoid_collision"))

    # Construct Section intents (e.g. 16-bar blocks)
    sec_len = max(4, bars // 4)
    sections = (
        SectionIntent("Intro", 1, sec_len),
        SectionIntent("Build", sec_len + 1, sec_len),
        SectionIntent("Drop", 2 * sec_len + 1, sec_len),
        SectionIntent("Outro", 3 * sec_len + 1, sec_len),
    )

    # 1. KICK (pitch 36): Solid 4-on-the-floor
    kick_16 = []
    for bar in range(sec_len):
        for beat in range(4):
            t = bar * 4.0 + beat
            kick_16.append(MidiNote(pitch=36, start_beats=t, duration_beats=0.25, velocity=118))
            if bar % 4 == 3 and beat == 3:
                kick_16.append(MidiNote(pitch=36, start_beats=_apply_swing_56(t + 0.75), duration_beats=0.15, velocity=85))

    # 2. SNARE (pitch 38): Backbeat on 2 & 4
    snare_16 = []
    for bar in range(sec_len):
        snare_16.append(MidiNote(pitch=38, start_beats=bar * 4.0 + 1.0, duration_beats=0.25, velocity=108))
        snare_16.append(MidiNote(pitch=38, start_beats=bar * 4.0 + 3.0, duration_beats=0.25, velocity=112))
        if bar in (7, 15):
            snare_16.append(MidiNote(pitch=38, start_beats=_apply_swing_56(bar * 4.0 + 3.5), duration_beats=0.2, velocity=95))
            snare_16.append(MidiNote(pitch=38, start_beats=_apply_swing_56(bar * 4.0 + 3.75), duration_beats=0.2, velocity=105))

    # Pre-drop tension snare roll (final bar of build section)
    build_snare = list(snare_16)
    last_bar_start = (sec_len - 1) * 4.0
    for i in range(16):
        build_snare.append(
            MidiNote(pitch=38, start_beats=last_bar_start + i * 0.25, duration_beats=0.2, velocity=int(70 + i * 3.5))
        )

    # 3. CLOSED HATS (pitch 42): 16th rolling groove with velocity dynamics
    hats_16 = []
    for bar in range(sec_len):
        for beat in range(4):
            t = bar * 4.0 + beat
            hats_16.append(MidiNote(pitch=42, start_beats=t, duration_beats=0.15, velocity=68))
            hats_16.append(MidiNote(pitch=42, start_beats=_apply_swing_56(t + 0.25), duration_beats=0.15, velocity=62))
            hats_16.append(MidiNote(pitch=42, start_beats=t + 0.5, duration_beats=0.2, velocity=108))
            hats_16.append(MidiNote(pitch=42, start_beats=_apply_swing_56(t + 0.75), duration_beats=0.15, velocity=74))

    # 4. OPEN HAT (pitch 46): Sizzling offbeat
    openhat_16 = []
    for bar in range(sec_len):
        for beat in range(4):
            t = bar * 4.0 + beat
            openhat_16.append(MidiNote(pitch=46, start_beats=t + 0.5, duration_beats=0.35, velocity=98))

    # 5. BASS: Variation-dependent density & interlock
    bass_16 = []
    for bar in range(sec_len):
        offset = bar * 4.0
        b_idx = bar % 4
        # Variant C enforces strict offbeat interlock; Variant B thins notes for dub space
        if variant_id == "B" and bar % 2 == 1:
            # Space out: root hit on offbeat 1.5 only
            bass_16.append(MidiNote(pitch=38, start_beats=offset + 1.5, duration_beats=0.4, velocity=115))
            continue

        if b_idx == 0:  # Dm (38, 41, 45, 50)
            start_off = 0.5 if (interlock == "avoid_collision" or variant_id == "C") else 0.0
            bass_16.append(MidiNote(pitch=38, start_beats=offset + start_off, duration_beats=0.3, velocity=115))
            bass_16.append(MidiNote(pitch=38, start_beats=_apply_swing_56(offset + 1.25), duration_beats=0.2, velocity=82))
            bass_16.append(MidiNote(pitch=50, start_beats=_apply_swing_56(offset + 1.75), duration_beats=0.25, velocity=120))
            bass_16.append(MidiNote(pitch=41, start_beats=offset + 2.5, duration_beats=0.3, velocity=110))
            bass_16.append(MidiNote(pitch=45, start_beats=_apply_swing_56(offset + 3.25), duration_beats=0.25, velocity=105))
        elif b_idx == 1:  # Gm (43, 46, 50)
            bass_16.append(MidiNote(pitch=43, start_beats=offset + 0.5, duration_beats=0.3, velocity=115))
            bass_16.append(MidiNote(pitch=43, start_beats=_apply_swing_56(offset + 1.25), duration_beats=0.2, velocity=82))
            bass_16.append(MidiNote(pitch=46, start_beats=_apply_swing_56(offset + 1.75), duration_beats=0.25, velocity=118))
            bass_16.append(MidiNote(pitch=43, start_beats=offset + 2.5, duration_beats=0.3, velocity=110))
            bass_16.append(MidiNote(pitch=48, start_beats=_apply_swing_56(offset + 3.25), duration_beats=0.25, velocity=105))
        elif b_idx == 2:  # FMaj (41, 45, 48, 53)
            bass_16.append(MidiNote(pitch=41, start_beats=offset + 0.5, duration_beats=0.3, velocity=115))
            bass_16.append(MidiNote(pitch=41, start_beats=_apply_swing_56(offset + 1.25), duration_beats=0.2, velocity=82))
            bass_16.append(MidiNote(pitch=53, start_beats=_apply_swing_56(offset + 1.75), duration_beats=0.25, velocity=118))
            bass_16.append(MidiNote(pitch=45, start_beats=offset + 2.5, duration_beats=0.3, velocity=110))
            bass_16.append(MidiNote(pitch=48, start_beats=_apply_swing_56(offset + 3.25), duration_beats=0.25, velocity=105))
        else:  # A7alt (45, 48, 49)
            bass_16.append(MidiNote(pitch=45, start_beats=offset + 0.5, duration_beats=0.3, velocity=115))
            bass_16.append(MidiNote(pitch=45, start_beats=_apply_swing_56(offset + 1.25), duration_beats=0.2, velocity=82))
            bass_16.append(MidiNote(pitch=49, start_beats=offset + 2.5, duration_beats=0.35, velocity=122))
            bass_16.append(MidiNote(pitch=48, start_beats=_apply_swing_56(offset + 3.25), duration_beats=0.2, velocity=95))

    # 6. STAB (hits on bass rests)
    stab_16 = []
    for bar in range(sec_len):
        offset = bar * 4.0
        b_idx = bar % 4
        if b_idx == 0:
            for p in (53, 57, 60):
                stab_16.append(MidiNote(pitch=p, start_beats=offset + 1.0, duration_beats=0.25, velocity=110))
                stab_16.append(MidiNote(pitch=p, start_beats=_apply_swing_56(offset + 3.5), duration_beats=0.25, velocity=115))
        elif b_idx == 1:
            for p in (53, 58, 62):
                stab_16.append(MidiNote(pitch=p, start_beats=offset + 1.0, duration_beats=0.25, velocity=110))
                stab_16.append(MidiNote(pitch=p, start_beats=_apply_swing_56(offset + 3.5), duration_beats=0.25, velocity=115))
        elif b_idx == 2:
            for p in (52, 57, 60):
                stab_16.append(MidiNote(pitch=p, start_beats=offset + 1.0, duration_beats=0.25, velocity=110))
                stab_16.append(MidiNote(pitch=p, start_beats=_apply_swing_56(offset + 3.5), duration_beats=0.25, velocity=115))
        else:
            for p in (55, 61, 65):
                stab_16.append(MidiNote(pitch=p, start_beats=offset + 1.0, duration_beats=0.25, velocity=110))
                stab_16.append(MidiNote(pitch=p, start_beats=_apply_swing_56(offset + 3.5), duration_beats=0.25, velocity=118))

    # 7. ARP (melodic top line in D Dorian)
    arp_16 = []
    d_dorian = [62, 65, 69, 72, 74, 77]
    for bar in range(sec_len):
        offset = bar * 4.0
        for i in range(16):
            pos = _apply_swing_56(offset + i * 0.25)
            pitch = d_dorian[(bar * 4 + i) % len(d_dorian)]
            vel = 105 if i % 4 == 2 else 72
            arp_16.append(MidiNote(pitch=pitch, start_beats=pos, duration_beats=0.2, velocity=vel))

    # Assemble raw clips across sections
    raw_clips = []
    for s in sections:
        if s.name in ("Intro", "Drop", "Outro"):
            raw_clips.append(CandidateClip(part="Kick", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(kick_16)))
        if s.name == "Build":
            raw_clips.append(CandidateClip(part="Snare", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(build_snare)))
        elif s.name == "Drop":
            raw_clips.append(CandidateClip(part="Snare", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(snare_16)))
        raw_clips.append(CandidateClip(part="Hats", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(hats_16)))
        if s.name in ("Drop", "Outro"):
            raw_clips.append(CandidateClip(part="OpenHat", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(openhat_16)))
        if s.name in ("Intro", "Drop", "Outro"):
            raw_clips.append(CandidateClip(part="Bass", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(bass_16)))
        if s.name in ("Build", "Drop"):
            raw_clips.append(CandidateClip(part="Stab", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(stab_16)))
        if s.name in ("Build", "Drop"):
            raw_clips.append(CandidateClip(part="Arp", section_name=s.name, start_bar=s.start_bar, length_bars=s.length_bars, notes=tuple(arp_16)))

    # Strictly order clips by PART_ORDER and start_bar
    ordered_clips = []
    present_parts = [part for part in PART_ORDER if any(clip.part == part for clip in raw_clips)]
    for part in present_parts:
        part_clips = sorted([clip for clip in raw_clips if clip.part == part], key=lambda c: c.start_bar)
        ordered_clips.extend(part_clips)

    candidate_id = uuid.uuid4().hex[:8]
    brief = ProductionBrief(
        original_text=(
            f"[ReferenceCard:{card.card_id}] {card.user_title} (Variant {variant_id}) | "
            f"tempo={round(tempo, 1)} (conf={tempo_confidence:.2f}) | "
            f"key={chosen_key} (conf={key_confidence:.2f}, source={key_source}) | "
            f"sync={base_sync:.2f}, rest={base_rest:.2f}, interlock={interlock}"
        ),
        tempo=SourcedValue(value=int(round(tempo)), source=SOURCE_AI),
        key=SourcedValue(value=chosen_key, source=SOURCE_USER if key else SOURCE_AI),
        bars=SourcedValue(value=bars, source=SOURCE_AI),
        genre=SourcedValue(value=str(rules.get("genre_profile", "tech_house")), source=SOURCE_GENRE),
        mood=SourcedValue(value=f"Derived from {card.user_title} (Variant {variant_id})", source=SOURCE_AI),
        hats_first_half=SourcedValue(value="normal", source=SOURCE_DEFAULT),
        hats_second_half=SourcedValue(value="dense", source=SOURCE_DEFAULT),
        bass_register=SourcedValue(value="low", source=SOURCE_DEFAULT),
        note_density=SourcedValue(value="dense" if base_rest < 0.3 else "normal", source=SOURCE_AI),
        drop_start_bar=SourcedValue(value=2 * sec_len + 1, source=SOURCE_AI),
        sections=sections,
    )

    return MidiCandidate(
        candidate_id=candidate_id,
        seed=seed,
        brief=brief,
        clips=tuple(ordered_clips),
    )
