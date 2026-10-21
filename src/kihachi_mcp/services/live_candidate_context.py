"""Turn observed Live Set facts into explicit, reviewable candidate inputs."""

from kihachi_mcp.models.live_state import LiveStateSnapshot
from kihachi_mcp.services.brief_parser import read_explicit


def brief_from_live(snapshot: LiveStateSnapshot, user_brief: str) -> dict[str, object]:
    """Use Live tempo only when the user did not specify one.

    Clip notes, key, genre and harmony are not present in a Set overview, so
    none of them are inferred from track names or clip counts.
    """
    brief = user_brief.strip()
    if not brief or len(brief) > 4000:
        raise ValueError("制作指示は1〜4000文字で入力してください")
    meter = snapshot.time_signature
    if (meter.numerator, meter.denominator) != (4, 4):
        raise ValueError("現在は4/4拍子のSetだけを候補生成に使用できます")
    fields, _spans = read_explicit(brief)
    sources: dict[str, str] = {}
    if "tempo" not in fields:
        tempo = snapshot.tempo
        if not 60 <= tempo <= 180 or not float(tempo).is_integer():
            raise ValueError("Liveのテンポは60〜180の整数BPMで指定してください")
        brief = f"{int(tempo)} BPM。{brief}"
        sources["tempo"] = "live"
    else:
        sources["tempo"] = "user"
    return {
        "brief": brief,
        "sources": sources,
        "set_name": snapshot.set_name,
        "observed_at": snapshot.observed_at,
        "track_count": len(snapshot.tracks),
        "session_clip_count": len(snapshot.session_clips),
        "arrangement_clip_count": len(snapshot.arrangement_clips),
        "note_content_used": False,
    }
