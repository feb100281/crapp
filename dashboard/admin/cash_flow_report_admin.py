"""
Отчёт о движении денежных средств — сводная таблица из витрины dashboard_cash_flow.

Строки: остаток на начало → деятельность → поступления/выплаты → статья →
подстатья, «Не разнесено», курсовые, чистый поток, остаток на конец.
Колонки: месяцы, кварталы или годы — за выбранный год или за весь период.
Клик по сумме — модалка с операциями этой строки за этот период.
Отчёт и операции выгружаются в Excel и CSV.

Остаток на начало периода = все строки ДДС до начала периода (включая ввод
остатков) + ввод остатков новых счетов внутри периода. Остаток на конец =
начало + поток. Так отчёт сходится с «Остатками по дням».
"""

from __future__ import annotations

import json
from collections import defaultdict
from urllib.parse import urlencode

from django.contrib import admin
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Abs
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import path, reverse

from core.admins.badges import Badge
from core.reports.http import csv_response, xlsx_response
from core.reports.xlsx import Book, Col, Row

from ..models import CashBalanceDay, CashFlow, CpAudit
from ..services import dds_book
from .common import DashboardAdmin, chart, kpi, money, money_cell, stale_status

ACCOUNT_FILTERS = (("bank_name", "Банк"), ("account_name", "Счёт"), ("currency", "Валюта"))

MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
QUARTERS = ["I кв.", "II кв.", "III кв.", "IV кв."]

# порядок и вид групп отчёта
ACTIVITIES = [
    (1, "Операционная деятельность"),
    (2, "Инвестиционная деятельность"),
    (3, "Финансовая деятельность"),
    (4, "Внутригрупповые операции"),
    (9, "Не разнесено"),
]
FX_ACTIVITY = 5
INTRAGROUP = 4  # переводы между своими: на графике не показываем, раздувают обороты
DIRECTIONS = [(1, "Поступления"), (2, "Выплаты")]

# цвет деятельности в графике (CSS-переменные — Unfold подставит значения)
ACTIVITY_COLORS = {
    1: ("var(--color-pk-blue)", "pk-dot-a1"),
    2: ("var(--color-pk-gold)", "pk-dot-a2"),
    3: ("var(--color-pk-navy)", "pk-dot-a3"),
    4: ("var(--color-pk-sky)", "pk-dot-a4"),
    9: ("#9ca3af", "pk-dot-a9"),
}

SOURCE_LABELS = {
    "BANK": ("Разнесено", "success"),
    "UNALLOC": ("Не разнесено", "warning"),
    "FEE": ("Комиссия", "info"),
    "DEBT": ("Удержание", "info"),
    "MANUAL": ("Проводка", "primary"),
    "FX": ("Курсовая", "gray"),
}

OPS_LIMIT = 500  # строк в модалке — крупнейшие по модулю

AUDIT_FIELDS = ("line_id", "direction", "inn", "cp_name", "cf_code", "manual", "is_open",
                "rule_resolver_id", "rule_kind", "kbk_resolver_id", "ba_resolver_id", "cp_resolver_id")


def _attach_links(rows) -> None:
    """Переходы из операции ДДС: сама операция, резолвер, который её разнёс,
    и резолверы, где можно завести правило. Связи берём из витрины dashboard_cp_audit."""
    from .cp_audit_admin import pin_url, resolver_url

    ids = {r.line_id for r in rows if r.line_id}
    audit: dict[int, list[dict]] = defaultdict(list)
    for a in CpAudit.objects.filter(line_id__in=ids).values(*AUDIT_FIELDS):
        audit[a["line_id"]].append(a)

    for r in rows:
        r.links, r.how = [], ""
        if not r.line_id:
            continue
        r.links.append(("Операция", reverse("admin:treasury_bsline_change", args=[r.line_id]),
                        "Открыть операцию: разноска руками"))
        found = audit.get(r.line_id)
        if not found:
            continue
        a = next((x for x in found if x["cf_code"] == r.cf_code), found[0])
        inn, name = a["inn"] or "", a["cp_name"] or ""
        ref = {"find": name, "inn": inn}

        if a["rule_resolver_id"]:
            r.how = "резолвер"
            r.links.append((f"Разнёс: {KIND_LABELS.get(a['rule_kind'], a['rule_kind'])}",
                            resolver_url(a["rule_kind"], a["rule_resolver_id"], **ref),
                            "Резолвер, правило которого разнесло операцию"))
        elif a["manual"]:
            r.how = "руками"
        elif a["is_open"]:
            r.how = "не разнесено"

        for kind, key in (("KBK", "kbk_resolver_id"), ("BA", "ba_resolver_id"), ("CP", "cp_resolver_id")):
            if a[key] and a[key] != a["rule_resolver_id"]:
                r.links.append((KIND_LABELS[kind], resolver_url(kind, a[key], **ref),
                                "Открыть резолвер и поправить правило"))
        if not a["cp_resolver_id"] and (inn or name):
            r.links.append(("+ Контрагент", pin_url(inn, name, a["direction"]),
                            "Завести резолвер контрагента — он важнее КБК и счёта"))


KIND_LABELS = {"KBK": "КБК", "BA": "Счёт", "CP": "Контрагент", "IC": "Свои счета"}

ALL = "all"      # ?y=all — весь период
MODES = (("m", "Месяцы"), ("q", "Кварталы"), ("y", "Годы"))

# вид строки отчёта → уровень заливки в Excel
XLSX_LEVELS = {"balance": "band", "activity": 1, "unalloc": "warn", "direction": 2,
               "article": 3, "fx": 2, "net": "total"}
# в Excel открыто: деятельность → направление → статья; подстатьи — под плюсиком
XLSX_OPEN_LEVELS = 3


def _open_months(request) -> set[str]:
    """Месяцы, развёрнутые по дням: ?d=2026-03,2026-04."""
    out = set()
    for part in (request.GET.get("d") or "").split(","):
        y, _, m = part.partition("-")
        if y.isdigit() and m.isdigit() and 1 <= int(m) <= 12:
            out.add(f"{int(y)}-{int(m):02d}")
    return out


def _filters(request) -> dict:
    return {f: request.GET[f] for f, _ in ACCOUNT_FILTERS if request.GET.get(f)}


# Колонка отчёта — «корзина»: (год, месяц), (год, квартал) или (год,).
# В адресе: 2025-03, 2025-Q1, 2025.

def _bucket_of(mode: str, year: int, month: int) -> tuple:
    if mode == "m":
        return (year, month)
    if mode == "q":
        return (year, (month - 1) // 3 + 1)
    return (year,)


def _bucket_range(mode: str, first: tuple, last: tuple) -> list[tuple]:
    """Все корзины от first до last подряд — пустые месяцы тоже колонки."""
    if mode == "y":
        return [(y,) for y in range(first[0], last[0] + 1)]
    size = 12 if mode == "m" else 4
    out, (y, i) = [], first
    while (y, i) <= last:
        out.append((y, i))
        y, i = (y, i + 1) if i < size else (y + 1, 1)
    return out


def _bucket_label(mode: str, bucket: tuple, with_year: bool) -> str:
    if mode == "m":
        return f"{MONTHS[bucket[1] - 1]} {bucket[0] % 100:02d}"
    if mode == "q":
        return f"{QUARTERS[bucket[1] - 1]} {bucket[0] % 100:02d}" if with_year else QUARTERS[bucket[1] - 1]
    return str(bucket[0])


def _bucket_code(mode: str, bucket: tuple) -> str:
    if mode == "m":
        return f"{bucket[0]}-{bucket[1]:02d}"
    if mode == "q":
        return f"{bucket[0]}-Q{bucket[1]}"
    return str(bucket[0])


def _bucket_filter(code: str) -> dict:
    """Код корзины из адреса → фильтр по витрине."""
    if code.count("-") == 2:
        return {"date": code}
    year, _, part = code.partition("-")
    flt = {"year": int(year)}
    if part.startswith("Q"):
        q = int(part[1:])
        flt["month__in"] = [3 * q - 2, 3 * q - 1, 3 * q]
    elif part:
        flt["month"] = int(part)
    return flt


def _bucket_title(code: str) -> str:
    if code.count("-") == 2:
        y, m, d = code.split("-")
        return f"{d}.{m}.{y}"
    year, _, part = code.partition("-")
    if part.startswith("Q"):
        return f"{QUARTERS[int(part[1:]) - 1]} {year}"
    if part:
        return f"{MONTHS[int(part) - 1]} {year}"
    return f"{year} год"


@admin.register(CashFlow)
class CashFlowReportAdmin(DashboardAdmin):
    """Вместо списка строк — сводный отчёт ДДС."""

    def get_urls(self):
        return [
            path("ops/", self.admin_site.admin_view(self.ops_view), name="dashboard_cashflow_ops"),
            *super().get_urls(),
        ]

    # ------------------------------------------------------------------
    # Отчёт
    # ------------------------------------------------------------------

    def changelist_view(self, request, extra_context=None):
        from django.db import connection

        if self.model._meta.db_table not in connection.introspection.table_names():
            return TemplateResponse(request, "dashboard/not_built.html", {
                **self.admin_site.each_context(request),
                "title": "Отчёт ДДС",
                "table": self.model._meta.db_table,
            })

        flt = _filters(request)
        base = CashFlow.objects.filter(**flt)

        years = sorted(base.order_by().values_list("year", flat=True).distinct())
        if not years:
            years = sorted(CashFlow.objects.order_by().values_list("year", flat=True).distinct())

        # год: число или «весь период»
        raw = request.GET.get("y")
        if raw == ALL:
            year = None
        else:
            try:
                year = int(raw or years[-1])
            except (ValueError, IndexError):
                year = years[-1] if years else 0
        y_param = ALL if year is None else year

        mode = request.GET.get("mode")
        if mode not in dict(MODES):
            mode = "y" if year is None else "m"

        days = _open_months(request) if mode == "m" else set()
        report = self._report(request, base, flt, year, mode, days)

        export = request.GET.get("export")
        if export and not report.get("empty"):
            name = "Отчёт ДДС " + report["period_name"]
            if export == "csv":
                return csv_response(*self._csv(report), name)
            if export == "xlsx":
                return xlsx_response(self._book(request, base, flt, year, report), name)

        ctx = {
            **self.admin_site.each_context(request),
            "title": "Отчёт о движении денежных средств",
            "opts": self.model._meta,
            "years": years,
            "year": year,
            "all_years": year is None,
            "y_param": y_param,
            "mode": mode,
            "modes": MODES,
            "filters": self._filter_bar(request, flt),
            "query": urlencode({**flt, "mode": mode}),
            "query_y": urlencode({**flt, "y": y_param}),
            "query_full": urlencode({**flt, "y": y_param, "mode": mode}),
            "days_open": bool(days),
            "days_reset": "?" + urlencode({**flt, "y": y_param, "mode": mode}) + "#dds",
            "focus": request.GET.get("f", ""),
        }
        ctx.update(report)
        return TemplateResponse(request, "dashboard/cash_flow_report.html", ctx)

    def _filter_bar(self, request, flt):
        bar = []
        for field, title in ACCOUNT_FILTERS:
            values = (
                CashFlow.objects.exclude(**{f"{field}__isnull": True})
                .order_by(field).values_list(field, flat=True).distinct()
            )
            bar.append({"name": field, "title": title, "values": list(values), "value": flt.get(field, "")})
        return bar

    def _report(self, request, base, flt, year, mode, days=frozenset()):
        scope = base if year is None else base.filter(year=year)
        periods = sorted(set(scope.order_by().values_list("year", "month").distinct()))
        if not periods:
            return {"empty": True}

        # колонки: корзины (месяц / квартал / год); развёрнутый месяц — ещё и его дни
        buckets = _bucket_range(mode, _bucket_of(mode, *periods[0]), _bucket_of(mode, *periods[-1]))
        labels = [_bucket_label(mode, b, with_year=year is None) for b in buckets]
        flows = scope.exclude(source="OPENING")
        day_list = defaultdict(list)
        if days:
            for d in (flows.filter(period__in=days).order_by("date")
                      .values_list("date", flat=True).distinct()):
                day_list[(d.year, d.month)].append(d)

        here = {**flt, "y": ALL if year is None else year, "mode": mode}
        columns, col, day_col = [], {}, {}
        for b, label in zip(buckets, labels):
            code = _bucket_code(mode, b)
            is_open = mode == "m" and code in days
            toggle = ""
            if mode == "m":
                rest = sorted(days ^ {code})
                toggle = ("?" + urlencode({**here, **({"d": ",".join(rest)} if rest else {}), "f": code})
                          + "#dds")
            col[b] = len(columns)
            columns.append({"kind": "month" if mode == "m" else "bucket", "label": label, "code": code,
                            "open": is_open, "toggle": toggle, "bucket": b})
            for d in day_list.get(b, ()) if is_open else ():
                day_col[d] = len(columns)
                columns.append({"kind": "day", "label": d.strftime("%d.%m"), "code": d.isoformat()})
        n = len(columns)
        main = [i for i, c in enumerate(columns) if c["kind"] != "day"]

        def index(row) -> int | None:
            return col.get(_bucket_of(mode, row["year"], row["month"]))

        def vec():
            return [0.0] * n

        # потоки (без ввода остатков)
        data: dict[tuple, list[float]] = defaultdict(vec)
        names: dict[tuple, tuple[str, str]] = {}
        rows = (
            scope.exclude(source="OPENING")
            .values("activity", "direction", "article_code", "article_name", "cf_code", "cf_name",
                    "year", "month")
            .annotate(s=Sum("amount_rub"))
            .order_by()
        )
        def put(r, i):
            v = r["s"] or 0
            act, d = r["activity"], r["direction"]
            data[("A", act)][i] += v
            if act == FX_ACTIVITY:
                return
            data[("D", act, d)][i] += v
            if act == 9:
                return
            art = r["article_code"] or r["cf_code"] or "—"
            data[("R", act, d, art)][i] += v
            names[("R", act, d, art)] = (art, r["article_name"] or r["cf_name"] or "Без статьи")
            if r["cf_code"] and r["cf_code"] != art:
                key = ("S", act, d, art, r["cf_code"])
                data[key][i] += v
                names[key] = (r["cf_code"], r["cf_name"] or "")

        for r in rows:
            i = index(r)
            if i is not None:
                put(r, i)

        if day_col:
            for r in (flows.filter(period__in=days)
                      .values("activity", "direction", "article_code", "article_name", "cf_code",
                              "cf_name", "date")
                      .annotate(s=Sum("amount_rub")).order_by()):
                i = day_col.get(r["date"])
                if i is not None:
                    put(r, i)

        # остатки: до начала периода + ввод остатков новых счетов в периоде
        before = 0
        if year is not None:
            before = base.filter(year__lt=year).aggregate(s=Sum("amount_rub"))["s"] or 0
        opening_in = defaultdict(float)
        for r in (scope.filter(source="OPENING").values("year", "month", "date")
                  .annotate(s=Sum("amount_rub")).order_by()):
            i = index(r)
            if i is not None:
                opening_in[i] += r["s"] or 0
            j = day_col.get(r["date"])
            if j is not None:
                opening_in[j] += r["s"] or 0

        net = vec()
        for (kind, *rest), v in data.items():
            if kind == "A":
                for i in range(n):
                    net[i] += v[i]
        opening, closing = vec(), vec()
        running = before
        day_running = before
        for i, c in enumerate(columns):
            if c["kind"] == "day":
                # день: от остатка на конец прошлого месяца, нарастающим итогом внутри месяца
                opening[i] = day_running + opening_in[i]
                closing[i] = opening[i] + net[i]
                day_running = closing[i]
                continue
            day_running = running
            opening[i] = running + opening_in[i]
            closing[i] = opening[i] + net[i]
            running = closing[i]

        def main_only(values):
            return [values[i] for i in main]

        first_col, last_col = main[0], main[-1]

        # --- строки таблицы ---
        table, open_state = [], {}

        def cells(values, drill: dict | None, total=None):
            out = []
            for i, v in enumerate(values):
                url = (self._ops_url(flt, year, columns[i]["code"], drill)
                       if drill and round(v) else "")
                out.append({"v": money(v) if round(v) else "", "neg": round(v) < 0, "url": url,
                            "day": columns[i]["kind"] == "day", "open": columns[i].get("open", False)})
            t = sum(main_only(values)) if total is None else total
            url = self._ops_url(flt, year, "", drill) if drill and round(t) else ""
            return out, {"v": money(t) if round(t) else "", "neg": round(t) < 0, "url": url}

        def add(kind, label, values, *, code="", rid="", parents=(), drill=None, total=None, children=False):
            c, t = cells(values, drill, total)
            table.append({
                "kind": kind, "label": label, "code": code, "id": rid,
                "show": " && ".join(f"open['{p}']" for p in parents) or "true",
                "level": len(parents), "cells": c, "total": t, "children": children,
                # числа как есть — для выгрузок (без дней); all_values — с днями
                "values": main_only(values), "all_values": list(values),
                "total_value": sum(main_only(values)) if total is None else total,
            })

        add("balance", "Остаток на начало", opening, total=opening[first_col])

        for act, act_name in ACTIVITIES:
            a = data.get(("A", act))
            if not a or not any(round(x) for x in a):
                continue
            aid = f"a{act}"
            open_state[aid] = True
            add("unalloc" if act == 9 else "activity", act_name, a, rid=aid,
                drill={"act": act}, children=True)
            for d, d_name in DIRECTIONS:
                dv = data.get(("D", act, d))
                if not dv or not any(round(x) for x in dv):
                    continue
                did = f"{aid}d{d}"
                arts = sorted(k for k in data if k[0] == "R" and k[1] == act and k[2] == d)
                open_state[did] = True
                add("direction", d_name, dv, rid=did, parents=(aid,),
                    drill={"act": act, "dir": d}, children=bool(arts))
                for key in arts:
                    art = key[3]
                    rid = f"{did}r{art}"
                    subs = sorted(k for k in data if k[0] == "S" and k[1:4] == key[1:4])
                    open_state[rid] = False
                    code, name = names[key]
                    add("article", name, data[key], code=code, rid=rid, parents=(aid, did),
                        drill={"act": act, "dir": d, "art": art}, children=bool(subs))
                    for sk in subs:
                        code, name = names[sk]
                        add("sub", name, data[sk], code=code, parents=(aid, did, rid),
                            drill={"act": act, "dir": d, "art": art, "cf": sk[4]})

        fx = data.get(("A", FX_ACTIVITY))
        if fx and any(round(x) for x in fx):
            add("fx", "Курсовые разницы", fx, drill={"act": FX_ACTIVITY})

        add("net", "Чистый денежный поток", net)
        add("balance", "Остаток на конец", closing, total=closing[last_col])

        # --- шапка ---
        inflow = sum(sum(main_only(v)) for k, v in data.items() if k[0] == "D" and k[2] == 1)
        outflow = sum(sum(main_only(v)) for k, v in data.items() if k[0] == "D" and k[2] == 2)
        unalloc = sum(abs(sum(main_only(v))) for k, v in data.items() if k[0] == "D" and k[1] == 9)
        turnover = abs(inflow) + abs(outflow)
        operating = sum(main_only(data.get(("A", 1), vec())))
        total_net = sum(main_only(net))
        opening_total, closing_total = opening[first_col], closing[last_col]

        period = f"{labels[0]} — {labels[-1]}" if n > 1 else labels[0]
        last_year = buckets[-1][0]
        period_name = (f"{buckets[0][0]}–{last_year}" if buckets[0][0] != last_year else str(last_year))
        last_day = (CashBalanceDay.objects.filter(date__lte=f"{last_year}-12-31")
                    .aggregate(d=Max("date"))["d"])

        # графики — по колонкам-периодам (без дней), в млн ₽, одна ось на график
        def mln(values):
            return [round(v / 1e6, 2) for v in values]

        inflow_v, outflow_v = vec(), vec()
        for k, v in data.items():
            if k[0] == "D" and k[1] != INTRAGROUP:
                target = inflow_v if k[2] == 1 else outflow_v
                for i in range(n):
                    target[i] += v[i]
        main_labels = [columns[i]["label"] for i in main]
        flow_chart = chart(main_labels, [
            {"type": "bar", "label": "Поступления", "data": mln(main_only(inflow_v)),
             "backgroundColor": "var(--color-pk-blue)", "stack": "f", "maxBarThickness": 22,
             "borderRadius": 3, "order": 2},
            {"type": "bar", "label": "Выплаты", "data": mln(main_only(outflow_v)),
             "backgroundColor": "var(--color-pk-gold)", "stack": "f", "maxBarThickness": 22,
             "borderRadius": 3, "order": 2},
            {"type": "line", "label": "Чистый поток", "data": mln(main_only(net)),
             "borderColor": "var(--color-pk-navy)", "backgroundColor": "var(--color-pk-navy)",
             "borderWidth": 2, "pointRadius": 3, "pointHoverRadius": 5, "tension": 0, "order": 1},
        ])
        balance_chart = chart(main_labels, [
            {"type": "line", "label": "Остаток на конец", "data": mln(main_only(closing)),
             "borderColor": "var(--color-pk-navy)", "backgroundColor": "rgba(24, 50, 74, .08)",
             "fill": True, "borderWidth": 2, "pointRadius": 3, "pointHoverRadius": 5, "tension": 0.2},
        ])
        axis = {"ticks": {"color": "#9ca3af"}, "grid": {"color": "#e5e7eb88"},
                "title": {"display": True, "text": "млн ₽", "color": "#9ca3af"}}
        base_opts = {
            "responsive": True,
            "maintainAspectRatio": False,
            "interaction": {"mode": "index", "intersect": False},
            "plugins": {"legend": {"display": False}},
        }
        flow_opts = {**base_opts, "scales": {
            "x": {"stacked": True, "grid": {"display": False}, "ticks": {"color": "#9ca3af"}},
            "y": {**axis, "stacked": True},
        }}
        balance_opts = {**base_opts, "scales": {
            "x": {"grid": {"display": False}, "ticks": {"color": "#9ca3af"}},
            "y": {**axis, "beginAtZero": True},
        }}

        dash = {
            "hero": {
                "kicker": f"Движение денежных средств · {'весь период ' if year is None else ''}{period_name}",
                "title": f"{money(total_net, signed=True)} ₽",
                "accent": "чистый поток",
                "sub": f"{period} · {' · '.join(flt.values()) or 'все счета'} · "
                       f"остаток {money(opening_total)} → {money(closing_total)} ₽",
            },
            "kpis": [
                kpi("Поступления", inflow, "все деятельности", tone="pos"),
                kpi("Выплаты", outflow, "все деятельности"),
                kpi("Операционный поток", operating, "чистый", signed=True),
                {"label": "Не разнесено", "value": f"{100 * unalloc / turnover:.1f} %" if turnover else "—",
                 "sub": f"{money(unalloc)} ₽ оборота", "tone": "neg" if unalloc else "plain"},
                kpi("Остаток на конец", closing_total, f"на конец {labels[-1]}", tone="plain"),
            ],
            "charts": [
                {
                    "title": "Поступления, выплаты и чистый поток, млн ₽ · без внутригрупповых",
                    "type": "bar",
                    "legend": [("Поступления", "pk-dot-a1"), ("Выплаты", "pk-dot-a2"),
                               ("Чистый поток", "pk-dot-a3")],
                    "data": flow_chart,
                    "options": json.dumps(flow_opts),
                },
                {
                    "title": "Остаток денег на конец периода, млн ₽",
                    "type": "line",
                    "legend": [],
                    "data": balance_chart,
                    "options": json.dumps(balance_opts),
                },
            ],
        }
        if last_day:
            dash["status"] = stale_status(last_day, flt, closing_total)

        return {
            "mode": mode,
            "labels": labels,
            "columns": columns,
            "table": table,
            "open_state": json.dumps(open_state),
            "dash": dash,
            "period": period,
            "period_name": period_name,
            "totals": {"net": total_net, "inflow": inflow, "outflow": outflow,
                       "opening": opening_total, "closing": closing_total},
        }

    # ------------------------------------------------------------------
    # Выгрузки
    # ------------------------------------------------------------------

    @staticmethod
    def _csv(report):
        header = ["Код", "Статья", "Уровень", *report["labels"], "Итого"]
        rows = [
            [r["code"], r["label"], r["level"], *[round(v, 2) for v in r["values"]],
             round(r["total_value"], 2)]
            for r in report["table"]
        ]
        return header, rows

    def _book(self, request, base, flt, year, report) -> Book:
        scope_text = " · ".join(flt.values()) or "все счета"
        params = f"Российский рубль (RUB) · период: {report['period']} · {scope_text}"
        totals = report["totals"]
        note = report["period"]

        book = Book("Отчёт о движении денежных средств",
                    "Прямой метод, по видам деятельности. Суммы в рублях по курсу ЦБ на день операции",
                    params)
        book.kpi("Чистый денежный поток", totals["net"], note)
        book.kpi("Поступления", totals["inflow"], note)
        book.kpi("Выплаты", totals["outflow"], note)
        book.kpi("Остаток на конец", totals["closing"], f"на конец: {report['labels'][-1]}")

        # --- сводный отчёт; помесячно — с днями под плюсиками над колонками
        if report.get("mode") == "m":
            months = {c["code"] for c in report["columns"] if c["kind"] == "month"}
            report = self._report(request, base, flt, year, "m", months)
        has_days = any(c["kind"] == "day" for c in report["columns"])
        period_cols = [
            Col(c["label"], kind="money", width=11, outline=1, hidden=True) if c["kind"] == "day"
            else Col(c["label"], kind="money", width=15,
                     collapsed=has_days and c["kind"] == "month")
            for c in report["columns"]
        ]
        table = report["table"]
        book.sheet(
            "ДДС", "Отчёт о движении денежных средств",
            subtitle=("Деятельность → поступления / выплаты → статья → подстатья. "
                      + ("Плюсик над месяцем раскрывает дни" if has_days else "")).strip(),
            description="Сводный отчёт: статьи по строкам, периоды по колонкам, остатки на начало и конец",
        ).table(
            [Col("Код", kind="code", width=10), Col("Статья", width=50, indent=True),
             *period_cols,
             Col("Итого", kind="money", width=17, total=True)],
            [
                Row([r["code"], r["label"].upper() if r["kind"] == "balance" else r["label"],
                     *r["all_values"], r["total_value"]],
                    level=XLSX_LEVELS.get(r["kind"]),
                    outline=r["level"], indent=2 * r["level"],
                    hidden=r["level"] >= XLSX_OPEN_LEVELS,
                    collapsed=r["children"] and r["level"] + 1 >= XLSX_OPEN_LEVELS)
                for r in table[:-1]
            ],
            total=Row([table[-1]["code"], table[-1]["label"].upper(), *table[-1]["all_values"],
                       table[-1]["total_value"]], level="band"),
            freeze_cols=2,
            freeze_rows=1,  # остаток на начало виден при прокрутке
        )

        scope = base if year is None else base.filter(year=year)
        flows = scope.exclude(source="OPENING")

        # --- расшифровка, контрагенты, карточка, операции
        dds_book.add_sheets(book, flows, year)
        return book

    # ------------------------------------------------------------------
    # Модалка: операции строки отчёта за период
    # ------------------------------------------------------------------

    def _ops_url(self, flt, year, bucket: str, drill):
        params = {**flt, "y": ALL if year is None else year, "b": bucket, **drill}
        return reverse("admin:dashboard_cashflow_ops") + "?" + urlencode(params)

    def _ops_by_cp(self, request, qs, period, here):
        """Та же выборка, свёрнутая по контрагентам."""
        groups = list(
            qs.values("inn", "cp_name")
            .annotate(n=Count("id"), s=Sum("amount_rub"), last=Max("date"),
                      inflow=Sum("amount_rub", filter=Q(amount_rub__gt=0), default=0),
                      outflow=Sum("amount_rub", filter=Q(amount_rub__lt=0), default=0))
            .order_by()
        )
        groups.sort(key=lambda x: -abs(x["s"] or 0))

        if request.GET.get("export") == "csv":
            return csv_response(
                ["Контрагент", "ИНН", "Операций", "Поступления, ₽", "Выплаты, ₽", "Итого, ₽", "Последняя"],
                ([x["cp_name"] or "", x["inn"] or "", x["n"], round(x["inflow"], 2),
                  round(x["outflow"], 2), round(x["s"] or 0, 2), x["last"]] for x in groups),
                f"Контрагенты ДДС {period}",
            )

        total = sum(x["s"] or 0 for x in groups)
        rows = groups[:OPS_LIMIT]
        for x in rows:
            x["name"] = x["cp_name"] or "Без контрагента"
            x["sum_cell"] = money_cell(x["s"], signed=True)
            x["in_cell"] = money_cell(x["inflow"]) if x["inflow"] else ""
            x["out_cell"] = money_cell(x["outflow"], signed=True) if x["outflow"] else ""
            x["share"] = f"{100 * abs(x['s'] or 0) / abs(total):.1f} %" if total else ""
            x["url"] = here(cpk=f"{x['inn'] or ''}|{x['cp_name'] or ''}")

        first = qs.first()
        g = request.GET
        if first and g.get("cf"):
            title = f"{first.cf_code} {first.cf_name}"
        elif first and g.get("art"):
            title = f"{first.article_code or ''} {first.article_name or 'Без статьи'}".strip()
        elif first:
            title = first.activity_name + (" · " + first.direction_name if g.get("dir") else "")
        else:
            title = "Контрагенты"

        return render(request, "dashboard/cf_ops_modal.html", {
            "view": "cp",
            "ops_url": here(),
            "cp_url": here(v="cp"),
            "title": title,
            "period": period,
            "scope": " · ".join(_filters(request).values()) or "все счета",
            "cps": rows,
            "count": len(groups),
            "limited": len(groups) > OPS_LIMIT,
            "limit": OPS_LIMIT,
            "csv_url": here(v="cp", export="csv"),
            "kpis": [
                {"label": "Контрагентов", "value": f"{len(groups):,}".replace(",", " "), "tone": "plain"},
                {"label": "Операций", "value": f"{sum(x['n'] for x in groups):,}".replace(",", " "),
                 "tone": "plain"},
                kpi("Итого", total, "", signed=True),
            ],
        })

    def ops_view(self, request):
        g = request.GET
        bucket = g.get("b") or ""
        qs = CashFlow.objects.filter(**_filters(request)).exclude(source="OPENING")
        if bucket:
            qs = qs.filter(**_bucket_filter(bucket))
        elif g.get("y") != ALL:
            qs = qs.filter(year=int(g["y"]))
        if g.get("act"):
            qs = qs.filter(activity=int(g["act"]))
        if g.get("dir"):
            qs = qs.filter(direction=int(g["dir"]))
        if g.get("art"):
            art = g["art"]
            qs = qs.filter(article_code=art) if art != "—" else qs.filter(article_code__isnull=True)
        if g.get("cf"):
            qs = qs.filter(cf_code=g["cf"])

        if bucket:
            period = _bucket_title(bucket)
        else:
            period = "весь период" if g.get("y") == ALL else f"{g['y']} год"

        # переключатель «Операции / Контрагенты» и отбор по одному контрагенту
        view = "cp" if g.get("v") == "cp" else "ops"
        cp_key = g.get("cpk")
        cp_label = ""
        if cp_key is not None:
            inn, _, name = cp_key.partition("|")
            qs = qs.filter(inn=inn or None, cp_name=name or None)
            cp_label = name or inn or "Без контрагента"
            view = "ops"

        def here(**changes):
            q = g.copy()
            for k in ("v", "cpk", "export"):
                q.pop(k, None)
            for k, v in changes.items():
                if v is not None:
                    q[k] = v
            return reverse("admin:dashboard_cashflow_ops") + "?" + q.urlencode()

        if view == "cp":
            return self._ops_by_cp(request, qs, period, here)

        if g.get("export") == "csv":
            return csv_response(
                ["Дата", "Счёт", "Банк", "Валюта", "Тип", "Деятельность", "Направление", "Код",
                 "Статья", "Подстатья", "Контрагент", "ИНН", "Назначение платежа",
                 "Сумма, вал.", "Курс", "Сумма, ₽"],
                (
                    [o.date, o.account_name, o.bank_name, o.currency,
                     SOURCE_LABELS.get(o.source, (o.source,))[0], o.activity_name, o.direction_name,
                     o.cf_code, o.article_name, o.cf_name, o.cp_name, o.inn, o.description,
                     o.amount_cur, o.rate, o.amount_rub]
                    for o in qs.order_by("date", "id").iterator()
                ),
                f"Операции ДДС {period}",
            )

        agg = qs.aggregate(s=Sum("amount_rub"))
        count = qs.count()
        inflow = qs.filter(amount_rub__gt=0).aggregate(s=Sum("amount_rub"))["s"] or 0
        outflow = qs.filter(amount_rub__lt=0).aggregate(s=Sum("amount_rub"))["s"] or 0

        rows = list(qs.annotate(a=Abs("amount_rub")).order_by("-a")[:OPS_LIMIT])
        rows.sort(key=lambda r: (r.date, r.amount_rub or 0))
        for r in rows:
            label, style = SOURCE_LABELS.get(r.source, (r.source, "gray"))
            r.badge = Badge(label, None, style).badge
            r.rub_cell = money_cell(r.amount_rub, signed=True)
        _attach_links(rows)

        first = rows[0] if rows else None
        if g.get("cf") and first:
            title = f"{first.cf_code} {first.cf_name}"
        elif g.get("art") and first:
            title = f"{first.article_code or ''} {first.article_name or 'Без статьи'}".strip()
        elif first:
            title = first.activity_name
            if g.get("dir"):
                title += " · " + first.direction_name
        else:
            title = "Операции"

        query = g.copy()
        query["export"] = "csv"
        if cp_label:
            title = f"{title} · {cp_label}"
        return render(request, "dashboard/cf_ops_modal.html", {
            "view": "ops",
            "ops_url": here(),
            "cp_url": here(v="cp"),
            "back_url": here(v="cp") if cp_key is not None else "",
            "title": title,
            "period": period,
            "scope": " · ".join(_filters(request).values()) or "все счета",
            "rows": rows,
            "count": count,
            "limited": count > OPS_LIMIT,
            "limit": OPS_LIMIT,
            "csv_url": reverse("admin:dashboard_cashflow_ops") + "?" + query.urlencode(),
            "kpis": [
                {"label": "Операций", "value": f"{count:,}".replace(",", " "), "tone": "plain"},
                kpi("Поступления", inflow, tone="pos"),
                kpi("Выплаты", outflow),
                kpi("Итого", agg["s"] or 0, signed=True),
            ],
        })
