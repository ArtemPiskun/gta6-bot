"""Signal scoring: how hard an item is "firing" (0-100)."""
from __future__ import annotations

import math
import re
from datetime import timedelta

from . import net
from .store import Store, now

WORD = re.compile(r"[a-zа-яё0-9]{3,}", re.I)
STOP = {"gta", "гта", "the", "and", "for", "with", "что", "как", "это", "grand", "theft", "auto",
        "vi", "rockstar", "games", "game", "new", "will", "has", "about", "from", "все", "для"}


def tokens(s: str) -> set[str]:
    return {w.lower() for w in WORD.findall(s)} - STOP


def hours_since(iso_s: str) -> float:
    d = net.parse_date(iso_s) or now()
    return max((now() - d).total_seconds() / 3600, 0.25)


def velocity(store: Store, row) -> tuple[float | None, float | None]:
    """(lifetime vph, recent vph from last two snapshots at least 10 min apart)."""
    snaps = store.snapshots(row["video_id"]) if row["video_id"] else []
    views = row["views"]
    if snaps:
        views = snaps[-1]["views"]
    if views is None:
        return None, None
    vph = views / hours_since(row["published"])
    recent = None
    if len(snaps) >= 2:
        last = snaps[-1]
        t_last = net.parse_date(last["ts"])
        for prev in reversed(snaps[:-1]):
            dt = (t_last - net.parse_date(prev["ts"])).total_seconds() / 3600
            if dt >= 1 / 6:
                recent = max(last["views"] - prev["views"], 0) / dt
                break
    return vph, recent


def channel_baseline(store: Store, channel_id: str, exclude: str) -> float | None:
    rows = store.db.execute(
        "SELECT vph FROM items WHERE channel_id=? AND id!=? AND vph IS NOT NULL ORDER BY published DESC LIMIT 15",
        (channel_id, exclude)).fetchall()
    vals = sorted(r[0] for r in rows if r[0])
    return vals[len(vals) // 2] if len(vals) >= 3 else None


def boost(text: str, words: dict[str, int]) -> int:
    t = text.lower()
    return min(sum(v for k, v in words.items() if k in t), 30)


def score_video(store: Store, row, cfg) -> dict:
    vph, recent = velocity(store, row)
    kw = cfg["youtube"]["title_keywords"]
    if row["source_type"] != "official" and not any(k in row["title"].lower() for k in kw):
        return {"score": 0, "vph": vph, "recent_vph": recent, "verified": 0}  # off-topic
    from .collectors import STREAM_RE
    if row["source_type"] != "official" and (row["live"] or STREAM_RE.search(row["title"])):
        return {"score": 0, "vph": vph, "recent_vph": recent, "verified": 0}  # streams / VODs of streams
    if row["source_type"] != "official" and row["duration"] and row["duration"] > 3 * 3600:
        return {"score": 0, "vph": vph, "recent_vph": recent, "verified": 0}  # multi-hour = stream recording
    speed = recent if recent is not None else vph
    s = 0.0
    if speed:
        # 1k vph ~ 35, 10k ~ 55, 100k ~ 75
        s += max(0.0, 20 * math.log10(max(speed, 1)) - 25)
    if vph and (base := channel_baseline(store, row["channel_id"], row["id"])):
        ratio = vph / base
        if ratio > 1.2:
            s += min(20, 10 * math.log2(ratio))
    age = hours_since(row["published"])
    s += 12 if age < 2 else 6 if age < 6 else 0
    if row["source_type"] == "official":
        s += 40  # anything from Rockstar's own channel is news
    s += boost(row["title"], cfg["scoring"]["boost_words"]) * 0.5
    return {"score": round(min(s, 100), 1), "vph": vph, "recent_vph": recent,
            "verified": int(row["source_type"] == "official")}


BASE = {"official": 70, "media": 38, "community": 28, "leak": 30, "youtube": 30}


def score_news(store: Store, row, cfg, window: list) -> dict:
    s = BASE.get(row["source_type"], 30)
    s += boost(row["title"] + " " + row["summary"], cfg["scoring"]["boost_words"])
    # corroboration: same story from other sources in the last 12h
    tk = tokens(row["title"])
    sources = set()
    for other in window:
        if other["id"] == row["id"] or other["source"] == row["source"]:
            continue
        ot = tokens(other["title"])
        if tk and ot and len(tk & ot) / len(tk | ot) >= 0.35:
            sources.add(other["source"])
    s += min(8 * len(sources), 24)
    age = hours_since(row["published"])
    s -= min(age * 2, 30)  # decay
    verified = row["source_type"] == "official" or (row["source_type"] == "media" and len(sources) >= 1)
    return {"score": round(max(0, min(s, 100)), 1), "verified": int(verified), "corroborated": len(sources)}


def rescore(store: Store, cfg) -> None:
    window = store.recent(12, "news")
    for row in store.recent(72):
        if row["kind"] == "video":
            r = score_video(store, row, cfg)
            store.update(row["id"], score=r["score"], vph=r["vph"], recent_vph=r["recent_vph"],
                         verified=r["verified"])
        else:
            r = score_news(store, row, cfg, window)
            store.update(row["id"], score=r["score"], verified=r["verified"])


def is_duplicate_story(row, posted: list) -> bool:
    mine = [tokens(row["title"])]
    if row["title_ru"]:
        mine.append(tokens(row["title_ru"]))
    for p in posted:
        theirs = [tokens(p["title"])] + ([tokens(p["title_ru"])] if p["title_ru"] else [])
        for a in mine:
            for b in theirs:
                if a and b and len(a & b) / len(a | b) >= 0.45:
                    return True
    return False
