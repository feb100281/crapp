"""
Стартовый план счетов и счета под наши банковские счета.

    seed_chart()          — завести стартовый план (существующие не трогает)
    sync_bank_accounts()  — под каждый наш банковский счёт свой субсчёт
                            в «Расчётных счетах (руб.)» или «Валютных счетах»
"""

from __future__ import annotations

from django.db.models import Max

from treasury.models.ba_model import BankAccount

from ..models.gl_account_model import GLAccount, Nature, Section

A, P, AP = Nature.ACTIVE, Nature.PASSIVE, Nature.BOTH

# (раздел, номер, название, характер)
CHART = [
    (Section.ASSETS, 1, "Расчётные счета (руб.)", A),
    (Section.ASSETS, 2, "Валютные счета", A),
    (Section.ASSETS, 3, "Касса", A),
    (Section.ASSETS, 4, "Денежные средства в пути", A),
    (Section.ASSETS, 5, "Депозиты", A),
    (Section.ASSETS, 10, "Расчёты с покупателями", AP),
    (Section.ASSETS, 11, "Авансы поставщикам", A),
    (Section.ASSETS, 12, "Расчёты с подотчётными", AP),
    (Section.ASSETS, 13, "Товары", A),
    (Section.ASSETS, 14, "Основные средства", A),
    (Section.ASSETS, 99, "Прочие активы", A),

    (Section.LIABILITIES, 1, "Расчёты с поставщиками", AP),
    (Section.LIABILITIES, 2, "Авансы покупателей", P),
    (Section.LIABILITIES, 3, "Кредиты и займы", P),
    (Section.LIABILITIES, 4, "Расчёты по налогам и взносам", AP),
    (Section.LIABILITIES, 5, "Расчёты с персоналом", AP),
    (Section.LIABILITIES, 99, "Прочие обязательства", P),

    (Section.EQUITY, 1, "Уставный капитал", P),
    (Section.EQUITY, 2, "Ввод начальных остатков", AP),
    (Section.EQUITY, 3, "Нераспределённая прибыль", AP),

    (Section.REVENUE, 1, "Выручка от продажи мебели", P),
    (Section.REVENUE, 99, "Прочая выручка", P),

    (Section.DIRECT, 1, "Себестоимость товаров", A),
    (Section.DIRECT, 2, "Доставка и логистика", A),
    (Section.DIRECT, 3, "Таможня и ВЭД", A),

    (Section.OVERHEADS, 1, "Аренда", A),
    (Section.OVERHEADS, 2, "Коммунальные и электроэнергия", A),
    (Section.OVERHEADS, 3, "Связь, IT, офис", A),

    (Section.SGA, 1, "ФОТ и взносы", A),
    (Section.SGA, 2, "Маркетинг и агенты", A),
    (Section.SGA, 3, "Банковские комиссии", A),
    (Section.SGA, 99, "Прочие расходы", A),

    (Section.OTHER, 1, "Курсовые разницы", AP),
    (Section.OTHER, 2, "Штрафы и пени", A),
    (Section.OTHER, 99, "Прочие доходы и расходы", AP),

    (Section.FINANCE, 1, "Проценты к уплате", A),
    (Section.FINANCE, 2, "Проценты к получению", P),

    (Section.TAX, 1, "Налог на прибыль", A),
]

RUB_GROUP = (Section.ASSETS, 1)
FX_GROUP = (Section.ASSETS, 2)
OPENING_ACCOUNT = (Section.EQUITY, 2)


def seed_chart() -> int:
    created = 0
    for section, number, name, nature in CHART:
        _, new = GLAccount.objects.get_or_create(
            section=section, number=number, parent=None,
            defaults={"name": name, "nature": nature},
        )
        created += new
    return created


def _group(section_number) -> GLAccount:
    section, number = section_number
    return GLAccount.objects.get(section=section, number=number, parent=None)


def bank_account_name(ba: BankAccount) -> str:
    bank = (ba.bank.name if ba.bank else "") or "Банк"
    bank = bank.replace("АО", "").replace("ПАО", "").replace("(", "").replace(")", "").strip()
    cur = ba.currency.code if ba.currency else "RUB"
    kind = " · транзит" if ba.ba_type == "TRANSIT" else ""
    return f"{bank} · {cur} · …{ba.number[-4:]}{kind}"[:200]


def sync_bank_accounts() -> int:
    """Субсчёт под каждый наш банковский счёт, у которого его ещё нет."""
    seed_chart()
    rub, fx = _group(RUB_GROUP), _group(FX_GROUP)
    created = 0

    for ba in (
        BankAccount.objects.filter(gl_account__isnull=True)
        .select_related("bank", "currency").order_by("number")
    ):
        is_rub = not ba.currency or ba.currency.code == "RUB"
        parent = rub if is_rub else fx
        last = parent.children.aggregate(m=Max("number"))["m"] or 0
        GLAccount.objects.create(
            parent=parent,
            section=parent.section,
            number=last + 1,
            name=bank_account_name(ba),
            nature=Nature.ACTIVE,
            currency=None if is_rub else ba.currency,
            bank_account=ba,
        )
        created += 1

    return created
