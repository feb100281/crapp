"""
Раскладка папки выписок:

    БАНК / ВАЛЮТА СЧЁТ / ГОД / ГГГГ-ММ-ДД_ГГГГ-ММ-ДД[ пометка].расширение

Банки без выгрузки 1С (Локо, Сбер, Совкомбанк) — внутри «ВЫПИСКИ БЕЗ txt».
Одинаковые файлы — в «_ДУБЛИ», нераспознанные — в «_НЕ РАЗОБРАНО».
Каждое перемещение пишется в «_журнал раскладки.csv». Загрузка выписок
от путей не зависит: выписка и операции узнаются по содержимому.
"""

from __future__ import annotations

import csv
import hashlib
import re
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PDF_DIR = "ВЫПИСКИ БЕЗ txt"
PDF_OUT = "txt из PDF"
DUPS = "_ДУБЛИ"
UNKNOWN = "_НЕ РАЗОБРАНО"
JOURNAL = "_журнал раскладки.csv"
OTHER = "ПРОЧЕЕ"

PDF_ONLY = {"ЛОКО-БАНК", "СБЕРБАНК", "СОВКОМБАНК"}
# признак в названии банка, папки или тексте выписки → папка банка
BANKS = [(re.compile(rx), short) for rx, short in [
    (r"САНКТ-ПЕТЕРБУРГ|\bБСПБ", "БАНК САНКТ-ПЕТЕРБУРГ"),
    (r"ДАТА ПОСЛЕДНЕЙ ОПЕРАЦИИ", "БАНК САНКТ-ПЕТЕРБУРГ"),       # шапка PDF-выписки БСПБ
    (r"\bВТБ\b|\bVTB", "ВТБ"), (r"\bАЛЬФА|\bАБ\b", "АЛЬФА-БАНК"),
    (r"\bГПБ\b|\bГАЗПРОМ", "ГАЗПРОМБАНК"), (r"\bОТП\b", "ОТП БАНК"), (r"\bФИНАМ", "ФИНАМ"),
    (r"\bЛОКО|LOCKO", "ЛОКО-БАНК"), (r"\bСОВКОМ", "СОВКОМБАНК"), (r"\bСБЕР", "СБЕРБАНК"),
]]
CURRENCY = {"810": "RUB", "643": "RUB", "156": "CNY", "978": "EUR", "840": "USD", "356": "INR",
            "398": "KZT", "933": "BYN", "417": "KGS", "784": "AED", "949": "TRY", "051": "AMD"}
TAGS = [("докум", "документы"), ("подпис", "подпись"), ("applications", "заявления"),
        ("заявлен", "заявления"), ("справка", "справка")]

ACC = re.compile(r"(?<!\d)(40[5-8]\d{17}|423\d{17})(?!\d)")
D = r"(\d{2})[.\-/](\d{2})[.\-/](\d{4})"
RANGES = [
    re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{4})-(\d{2})-(\d{2})"),       # наше имя: 2026-10-03_2026-10-19
    re.compile(D + r"\s*(?:-|–|—|по|_)\s*" + D),                 # 01.01.2023–02.09.2026, с … по …
    re.compile(r"(\d{4})(\d{2})(\d{2})_(\d{4})(\d{2})(\d{2})"),   # 20261003_20261019 (год впереди)
]
SINGLE = [
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})"),                    # наше имя: 2026-10-02
    re.compile(r"(?<!\d)" + D + r"(?!\d)"),                       # 02.10.2026, 09-08-2023
    re.compile(r"(\d{4})\.(\d{2})\.(\d{2})"),                     # СберБизнес: 2024.02.06
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"),            # 20261002
]


@dataclass
class Info:
    bank: str | None = None
    account: str | None = None
    d1: date | None = None
    d2: date | None = None
    note: str = ""


def bank_short(name: str | None) -> str | None:
    up = (name or "").upper()
    for rx, short in BANKS:
        if rx.search(up):
            return short
    return None


def currency(account: str) -> str:
    return CURRENCY.get(account[5:8], account[5:8])


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _date(parts, year_first=False) -> date | None:
    try:
        y, m, d = (parts[0], parts[1], parts[2]) if year_first else (parts[2], parts[1], parts[0])
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def dates_in(text: str) -> tuple[date | None, date | None]:
    for i, rx in enumerate(RANGES):
        m = rx.search(text)
        if m:
            first = i != 1                                 # год впереди везде, кроме 01.01.2023–…
            return _date(m.groups()[:3], first), _date(m.groups()[3:], first)
    for i, rx in enumerate(SINGLE):
        m = rx.search(text)
        if m:
            day = _date(m.groups(), year_first=i != 1)
            if day:
                return day, day
    return None, None


def make_dirs(root: Path, folder: Path) -> None:
    """mkdir с исправлением регистра: на Mac «ОТП банк» и «ОТП БАНК» — одна папка."""
    current = root
    for part in folder.relative_to(root).parts:
        wanted = current / part
        if current.is_dir():
            twin = next((c for c in current.iterdir()
                         if c.name.casefold() == part.casefold() and c.name != part), None)
            if twin is not None:
                tmp = current / f".{part}.tmp"
                twin.rename(tmp)
                tmp.rename(wanted)
        wanted.mkdir(exist_ok=True)
        current = wanted


def pdf_text(path: Path) -> str:
    """Текст первых двух страниц: pdfplumber, иначе pdftotext."""
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages[:2])
    except ImportError:
        try:
            return subprocess.run(["pdftotext", "-l", "2", str(path), "-"], capture_output=True,
                                  text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    except Exception:  # noqa: BLE001 — битый PDF разбираем по имени файла
        return ""


def read_1c(path: Path) -> Info:
    raw = path.read_bytes()
    text = raw.decode("cp1251", errors="replace") if b"1CClientBankExchange" in raw[:200] else \
        raw.decode("utf-8", errors="replace")
    accounts, starts, ends = [], [], []
    for line in text.splitlines():
        key, _, value = line.partition("=")
        value = value.strip()
        if key == "РасчСчет" and value and value not in accounts:
            accounts.append(value)
        elif key == "ДатаНачала" and value:
            starts.append(_date(value.split(".")))
        elif key == "ДатаКонца" and value:
            ends.append(_date(value.split(".")))
    starts, ends = [d for d in starts if d], [d for d in ends if d]
    return Info(account=accounts[0] if accounts else None,
                d1=min(starts) if starts else None, d2=max(ends) if ends else None,
                note="несколько счетов в файле" if len(accounts) > 1 else "")


def pick_account(found: list[str], known: dict) -> str | None:
    ours = [a for a in found if a in known]
    return (ours or found or [None])[0]


def identify(path: Path, rel: Path, known: dict[str, str]) -> Info:
    ext = path.suffix.lower()
    if ext == ".txt":
        info = read_1c(path)
    else:
        info = Info()
        info.account = pick_account(ACC.findall(path.name), known)
        info.d1, info.d2 = dates_in(path.name)
        body = ""
        if ext == ".pdf" and (not info.account or not info.d1):
            body = pdf_text(path)
        elif ext in (".xlsx", ".zip") and not info.account:
            try:
                with zipfile.ZipFile(path) as z:
                    names = z.namelist()
                    body = " ".join(names)
                    for n in names:
                        if n.endswith("sharedStrings.xml"):
                            body += " " + z.read(n).decode("utf-8", errors="ignore")
            except zipfile.BadZipFile:
                pass
        if body:                                           # в тексте — только наши счета
            info.account = info.account or next((a for a in ACC.findall(body) if a in known), None)
            if not info.d1:
                info.d1, info.d2 = dates_in(body)
        info.bank = bank_short(body[:400])
    info.bank = (bank_short(known.get(info.account)) if info.account in known else None) \
        or info.bank or bank_short(" ".join(rel.parts[:-1])) or bank_short(path.name)
    if info.bank and not info.account:                     # у банка один счёт — значит, он
        same = [a for a, b in known.items() if bank_short(b) == info.bank]
        if len(same) == 1:
            info.account = same[0]
    return info


def tag(name: str) -> str:
    low = name.lower()
    words = []
    for key, word in TAGS:
        if key in low and word not in words:
            words.append(word)
    return (" " + " ".join(words)) if words else ""


def target_for(root: Path, path: Path, info: Info) -> Path:
    ext = path.suffix.lower()
    if not info.bank:
        return root / UNKNOWN / path.name
    base = root / PDF_DIR if info.bank in PDF_ONLY else root
    folder = base / info.bank
    if not info.account:
        return folder / ("АРХИВЫ" if ext == ".zip" else OTHER) / path.name
    folder = folder / f"{currency(info.account)} {info.account}"
    if not info.d1:
        return folder / OTHER / path.name
    span = f"{info.d1:%Y-%m-%d}" if info.d1 == info.d2 else f"{info.d1:%Y-%m-%d}_{info.d2:%Y-%m-%d}"
    return folder / f"{info.d2:%Y}" / month_dir(info.d2) / f"{span}{tag(path.name)}{ext}"


MONTH_NAMES = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
               "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def month_dir(d: date) -> str:
    """Папка месяца по дате окончания выписки: «03. Март»."""
    return f"{d.month:02d}. {MONTH_NAMES[d.month - 1]}"


SHORTS = {short for _, short in BANKS}
ACC_DIR = re.compile(r"^[A-Z0-9]{3} \d{20}$")


OUR_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _placed(parts: tuple) -> bool:
    """
    Файл уже разложен — повторно не трогаем: БАНК / ВАЛЮТА СЧЁТ / ГОД / ММ. Месяц / ГГГГ-ММ-ДД….
    Файл, брошенный вручную в папку банка или счёта, раскладывается заново.
    """
    if parts and parts[0] == PDF_DIR:
        parts = parts[1:]
    if len(parts) == 3 and parts[0] in SHORTS and parts[1] in (OTHER, "АРХИВЫ"):
        return True
    return (len(parts) == 5 and parts[0] in SHORTS and ACC_DIR.match(parts[1]) is not None
            and re.fullmatch(r"\d{4}", parts[2]) is not None and MONTH_DIR.match(parts[3]) is not None
            and OUR_NAME.match(parts[4]) is not None)


MONTH_DIR = re.compile(r"^\d{2}\. \S+$")


def regroup_months(root: Path, log=print) -> int:
    """
    Переход на папки месяцев: файлы старой раскладки БАНК / СЧЁТ / ГОД / файл
    переносятся в ГОД / «ММ. Месяц» по последней дате в имени. Ничего не перезаписывает.
    """
    root, moved = Path(root), 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if parts and parts[0] == PDF_DIR:
            parts = parts[1:]
        if not (len(parts) == 4 and parts[0] in SHORTS and ACC_DIR.match(parts[1])
                and re.fullmatch(r"\d{4}", parts[2]) and OUR_NAME.match(parts[3])):
            continue
        last = re.findall(r"\d{4}-\d{2}-\d{2}", parts[3])[-1]
        target = path.parent / month_dir(date.fromisoformat(last)) / path.name
        if target.exists():
            continue
        make_dirs(root, target.parent)
        path.rename(target)
        moved += 1
    if moved:
        log(f"По папкам месяцев перенесено файлов: {moved}")
    return moved


def _skip(rel: Path) -> bool:
    parts = rel.parts
    return (rel.name.startswith(".") or rel.name == JOURNAL or parts[0] == DUPS
            or parts[:2] == (PDF_DIR, PDF_OUT) or _placed(parts))


def _sort_one(root, path, known, apply, log, stats, periods, taken, rows, vacated) -> None:
    rel = path.relative_to(root)
    info = identify(path, rel, known)
    if info.account and info.d1 and path.suffix.lower() == ".txt":
        periods[(path.parent, info.account)] = (info.d1, info.d2)
    elif info.account and not info.d1 and (path.parent, info.account) in periods:
        info.d1, info.d2 = periods[(path.parent, info.account)]
    target = target_for(root, path, info)
    digest = sha1(path)
    if target == path:
        stats["in_place"] += 1
        taken[target] = digest
        return

    reason = "раскладка" + (f" ({info.note})" if info.note else "")
    stem, ext, n = target.stem, target.suffix, 2
    while True:
        owner = taken.get(target) or (sha1(target) if target.exists() else None)
        if owner is None:
            break
        if owner == digest:                            # точная копия уже лежит на месте
            reason = "дубль: " + str(target.relative_to(root))
            target, k = root / DUPS / rel, 2
            while target.exists() or target in taken:
                target = (root / DUPS / rel).with_name(f"{rel.stem} ({k}){rel.suffix}")
                k += 1
            break
        target = target.with_name(f"{stem} ({n}){ext}")
        n += 1
    taken[target] = digest
    if target.parts[len(root.parts)] == DUPS:
        stats["dups"] += 1
    elif target.parts[len(root.parts)] == UNKNOWN:
        stats["unknown"] += 1
    else:
        stats["moved"] += 1
    rows.append((str(rel), str(target.relative_to(root)), reason))
    log(f"  {rel}  →  {target.relative_to(root)}" + ("" if reason == "раскладка" else f"   [{reason}]"))
    if apply:
        make_dirs(root, target.parent)
        path.rename(target)
        vacated.add(path.parent)


def sort_folder(root: Path, known: dict[str, str], apply: bool = True, log=print) -> dict:
    """known — номер счёта → название банка. apply=False — только показать план."""
    root = Path(root)
    if apply:
        regroup_months(root, log)
    files = [p for p in sorted(root.rglob("*")) if p.is_file() and not _skip(p.relative_to(root))]
    # сначала txt: по ним PDF той же папки узнают свой период
    files.sort(key=lambda p: (p.suffix.lower() != ".txt", str(p)))
    stats = {"files": len(files), "in_place": 0, "moved": 0, "dups": 0, "unknown": 0}
    periods: dict[tuple[Path, str], tuple[date, date]] = {}
    taken: dict[Path, str] = {}
    rows = []
    vacated: set[Path] = set()

    for path in files:
        try:
            _sort_one(root, path, known, apply, log, stats, periods, taken, rows, vacated)
        except OSError as exc:                  # файл не скачан из облака и т.п. — идём дальше
            stats["skipped"] = stats.get("skipped", 0) + 1
            log(f"  ! {path.relative_to(root)}: пропущен ({exc.strerror or exc}) — разложится при следующем запуске")


    if apply:
        if rows:
            journal = root / JOURNAL
            new = not journal.exists()
            with journal.open("a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                if new:
                    w.writerow(["когда", "было", "стало", "почему"])
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                w.writerows([now, *r] for r in rows)
        stats["empty_dirs"] = remove_empty(root, vacated)
    log(f"Файлов: {stats['files']}, уже на месте: {stats['in_place']}, разложено: {stats['moved']}, "
        f"дублей: {stats['dups']}, не разобрано: {stats['unknown']}"
        + (f", пропущено (не скачаны): {stats['skipped']}" if stats.get("skipped") else "")
        + ("" if apply else "  — пробный прогон, файлы не перемещались"))
    return stats


def remove_empty(root: Path, vacated: set[Path]) -> int:
    """
    Удаляет только папки, из которых раскладка сама унесла файлы, и их опустевших
    родителей. Остальные папки не трогает: в облачной папке (Nextcloud) ещё не
    скачанная папка выглядит пустой, и её удаление стирает файлы на сервере.
    """
    root = Path(root)
    keep = {root, root / PDF_DIR, root / PDF_DIR / PDF_OUT}
    dirs: set[Path] = set()
    for d in vacated:
        while d != root and root in d.parents:
            dirs.add(d)
            d = d.parent
    removed = 0
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        if d in keep or not d.is_dir():
            continue
        children = list(d.iterdir())
        if any(c.name != ".DS_Store" for c in children):
            continue
        try:
            for c in children:
                c.unlink()
            d.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


def known_accounts() -> dict[str, str]:
    """Счета из справочника: номер → банк."""
    from treasury.models.ba_model import BankAccount

    return {a.number: (a.bank.name if a.bank else "") for a in BankAccount.objects.select_related("bank")}


def backup_folder(src: Path, dst: Path, log=print) -> dict:
    """
    Резервная копия: файлы из src, которых нет в dst, копируются с той же структурой папок.
    В dst ничего не удаляется и не перезаписывается: другой файл под тем же именем
    сохраняется рядом с пометкой «(копия ГГГГ-ММ-ДД)». Сравнение по пути и размеру —
    без чтения файлов, чтобы не скачивать весь облачный архив при каждом запуске.
    Служебные папки и файлы на «_» и готовые txt из PDF не копируются.
    """
    import shutil

    src, dst = Path(src), Path(dst)
    if dst.exists():
        regroup_months(dst, log)
    stats = {"copied": 0, "renamed": 0, "same": 0}
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if (not path.is_file() or rel.name.startswith(".")
                or any(part.startswith("_") for part in rel.parts) or rel.parts[:2] == (PDF_DIR, PDF_OUT)):
            continue
        target = dst / rel
        if target.exists():
            if target.stat().st_size == path.stat().st_size:
                stats["same"] += 1
                continue
            target = target.with_name(f"{target.stem} (копия {date.today():%Y-%m-%d}){target.suffix}")
            if target.exists():
                stats["same"] += 1
                continue
            stats["renamed"] += 1
        try:
            make_dirs(dst, target.parent)
            shutil.copy2(path, target)
        except OSError as exc:                  # не скачан из облака — скопируется в следующий раз
            log(f"  ! резерв: {rel} пропущен ({exc.strerror or exc})")
            continue
        stats["copied"] += 1
        log(f"  → резерв: {rel}" + ("" if target.name == rel.name else f"  (как {target.name})"))
    log(f"Резервная копия: скопировано {stats['copied']} (из них под новым именем {stats['renamed']}), "
        f"уже были {stats['same']}")
    return stats
