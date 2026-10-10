"""Настройки бота из .env: токен, администраторы, адрес Mini App."""

from __future__ import annotations

import os
import json
from pathlib import Path

from django.conf import settings


def token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def admin_ids() -> set[int]:
    return {int(x) for x in os.getenv("TELEGRAM_ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}


def webapp_url() -> str:
    """Публичный HTTPS-адрес сайта (Mini App открывается только по https)."""
    url = os.getenv("TELEGRAM_WEBAPP_URL", "").strip().rstrip("/")
    return url if url.startswith("https://") else ""


INVITE_HOURS = int(os.getenv("TELEGRAM_INVITE_HOURS", "24") or 24)


SHORT_DESCRIPTION = "Остатки по банкам, движение денег, платежи контрагентам и курсы ЦБ."
DESCRIPTION = ("COSMORELAX · Деньги\n\n"
               "💰 Остатки на счетах на любую дату\n"
               "📋 Движение денег за день — PDF и Excel\n"
               "📊 Отчёт ДДС за год в Excel\n"
               "💸 Оплаты контрагента: сколько заплатили мы и он нам\n"
               "🛡 Проверка контрагента по ИНН: статус, руководитель, адрес, риски\n"
               "💱 Курсы ЦБ с графиком\n\n"
               "Доступ — по приглашению.")


def _username_file() -> Path:
    return Path(settings.BASE_DIR) / "data" / ".tg_bot_username"


def remember_username(name: str) -> None:
    """Бот при запуске запоминает свой @username — для ссылок-приглашений в админке."""
    if name:
        path = _username_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")


def bot_username() -> str:
    name = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if name:
        return name
    try:
        return _username_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _menu_file() -> Path:
    return Path(settings.BASE_DIR) / "data" / ".tg_menu.json"


def menu_version(chat: int) -> str:
    """Какую версию клавиатуры бот последней отправлял в этот чат."""
    try:
        return json.loads(_menu_file().read_text()).get(str(chat), "")
    except (OSError, ValueError):
        return ""


def remember_menu(chat: int, version: str) -> None:
    try:
        data = json.loads(_menu_file().read_text())
    except (OSError, ValueError):
        data = {}
    data[str(chat)] = version
    try:
        _menu_file().parent.mkdir(parents=True, exist_ok=True)
        _menu_file().write_text(json.dumps(data))
    except OSError:
        pass
