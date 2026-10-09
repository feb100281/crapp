"""
Дашборд «Контрагенты и статьи» — витрина dashboard_cp_audit.

Показывает, на какие статьи разнесены платежи каждого контрагента, и где
это настраивается: резолвер контрагента, КБК или счёта — ссылки ведут
прямо в карточку резолвера, где шаблоны отфильтрованы по контрагенту.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import admin
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import path, reverse

from core.admins.badges import Badge
from core.reports.http import csv_response, xlsx_response
from core.reports.xlsx import FMT_QTY, Book, Col, Row

from ..models import CpAudit
from ..services import cp_audit
from ..services.marts import ensure_cp_audit
from .common import DashboardAdmin, money

PAGE = 100        # контрагентов на странице
OPS_LIMIT = 500   # строк в модалке

DIRECTIONS = {1: "Поступления", 2: "Выплаты"}
DIRECTION_BADGES = {1: ("south_west", "success"), 2: ("north_east", "danger")}

RESOLVER_URLS = {
    "CP": "admin:treasury_cpresolver_change",
    "KBK": "admin:treasury_kbkresolver_change",
    "BA": "admin:treasury_baresolver_change",
    "IC": "admin:treasury_icresolver_change",
}


def resolver_url(kind: str, pk: int, find: str = "", inn: str = "") -> str:
    """Карточка резолвера с отбором шаблонов по контрагенту (по ИНН или названию)."""
    url = reverse(RESOLVER_URLS[kind], args=[pk])
    query = {"inn": inn, "name": find} if inn else ({"find": find} if find else {})
    return url + ("?" + urlencode(query) if query else "")


def pin_url(inn, name, direction, next_url: str = "") -> str:
    query = {"inn": inn or "", "name": name or "", "dir": direction}
    if next_url:
        query["next"] = next_url
    return reverse("admin:treasury_cpresolver_pin") + "?" + urlencode(query)


@admin.register(CpAudit)
class CpAuditAdmin(DashboardAdmin):
    """Вместо списка строк — дашборд по контрагентам."""

    def get_urls(self):
        return [
            path("ops/", self.admin_site.admin_view(self.ops_view), name="dashboard_cpaudit_ops"),
            *super().get_urls(),
        ]

    @staticmethod
    def _params(request) -> dict:
        g = request.GET

        def number(name):
            try:
                return int(g.get(name) or 0) or None
            except ValueError:
                return None

        view = g.get("view") if g.get("view") in dict(cp_audit.VIEWS) else "multi"
        direction = number("dir")
        return {
            "view": view,
            "direction": direction if direction in DIRECTIONS else None,
            "year": number("y"),
            "ba": (g.get("ba") or "").strip()[:5] or None,
            "item_id": number("item"),
            "q": (g.get("q") or "").strip(),
        }

    # ------------------------------------------------------------------

    def changelist_view(self, request, extra_context=None):
        ensure_cp_audit()

        p = self._params(request)
        view = p.pop("view")
        every = cp_audit.build(**p)
        rows = cp_audit.select(every, view)
        st = cp_audit.stats(every)

        if request.GET.get("export") == "csv":
            return csv_response(*self._csv(rows), "Контрагенты и статьи")
        if request.GET.get("export") == "xlsx":
            return xlsx_response(self._book(rows, st, p, view), "Контрагенты и статьи")

        here = request.get_full_path()
        page = Paginator(rows, PAGE).get_page(request.GET.get("p"))

        def ops_url(row, cf):
            query = {"mode": row.mode, "inn": row.inn, "name": row.name, "dir": row.direction, "cf": cf}
            query.update({k: v for k, v in (("y", p["year"]), ("ba", p["ba"])) if v})
            return reverse("admin:dashboard_cpaudit_ops") + "?" + urlencode(query)

        def label(row, kind, pk):
            return row.places.get((kind, pk)) or cp_audit.KIND_LABELS.get(kind, kind)

        for row in page:
            row.badge = Badge(DIRECTIONS[row.direction], *DIRECTION_BADGES[row.direction]).badge
            row.amount_text = money(row.total_amount)
            row.open_text = money(row.open_amount)
            row.pin_url = pin_url(row.inn, row.name, row.direction, here)
            row.open_ops_url = ops_url(row, "none")
            row.all_ops_url = ops_url(row, "")
            # где настраивать: резолверы КБК / счёта, которым принадлежат строки
            row.links = [
                {"label": text, "url": resolver_url(kind, pk, row.name, row.inn if row.mode == "inn" else ""),
                 "open": row.open_in.get((kind, pk), 0)}
                for (kind, pk), text in sorted(row.places.items(), key=lambda kv: kv[1])
            ]
            row.open_links = [link for link in row.links if link["open"]]
            row.uses = row.item_list
            for use in row.uses:
                use.amount_text = money(use.amount)
                use.share_text = "<1%" if 0 < use.share < 0.005 else f"{100 * use.share:.0f}%"
                use.ops_url = ops_url(row, use.item_id or "none")
                use.period = (f"{use.first:%d.%m.%Y} — {use.last:%d.%m.%Y}"
                              if use.first and use.first != use.last
                              else (f"{use.first:%d.%m.%Y}" if use.first else ""))
                use.how = [
                    {"label": label(row, kind, pk), "url": resolver_url(kind, pk, row.name, row.inn if row.mode == "inn" else ""),
                     "count": n, "cp": kind == "CP"}
                    for (kind, pk), n in sorted(use.by.items(), key=lambda kv: -kv[1])
                ]

        query = request.GET.copy()
        for key in ("p", "export"):
            query.pop(key, None)

        def link(**change):
            q = query.copy()
            for key, value in change.items():
                if value in (None, ""):
                    q.pop(key, None)
                else:
                    q[key] = value
            return "?" + q.urlencode()

        base = CpAudit.objects.order_by()
        years = sorted(base.exclude(year__isnull=True).values_list("year", flat=True).distinct(), reverse=True)
        bas = sorted(base.exclude(ba_key__isnull=True).values_list("ba_key", flat=True).distinct())
        items = (base.exclude(cf_item_id__isnull=True)
                 .values_list("cf_item_id", "cf_code", "cf_name").distinct().order_by("cf_code"))

        return TemplateResponse(request, "dashboard/cp_audit.html", {
            **self.admin_site.each_context(request),
            "title": "Контрагенты и статьи",
            "opts": self.model._meta,
            "page": page,
            "here": here,
            "params": p,
            "view": view,
            "views": [(code, text, link(view=code)) for code, text in cp_audit.VIEWS],
            "directions": [("", "Все", link(dir=None)), (1, "Поступления", link(dir=1)),
                           (2, "Выплаты", link(dir=2))],
            "years": years,
            "bas": bas,
            "items": items,
            "export_xlsx": link(export="xlsx"),
            "export_csv": link(export="csv"),
            "prev_url": link(p=page.previous_page_number()) if page.has_previous() else "",
            "next_url": link(p=page.next_page_number()) if page.has_next() else "",
            "reset_url": "?view=" + view,
            "pin_action": reverse("admin:treasury_cpresolver_pin"),
            "found": len(rows),
            "dash": {
                "hero": {
                    "kicker": "Контроль разноски",
                    "title": f"{st['multi']}",
                    "accent": "контрагентов разнесены на несколько статей",
                    "sub": "Раскройте строку: на какие статьи и каким резолвером разнесено. "
                           "Ссылки на резолвер открывают его с отбором по контрагенту.",
                },
                "kpis": [
                    {"label": "Контрагентов", "value": f"{st['total']:,}".replace(",", " "),
                     "sub": "с учётом фильтров", "tone": "plain"},
                    {"label": "Несколько статей", "value": str(st["multi"]),
                     "sub": "стоит просмотреть", "tone": "plain"},
                    {"label": "Похоже на ошибку", "value": str(st["suspect"]),
                     "sub": f"редкая статья: до {cp_audit.RARE_OPS} операций и до "
                            f"{cp_audit.RARE_SHARE:.0%} платежей",
                     "tone": "neg" if st["suspect"] else "pos"},
                    {"label": "Закреплено", "value": str(st["pinned"]),
                     "sub": "есть резолвер контрагента", "tone": "plain"},
                    {"label": "Не разнесено", "value": money(st["open_amount"]),
                     "sub": f"строк: {st['open_count']}", "tone": "neg" if st["open_count"] else "pos"},
                ],
            },
        })

    # ------------------------------------------------------------------
    # Выгрузки
    # ------------------------------------------------------------------

    @staticmethod
    def _flat(rows):
        for row in rows:
            direction = DIRECTIONS[row.direction]
            flag = "похоже на ошибку" if row.suspect else ("несколько статей" if row.multi else "")
            pinned = "да" if row.pinned else ""
            for use in row.item_list:
                yield [row.name, row.inn, direction, use.article_code or use.code,
                       use.article_name or use.name, use.code, use.name, use.count, use.amount,
                       round(100 * use.share, 1), use.first, use.last, use.manual,
                       "редкая" if use.rare else "", flag, pinned]
            if row.open_count:
                yield [row.name, row.inn, direction, "", "", "", "Не разнесено", row.open_count,
                       row.open_amount, None, None, None, 0, "", flag, pinned]

    def _csv(self, rows):
        return (
            ["Контрагент", "ИНН", "Направление", "Код статьи", "Статья", "Код подстатьи",
             "Подстатья", "Операций", "Сумма, ₽",
             "Доля операций, %", "Первая", "Последняя", "Руками", "Статья", "Контрагент",
             "Закреплён"],
            self._flat(rows),
        )

    @staticmethod
    def _book(rows, st, p, view) -> Book:
        scope = [dict(cp_audit.VIEWS)[view].lower()]
        if p["direction"]:
            scope.append(DIRECTIONS[p["direction"]].lower())
        if p["year"]:
            scope.append(f"{p['year']} год")
        if p["ba"]:
            scope.append(f"счёт {p['ba']}")
        if p["q"]:
            scope.append(f"поиск «{p['q']}»")

        book = Book(
            "Контрагенты и статьи",
            "На какие статьи ДДС разнесены платежи каждого контрагента",
            f"Российский рубль (RUB) · показано: {', '.join(scope)} · контрагентов: {len(rows)}",
        )
        book.kpi("Несколько статей", st["multi"], "контрагентов", FMT_QTY)
        book.kpi("Похоже на ошибку", st["suspect"], "есть редкая статья", FMT_QTY)
        book.kpi("Закреплено", st["pinned"], "резолвер контрагента", FMT_QTY)
        book.kpi("Не разнесено, ₽", st["open_amount"], f"строк: {st['open_count']}")

        table = []
        for row in rows:
            table.append(Row([
                row.name, row.inn, DIRECTIONS[row.direction], "", "",
                row.total_count, row.total_amount, None, None, None,
                "закреплён" if row.pinned else "",
            ], level="warn" if row.suspect else 3, collapsed=True))
            for use in row.item_list:
                table.append(Row([
                    "", "", "", use.code,
                    f"{use.article_name} → {use.name}" if use.article_name else use.name,
                    use.count, use.amount, 100 * use.share,
                    use.first, use.last, "редкая статья" if use.rare else "",
                ], outline=1, hidden=True, indent=2))
            if row.open_count:
                table.append(Row(["", "", "", "", "Не разнесено", row.open_count, row.open_amount,
                                  None, None, None, ""], outline=1, hidden=True, indent=2))

        book.sheet(
            "Контрагенты и статьи", "Контрагенты и статьи",
            subtitle="Контрагент → статьи (под плюсиком). Выделены контрагенты с редкой статьёй: "
                     f"до {cp_audit.RARE_OPS} операций и до {cp_audit.RARE_SHARE:.0%} платежей",
            description="Каждый контрагент и статьи, на которые он разнесён: операции, сумма, доля, период",
        ).table(
            [Col("Контрагент", width=46), Col("ИНН", kind="code", width=14),
             Col("Направление", width=13), Col("Код", kind="code", width=9),
             Col("Статья", width=56, indent=True),
             Col("Операций", kind="int", width=10), Col("Сумма, ₽", kind="money", width=17, total=True),
             Col("Доля", kind="pct", width=9), Col("Первая", kind="date", width=12),
             Col("Последняя", kind="date", width=12), Col("Пометка", width=18)],
            table,
        )
        return book

    # ------------------------------------------------------------------
    # Модалка: операции контрагента по статье
    # ------------------------------------------------------------------

    def ops_view(self, request):
        g = request.GET
        try:
            direction = int(g.get("dir") or 0)
            year = int(g["y"]) if g.get("y") else None
        except ValueError:
            return HttpResponseBadRequest("Неверные параметры")
        mode = "name" if g.get("mode") == "name" else "inn"
        inn, name, cf = g.get("inn") or "", g.get("name") or "", g.get("cf") or ""

        qs = cp_audit.cp_lines(mode, inn, name, direction, year=year, ba=g.get("ba") or None)
        if cf == "none":
            qs = qs.filter(is_open=True)
            item_title = "Не разнесено"
        elif cf:
            qs = qs.filter(cf_item_id=cf)
            first = qs.first()
            item_title = f"{first.cf_code} {first.cf_name}" if first else ""
        else:
            item_title = "Все операции"

        count = qs.count()
        total = sum(v or 0 for v in qs.values_list("amount", flat=True))
        rows = list(qs.order_by("-date", "line_id")[:OPS_LIMIT])
        kinds = cp_audit.KIND_LABELS
        for r in rows:
            r.url = reverse("admin:treasury_bsline_change", args=[r.line_id])
            r.amount_text = money(r.amount)
            r.account = f"…{r.ba_number[-6:]}" if r.ba_number else ""
            if r.is_open:
                r.how = None
            elif r.manual or not r.rule_kind:
                r.how = Badge("руками", None, "primary").badge
            else:
                r.how = Badge(kinds.get(r.rule_kind, r.rule_kind), None,
                              "warning" if r.rule_kind == "CP" else "info").badge

        # из дашборда открывается в новой вкладке — целой страницей
        template = ("dashboard/cp_audit_ops.html" if request.headers.get("HX-Request")
                    else "dashboard/cp_audit_ops_page.html")
        return render(request, template, {
            **({} if request.headers.get("HX-Request") else self.admin_site.each_context(request)),
            "title": f"Операции · {name or inn}",
            "name": name or inn,
            "inn": inn if mode == "inn" else "",
            "direction": DIRECTIONS.get(direction, ""),
            "item_title": item_title,
            "rows": rows,
            "count": count,
            "total": money(total),
            "limited": count > OPS_LIMIT,
            "limit": OPS_LIMIT,
        })
