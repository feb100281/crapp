"""Тонкий клиент Telegram Bot API на requests — без сторонних библиотек."""

from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger("tgbot")


class TelegramError(RuntimeError):
    pass


class TelegramUnreachable(TelegramError):
    """Нет связи с api.telegram.org (блокировка, нет интернета, нужен VPN/прокси)."""


class Bot:
    def __init__(self, token: str, session: requests.Session | None = None):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN не задан в .env")
        self.base = f"https://api.telegram.org/bot{token}/"
        self.http = session or requests.Session()
        proxy = os.getenv("TELEGRAM_PROXY", "").strip()      # http://host:port или socks5://host:port
        if proxy:
            self.http.proxies = {"http": proxy, "https": proxy}

    def _post(self, method: str, **kw):
        """POST без утечки токена в текст ошибок."""
        try:
            return self.http.post(self.base + method, **kw)
        except requests.ConnectionError:
            raise TelegramUnreachable(f"{method}: нет связи с api.telegram.org") from None
        except requests.Timeout:
            raise TelegramUnreachable(f"{method}: api.telegram.org не отвечает") from None

    @staticmethod
    def _json(method: str, resp):
        try:
            return resp.json()
        except ValueError:                   # ответил не Telegram: прокси, заглушка провайдера
            raise TelegramUnreachable(f"{method}: ответ не от Telegram (HTTP {resp.status_code})") from None

    def call(self, method: str, timeout: float = 30, files=None, **params):
        data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in params.items() if v is not None}
        resp = self._post(method, data=data, files=files, timeout=timeout)
        body = self._json(method, resp)
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description')}")
        return body["result"]

    # --- обёртки, которые нужны боту ---

    def me(self):
        return self.call("getMe")

    def updates(self, offset: int | None, wait: int = 50):
        """Длинный опрос: Telegram держит запрос до wait секунд, пока нет новых сообщений."""
        data = {"offset": offset, "timeout": wait, "allowed_updates": ["message", "callback_query"]}
        data = {k: (json.dumps(v) if isinstance(v, list) else v) for k, v in data.items() if v is not None}
        resp = self._post("getUpdates", data=data, timeout=wait + 10)
        body = self._json("getUpdates", resp)
        if not body.get("ok"):
            raise TelegramError(f"getUpdates: {body.get('description')}")
        return body["result"]

    def send(self, chat_id: int, text: str, markup=None, **kw):
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         reply_markup=markup, disable_web_page_preview=True, **kw)

    def edit(self, chat_id: int, message_id: int, text: str, markup=None):
        return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                         parse_mode="HTML", reply_markup=markup)

    def answer(self, callback_id: str, text: str = ""):
        try:
            return self.call("answerCallbackQuery", callback_query_id=callback_id, text=text or None)
        except TelegramError as exc:          # запрос устарел — не страшно
            log.debug("answerCallbackQuery: %s", exc)

    def typing(self, chat_id: int, action: str = "upload_document"):
        try:
            self.call("sendChatAction", chat_id=chat_id, action=action)
        except TelegramError:
            pass

    def photo(self, chat_id: int, filename: str, data: bytes, caption: str = "", markup=None):
        return self.call("sendPhoto", timeout=120, chat_id=chat_id, caption=caption or None, parse_mode="HTML",
                         reply_markup=markup, files={"photo": (filename, data, "image/png")})

    def document(self, chat_id: int, filename: str, data: bytes, caption: str = "", mime: str = ""):
        return self.call("sendDocument", timeout=120, chat_id=chat_id, caption=caption or None,
                         parse_mode="HTML", files={"document": (filename, data, mime or "application/octet-stream")})
