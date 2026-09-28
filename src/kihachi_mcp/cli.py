"""`kihachi` command line. Same services as the Studio and the MCP tools.

Commands that touch Live go through the running Studio, because only one
process may own the Live bridge's reply port.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

from kihachi_mcp.services.diagnostics import (
    STUDIO_HOST,
    STUDIO_PORT,
    DiagnosticsService,
    port_open,
)
from kihachi_mcp.services.live_paths import candidate_store_dir
from kihachi_mcp.services.studio_client import (
    NOT_RUNNING,
    SEND_TIMEOUT_SECONDS,
    studio_post,
    studio_running,
)
from kihachi_mcp.services.studio_runtime import StudioRuntime

STUDIO_URL = f"http://{STUDIO_HOST}:{STUDIO_PORT}/"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    return int(args.handler(args) or 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kihachi", description="KIHACHI MUSIC AI")
    commands = parser.add_subparsers()

    start = commands.add_parser("start", help="制作画面（Studio）を起動します")
    start.add_argument("--no-browser", action="store_true")
    start.set_defaults(handler=_start)

    doctor = commands.add_parser("doctor", help="環境を診断します")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_doctor)

    create = commands.add_parser("create", help="制作指示から候補を作ります")
    create.add_argument("prompt")
    create.add_argument("--seed", type=int)
    create.add_argument("--offline", action="store_true", help="AIを使わず明示指定と既定値だけで作る")
    create.set_defaults(handler=_create)

    projects = commands.add_parser("projects", help="保存済みの候補を一覧します")
    projects.set_defaults(handler=_projects)

    inspect = commands.add_parser("inspect", help="候補の SongSpec・構成・トラックを表示します")
    inspect.add_argument("project")
    inspect.set_defaults(handler=_inspect)

    review = commands.add_parser("review", help="候補をレビューします")
    review.add_argument("project")
    review.set_defaults(handler=_review)

    revise = commands.add_parser("revise", help="指摘や範囲だけを局所修正します")
    revise.add_argument("project")
    revise.add_argument("--issue", action="append", default=[])
    revise.add_argument(
        "--scope", action="append", default=[],
        choices=["bass", "drums", "section", "velocity", "arrangement"],
    )
    revise.add_argument("--bars", help="例: 33-49")
    revise.add_argument("--accept", action="store_true", help="修正案を採用して新しい候補として保存する")
    revise.set_defaults(handler=_revise)

    approve = commands.add_parser("approve", help="候補を承認します（Liveへ送る前に必要）")
    approve.add_argument("project")
    approve.set_defaults(handler=_approve)

    ableton = commands.add_parser("ableton", help="Ableton Live への計画・送信・検証")
    steps = ableton.add_subparsers()
    plan = steps.add_parser("plan", help="候補だけから計画を表示します（Live不要）")
    plan.add_argument("project")
    plan.set_defaults(handler=_ableton_plan)
    for name, helptext in (
        ("dry-run", "Liveと照合した送信内容を表示します（Liveは変わりません）"),
        ("execute", "承認済みの候補をLiveへ1回だけ送り、読み戻して検証します"),
        ("verify", "Liveから読み戻して計画と比べます"),
    ):
        step = steps.add_parser(name, help=helptext)
        step.add_argument("project")
        step.add_argument("--change-tempo", action="store_true")
        step.add_argument("--skip-instruments", action="store_true")
        if name == "execute":
            step.add_argument("--yes", action="store_true", help="確認の質問を省略する")
        step.set_defaults(handler=_ableton_live, step=name)
    return parser


def _runtime() -> StudioRuntime:
    return StudioRuntime(candidate_dir=Path(candidate_store_dir()))


def _resolve(runtime: StudioRuntime, text: str) -> str:
    """Accept a full id or its first characters, as shown in track names."""
    if runtime.get_candidate(text) is not None:
        return text
    matches = [row["candidate_id"] for row in runtime.list_projects() if row["candidate_id"].startswith(text)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"候補 '{text}' が見つかりません。kihachi projects で確認してください。")
    raise SystemExit(f"'{text}' に当てはまる候補が複数あります。もう少し長く指定してください。")


def _start(args: argparse.Namespace) -> int:
    if port_open(STUDIO_HOST, STUDIO_PORT):
        print(f"制作画面はすでに起動しています: {STUDIO_URL}")
        if not args.no_browser:
            webbrowser.open(STUDIO_URL)
        return 0
    report = DiagnosticsService().run()
    for check in report["checks"]:
        if check["name"] in {"Ollama", "Model", "Remote Script"}:
            print(f"  {check['name']:<14}{check['status']:<9}{check['detail']}")
    from kihachi_mcp.studio.__main__ import main as studio_main

    return studio_main(["--no-browser"] if args.no_browser else [])


def _doctor(args: argparse.Namespace) -> int:
    report = DiagnosticsService().run()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    print("SYSTEM DIAGNOSTICS\n")
    for check in report["checks"]:
        print(f"  {check['name']:<16}{check['status']:<9}{check['detail']}")
        if check["status"] not in {"PASS"} and check["fix"]:
            print(f"  {'':<16}{'':<9}→ {check['fix']}")
    print("\nOK" if report["ok"] else "\n必須項目に失敗があります")
    return 0 if report["ok"] else 1


def _create(args: argparse.Namespace) -> int:
    runtime = _runtime()
    if args.offline:
        runtime.update_settings({"ai_provider": "deterministic"}, persist=False)
    print("生成中…（ローカルAIは初回に数十秒かかることがあります）", file=sys.stderr)
    result = runtime.generate(args.prompt, seed=args.seed)
    if not result.get("ok"):
        print(result.get("error") or "生成できませんでした", file=sys.stderr)
        return 1
    candidate_id = result["candidate"]["candidate_id"]
    _print_project(runtime.project(candidate_id))
    print(f"\n候補ID: {candidate_id}")
    print("次: kihachi review", candidate_id[:8], "→ kihachi approve", candidate_id[:8])
    return 0


def _projects(args: argparse.Namespace) -> int:
    rows = _runtime().list_projects()
    if not rows:
        print("保存済みの候補はありません。kihachi create \"...\" で作れます。")
        return 0
    for row in rows:
        flags = "".join(
            mark for mark, on in (("A", row["approved"]), ("S", row["applied"]), ("R", row["arranged"])) if on
        )
        print(
            f"{row['candidate_id'][:8]}  {row['tempo']:>5} BPM  {row['key']:<4} {row['genre']:<16}"
            f" {row['bars']:>4} bars  {flags:<3} {row['title']}"
        )
    print("\nA=承認済 S=Liveへ送信済 R=アレンジメント展開済")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    runtime = _runtime()
    _print_project(runtime.project(_resolve(runtime, args.project)))
    return 0


def _print_project(project: dict[str, Any]) -> None:
    if not project.get("ok"):
        print(project.get("error"))
        return
    spec = project["songspec"]
    print(
        f"SongSpec: {spec['tempo']} BPM / {spec['key']} ({spec['scale']}) / {spec['time_signature']} / "
        f"{spec['genre']} / {spec['bars']} bars ({spec['duration_minutes']} 分) / swing {round(spec['swing'] * 100)}%"
    )
    print("Arrangement:")
    for section in project["arrangement"]["sections"]:
        print(
            f"  {section['start_bar']:>4}–{section['end_bar']:<4} {section['name']:<10}"
            f" energy {section['energy']:.2f}  {', '.join(section['active_tracks'])}"
        )
    print("Tracks:")
    for track in project["tracks"]:
        print(f"  {track['label']:<12} clips {track['clips']:>3}  notes {track['note_count']:>5}")
    status = project["status"]
    print(
        "Status: "
        + " / ".join(
            f"{name} {'済' if status[key] else '未'}"
            for name, key in (("承認", "approved"), ("Live送信", "applied"), ("展開", "arranged"))
        )
    )


def _review(args: argparse.Namespace) -> int:
    runtime = _runtime()
    report = runtime.review(_resolve(runtime, args.project))
    for dimension in report["dimensions"]:
        print(f"  {dimension['name']:<22}{dimension['status'].upper()}")
    if not report["issues"]:
        print("\n規則で見つかった指摘はありません。")
    for issue in report["issues"]:
        print(f"\n[{issue['severity'].upper()}] {issue['id']}: {issue['description']}")
        print(f"  小節 {issue['bars']} / 推奨: {issue['recommendation']}")
    return 0


def _revise(args: argparse.Namespace) -> int:
    runtime = _runtime()
    candidate_id = _resolve(runtime, args.project)
    bars = None
    if args.bars:
        try:
            first, last = (int(part) for part in args.bars.replace("–", "-").split("-", 1))
        except ValueError:
            print("--bars は 33-49 のように指定してください", file=sys.stderr)
            return 2
        bars = (first, last)
    result = runtime.propose_revision(
        candidate_id, issue_ids=args.issue or None, scopes=args.scope or None, bars=bars
    )
    if not result.get("ok"):
        print(result.get("error"), file=sys.stderr)
        return 1
    revision = result["revision"]
    print("BEFORE")
    for line in revision["before"]:
        print("  " + line)
    print("AFTER")
    for line in revision["after"]:
        print("  " + line)
    if not args.accept:
        print("\n修正案は保存していません。採用するには --accept を付けて実行してください。")
        return 0
    decided = runtime.decide_revision(revision["revision_id"], accept=True)
    print(f"\n採用しました。新しい候補: {decided['selected_candidate_id']}（送信前にもう一度承認が必要です）")
    return 0


def _approve(args: argparse.Namespace) -> int:
    runtime = _runtime()
    result = runtime.approve(_resolve(runtime, args.project))
    print("承認しました。" if result.get("ok") else result.get("error"))
    return 0 if result.get("ok") else 1


def _ableton_plan(args: argparse.Namespace) -> int:
    runtime = _runtime()
    plan = runtime.ableton_plan(_resolve(runtime, args.project))
    print("ABLETON PLAN\n")
    print(f"  Tempo        {plan['tempo']} BPM")
    print(f"  Tracks       {plan['tracks']} ({', '.join(plan['track_names'])})")
    print(f"  Clips        {plan['clips']}")
    print(f"  MIDI Notes   {plan['midi_notes']}")
    print(f"  Arrangement  {plan['arrangement_bars']} bars")
    print("  Operations")
    for operation in plan["operations"]:
        print(f"    - {operation}")
    return 0


def _ableton_live(args: argparse.Namespace) -> int:
    runtime = _runtime()
    candidate_id = _resolve(runtime, args.project)
    if not studio_running():
        print(NOT_RUNNING, file=sys.stderr)
        return 1
    body = {
        "candidate_id": candidate_id,
        "change_tempo": args.change_tempo,
        "skip_instruments": args.skip_instruments,
    }
    if args.step == "dry-run":
        result = studio_post("/api/ableton/dry-run", body)
        return _print_dry_run(result)
    if args.step == "verify":
        return _print_verification(studio_post("/api/ableton/verify", body))
    dry = studio_post("/api/ableton/dry-run", body)
    if _print_dry_run(dry) != 0:
        return 1
    if not args.yes:
        answer = input("\nこの内容でLiveへ1回だけ送ります。よろしいですか？ [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("送信しませんでした。")
            return 1
    result = studio_post(
        "/api/ableton/send", {**body, "confirmed": True}, timeout=SEND_TIMEOUT_SECONDS
    )
    receipt = result.get("receipt") or {}
    print(f"\n結果: {receipt.get('status') or result.get('error')}")
    if result.get("verification"):
        return _print_verification(result["verification"])
    return 0 if result.get("ok") else 1


def _print_dry_run(result: dict[str, Any]) -> int:
    if not result.get("ok"):
        print(result.get("error"), file=sys.stderr)
        return 1
    plan = result["plan"]
    print(f"ABLETON PLAN（ドライラン） Live: {result['live'].get('state')}")
    print(f"  Tempo {plan['tempo']} BPM / Tracks {plan['tracks']} / Clips {plan['clips']} / Notes {plan['midi_notes']} / {plan['arrangement_bars']} bars")
    for name, count in (result.get("live_operations") or {}).items():
        print(f"    {name} × {count}")
    for blocker in result.get("blockers") or []:
        print(f"  ✗ {blocker}")
    return 0 if result.get("can_send") else 1


def _print_verification(result: dict[str, Any]) -> int:
    print("\nABLETON VERIFICATION\n")
    if result.get("error"):
        print("  " + result["error"])
    for line in result.get("lines") or []:
        print(f"  {line['name']:<13}{line['status']:<6}{line['detail']}")
    print(f"\n{result.get('status', '')}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
