"""
Касса → банк: сдача наличных по 1С (корр. 57.*) против взносов по банковским выпискам.

По дням: сколько списано из кассы, сколько и на какой счёт зачислил банк,
разница дня и накопленная разница («в пути»). Статус:
    ok      — сошлось
    transit — в пути не дольше TRANSIT_DAYS дней (нормально)
    bad     — висит дольше: в 1С сдано, а в банк не пришло (+) или банк
              зачислил то, чего нет в кассе 1С (−)
    nostmt  — после последней банковской выписки — сверять не с чем
"""

from __future__ import annotations

from django.contrib import admin
from django.db import DatabaseError
from django.template.response import TemplateResponse

from core.admins.base_admin import AppModelAdmin

from ..models.cash_line_model import CashTransfer
from ..services.cash_lines import TRANSIT_DAYS, cash_bank_days

STATUS = {
    "ok": ("pk-chip-ok", "сошлось"),
    "transit": ("pk-chip-warn", "в пути"),
    "bad": ("pk-chip-bad", "нет пары"),
    "nostmt": ("pk-chip-warn", "нет выписки банка"),
}


def _money(v, signed=False) -> str:
    if v is None:
        return ""
    v = float(v)
    if round(v) == 0 and not signed:
        return "0"
    text = f"{abs(v):,.0f}".replace(",", " ")
    if round(v) < 0:
        return "−" + text
    return ("+" + text) if signed and round(v) > 0 else text


@admin.register(CashTransfer)
class CashTransferAdmin(AppModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        try:
            year = int(request.GET.get("y") or 0) or None
        except ValueError:
            year = None
        problems = request.GET.get("p") == "1"
        try:
            data = cash_bank_days(year=year, problems_only=problems)
        except DatabaseError:
            data = None

        ctx = {**self.admin_site.each_context(request), "title": "Касса → банк", "opts": self.model._meta}
        if not data or not data["years"]:
            ctx["empty"] = True
            return TemplateResponse(request, "treasury/cash_transfer.html", ctx)

        if year is None:
            year = data["years"][-1]
            data = cash_bank_days(year=year, problems_only=problems)

        accounts = data["accounts"]
        rows = []
        for r in data["rows"]:
            css, label = STATUS[r["status"]]
            rows.append({
                "date": r["date"],
                "onec": _money(r["onec"]) if r["onec_n"] else "",
                "onec_n": r["onec_n"],
                "hints": r["hints"],
                "bank": [(_money(v[0]) if v else "", v[1] if v else 0) for v in r["bank"].values()],
                "diff": _money(r["diff"], signed=True) if round(float(r["diff"])) else "",
                "diff_neg": float(r["diff"]) < 0,
                "cum": _money(r["cum"], signed=True) if r["cum"] else "",
                "cum_neg": float(r["cum"]) < 0,
                "css": css,
                "label": label + (f" · {r['age']} дн." if r["status"] in ("transit", "bad") and r["age"] else ""),
            })

        t = data["totals"]
        bad = t["bad_days"]
        ctx.update({
            "years": data["years"], "year": year, "problems": problems,
            "accounts": list(accounts.values()),
            "rows": rows,
            "transit_days": TRANSIT_DAYS,
            "coverage_end": data["coverage_end"],
            "dash": {
                "hero": {
                    "kicker": f"Касса → банк · {year}",
                    "title": f"{_money(t['onec'])} ₽",
                    "accent": "сдано из кассы по 1С",
                    "sub": f"Зачислено банком {_money(t['bank'])} ₽ · счета: {', '.join(accounts.values())}"
                           + (f" · выписки по {data['coverage_end']:%d.%m.%Y}" if data["coverage_end"] else ""),
                },
                "status": {
                    "tone": "bad" if bad else "ok",
                    "icon": "report" if bad else "verified",
                    "title": (f"Без пары дольше {TRANSIT_DAYS} дней: {bad} дн." if bad
                              else "Всё сданное из кассы дошло до банка"),
                    "sub": ("Фильтр «Только расхождения» покажет эти дни." if bad else
                            f"Расхождения в пределах {TRANSIT_DAYS} дней — деньги в пути, это нормально."),
                },
                "kpis": [
                    {"label": "Сдано по 1С", "value": _money(t["onec"]), "sub": "корр. 57.01", "tone": "plain"},
                    {"label": "Зачислено банком", "value": _money(t["bank"]), "sub": "взносы по выпискам",
                     "tone": "plain"},
                    {"label": "В пути сейчас", "value": _money(data["in_transit"], signed=True),
                     "sub": "1С − банк, накопленно", "tone": "neg" if data["in_transit"] else "pos"},
                    {"label": "Дней «в пути»", "value": str(t["transit_days"]),
                     "sub": f"до {TRANSIT_DAYS} дней", "tone": "plain"},
                    {"label": "Дней без пары", "value": str(bad), "sub": f"дольше {TRANSIT_DAYS} дней",
                     "tone": "neg" if bad else "pos"},
                ],
            },
        })
        return TemplateResponse(request, "treasury/cash_transfer.html", ctx)
