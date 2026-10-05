"""
Базовые классы админки приложения.

Все ModelAdmin проекта наследуются от AppModelAdmin — чтобы списки,
фильтры и формы вели себя одинаково во всех разделах.
"""

from __future__ import annotations

from django.db import models
from django_json_widget.widgets import JSONEditorWidget
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.forms.widgets import WysiwygWidget



class AppModelAdmin(ModelAdmin):
    # Списки
    list_fullwidth = True
    list_filter_sheet = False
    list_filter_submit = True
    list_per_page = 50

    # Формы
    warn_unsaved_form = True
    change_form_show_cancel_button = True

    formfield_overrides = {
        models.TextField: {
            "widget": WysiwygWidget,
        },
        models.JSONField: {
            "widget": JSONEditorWidget,
        },
    }


class AppTabularInline(TabularInline):
    tab = True
    extra = 0
    show_change_link = True


class AppStackedInline(StackedInline):
    tab = True
    extra = 0
    show_change_link = True
