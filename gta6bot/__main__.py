"""CLI.

python -m gta6bot run        один цикл: сбор -> скоринг -> пост -> экспорт
python -m gta6bot loop       бесконечный цикл (для сервера / ПК)
python -m gta6bot digest     отправить сводку «что стреляет» за 24 ч
python -m gta6bot top        показать топ сигналов в консоли
python -m gta6bot test-telegram   проверить токен и права в канале
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import tomllib
from pathlib import Path

from . import collectors, publish, scoring
from .store import Store

log = logging.getLogger("gta6bot")
ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_cfg(path: str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if os.getenv("LAUNCH_MODE") is not None:
        cfg["general"]["launch_mode"] = os.getenv("LAUNCH_MODE") == "1"
    if not os.getenv("TELEGRAM_CHANNEL") and cfg["telegram"].get("channel"):
        os.environ["TELEGRAM_CHANNEL"] = cfg["telegram"]["channel"]
    if os.getenv("EXPORT_DIR"):
        cfg["general"]["export_dir"] = os.environ["EXPORT_DIR"]
    return cfg


def open_store(cfg) -> Store:
    p = cfg["general"]["db_path"]
    store = Store(p if os.path.isabs(p) else str(ROOT / p))
    for ch in cfg["youtube"]["seed_channels"]:
        store.add_channel(ch["id"], ch["name"], ch.get("official", False))
    return store


def cycle(store: Store, cfg) -> dict:
    stats = {}
    key = os.getenv("YOUTUBE_API_KEY")
    stats["rss_videos"] = collectors.collect_channel_rss(store, cfg)
    if key:
        try:
            stats["search_videos"] = collectors.collect_youtube_search(store, cfg, key)
            stats["stats_refreshed"] = collectors.refresh_video_stats(store, key)
        except Exception as e:
            log.error("youtube api: %s", e)
            publish.notify_admin(f"YouTube API ошибка: {e}")
    stats["news"] = collectors.collect_news(store, cfg)
    scoring.rescore(store, cfg)
    stats["new_channels"] = collectors.auto_track_channels(store, cfg)
    try:
        stats["approved"] = publish.process_approvals(store, cfg)
    except Exception as e:
        log.error("approvals: %s", e)
    stats["posted"] = publish.post_due(store, cfg)
    try:
        stats["export"] = publish.export_site(store, cfg)
    except Exception as e:
        log.error("export: %s", e)
    log.info("cycle: %s", stats)
    return stats


def cycle_collect_only(store: Store, cfg) -> None:
    key = os.getenv("YOUTUBE_API_KEY")
    collectors.collect_channel_rss(store, cfg)
    if key:
        collectors.collect_youtube_search(store, cfg, key)
        collectors.refresh_video_stats(store, key)
    collectors.collect_news(store, cfg)
    scoring.rescore(store, cfg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gta6bot")
    ap.add_argument("cmd", choices=["run", "loop", "digest", "top", "preview", "test-telegram"])
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env(ROOT / ".env")
    cfg = load_cfg(a.config)
    store = open_store(cfg)

    if a.cmd == "run":
        cycle(store, cfg)
    elif a.cmd == "loop":
        every = cfg["general"].get("loop_interval_minutes", 5) * 60
        last_digest = store.get("last_digest_day")
        while True:
            try:
                cycle(store, cfg)
                today = time.strftime("%Y-%m-%d")
                if time.gmtime().tm_hour == 17 and last_digest != today:  # 20:00 МСК
                    publish.send(os.getenv("TELEGRAM_CHANNEL", "@dry_run"), publish.digest(store, cfg))
                    store.set("last_digest_day", today)
                    last_digest = today
            except Exception as e:
                log.exception("cycle crashed")
                publish.notify_admin(f"Цикл упал: {e}")
            time.sleep(every)
    elif a.cmd == "digest":
        publish.send(os.getenv("TELEGRAM_CHANNEL", "@dry_run"), publish.digest(store, cfg))
    elif a.cmd == "preview":
        # collect + score, then show the next posts without sending or marking anything
        os.environ["DRY_RUN"] = "1"
        cycle_collect_only(store, cfg)
        cands = sorted((r for r in store.recent(24) if not r["posted_at"]
                        and r["score"] >= publish.threshold(r, cfg)), key=lambda r: r["score"], reverse=True)
        shown = 0
        for r in cands:
            if shown >= 6:
                break
            print(f"\n===== score {r['score']} | {r['kind']} | {r['source']} | {r['title']}")
            if publish.llm_available():
                post = publish.compose(r, publish.related_items(store, r))
                if not post:
                    print("[редактор отказался]")
                    continue
                store.update(r["id"], title_ru=post["title"], summary_ru=post["text"], why=post["why"])
                r = store.db.execute("SELECT * FROM items WHERE id=?", (r["id"],)).fetchone()
            print(publish.render(r, cfg))
            shown += 1
    elif a.cmd == "top":
        for r in sorted(store.recent(24), key=lambda r: r["score"], reverse=True)[:25]:
            print(f"{r['score']:5.1f} {r['kind']:5} {r['category']:6} {(r['source'] or '')[:18]:18} {r['title'][:80]}")
    elif a.cmd == "test-telegram":
        me = publish.tg_call("getMe", {})["result"]
        print("bot:", me["username"])
        chat = os.environ["TELEGRAM_CHANNEL"]
        info = publish.tg_call("getChat", {"chat_id": chat})["result"]
        print("channel:", info.get("title"))
        publish.send(chat, "✅ Бот подключён. Мониторинг GTA 6 запущен.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
