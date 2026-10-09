"""
Резолверы контрагентов — правила «этот контрагент → эта статья».

Приоритетнее резолверов КБК и счетов: строка сначала проверяется здесь, и
только если ни одно правило не подошло — по правилам КБК или счёта.
На переводы между своими счетами не действует.

Контрагент ищется по ИНН. Если ИНН нет (нерезиденты) или он общий на многих
(физлица приходят с ИНН своего банка) — по названию из выписки.

Заводится кнопкой «закрепить статью» (карточка резолвера КБК или счёта, дашборд
«Контрагенты и статьи») или вручную. Удалили — строки вернулись КБК / счёту.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.http import url_has_allowed_host_and_scheme

from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.widgets import UnfoldAdminTextInputWidget

from core.admins.fields import Stack
from ..models.cf_item_model import CFItem
from ..models.resolver_model import CpResolver, NAME_KEY_PREFIX, Resolver, ResolverKind, name_key
from ..services.resolver import cp_key, pin_cp, release_cp, resolve, set_catch_all, shared_inns
from .resolver_admin_base import ResolverAdminBase, ResolverRuleInline


class CpRuleInline(ResolverRuleInline):
    verbose_name_plural = (
        "Правила: «содержит» → статья. Пустое «содержит» = все платежи контрагента; "
        "несколько услуг — несколько правил с текстом"
    )

    def get_extra(self, request, obj=None, **kwargs):
        return 0 if obj and obj.rules.exists() else 1


class CpResolverForm(forms.ModelForm):
    inn = forms.CharField(
        label="ИНН", required=False, max_length=12, widget=UnfoldAdminTextInputWidget,
        help_text="Все строки с этим ИНН. Оставьте пустым, если ищем по названию",
    )

    class Meta:
        model = CpResolver
        fields = ["name", "inn", "match_name", "direction", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Контрагент"
        self.fields["name"].help_text = "Как показывать в списке"
        self.fields["match_name"].help_text = (
            "Заполняется, когда ИНН нет или он общий (физлицо с ИНН банка): "
            "точное название из выписки, регистр не важен"
        )
        if self.instance.pk and not self.instance.match_name:
            self.fields["inn"].initial = self.instance.key

    def clean(self):
        data = super().clean()
        inn = (data.get("inn") or "").strip()
        match_name = " ".join((data.get("match_name") or "").split())
        direction = data.get("direction")

        if match_name:
            key = name_key(match_name)
        elif inn:
            key = inn
        else:
            raise ValidationError("Укажите ИНН или название контрагента")

        clash = Resolver.objects.filter(kind=ResolverKind.CP, key=key, direction=direction)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if direction and clash.exists():
            raise ValidationError("Для этого контрагента и направления резолвер уже есть")

        self.instance.kind = ResolverKind.CP
        self.instance.key = key
        data["match_name"] = match_name or None
        return data


@admin.register(CpResolver)
class CpResolverAdmin(ResolverAdminBase):
    kind = ResolverKind.CP
    kind_label = "Контрагент"
    changelist_url_name = "admin:treasury_cpresolver_changelist"

    form = CpResolverForm
    inlines = [CpRuleInline]

    list_display = [
        "cp_display",
        "direction_display",
        "lines_count",
        "amount_display",
        "open_display",
        "items_display",
    ]
    list_display_links = ["cp_display"]

    search_fields = ["key", "name", "match_name", "note", "rules__cf_item__name", "rules__text_regex"]
    search_help_text = "Контрагент, ИНН, статья — или назначение из строк"

    readonly_fields = ["lines_count", "amount_display", "open_display",
                       "open_patterns_display", "alloc_summary_display"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("name", "direction"),
                    ("inn", "match_name"),
                    ("lines_count", "amount_display", "open_display"),
                    "open_patterns_display",
                    "alloc_summary_display",
                    "note",
                ),
            },
        ),
    )

    actions_list = ["audit_link", "refresh_all", "rebuild_cash_flow", "export_rules"]

    def has_add_permission(self, request):
        return True

    @action(description="Контрагенты и статьи", url_path="audit-link", icon="fact_check",
            variant=ActionVariant.DEFAULT)
    def audit_link(self, request):
        return redirect(reverse("admin:dashboard_cpaudit_changelist"))

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        return {k: v for k, v in initial.items() if k in ("name", "inn", "match_name", "direction", "note")}

    def delete_model(self, request, obj):
        release_cp(obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            release_cp(obj)

    def response_add(self, request, obj, post_url_continue=None):
        return self._back(request) or super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if "_continue" in request.POST or "_addanother" in request.POST:
            return super().response_change(request, obj)
        return self._back(request) or super().response_change(request, obj)

    @staticmethod
    def _back(request):
        """После сохранения — туда, откуда пришли «закреплять» (если пришли)."""
        target = request.GET.get("next")
        if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            return redirect(target)
        return None

    # ------------------------------------------------------------------
    # «Закрепить статью»: открыть резолвер контрагента или завести новый
    # ------------------------------------------------------------------

    def get_urls(self):
        view = self.admin_site.admin_view
        return [
            path("pin/", view(self.pin_view), name="treasury_cpresolver_pin"),
            path("audit/", view(self.audit_redirect), name="treasury_cpresolver_audit"),
            *super().get_urls(),
        ]

    def pin_view(self, request):
        data = request.POST if request.method == "POST" else request.GET
        inn, name = (data.get("inn") or "").strip(), (data.get("name") or "").strip()
        try:
            direction = int(data.get("dir") or 0)
        except ValueError:
            direction = 0
        if direction not in (1, 2) or not (inn or name):
            return HttpResponseBadRequest("Нужны контрагент и направление")

        target = data.get("next") or ""
        safe_next = target if url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}) else ""

        # одна кнопка: POST со статьёй — сразу правило «все платежи → статья»
        if request.method == "POST" and data.get("cf"):
            item = get_object_or_404(CFItem, pk=data["cf"])
            if item.children.exists() or not item.accepts(direction):
                messages.error(request, f"Статья «{item}» не подходит: нужна подстатья того же направления "
                                        f"или с флажком «Принимает возвраты»")
                return redirect(safe_next or reverse(self.changelist_url_name))
            resolver, _ = pin_cp(inn, name, direction)
            set_catch_all(resolver, item)
            resolve(resolver)
            resolver.refresh_from_db()
            messages.success(
                request,
                f"{resolver.name or resolver.key}: все платежи → {item}. "
                f"Строк {resolver.lines_count}, не разнесено {resolver.open_count} "
                f"(разнесённое руками не меняется).",
            )
            return redirect(safe_next or reverse("admin:treasury_cpresolver_change", args=[resolver.pk]))

        mode, value = cp_key(inn, name, shared_inns())
        key = inn if mode == "inn" else name_key(name)
        existing = Resolver.objects.filter(kind=ResolverKind.CP, key=key, direction=direction).first()
        tail = "?" + urlencode({"next": safe_next}) if safe_next else ""
        if existing:
            return redirect(reverse("admin:treasury_cpresolver_change", args=[existing.pk]) + tail)

        query = {"name": name[:200], "direction": direction}
        query.update({"inn": inn} if mode == "inn" else {"match_name": name[:300]})
        if safe_next:
            query["next"] = safe_next
        return redirect(reverse("admin:treasury_cpresolver_add") + "?" + urlencode(query))

    # ------------------------------------------------------------------
    # Дашборд «Контрагенты и статьи» переехал в dashboard (витрина)
    # ------------------------------------------------------------------

    def audit_redirect(self, request):
        query = request.META.get("QUERY_STRING", "")
        return redirect(reverse("admin:dashboard_cpaudit_changelist") + ("?" + query if query else ""))

    # ------------------------------------------------------------------

    @display(description="Контрагент", ordering="name")
    def cp_display(self, obj):
        how = "по названию" if obj.key.startswith(NAME_KEY_PREFIX) else f"ИНН {obj.key}"
        return Stack((obj.name or obj.key)[:80], how).html
