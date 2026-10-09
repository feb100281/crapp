"""
Сверка с банком — витрина dashboard_cash_check (только чтение).

Главное: конечный остаток ПОСЛЕДНЕЙ выписки по счёту против нашего расчёта
на ту же дату + пропуски между выписками. Арифметика отчёта ДДС
(начало + ДДС = конец) — справочно, в шапке.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from django.contrib import admin
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.decorators import display

from core.admins.badges import Badge
from core.reports.xlsx import FMT_QTY, Book, Col, Row

from ..models import CashBalance, CashCheck
from .common import DashboardAdmin, kpi, money, money2, money_cell, print_context, value_filter

OFF = Q(diff_cur__gte=0.01) | Q(diff_cur__lte=-0.01) | Q(gap_count__gt=0)

# тексты разрывов из sql/bs/dashboard_load.sql (dashboard_cash_check.gaps)
GAP_JOINT = re.compile(r"стык (\d\d)\.(\d\d)→(\d\d)\.(\d\d)\.(\d{4}): остаток (-?[\d.]+) → (-?[\d.]+)")
GAP_HOLE = re.compile(r"(?:нет выписок )?(\d\d\.\d\d\.\d{4})–(\d\d\.\d\d\.\d{4})$")
GAP_LINES = re.compile(r"строки ≠ итогам: (.+) \(([+-][\d.]+)\)")


def explain(gaps: str, currency: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Разрывы из витрины → (причины, что прислать) человеческим языком."""
    reasons, asks = [], []
    for part in filter(None, (gaps or "").split("; ")):
        if m := GAP_JOINT.match(part):
            d1, m1, d2, m2, year, prev_eb, bb = m.groups()
            y2 = int(year)
            y1 = y2 - 1 if int(m1) > int(m2) else y2
            day = f"{d1}.{m1}.{y1}"
            reasons.append(
                f"Остаток не продолжается между выписками: на конец {day} — {money2(float(prev_eb))} "
                f"{currency}, а на начало {d2}.{m2}.{y2} — {money2(float(bb))} {currency}. "
                f"Выписку за {day} выгрузили посреди дня, операции после выгрузки не попали."
            )
            asks.append((f"Выписка за {day} — полный день",
                         "заново, после закрытия дня; прежний файл заменим"))
        elif m := GAP_HOLE.match(part):
            d1, d2 = m.groups()
            reasons.append(f"Нет выписок за период {d1} — {d2}, а остаток за это время изменился.")
            asks.append((f"Выписки с {d1} по {d2}", ""))
        elif m := GAP_LINES.match(part):
            name, amount = m.groups()
            reasons.append(
                f"В файле «{name.rsplit('/', 1)[-1]}» сумма операций не сходится с его же итогами "
                f"на {money2(abs(float(amount)))} {currency}: в файле есть лишние или "
                f"задвоенные документы."
            )
            asks.append(("Та же выписка, выгруженная заново", f"за период файла «{name.rsplit('/', 1)[-1]}»"))
        else:
            reasons.append(part)
            asks.append(("Выписка за период, указанный в причине", ""))
    return reasons, asks


class StatusFilter(admin.SimpleListFilter):
    title = "Статус"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("off", "Расхождения"), ("ok", "Сходится")]

    def queryset(self, request, queryset):
        if self.value() == "off":
            return queryset.filter(OFF)
        if self.value() == "ok":
            return queryset.exclude(OFF)
        return queryset


@admin.register(CashCheck)
class CashCheckAdmin(DashboardAdmin):
    list_display = [
        "account_name",
        "stmt_display",
        "bank_display",
        "calc_display",
        "diff_display",
        "gaps_display",
        "status_display",
    ]
    list_display_links = None
    ordering = ["id"]
    list_per_page = 100

    list_filter = [StatusFilter, value_filter("bank_name", "Банк"), value_filter("currency", "Валюта")]

    @display(description="Последняя выписка", ordering="stmt_to")
    def stmt_display(self, obj):
        if not obj.stmt_to:
            return "—"
        return format_html('{}<div class="pk-mini-note">выписок: {}</div>',
                           obj.stmt_to.strftime("%d.%m.%Y"), obj.statements or 0)

    @display(description="Остаток по банку", ordering="bank_eb")
    def bank_display(self, obj):
        return format_html('{}<div class="pk-mini-note pk-num">{}</div>',
                           money_cell(obj.bank_eb), obj.currency or "")

    @display(description="Наш расчёт", ordering="calc_eb")
    def calc_display(self, obj):
        return money_cell(obj.calc_eb)

    @display(description="Расхождение", ordering="diff_rub")
    def diff_display(self, obj):
        if abs(obj.diff_cur or 0) < 0.01:
            return mark_safe('<span class="pk-money">0</span>')
        rub = "" if obj.currency == "RUB" else format_html(
            '<div class="pk-mini-note pk-num">{} ₽</div>', money(obj.diff_rub, signed=True))
        return format_html("{}{}", money_cell(obj.diff_cur, signed=True), rub)

    @display(description="Разрывы в выписках")
    def gaps_display(self, obj):
        if not obj.gap_count:
            return ""
        chips = format_html_join("", '<div><span class="pk-chip pk-chip-bad">{}</span></div>',
                                 ((g,) for g in (obj.gaps or "").split("; ")))
        return format_html('{}<div class="pk-mini-note">объясняет {} {}</div>',
                           chips, money(obj.gap_amount, signed=True), obj.currency or "")

    @display(description="Статус")
    def status_display(self, obj):
        if obj.ok:
            return Badge("Сходится с банком", "check_circle", "success").badge
        if obj.gap_count:
            return Badge("Разрыв в выписках", "event_busy", "danger").badge
        return Badge("Расхождение", "error", "danger").badge

    CHECK_HEADER = ["Счёт", "Банк", "Номер счёта", "Валюта", "Последняя выписка", "Выписок",
                    "Остаток по банку", "Наш расчёт", "Расхождение, вал.", "Расхождение, ₽",
                    "Разрывы в выписках", "Статус"]

    @staticmethod
    def _check_rows(queryset):
        def status(o):
            return "Сходится" if o.ok else ("Разрыв в выписках" if o.gap_count else "Расхождение")

        return [
            [o.account_name, o.bank_name or "", o.ba_number, o.currency, o.stmt_to, o.statements,
             o.bank_eb, o.calc_eb, o.diff_cur, o.diff_rub, o.gaps or "", status(o)]
            for o in queryset.order_by("id")
        ]

    def export_csv(self, request, queryset):
        return self.CHECK_HEADER, self._check_rows(queryset)

    # ------------------------------------------------------------------
    # Печать: письмо бухгалтеру — что не сходится и какие выписки прислать
    # ------------------------------------------------------------------

    def get_urls(self):
        return [
            path("print/", self.admin_site.admin_view(self.print_view), name="dashboard_cashcheck_print"),
            *super().get_urls(),
        ]

    def print_links(self, request):
        return [("Печать для бухгалтера", reverse("admin:dashboard_cashcheck_print"))]

    def print_view(self, request):
        accounts = []
        diff_total = 0.0
        for o in CashCheck.objects.filter(OFF).order_by("bank_name", "ba_number"):
            cur = o.currency or ""
            reasons, asks = explain(o.gaps, cur)
            if not reasons:
                reasons = ["Расхождение не объясняется разрывами между выписками: возможно, "
                           "не хватает выписки или в ней не все операции."]
                asks = [("Выписка за весь период по счёту", f"по {o.stmt_to:%d.%m.%Y}" if o.stmt_to else "")]
            diff_total += o.diff_rub or 0
            accounts.append({
                "bank": o.bank_name or "Банк не указан",
                "number": o.ba_number,
                # своё название счёта из плана счетов; собранное «Банк · RUB · …1234» не повторяем
                "name": o.account_name if o.account_name and "…" not in o.account_name else "",
                "currency": cur,
                "stmt_to": f"{o.stmt_to:%d.%m.%Y}" if o.stmt_to else "—",
                "bank_eb": f"{money2(o.bank_eb)} {cur}",
                "calc_eb": f"{money2(o.calc_eb)} {cur}",
                "diff": f"{money2(o.diff_cur, signed=True)} {cur}",
                "diff_neg": (o.diff_cur or 0) < 0,
                "reasons": reasons,
                "asks": asks,
            })

        # счета с остатком, по которым выписка отстаёт от последнего дня
        last = CashBalance.objects.aggregate(d=Max("date"))["d"]
        stale = []
        if last:
            for b in (CashBalance.objects.filter(date=last, stale=True)
                      .exclude(base_eb__range=(-0.005, 0.005)).order_by("bank_name", "ba_number")):
                stale.append({
                    "bank": b.bank_name or "", "number": b.ba_number, "currency": b.currency or "",
                    "stmt_to": f"{b.stmt_to:%d.%m.%Y}" if b.stmt_to else "—",
                    "from": f"{b.stmt_to + timedelta(days=1):%d.%m.%Y}" if b.stmt_to else "—",
                    "eb": money2(b.base_eb),
                })

        as_of = f"{last:%d.%m.%Y}" if last else f"{date.today():%d.%m.%Y}"
        ctx = print_context(
            request, "Сверка с банком", "Запрос выписок",
            f"Что не сходится с банком и какие выписки нужны · на {as_of}",
        )
        ctx.update({
            "accounts": accounts,
            "stale": stale,
            "as_of": as_of,
            "diff_total": money2(diff_total, signed=True),
            "diff_neg": diff_total < 0,
        })
        return render(request, "dashboard/print_check.html", ctx)

    def export_book(self, request, queryset):
        rows = self._check_rows(queryset)
        off = [r for r in rows if r[-1] != "Сходится"]
        book = Book(
            "Сверка с банком",
            "Конечный остаток последней выписки по каждому счёту против нашего расчёта",
            f"счетов: {len(rows)} · расходятся: {len(off)}",
        )
        book.kpi("Расхождение, ₽", sum(r[9] or 0 for r in rows), "расчёт минус банк")
        book.kpi("Счетов", len(rows), "в сверке", FMT_QTY)
        book.kpi("Расходятся", len(off), "с остатком банка", FMT_QTY)
        book.sheet(
            "Сверка", "Сверка с банком",
            subtitle="Расчёт = ввод остатков + строки выписок + проводки на дату последней выписки",
            description="По каждому счёту: остаток банка, наш расчёт, расхождение и пропуски выписок",
        ).table(
            [Col("Счёт", width=34), Col("Банк", width=28), Col("Номер счёта", kind="code", width=24),
             Col("Валюта", width=9), Col("Последняя выписка", kind="date", width=13),
             Col("Выписок", kind="int", width=10), Col("Остаток по банку", kind="money_dec", width=18),
             Col("Наш расчёт", kind="money_dec", width=18),
             Col("Расхождение, вал.", kind="money_dec", width=16),
             Col("Расхождение, ₽", kind="money", width=16, total=True),
             Col("Пропуски выписок", width=34, wrap=True), Col("Статус", width=22)],
            [Row(r, level=None if r[-1] == "Сходится" else "warn") for r in rows],
            note="Выделены счета с расхождением или пропусками выписок.",
        )
        return book

    def header(self, request, queryset):
        agg = queryset.aggregate(
            n=Count("id"),
            off=Count("id", filter=OFF),
            gaps=Count("id", filter=Q(gap_count__gt=0)),
            diff=Sum("diff_rub"),
            arithmetic=Sum("check_rub"),
        )
        if not agg["n"]:
            return {}

        ok = agg["off"] == 0
        dates = sorted({d for d in queryset.values_list("stmt_to", flat=True) if d})
        span = (f"{dates[0]:%d.%m.%Y} — {dates[-1]:%d.%m.%Y}" if len(dates) > 1
                else (f"{dates[0]:%d.%m.%Y}" if dates else "—"))
        arithmetic_ok = abs(agg["arithmetic"] or 0) < 1

        return {
            "hero": {
                "kicker": "Контроль",
                "title": "Сверка с банком",
                "sub": "Конечный остаток последней выписки по каждому счёту против нашего расчёта "
                       "на ту же дату, плюс разрывы в самих выписках",
            },
            "status": {
                "tone": "ok" if ok else "bad",
                "icon": "verified" if ok else "report",
                "title": (f"Все {agg['n']} счетов сходятся с банком" if ok
                          else f"Не сходятся с банком {agg['off']} из {agg['n']} счетов"),
                "sub": (f"Разрывы в выписках по {agg['gaps']} счетам: остаток не продолжается между файлами "
                         "или строки файла не бьются с его итогами — довыгрузите выписку за стык."
                        if agg["gaps"] else
                        "Разрывов в выписках нет." if ok else
                        "Разрывов нет — значит, расхождение в проводках ГК или вводе остатков."),
            },
            "kpis": [
                {"label": "Счетов", "value": str(agg["n"]), "sub": f"выписки по {span}", "tone": "plain"},
                {"label": "Расходятся", "value": str(agg["off"]), "sub": "с остатком банка",
                 "tone": "neg" if agg["off"] else "pos"},
                {"label": "С разрывами", "value": str(agg["gaps"]), "sub": "в самих выписках",
                 "tone": "neg" if agg["gaps"] else "pos"},
                kpi("Расхождение, ₽", agg["diff"], "расчёт − банк", signed=True),
                {"label": "Арифметика ДДС", "value": "ок" if arithmetic_ok else money(agg["arithmetic"]),
                 "sub": "начало + ДДС = конец", "tone": "pos" if arithmetic_ok else "neg"},
            ],
        }
