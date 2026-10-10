"""
Проверка контрагента: карточка из ЕГРЮЛ/ЕГРИП (через DaData), флаги риска,
наши расчёты с ним из витрины и ссылки на официальные сервисы.

Ключ DaData — DADATA_API_KEY в .env (бесплатно до 10 000 запросов в день).
Что вернётся, зависит от тарифа: телефоны, почта, учредители, финансы —
только на платном «Максимальном»; бот показывает всё, что пришло.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone as dt_tz
from pathlib import Path

import requests
from django.db.models import Count, Max, Q, Sum

from ..models import CashFlow
from .cp_report import SKIP

FIND_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
SUGGEST_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"
OKVED_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/okved2"
CACHE_SECONDS = 12 * 3600
_cache: dict[tuple, tuple[float, object]] = {}

STATUS = {
    "ACTIVE": ("✅", "Действует"),
    "LIQUIDATING": ("⚠️", "Ликвидируется"),
    "LIQUIDATED": ("⛔", "Ликвидирована"),
    "BANKRUPT": ("⛔", "Банкротство"),
    "REORGANIZING": ("⚠️", "Реорганизуется"),
}
TAX = {"USN": "УСН", "ENVD": "ЕНВД", "ESHN": "ЕСХН", "SRP": "СРП", "AUSN": "АУСН", "PSN": "Патент", "NPD": "НПД"}


class CheckUnavailable(Exception):
    """Нет ключа или DaData не ответила."""


def api_key() -> str:
    return os.getenv("DADATA_API_KEY", "").strip()


def _post(url: str, payload: dict):
    key = (url, tuple(sorted(payload.items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    if not api_key():
        raise CheckUnavailable("нет ключа DADATA_API_KEY в .env")
    try:
        resp = requests.post(url, json=payload, timeout=15, headers={
            "Authorization": f"Token {api_key()}", "Accept": "application/json"})
    except requests.RequestException:
        raise CheckUnavailable("DaData не отвечает") from None
    if resp.status_code in (401, 403):
        raise CheckUnavailable("DaData не приняла ключ — проверьте DADATA_API_KEY")
    if resp.status_code == 429:
        raise CheckUnavailable("исчерпан лимит запросов DaData на сегодня")
    if not resp.ok:
        raise CheckUnavailable(f"DaData ответила {resp.status_code}")
    data = resp.json().get("suggestions") or []
    _cache[key] = (time.time(), data)
    return data


# ---------------------------------------------------------------- поиск

def suggest(text: str, limit: int = 8) -> list[dict]:
    """По названию (или части ИНН) — список кандидатов: ИНН, название, адрес, статус."""
    out = []
    for s in _post(SUGGEST_URL, {"query": text.strip()[:300], "count": limit}):
        d = s.get("data") or {}
        out.append({"inn": d.get("inn") or "", "name": s.get("value") or "",
                    "city": ((d.get("address") or {}).get("data") or {}).get("city") or "",
                    "status": (d.get("state") or {}).get("status") or ""})
    return [o for o in out if o["inn"]]


def find(inn: str) -> dict | None:
    """Главная организация (не филиал) по ИНН или ОГРН."""
    found = _post(FIND_URL, {"query": inn, "branch_type": "MAIN", "count": 1})
    return found[0] if found else None


def okved_name(code: str) -> str:
    """Название вида деятельности по коду ОКВЭД 2 (справочник DaData, кэш в data/.okved.json)."""
    if not code:
        return ""
    from django.conf import settings

    path = Path(settings.BASE_DIR) / "data" / ".okved.json"
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        names = {}
    if code in names:
        return names[code]
    try:
        found = _post(OKVED_URL, {"query": code})
    except CheckUnavailable:
        return ""
    name = ((found[0].get("data") or {}).get("name") or "") if found else ""
    if name:
        names[code] = name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(names, ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError:
            pass
    return name


# ---------------------------------------------------------------- разбор

def _ms(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=dt_tz.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _years(d: date | None, today: date) -> str:
    if not d:
        return ""
    months = (today.year - d.year) * 12 + today.month - d.month
    if months < 12:
        return f"{max(months, 0)} мес."
    y = months // 12
    word = "год" if y % 10 == 1 and y % 100 != 11 else ("года" if 2 <= y % 10 <= 4 and not 12 <= y % 100 <= 14 else "лет")
    return f"{y} {word}"


def _money(v) -> str:
    if v in (None, ""):
        return ""
    v = float(v)
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f}".replace(".", ",") + " млрд ₽"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}".replace(".", ",") + " млн ₽"
    return f"{v:,.0f}".replace(",", " ") + " ₽"


def profile(s: dict, today: date | None = None) -> dict:
    """Ответ DaData → плоская карточка + флаги."""
    today = today or date.today()
    d = s.get("data") or {}
    state = d.get("state") or {}
    mgmt = d.get("management") or {}
    addr = d.get("address") or {}
    is_ip = d.get("type") == "INDIVIDUAL"
    reg = _ms(state.get("registration_date"))
    liq = _ms(state.get("liquidation_date"))
    mgr_since = _ms(mgmt.get("start_date"))
    fin = d.get("finance") or {}

    flags = []
    if state.get("status") and state["status"] != "ACTIVE":
        flags.append(STATUS.get(state["status"], ("⚠️", state["status"]))[1]
                     + (f" с {liq:%d.%m.%Y}" if liq else ""))
    if reg and (today - reg).days < 365:
        flags.append(f"Молодая компания: зарегистрирована {_years(reg, today)} назад")
    if mgr_since and (today - mgr_since).days < 180:
        flags.append(f"Руководитель сменился недавно — {mgr_since:%d.%m.%Y}")
    if addr.get("invalidity") or (addr.get("data") or {}).get("qc_complete") == "INVALID":
        flags.append("Адрес отмечен ФНС как недостоверный")
    if d.get("invalid"):
        flags.append("Есть отметка о недостоверности сведений в ЕГРЮЛ")
    if fin.get("debt"):
        flags.append(f"Недоимка по налогам: {_money(fin['debt'])}")
    if fin.get("penalty"):
        flags.append(f"Пени и штрафы: {_money(fin['penalty'])}")

    def contacts(key):
        return [c.get("value") for c in d.get(key) or [] if c.get("value")]

    return {
        "name": s.get("value") or (d.get("name") or {}).get("short_with_opf") or "",
        "full_name": (d.get("name") or {}).get("full_with_opf") or "",
        "is_ip": is_ip,
        "inn": d.get("inn") or "", "kpp": d.get("kpp") or "", "ogrn": d.get("ogrn") or "",
        "okpo": d.get("okpo") or "",
        "status": state.get("status") or "", "status_icon": STATUS.get(state.get("status"), ("❔", ""))[0],
        "status_text": STATUS.get(state.get("status"), ("", state.get("status") or "нет данных"))[1],
        "registered": reg, "age": _years(reg, today), "liquidated": liq,
        "actual": _ms(state.get("actuality_date")),
        "manager": "" if is_ip else (mgmt.get("name") or ""), "manager_post": mgmt.get("post") or "",
        "manager_since": mgr_since,
        "address": addr.get("unrestricted_value") or addr.get("value") or "",
        "okved": d.get("okved") or "",
        "okved_name": "" if any(o.get("main") for o in d.get("okveds") or []) else okved_name(d.get("okved") or ""),
        "okveds": [(o.get("code"), o.get("name"), o.get("main")) for o in d.get("okveds") or []],
        "employees": d.get("employee_count"),
        "tax": TAX.get(fin.get("tax_system"), fin.get("tax_system") or ""),
        "fin_year": fin.get("year"), "income": fin.get("income"), "revenue": fin.get("revenue"),
        "expense": fin.get("expense"),
        "capital": (d.get("capital") or {}).get("value"),
        "branches": d.get("branch_count") or 0,
        "founders": [(f.get("name") or (f.get("fio") or {}).get("source") or "",
                      (f.get("share") or {}).get("value"), (f.get("share") or {}).get("type"))
                     for f in d.get("founders") or []],
        "phones": contacts("phones"), "emails": contacts("emails"), "sites": contacts("sites"),
        "licenses": len(d.get("licenses") or []),
        "flags": flags,
    }


# ---------------------------------------------------------------- наши расчёты

def ours(inn: str) -> dict:
    qs = CashFlow.objects.exclude(SKIP).filter(inn=inn)
    agg = qs.aggregate(inflow=Sum("amount_rub", filter=Q(amount_rub__gt=0), default=0),
                       outflow=Sum("amount_rub", filter=Q(amount_rub__lt=0), default=0),
                       n=Count("id"), last=Max("date"))
    top = (qs.values("cf_name", "article_name").annotate(s=Sum("amount_rub")).order_by("s")[:3])
    return {"n": agg["n"], "inflow": agg["inflow"] or 0.0, "outflow": agg["outflow"] or 0.0,
            "last": agg["last"], "articles": [t["cf_name"] or t["article_name"] or "без статьи" for t in top]}


def links(inn: str) -> list[tuple[str, str]]:
    """Сервисы ИНН из ссылки не подставляют: ИНН копируется кнопкой и вставляется в поиск."""
    return [
        ("📄 ЕГРЮЛ (выписка ФНС)", "https://egrul.nalog.ru/index.html"),
        ("📊 Отчётность ГИР БО", "https://bo.nalog.ru/"),
        ("🚫 Блокировки счетов", "https://service.nalog.ru/bi.html"),
        ("⚖️ Арбитражные дела", "https://kad.arbitr.ru/"),
        ("🔎 Rusprofile", "https://www.rusprofile.ru/"),
    ]


# ---------------------------------------------------------------- текст для бота

def _esc(t) -> str:
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rub(v: float) -> str:
    return f"{abs(v):,.0f}".replace(",", " ") + " ₽"


def text(p: dict | None, our: dict, inn: str, note: str = "") -> str:
    lines = []
    if p:
        lines += [f"🛡 <b>{_esc(p['name'])}</b>",
                  f"{p['status_icon']} {p['status_text']}"
                  + (f" · зарегистрирована {p['registered']:%d.%m.%Y} ({p['age']})" if p["registered"] else "")]
        req = [f"ИНН <code>{p['inn']}</code>"] + ([f"КПП <code>{p['kpp']}</code>"] if p["kpp"] else []) + \
              ([f"{'ОГРНИП' if p['is_ip'] else 'ОГРН'} <code>{p['ogrn']}</code>"] if p["ogrn"] else [])
        lines.append(" · ".join(req))
        if p["flags"]:
            lines += ["", "<b>Обратите внимание</b>"] + [f"⚠️ {_esc(f)}" for f in p["flags"]]
        lines.append("")
        if p["manager"]:
            post = (p["manager_post"] or "Руководитель").capitalize()
            lines.append(f"👤 {_esc(post)}: <b>{_esc(p['manager'])}</b>"
                         + (f" (с {p['manager_since']:%d.%m.%Y})" if p["manager_since"] else ""))
        if p["address"]:
            lines.append(f"📍 {_esc(p['address'])}")
        main = next(((c, n) for c, n, m in p["okveds"] if m), None)
        if main:
            lines.append(f"🏷 ОКВЭД {main[0]} — {_esc(main[1])}"
                         + (f" (+{len(p['okveds']) - 1} доп.)" if len(p["okveds"]) > 1 else ""))
        elif p["okved"]:
            lines.append(f"🏷 ОКВЭД {p['okved']}" + (f" — {_esc(p['okved_name'])}" if p["okved_name"] else ""))
        extra = []
        if p["employees"] is not None:
            extra.append(f"сотрудников: {p['employees']}")
        if p["tax"]:
            extra.append(f"налоги: {p['tax']}")
        if p["capital"]:
            extra.append(f"уставный капитал: {_money(p['capital'])}")
        if p["branches"]:
            extra.append(f"филиалов: {p['branches']}")
        if extra:
            lines.append("ℹ️ " + " · ".join(extra))
        if p["income"] or p["revenue"]:
            lines.append(f"💰 За {p['fin_year'] or 'последний'} год: доходы {_money(p['income'] or p['revenue'])}"
                         + (f", расходы {_money(p['expense'])}" if p["expense"] else ""))
        if p["founders"]:
            names = [f"{_esc(n)}" + (f" ({v:g} %)" if v and t == "PERCENT" else "") for n, v, t in p["founders"][:3]]
            lines.append("👥 Учредители: " + "; ".join(names)
                         + (f" и ещё {len(p['founders']) - 3}" if len(p["founders"]) > 3 else ""))
        for icon, key in (("☎️", "phones"), ("✉️", "emails"), ("🌐", "sites")):
            if p[key]:
                lines.append(f"{icon} " + ", ".join(_esc(v) for v in p[key][:3]))
        if not (p["phones"] or p["emails"]):
            lines.append("<i>Телефоны и почта в ЕГРЮЛ не публикуются; DaData отдаёт их на тарифе «Максимальный».</i>")
    else:
        lines.append(f"🛡 <b>ИНН <code>{inn}</code></b>")
        if note:
            lines.append(f"<i>{_esc(note)}</i>")

    lines += ["", "<b>Наши расчёты</b>"]
    if our["n"]:
        lines.append(f"Получили от него: <b>{_rub(our['inflow'])}</b> · заплатили ему: <b>{_rub(our['outflow'])}</b>")
        lines.append(f"Операций: {our['n']}, последняя — {our['last']:%d.%m.%Y}"
                     + (f" · {', '.join(_esc(a) for a in our['articles'])}" if our["articles"] else ""))
    else:
        lines.append("В наших выписках не встречается.")
    if p and p["actual"]:
        lines += ["", f"<i>Сведения ЕГРЮЛ/ЕГРИП на {p['actual']:%d.%m.%Y} (DaData).</i>"]
    lines += ["", "<i>📋 Нажмите на ИНН или кнопку «Скопировать ИНН» и вставьте его в поиск: "
                  "эти сайты не принимают ИНН в ссылке.</i>"]
    return "\n".join(lines)
