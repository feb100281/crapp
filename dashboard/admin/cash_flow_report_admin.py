"""
Отчёт о движении денежных средств — сводная таблица из витрины dashboard_cash_flow.

Строки: остаток на начало → деятельность → поступления/выплаты → статья →
подстатья, «Не разнесено», курсовые, чистый поток, остаток на конец.
Колонки: месяцы (или кварталы) выбранного года + итого.
Клик по сумме — модалка с операциями этой строки за этот период.

Остаток на начало периода = все строки ДДС до начала периода (включая ввод
остатков) + ввод остатков новых счетов внутри периода. Остаток на конец =
начало + поток. Так отчёт сходится с «Остатками по дням».
"""

from __future__ import annotations

import json
from collections import defaultdict
from urllib.parse import urlencode

from django.contrib import admin
from django.db.models import Max, Min, Sum
from django.db.models.functions import Abs
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import path, reverse

from core.admins.badges import Badge

from ..models import CashBalanceDay, CashFlow
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


def _filters(request) -> dict:
    return {f: request.GET[f] for f, _ in ACCOUNT_FILTERS if request.GET.get(f)}


def _bucket_months(mode: str, bucket: int) -> list[int]:
    return [bucket] if mode == "m" else [3 * bucket - 2, 3 * bucket - 1, 3 * bucket]


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
        try:
            year = int(request.GET.get("y") or years[-1])
        except (ValueError, IndexError):
            year = years[-1] if years else 0
        mode = "q" if request.GET.get("mode") == "q" else "m"

        ctx = {
            **self.admin_site.each_context(request),
            "title": "Отчёт о движении денежных средств",
            "opts": self.model._meta,
            "years": years,
            "year": year,
            "mode": mode,
            "filters": self._filter_bar(request, flt),
            "query": urlencode({**flt, "mode": mode}),
            "query_y": urlencode({**flt, "y": year}),
        }
        ctx.update(self._report(request, base, flt, year, mode))
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

    def _report(self, request, base, flt, year, mode):
        in_year = base.filter(year=year)
        months = in_year.aggregate(a=Min("month"), b=Max("month"))
        if months["a"] is None:
            return {"empty": True}

        # колонки
        if mode == "m":
            buckets = list(range(months["a"], months["b"] + 1))
            labels = [f"{MONTHS[m - 1]} {year % 100:02d}" for m in buckets]
            to_bucket = {m: m for m in buckets}
        else:
            buckets = sorted({(m - 1) // 3 + 1 for m in range(months["a"], months["b"] + 1)})
            labels = [QUARTERS[q - 1] for q in buckets]
            to_bucket = {m: (m - 1) // 3 + 1 for m in range(1, 13)}
        col = {b: i for i, b in enumerate(buckets)}
        n = len(buckets)

        def vec():
            return [0.0] * n

        # потоки (без ввода остатков)
        data: dict[tuple, list[float]] = defaultdict(vec)
        names: dict[tuple, tuple[str, str]] = {}
        rows = (
            in_year.exclude(source="OPENING")
            .values("activity", "direction", "article_code", "article_name", "cf_code", "cf_name", "month")
            .annotate(s=Sum("amount_rub"))
            .order_by()
        )
        for r in rows:
            i = col.get(to_bucket.get(r["month"]))
            if i is None:
                continue
            v = r["s"] or 0
            act, d = r["activity"], r["direction"]
            data[("A", act)][i] += v
            if act == FX_ACTIVITY:
                continue
            data[("D", act, d)][i] += v
            if act == 9:
                continue
            art = r["article_code"] or r["cf_code"] or "—"
            data[("R", act, d, art)][i] += v
            names[("R", act, d, art)] = (art, r["article_name"] or r["cf_name"] or "Без статьи")
            if r["cf_code"] and r["cf_code"] != art:
                key = ("S", act, d, art, r["cf_code"])
                data[key][i] += v
                names[key] = (r["cf_code"], r["cf_name"] or "")

        # остатки: до начала периода + ввод остатков новых счетов в периоде
        before = base.filter(year__lt=year).aggregate(s=Sum("amount_rub"))["s"] or 0
        opening_in = defaultdict(float)
        for r in (in_year.filter(source="OPENING").values("month")
                  .annotate(s=Sum("amount_rub")).order_by()):
            i = col.get(to_bucket.get(r["month"]))
            if i is not None:
                opening_in[i] += r["s"] or 0

        net = vec()
        for (kind, *rest), v in data.items():
            if kind == "A":
                for i in range(n):
                    net[i] += v[i]
        opening, closing = vec(), vec()
        running = before
        for i in range(n):
            opening[i] = running + opening_in[i]
            closing[i] = opening[i] + net[i]
            running = closing[i]

        # --- строки таблицы ---
        table, open_state = [], {}

        def cells(values, drill: dict | None, total=None):
            out = []
            for i, v in enumerate(values):
                url = self._ops_url(flt, year, mode, buckets[i], drill) if drill and round(v) else ""
                out.append({"v": money(v) if round(v) else "", "neg": round(v) < 0, "url": url})
            t = sum(values) if total is None else total
            url = self._ops_url(flt, year, mode, 0, drill) if drill and round(t) else ""
            return out, {"v": money(t) if round(t) else "", "neg": round(t) < 0, "url": url}

        def add(kind, label, values, *, code="", rid="", parents=(), drill=None, total=None, children=False):
            c, t = cells(values, drill, total)
            table.append({
                "kind": kind, "label": label, "code": code, "id": rid,
                "show": " && ".join(f"open['{p}']" for p in parents) or "true",
                "level": len(parents), "cells": c, "total": t, "children": children,
            })

        add("balance", "Остаток на начало", opening, total=opening[0])

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
        add("balance", "Остаток на конец", closing, total=closing[-1])

        # --- шапка ---
        inflow = sum(sum(v) for k, v in data.items() if k[0] == "D" and k[2] == 1)
        outflow = sum(sum(v) for k, v in data.items() if k[0] == "D" and k[2] == 2)
        unalloc = sum(abs(sum(v)) for k, v in data.items() if k[0] == "D" and k[1] == 9)
        turnover = abs(inflow) + abs(outflow)
        operating = sum(data.get(("A", 1), vec()))
        total_net = sum(net)

        period = f"{labels[0]} — {labels[-1]}" if n > 1 else labels[0]
        last_day = (CashBalanceDay.objects.filter(date__lte=f"{year}-12-31")
                    .aggregate(d=Max("date"))["d"])

        datasets = []
        legend = []
        for act, act_name in ACTIVITIES:
            a = data.get(("A", act))
            if not a or not any(round(x) for x in a):
                continue
            color, dot = ACTIVITY_COLORS[act]
            datasets.append({"type": "bar", "label": act_name, "data": [round(x) for x in a],
                             "backgroundColor": color, "stack": "flow", "maxBarThickness": 28,
                             "borderRadius": 3})
            legend.append((act_name.split()[0], dot))
        datasets.append({"type": "line", "label": "Остаток на конец", "data": [round(x) for x in closing],
                         "borderColor": "var(--color-pk-fall)", "backgroundColor": "var(--color-pk-fall)",
                         "yAxisID": "y1", "borderWidth": 2, "pointRadius": 2, "tension": 0.25})
        legend.append(("Остаток", "pk-dot-fall"))

        options = {
            "responsive": True,
            "maintainAspectRatio": False,
            "interaction": {"mode": "index", "intersect": False},
            "plugins": {"legend": {"display": False}},
            "scales": {
                "x": {"stacked": True, "grid": {"display": False}, "ticks": {"color": "#9ca3af"}},
                "y": {"stacked": True, "ticks": {"color": "#9ca3af"}, "grid": {"color": "#e5e7eb55"}},
                "y1": {"position": "right", "grid": {"display": False}, "ticks": {"color": "#9ca3af"}},
            },
        }

        dash = {
            "hero": {
                "kicker": f"Движение денежных средств · {year}",
                "title": f"{money(total_net, signed=True)} ₽",
                "accent": "чистый поток",
                "sub": f"{period} · {' · '.join(flt.values()) or 'все счета'} · "
                       f"остаток {money(opening[0])} → {money(closing[-1])} ₽",
            },
            "kpis": [
                kpi("Поступления", inflow, "все деятельности", tone="pos"),
                kpi("Выплаты", outflow, "все деятельности"),
                kpi("Операционный поток", operating, "чистый", signed=True),
                {"label": "Не разнесено", "value": f"{100 * unalloc / turnover:.1f} %" if turnover else "—",
                 "sub": f"{money(unalloc)} ₽ оборота", "tone": "neg" if unalloc else "plain"},
                kpi("Остаток на конец", closing[-1], f"на конец {labels[-1]}", tone="plain"),
            ],
            "charts": [{
                "title": "Чистый поток по деятельности и остаток, ₽",
                "type": "bar",
                "legend": legend,
                "data": chart(labels, datasets),
                "options": json.dumps(options),
            }],
        }
        if last_day:
            dash["status"] = stale_status(last_day, flt, closing[-1])

        return {
            "labels": labels,
            "table": table,
            "open_state": json.dumps(open_state),
            "dash": dash,
        }

    # ------------------------------------------------------------------
    # Модалка: операции строки отчёта за период
    # ------------------------------------------------------------------

    def _ops_url(self, flt, year, mode, bucket, drill):
        params = {**flt, "y": year, "mode": mode, "b": bucket, **drill}
        return reverse("admin:dashboard_cashflow_ops") + "?" + urlencode(params)

    def ops_view(self, request):
        g = request.GET
        year, mode, bucket = int(g["y"]), g.get("mode", "m"), int(g.get("b") or 0)
        qs = CashFlow.objects.filter(year=year, **_filters(request)).exclude(source="OPENING")
        if bucket:
            qs = qs.filter(month__in=_bucket_months(mode, bucket))
        if g.get("act"):
            qs = qs.filter(activity=int(g["act"]))
        if g.get("dir"):
            qs = qs.filter(direction=int(g["dir"]))
        if g.get("art"):
            art = g["art"]
            qs = qs.filter(article_code=art) if art != "—" else qs.filter(article_code__isnull=True)
        if g.get("cf"):
            qs = qs.filter(cf_code=g["cf"])

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

        if bucket:
            period = (f"{MONTHS[bucket - 1]} {year}" if mode == "m" else f"{QUARTERS[bucket - 1]} {year}")
        else:
            period = f"{year} год"

        return render(request, "dashboard/cf_ops_modal.html", {
            "title": title,
            "period": period,
            "scope": " · ".join(_filters(request).values()) or "все счета",
            "rows": rows,
            "count": count,
            "limited": count > OPS_LIMIT,
            "limit": OPS_LIMIT,
            "kpis": [
                {"label": "Операций", "value": f"{count:,}".replace(",", " "), "tone": "plain"},
                kpi("Поступления", inflow, tone="pos"),
                kpi("Выплаты", outflow),
                kpi("Итого", agg["s"] or 0, signed=True),
            ],
        })
