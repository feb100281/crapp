import re
from functools import lru_cache

from django.apps import AppConfig
from django.db.backends.signals import connection_created


@lru_cache(maxsize=512)
def _like_regex(pattern: str, escape: str | None):
    """LIKE-шаблон SQL → регулярка (% — любая строка, _ — один символ)."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if escape and ch == escape and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def _like(pattern, value, escape=None):
    if pattern is None or value is None:
        return None
    return _like_regex(str(pattern), escape).fullmatch(str(value)) is not None


def _unicode_like(sender, connection, **kwargs):
    # SQLite сравнивает без учёта регистра только латиницу: поиск «альфа»
    # не находит «АЛЬФА». Подменяем LIKE — поиск в админке ищет и по-русски.
    if connection.vendor == "sqlite":
        connection.connection.create_function("like", 2, _like, deterministic=True)
        connection.connection.create_function("like", 3, _like, deterministic=True)


class CoreConfig(AppConfig):
    name = 'core'
    verbose_name = 'Команды'

    def ready(self):
        connection_created.connect(_unicode_like, dispatch_uid="core_unicode_like")
