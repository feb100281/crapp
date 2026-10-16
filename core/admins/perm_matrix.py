"""
Матрица прав для групп и пользователей.

Разделы = приложения (verbose_name из apps.py), строки = модели,
колонки = Просмотр / Добавление / Изменение / Удаление.
Нестандартные права модели (например «запуск команды») — чипами под названием строки.
"""

from __future__ import annotations

from django import forms
from django.apps import apps
from django.contrib.auth.models import Permission

ACTIONS = [
    ("view", "Просмотр"),
    ("add", "Добавление"),
    ("change", "Изменение"),
    ("delete", "Удаление"),
]

# служебные разделы Django — прав на них никому раздавать не нужно
HIDDEN_APPS = {"admin", "contenttypes", "sessions"}
HIDDEN_MODELS = {("auth", "permission")}


class PermissionMatrixWidget(forms.SelectMultiple):
    template_name = "core/widgets/perm_matrix.html"

    def get_context(self, name, value, attrs):
        # без optgroups SelectMultiple — строим свою структуру
        context = forms.Widget.get_context(self, name, value, attrs)
        selected = {str(v) for v in (value or [])}

        qs = getattr(self.choices, "queryset", None)
        if qs is None:
            qs = Permission.objects.all()
        perms = qs.select_related("content_type")

        app_order = {cfg.label: i for i, cfg in enumerate(apps.get_app_configs())}
        sections: dict[str, dict] = {}

        for perm in perms:
            ct = perm.content_type
            if ct.app_label in HIDDEN_APPS or (ct.app_label, ct.model) in HIDDEN_MODELS:
                continue
            model = ct.model_class()
            if model is None:           # право от удалённой модели
                continue

            app_cfg = apps.get_app_config(ct.app_label)
            sec = sections.setdefault(ct.app_label, {
                "label": str(app_cfg.verbose_name),
                "order": app_order.get(ct.app_label, 999),
                "models": {},
            })
            model_order = [m._meta.model_name for m in app_cfg.get_models()]
            row = sec["models"].setdefault(ct.model, {
                "label": str(model._meta.verbose_name_plural).capitalize(),
                "order": model_order.index(ct.model) if ct.model in model_order else 999,
                "cells": {},
                "extra": [],
            })

            act = perm.codename.split("_", 1)[0]
            item = {
                "pk": perm.pk,
                "checked": str(perm.pk) in selected,
                "label": perm.name,
            }
            if perm.codename == f"{act}_{ct.model}" and act in dict(ACTIONS):
                item["act"] = act
                row["cells"][act] = item
            else:
                row["extra"].append(item)

        groups = []
        for sec in sorted(sections.values(), key=lambda s: (s["order"], s["label"])):
            models = []
            for row in sorted(sec["models"].values(), key=lambda r: (r["order"], r["label"])):
                row["cells"] = [row["cells"].get(code) for code, _ in ACTIONS]
                models.append(row)
            groups.append({"label": sec["label"], "models": models})

        context["widget"]["matrix"] = {"actions": ACTIONS, "groups": groups}
        return context
