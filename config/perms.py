"""Права на разделы: меню, вкладки и рабочий стол показывают только доступное пользователю."""

from __future__ import annotations


def can_view(user, model: str) -> bool:
    """model — «app.model»: просмотр или изменение (изменение в админке включает просмотр)."""
    app, name = model.split(".")
    return user.has_perm(f"{app}.view_{name}") or user.has_perm(f"{app}.change_{name}")


def view(model: str):
    """Колбэк для пункта меню Unfold: «permission»: view("treasury.statement")."""
    return lambda request: can_view(request.user, model)
