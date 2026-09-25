"""Optional Telegram, Bark, or Server酱 notices. Failures are logged only."""

from __future__ import annotations

import logging

from app.config import Settings
from app.httputil import site_client

log = logging.getLogger("app.notify")

CHANNELS = ("", "telegram", "bark", "serverchan")


def normalize_channel(raw: str | None) -> str | None:
    text = (raw or "").strip().lower()
    if text in ("none", "off"):
        text = ""
    if text not in CHANNELS:
        return None
    return text


async def send_notice(settings: Settings, title: str, body: str) -> None:
    channel = normalize_channel(getattr(settings, "notify_channel", "")) or ""
    if not channel:
        return
    try:
        if channel == "telegram":
            await _telegram(settings, title, body)
        elif channel == "bark":
            await _bark(settings, title, body)
        else:
            await _serverchan(settings, title, body)
    except Exception:
        log.warning("通知失败：%s", title, exc_info=True)
        return
    log.info("已通知 %s：%s", channel, title)


async def _telegram(settings: Settings, title: str, body: str) -> None:
    token = (settings.notify_telegram_token or "").strip()
    chat = (settings.notify_telegram_chat or "").strip()
    if not token or not chat:
        log.warning("Telegram 通知缺 token 或 chat id")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": f"{title}\n{body}".strip(), "disable_web_page_preview": True}
    async with site_client(settings) as client:
        response = await client.post(url, json=payload, timeout=8.0)
        response.raise_for_status()


async def _bark(settings: Settings, title: str, body: str) -> None:
    raw = (settings.notify_bark_url or "").strip().rstrip("/")
    if not raw:
        log.warning("Bark 通知没有地址")
        return
    url = raw if "://" in raw else f"https://api.day.app/{raw}"
    async with site_client(settings) as client:
        response = await client.post(
            url,
            json={"title": title, "body": body, "group": "jav-dl"},
            timeout=8.0,
        )
        response.raise_for_status()


async def _serverchan(settings: Settings, title: str, body: str) -> None:
    key = (settings.notify_serverchan_key or "").strip()
    if not key:
        log.warning("Server酱通知没有 SendKey")
        return
    url = key if key.startswith("http://") or key.startswith("https://") else f"https://sctapi.ftqq.com/{key}.send"
    async with site_client(settings) as client:
        response = await client.post(url, data={"title": title, "desp": body}, timeout=8.0)
        response.raise_for_status()
