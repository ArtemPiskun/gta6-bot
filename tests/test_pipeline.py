"""Offline end-to-end test: network is mocked with fixtures. Run: python -m unittest discover tests"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gta6bot import __main__ as app, net, publish  # noqa: E402
from gta6bot.store import iso, now  # noqa: E402


def yt_feed(cid, name, videos):
    entries = "".join(f"""
 <entry><id>yt:video:{v}</id><yt:videoId>{v}</yt:videoId><yt:channelId>{cid}</yt:channelId>
  <title>{t}</title><link rel="alternate" href="https://www.youtube.com/watch?v={v}"/>
  <author><name>{name}</name></author><published>{p}</published>
  <media:group><media:title>{t}</media:title><media:description>desc</media:description>
   <media:thumbnail url="https://i.ytimg.com/vi/{v}/hqdefault.jpg"/>
   <media:community><media:statistics views="{views}"/></media:community></media:group></entry>"""
                      for v, t, p, views in videos)
    return f"""<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
 xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">{entries}</feed>""".encode()


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
                   f"<pubDate>{p}</pubDate></item>" for t, u, d, p in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update(DRY_RUN="1", EXPORT_DIR=self.tmp + "/site", LAUNCH_MODE="1")
        os.environ.pop("YOUTUBE_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.cfg = app.load_cfg(str(Path(__file__).resolve().parent.parent / "config.toml"))
        self.cfg["general"]["db_path"] = self.tmp + "/t.sqlite"
        self.store = app.open_store(self.cfg)
        h = lambda x: iso(now() - timedelta(hours=x))  # noqa: E731
        rfc = lambda x: (now() - timedelta(hours=x)).strftime("%a, %d %b %Y %H:%M:%S +0000")  # noqa: E731
        self.views = {"tgg1": 950_000, "tgg_old1": 300_000, "tgg_old2": 250_000, "tgg_old3": 280_000,
                      "rsg1": 5_000_000}
        self.pub = {"tgg1": h(13), "tgg_old1": h(40), "tgg_old2": h(50), "tgg_old3": h(60), "rsg1": h(1)}

        def fake_fetch(url, **kw):
            if "channel_id=UC72PuhDwKtZ5MikpGNhPAtA" in url:
                return yt_feed("UC72PuhDwKtZ5MikpGNhPAtA", "TGG", [
                    ("tgg1", "Just Over 24 Hours to GTA 6... NEW INFO", self.pub["tgg1"], self.views["tgg1"]),
                    ("tgg_old1", "GTA 6 map size explained", self.pub["tgg_old1"], self.views["tgg_old1"]),
                    ("tgg_old2", "GTA 6 preload guide", self.pub["tgg_old2"], self.views["tgg_old2"]),
                    ("tgg_old3", "GTA 6 cars list", self.pub["tgg_old3"], self.views["tgg_old3"]),
                    ("x1", "My Minecraft house", h(2), 10)])
            if "channel_id=UCmDTrq0LNgPodDOFZiSbsww" in url:
                return yt_feed("UCmDTrq0LNgPodDOFZiSbsww", "Rockstar Games", [
                    ("rsg1", "Grand Theft Auto VI Launch Trailer", self.pub["rsg1"], self.views["rsg1"])])
            if "youtube.com/feeds" in url:
                return yt_feed("x", "x", [])
            if "Google+News+EN" in url or ("news.google.com" in url and "hl=en-US" in url and "site:" not in url):
                return rss([
                    ("GTA 6 launch trailer released ahead of November 19 release - IGN", "https://ign.com/a", "", rfc(1)),
                    ("GTA 6 launch trailer drops ahead of release - Polygon", "https://polygon.com/b", "", rfc(1.5)),
                    ("Old GTA 5 story - Kotaku", "https://kotaku.com/c", "", rfc(1)),
                ])
            if "news.google.com" in url and "hl=ru" in url:
                return rss([("Вышел релизный трейлер GTA 6 - StopGame", "https://stopgame.ru/d", "", rfc(1.2))])
            if "GamingLeaksAndRumours" in url:
                return rss([("[Rumor] GTA 6 PC version leak points to 2027", "https://reddit.com/e", "", rfc(2))])
            raise RuntimeError("blocked in test")

        net.fetch = fake_fetch

    def test_cycle(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            app.cycle(self.store, self.cfg)
            # second snapshot 20 min later for recent velocity
            for v in self.views:
                self.views[v] = int(self.views[v] * 1.02)
            self.store.db.execute("UPDATE snapshots SET ts=?", (iso(now() - timedelta(minutes=20)),))
            self.store.db.commit()
            self.store.db.execute("UPDATE items SET posted_at=NULL WHERE posted_at IS NOT NULL")
            app.cycle(self.store, self.cfg)
        out = buf.getvalue()
        print(out)
        rows = {r["url"]: r for r in self.store.all_items()}
        self.assertNotIn("https://www.youtube.com/watch?v=x1", rows, "off-topic video filtered")
        self.assertNotIn("https://kotaku.com/c", rows, "off-topic news filtered")
        rsg = rows["https://www.youtube.com/watch?v=rsg1"]
        self.assertEqual(rsg["source_type"], "official")
        self.assertGreaterEqual(rsg["score"], 80)
        ign = rows["https://ign.com/a"]
        self.assertEqual(ign["source"], "IGN")
        self.assertTrue(ign["verified"], "corroborated by 2+ sources")
        leak = rows["https://reddit.com/e"]
        self.assertEqual(leak["category"], "leak")
        self.assertIn("[DRY RUN]", out)
        self.assertIn("Grand Theft Auto VI Launch Trailer", out, "official trailer posted first")
        site = Path(self.tmp) / "site"
        news = json.loads((site / "news.json").read_text())
        self.assertTrue(all({"id", "title", "summary", "url", "source", "source_type", "category",
                             "published", "image", "tags", "score", "verified"} <= set(n) for n in news))
        trending = json.loads((site / "trending.json").read_text())
        self.assertTrue(trending["videos"])
        self.assertIsNotNone(trending["videos"][0]["recent_vph"])
        d = publish.digest(self.store, self.cfg)
        self.assertEqual(d.count("launch trailer"), 1, "same story listed once")
        # following cycles: next posts are IGN (story) and the leak is held below leak threshold
        with redirect_stdout(io.StringIO()) as more:
            for _ in range(3):
                self.store.db.execute("UPDATE items SET posted_at=? WHERE posted_at LIKE '20%'",
                                      (iso(now() - timedelta(minutes=10)),))
                app.cycle(self.store, self.cfg)
        posts = more.getvalue()
        print(posts)
        self.assertIn("ign.com/a", posts)
        self.assertIn("watch?v=tgg1", posts, "13h-old video with 74K/h still posted")
        self.assertNotIn("polygon.com/b", posts, "duplicate story not reposted")
        # review mode: drafts go to admin chat with buttons, channel untouched
        os.environ.update(TELEGRAM_ADMIN_CHAT="42", POST_MODE="review")
        try:
            self.cfg["telegram"]["post_threshold_leak"] = 0
            self.store.db.execute("UPDATE items SET posted_at=? WHERE posted_at LIKE '20%'",
                                  (iso(now() - timedelta(hours=2)),))
            with redirect_stdout(io.StringIO()) as rv:
                app.cycle(self.store, self.cfg)
            self.assertIn("[DRY RUN REVIEW] -> 42", rv.getvalue())
            self.assertNotIn("[DRY RUN] -> @dry_run", rv.getvalue())
            self.assertIn("reddit.com/e", rv.getvalue(), "leak goes to review")
        finally:
            os.environ.pop("TELEGRAM_ADMIN_CHAT"); os.environ.pop("POST_MODE")


if __name__ == "__main__":
    unittest.main()
