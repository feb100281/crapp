"""
Клиент DaData (findById/party) и маппинг ответа на модель CP.

Логика разбора payload здесь должна совпадать с sql/bs/cp_init.sql:
массовая загрузка (init_cp) и точечное обновление из админки обязаны
давать одинаковый результат для одного и того же ИНН.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone as dt_timezone

import requests

from django.conf import settings
from django.utils import timezone

from ...models.cp_model import CP, CPStatus

DADATA_URL = (
    "https://suggestions.dadata.ru/"
    "suggestions/api/4_1/rs/findById/party"
)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 5

# Специально сильно ниже официальных 30 req/sec
REQUEST_DELAY = 0.20

# Организационно-правовые формы, которые переносим в конец названия:
# «ООО ДРИМ РИЭЛТИ» -> «ДРИМ РИЭЛТИ ООО»
OPF_PREFIXES = (
    "ООО",
    "АО",
    "ЗАО",
    "ОАО",
    "ПАО",
    "ИП",
)

QUOTES = '"«»\\'


class DaDataError(RuntimeError):
    pass


# ======================================================================
# Разбор
# ======================================================================


def normalize_name(value: str | None) -> str | None:
    """UPPER + без кавычек + ОПФ переносится в конец."""

    if not value:
        return None

    name = value

    for char in QUOTES:
        name = name.replace(char, "")

    name = " ".join(name.upper().split())

    if not name:
        return None

    parts = name.split(" ")

    if len(parts) > 1 and parts[0] in OPF_PREFIXES:
        name = " ".join(parts[1:] + [parts[0]])

    return name


def _ms_to_date(value) -> date | None:
    """DaData отдаёт даты как unix timestamp в миллисекундах."""

    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return None

    return datetime.fromtimestamp(
        stamp / 1000,
        tz=dt_timezone.utc,
    ).date()


def first_suggestion(payload) -> dict:
    if not isinstance(payload, dict):
        return {}

    suggestions = payload.get("suggestions") or []

    if not suggestions:
        return {}

    return suggestions[0] or {}


def extract_fields(payload, fallback_name: str | None = None) -> dict:
    """payload DaData -> словарь полей модели CP (без inn, ba и payload)."""

    suggestion = first_suggestion(payload)
    data = suggestion.get("data") or {}

    address = data.get("address") or {}
    address_data = address.get("data") or {}
    state = data.get("state") or {}
    management = data.get("management") or {}

    status = state.get("status") or CPStatus.NA

    if status not in CPStatus.values:
        status = CPStatus.NA

    return {
        "ogrn": data.get("ogrn"),
        "name": normalize_name(
            suggestion.get("value") or fallback_name
        ),
        "address": address.get("value"),
        "registration_date": _ms_to_date(
            state.get("registration_date")
        ),
        "manager_name": management.get("name"),
        "country": address_data.get("country"),
        "region": address_data.get("region"),
        "status": status,
    }


# ======================================================================
# HTTP
# ======================================================================


def get_api_key() -> str:
    api_key = getattr(settings, "DADATA_API_KEY", None)

    if not api_key:
        raise DaDataError(
            "Не задан DADATA_API_KEY (.env)"
        )

    return api_key


def build_session(api_key: str | None = None) -> requests.Session:
    session = requests.Session()

    session.headers.update({
        "Authorization": f"Token {api_key or get_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Cosmo/1.0",
    })

    return session


def fetch_party(
    inn: str,
    session: requests.Session | None = None,
    log=None,
) -> dict:
    """
    Запрос одного ИНН.

    Возвращает {"payload": ..., "status": int|None, "error": str|None}.
    """

    own_session = session is None

    if own_session:
        session = build_session()

    def write(text):
        if log:
            log(text)

    try:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = session.post(
                    DADATA_URL,
                    json={
                        "query": inn,
                        "branch_type": "MAIN",
                    },
                    timeout=REQUEST_TIMEOUT,
                )

                if response.status_code == 200:
                    return {
                        "payload": response.json(),
                        "status": 200,
                        "error": None,
                    }

                # Неверный ключ — продолжать бессмысленно
                if response.status_code in (401, 403):
                    raise DaDataError(
                        "DaData authorization error "
                        f"HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )

                # Лимит запросов
                if response.status_code == 429:
                    wait = min(2 ** attempt, 60)

                    write(
                        f"[{inn}] HTTP 429. "
                        f"Попытка {attempt}/{MAX_RETRIES}. "
                        f"Ждём {wait} сек."
                    )

                    time.sleep(wait)
                    continue

                # Временные ошибки сервера
                if 500 <= response.status_code < 600:
                    wait = min(2 ** attempt, 30)

                    write(
                        f"[{inn}] HTTP {response.status_code}. "
                        f"Попытка {attempt}/{MAX_RETRIES}. "
                        f"Ждём {wait} сек."
                    )

                    time.sleep(wait)
                    continue

                # Остальные 4xx не ретраим
                return {
                    "payload": None,
                    "status": response.status_code,
                    "error": response.text[:2000],
                }

            except requests.RequestException as exc:
                wait = min(2 ** attempt, 30)

                write(
                    f"[{inn}] Network error: {exc}. "
                    f"Попытка {attempt}/{MAX_RETRIES}."
                )

                if attempt < MAX_RETRIES:
                    time.sleep(wait)

        return {
            "payload": None,
            "status": None,
            "error": (
                f"Превышено количество попыток ({MAX_RETRIES})"
            ),
        }

    finally:
        if own_session:
            session.close()


# ======================================================================
# Обновление модели
# ======================================================================


def update_cp_from_dadata(
    cp: CP,
    session: requests.Session | None = None,
) -> bool:
    """
    Точечное обновление контрагента.

    True  — DaData нашла организацию, поля обновлены.
    False — организация не найдена (модель не трогаем).
    Ошибки поднимаются исключением.
    """

    result = fetch_party(cp.inn, session=session)

    if result["status"] != 200:
        raise DaDataError(
            f"HTTP {result['status']}: {result['error']}"
        )

    payload = result["payload"]

    if not first_suggestion(payload):
        return False

    fields = extract_fields(payload, fallback_name=cp.name)

    for field, value in fields.items():
        if value not in (None, ""):
            setattr(cp, field, value)

    cp.payload = payload
    cp.updated_at = timezone.now()

    cp.save(
        update_fields=[
            *fields.keys(),
            "payload",
            "updated_at",
        ]
    )

    return True
