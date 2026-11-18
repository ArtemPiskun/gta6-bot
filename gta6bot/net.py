"""HTTP and RSS/Atom parsing on the standard library only."""
from __future__ import annotations

import json
import re
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger("gta6bot.net")
UA = "Mozilla/5.0 (compatible; gta6-news-monitor/1.0)"


def fetch(url: str, *, data: bytes | None = None, headers: dict | None = None,
          timeout: int = 20, retries: int = 2) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read()[:300].decode("utf-8", "replace")
            except Exception:
                detail = ""
            last = RuntimeError(f"HTTP {e.code}: {detail}")
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:  # network errors, timeouts
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(redact(f"fetch failed {url}: {last}"))


TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")


def redact(text: str) -> str:
    """Never show the Telegram bot token in logs or admin messages."""
    return TOKEN_RE.sub("bot***", str(text))


def fetch_json(url: str, params: dict | None = None, **kw) -> dict:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    return json.loads(fetch(url, **kw))


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(s)
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def parse_feed(raw: bytes) -> list[dict]:
    """Return entries from RSS 2.0 or Atom (incl. YouTube channel feeds).

    Each entry: title, url, summary, published (datetime|None), image,
    video_id, channel_id, author, views.
    """
    root = ET.fromstring(raw)
    out = []
    items = [e for e in root.iter() if _strip_ns(e.tag) in ("item", "entry")]
    for it in items:
        e = {"title": "", "url": "", "summary": "", "published": None, "image": "",
             "video_id": "", "channel_id": "", "author": "", "views": None}
        for c in it.iter():
            t = _strip_ns(c.tag)
            txt = (c.text or "").strip()
            if t == "title" and not e["title"]:
                e["title"] = txt
            elif t == "link":
                href = c.attrib.get("href")
                rel = c.attrib.get("rel", "alternate")
                if href and rel == "alternate" and not e["url"]:
                    e["url"] = href
                elif txt and not e["url"]:
                    e["url"] = txt
            elif t in ("description", "summary", "content", "encoded") and not e["summary"]:
                e["summary"] = txt
            elif t in ("pubDate", "published", "updated", "date") and not e["published"]:
                e["published"] = parse_date(txt)
            elif t == "videoId":
                e["video_id"] = txt
            elif t == "channelId":
                e["channel_id"] = txt
            elif t == "name" and not e["author"]:
                e["author"] = txt
            elif t == "thumbnail" and not e["image"]:
                e["image"] = c.attrib.get("url", "")
            elif t == "content" and c.attrib.get("medium") == "image" and not e["image"]:
                e["image"] = c.attrib.get("url", "")
            elif t == "enclosure" and c.attrib.get("type", "").startswith("image") and not e["image"]:
                e["image"] = c.attrib.get("url", "")
            elif t == "statistics" and c.attrib.get("views"):
                e["views"] = int(c.attrib["views"])
            elif t == "source" and not e["author"]:
                e["author"] = txt
        if e["title"] and e["url"]:
            out.append(e)
    return out
