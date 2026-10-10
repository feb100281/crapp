"""
Mini App: страница внутри Telegram и её API.

Вход — по подписи Telegram (initData): страницу открывает только
пользователь бота с доступом, без пароля. Файлы не скачиваются в окне
(Telegram это плохо умеет), а приходят от бота в чат.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import date
from urllib.parse import parse_qsl

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from dashboard.admin.common import money2
from dashboard.services import cp_render, cp_report, day_render, day_report, dds_export, fx_report

from . import conf
from .api import Bot, TelegramError
from .handlers import XLSX, data_span, dds_caption
from .models import TgUser

MAX_AGE = 24 * 3600


def verify_init_data(init_data: str, token: str, max_age: int = MAX_AGE) -> dict | None:
    """Проверка подписи Telegram WebApp. Возвращает user или None."""
    if not init_data or not token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    if max_age and time.time() - int(pairs.get("auth_date", 0)) > max_age:
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None


def _user(request) -> TgUser | None:
    data = verify_init_data(request.headers.get("X-Telegram-Init-Data", ""), conf.token())
    if not data or "id" not in data:
        return None
    u = TgUser.objects.filter(tg_id=data["id"]).first()
    if u and (u.is_active or data["id"] in conf.admin_ids()):
        return u
    if u is None and data["id"] in conf.admin_ids():
        return TgUser.objects.create(tg_id=data["id"], first_name=data.get("first_name", ""),
                                     username=data.get("username", ""), note="администратор")
    return None


def _date(request) -> date | None:
    try:
        return date.fromisoformat(request.GET.get("on") or request.POST.get("on") or "")
    except ValueError:
        return None


def _deny():
    return JsonResponse({"error": "Нет доступа. Откройте приложение из бота."}, status=403)


@xframe_options_exempt
def app(request):
    return render(request, "tgbot/app.html", {})


@csrf_exempt
def api_meta(request):
    user = _user(request)
    if not user:
        return _deny()
    lo, hi = data_span()
    return JsonResponse({"name": user.first_name or user.note, "lo": lo and lo.isoformat(),
                         "hi": hi and hi.isoformat(), "dds_years": dds_export.years()[-4:][::-1]})


@csrf_exempt
def api_balances(request):
    if not _user(request):
        return _deny()
    data = day_render.balances_data(_date(request))
    if not data["on"]:
        return JsonResponse({"empty": True})
    return JsonResponse({
        "on": data["on"].isoformat(),
        "total": money2(data["total"]),
        "currencies": [{"code": c, "value": money2(v)} for c, v in data["by_currency"]],
        "banks": [{
            "bank": g["bank"], "total": money2(g["total"]), "neg": g["total"] < 0,
            "rows": [{"number": f"…{r.ba_number[-4:]}", "currency": r.currency, "eb": money2(r.base_eb),
                      "eb_rub": money2(r.eb_rub), "neg": (r.base_eb or 0) < 0, "stale": r.stale,
                      "stmt_to": r.stmt_to and r.stmt_to.strftime("%d.%m")} for r in g["rows"]],
        } for g in data["groups"]],
    })


@csrf_exempt
def api_day(request):
    if not _user(request):
        return _deny()
    on = _date(request)
    lo, hi = data_span()
    if not hi:
        return JsonResponse({"empty": True})
    on = min(max(on or hi, lo), hi)
    ctx = day_render.day_context(day_report.build(on))
    return JsonResponse({
        "on": on.isoformat(),
        "empty": ctx["empty"],
        "opening": ctx["opening_t"], "closing": ctx["closing_t"],
        "inflow": ctx["inflow_t"], "outflow": ctx["outflow_t"],
        "fx": ctx["fx_t"], "check_ok": ctx["check_ok"],
        "blocks": [{
            "title": b["title"].capitalize(), "total": b["total_t"], "neg": b["neg"], "count": b["count"],
            "articles": [{"code": a["code"], "name": a["name"], "total": a["total_t"], "neg": a["neg"],
                          "count": a["count"],
                          "rows": [{"name": c["name"], "inn": c["inn"], "sub": c["sub"], "amount": c["amount_t"],
                                    "neg": c["neg"], "count": c["count"]} for c in a["rows"]]}
                         for a in b["articles"]],
        } for b in ctx["blocks"]],
    })


@csrf_exempt
def api_cp_search(request):
    if not _user(request):
        return _deny()
    found = cp_report.search(request.GET.get("q", ""), limit=12)
    return JsonResponse({"items": [dict(f, last=f["last"].strftime("%d.%m.%Y")) for f in found]})


@csrf_exempt
def api_cp(request):
    if not _user(request):
        return _deny()
    period = request.GET.get("period", "y")
    r = cp_report.build(request.GET.get("key", ""), period if period in cp_report.PERIODS else "y")
    return JsonResponse({
        "name": r["name"], "inn": r["inn"], "period": r["period"],
        "periods": [{"code": k, "name": v} for k, v in cp_report.PERIODS.items()],
        "range": f"{r['date_from']:%d.%m.%Y} — {r['date_to']:%d.%m.%Y}" if r["date_from"] else "",
        "paid": money2(-r["outflow"]) if round(r["outflow"], 2) else "", "got": money2(r["inflow"]) if round(r["inflow"], 2) else "",
        "net": money2(r["net"], signed=True), "count": r["count"],
        "articles": [{"name": a["name"], "sum": money2(a["sum"], signed=True), "neg": a["sum"] < 0, "n": a["n"]}
                     for a in r["articles"]],
        "ops": [{"date": o["date"].strftime("%d.%m.%Y"), "sum": money2(o["amount_rub"], signed=True),
                 "neg": (o["amount_rub"] or 0) < 0, "article": o["article_name"] or o["cf_name"] or "без статьи",
                 "desc": (o["description"] or "")[:140]} for o in r["ops"][:50]],
        "more": max(len(r["ops"]) - 50, 0),
    })


@csrf_exempt
def api_fx(request):
    if not _user(request):
        return _deny()
    curs = fx_report.currencies()
    cur = request.GET.get("cur") or (curs[0] if curs else "")
    try:
        days = int(request.GET.get("days") or 90)
    except ValueError:
        days = 90
    s = fx_report.summary(cur, days) if cur in curs else None
    if not s:
        return JsonResponse({"currencies": curs, "empty": True})
    names = {1: "за день", 7: "за неделю", 30: "за 30 дней", 90: "за 90 дней", 365: "за год"}
    return JsonResponse({
        "currencies": curs, "cur": cur, "days": days, "name": s["name"], "date": s["date"].strftime("%d.%m.%Y"),
        "label": fx_report.day_label(s), "note": fx_report.next_note(s),
        "next": s["next"] and {"date": s["next"]["date"].strftime("%d.%m"), "rate": fx_report.fmt_rate(s["next"]["rate"]),
                               "diff": ("+" if s["next"]["diff"] > 0 else "") + fx_report.fmt_rate(s["next"]["diff"]),
                               "up": s["next"]["diff"] > 0},
        "rate": fx_report.fmt_rate(s["rate"]),
        "changes": [{"label": names[c["days"]], "diff": ("+" if c["diff"] > 0 else "") + fx_report.fmt_rate(c["diff"]),
                     "pct": ("+" if c["pct"] > 0 else "") + f"{c['pct']:.2f}".replace(".", ","), "up": c["diff"] > 0}
                    for c in s["changes"]],
        "tips": fx_report.conclusions(s),
        "svg": fx_report.chart_svg(s, width=720, height=380),
    })


@csrf_exempt
@require_POST
def api_send(request):
    """Прислать файл в чат: kind=bal|day|cp|dds, fmt=pdf|xlsx, on=YYYY-MM-DD, year=."""
    user = _user(request)
    if not user:
        return _deny()
    kind, fmt, on = request.POST.get("kind"), request.POST.get("fmt"), _date(request)
    bot = Bot(conf.token())
    try:
        if kind == "dds":
            year = int(request.POST.get("year") or 0) or max(dds_export.years() or [0])
            book, name, report = dds_export.book(year)
            if book is None:
                return JsonResponse({"error": "Отчёта ДДС за этот год нет"}, status=404)
            bot.document(user.tg_id, f"{name}.xlsx", book.save(), mime=XLSX,
                         caption=dds_caption(name, report, year))
        elif kind == "cp":
            r = cp_report.build(request.POST.get("key", ""), request.POST.get("period", "y"))
            name = f"Платежи {r['name'][:40]}".replace("/", " ").replace('"', "")
            if fmt == "xlsx":
                bot.document(user.tg_id, f"{name}.xlsx", cp_render.book(r).save(),
                             mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                bot.document(user.tg_id, f"{name}.pdf", cp_render.pdf(r), mime="application/pdf")
        elif kind == "bal":
            data = day_render.balances_data(on)
            bot.document(user.tg_id, f"Остатки {data['on']:%d.%m.%Y}.pdf",
                         day_render.balances_pdf(data["on"]), mime="application/pdf")
        elif kind == "day" and on:
            report = day_report.build(on)
            if fmt == "xlsx":
                bot.document(user.tg_id, f"Движение денег {on:%d.%m.%Y}.xlsx", day_render.day_book(report).save(),
                             mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                bot.document(user.tg_id, f"Движение денег {on:%d.%m.%Y}.pdf", day_render.day_pdf(report),
                             mime="application/pdf")
        else:
            return HttpResponse(status=400)
    except day_render.PdfUnavailable:
        return JsonResponse({"error": "PDF на сервере пока недоступен (нужен Playwright)"}, status=503)
    except TelegramError as exc:
        return JsonResponse({"error": f"Telegram: {exc}"}, status=502)
    return JsonResponse({"ok": True})
