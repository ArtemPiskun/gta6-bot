"""Resolve Google News links to the real article and read its preview image (og:image)."""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse

from . import net

log = logging.getLogger("gta6bot.article")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _gnews_id(url: str) -> str | None:
    p = urllib.parse.urlparse(url)
    if p.netloc != "news.google.com":
        return None
    m = re.search(r"/(?:rss/)?articles/([^/?#]+)", p.path)
    return m.group(1) if m else None


def resolve(url: str) -> str:
    """Real article URL for a news.google.com link (else the url itself)."""
    gid = _gnews_id(url)
    if not gid:
        return url
    try:
        page = net.fetch(f"https://news.google.com/articles/{gid}", headers={"User-Agent": BROWSER_UA},
                         retries=1).decode("utf-8", "replace")
        sig = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sig and ts):
            raise ValueError("no signature on article page")
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                 f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sig.group(1)}"]')
        freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        raw = net.fetch("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                        data=urllib.parse.urlencode({"f.req": freq}).encode(),
                        headers={"User-Agent": BROWSER_UA,
                                 "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                        retries=1).decode("utf-8", "replace")
        body = raw.split("\n\n", 1)[1] if "\n\n" in raw else raw
        real = json.loads(json.loads(body)[0][2])[1]
        if real.startswith("http"):
            return real
    except Exception as e:
        log.info("gnews resolve failed for %s: %s", gid[:20], e)
    return url


META_RE = re.compile(r"<meta\s+[^>]*>", re.I)


def _meta(page: str, names: tuple[str, ...]) -> str:
    for tag in META_RE.findall(page[:300_000]):
        key = re.search(r'(?:property|name)\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        val = re.search(r'content\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        if key and val and key.group(1).lower() in names:
            return html.unescape(val.group(1)).strip()
    return ""


def fetch_meta(url: str) -> dict:
    """og:image and og:description of an article page."""
    try:
        page = net.fetch(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
                         retries=1, timeout=20).decode("utf-8", "replace")
    except Exception as e:
        log.info("article fetch failed %s: %s", url[:80], e)
        return {}
    img = _meta(page, ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"))
    if img.startswith("//"):
        img = "https:" + img
    elif img.startswith("/"):
        img = urllib.parse.urljoin(url, img)
    desc = _meta(page, ("og:description", "description", "twitter:description"))
    return {"image": img if img.startswith("http") else "", "description": desc}


def enrich(store, row) -> dict:
    """Resolve the article once and cache url/image/description on the item."""
    if row["article_url"]:
        return {"url": row["article_url"], "image": row["image"]}
    real = resolve(row["url"])
    meta = fetch_meta(real) if "news.google.com" not in real else {}
    fields = {"article_url": real}
    if meta.get("image") and not row["image"]:
        fields["image"] = meta["image"]
    if meta.get("description") and len(meta["description"]) > len(row["summary"] or ""):
        fields["summary"] = meta["description"][:1500]
    store.update(row["id"], **fields)
    return {"url": real, "image": fields.get("image", row["image"])}
