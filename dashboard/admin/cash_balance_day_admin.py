"""
Остатки по дням: строка — день (сумма по счетам в ₽), раскрываешь день —
видишь счета. Связь со счетами по дате, без FK.

Фильтры банк / счёт / валюта не выкидывают дни, а пересчитывают суммы дня
по выбранным счетам (и в раскрытии показываются только они).
"""

from __future__ import annotations

import json
from collections import OrderedDict

from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path, reverse
from urllib.parse import quote, urlencode
from django.utils.html import format_html
from django.db.models import Count, F, FloatField, IntegerField, Max, Min, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from unfold.contrib.filters.admin import RangeDateFilter
from unfold.decorators import display
from unfold.sections import TableSection

from core.admins.badges import Badge
from core.reports.xlsx import Book, Col, Row

from ..models import CashBalance, CashBalanceDay, CashFlow
from .common import (DashboardAdmin, chart, kpi, money, money2, money_cell, print_context,
                     stale_status, value_filter)

ACCOUNT_FILTERS = ("bank_name", "account_name", "currency")
SUMS = ("bb_rub", "dt_rub", "cr_rub", "fx_diff_rub", "eb_rub")


def _pdf_response(make, name: str):
    """PDF файлом; если Chromium не установлен — понятная подсказка вместо 500."""
    from ..services.day_render import PdfUnavailable

    try:
        data = make()
    except PdfUnavailable as exc:
        return HttpResponse(f"PDF недоступен: {exc}", status=503, content_type="text/plain; charset=utf-8")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}.pdf"
    return response


def account_filters(request) -> dict:
    return {f: request.GET[f] for f in ACCOUNT_FILTERS if request.GET.get(f)}


def _day_sum(field, flt, agg=Sum, output=FloatField()):
    sub = (
        CashBalance.objects.filter(date=OuterRef("date"), **flt)
        .order_by()
        .values("date")
        .annotate(v=agg(field))
        .values("v")[:1]
    )
    return Coalesce(Subquery(sub, output_field=output), Value(0), output_field=output)


class AccountsSection(TableSection):
    """Счета дня — то, что видно при раскрытии строки."""

    verbose_name = "Счета"
    related_name = "day_accounts"
    fields = ["account", "stmt", "currency", "rate", "base_eb", "bb", "dt", "cr", "fx", "eb"]

    def __init__(self, request, instance):
        super().__init__(request, instance)
        instance.day_accounts = CashBalance.objects.filter(
            date=instance.date, **account_filters(request)
        ).order_by("bank_name", "account_name")

    @display(description="Счёт")
    def account(self, obj):
        """Клик — модалка с операциями счёта за день (htmx-фрагмент)."""
        url = reverse("admin:dashboard_cashbalanceday_ops", args=[obj.ba_id, obj.date.isoformat()])
        return format_html(
            '<a href="#" class="pk-ops-link" hx-get="{}" hx-target="#pk-ops-body" '
            'onclick="pkOpsOpen()">{}<span class="pk-field-sub"> · {}</span></a>',
            url, obj.account_name, obj.bank_name or "",
        )

    @display(description="Выписка по")
    def stmt(self, obj):
        if not obj.stmt_to:
            return ""
        label = obj.stmt_to.strftime("%d.%m.%Y")
        if obj.stale:
            days = (obj.date - obj.stmt_to).days
            return Badge(f"{label} · {days} дн.", "schedule", "danger",
                         title="Выписки нет — остаток перенесён").badge
        return label

    @display(description="Валюта")
    def currency(self, obj):
        return obj.currency

    @display(description="Курс")
    def rate(self, obj):
        return "" if obj.currency == "RUB" or obj.rate is None else f"{obj.rate:.4f}"

    @display(description="Остаток (вал.)")
    def base_eb(self, obj):
        return money_cell(obj.base_eb)

    @display(description="Начало, ₽")
    def bb(self, obj):
        return money_cell(obj.bb_rub)

    @display(description="Приход, ₽")
    def dt(self, obj):
        return money_cell(obj.dt_rub)

    @display(description="Расход, ₽")
    def cr(self, obj):
        return money_cell(-(obj.cr_rub or 0))

    @display(description="Курсовая, ₽")
    def fx(self, obj):
        return money_cell(obj.fx_diff_rub, signed=True)

    @display(description="Конец, ₽")
    def eb(self, obj):
        return money_cell(obj.eb_rub)


@admin.register(CashBalanceDay)
class CashBalanceDayAdmin(DashboardAdmin):
    list_display = ["date", "accounts_display", "stale_display", "bb_display", "dt_display",
                    "cr_display", "fx_display", "eb_display"]
    list_display_links = None
    ordering = ["-date"]
    list_per_page = 31
    date_hierarchy = "date"
    list_sections = [AccountsSection]

    list_filter = [
        ("date", RangeDateFilter),
        value_filter("period", "Период"),
        value_filter("bank_name", "Банк", model=CashBalance, apply=False),
        value_filter("account_name", "Счёт", model=CashBalance, apply=False),
        value_filter("currency", "Валюта", model=CashBalance, apply=False),
    ]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        flt = account_filters(request)
        if not flt:
            return qs.annotate(
                n=F("accounts"),
                v_stale=F("stale_accounts"),
                v_stale_rub=F("stale_rub"),
                **{f"v_{f}": F(f) for f in SUMS},
            )
        stale = {**flt, "stale": True}
        return qs.annotate(
            n=_day_sum("id", flt, agg=Count, output=IntegerField()),
            v_stale=_day_sum("id", stale, agg=Count, output=IntegerField()),
            v_stale_rub=_day_sum("eb_rub", stale),
            **{f"v_{f}": _day_sum(f, flt) for f in SUMS},
        ).filter(n__gt=0)

    # --- операции счёта за день (модалка) ---

    SOURCE_LABELS = {
        "BANK": ("Разнесено", "success"),
        "UNALLOC": ("Не разнесено", "warning"),
        "FEE": ("Комиссия", "info"),
        "DEBT": ("Удержание", "info"),
        "MANUAL": ("Проводка", "primary"),
        "FX": ("Курсовая", "gray"),
    }

    def get_urls(self):
        return [
            path("print/", self.admin_site.admin_view(self.print_view), name="dashboard_cashbalanceday_print"),
            path("day-report/", self.admin_site.admin_view(self.day_report_view),
                 name="dashboard_cashbalanceday_dayreport"),
            path("day/<str:date>/", self.admin_site.admin_view(self.day_view),
                 name="dashboard_cashbalanceday_day"),
            path(
                "ops/<int:ba_id>/<str:date>/",
                self.admin_site.admin_view(self.ops_view),
                name="dashboard_cashbalanceday_ops",
            ),
            *super().get_urls(),
        ]

    # ------------------------------------------------------------------
    # Страница: год → месяц → дни вместо длинного списка дней
    # ------------------------------------------------------------------

    def changelist_view(self, request, extra_context=None):
        from django.db import connection
        from django.template.response import TemplateResponse

        table = self.model._meta.db_table
        if table not in connection.introspection.table_names():
            return TemplateResponse(request, "dashboard/not_built.html", {
                **self.admin_site.each_context(request), "title": "Остатки по дням", "table": table})

        flt = account_filters(request)
        qs = self.get_queryset(request)
        years = sorted(CashBalanceDay.objects.order_by().values_list("year", flat=True).distinct())
        raw = request.GET.get("y")
        year = None if raw == "all" else (int(raw) if raw and raw.isdigit() else (years[-1] if years else None))
        scope = qs if year is None else qs.filter(year=year)

        export = request.GET.get("export")
        if export == "csv":
            from core.reports.http import csv_response
            return csv_response(*self.export_csv(request, scope), "Остатки по дням")
        if export == "xlsx":
            from core.reports.http import xlsx_response
            book = self.export_book(request, scope)
            if book:
                return xlsx_response(book, "Остатки по дням")

        rows = list(scope.order_by("date").values(
            "date", "n", "v_stale", "v_stale_rub", "v_bb_rub", "v_dt_rub", "v_cr_rub", "v_fx_diff_rub", "v_eb_rub"))

        here = {**flt, "y": "all" if year is None else year}
        ctx = {
            **self.admin_site.each_context(request),
            "title": "Остатки по дням",
            "opts": self.model._meta,
            "years": years,
            "year": year,
            "query": urlencode(flt),
            "filters": [
                {"name": f, "title": t, "value": flt.get(f, ""),
                 "values": list(CashBalance.objects.exclude(**{f"{f}__isnull": True})
                                .order_by(f).values_list(f, flat=True).distinct())}
                for f, t in (("bank_name", "Банк"), ("account_name", "Счёт"), ("currency", "Валюта"))
            ],
            "y_param": here["y"],
            "day_url": reverse("admin:dashboard_cashbalanceday_day", args=["0000-00-00"]),
            "flt_query": urlencode(flt),
        }
        if not rows:
            ctx["empty"] = True
            return TemplateResponse(request, "dashboard/balance_tree.html", ctx)

        dash = self.header(request, scope)
        dash["charts"] = self._charts(rows, flt)
        dash["exports"] = [("Excel", "?" + urlencode({**here, "export": "xlsx"})),
                           ("CSV", "?" + urlencode({**here, "export": "csv"}))]
        dash["prints"] = self.print_links(request)
        # «Счетов» — только с ненулевым остатком на конец дня
        nonzero = dict(
            CashBalance.objects.filter(date__gte=rows[0]["date"], date__lte=rows[-1]["date"], **flt)
            .exclude(base_eb__range=(-0.005, 0.005)).order_by()
            .values("date").annotate(c=Count("id")).values_list("date", "c"))
        for r in rows:
            r["n"] = nonzero.get(r["date"], 0)
        tree, open_state = self._tree(rows)
        ctx.update({"dash": dash, "tree": tree, "open_state": json.dumps(open_state)})
        return TemplateResponse(request, "dashboard/balance_tree.html", ctx)

    @staticmethod
    def _tree(rows):
        """Строки таблицы: год → месяц → день. Начало — первого дня, конец — последнего."""
        months_ru = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
                     "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

        def pack(kind, label, rid, parents, part, children=True, day=None):
            first, last = part[0], part[-1]
            return {
                "kind": kind, "label": label, "id": rid, "level": len(parents), "children": children,
                "show": " && ".join(f"open['{p}']" for p in parents) or "true",
                "bb": money_cell(first["v_bb_rub"]),
                "dt": money_cell(sum(r["v_dt_rub"] or 0 for r in part)),
                "cr": money_cell(-sum(r["v_cr_rub"] or 0 for r in part)),
                "fx": money_cell(sum(r["v_fx_diff_rub"] or 0 for r in part), signed=True),
                "eb": money_cell(last["v_eb_rub"]),
                "stale": last["v_stale"], "stale_rub": money(last["v_stale_rub"]),
                "accounts": last["n"], "day": day,
            }

        by_year = OrderedDict()
        for r in rows:
            by_year.setdefault(r["date"].year, OrderedDict()).setdefault(r["date"].month, []).append(r)

        out, open_state = [], {}
        last_year = next(reversed(by_year))
        for y, months in by_year.items():
            yid = f"y{y}"
            open_state[yid] = y == last_year
            out.append(pack("year", str(y), yid, [], [r for m in months.values() for r in m]))
            last_month = next(reversed(months))
            for m, days in months.items():
                mid = f"{yid}m{m}"
                open_state[mid] = y == last_year and m == last_month
                out.append(pack("month", f"{months_ru[m - 1]} {y}", mid, [yid], days))
                for d in days:
                    out.append(pack("day", d["date"].strftime("%d.%m.%Y"), "", [yid, mid], [d],
                                    children=False, day=d["date"].isoformat()))
        return out, open_state

    @staticmethod
    def _charts(rows, flt):
        last = rows[-1]["date"]
        balance = chart(
            [r["date"].strftime("%d.%m.%y") for r in rows],
            [{"label": "Остаток, млн ₽", "data": [round((r["v_eb_rub"] or 0) / 1e6, 2) for r in rows],
              "borderColor": "var(--color-pk-navy)", "backgroundColor": "rgba(24, 50, 74, .08)",
              "fill": True, "borderWidth": 2, "pointRadius": 0, "pointHoverRadius": 4, "tension": 0.15}],
        )
        banks = OrderedDict()
        for b in (CashBalance.objects.filter(date=last, **flt).values("bank_name")
                  .annotate(s=Sum("eb_rub")).order_by("-s")):
            if abs(b["s"] or 0) >= 1:
                banks[b["bank_name"] or "Без банка"] = round(b["s"] / 1e6, 2)
        by_bank = chart(list(banks.keys()), [{
            "label": "Остаток, млн ₽", "data": list(banks.values()),
            "backgroundColor": "var(--color-pk-blue)", "borderRadius": 3, "maxBarThickness": 22}])
        axis = {"ticks": {"color": "#9ca3af"}, "grid": {"color": "#e5e7eb88"}}
        base = {"responsive": True, "maintainAspectRatio": False,
                "plugins": {"legend": {"display": False}},
                "interaction": {"mode": "index", "intersect": False}}
        return [
            {"title": "Остаток денег на конец дня, млн ₽", "type": "line", "data": balance,
             "options": json.dumps({**base, "scales": {
                 "x": {"grid": {"display": False}, "ticks": {"color": "#9ca3af", "maxTicksLimit": 12}},
                 "y": axis}})},
            {"title": f"Остаток по банкам на {last:%d.%m.%Y}, млн ₽", "type": "bar", "data": by_bank,
             "options": json.dumps({**base, "indexAxis": "y", "scales": {
                 "x": axis, "y": {"grid": {"display": False}, "ticks": {"color": "#6b7a87"}}}})},
        ]

    def day_view(self, request, date: str):
        """Счета дня: только с остатком или движением."""
        flt = account_filters(request)
        rows = [
            a for a in CashBalance.objects.filter(date=date, **flt).order_by("bank_name", "ba_number")
            if any(abs(v or 0) >= 0.005 for v in (a.base_eb, a.base_bb, a.base_dt, a.base_cr))
        ]
        for a in rows:
            a.ops_url = reverse("admin:dashboard_cashbalanceday_ops", args=[a.ba_id, date])
            a.cells = [money_cell(a.base_eb), money_cell(a.bb_rub), money_cell(a.dt_rub),
                       money_cell(-(a.cr_rub or 0)), money_cell(a.fx_diff_rub, signed=True), money_cell(a.eb_rub)]
        total = sum(a.eb_rub or 0 for a in rows)
        report_url = reverse("admin:dashboard_cashbalanceday_dayreport") + "?" + urlencode({**flt, "on": date})
        return render(request, "dashboard/day_accounts_modal.html", {
            "report_url": report_url,
            "date": date, "rows": rows, "total": money(total),
            "stale": sum(1 for a in rows if a.stale),
        })

    # ------------------------------------------------------------------
    # Печать: остатки по счетам на дату (нулевые не показываем)
    # ------------------------------------------------------------------

    def print_links(self, request):
        flt = account_filters(request)
        url = reverse("admin:dashboard_cashbalanceday_print")
        return [("Печать остатков", f"{url}?{urlencode(flt)}" if flt else url)]

    def print_view(self, request):
        from datetime import date as date_type

        from ..services import day_render

        flt = account_filters(request)
        try:
            on = date_type.fromisoformat(request.GET.get("on") or "")
        except ValueError:
            on = None
        if request.GET.get("fmt") == "pdf":
            return _pdf_response(lambda: day_render.balances_pdf(on, flt), "Остатки")
        return HttpResponse(day_render.balances_html(on, flt, request=request))

    def day_report_view(self, request):
        """Движение денег за день: ?on=YYYY-MM-DD&fmt=html|pdf|xlsx (+ фильтры счетов)."""
        from datetime import date as date_type

        from core.reports.http import xlsx_response

        from ..services import day_render, day_report

        flt = account_filters(request)
        last = CashBalance.objects.aggregate(d=Max("date"))["d"]
        try:
            on = date_type.fromisoformat(request.GET.get("on") or "")
        except ValueError:
            on = last
        if on is None:
            return HttpResponse("Нет данных")
        report = day_report.build(on, flt)
        name = f"Движение денег {on:%d.%m.%Y}"
        fmt = request.GET.get("fmt")
        if fmt == "pdf":
            return _pdf_response(lambda: day_render.day_pdf(report), name)
        if fmt == "xlsx":
            return xlsx_response(day_render.day_book(report), name)
        base = reverse("admin:dashboard_cashbalanceday_dayreport")
        query = {**flt, "on": on.isoformat()}
        return HttpResponse(day_render.day_html(
            report, request=request, on_iso=on.isoformat(), keep=list(flt.items()),
            pdf_url=f"{base}?{urlencode({**query, 'fmt': 'pdf'})}",
            xlsx_url=f"{base}?{urlencode({**query, 'fmt': 'xlsx'})}",
        ))

    def ops_view(self, request, ba_id: int, date: str):
        acc = CashBalance.objects.filter(ba_id=ba_id, date=date).first()
        rows = list(
            CashFlow.objects.filter(ba_id=ba_id, date=date)
            .exclude(source="OPENING")
            .order_by("amount_rub")
        )
        for r in rows:
            label, style = self.SOURCE_LABELS.get(r.source, (r.source, "gray"))
            r.badge = Badge(label, None, style).badge
            r.cur_cell = money_cell(r.amount_cur, signed=True)
            r.rub_cell = money_cell(r.amount_rub, signed=True)

        inflow = sum(r.amount_rub or 0 for r in rows if (r.amount_rub or 0) > 0 and r.source != "FX")
        outflow = sum(r.amount_rub or 0 for r in rows if (r.amount_rub or 0) < 0 and r.source != "FX")
        fx = sum(r.amount_rub or 0 for r in rows if r.source == "FX")
        return render(request, "dashboard/ops_modal.html", {
            "acc": acc,
            "rows": rows,
            "is_fx": bool(acc and acc.currency and acc.currency != "RUB"),
            "kpis": [
                kpi("Остаток на начало", acc.bb_rub if acc else 0, tone="plain"),
                kpi("Поступления", inflow, tone="pos"),
                kpi("Выплаты", outflow),
                kpi("Курсовая", fx, signed=True),
                kpi("Остаток на конец", acc.eb_rub if acc else 0, tone="plain"),
            ],
        })

    # --- выгрузка ---

    DAY_HEADER = ["Дата", "Счетов", "Без свежей выписки", "Начало, ₽", "Приход, ₽",
                  "Расход, ₽", "Курсовая, ₽", "Конец, ₽"]

    @staticmethod
    def _day_rows(queryset):
        return [
            [r["date"], r["n"], r["v_stale"], r["v_bb_rub"], r["v_dt_rub"],
             -(r["v_cr_rub"] or 0), r["v_fx_diff_rub"], r["v_eb_rub"]]
            for r in queryset.order_by("date").values(
                "date", "n", "v_stale", "v_bb_rub", "v_dt_rub", "v_cr_rub", "v_fx_diff_rub", "v_eb_rub")
        ]

    def export_csv(self, request, queryset):
        return self.DAY_HEADER, self._day_rows(queryset)

    def export_book(self, request, queryset):
        days = self._day_rows(queryset)
        if not days:
            return None
        flt = account_filters(request)
        first, last = days[0], days[-1]
        scope = " · ".join(flt.values()) if flt else "все счета"
        params = (f"Российский рубль (RUB) · период: {first[0]:%d.%m.%Y} — {last[0]:%d.%m.%Y} · {scope}")

        book = Book("Остатки денежных средств", "Остатки и обороты по дням, все счета в рублях", params)
        book.kpi("Остаток на конец", last[7], f"на {last[0]:%d.%m.%Y}")
        book.kpi("Поступления", sum(d[4] or 0 for d in days), "за период")
        book.kpi("Выплаты", sum(d[5] or 0 for d in days), "за период")
        book.kpi("Курсовая разница", sum(d[6] or 0 for d in days), "переоценка валюты")

        money_col = lambda title, total=False: Col(title, kind="money", width=16, total=total)  # noqa: E731
        book.sheet(
            "Остатки по дням", "Остатки денежных средств по дням",
            subtitle="Сумма по счетам в рублях по курсу ЦБ на день",
            description="День за днём: остаток на начало, приход, расход, курсовая разница, остаток на конец",
        ).table(
            [Col("Дата", kind="date", width=12), Col("Счетов", kind="int", width=9),
             Col("Без свежей выписки", kind="int", width=12),
             money_col("Начало, ₽"), money_col("Приход, ₽"), money_col("Расход, ₽"),
             money_col("Курсовая, ₽"), money_col("Конец, ₽", True)],
            [Row(d) for d in days],
        )

        accounts = CashBalance.objects.filter(date__gte=first[0], date__lte=last[0], **flt)
        book.sheet(
            "Остатки по счетам", "Остатки по счетам на конец периода",
            subtitle=f"На {last[0]:%d.%m.%Y}: остаток в валюте счёта и в рублях",
            description="Каждый счёт на последнюю дату: валюта, курс, остаток, дата последней выписки",
        ).table(
            [Col("Счёт", width=34), Col("Банк", width=30), Col("Номер счёта", kind="code", width=24),
             Col("Валюта", width=9), Col("Курс", kind="money_dec", width=11),
             Col("Остаток, вал.", kind="money_dec", width=18), Col("Выписка по", kind="date", width=12),
             Col("Устарела", width=10), Col("Остаток, ₽", kind="money", width=18, total=True)],
            [
                Row([a.account_name, a.bank_name or "", a.ba_number, a.currency, a.rate, a.base_eb,
                     a.stmt_to, "да" if a.stale else "", a.eb_rub], level="warn" if a.stale else None)
                for a in accounts.filter(date=last[0]).order_by("bank_name", "account_name")
            ],
            total=Row(["ИТОГО", "", "", "", None, None, None, "", last[7]]),
            note="Выделены счета, по которым нет свежей выписки — остаток перенесён с последней.",
        )

        book.sheet(
            "Счета по дням", "Остатки по счетам по дням",
            subtitle="Исходные строки: счёт × день",
            description="Плоская таблица для своих сводных: счёт, день, обороты и остаток в рублях и валюте",
        ).table(
            [Col("Дата", kind="date", width=12), Col("Счёт", width=30), Col("Банк", width=26),
             Col("Валюта", width=9), Col("Курс", kind="money_dec", width=11),
             Col("Остаток, вал.", kind="money_dec", width=18),
             money_col("Начало, ₽"), money_col("Приход, ₽"), money_col("Расход, ₽"),
             money_col("Курсовая, ₽"), money_col("Конец, ₽", True)],
            (
                Row([a["date"], a["account_name"], a["bank_name"] or "", a["currency"], a["rate"],
                     a["base_eb"], a["bb_rub"], a["dt_rub"], -(a["cr_rub"] or 0), a["fx_diff_rub"],
                     a["eb_rub"]])
                for a in accounts.order_by("date", "bank_name", "account_name").values(
                    "date", "account_name", "bank_name", "currency", "rate", "base_eb",
                    "bb_rub", "dt_rub", "cr_rub", "fx_diff_rub", "eb_rub").iterator()
            ),
            autofilter=True,
        )
        return book

    # --- колонки ---

    @display(description="Счетов", ordering="n")
    def accounts_display(self, obj):
        return obj.n

    @display(description="Выписки", ordering="v_stale")
    def stale_display(self, obj):
        if not obj.v_stale:
            return ""
        return Badge(f"{obj.v_stale} без выписки", "warning", "danger",
                     title=f"Перенесённый остаток {money(obj.v_stale_rub)} ₽").badge

    @display(description="Начало, ₽", ordering="v_bb_rub")
    def bb_display(self, obj):
        return money_cell(obj.v_bb_rub)

    @display(description="Приход, ₽", ordering="v_dt_rub")
    def dt_display(self, obj):
        return money_cell(obj.v_dt_rub)

    @display(description="Расход, ₽", ordering="v_cr_rub")
    def cr_display(self, obj):
        return money_cell(-(obj.v_cr_rub or 0))

    @display(description="Курсовая, ₽", ordering="v_fx_diff_rub")
    def fx_display(self, obj):
        return money_cell(obj.v_fx_diff_rub, signed=True)

    @display(description="Конец, ₽", ordering="v_eb_rub")
    def eb_display(self, obj):
        return money_cell(obj.v_eb_rub)

    # --- шапка ---

    def header(self, request, queryset):
        rows = list(
            queryset.order_by("date").values(
                "date", "v_bb_rub", "v_dt_rub", "v_cr_rub", "v_fx_diff_rub", "v_eb_rub"
            )
        )
        if not rows:
            return {}

        first, last = rows[0], rows[-1]
        dt = sum(r["v_dt_rub"] or 0 for r in rows)
        cr = sum(r["v_cr_rub"] or 0 for r in rows)
        fx = sum(r["v_fx_diff_rub"] or 0 for r in rows)
        opening, closing = first["v_bb_rub"] or 0, last["v_eb_rub"] or 0
        change = closing - opening

        flt = account_filters(request)
        scope = " · ".join(flt.values()) if flt else "все счета"
        d1, d2 = first["date"].strftime("%d.%m.%Y"), last["date"].strftime("%d.%m.%Y")

        # остаток — по дням; обороты — по месяцам
        months: OrderedDict[str, list[float]] = OrderedDict()
        for r in rows:
            m = months.setdefault(r["date"].strftime("%m.%y"), [0.0, 0.0])
            m[0] += r["v_dt_rub"] or 0
            m[1] += r["v_cr_rub"] or 0

        status = stale_status(last["date"], flt, closing)

        return {
            "status": status,
            "hero": {
                "kicker": "Денежные средства",
                "title": f"{money(closing)} ₽",
                "accent": f"на {d2}",
                "sub": f"Период {d1} — {d2} · {scope}",
            },
            "kpis": [
                kpi("Остаток на начало", opening, f"на {d1}", tone="plain"),
                kpi("Поступления", dt, "за период", tone="pos"),
                kpi("Выплаты", -cr, "за период"),
                kpi("Курсовая разница", fx, "переоценка валюты", signed=True),
                kpi("Остаток на конец", closing,
                    f"{money(change, signed=True)} ₽ за период", tone="plain"),
            ],
            "charts": [
                {
                    "title": "Остаток денежных средств, ₽",
                    "type": "line",
                    "wide": True,
                    "data": chart(
                        [r["date"].strftime("%d.%m.%y") for r in rows],
                        [{
                            "label": "Остаток, ₽",
                            "data": [round(r["v_eb_rub"] or 0) for r in rows],
                            "borderColor": "var(--color-pk-blue)",
                            "backgroundColor": "var(--color-pk-blue)",
                            "maxTicksXLimit": 12,
                        }],
                    ),
                },
                {
                    "title": "Поступления и выплаты по месяцам, ₽",
                    "type": "bar",
                    "wide": True,
                    "legend": [("Поступления", "pk-dot-rise"), ("Выплаты", "pk-dot-fall")],
                    "data": chart(
                        list(months.keys()),
                        [
                            {"label": "Поступления", "data": [round(v[0]) for v in months.values()],
                             "backgroundColor": "var(--color-pk-rise)", "maxBarThickness": 14},
                            {"label": "Выплаты", "data": [-round(v[1]) for v in months.values()],
                             "backgroundColor": "var(--color-pk-fall)", "maxBarThickness": 14},
                        ],
                    ),
                },
            ],
        }
