"""
Карточка «сырых» данных DaData для страницы контрагента.

Модель хранит полный ответ в payload — здесь он раскладывается
в читаемые блоки. Ничего не вычисляем: только показываем то,
что реально пришло от источника.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

EMPTY = mark_safe('<div class="pk-dd-empty">Данные DaData ещё не загружены</div>')

TYPE_LABELS = {
    "LEGAL": "Юридическое лицо",
    "INDIVIDUAL": "Индивидуальный предприниматель",
}

BRANCH_LABELS = {
    "MAIN": "Головная организация",
    "BRANCH": "Филиал",
}

STATUS_LABELS = {
    "ACTIVE": "Действующая",
    "LIQUIDATING": "Ликвидируется",
    "LIQUIDATED": "Ликвидирована",
    "BANKRUPT": "Банкротство",
    "REORGANIZING": "Реорганизация",
}


def _date(value) -> str | None:
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return None

    return datetime.fromtimestamp(
        stamp / 1000,
        tz=dt_timezone.utc,
    ).strftime("%d.%m.%Y")


def _money(value) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None

    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def _join(parts) -> SafeString:
    """str.join теряет пометку safe, поэтому склеиваем явно."""

    return mark_safe(
        "".join(str(part) for part in parts if part)
    )


def _item(label, value, mono: bool = False) -> SafeString:
    if value in (None, "", []):
        return mark_safe("")

    return format_html(
        '<div class="pk-dd-item">'
        '<div class="pk-dd-label">{}</div>'
        '<div class="pk-dd-value{}">{}</div>'
        "</div>",
        label,
        " pk-mono" if mono else "",
        value,
    )


def _section(title, *items) -> SafeString:
    body = _join(items)

    if not body:
        return mark_safe("")

    return format_html(
        '<div class="pk-dd-section">'
        '<div class="pk-dd-title">{}</div>'
        '<div class="pk-dd-grid">{}</div>'
        "</div>",
        title,
        body,
    )


def dadata_panel(payload) -> SafeString:
    """payload DaData -> HTML-карточка."""

    if not isinstance(payload, dict):
        return EMPTY

    suggestions = payload.get("suggestions") or []

    if not suggestions:
        return EMPTY

    suggestion = suggestions[0] or {}
    data = suggestion.get("data") or {}

    name = data.get("name") or {}
    address = data.get("address") or {}
    address_data = address.get("data") or {}
    state = data.get("state") or {}
    management = data.get("management") or {}
    opf = data.get("opf") or {}
    okved_full = (data.get("okveds") or [])
    capital = data.get("capital") or {}
    finance = data.get("finance") or {}

    okved_name = None

    for item in okved_full:
        if item.get("main"):
            okved_name = item.get("name")
            break

    blocks = [
        _section(
            "Идентификация",
            _item("ИНН", data.get("inn"), mono=True),
            _item("КПП", data.get("kpp"), mono=True),
            _item("ОГРН", data.get("ogrn"), mono=True),
            _item("Дата ОГРН", _date(data.get("ogrn_date"))),
            _item("Тип", TYPE_LABELS.get(data.get("type"), data.get("type"))),
            _item(
                "Подразделение",
                BRANCH_LABELS.get(
                    data.get("branch_type"),
                    data.get("branch_type"),
                ),
            ),
            _item("ОПФ", opf.get("short") or opf.get("full")),
        ),
        _section(
            "Наименование",
            _item("Краткое", name.get("short_with_opf") or name.get("short")),
            _item("Полное", name.get("full_with_opf") or name.get("full")),
            _item("Латиницей", name.get("latin")),
        ),
        _section(
            "Статус",
            _item(
                "Состояние",
                STATUS_LABELS.get(
                    state.get("status"),
                    state.get("status"),
                ),
            ),
            _item("Регистрация", _date(state.get("registration_date"))),
            _item("Ликвидация", _date(state.get("liquidation_date"))),
            _item("Актуальность", _date(state.get("actuality_date"))),
        ),
        _section(
            "Адрес",
            _item("Юридический адрес", address.get("value")),
            _item("Индекс", address_data.get("postal_code"), mono=True),
            _item("Страна", address_data.get("country")),
            _item("Регион", address_data.get("region_with_type")),
            _item("Город", address_data.get("city_with_type")),
        ),
        _section(
            "Руководство",
            _item("Руководитель", management.get("name")),
            _item("Должность", management.get("post")),
            _item("С даты", _date(management.get("start_date"))),
        ),
        _section(
            "Деятельность",
            _item("ОКВЭД", data.get("okved"), mono=True),
            _item("Вид деятельности", okved_name),
            _item("Всего кодов ОКВЭД", len(okved_full) or None),
            _item("Сотрудников", data.get("employee_count")),
            _item("Уставный капитал", _money(capital.get("value"))),
            _item("Налоговый режим", (finance.get("tax_system") or None)),
        ),
    ]

    body = _join(blocks)

    if not body:
        return EMPTY

    return format_html('<div class="pk-dd">{}</div>', body)
