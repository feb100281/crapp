"""Запуск бота: python manage.py telegram_bot (длинный опрос, без вебхука и домена)."""

from __future__ import annotations

import logging
import time

import requests
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from dashboard.services import fx_feed
from tgbot import conf
from tgbot.api import Bot, TelegramError, TelegramUnreachable
from tgbot.handlers import BotApp


class Command(BaseCommand):
    help = "Telegram-бот: остатки и движение денег за день"

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        bot = Bot(conf.token())
        app = BotApp(bot)
        try:
            me = bot.me()
        except TelegramUnreachable:
            self.stderr.write(
                "Нет связи с api.telegram.org.\n"
                "Скорее всего, его блокирует провайдер. Варианты:\n"
                "  • включите VPN и запустите снова;\n"
                "  • укажите прокси в .env: TELEGRAM_PROXY=http://host:port (или socks5://host:port);\n"
                "  • запускайте бота на сервере, где Telegram доступен.\n"
                "Проверка: curl -m 10 https://api.telegram.org"
            )
            return
        except TelegramError as exc:
            self.stderr.write(f"Telegram отказал: {exc}. Проверьте TELEGRAM_BOT_TOKEN в .env")
            return
        conf.remember_username(me.get("username", ""))
        bot.call("setMyCommands", commands=[
            {"command": "balance", "description": "Остатки на дату"},
            {"command": "day", "description": "Движение денег за день"},
            {"command": "dds", "description": "Отчёт ДДС за год в Excel"},
            {"command": "cp", "description": "Оплаты контрагента"},
            {"command": "check", "description": "Проверка контрагента по ИНН"},
            {"command": "fx", "description": "Курсы валют"},
            {"command": "help", "description": "Что умеет бот"},
        ])
        for method, field, text in (
            ("setMyShortDescription", "short_description", conf.SHORT_DESCRIPTION),
            ("setMyDescription", "description", conf.DESCRIPTION),
        ):
            try:
                bot.call(method, **{field: text})
            except TelegramError:
                pass
        if not conf.admin_ids():
            self.stderr.write("TELEGRAM_ADMIN_IDS не задан — некому выдавать приглашения")
        self.stdout.write(f"Бот @{me['username']} запущен. Остановить — Ctrl+C")

        offset = None
        while True:
            fx_feed.maybe_update()          # курсы ЦБ: сразу и дальше раз в пару часов
            try:
                for upd in bot.updates(offset):
                    offset = upd["update_id"] + 1
                    close_old_connections()
                    app.handle(upd)
            except KeyboardInterrupt:
                self.stdout.write("Остановлен")
                return
            except (requests.RequestException, TelegramError) as exc:
                logging.warning("%s — повтор через 5 с", exc)
                time.sleep(5)
