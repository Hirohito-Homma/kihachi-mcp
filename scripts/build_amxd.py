"""Max for Live デバイス（KIHACHI Live Device.amxd）に JS 正本を内包（Freeze）するビルドスクリプト。"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
MAXFORLIVE_DIR = ROOT_DIR / "maxforlive"
DEFAULT_AMXD = MAXFORLIVE_DIR / "KIHACHI Live Device.amxd"
EMBEDDED_JS_FLAGS: dict[str, int] = {
    "kihachi.device.js": 0x0,
    "kihachi.bridge.js": 0x8,
}
EMBEDDED_JS_NAMES = tuple(EMBEDDED_JS_FLAGS.keys())
EXPECTED_CODE_ENTRY = {"kind": "javascript", "local": 1}

AMXD_HEADER = b"ampf\x04\x00\x00\x00aaaa"
META_CHUNK = b"meta\x04\x00\x00\x00\x01\x00\x00\x00"
MXC_MAGIC = b"mx@c"


def _pack_chunk_be(tag: bytes, payload: bytes) -> bytes:
    """ビッグエンディアン長の Max チャンク（4バイトタグ + 4バイト全長 + ペイロード）を組み立てる。"""
    return tag + struct.pack(">I", 8 + len(payload)) + payload


def _pack_dire(
    *,
    file_type: bytes,
    filename: str,
    size: int,
    offset: int,
    flag: int,
) -> bytes:
    """dlst 内の 1 エントリ（dire チャンク）を生成する。"""
    # ファイル名は NUL 終端かつ 4 バイト境界へパディングする
    raw_name = filename.encode("utf-8") + b"\x00"
    pad_len = (-len(raw_name)) % 4
    padded_name = raw_name + (b"\x00" * pad_len)

    inner = b"".join(
        [
            _pack_chunk_be(b"type", file_type),
            _pack_chunk_be(b"fnam", padded_name),
            _pack_chunk_be(b"sz32", struct.pack(">I", size)),
            _pack_chunk_be(b"of32", struct.pack(">I", offset)),
            _pack_chunk_be(b"vers", struct.pack(">I", 0)),
            _pack_chunk_be(b"flag", struct.pack(">I", flag)),
            _pack_chunk_be(b"mdat", struct.pack(">I", 0)),
        ]
    )
    return _pack_chunk_be(b"dire", inner)


def _parse_amxd_dlst(amxd_bytes: bytes) -> dict[str, tuple[bytes, dict[bytes, bytes]]]:
    """`.amxd` バイナリから dlst エントリと dire フィールドを抽出する（非 Freeze 時は本体のみ）。"""
    if len(amxd_bytes) < 32 or amxd_bytes[:4] != b"ampf":
        raise ValueError("Invalid .amxd header: expected 'ampf'")

    ptch_offset = amxd_bytes.find(b"ptch")
    if ptch_offset == -1 or ptch_offset + 8 > len(amxd_bytes):
        raise ValueError("Missing 'ptch' chunk in .amxd")

    ptch_len = struct.unpack("<I", amxd_bytes[ptch_offset + 4 : ptch_offset + 8])[0]
    ptch_payload = amxd_bytes[ptch_offset + 8 : ptch_offset + 8 + ptch_len]

    if not ptch_payload.startswith(MXC_MAGIC):
        return {"KIHACHI Live Device.amxd": (ptch_payload.rstrip(b"\x00"), {})}

    if len(ptch_payload) < 16:
        raise ValueError("Truncated mx@c header in .amxd")

    dlst_offset = struct.unpack(">I", ptch_payload[12:16])[0]
    if dlst_offset + 8 > len(ptch_payload) or ptch_payload[dlst_offset : dlst_offset + 4] != b"dlst":
        raise ValueError("Missing or invalid 'dlst' chunk in frozen .amxd")

    dlst_len = struct.unpack(">I", ptch_payload[dlst_offset + 4 : dlst_offset + 8])[0]
    pos = dlst_offset + 8
    stop = dlst_offset + dlst_len
    entries: dict[str, tuple[bytes, dict[bytes, bytes]]] = {}

    while pos + 8 <= stop:
        tag = ptch_payload[pos : pos + 4]
        ln = struct.unpack(">I", ptch_payload[pos + 4 : pos + 8])[0]
        if ln < 8 or pos + ln > stop:
            raise ValueError(f"Invalid chunk length in dlst: {tag!r}")
        if tag == b"dire":
            p2 = pos + 8
            end_dire = pos + ln
            fields: dict[bytes, bytes] = {}
            while p2 + 8 <= end_dire:
                t2 = ptch_payload[p2 : p2 + 4]
                l2 = struct.unpack(">I", ptch_payload[p2 + 4 : p2 + 8])[0]
                if l2 < 8 or p2 + l2 > end_dire:
                    raise ValueError(f"Invalid dire field length: {t2!r}")
                fields[t2] = ptch_payload[p2 + 8 : p2 + l2]
                p2 += l2
            name = fields[b"fnam"].split(b"\x00", 1)[0].decode("utf-8")
            sz = struct.unpack(">I", fields[b"sz32"])[0]
            of = struct.unpack(">I", fields[b"of32"])[0]
            entries[name] = (ptch_payload[of : of + sz], fields)
        pos += ln

    return entries


def extract_amxd_entries(amxd_bytes: bytes) -> dict[str, bytes]:
    """`.amxd` バイナリから内包ファイル（Freeze 時は dlst、非 Freeze 時は本体 JSON のみ）を抽出する。"""
    return {name: blob for name, (blob, _fields) in _parse_amxd_dlst(amxd_bytes).items()}


def extract_amxd_dire_metadata(amxd_bytes: bytes) -> dict[str, dict[str, int]]:
    """`.amxd` バイナリの各 dlst エントリから dire メタデータ（flag 等）を抽出する。"""
    metadata: dict[str, dict[str, int]] = {}
    for name, (_blob, fields) in _parse_amxd_dlst(amxd_bytes).items():
        if not fields:
            continue
        metadata[name] = {
            "flag": struct.unpack(">I", fields[b"flag"])[0],
            "mdat": struct.unpack(">I", fields[b"mdat"])[0],
        }
    return metadata


def extract_patcher_json(amxd_bytes: bytes) -> dict:
    """`.amxd`（Freeze 前後を問わず）から patcher JSON を取り出す。"""
    entries = extract_amxd_entries(amxd_bytes)
    for name, raw in entries.items():
        if name.endswith(".amxd"):
            return json.loads(raw.rstrip(b"\x00").decode("utf-8"))
    raise ValueError("Main patcher JSON entry not found in .amxd")


def build_frozen_amxd_bytes(
    *,
    base_amxd_bytes: bytes,
    js_sources: dict[str, bytes],
    device_filename: str = "KIHACHI Live Device.amxd",
) -> bytes:
    """ベース `.amxd` のパッチ構造と JS バイナリから Freeze 済み `.amxd` バイナリを生成する。"""
    patch = extract_patcher_json(base_amxd_bytes)
    project = patch.setdefault("patcher", {}).setdefault("project", {})
    project["readonly"] = 1
    contents = project.setdefault("contents", {})
    code = contents.setdefault("code", {})
    for js_name in js_sources:
        code[js_name] = dict(EXPECTED_CODE_ENTRY)

    patch_bytes = (json.dumps(patch, indent=4, ensure_ascii=False) + "\n").encode("utf-8")

    # mx@c ヘッダ（16 バイト）直後から各ファイルの実データを連結する
    offset = 16
    blobs: list[bytes] = []
    dires: list[bytes] = []

    blobs.append(patch_bytes)
    dires.append(
        _pack_dire(
            file_type=b"JSON",
            filename=device_filename,
            size=len(patch_bytes),
            offset=offset,
            flag=17,
        )
    )
    offset += len(patch_bytes)

    for js_name, js_bytes in js_sources.items():
        blobs.append(js_bytes)
        dires.append(
            _pack_dire(
                file_type=b"TEXT",
                filename=js_name,
                size=len(js_bytes),
                offset=offset,
                flag=EMBEDDED_JS_FLAGS.get(js_name, 0),
            )
        )
        offset += len(js_bytes)

    dlst_chunk = _pack_chunk_be(b"dlst", b"".join(dires))
    dlst_offset = offset
    mxc_header = MXC_MAGIC + struct.pack(">III", 16, 0, dlst_offset)
    ptch_payload = mxc_header + b"".join(blobs) + dlst_chunk

    # meta チャンクは元デバイスから引き継ぐ（存在しない場合は既定値）
    meta_offset = amxd_bytes_meta_chunk(base_amxd_bytes)
    ptch_chunk = b"ptch" + struct.pack("<I", len(ptch_payload)) + ptch_payload
    return AMXD_HEADER + meta_offset + ptch_chunk


def amxd_bytes_meta_chunk(amxd_bytes: bytes) -> bytes:
    """元の `.amxd` から meta チャンクを取り出す（無ければ標準値を返す）。"""
    idx = amxd_bytes.find(b"meta")
    if idx != -1 and idx + 12 <= len(amxd_bytes):
        ln = struct.unpack("<I", amxd_bytes[idx + 4 : idx + 8])[0]
        if idx + 8 + ln <= len(amxd_bytes):
            return amxd_bytes[idx : idx + 8 + ln]
    return META_CHUNK


def check_amxd_sync(amxd_path: Path, maxforlive_dir: Path) -> list[str]:
    """`.amxd` 内に埋め込まれた JS・Freeze フラグ・project.contents.code 登録を検査し、不一致理由の一覧を返す。"""
    errors: list[str] = []
    amxd_bytes = amxd_path.read_bytes()
    entries = extract_amxd_entries(amxd_bytes)
    dire_meta = extract_amxd_dire_metadata(amxd_bytes)

    for js_name in EMBEDDED_JS_NAMES:
        expected = (maxforlive_dir / js_name).read_bytes()
        actual = entries.get(js_name)
        if actual is None:
            errors.append(f"{js_name} is not embedded in {amxd_path.name}")
        elif actual != expected:
            errors.append(f"{js_name} inside {amxd_path.name} differs from maxforlive/{js_name}")

        expected_flag = EMBEDDED_JS_FLAGS[js_name]
        actual_flag = dire_meta.get(js_name, {}).get("flag")
        if actual_flag != expected_flag:
            errors.append(
                f"{js_name} dire.flag in {amxd_path.name} is {actual_flag!r}, expected {expected_flag!r}"
            )

    patch = extract_patcher_json(amxd_bytes)
    project = patch.get("patcher", {}).get("project", {})
    if project.get("readonly") != 1:
        errors.append(f"{amxd_path.name} patcher.project.readonly is {project.get('readonly')!r}, expected 1")

    code = project.get("contents", {}).get("code", {})
    if not isinstance(code, dict):
        errors.append(f"{amxd_path.name} patcher.project.contents.code must be a dict")
        return errors

    for js_name in EMBEDDED_JS_NAMES:
        if code.get(js_name) != EXPECTED_CODE_ENTRY:
            errors.append(
                f"{js_name} entry in patcher.project.contents.code is {code.get(js_name)!r}, "
                f"expected {EXPECTED_CODE_ENTRY!r}"
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Embed maxforlive JS sources into KIHACHI Live Device.amxd (Max Freeze format)."
    )
    parser.add_argument(
        "--amxd",
        type=Path,
        default=DEFAULT_AMXD,
        help="Target .amxd file path (default: maxforlive/KIHACHI Live Device.amxd)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that embedded JS files inside the .amxd match maxforlive/*.js without modifying files.",
    )
    args = parser.parse_args(argv)

    amxd_path: Path = args.amxd.resolve()
    maxforlive_dir = amxd_path.parent

    if args.check:
        errors = check_amxd_sync(amxd_path, maxforlive_dir)
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1
        print(f"OK: {amxd_path.name} embeds up-to-date {', '.join(EMBEDDED_JS_NAMES)}")
        return 0

    js_sources = {name: (maxforlive_dir / name).read_bytes() for name in EMBEDDED_JS_NAMES}
    built = build_frozen_amxd_bytes(
        base_amxd_bytes=amxd_path.read_bytes(),
        js_sources=js_sources,
        device_filename=amxd_path.name,
    )
    amxd_path.write_bytes(built)
    print(f"Built {amxd_path} ({len(built)} bytes, embedded: {', '.join(EMBEDDED_JS_NAMES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
