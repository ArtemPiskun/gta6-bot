"""Russian copy (optional Claude rewrite), Telegram posting, site export."""
from __future__ import annotations

import html
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import net
from . import scoring
from .scoring import is_duplicate_story
from .store import Store, iso, now

log = logging.getLogger("gta6bot.publish")
MSK = timezone(timedelta(hours=3))
CAT_EMOJI = {"news": "📰", "update": "🛠", "guide": "📘", "video": "🎬", "leak": "🕵️"}
CAT_RU = {"news": "Новости", "update": "Обновление", "guide": "Гайд", "video": "Видео", "leak": "Слух"}


def fmt_num(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    for div, suf in ((1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}{suf}".replace(".0", "")
    return str(int(n))


def is_russian(s: str) -> bool:
    cyr = sum(1 for c in s if "а" <= c.lower() <= "я" or c.lower() == "ё")
    return cyr > len(s) * 0.3


# ------------------------------------------------------------- copywriting
EDITOR_PROMPT = """Ты редактор русскоязычного Telegram-канала про GTA 6. Канал читают фанаты, которым важны факты, а не ссылки.
Из материала ниже сделай пост или откажись от него.

Откажись ("skip": true), если это: стрим или запись стрима, реакция, мем, кликбейт без фактов, видео «скачиваю / жду / готовлю комнату»,
реклама, не про GTA 6, видео не на английском или русском, или если из данных нельзя написать 2 содержательных предложения.

Если пост нужен:
- title: заголовок по-русски до 80 символов, по сути, без капса и кликбейта;
- text: 2-3 предложения по-русски: что именно произошло или что показано, конкретные факты, цифры, даты из материала;
- why: одно предложение, почему это важно игроку;
- Ничего не выдумывай. Если это слух или неподтверждённая информация, прямо так и напиши в text.
- Для видео опиши, что в нём (по названию и описанию) и почему оно набирает просмотры; не пиши «смотрите видео».

Ответь строго JSON без пояснений: {"skip": false, "reason": "", "title": "", "text": "", "why": ""}"""


def llm_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def compose(row, related: list) -> dict | None:
    """Ask Claude to write the post. Returns dict(title,text,why) or None to skip."""
    facts = [f"Тип: {'видео YouTube' if row['kind'] == 'video' else 'новость'}",
             f"Источник: {row['source']} ({row['source_type']})",
             f"Заголовок: {row['title']}",
             f"Описание: {(row['summary'] or '')[:1500]}"]
    if row["kind"] == "video":
        facts.append(f"Канал: {row['channel_title']}; просмотры: {fmt_num(row['views'])}; "
                     f"скорость: {fmt_num(row['recent_vph'] or row['vph'])} в час")
    for r in related[:4]:
        facts.append(f"Похожий материал ({r['source']}): {r['title']}. {(r['summary'] or '')[:400]}")
    body = json.dumps({"model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"), "max_tokens": 600,
                       "system": EDITOR_PROMPT,
                       "messages": [{"role": "user", "content": "\n".join(facts)}]}).encode()
    raw = net.fetch("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
        "content-type": "application/json"}, timeout=60, retries=1)
    text = "".join(b.get("text", "") for b in json.loads(raw)["content"])
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if data.get("skip") or not data.get("text"):
        log.info("editor skipped %r: %s", row["title"][:60], data.get("reason"))
        return None
    return {"title": data["title"].strip(), "text": data["text"].strip(), "why": (data.get("why") or "").strip()}


def related_items(store: Store, row) -> list:
    from .scoring import tokens
    tk = tokens(row["title"])
    out = []
    for r in store.recent(24, "news"):
        if r["id"] == row["id"]:
            continue
        ot = tokens(r["title"])
        if tk and ot and len(tk & ot) / len(tk | ot) >= 0.3:
            out.append(r)
    return sorted(out, key=lambda r: len(r["summary"] or ""), reverse=True)


def render(row, cfg) -> str:
    e = html.escape
    title = row["title_ru"] or row["title"]
    cat = row["category"]
    lines = [f"<b>{e(title)}</b>"]
    if cat == "leak" and not row["verified"]:
        lines.append("⚠️ <i>Слух, официально не подтверждено</i>")
    if row["summary_ru"]:
        lines += ["", e(row["summary_ru"])]
    if row["why"]:
        lines += ["", f"💡 {e(row['why'])}"]
    lines.append("")
    if row["kind"] == "video":
        lines.append(f'🎬 <a href="{e(row["url"])}">{e(row["channel_title"])}</a> · {fmt_num(row["views"])} просмотров')
    else:
        badge = " ✅" if row["verified"] else ""
        lines.append(f'Источник: <a href="{e(row["url"])}">{e(row["source"])}</a>{badge}')
    lines.append(f"{cfg['telegram']['hashtags']} #{CAT_RU.get(cat, 'Новости')}")
    return "\n".join(lines)


# ------------------------------------------------------------- telegram
def tg_call(method: str, payload: dict) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    raw = net.fetch(f"https://api.telegram.org/bot{token}/{method}",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
                    timeout=45, retries=2)
    res = json.loads(raw)
    if not res.get("ok"):
        raise RuntimeError(res)
    return res


def send(chat: str, text: str, image: str = "") -> None:
    if os.getenv("DRY_RUN", "0") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("---- [DRY RUN] ->", chat, "\n" + text + (f"\n[image] {image}" if image else ""))
        return
    if image and len(text) <= 1024:
        try:
            tg_call("sendPhoto", {"chat_id": chat, "photo": image, "caption": text, "parse_mode": "HTML"})
            return
        except Exception as e:
            log.warning("sendPhoto failed, falling back to text: %s", e)
    tg_call("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "link_preview_options": {"is_disabled": False}})


def threshold(row, cfg) -> float:
    t = cfg["telegram"]
    if row["category"] == "leak" and not row["verified"]:
        base = t["post_threshold_leak"]
    elif row["kind"] == "video":
        base = t["post_threshold_video"]
    else:
        base = t["post_threshold_news"]
    if cfg["general"].get("launch_mode"):
        base += t.get("launch_threshold_delta", 0)
    return base


def post_due(store: Store, cfg) -> list[str]:
    t = cfg["telegram"]
    launch = cfg["general"].get("launch_mode")
    chat = os.getenv("TELEGRAM_CHANNEL", "@dry_run")
    hour_msk = datetime.now(MSK).hour
    q0, q1 = t.get("quiet_hours_msk", [2, 8])
    if not launch and q0 <= hour_msk < q1:
        return []
    limit = t["launch_max_posts_per_hour"] if launch else t["max_posts_per_hour"]
    last_hour = store.posted_since(60)
    if len(last_hour) >= limit:
        return []
    if last_hour and store.posted_since(t["min_gap_minutes"]):
        return []
    recent_posted = store.posted_since(24 * 60)
    candidates = [r for r in store.recent(max(t["max_age_hours"], t.get("max_age_hours_video", 24)))
                  if not r["posted_at"] and r["score"] >= threshold(r, cfg)
                  and scoring.hours_since(r["published"]) <= (
                      t.get("max_age_hours_video", 24) if r["kind"] == "video" else t["max_age_hours"])]
    candidates.sort(key=lambda r: r["score"], reverse=True)
    if not llm_available():
        if store.get("warned_no_llm") != time_bucket():
            notify_admin("Посты на паузе: нужен ANTHROPIC_API_KEY, без него бот не пишет текст постов.")
            store.set("warned_no_llm", time_bucket())
        log.warning("no ANTHROPIC_API_KEY: posting disabled, monitoring and export continue")
        return []
    # at most one video among the last N channel posts: the channel is about news, not links
    last_posts = store.db.execute("SELECT kind FROM items WHERE posted_at LIKE '20%' "
                                  "ORDER BY posted_at DESC LIMIT ?", (t.get("video_every_n_posts", 3) - 1,)).fetchall()
    allow_video = not any(r["kind"] == "video" for r in last_posts)
    sent = []
    tried = 0
    for row in candidates:
        if row["kind"] == "video" and not allow_video and row["source_type"] != "official":
            continue
        if is_duplicate_story(row, recent_posted):
            store.update(row["id"], posted_at="!dup")  # skip forever, already covered
            continue
        if tried >= 3:  # cap editor calls per cycle
            break
        tried += 1
        try:
            post = compose(row, related_items(store, row))
        except Exception as e:
            log.warning("editor failed for %r: %s", row["title"][:60], e)
            continue
        if not post:
            store.update(row["id"], posted_at="!skip")
            continue
        store.update(row["id"], title_ru=post["title"], summary_ru=post["text"], why=post["why"])
        row = store.db.execute("SELECT * FROM items WHERE id=?", (row["id"],)).fetchone()
        if is_duplicate_story(row, recent_posted):
            store.update(row["id"], posted_at="!dup")
            continue
        try:
            if needs_review(row, cfg):
                send_for_review(row, cfg)
                store.update(row["id"], posted_at="!pending")
                sent.append("[на одобрение] " + row["title"])
                continue  # drafts don't count towards channel pacing
            send(chat, render(row, cfg), row["image"])
        except Exception as e:
            log.error("telegram send failed: %s", e)
            notify_admin(f"Ошибка отправки в канал: {e}")
            break
        store.update(row["id"], posted_at=iso(now()))
        sent.append(row["title"])
        break  # one post per cycle keeps the feed paced (min_gap)
    return sent


def time_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------- review mode
def post_mode(cfg) -> str:
    return os.getenv("POST_MODE") or cfg["telegram"].get("mode", "auto")


def needs_review(row, cfg) -> bool:
    """review: everything goes to the admin first. auto: only unverified leaks do (if an admin chat exists)."""
    if not os.getenv("TELEGRAM_ADMIN_CHAT"):
        return False
    if post_mode(cfg) == "review":
        return True
    return row["category"] == "leak" and not row["verified"]


def send_for_review(row, cfg) -> None:
    admin = os.environ["TELEGRAM_ADMIN_CHAT"]
    text = f"📝 <b>Черновик</b> · score {int(row['score'])}\n\n" + render(row, cfg)
    markup = {"inline_keyboard": [[{"text": "✅ Опубликовать", "callback_data": f"pub:{row['id']}"},
                                   {"text": "❌ Пропустить", "callback_data": f"skip:{row['id']}"}]]}
    if os.getenv("DRY_RUN", "0") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("---- [DRY RUN REVIEW] ->", admin, "\n" + text)
        return
    tg_call("sendMessage", {"chat_id": admin, "text": text, "parse_mode": "HTML", "reply_markup": markup})


def process_approvals(store: Store, cfg) -> list[str]:
    """Poll button presses from the admin chat and publish approved drafts."""
    if not os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("DRY_RUN", "0") == "1":
        return []
    admin = os.getenv("TELEGRAM_ADMIN_CHAT", "")
    offset = int(store.get("tg_offset", "0"))
    res = tg_call("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["callback_query"]})
    done = []
    for upd in res.get("result", []):
        store.set("tg_offset", str(upd["update_id"] + 1))
        cq = upd.get("callback_query")
        if not cq or str(cq["from"]["id"]) != admin and str(cq.get("message", {}).get("chat", {}).get("id")) != admin:
            continue
        action, _, iid = cq.get("data", "").partition(":")
        row = store.db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        answer = "Не найдено"
        if row and row["posted_at"] == "!pending":
            if action == "pub":
                send(os.environ["TELEGRAM_CHANNEL"], render(row, cfg), row["image"])
                store.update(iid, posted_at=iso(now()))
                answer = "Опубликовано"
                done.append(row["title"])
            else:
                store.update(iid, posted_at="!skip")
                answer = "Пропущено"
        try:
            tg_call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": answer})
            msg = cq.get("message")
            if msg:
                tg_call("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                                   "reply_markup": {"inline_keyboard": []}})
        except Exception as e:
            log.warning("callback ack failed: %s", e)
    return done


def notify_admin(text: str) -> None:
    admin = os.getenv("TELEGRAM_ADMIN_CHAT")
    if admin:
        try:
            send(admin, html.escape(text))
        except Exception as e:
            log.error("admin notify failed: %s", e)


def digest(store: Store, cfg, hours=24, top=10) -> str:
    vids = [r for r in store.recent(hours, "video") if r["vph"]]
    vids.sort(key=lambda r: r["vph"], reverse=True)
    e = html.escape
    lines = [f"📊 <b>Что стреляет по GTA 6 за {hours} ч</b>", ""]
    for i, r in enumerate(vids[:top], 1):
        lines.append(f'{i}. <a href="{e(r["url"])}">{e(r["title"][:80])}</a>\n'
                     f'   {e(r["channel_title"])} · 👁 {fmt_num(r["views"])} · ⚡ {fmt_num(r["vph"])}/час')
    news = []
    for r in sorted(store.recent(hours, "news"), key=lambda r: r["score"], reverse=True):
        if len(news) < 5 and not is_duplicate_story(r, news):
            news.append(r)
    if news:
        lines += ["", "📰 <b>Главные новости</b>"]
        lines += [f'• <a href="{e(r["url"])}">{e((r["title_ru"] or r["title"])[:90])}</a>' for r in news]
    lines += ["", cfg["telegram"]["hashtags"]]
    return "\n".join(lines)


# ------------------------------------------------------------- site export
def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def export_site(store: Store, cfg) -> dict:
    out = Path(cfg["general"]["export_dir"])
    news = []
    for r in store.all_items(400):
        if r["kind"] == "video" and r["score"] < 40:
            continue
        news.append({
            "id": r["id"], "title": r["title_ru"] or r["title"],
            "summary": r["summary_ru"] or r["summary"][:400], "url": r["url"], "source": r["source"],
            "source_type": r["source_type"], "category": r["category"], "published": r["published"],
            "image": r["image"], "tags": json.loads(r["tags"] or "[]"), "score": int(r["score"] or 0),
            "verified": bool(r["verified"]),
        })
    news = news[:200]
    vids = sorted((r for r in store.recent(72, "video") if r["vph"]), key=lambda r: r["score"], reverse=True)
    trending = [{"video_id": r["video_id"], "title": r["title"], "channel_id": r["channel_id"],
                 "channel_title": r["channel_title"], "url": r["url"], "thumbnail": r["image"],
                 "published": r["published"], "views": r["views"], "vph": round(r["vph"] or 0),
                 "recent_vph": round(r["recent_vph"]) if r["recent_vph"] else None,
                 "score": int(r["score"] or 0)} for r in vids[:50]]
    chans: dict[str, dict] = {}
    for r in store.recent(24, "video"):
        c = chans.setdefault(r["channel_id"], {"channel_id": r["channel_id"], "title": r["channel_title"],
                                               "videos_24h": 0, "total_vph": 0, "best_video_url": "",
                                               "_best": -1})
        c["videos_24h"] += 1
        c["total_vph"] += r["vph"] or 0
        if (r["vph"] or 0) > c["_best"]:
            c["_best"], c["best_video_url"] = r["vph"] or 0, r["url"]
    channels = sorted(chans.values(), key=lambda c: c["total_vph"], reverse=True)[:50]
    top = channels[0]["total_vph"] if channels else 1
    for c in channels:
        c.pop("_best")
        c["total_vph"] = round(c["total_vph"])
        c["score"] = int(100 * c["total_vph"] / top) if top else 0
    _write_json(out / "news.json", news)
    _write_json(out / "trending.json", {"updated": iso(now()), "videos": trending})
    _write_json(out / "channels.json", {"updated": iso(now()), "channels": channels})
    return {"news": len(news), "trending": len(trending), "channels": len(channels)}
