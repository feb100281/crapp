"""
Раскрывающаяся строка списка (Unfold list_sections) с подчинёнными записями:
статья ДДС → подстатьи, счёт плана → субсчета.

    class SubItemsSection(ChildrenSection):
        verbose_name = "Подстатьи"
        fields = ["code", "name_link", ...]

        def rows(self, instance):
            return instance.children.order_by("code")
"""

from __future__ import annotations

from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.sections import TableSection


class ChildrenSection(TableSection):
    related_name = "section_rows"
    empty_text = "Нет подчинённых записей"

    def __init__(self, request, instance):
        super().__init__(request, instance)
        instance.section_rows = self.rows(instance)

    def rows(self, instance):
        return instance.children.order_by("code")

    def render(self) -> str:
        if not self.instance.section_rows.exists():
            return mark_safe(f'<div class="pk-mini-note">{self.empty_text}</div>')
        return super().render()

    @staticmethod
    def change_link(obj, text):
        """Ссылка на карточку подчинённой записи."""
        meta = obj._meta
        url = reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, text)
