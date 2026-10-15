"""
Названия контрагентов для отчётов.

cp_cp.title   — «Название для отчётов»: пустое заполняется автоматически
                (короткая форма наименования), исправленное руками не трогаем.
cp_name_title — raw → title: очищенные названия из выписок для строк без
                карточки (физлица с ИНН банка, без ИНН).
Обе пересобираются перед расчётом ДДС.
"""

from __future__ import annotations

import re

from django.db import connection, transaction

TABLE = "cp_name_title"

_SPACES = re.compile(r"\s+")
_LEAD_INN = re.compile(r"^ИНН\s*(\d{10,12})?[,\s]*", re.I)
_LEAD_ID = re.compile(r"^(ИНН|УНП)\s*\d+[,\s]*", re.I)
_PAYER = re.compile(r"\bПлательщик\s+(.+)", re.I)
# дальше начинаются адрес, счета, документы
_CUT = re.compile(
    r"//|\s+р/с\b|\s+счет\b|\s+сч\.|\s+ИНН\s*\d|\s+П-Т\b|\s+П\.\s|,?\s*ADD\.|"
    r"\s+Г\.|\s+Г\s|\s+УЛ\.|\s+ПЕР\.|\s+АВЕНЮ\b|\s+БЛР\b|\s+РБ\b|\s+UL\.|\s+DR\.\s",
    re.I,
)
# иностранная компания — до правовой формы включительно
_LEGAL = re.compile(
    r"^(.+?\b(?:CO\.?,?\s*LTD\.?|CO\.?,?\s*LIMITED|COMPANY\s+LIMITED|PVT\.?\s*LTD\.?|"
    r"LIMITED|LTD\.?|INC\.?|LLC|GMBH|S\.?R\.?L\.?))(?=\s|,|$)",
    re.I,
)
_LATIN = re.compile(r"[A-Za-z]")
_CYR = re.compile(r"[А-Яа-яЁё]")


def clean_title(raw: str | None) -> str:
    s = _SPACES.sub(" ", str(raw or "")).strip()
    if not s:
        return ""
    s = _LEAD_INN.sub("", s)
    m = _PAYER.search(s)
    if m:
        s = m.group(1)
    s = _LEAD_ID.sub("", s)
    s = _CUT.split(s, maxsplit=1)[0]
    if _LATIN.search(s) and not _CYR.search(s):
        m = _LEGAL.match(s)
        if m:
            s = m.group(1)
        else:
            s = s.split(",", 1)[0]
            words = []
            for word in s.split(" "):                     # адрес начинается с цифр
                if re.search(r"\d", word) or word.upper() in ("PLOT", "NO", "NO.", "NEAR"):
                    break
                words.append(word)
            s = " ".join(words) or s
    s = s.strip(" ,.;:-")
    return s.upper() if s else _SPACES.sub(" ", str(raw)).strip().upper()


_OPF = (
    ("ФИЛИАЛ КОМПАНИИ С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ", "ФИЛИАЛ КОО"),
    ("КОМПАНИЯ С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ", "КОО"),
    ("ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ", "ООО"),
    ("НЕПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО", "АО"),
    ("ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО", "ПАО"),
    ("ЗАКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО", "ЗАО"),
    ("ОТКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО", "ОАО"),
    ("АКЦИОНЕРНОЕ ОБЩЕСТВО", "АО"),
    ("ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ", "ИП"),
    ("СПЕЦИАЛИЗИРОВАННЫЙ ЗАСТРОЙЩИК", "СЗ"),
)
_LEAD_OPF = ("ООО", "АО", "ЗАО", "ОАО", "ПАО", "ИП", "КОО")


def short_name(name: str | None) -> str:
    """Полные правовые формы → сокращения, форма в конце: «СТРОЙКРАФТ ООО»."""
    s = _SPACES.sub(" ", str(name or "").replace('"', "").replace("«", "").replace("»", "")).strip().upper()
    for full, short in _OPF:
        s = s.replace(full, short)
    parts = s.split(" ")
    if len(parts) > 1 and parts[0] in _LEAD_OPF:
        s = " ".join(parts[1:] + parts[:1])
    return s


def auto_title(cp) -> str:
    """Карточка DaData — короткая форма наименования; не найден — очищенное название из выписки."""
    return short_name(cp.name) if cp.updated_at else clean_title(cp.name)


def fill_titles() -> int:
    """Заполняет пустые «Названия для отчётов». Исправленные руками остаются."""
    from ...models.cp_model import CP

    todo = list(CP.objects.filter(title="").exclude(name__isnull=True).exclude(name=""))
    for cp in todo:
        cp.title = auto_title(cp)[:255]
    CP.objects.bulk_update(todo, ["title"], batch_size=500)
    return len(todo)


def _ensure(cur) -> None:
    cur.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (raw TEXT PRIMARY KEY, title TEXT)")


def refresh_titles(full: bool = True) -> int:
    """full — пересчитать все; иначе только названия, которых ещё нет. Возвращает число строк."""
    fill_titles()
    with transaction.atomic(), connection.cursor() as cur:
        _ensure(cur)
        if full:
            cur.execute(f"DELETE FROM {TABLE}")
        cur.execute(
            f"SELECT DISTINCT cp_name FROM treasury_bsline WHERE cp_name IS NOT NULL "
            f"AND cp_name NOT IN (SELECT raw FROM {TABLE})"
        )
        raws = [r[0] for r in cur.fetchall()]
        cur.executemany(f"INSERT INTO {TABLE} (raw, title) VALUES (%s, %s)",
                        [(r, clean_title(r)) for r in raws])
    return len(raws)
