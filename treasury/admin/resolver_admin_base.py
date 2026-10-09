"""
Общая основа админки резолверов (КБК, счета, свои, контрагенты).

Список: по строке на ключ + направление, неразобранное — наверху. Поиск
находит резолвер и по его строкам: контрагент, ИНН, счёт, назначение.
Карточка: что ещё не разнесено (шаблоны назначений, внутри — контрагенты),
куда разнесено сейчас и правила «содержит → статья». Сохранили правила —
строки сразу переразнеслись, разнесённое исчезает из «Не разнесено».
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import admin, messages
from django.db.models import Count, Q, Sum
from django.http import HttpRequest
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html

from unfold.contrib.filters.admin import ChoicesDropdownFilter, DropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import PaginationInlineFormSet

from core.admins.badges import Badge, ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.fields import Stack
from core.reports.http import xlsx_response

from ..models.bs_line_alloc_model import BSLineAlloc
from ..models.bs_line_model import BSLine
from ..models.cf_item_model import CFItem, Direction
from ..models.resolver_model import ResolverKind, norm_name
from ..models.resolver_rule_model import ResolverRule
from ..services.cash_reports import build as build_cash_reports
from ..services.resolver import (
    BA_LINES, CP_ELIGIBLE, cp_key, lines, open_patterns, resolve, resolve_all, shared_inns, sync,
)


DIRECTION_BADGES = {
    Direction.INFLOW: ("south_west", "success"),
    Direction.OUTFLOW: ("north_east", "danger"),
}

PATTERNS_LIMIT = 500   # шаблонов в карточке
CPS_LIMIT = 40         # контрагентов внутри одного шаблона
ITEMS_SHOWN = 5        # статей в строке списка


def money(value) -> str:
    """Рубли без копеек, тысячи через пробел."""
    return f"{value or 0:,.0f}".replace(",", " ")


def lines_url(**params) -> str:
    """Список операций с фильтром (поля BSLine напрямую)."""
    query = {k: v for k, v in params.items() if v not in (None, "")}
    return reverse("admin:treasury_bsline_changelist") + "?" + urlencode(query)


def pin_url(inn, name, direction, next_url: str = "") -> str:
    """Завести (или открыть) резолвер контрагента."""
    query = {"inn": inn or "", "name": name or "", "dir": direction}
    if next_url:
        query["next"] = next_url
    return reverse("admin:treasury_cpresolver_pin") + "?" + urlencode(query)


# ------------------------------------------------------------------
# Фильтры списка
# ------------------------------------------------------------------

class OpenFilter(admin.SimpleListFilter):
    title = "Разнесено"
    parameter_name = "open"

    def lookups(self, request, model_admin):
        return [("yes", "Есть неразнесённое"), ("no", "Всё разнесено")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(open_count__gt=0)
        if self.value() == "no":
            return queryset.filter(open_count=0)
        return queryset


class RulesFilter(admin.SimpleListFilter):
    title = "Правила"
    parameter_name = "rules"

    def lookups(self, request, model_admin):
        return [("no", "Нет правил"), ("yes", "Есть правила"), ("rest", "Есть «всё остальное»")]

    def queryset(self, request, queryset):
        if self.value() == "no":
            return queryset.filter(rules__isnull=True)
        if self.value() == "yes":
            return queryset.filter(rules__isnull=False).distinct()
        if self.value() == "rest":
            return queryset.filter(rules__text_regex="").distinct()
        return queryset


class ItemFilter(DropdownFilter):
    """Статья ДДС, на которую разносит хотя бы одно правило резолвера."""

    title = "Статья"
    parameter_name = "item"

    def lookups(self, request, model_admin):
        items = (
            CFItem.objects.filter(resolver_rules__resolver__kind=model_admin.kind)
            .distinct().order_by("code")
        )
        return [(str(i.pk), str(i)) for i in items]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(rules__cf_item_id=self.value()).distinct()
        return queryset


class SizeFilter(admin.SimpleListFilter):
    title = "Не разнесено, ₽"
    parameter_name = "size"

    STEPS = (("1m", "от 1 млн", 1_000_000), ("100k", "от 100 тыс.", 100_000), ("10k", "от 10 тыс.", 10_000))

    def lookups(self, request, model_admin):
        return [(code, label) for code, label, _ in self.STEPS]

    def queryset(self, request, queryset):
        for code, _, amount in self.STEPS:
            if self.value() == code:
                return queryset.filter(open_amount__gte=amount)
        return queryset


# ------------------------------------------------------------------

class RuleFormSet(PaginationInlineFormSet):
    """Статья правила — без подстатей и того же направления, что и резолвер
    (или с флажком «Принимает возвраты»).
    Нужна здесь: у нового резолвера модель правила направление ещё не видит."""

    def clean(self):
        super().clean()
        direction = getattr(self.instance, "direction", None)
        for form in self.forms:
            data = getattr(form, "cleaned_data", None) or {}
            item = data.get("cf_item")
            if not item or data.get("DELETE"):
                continue
            if item.children.exists():
                form.add_error("cf_item", "У статьи есть подстатьи — выберите подстатью")
            elif not item.accepts(direction):
                form.add_error(
                    "cf_item",
                    f"Резолвер — {Direction(direction).label.lower()}, а статья — "
                    f"{item.get_direction_display().lower()}: выберите статью того же направления "
                    f"или, если это возврат, включите у статьи «Принимает возвраты»",
                )


class ResolverRuleInline(AppTabularInline):
    model = ResolverRule
    formset = RuleFormSet
    fk_name = "resolver"
    fields = ["order", "text_regex", "cf_item", "hits", "hits_amount"]
    readonly_fields = ["hits", "hits_amount"]
    autocomplete_fields = ["cf_item"]
    verbose_name = "Правило"
    verbose_name_plural = "Правила: «содержит» → статья (пустое «содержит» = всё остальное)"
    tab = False
    show_change_link = False

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "text_regex" and field:
            field.help_text = ("Кусок назначения как есть — скобки, точки, № можно; регистр и лишние "
                               "пробелы не важны. Или регулярка: «аренд|коммунал». Пусто — всё остальное")
        return field


class ResolverAdminBase(AppModelAdmin):
    """Наследники задают kind, kind_label, changelist_url_name и свои поля."""

    kind = None
    kind_label = "Резолвер"
    changelist_url_name = ""

    list_display_links = ["key"]

    list_filter = [
        OpenFilter,
        SizeFilter,
        ("direction", ChoicesDropdownFilter),
        RulesFilter,
        ItemFilter,
    ]

    inlines = [ResolverRuleInline]

    actions_list = ["refresh_all", "rebuild_cash_flow", "export_rules"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("rules__cf_item")

    def get_object(self, request, object_id, from_field=None):
        # ?find=… &inn=… — открыт из дашборда: шаблоны только этого контрагента
        obj = super().get_object(request, object_id, from_field)
        if obj is not None:
            obj._find = ((request.GET.get("find") or "").strip(), (request.GET.get("inn") or "").strip(),
                         (request.GET.get("name") or "").strip())
        return obj

    def has_add_permission(self, request):
        return False

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        resolve(obj)
        obj.refresh_from_db()
        label = obj.name or obj.key if obj.kind == ResolverKind.CP else obj.key
        if obj.open_count:
            messages.info(
                request,
                f"{self.kind_label} {label}: не разнесено {obj.open_count} строк "
                f"на {money(obj.open_amount)} ₽",
            )
        else:
            messages.success(request, f"{self.kind_label} {label}: всё разнесено")

    # ------------------------------------------------------------------
    # Поиск: резолвер находится и по своим строкам
    # ------------------------------------------------------------------

    def get_search_results(self, request, queryset, search_term):
        found, distinct = super().get_search_results(request, queryset, search_term)
        term = (search_term or "").strip()
        if not term or self.kind == ResolverKind.IC:
            return found, distinct

        ids = self._ids_by_lines(term)
        if not ids:
            return found, distinct
        return queryset.filter(Q(pk__in=found.values("pk")) | Q(pk__in=ids)), False

    def _ids_by_lines(self, term: str) -> set[int]:
        match = (
            Q(cp_name__icontains=term) | Q(description__icontains=term)
            | Q(inn_adjust__startswith=term) | Q(cp_ba__startswith=term)
        )
        hits = BSLine.objects.filter(match).order_by()
        resolvers = self.model.objects.all()

        if self.kind == ResolverKind.KBK:
            pairs = set(hits.filter(kbk__isnull=False).values_list("kbk", "direction").distinct())
            return {r.pk for r in resolvers if (r.key, r.direction) in pairs}

        if self.kind == ResolverKind.BA:
            pairs = set(hits.filter(**BA_LINES).values_list("ba_resolver", "direction").distinct())
            return {r.pk for r in resolvers if (r.key, r.direction) in pairs}

        hits = hits.filter(**CP_ELIGIBLE)
        by_inn, by_name = set(), set()
        for inn, name, direction in hits.values_list("inn_adjust", "cp_name", "direction").distinct():
            by_inn.add((inn, direction))
            by_name.add((norm_name(name), direction))
        return {
            r.pk for r in resolvers
            if ((norm_name(r.match_name), r.direction) in by_name if r.match_name
                else (r.key, r.direction) in by_inn)
        }

    # ------------------------------------------------------------------
    # Колонки списка
    # ------------------------------------------------------------------

    @display(description="Направление", ordering="direction")
    def direction_display(self, obj):
        return ChoiceBadge(obj, "direction", DIRECTION_BADGES).badge

    @display(description="Контрагенты", ordering="payee")
    def payee_display(self, obj):
        return Stack((obj.payee or "")[:80] or None).html

    @display(description="Сумма", ordering="lines_amount")
    def amount_display(self, obj):
        return money(obj.lines_amount)

    @display(description="Не разнесено", ordering="open_amount")
    def open_display(self, obj):
        if not obj.open_count:
            return Badge("Всё", "check", "success").badge
        return Badge(f"{obj.open_count} стр · {money(obj.open_amount)}", "pending", "warning").badge

    @display(description="Статьи")
    def items_display(self, obj):
        names = list(dict.fromkeys(str(r.cf_item) for r in obj.rules.all()))
        if not names:
            return Badge("нет правил", None, "gray").badge
        if len(names) > ITEMS_SHOWN:
            names = names[:ITEMS_SHOWN] + [f"… и ещё {len(names) - ITEMS_SHOWN}"]
        return Stack(*names).html

    # ------------------------------------------------------------------
    # Карточка: не разнесено / разнесено
    # ------------------------------------------------------------------

    @display(description="Не разнесено — шаблоны назначений")
    def open_patterns_display(self, obj):
        if not obj.pk:
            return "Появится после сохранения"
        find, find_inn, label = getattr(obj, "_find", ("", "", ""))
        label = label or find or find_inn
        groups = open_patterns(obj, limit=PATTERNS_LIMIT + 1, find=find, find_inn=find_inn)
        reset_url = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
        if not groups:
            if find or find_inn:
                return format_html(
                    '{} <a href="{}">показать все шаблоны</a>',
                    Badge(f"У «{label}» здесь всё разнесено", "check", "success").badge, reset_url,
                )
            return Badge("Всё разнесено", "check", "success").badge

        shared = shared_inns()
        back = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
        own_cp = obj.kind == ResolverKind.CP
        pinnable = obj.kind in (ResolverKind.BA, ResolverKind.KBK)

        for g in groups[:PATTERNS_LIMIT]:
            cps = g["cps"]
            g["cp_count"] = len(cps)
            g["more"] = max(len(cps) - CPS_LIMIT, 0)
            g["cps"] = cps[:CPS_LIMIT]
            g["amount_text"] = money(g["amount"])
            g["search"] = " ".join(
                [g["pattern"], g["example"]] + [f"{c['name']} {c['inn']}" for c in cps]
            ).lower()
            for c in g["cps"]:
                mode, _ = cp_key(c["inn"], c["name"], shared)
                c["amount_text"] = money(c["amount"])
                c["by_name"] = mode == "name"
                c["ops_url"] = lines_url(
                    direction__exact=obj.direction, alloc="no", desc_pattern__exact=g["pattern"],
                    **({"cp_name__iexact": c["name"]} if mode == "name" else {"inn_adjust__exact": c["inn"]}),
                )
                if pinnable and (c["inn"] or c["name"]):
                    c["pin_url"] = pin_url(c["inn"], c["name"], obj.direction, back)

        return render_to_string("treasury/open_patterns.html", {
            "groups": groups[:PATTERNS_LIMIT],
            "limited": len(groups) > PATTERNS_LIMIT,
            "limit": PATTERNS_LIMIT,
            "cps_limit": CPS_LIMIT,
            "own_cp": own_cp,
            "find": label if (find or find_inn) else "",
            "reset_url": reset_url,
        })

    @display(description="Разнесено сейчас — по статьям")
    def alloc_summary_display(self, obj):
        if not obj.pk:
            return "Появится после сохранения"
        rows = list(
            BSLineAlloc.objects.filter(line__in=lines(obj).values("pk")).order_by()
            .values("cf_item__code", "cf_item__name", "manual", "resolver_rule__resolver__kind",
                    "resolver_rule__resolver_id")
            .annotate(n=Count("id"), s=Sum("amount"))
        )
        if not rows:
            return Badge("Пока ничего не разнесено", None, "gray").badge

        sources = dict(ResolverKind.choices)
        for r in rows:
            kind = r["resolver_rule__resolver__kind"]
            if r["manual"] or not kind:
                r["source"], r["style"] = "руками", "primary"
            elif r["resolver_rule__resolver_id"] == obj.pk:
                r["source"], r["style"] = "правила этого резолвера", "success"
            else:
                r["source"] = f"резолвер: {sources.get(kind, kind).lower()}"
                r["style"] = "warning" if kind == ResolverKind.CP else "info"
            r["badge"] = Badge(r["source"], None, r["style"]).badge
            r["amount_text"] = money(r["s"])
        rows.sort(key=lambda r: -(r["s"] or 0))
        return render_to_string("treasury/alloc_summary.html", {"rows": rows})

    # ------------------------------------------------------------------
    # Кнопки над списком
    # ------------------------------------------------------------------

    @action(
        description="Обновить список и переразнести",
        url_path="refresh-all",
        icon="refresh",
        variant=ActionVariant.PRIMARY,
    )
    def refresh_all(self, request: HttpRequest):
        new = sync()
        s = resolve_all(self.kind)
        messages.success(
            request,
            f"Новых резолверов: {new}. Всего: {s['resolvers']}, строк: {s['lines']}, "
            f"не разнесено: {s['open']}.",
        )
        return redirect(reverse(self.changelist_url_name))

    @action(
        description="Пересчитать ДДС (parquet)",
        url_path="rebuild-cash-flow",
        icon="table_chart",
        variant=ActionVariant.DEFAULT,
    )
    def rebuild_cash_flow(self, request: HttpRequest):
        log = []
        s = build_cash_reports(log=log.append)
        if s.get("skipped"):
            messages.warning(request, "ДДС не пересчитан: нет курсов ЦБ (запустите «Импорт курсов»)")
        else:
            level = messages.success if not s["off"] else messages.warning
            level(
                request,
                f"ДДС пересчитан: строк {s['rows']}, не разнесено {s['unalloc']} "
                f"на {money(s['unalloc_rub'])} ₽. "
                + ("Остатки сходятся." if not s["off"] else f"Не сходится счетов: {s['off']} — см. лог / cash_flow_check."),
            )
        return redirect(reverse(self.changelist_url_name))

    @action(
        description="Скачать правила (Excel)",
        url_path="export-rules",
        icon="download",
        variant=ActionVariant.DEFAULT,
    )
    def export_rules(self, request: HttpRequest):
        from ..services.exports import rules_workbook

        return xlsx_response(rules_workbook(self.kind), f"Правила разноски — {self.kind_label}")
