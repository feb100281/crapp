"""Предупреждение в «Командах»: что не разложилось и не загрузилось при последнем запуске «Выписок»."""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.utils.html import format_html, format_html_join

LOG = Path(settings.MEDIA_ROOT) / "jobs" / "logs" / "bs.log"
SKIPPED = re.compile(r"^\s*! (?:резерв: )?(.+?): пропущен")
FAILED = re.compile(r"^ERROR: (.+)$")


def _problems() -> list[str]:
    if not LOG.exists():
        return []
    items = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if m := FAILED.match(line):
            items.append(f"не загружен: {m.group(1)}")
        elif m := SKIPPED.match(line):
            items.append(f"не разложен: {m.group(1)}")
        elif "ВНИМАНИЕ: раскладка не выполнена" in line or "ВНИМАНИЕ: резервная копия не сделана" in line:
            items.append(line.strip().removeprefix("ВНИМАНИЕ: "))
    return list(dict.fromkeys(items))


def _leftovers() -> list[str]:
    """Папки и файлы в корне источника, которые раскладка так и не забрала."""
    from treasury.services.bs_sort import DUPS, JOURNAL, PDF_DIR, SHORTS

    from ..models.jobs import Jobs

    job = Jobs.objects.filter(command="bs").first()
    source = (job.param or {}).get("source") if job else None
    if not source or not Path(source).expanduser().is_dir():
        return []
    known = SHORTS | {PDF_DIR, DUPS, JOURNAL}           # «_НЕ РАЗОБРАНО» тоже показываем
    return sorted(p.name for p in Path(source).expanduser().iterdir()
                  if not p.name.startswith(".") and p.name not in known)


def statements_hint(request) -> None:
    problems = _problems()
    left = [f"лежит неразобранным: {name}" for name in _leftovers()]
    items = list(dict.fromkeys(problems + left))
    if not items:
        return
    messages.warning(request, format_html(
        '<details><summary style="cursor:pointer">Выписки: при последнем запуске не всё разложено '
        'или загружено ({}). <span style="text-decoration:underline">Показать</span></summary>'
        '<ul style="margin:6px 0 0 18px;list-style:disc">{}</ul>'
        '<div style="margin-top:6px">Чаще всего файл ещё не скачан из Nextcloud: дождитесь синхронизации '
        'и запустите «Выписки» ещё раз.</div></details>',
        len(items), format_html_join("", "<li>{}</li>", ((i,) for i in items[:15]))))
