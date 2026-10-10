"""
Логика бота: меню, выбор даты, отчёты, контрагенты, курсы.

Доступ выдаётся только в админке (приглашение-ссылка). Отчёты строятся
из витрин теми же сервисами, что и печать на сайте.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
from datetime import date, timedelta

from django.db.models import Max, Min
from django.utils import timezone

from dashboard.models import CashBalance
from dashboard.admin.common import money2
from dashboard.services import cp_check, cp_render, cp_report, day_render, day_report, dds_export, fx_report

from . import conf
from .api import Bot, TelegramError
from .dates import calendar_markup, parse
from .models import TgInvite, TgUser

log = logging.getLogger("tgbot")

BTN_BAL = "💰 Остатки"
BTN_DAY = "📋 Движение за день"
BTN_DDS = "📊 Отчёт ДДС"
BTN_CP = "💸 Оплаты контрагента"
BTN_FX = "💱 Курсы"
BTN_CHK = "🛡 Проверка контрагента"
BTN_APP = "📱 Приложение"
KINDS = {"bal": "Остатки на дату", "day": "Движение денег за день"}
NO_ACCESS = "🔒 Доступ к боту — по приглашению. Попросите ссылку у администратора."
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def data_span() -> tuple[date | None, date | None]:
    span = CashBalance.objects.aggregate(lo=Min("date"), hi=Max("date"))
    return span["lo"], span["hi"]


def dds_caption(name: str, report: dict, year: int) -> str:
    t = report["totals"]
    _, hi = data_span()
    return "\n".join([
        f"📊 <b>{esc(name)}</b>" + (f" · данные на {hi:%d.%m.%Y}" if hi and hi.year == year else ""),
        f"Поступления: <b>{money2(abs(t['inflow']))}</b> ₽",
        f"Выплаты: <b>{money2(abs(t['outflow']))}</b> ₽",
        f"Чистый поток: <b>{money2(t['net'], signed=True)}</b> ₽",
        f"Остаток на конец: <b>{money2(t['closing'])}</b> ₽",
        "Открывайте в Excel: плюсики над месяцами раскрывают дни.",
    ])


class BotApp:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.last_kind: dict[int, str] = {}     # что пользователь смотрел последним
        self.cp_found: dict[int, list] = {}     # результаты поиска контрагента: индекс → ключ

    # ------------------------------------------------------------------ доступ

    @staticmethod
    def is_admin(tg_id: int) -> bool:
        return tg_id in conf.admin_ids()

    def user(self, frm: dict) -> TgUser | None:
        """Пользователь с доступом или None. Администратора из .env пускаем всегда."""
        tg_id = frm["id"]
        u = TgUser.objects.filter(tg_id=tg_id).first()
        if u is None and self.is_admin(tg_id):
            u = TgUser.objects.create(tg_id=tg_id, first_name=frm.get("first_name", ""),
                                      username=frm.get("username", ""), note="администратор")
        if u and (u.is_active or self.is_admin(tg_id)):
            TgUser.objects.filter(pk=u.pk).update(last_seen=timezone.now())
            return u
        return None

    @staticmethod
    def menu() -> dict:
        rows = [[{"text": BTN_BAL}, {"text": BTN_DAY}],
                [{"text": BTN_CP}, {"text": BTN_CHK}],
                [{"text": BTN_DDS}, {"text": BTN_FX}]]
        app = conf.webapp_url()
        if app:
            rows.append([{"text": BTN_APP, "web_app": {"url": f"{app}/tg/app/"}}])
        return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}

    @classmethod
    def menu_version(cls) -> str:
        return hashlib.md5(json.dumps(cls.menu()["keyboard"], ensure_ascii=False).encode()).hexdigest()[:8]

    def send_menu(self, chat: int, text: str) -> None:
        """Сообщение с нижней клавиатурой; запоминаем, какая версия меню у чата."""
        self.bot.send(chat, text, self.menu())
        conf.remember_menu(chat, self.menu_version())

    # ------------------------------------------------------------------ входящие

    def handle(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                self.on_callback(update["callback_query"])
            elif "message" in update:
                self.on_message(update["message"])
        except Exception:                       # бот не должен падать из-за одного сообщения
            log.exception("update %s", update.get("update_id"))
            chat = (update.get("message") or update.get("callback_query", {}).get("message") or {}).get("chat", {})
            if chat.get("id"):
                try:
                    self.bot.send(chat["id"], "Что-то пошло не так. Попробуйте ещё раз чуть позже.")
                except TelegramError:
                    pass

    def on_message(self, msg: dict) -> None:
        chat, frm = msg["chat"]["id"], msg.get("from") or {}
        if msg["chat"].get("type") != "private":
            return                               # в группах молчим: отчёты только в личке
        text = (msg.get("text") or "").strip()

        if text.startswith("/start"):
            return self.start(chat, frm, text.partition(" ")[2].strip())
        if self.user(frm) is None:
            return self.bot.send(chat, NO_ACCESS)
        if text not in ("/help", "/menu") and conf.menu_version(chat) != self.menu_version():
            self.send_menu(chat, "🔄 Меню обновлено — новые кнопки внизу 👇")   # Telegram сам клавиатуру не обновляет

        if text in ("/balance", BTN_BAL):
            return self.ask_date(chat, "bal")
        if text in ("/day", BTN_DAY):
            return self.ask_date(chat, "day")
        if text in ("/cp", BTN_CP, "🔎 Контрагент"):     # старая подпись кнопки
            self.last_kind[chat] = "cp"
            return self.bot.send(chat, "🔎 Напишите часть названия контрагента или ИНН — например "
                                       "<b>спутник</b> или <b>7708</b>.")
        if text in ("/check", BTN_CHK) or text.startswith("/check "):
            self.last_kind[chat] = "chk"
            arg = text.partition(" ")[2].strip() if text.startswith("/check ") else ""
            if arg:
                return self.check_query(chat, arg)
            return self.bot.send(chat, "🛡 Напишите <b>ИНН</b> (10 или 12 цифр) или <b>название</b> — "
                                       "покажу карточку из ЕГРЮЛ, на что обратить внимание и наши расчёты с ним.")
        if text in ("/dds", BTN_DDS):
            return self.dds_menu(chat)
        if text in ("/fx", BTN_FX):
            return self.fx_menu(chat)
        if text in ("/help", "/menu"):
            return self.help(chat, frm)

        if self.last_kind.get(chat) == "chk" and len(text) >= 2 and not text.startswith("/"):
            return self.check_query(chat, text)

        on = parse(text)
        if on:
            kind = self.last_kind.get(chat)
            if kind in KINDS:
                return self.report(chat, kind, on)
            return self.bot.send(chat, f"Что показать за <b>{on:%d.%m.%Y}</b>?", {"inline_keyboard": [[
                {"text": "💰 Остатки", "callback_data": f"d:bal:{on.isoformat()}"},
                {"text": "📋 Движение", "callback_data": f"d:day:{on.isoformat()}"}]]})
        if len(text) >= 2 and not text.startswith("/"):
            return self.cp_search(chat, text)    # любой текст — поиск контрагента
        self.send_menu(chat, "Выберите отчёт кнопкой внизу.")

    def on_callback(self, cq: dict) -> None:
        data = cq.get("data") or ""
        msg = cq.get("message") or {}
        chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
        if data == "noop":
            return self.bot.answer(cq["id"])
        if self.user(cq["from"]) is None:
            return self.bot.answer(cq["id"], "Нет доступа")
        self.bot.answer(cq["id"])
        head, _, rest = data.partition(":")
        kind, _, arg = rest.partition(":")

        if data == "x":
            return self.bot.edit(chat, mid, "Выбор даты закрыт.")
        if head == "q":                          # быстрые даты
            lo, hi = data_span()
            if arg == "cal":
                base = hi or date.today()
                return self.bot.edit(chat, mid, f"{KINDS[kind]}: выберите дату",
                                     calendar_markup(kind, base.year, base.month, lo, hi))
            on = (hi or date.today()) - timedelta(days=1 if arg == "prev" else 0)
            return self.report(chat, kind, on, edit=mid)
        if head == "c":                          # листание календаря
            y, m = map(int, arg.split("-"))
            lo, hi = data_span()
            return self.bot.edit(chat, mid, f"{KINDS[kind]}: выберите дату", calendar_markup(kind, y, m, lo, hi))
        if head == "d":                          # дата выбрана
            return self.report(chat, kind, date.fromisoformat(arg), edit=mid)
        if head == "f":                          # файл отчёта за день
            return self.send_day_file(chat, kind, date.fromisoformat(arg))
        if head == "p":                          # контрагент: kind=индекс, arg=период
            return self.cp_report(chat, int(kind), arg or "y")
        if head == "g":                          # файл по контрагенту: kind=формат, arg=индекс:период
            idx, _, period = arg.partition(":")
            return self.cp_file(chat, kind, int(idx), period)
        if head == "k":                          # проверка контрагента: kind=ИНН
            return self.check_inn(chat, kind)
        if head == "dds":                        # отчёт ДДС за год: kind=год
            return self.dds(chat, int(kind))
        if head == "fxf":                        # график файлом — без сжатия Telegram
            return self.fx_file(chat, kind, int(arg or 90))
        if head == "fx":                         # курс: kind=валюта, arg=дней
            return self.fx(chat, kind, int(arg or 90))

    # ------------------------------------------------------------------ вход

    def start(self, chat: int, frm: dict, code: str) -> None:
        user = self.user(frm)
        if user is None and code:
            inv = TgInvite.objects.filter(code=code).first()
            if inv and inv.is_valid:
                user, _ = TgUser.objects.update_or_create(
                    tg_id=frm["id"], defaults={"first_name": frm.get("first_name", ""),
                                               "username": frm.get("username", ""), "is_active": True,
                                               "note": inv.note})
                inv.used_at, inv.used_by = timezone.now(), user
                inv.save(update_fields=["used_at", "used_by"])
                login = f" (@{esc(frm['username'])})" if frm.get("username") else ""
                self.notify_admins(f"✅ По приглашению «{esc(inv.note) or inv.code}» подключился "
                                   f"{esc(frm.get('first_name'))}{login}")
            else:
                return self.bot.send(chat, "🔒 Ссылка-приглашение недействительна или уже использована. "
                                           "Попросите у администратора новую.")
        if user is None:
            return self.bot.send(chat, NO_ACCESS)
        self.help(chat, frm, hello=True)

    def help(self, chat: int, frm: dict, hello: bool = False) -> None:
        h = timezone.localtime().hour
        greet = "Доброй ночи" if h < 5 else "Доброе утро" if h < 12 else "Добрый день" if h < 18 else "Добрый вечер"
        _, hi = data_span()
        lines = [f"{greet}, {esc(frm.get('first_name') or 'коллега')}! 👋" if hello else "<b>Что умеет бот</b>"]
        if hi:
            lines.append(f"Данные по выпискам — на <b>{hi:%d.%m.%Y}</b>.")
        lines += ["",
                  f"{BTN_BAL} — деньги на счетах на дату: сводка и PDF",
                  f"{BTN_DAY} — поступления и выплаты по статьям, PDF или Excel",
                  f"{BTN_CHK} — карточка из ЕГРЮЛ по ИНН или названию: статус, руководитель, адрес, риски",
                  f"{BTN_DDS} — отчёт о движении денег за год в Excel, как на сайте",
                  f"{BTN_CP} — сколько заплатили контрагенту и сколько он нам",
                  f"{BTN_FX} — курсы ЦБ, график и выводы",
                  "", "Дату можно просто написать: <b>02.10</b> или <b>вчера</b>. "
                      "Любой другой текст — поиск контрагента."]
        if conf.webapp_url():
            lines.append(f"{BTN_APP} — всё то же в удобном окне.")
        self.send_menu(chat, "\n".join(lines))

    def notify_admins(self, text: str) -> None:
        for admin in conf.admin_ids():
            try:
                self.bot.send(admin, text)
            except TelegramError:
                pass

    # ------------------------------------------------------------------ отчёты по дате

    def ask_date(self, chat: int, kind: str) -> None:
        self.last_kind[chat] = kind
        _, hi = data_span()
        if not hi:
            return self.bot.send(chat, "Данных пока нет — витрины не построены.")
        prev = hi - timedelta(days=1)
        self.bot.send(chat, f"{KINDS[kind]}: за какой день?\n<i>Последние данные — на {hi:%d.%m.%Y}.</i>",
                      {"inline_keyboard": [
                          [{"text": f"📌 Последний день · {hi:%d.%m}", "callback_data": f"q:{kind}:last"},
                           {"text": f"Накануне · {prev:%d.%m}", "callback_data": f"q:{kind}:prev"}],
                          [{"text": "📅 Выбрать в календаре", "callback_data": f"q:{kind}:cal"}]]})

    def report(self, chat: int, kind: str, on: date, edit: int | None = None) -> None:
        self.last_kind[chat] = kind
        lo, hi = data_span()
        if not hi:
            return self.bot.send(chat, "Данных пока нет — витрины не построены.")
        note = ""
        if on > hi:
            note = (f"⚠️ За {on:%d.%m.%Y} выписок ещё нет. Последние данные — на <b>{hi:%d.%m.%Y}</b>, "
                    f"показываю их.\n\n")
            on = hi
        elif on < lo:
            note = f"⚠️ Данные начинаются с {lo:%d.%m.%Y}.\n\n"
            on = lo
        if edit:
            try:
                self.bot.edit(chat, edit, f"{KINDS[kind]} · <b>{on:%d.%m.%Y}</b>")
            except TelegramError:
                pass

        if kind == "bal":
            self.bot.send(chat, note + day_render.balances_text(on))
            self.bot.typing(chat)
            try:
                self.bot.document(chat, f"Остатки {on:%d.%m.%Y}.pdf", day_render.balances_pdf(on), mime="application/pdf")
            except day_render.PdfUnavailable:
                self.bot.send(chat, "PDF пока недоступен на сервере (нужен Playwright).")
            return

        report = day_report.build(on)
        nav = []
        if on > lo:
            nav.append({"text": "‹ день", "callback_data": f"d:day:{(on - timedelta(days=1)).isoformat()}"})
        nav.append({"text": "📅 Дата", "callback_data": "q:day:cal"})
        if on < hi:
            nav.append({"text": "день ›", "callback_data": f"d:day:{(on + timedelta(days=1)).isoformat()}"})
        buttons = [] if report["empty"] else [[
            {"text": "📄 PDF", "callback_data": f"f:pdf:{on.isoformat()}"},
            {"text": "📊 Excel", "callback_data": f"f:xlsx:{on.isoformat()}"}]]
        self.bot.send(chat, note + day_render.day_text(report), {"inline_keyboard": buttons + [nav]})

    def send_day_file(self, chat: int, fmt: str, on: date) -> None:
        report = day_report.build(on)
        self.bot.typing(chat)
        if fmt == "xlsx":
            return self.bot.document(chat, f"Движение денег {on:%d.%m.%Y}.xlsx", day_render.day_book(report).save(),
                                     mime=XLSX)
        try:
            self.bot.document(chat, f"Движение денег {on:%d.%m.%Y}.pdf", day_render.day_pdf(report),
                              mime="application/pdf")
        except day_render.PdfUnavailable:
            self.bot.send(chat, "PDF пока недоступен на сервере (нужен Playwright). Excel работает.")

    # ------------------------------------------------------------------ контрагенты

    def cp_search(self, chat: int, text: str) -> None:
        found = cp_report.search(text)
        if not found:
            return self.bot.send(chat, f"Контрагентов по запросу «{esc(text)}» не нашлось. "
                                       f"Попробуйте другую часть названия или ИНН.")
        self.cp_found[chat] = [f["key"] for f in found]
        rows = [[{"text": f"{f['name'][:40]} · {f['last']:%d.%m.%y}", "callback_data": f"p:{i}:y"}]
                for i, f in enumerate(found)]
        self.bot.send(chat, f"🔎 Нашлось {len(found)}. Выберите контрагента:", {"inline_keyboard": rows})

    def _cp_key(self, chat: int, idx: int) -> str | None:
        keys = self.cp_found.get(chat) or []
        return keys[idx] if 0 <= idx < len(keys) else None

    def cp_report(self, chat: int, idx: int, period: str) -> None:
        key = self._cp_key(chat, idx)
        if not key:
            return self.bot.send(chat, "Поиск устарел — напишите название ещё раз.")
        r = cp_report.build(key, period)
        periods = [{"text": ("✓ " if p == period else "") + name, "callback_data": f"p:{idx}:{p}"}
                   for p, name in cp_report.PERIODS.items()]
        files = [] if not r["count"] else [[
            {"text": "📄 PDF", "callback_data": f"g:pdf:{idx}:{period}"},
            {"text": "📊 Excel", "callback_data": f"g:xlsx:{idx}:{period}"}]]
        check = [[{"text": "🛡 Проверить контрагента", "callback_data": f"k:{r['inn']}"}]] if r["inn"] else []
        self.bot.send(chat, cp_render.text(r), {"inline_keyboard": [periods[:2], periods[2:]] + files + check})

    def cp_file(self, chat: int, fmt: str, idx: int, period: str) -> None:
        key = self._cp_key(chat, idx)
        if not key:
            return self.bot.send(chat, "Поиск устарел — напишите название ещё раз.")
        r = cp_report.build(key, period)
        name = f"Платежи {r['name'][:40]}".replace("/", " ").replace('"', "")
        self.bot.typing(chat)
        if fmt == "xlsx":
            return self.bot.document(chat, f"{name}.xlsx", cp_render.book(r).save(), mime=XLSX)
        try:
            self.bot.document(chat, f"{name}.pdf", cp_render.pdf(r), mime="application/pdf")
        except day_render.PdfUnavailable:
            self.bot.send(chat, "PDF пока недоступен на сервере (нужен Playwright). Excel работает.")

    # ------------------------------------------------------------------ курсы

    # ------------------------------------------------------------------ отчёт ДДС

    def dds_menu(self, chat: int) -> None:
        ys = dds_export.years()
        if not ys:
            return self.bot.send(chat, "Отчёта ДДС пока нет — витрина не построена.")
        if len(ys) == 1:
            return self.dds(chat, ys[0])
        btns = [{"text": f"{'📌 ' if y == ys[-1] else ''}{y}", "callback_data": f"dds:{y}"} for y in ys[-4:][::-1]]
        self.bot.send(chat, "📊 <b>Отчёт ДДС</b> в Excel: помесячно, дни под плюсиками, расшифровка "
                            "по контрагентам и все операции.\nЗа какой год?", {"inline_keyboard": [btns]})

    def dds(self, chat: int, year: int) -> None:
        self.bot.typing(chat)
        book, name, report = dds_export.book(year)
        if book is None:
            return self.bot.send(chat, f"За {year} год данных нет.")
        caption = dds_caption(name, report, year)
        self.bot.document(chat, f"{name}.xlsx", book.save(), mime=XLSX, caption=caption)

    def fx_menu(self, chat: int) -> None:
        curs = fx_report.currencies()
        if not curs:
            return self.bot.send(chat, "Курсов пока нет.")
        row = [{"text": (f"{fx_report.SYMBOLS[c]} {c}" if fx_report.SYMBOLS.get(c, c) != c else c), "callback_data": f"fx:{c}:90"} for c in curs]
        self.bot.send(chat, "💱 Какой курс показать?\n<i>Курс ЦБ на сегодня, а в рабочие дни ближе к вечеру — и на завтра.</i>",
                      {"inline_keyboard": [row[i:i + 3] for i in range(0, len(row), 3)]})

    def fx(self, chat: int, cur: str, days: int) -> None:
        s = fx_report.summary(cur, days)
        if not s:
            return self.bot.send(chat, f"По {esc(cur)} курсов нет.")
        periods = [{"text": ("✓ " if d == days else "") + name, "callback_data": f"fx:{cur}:{d}"}
                   for d, name in ((30, "30 дней"), (90, "90 дней"), (365, "Год"))]
        others = [{"text": c, "callback_data": f"fx:{c}:{days}"} for c in fx_report.currencies() if c != cur]
        markup = {"inline_keyboard": [periods] + [others[i:i + 5] for i in range(0, len(others), 5)]
                  + [[{"text": "🔍 График в полном качестве", "callback_data": f"fxf:{cur}:{days}"}]]}
        self.bot.typing(chat, "upload_photo")
        try:
            caption = fx_report.text(s)
            if len(caption) > 1000:            # подпись к фото в Telegram — до 1024 символов
                self.bot.photo(chat, f"{cur}.png", fx_report.chart_png(s))
                return self.bot.send(chat, caption, markup)
            self.bot.photo(chat, f"{cur}.png", fx_report.chart_png(s), caption=caption, markup=markup)
        except day_render.PdfUnavailable:
            self.bot.send(chat, fx_report.text(s), markup)

    def fx_file(self, chat: int, cur: str, days: int) -> None:
        s = fx_report.summary(cur, days)
        if not s:
            return
        self.bot.typing(chat, "upload_document")
        try:
            png = fx_report.chart_png(s, scale=3)
        except day_render.PdfUnavailable:
            return self.bot.send(chat, "Картинки на сервере пока недоступны (нужен Playwright).")
        self.bot.document(chat, f"Курс {cur} {s['date']:%d.%m.%Y}.png", png, mime="image/png")

    # ------------------------------------------------------------------ проверка контрагента

    def check_query(self, chat: int, text: str) -> None:
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits and len(digits) in (10, 12, 13, 15) and len(digits) >= len(text.replace(" ", "")) - 3:
            return self.check_inn(chat, digits)
        try:
            found = cp_check.suggest(text)
        except cp_check.CheckUnavailable as exc:
            return self.bot.send(chat, f"Поиск по названию сейчас недоступен: {esc(exc)}.\n"
                                       "Напишите ИНН — покажу хотя бы наши расчёты и ссылки на сервисы ФНС.")
        if not found:
            return self.bot.send(chat, f"По запросу «{esc(text)}» в ЕГРЮЛ ничего не нашлось. Попробуйте ИНН.")
        rows = [[{"text": f"{cp_check.STATUS.get(f['status'], ('❔', ''))[0]} {f['name'][:42]}"
                          + (f" · {f['city']}" if f["city"] else ""), "callback_data": f"k:{f['inn']}"}]
                for f in found]
        self.bot.send(chat, "🛡 Кого проверить?", {"inline_keyboard": rows})

    def check_inn(self, chat: int, inn: str) -> None:
        self.bot.typing(chat, "typing")
        p, note = None, ""
        try:
            found = cp_check.find(inn)
            if found:
                p = cp_check.profile(found)
            else:
                note = "В ЕГРЮЛ/ЕГРИП такого ИНН нет — проверьте цифры."
        except cp_check.CheckUnavailable as exc:
            note = f"Карточка из ЕГРЮЛ недоступна: {exc}."
            if not cp_check.api_key():
                note += " Ключ бесплатный — dadata.ru, строка DADATA_API_KEY в .env."
        our = cp_check.ours(p["inn"] if p else inn)
        links = [{"text": t, "url": u} for t, u in cp_check.links(p["inn"] if p else inn)]
        rows = [[{"text": "📋 Скопировать ИНН", "copy_text": {"text": p["inn"] if p else inn}}]]
        rows += [links[i:i + 2] for i in range(0, len(links), 2)]
        if our["n"]:
            self.cp_found[chat] = [f"inn:{p['inn'] if p else inn}"]
            rows.insert(0, [{"text": "💸 Наши оплаты подробно", "callback_data": "p:0:a"}])
        self.bot.send(chat, cp_check.text(p, our, inn, note), {"inline_keyboard": rows})
