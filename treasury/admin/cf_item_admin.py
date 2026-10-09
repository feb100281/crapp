"""
Админка справочника статей ДДС.

Список — статьи верхнего уровня, подстатьи раскрываются строкой. По каждой
видно, где она используется (правила, строки, оборот). Перестройка — кнопкой
«Перенести / объединить»: статья → подстатья другой статьи, подстатья →
отдельная статья, слияние двух статей. Разноска при этом не теряется.
"""

from __future__ import annotations

import json

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from core.admins.badges import Badge, ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.sections import ChildrenSection
from core.reports.http import csv_response, xlsx_response

from ..models.bs_line_alloc_model import BSLineAlloc
from ..models.cf_item_model import Activity, CFItem, Direction
from ..models.resolver_rule_model import ResolverRule
from ..services import cf_items
from ..services.cash_reports import build as build_cash_reports


ACTIVITY_BADGES = {
    Activity.OPERATING: ("storefront", "primary"),
    Activity.INVESTING: ("domain", "info"),
    Activity.FINANCING: ("account_balance", "warning"),
    Activity.INTRAGROUP: ("sync_alt", "gray"),
}

DIRECTION_BADGES = {
    Direction.INFLOW: ("south_west", "success"),
    Direction.OUTFLOW: ("north_east", "danger"),
}


def money(value) -> str:
    return f"{value or 0:,.0f}".replace(",", "\u00a0")


def _usage(request) -> dict:
    """Использование статей — один раз на запрос."""
    if not hasattr(request, "_cf_usage"):
        request._cf_usage = cf_items.usage()
    return request._cf_usage


def _usage_text(rules: int, allocs: int, amount, journal: int = 0) -> str:
    if not (rules or allocs or journal):
        return ""
    parts = []
    if rules:
        parts.append(f"правил {rules}")
    if allocs:
        parts.append(f"строк {allocs} · {money(amount)}\u00a0₽")
    if journal:
        parts.append(f"проводок {journal}")
    return " · ".join(parts)


KIND_SHORT = {"KBK": "КБК", "BA": "Счёт", "IC": "Свои счета", "CP": "Контрагент"}
RESOLVER_ADMIN = {"KBK": "kbkresolver", "BA": "baresolver", "IC": "icresolver", "CP": "cpresolver"}


def _resolvers_by_item() -> dict[int, list[dict]]:
    """{id статьи: [резолверы, чьи правила разносят на неё]} — для дерева."""
    out: dict[int, list[dict]] = {}
    rows = (ResolverRule.objects.filter(resolver__isnull=False).order_by()
            .values("cf_item_id", "resolver_id", "resolver__kind", "resolver__key",
                    "resolver__name", "resolver__direction")
            .annotate(n=Count("id")))
    for r in rows:
        kind = r["resolver__kind"]
        key, name = r["resolver__key"] or "", r["resolver__name"] or ""
        label = f"{key} · {name}" if name and not key.startswith("~") else (name or key)
        out.setdefault(r["cf_item_id"], []).append({
            "kind": kind,
            "kind_label": KIND_SHORT.get(kind, kind),
            "label": f"{label} · {Direction(r['resolver__direction']).label.lower()}",
            "url": reverse(f"admin:treasury_{RESOLVER_ADMIN.get(kind, 'resolver')}_change",
                           args=[r["resolver_id"]]),
            "n": r["n"],
        })
    return out


def restructure_url(*ids) -> str:
    return reverse("admin:treasury_cfitem_restructure") + "?ids=" + ",".join(str(i) for i in ids)


class UsedFilter(admin.SimpleListFilter):
    title = "Использование"
    parameter_name = "used"

    def lookups(self, request, model_admin):
        return [("no", "Нигде не используется"), ("yes", "Есть правила или разноска")]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        own = lambda model, ref: Exists(model.objects.filter(cf_item=OuterRef(ref)))  # noqa: E731
        sub = lambda model: Exists(model.objects.filter(cf_item__parent=OuterRef("pk")))  # noqa: E731
        queryset = queryset.annotate(
            used_any=Q(own(ResolverRule, "pk")) | Q(own(BSLineAlloc, "pk"))
            | Q(sub(ResolverRule)) | Q(sub(BSLineAlloc)),
        )
        return queryset.filter(used_any=self.value() == "yes")


class SubItemsSection(ChildrenSection):
    """Подстатьи — раскрываются прямо в списке статей."""

    verbose_name = "Подстатьи"
    empty_text = "Подстатей нет — разносится на саму статью"
    fields = ["code", "name", "description", "usage", "is_active", "move"]

    @display(description="Код")
    def code(self, obj):
        return self.change_link(obj, obj.code)

    @display(description="Подстатья")
    def name(self, obj):
        return self.change_link(obj, obj.name)

    @display(description="Описание")
    def description(self, obj):
        return obj.description or ""

    @display(description="Используется")
    def usage(self, obj):
        u = _usage(self.request).get(obj.pk, {})
        return _usage_text(u.get("rules", 0), u.get("allocs", 0), u.get("amount"), u.get("journal", 0)) or "—"

    @display(description="Активна")
    def is_active(self, obj):
        return "да" if obj.is_active else "нет"

    @display(description="")
    def move(self, obj):
        return format_html('<a href="{}">перенести / объединить</a>', restructure_url(obj.pk))


class CFSubItemInline(AppTabularInline):
    """Подстатьи — заводятся прямо в карточке статьи."""

    model = CFItem
    fk_name = "parent"
    fields = ["number", "name", "code", "is_active"]
    readonly_fields = ["code"]
    verbose_name = "Подстатья"
    verbose_name_plural = "Подстатьи"


class RestructureForm(forms.Form):
    """Что сделать с выбранными статьями."""

    OPS = (
        ("sub", "Сделать подстатьёй статьи"),
        ("top", "Сделать отдельной статьёй"),
        ("merge", "Объединить со статьёй"),
    )

    op = forms.ChoiceField(choices=OPS, widget=forms.RadioSelect, initial="sub", label="Действие")
    parent = forms.ModelChoiceField(
        queryset=CFItem.objects.none(), required=False, label="В какую статью",
    )
    activity = forms.TypedChoiceField(
        choices=Activity.choices, coerce=int, required=False, label="Деятельность",
    )
    target = forms.ModelChoiceField(
        queryset=CFItem.objects.none(), required=False, label="С какой статьёй объединить",
    )
    delete_source = forms.BooleanField(
        required=False, initial=True, label="Удалить исходную статью после объединения",
    )
    rebuild = forms.BooleanField(
        required=False, initial=True, label="Сразу пересчитать ДДС",
    )

    def __init__(self, *args, items: list[CFItem], **kwargs):
        super().__init__(*args, **kwargs)
        self.items = items
        direction = items[0].direction
        ids = [i.pk for i in items]
        same = CFItem.objects.filter(direction=direction, is_active=True).exclude(pk__in=ids)
        self.fields["parent"].queryset = same.filter(parent__isnull=True).order_by("code")
        self.fields["target"].queryset = (
            same.filter(children__isnull=True).exclude(parent_id__in=ids).order_by("code")
        )
        self.fields["activity"].initial = items[0].activity

    def clean(self):
        data = super().clean()
        need = {"sub": "parent", "top": "activity", "merge": "target"}.get(data.get("op"))
        if need and not data.get(need):
            self.add_error(need, "Выберите значение")
        return data


@admin.register(CFItem)
class CFItemAdmin(AppModelAdmin):
    list_display = [
        "code",
        "name_display",
        "activity_display",
        "direction_display",
        "children_count",
        "usage_display",
        "is_active",
    ]

    list_display_links = ["code", "name_display"]

    ordering = ["code"]

    # в списке — только статьи; подстатьи раскрываются строкой (list_sections),
    # поиск находит статью и по её подстатьям
    list_sections = [SubItemsSection]
    search_fields = ["code", "name", "description", "children__code", "children__name",
                     "children__description"]
    search_help_text = "Код, название или описание статьи / подстатьи"

    list_filter = [
        ("activity", ChoicesDropdownFilter),
        ("direction", ChoicesDropdownFilter),
        UsedFilter,
        "is_active",
    ]

    actions = ["restructure_selected"]
    actions_list = ["tree_link", "export_xlsx", "export_csv"]
    actions_row = ["restructure_row"]
    actions_detail = ["restructure_detail"]

    readonly_fields = ["code"]

    fieldsets = (
        (
            "Статья",
            {
                "fields": (
                    ("activity", "direction"),
                    "parent",
                    ("number", "code"),
                    "name",
                    ("reversible", "is_active"),
                ),
            },
        ),
        (
            "Описание",
            {
                "classes": ["tab"],
                "fields": ("description",),
            },
        ),
    )

    def get_inlines(self, request, obj):
        # подстатьи показываем только у статьи верхнего уровня
        if obj is None or obj.parent_id is None:
            return [CFSubItemInline]
        return []

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # родителем может быть только статья верхнего уровня
        if db_field.name == "parent":
            kwargs["queryset"] = CFItem.objects.filter(parent__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("parent")
        match = getattr(request, "resolver_match", None)
        if match and match.url_name == "treasury_cfitem_changelist":
            qs = qs.filter(parent__isnull=True).prefetch_related("children")
        return qs

    # ------------------------------------------------------------------
    # Перенос и объединение
    # ------------------------------------------------------------------

    def get_urls(self):
        return [
            path("tree/", self.admin_site.admin_view(self.tree_view), name="treasury_cfitem_tree"),
            path("restructure/", self.admin_site.admin_view(self.restructure_view),
                 name="treasury_cfitem_restructure"),
            *super().get_urls(),
        ]

    # ------------------------------------------------------------------
    # Дерево: деятельность → направление → статья → подстатья
    # ------------------------------------------------------------------

    def tree_view(self, request):
        from ..services.exports import plain

        items = list(CFItem.objects.select_related("parent").order_by("code"))
        use = _usage(request)
        children = {}
        for item in items:
            if item.parent_id:
                children.setdefault(item.parent_id, []).append(item)
        add_url = reverse("admin:treasury_cfitem_add")
        lines_url = reverse("admin:treasury_bsline_changelist")
        resolvers = _resolvers_by_item()

        def numbers(group):
            u = [use.get(i.pk, {}) for i in group]
            return (sum(x.get("rules", 0) for x in u), sum(x.get("allocs", 0) for x in u),
                    sum(x.get("amount", 0) for x in u))

        def item_row(item, kind, rid, parents, subs=()):
            rules, allocs, amount = numbers([item, *subs])
            used_by = {}
            for i in (item, *subs):
                for r in resolvers.get(i.pk, ()):
                    used_by.setdefault(r["url"], dict(r, n=0))["n"] += r["n"]
            text = plain(item.description)
            return {
                "kind": kind, "id": rid, "parents": parents, "level": len(parents),
                "code": item.code, "name": item.name, "description": text,
                "rules": rules, "allocs": allocs, "amount": money(amount) if amount else "",
                "lines_url": f"{lines_url}?cf={item.pk}" if allocs else "",
                "resolvers": sorted(used_by.values(), key=lambda r: (r["kind"], r["label"])),
                "active": item.is_active, "children": bool(subs),
                "reversible": item.reversible,
                "locked": bool(cf_items.locked_reason(item)),
                "edit_url": reverse("admin:treasury_cfitem_change", args=[item.pk]),
                "move_url": restructure_url(item.pk),
                "add_url": (f"{add_url}?parent={item.pk}&activity={item.activity}&direction={item.direction}"
                            if kind == "item" else ""),
                "search": " ".join([item.code, item.name, text] + [f"{c.code} {c.name}" for c in subs]).lower(),
            }

        rows, open_state = [], {}
        tops = [i for i in items if not i.parent_id]
        for activity, activity_name in Activity.choices:
            group = [i for i in tops if i.activity == activity]
            aid = f"a{activity}"
            open_state[aid] = True
            act_row = {"kind": "activity", "id": aid, "parents": [], "level": 0, "name": activity_name,
                       "code": f"{activity}00000", "children": True, "search": ""}
            rows.append(act_row)
            for direction, direction_name in Direction.choices:
                part = [i for i in group if i.direction == direction]
                did = f"{aid}d{direction}"
                open_state[did] = True
                rules, allocs, amount = numbers(part + [c for i in part for c in children.get(i.pk, [])])
                dir_row = {
                    "kind": "direction", "id": did, "parents": [aid], "level": 1, "name": direction_name,
                    "code": f"{activity}{direction}0000", "children": bool(part),
                    "rules": rules, "allocs": allocs, "amount": money(amount) if amount else "",
                    "add_url": f"{add_url}?activity={activity}&direction={direction}", "search": "",
                }
                rows.append(dir_row)
                for item in part:
                    subs = children.get(item.pk, [])
                    iid = f"i{item.pk}"
                    open_state[iid] = True
                    row = item_row(item, "item", iid, [aid, did], subs)
                    rows.append(row)
                    dir_row["search"] += " " + row["search"]
                    for sub in subs:
                        rows.append(item_row(sub, "sub", f"s{sub.pk}", [aid, did, iid]))
                act_row["search"] += " " + dir_row["search"]

        for row in rows:
            row["show"] = " && ".join(f"open['{p}']" for p in row["parents"]) or "true"

        return TemplateResponse(request, "treasury/cf_tree.html", {
            **self.admin_site.each_context(request),
            "title": "Статьи ДДС",
            "opts": self.model._meta,
            "rows": rows,
            "open_state": json.dumps(open_state),
            "list_url": reverse("admin:treasury_cfitem_changelist") + "?list=1",
            "xlsx_url": reverse("admin:treasury_cfitem_export_xlsx"),
            "add_url": add_url,
            "counts": {"items": len(tops), "subs": len(items) - len(tops)},
        })

    @admin.action(description="Перенести / объединить выбранные")
    def restructure_selected(self, request, queryset):
        return redirect(restructure_url(*queryset.values_list("pk", flat=True)))

    @action(description="Перенести / объединить", url_path="restructure-row", icon="move_up")
    def restructure_row(self, request: HttpRequest, object_id: int):
        return redirect(restructure_url(object_id))

    @action(description="Перенести / объединить", url_path="restructure-detail", icon="move_up")
    def restructure_detail(self, request: HttpRequest, object_id: int):
        return redirect(restructure_url(object_id))

    def restructure_view(self, request):
        back = reverse("admin:treasury_cfitem_tree")
        try:
            ids = [int(x) for x in request.GET.get("ids", "").split(",") if x.strip()]
        except ValueError:
            ids = []
        items = list(CFItem.objects.filter(pk__in=ids).select_related("parent").order_by("code"))
        if not items:
            messages.warning(request, "Не выбрано ни одной статьи")
            return redirect(back)
        if len({i.direction for i in items}) > 1:
            messages.error(request, "Выбраны и поступления, и выплаты — переносите их отдельно")
            return redirect(back)

        form = RestructureForm(request.POST or None, items=items)
        if request.method == "POST" and form.is_valid():
            done = self._restructure(request, items, form.cleaned_data)
            if done:
                return redirect(back)

        use = _usage(request)
        rows = []
        for item in items:
            subs = list(item.children.order_by("code")) if not item.parent_id else []
            total = [use.get(i.pk, {}) for i in [item] + subs]
            rows.append({
                "item": item,
                "subs": subs,
                "locked": cf_items.locked_reason(item),
                "usage": _usage_text(
                    sum(u.get("rules", 0) for u in total),
                    sum(u.get("allocs", 0) for u in total),
                    sum(u.get("amount", 0) for u in total),
                    sum(u.get("journal", 0) for u in total),
                ) or "не используется",
            })

        return TemplateResponse(request, "treasury/cf_restructure.html", {
            **self.admin_site.each_context(request),
            "title": "Перенести / объединить статьи",
            "opts": self.model._meta,
            "form": form,
            "rows": rows,
            "back": back,
            "direction": items[0].get_direction_display(),
        })

    def _restructure(self, request, items, data) -> bool:
        log, ok = [], True
        for item in items:
            try:
                # статья могла уйти вместе с родителем на предыдущем шаге
                item = CFItem.objects.select_related("parent").get(pk=item.pk)
                if data["op"] == "sub":
                    log += cf_items.make_sub(item, data["parent"])
                elif data["op"] == "top":
                    log += cf_items.make_top(item, data["activity"])
                else:
                    log += cf_items.merge(item, data["target"], delete=data["delete_source"])
            except CFItem.DoesNotExist:
                continue
            except ValidationError as exc:
                ok = False
                messages.error(request, "; ".join(exc.messages))

        for line in log:
            messages.success(request, line)
        if not log:
            return False

        from dashboard.services.marts import refresh_cp_audit

        refresh_cp_audit()

        if data["rebuild"]:
            try:
                stats = build_cash_reports(log=lambda *_: None)
                if stats.get("skipped"):
                    messages.warning(request, "ДДС не пересчитан: нет курсов ЦБ")
                else:
                    messages.info(request, "ДДС пересчитан — отчёт уже с новыми статьями")
            except Exception as exc:  # статьи уже перенесены, отчёт пересчитается позже
                messages.warning(request, f"Статьи перенесены, но ДДС не пересчитался: {exc}")
        else:
            messages.info(request, "Не забудьте пересчитать ДДС — в отчёте пока старые коды статей")
        return ok or bool(log)

    # ------------------------------------------------------------------
    # Выгрузка
    # ------------------------------------------------------------------

    @action(description="Деревом", url_path="tree-link", icon="account_tree")
    def tree_link(self, request: HttpRequest):
        return redirect(reverse("admin:treasury_cfitem_tree"))

    @action(description="Скачать Excel", url_path="export-xlsx", icon="download",
            variant=ActionVariant.PRIMARY)
    def export_xlsx(self, request: HttpRequest):
        from ..services.exports import cf_items_workbook

        return xlsx_response(cf_items_workbook(), "Статьи ДДС")

    @action(description="Скачать CSV", url_path="export-csv", icon="csv")
    def export_csv(self, request: HttpRequest):
        from ..services.exports import plain

        use = cf_items.usage()
        rows = []
        for item in CFItem.objects.select_related("parent").order_by("code"):
            u = use.get(item.pk, {})
            rows.append([
                item.code, item.parent.name if item.parent else item.name,
                item.name if item.parent else "", item.get_activity_display(),
                item.get_direction_display(), item.is_active, u.get("rules", 0),
                u.get("allocs", 0), u.get("amount", 0), plain(item.description),
            ])
        return csv_response(
            ["Код", "Статья", "Подстатья", "Деятельность", "Направление", "Активна",
             "Правил", "Операций", "Оборот, ₽", "Описание"],
            rows, "Статьи ДДС",
        )

    # ------------------------------------------------------------------

    @display(description="Название", ordering="code")
    def name_display(self, obj: CFItem):
        if obj.is_sub:
            return format_html('<span class="pk-field-sub">↳</span> {}', obj.name)
        return format_html("<strong>{}</strong>", obj.name)

    @display(description="Деятельность", ordering="activity")
    def activity_display(self, obj: CFItem):
        return ChoiceBadge(obj, "activity", ACTIVITY_BADGES).badge

    @display(description="Направление", ordering="direction")
    def direction_display(self, obj: CFItem):
        return ChoiceBadge(obj, "direction", DIRECTION_BADGES).badge

    @display(description="Подстатей")
    def children_count(self, obj: CFItem) -> int | str:
        if obj.is_sub:
            return "—"
        return len(obj.children.all())

    @display(description="Используется")
    def usage_display(self, obj: CFItem):
        use = _usage(self.request_for_usage)
        ids = [obj.pk] + [c.pk for c in obj.children.all()]
        total = [use.get(i, {}) for i in ids]
        text = _usage_text(
            sum(u.get("rules", 0) for u in total),
            sum(u.get("allocs", 0) for u in total),
            sum(u.get("amount", 0) for u in total),
            sum(u.get("journal", 0) for u in total),
        )
        return text or Badge("не используется", None, "gray").badge

    def changelist_view(self, request, extra_context=None):
        # по умолчанию — дерево; список — по кнопке «Списком» (?list=1),
        # с поиском, фильтрами или после массового действия
        if request.GET.get("list"):
            request.GET = request.GET.copy()
            request.GET.pop("list")
        elif request.method == "GET" and not request.GET:
            return redirect(reverse("admin:treasury_cfitem_tree"))
        self.request_for_usage = request
        return super().changelist_view(request, extra_context)
