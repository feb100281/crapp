"""
PDF-выписки → файлы 1CClientBankExchange (.txt), которые читает команда bs.

Поддержаны форматы: КБ «ЛОКО-Банк» (рубли и валюта), СберБизнес, Совкомбанк.
Одинаковые PDF (в т.ч. из zip-архивов) обрабатываются один раз.
Для каждой выписки сверяем обороты с итогами банка; расхождение — в отчёте.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber

OUR_INN = "7731432601"

VO_TYPES = {
    "01": "Платежное поручение",
    "02": "Платежное требование",
    "06": "Инкассовое поручение",
    "09": "Мемориальный ордер",
    "16": "Платежный ордер",
    "17": "Банковский ордер",
}

MONTHS = {m: i for i, m in enumerate(
    ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
     "августа", "сентября", "октября", "ноября", "декабря"], 1)}

DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
AMOUNT_RE = re.compile(r"-?\d{1,3}(?:[  ]\d{3})*(?:[.,]\d{2})")


# ------------------------------------------------------------------ модель

@dataclass
class Op:
    day: date
    number: str
    amount: Decimal
    incoming: bool                 # True — поступление на наш счёт
    cp_name: str = ""
    cp_inn: str = ""
    cp_account: str = ""
    cp_bic: str = ""
    cp_bank: str = ""
    purpose: str = ""
    vo: str = ""


@dataclass
class Statement:
    bank: str
    account: str
    currency: str
    date_from: date
    date_to: date
    opening: Decimal
    closing: Decimal
    total_in: Decimal | None = None
    total_out: Decimal | None = None
    our_name: str = 'ООО "КОСМО"'
    our_inn: str = OUR_INN
    ops: list[Op] = field(default_factory=list)

    def check(self) -> list[str]:
        got_in = sum((o.amount for o in self.ops if o.incoming), Decimal(0))
        got_out = sum((o.amount for o in self.ops if not o.incoming), Decimal(0))
        errors = []
        if self.total_in is not None and got_in != self.total_in:
            errors.append(f"поступления {got_in} ≠ {self.total_in} по итогам банка")
        if self.total_out is not None and got_out != self.total_out:
            errors.append(f"списания {got_out} ≠ {self.total_out} по итогам банка")
        if self.opening + got_in - got_out != self.closing:
            errors.append(f"остаток {self.opening} + {got_in} − {got_out} ≠ {self.closing}")
        return errors


# ------------------------------------------------------------------ помощники

def num(text: str) -> Decimal:
    m = AMOUNT_RE.search(text or "")
    if not m:
        return Decimal(0)
    return Decimal(m.group(0).replace(" ", "").replace(" ", "").replace(",", "."))


def one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def d(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def ru_date(text: str) -> date:
    day, month, year = text.split()[:3]
    return date(int(year), MONTHS[month.lower()], int(day))


def tables(pdf, stop_marker: str | None = None):
    """Строки всех таблиц подряд; после stop_marker (итоги выписки) — конец."""
    for page in pdf.pages:
        for table in page.extract_tables():
            for row in table:
                cells = [c or "" for c in row]
                yield cells
                if stop_marker and cells[0].startswith(stop_marker):
                    return


def search(pattern: str, text: str, group: int = 1, default: str = "") -> str:
    m = re.search(pattern, text, re.I)
    return m.group(group).strip() if m else default


# ------------------------------------------------------------------ ЛОКО-Банк

def parse_loko(pdf, text: str) -> Statement:
    account, currency = re.search(r"сч[её]т:\s*(\d{20})\s*\((\w{3})\)", text, re.I).groups()
    period = re.search(r"Выписка по счету с (\S+) по (\S+)", text)

    def head(label: str) -> Decimal:
        return num(search(rf"{label}\s+(\S[^\n]*)", text))

    st = Statement(
        bank="Локо", account=account, currency=currency,
        date_from=d(period.group(1)), date_to=d(period.group(2)),
        opening=head("Входящий остаток"), closing=head("Исходящий остаток"),
        total_in=head("Поступления"), total_out=head("Расходы"),
        our_inn=search(r"ИНН:\s*(\d{10,12})", text, default=OUR_INN),
    )
    day = st.date_from
    last: Op | None = None
    for row in tables(pdf, "Оборот за весь период"):
        first = row[0]
        if first.startswith("Дата:"):
            day = d(search(r"Дата:\s*(\d{2}\.\d{2}\.\d{4})", first)) or day
            continue
        if first.startswith(("Номер", "Оборот", "Исходящее")) or len(row) < 6:
            continue
        debit, credit = num(row[4]), num(row[5])
        if not first.strip() and not debit and not credit and last:
            last.purpose = one_line(f"{last.purpose} {row[3]}")      # хвост строки с прошлой страницы
            continue
        if not debit and not credit:
            continue                                                 # рублёвая погрешность покрытия
        cp, req = row[1], row[2]
        op = Op(day=day, number=first.replace("\n", ""), amount=debit or credit,
                incoming=bool(credit), purpose=one_line(row[3]))
        if currency == "RUB":
            op.cp_name = one_line(cp)
            op.cp_bic = search(r"БИК:\s*(\d{9})", req)
            op.cp_inn = search(r"ИНН:\s*(\d{10,12})", req)
            op.cp_account = search(r"Р/[CС]:\s*(\d{20})", req)
            op.cp_bank = one_line(search(r"Банк-корреспондент:(.*?)(?:ИНН:|Р/[CС]:|$)", req.replace("\n", " ")))
        else:
            lines = [l.strip() for l in cp.split("\n") if l.strip()]
            acc = [l for l in lines if re.fullmatch(r"[A-Z0-9]{8,34}", l) and re.search(r"\d{6}", l)]
            op.cp_account = acc[-1] if acc else ""
            name = []
            for l in lines:                                  # имя — до LTD / LIMITED, дальше адрес
                if l in acc or l.startswith("SWIFT"):
                    break
                name.append(l)
                if re.search(r"(LTD\.?|LIMITED|CO\.)$", l, re.I):
                    break
            op.cp_name = one_line(" ".join(name) or lines[0] if lines else "")
            blines = [l.strip() for l in req.split("\n") if l.strip()]
            swift = [l for l in blines if re.fullmatch(r"(SWIFT\s+)?[A-Z0-9]{8,11}", l)]
            op.cp_bank = one_line(" ".join(l for l in blines if l not in swift))
            op.cp_bic = swift[-1].replace("SWIFT", "").strip() if swift else ""
        st.ops.append(op)
        last = op
    return st


# ------------------------------------------------------------------ СберБизнес

def parse_sber(pdf, text: str) -> Statement:
    account = search(r"ЛИЦЕВОМУ СЧЕТУ\s+(\d{20})", text)
    period = re.search(r"за период с\s+(\d{1,2} \S+ \d{4})\s*г\.\s*по\s+(\d{1,2} \S+ \d{4})", text)

    def pair(label: str) -> tuple[Decimal, Decimal]:
        m = re.search(rf"^{label}[^\n]*$", text, re.M)
        line = m.group(0) if m else ""
        amounts = AMOUNT_RE.findall(line.replace(label, ""))
        vals = [num(a) for a in amounts[:2]] + [Decimal(0)] * 2
        return vals[0], vals[1]

    ob_d, ob_c = pair("Входящий остаток")
    cb_d, cb_c = pair("Исходящий остаток")
    out_, in_ = pair("Итого оборотов")
    st = Statement(
        bank="Сбер", account=account, currency="RUB",
        date_from=ru_date(period.group(1)), date_to=ru_date(period.group(2)),
        opening=ob_c - ob_d, closing=cb_c - cb_d, total_in=in_, total_out=out_,
    )
    last: Op | None = None
    for page in pdf.pages:
        ptext = page.extract_text() or ""
        if "ВЫПИСКА" not in ptext and "Дата" not in ptext[:400] and last is None:
            continue
        if re.search(r"ПЛАТЕЖНОЕ ПОРУЧЕНИЕ|Отметки банка", ptext) and "ВЫПИСКА" not in ptext:
            break
        for table in page.extract_tables():
            for row in table:
                row = [c or "" for c in row]
                if len(row) < 9:
                    continue
                if DATE_RE.match(row[0].strip()):
                    debit, credit = num(row[3]), num(row[4])
                    incoming = bool(credit)
                    side = row[1] if incoming else row[2]          # реквизиты контрагента
                    parts = [p.strip() for p in side.split("\n") if p.strip()]
                    bank = one_line(row[7])
                    op = Op(day=d(row[0].strip()), number=row[5].strip(), amount=debit or credit,
                            incoming=incoming, vo=row[6].strip(), purpose=one_line(row[8]),
                            cp_account=parts[0] if parts else "",
                            cp_inn=parts[1] if len(parts) > 1 and parts[1].isdigit() else "",
                            cp_name=one_line(" ".join(parts[2:] if len(parts) > 1 and parts[1].isdigit() else parts[1:])),
                            cp_bic=search(r"БИК\s*(\d{9})", bank),
                            cp_bank=one_line(re.sub(r"БИК\s*\d{9}", "", bank)))
                    st.ops.append(op)
                    last = op
                elif last and not row[0].strip() and row[1] != "Дебет" and not num(row[3]) and not num(row[4]):
                    last.purpose = one_line(f"{last.purpose} {row[8]}")
    return st


# ------------------------------------------------------------------ Совкомбанк

def parse_sovcom(pdf, text: str) -> Statement:
    account = search(r"Сч[её]т №\s+(\d{20})", text)
    period = re.search(r"за период:\s*c\s*(\S+)\s*по\s*(\S+)", text)
    st = Statement(
        bank="Совкомбанк", account=account, currency="RUB",
        date_from=d(period.group(1)), date_to=d(period.group(2)),
        opening=num(search(r"Входящий остаток:\s*([^\n]+)", text)),
        closing=Decimal(0),
    )
    last: Op | None = None
    for row in tables(pdf):
        if len(row) < 8:
            continue
        if row[0].startswith("Итого"):
            st.total_out, st.total_in = num(row[5]), num(row[6])
            continue
        if DATE_RE.match(row[0].strip()):
            debit, credit = num(row[5]), num(row[6])
            number, _, vo = row[1].partition("/")
            who = [p.strip() for p in row[2].split("\n") if p.strip()]
            inn = who[-1] if who and re.fullmatch(r"\d{10,12}", who[-1]) else ""
            bank = [p.strip() for p in row[4].split("\n") if p.strip()]
            bic = bank[-1] if bank and re.fullmatch(r"\d{9}", bank[-1]) else ""
            op = Op(day=d(row[0].strip()), number=number.strip(), vo=vo.strip(),
                    amount=debit or credit, incoming=bool(credit),
                    cp_name=one_line(" ".join(who[:-1] if inn else who)), cp_inn=inn,
                    cp_account=row[3].strip(), cp_bic=bic,
                    cp_bank=one_line(" ".join(bank[:-1] if bic else bank)),
                    purpose=one_line(row[7]))
            st.ops.append(op)
            last = op
        elif last and not row[0].strip() and not num(row[5]) and not num(row[6]) and row[7]:
            last.purpose = one_line(f"{last.purpose} {row[7]}")
    closing = search(r"Исходящий остаток:\s*\n?\s*([\d  ]+,\d\d)", text)
    got_in = sum((o.amount for o in st.ops if o.incoming), Decimal(0))
    got_out = sum((o.amount for o in st.ops if not o.incoming), Decimal(0))
    st.closing = num(closing) if closing else st.opening + got_in - got_out
    return st


# ------------------------------------------------------------------ выбор формата

def parse_pdf(data: bytes) -> Statement:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages[:2])
        if "ЛОКО" in text.upper() or "LOCKO" in text.upper():
            return parse_loko(pdf, text)
        if "СберБизнес" in text or "ЛИЦЕВОМУ СЧЕТУ" in text:
            return parse_sber(pdf, text)
        if "Совкомбанк" in text:
            return parse_sovcom(pdf, text)
    raise ValueError("формат выписки не распознан")


# ------------------------------------------------------------------ запись 1С

def fmt(x: Decimal) -> str:
    return f"{x:.2f}"


def to_1c(st: Statement) -> str:
    out = [
        "1CClientBankExchange", "ВерсияФормата=1.03", "Кодировка=Windows",
        "Отправитель=PDF-выписка", "Получатель=",
        f"ДатаСоздания={date.today():%d.%m.%Y}",
        f"ДатаНачала={st.date_from:%d.%m.%Y}", f"ДатаКонца={st.date_to:%d.%m.%Y}",
        f"РасчСчет={st.account}",
        "СекцияРасчСчет",
        f"ДатаНачала={st.date_from:%d.%m.%Y}", f"ДатаКонца={st.date_to:%d.%m.%Y}",
        f"РасчСчет={st.account}",
        f"НачальныйОстаток={fmt(st.opening)}",
        f"ВсегоПоступило={fmt(sum((o.amount for o in st.ops if o.incoming), Decimal(0)))}",
        f"ВсегоСписано={fmt(sum((o.amount for o in st.ops if not o.incoming), Decimal(0)))}",
        f"КонечныйОстаток={fmt(st.closing)}",
        "КонецРасчСчет",
    ]
    for o in st.ops:
        kind = VO_TYPES.get(o.vo) or (
            "Банковский ордер" if not re.fullmatch(r"\d{1,7}", o.number) else "Платежное поручение")
        we = [st.account, st.our_name, st.our_inn]
        cp = [o.cp_account, o.cp_name, o.cp_inn]
        payer, receiver = (cp, we) if o.incoming else (we, cp)
        out += [
            f"СекцияДокумент={kind}",
            f"Номер={o.number}",
            f"Дата={o.day:%d.%m.%Y}",
            f"Сумма={fmt(o.amount)}",
            f"ПлательщикСчет={payer[0]}",
            f"Плательщик={payer[1]}",
            f"ПлательщикИНН={payer[2]}",
            f"ПлательщикРасчСчет={payer[0]}",
            f"ПлательщикБИК={'' if o.incoming is False else o.cp_bic}",
            f"ПлательщикБанк1={o.cp_bank if o.incoming else ''}",
            f"ПолучательСчет={receiver[0]}",
            f"Получатель={receiver[1]}",
            f"ПолучательИНН={receiver[2]}",
            f"ПолучательРасчСчет={receiver[0]}",
            f"ПолучательБИК={'' if o.incoming else o.cp_bic}",
            f"ПолучательБанк1={'' if o.incoming else o.cp_bank}",
            f"ДатаСписано={'' if o.incoming else f'{o.day:%d.%m.%Y}'}",
            f"ДатаПоступило={f'{o.day:%d.%m.%Y}' if o.incoming else ''}",
            f"ВидОплаты={o.vo or '01'}",
            f"НазначениеПлатежа={o.purpose}",
            "КонецДокумента",
        ]
    out.append("КонецФайла")
    return "\r\n".join(out) + "\r\n"


# ------------------------------------------------------------------ папка

def _pdfs(root: Path):
    """Все PDF папки, включая лежащие в zip; путь — для отчёта."""
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lower() == ".pdf":
            yield str(f.relative_to(root)), f.read_bytes()
        elif f.is_file() and f.suffix.lower() == ".zip":
            with zipfile.ZipFile(f) as z:
                for name in sorted(z.namelist()):
                    if name.lower().endswith(".pdf"):
                        yield f"{f.relative_to(root)}:{name}", z.read(name)


def link_own_transfers(statements: list[Statement]) -> int:
    """Переводы между нашими счетами, где банк не указал счёт получателя
    (Локо: транзитный ↔ текущий валютный): находим встречную операцию
    в выписке другого нашего счёта и проставляем его номер."""
    linked = 0
    for st in statements:
        for op in st.ops:
            if op.cp_account or op.cp_inn not in ("", st.our_inn) or "КОСМО" not in op.cp_name.upper():
                continue
            for other in statements:
                if other.account == st.account or other.currency != st.currency:
                    continue
                if any(o.day == op.day and o.amount == op.amount and o.incoming != op.incoming
                       for o in other.ops):
                    op.cp_account, op.cp_inn = other.account, st.our_inn
                    linked += 1
                    break
    return linked


def convert_folder(src: Path, out: Path, log=print) -> dict:
    """PDF из src → txt в out (по папке на банк). Возвращает счётчики."""
    seen, done, parsed = set(), {}, []
    stats = {"pdf": 0, "dup": 0, "txt": 0, "bad": 0, "warn": 0}
    for where, data in _pdfs(src):
        stats["pdf"] += 1
        h = hashlib.sha1(data).hexdigest()
        if h in seen:
            stats["dup"] += 1
            continue
        seen.add(h)
        try:
            parsed.append((where, parse_pdf(data)))
        except Exception as exc:                          # noqa: BLE001 — отчёт по файлу, идём дальше
            stats["bad"] += 1
            log(f"  ✗ {where}: {exc}")
    stats["own"] = link_own_transfers([st for _, st in parsed])
    for where, st in parsed:
        name = f"{st.bank}_{st.account}_{st.date_from:%Y-%m-%d}_{st.date_to:%Y-%m-%d}.txt"
        if name in done:
            stats["dup"] += 1
            continue
        target = out / st.bank / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(to_1c(st).encode("cp1251", errors="replace"))
        done[name] = where
        stats["txt"] += 1
        problems = st.check()
        if problems:
            stats["warn"] += 1
            log(f"  ⚠ {where} → {name}: " + "; ".join(problems))
    log(f"PDF: {stats['pdf']}, дублей: {stats['dup']}, txt: {stats['txt']}, "
        f"не разобрано: {stats['bad']}, с расхождением итогов: {stats['warn']}, "
        f"переводов между своими счетами найдено: {stats['own']}")
    return stats
