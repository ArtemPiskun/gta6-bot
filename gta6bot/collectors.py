"""Collectors: YouTube channel RSS (free), YouTube Data API (search + stats), news RSS."""
from __future__ import annotations

import html
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import net
from .store import Store, iso, item_id, now

log = logging.getLogger("gta6bot.collect")
YT_API = "https://www.googleapis.com/youtube/v3/"
TAG_RE = re.compile(r"<[^>]+>")


def clean(s: str, limit=600) -> str:
    s = html.unescape(TAG_RE.sub(" ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def iso_duration(d: str) -> int | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return None
    dd, h, mi, se = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + se


STREAM_RE = re.compile(r"\b(live|livestream|stream|streaming|marathon|irl|24/7)\b|стрим|трансляц|прямой эфир|🔴", re.I)


def matches(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(k in t for k in keywords)


def classify(title: str, summary: str = "", kind: str = "news") -> str:
    t = f"{title} {summary}".lower()
    if re.search(r"\b(leak|leaked|insider|rumou?r)|слив|слух|инсайд", t):
        return "leak"
    if re.search(r"\b(guide|how to|tips|walkthrough|best way|beginner|explained)\b|гайд|как |советы|прохожд|секрет|фишк", t):
        return "guide"
    if re.search(r"\b(patch|title update|hotfix|update \d|patch notes)\b|обновлени|патч", t):
        return "update"
    return "video" if kind == "video" else "news"


def domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


# ---------------------------------------------------------------- YouTube RSS
def collect_channel_rss(store: Store, cfg: dict) -> int:
    kw = cfg["youtube"]["title_keywords"]
    new = 0
    for ch in store.channels():
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"
        try:
            entries = net.parse_feed(net.fetch(url))
        except Exception as e:
            log.warning("rss %s: %s", ch["title"], e)
            continue
        for e in entries:
            official = bool(ch["official"])
            if not official and not matches(e["title"], kw):  # title only: descriptions are tag spam
                continue
            vid = e["video_id"] or e["url"].rsplit("=", 1)[-1]
            published = e["published"] or now()
            if published < now() - timedelta(days=3):
                continue
            it = {
                "id": item_id(f"https://www.youtube.com/watch?v={vid}"),
                "kind": "video", "url": f"https://www.youtube.com/watch?v={vid}",
                "title": e["title"], "summary": clean(e["summary"], 400),
                "source": ch["title"], "source_type": "official" if official else "youtube",
                "category": classify(e["title"], e["summary"], "video"),
                "image": e["image"] or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "video_id": vid, "channel_id": ch["channel_id"], "channel_title": ch["title"],
                "published": iso(published), "views": e["views"], "tags": ["youtube"],
            }
            if store.upsert(it):
                new += 1
            if e["views"] is not None:
                store.snapshot(vid, e["views"])
    return new


# ---------------------------------------------------------------- YouTube API
def collect_youtube_search(store: Store, cfg: dict, api_key: str, force=False) -> int:
    """search.list costs 100 quota units: run one query per interval, rotating."""
    ycfg = cfg["youtube"]
    last = store.get("yt_search_last")
    interval = ycfg.get("search_interval_minutes", 20)
    if not force and last and net.parse_date(last) > now() - timedelta(minutes=interval):
        return 0
    queries = ycfg["search_queries"]
    idx = int(store.get("yt_search_idx", "0")) % len(queries)
    q = queries[idx]
    store.set("yt_search_idx", str(idx + 1))
    store.set("yt_search_last", iso(now()))
    params = {"part": "snippet", "q": q, "type": "video", "order": "date", "maxResults": 50,
              "publishedAfter": iso(now() - timedelta(hours=24)), "key": api_key}
    data = net.fetch_json(YT_API + "search", params)
    new = 0
    for r in data.get("items", []):
        sn = r["snippet"]
        vid = r["id"].get("videoId")
        if sn.get("liveBroadcastContent") in ("live", "upcoming"):
            continue  # streams are not posts
        if not vid or not matches(html.unescape(sn["title"]), ycfg["title_keywords"]):
            continue
        it = {
            "kind": "video", "url": f"https://www.youtube.com/watch?v={vid}",
            "title": html.unescape(sn["title"]), "summary": clean(sn.get("description", ""), 400),
            "source": sn["channelTitle"], "source_type": "youtube",
            "category": classify(sn["title"], sn.get("description", ""), "video"),
            "image": sn.get("thumbnails", {}).get("high", {}).get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"),
            "video_id": vid, "channel_id": sn["channelId"], "channel_title": sn["channelTitle"],
            "published": iso(net.parse_date(sn["publishedAt"]) or now()), "tags": ["youtube", "search"],
        }
        if store.upsert(it):
            new += 1
    log.info("youtube search %r: %d new", q, new)
    return new


def refresh_video_stats(store: Store, api_key: str) -> int:
    """videos.list: 1 quota unit per 50 ids. Snapshots views for velocity."""
    vids = [r["video_id"] for r in store.recent(72, "video") if r["video_id"]]
    n = 0
    for i in range(0, len(vids), 50):
        chunk = vids[i:i + 50]
        data = net.fetch_json(YT_API + "videos", {"part": "statistics,contentDetails,liveStreamingDetails",
                                                  "id": ",".join(chunk), "key": api_key})
        for v in data.get("items", []):
            st = v.get("statistics", {})
            views = int(st.get("viewCount", 0))
            store.snapshot(v["id"], views, int(st.get("likeCount", 0) or 0), int(st.get("commentCount", 0) or 0))
            live = int("liveStreamingDetails" in v)
            store.db.execute("UPDATE items SET views=?, live=?, duration=? WHERE video_id=?",
                             (views, live, iso_duration(v.get("contentDetails", {}).get("duration", "")), v["id"]))
            n += 1
    store.db.commit()
    return n


def auto_track_channels(store: Store, cfg: dict) -> list[str]:
    """Channels discovered through search whose video is exploding become tracked (free RSS)."""
    ycfg = cfg["youtube"]
    if len(store.channels()) >= ycfg.get("max_tracked_channels", 80):
        return []
    added = []
    for r in store.recent(48, "video"):
        if r["channel_id"] and (r["vph"] or 0) >= ycfg.get("auto_track_vph", 5000):
            if store.add_channel(r["channel_id"], r["channel_title"], source="auto"):
                added.append(r["channel_title"])
    return added


# ---------------------------------------------------------------- News RSS
def collect_news(store: Store, cfg: dict) -> int:
    ncfg = cfg["news"]
    kw = ncfg["title_keywords"]
    official_domains = ncfg.get("official_domains", [])
    new = 0
    for f in ncfg["feeds"]:
        try:
            entries = net.parse_feed(net.fetch(f["url"]))
        except Exception as e:
            log.warning("feed %s: %s", f["name"], e)
            continue
        for e in entries:
            title = clean(e["title"], 300)
            summary = clean(e["summary"])
            stype = f.get("type", "media")
            if stype != "official" and not matches(title + " " + summary, kw):
                continue
            if stype == "official" and not matches(title + " " + summary, kw + ["grand theft auto", "gta online"]):
                continue
            published = e["published"] or now()
            if published < now() - timedelta(days=2):
                continue
            source = f["name"]
            # Google News: "Title - Publisher"
            if "news.google.com" in f["url"] and " - " in title:
                title, pub = title.rsplit(" - ", 1)
                source = pub.strip() or source
            if any(d in domain(e["url"]) for d in official_domains) or "rockstar games" in source.lower():
                stype = "official"
            cat = classify(title, summary)
            if stype == "leak":
                cat = "leak"
            it = {
                "kind": "news", "url": e["url"], "title": title, "summary": summary,
                "source": source, "source_type": stype, "category": cat, "image": e["image"],
                "published": iso(published), "tags": [f["name"]],
            }
            if store.upsert(it):
                new += 1
    return new
