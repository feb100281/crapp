"""
Бэйджи для @display-полей админки.

Вся визуальная часть живёт в static/css/input.css (классы .pk-badge*),
здесь только сборка разметки. Правило проекта: никакого «инлайнового»
Tailwind внутри Python — только семантические pk-классы.
"""

from __future__ import annotations

from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

EMPTY = mark_safe('<span class="pk-field-sub">—</span>')

# Допустимые стили бэйджа (см. .pk-badge-* в input.css)
STYLES = (
    "primary",
    "success",
    "warning",
    "danger",
    "info",
    "gray",
)


def icon_html(icon: str | None, css: str = "pk-badge-icon") -> SafeString:
    """Material Symbols иконка (иконочный шрифт подключает Unfold)."""

    if not icon:
        return mark_safe("")

    return format_html(
        '<span class="material-symbols-outlined {}">{}</span>',
        css,
        icon,
    )


class Badge:
    """
    Базовый бэйдж.

        Badge("Не обновлён", "error", "danger").badge
    """

    def __init__(
        self,
        label=None,
        icon: str | None = None,
        style: str = "gray",
        title: str | None = None,
        extra_class: str = "",
    ) -> None:
        self.label = label
        self.icon = icon
        self.style = style
        self.title = title
        self.extra_class = extra_class

    # ------------------------------------------------------------------
    # Служебное
    # ------------------------------------------------------------------

    @property
    def css(self) -> str:
        style = self.style if self.style in STYLES else "gray"

        return " ".join(
            part
            for part in (
                "pk-badge",
                f"pk-badge-{style}",
                self.extra_class,
            )
            if part
        )

    # ------------------------------------------------------------------
    # Рендер
    # ------------------------------------------------------------------

    @property
    def badge(self) -> SafeString:
        if self.label in (None, ""):
            return EMPTY

        return format_html(
            '<span class="{}" title="{}">{}<span>{}</span></span>',
            self.css,
            self.title or "",
            icon_html(self.icon),
            self.label,
        )

    def __str__(self) -> str:
        return self.badge


class ChoiceBadge(Badge):
    """
    Бэйдж для поля с choices.

        ChoiceBadge(obj, "status", MAP).badge

    MAP: {value: (icon, style)}
    """

    def __init__(self, obj, field: str, mapping: dict, **kwargs) -> None:
        value = getattr(obj, field, None)

        icon, style = mapping.get(value, (None, "gray"))

        label = None

        getter = getattr(obj, f"get_{field}_display", None)

        if getter:
            label = getter()
        elif value:
            label = value

        kwargs.setdefault("icon", icon)
        kwargs.setdefault("style", style)

        super().__init__(label=label, **kwargs)


class TimeBadge(Badge):
    """
    Бэйдж «когда это было».

        a = TimeBadge(dt)
        a.icon = "check_circle"
        a.style = "success"
        return a.related_time_badge
    """

    def __init__(
        self,
        value=None,
        icon: str | None = "schedule",
        style: str = "gray",
        empty_label: str = "Нет данных",
        **kwargs,
    ) -> None:
        self.value = value
        self.empty_label = empty_label

        super().__init__(
            label=None,
            icon=icon,
            style=style,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Расчёты
    # ------------------------------------------------------------------

    @property
    def local(self):
        if not self.value:
            return None

        if timezone.is_aware(self.value):
            return timezone.localtime(self.value)

        return self.value

    @property
    def days(self) -> int | None:
        local = self.local

        if not local:
            return None

        return abs(
            (timezone.localdate() - local.date()).days
        )

    @property
    def exact(self) -> str:
        local = self.local

        if not local:
            return ""

        if local.hour or local.minute:
            return local.strftime("%d.%m.%Y %H:%M")

        return local.strftime("%d.%m.%Y")

    @property
    def human(self) -> str:
        days = self.days

        if days is None:
            return self.empty_label

        if days == 0:
            return "сегодня"

        if days == 1:
            return "вчера"

        if days < 30:
            return f"{days} дн. назад"

        if days < 365:
            months = max(days // 30, 1)
            return f"{months} мес. назад"

        years = days // 365
        return f"{years} г. назад"

    # ------------------------------------------------------------------
    # Рендер
    # ------------------------------------------------------------------

    @property
    def time_badge(self) -> SafeString:
        self.label = self.human
        self.title = self.exact

        return self.badge

    @property
    def related_time_badge(self) -> SafeString:
        """Бэйдж с относительным временем + точная дата подписью снизу."""

        if not self.value:
            return Badge(
                self.empty_label,
                self.icon,
                self.style,
            ).badge

        return format_html(
            '<span class="pk-time-badge">{}<span class="pk-field-sub">{}</span></span>',
            self.time_badge,
            self.exact,
        )
