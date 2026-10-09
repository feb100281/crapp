"""
Разноска строк выписки через резолверы.

    sync()               — создать резолверы для новых КБК и счетов (по строкам в базе)
    resolve(resolver)    — переразнести строки одного резолвера по его правилам
    resolve_all()        — всё сразу (после импорта выписок)
    open_patterns(r)     — шаблоны назначений, которые ещё не разнесены
    pin_cp(...)          — найти или завести резолвер контрагента

Какая строка в какой резолвер попадает:
    между своими счетами              → резолвер «Свои» (валюта / валюта)
    всё остальное                     → сначала резолвер контрагента (если заведён
                                        и его правило подошло), иначе:
        есть КБК                      → резолвер КБК
        нет КБК                       → резолвер счёта (5 цифр)
    счёт контрагента не 20 знаков     → резолвера счёта нет, только контрагент

Резолвер контрагента ищет строки по ИНН. Если ИНН нет или он общий на многих
(физлица приходят с ИНН своего банка) — по названию из выписки.

Строки, у которых есть разноска «руками» (manual=True), не трогаются.
У остальных автоматическая разноска стирается и строится заново —
поэтому прогон можно повторять сколько угодно.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Substr

from macro.models.fx_model import Fx

from ..models.bs_line_alloc_model import BSLineAlloc
from ..models.bs_line_model import BSLine
from ..models.cf_item_model import CFItem, Direction
from ..models.resolver_model import Resolver, ResolverKind, name_key, norm_name
from ..models.resolver_rule_model import ResolverRule


# Подсказки по плану счетов ЦБ (Положение 809-П). Человек может поправить.
BA_NAMES = {
    "03100": "Казначейство (бюджет)",
    "03212": "Казначейство — ФССП, суды",
    "03214": "Бюджетные учреждения",
    "03224": "Бюджетные учреждения",
    "03225": "Бюджетные учреждения",
    "03226": "Бюджетные учреждения",
    "20208": "Касса банка — внесение наличных",
    "30101": "Корсчёт банка в ЦБ",
    "30110": "Корсчета в других банках",
    "30111": "Корсчета банков-нерезидентов (ВЭД)",
    "30220": "Транзит банка по переводам",
    "30232": "Расчёты по картам (эквайринг)",
    "30233": "Незавершённые переводы (карты, СБП, взносы)",
    "30301": "Расчёты между филиалами банка",
    "30302": "Расчёты между филиалами банка",
    "40701": "Финансовые организации",
    "40702": "Коммерческие организации",
    "40703": "Некоммерческие организации",
    "40802": "ИП",
    "40807": "Юрлица-нерезиденты",
    "40817": "Физлица",
    "40820": "Физлица-нерезиденты",
    "42102": "Депозиты юрлиц",
    "42301": "Вклады физлиц",
    "45201": "Кредиты юрлицам — овердрафт",
    "45204": "Кредиты юрлицам",
    "45205": "Кредиты юрлицам",
    "45206": "Кредиты юрлицам",
    "45207": "Кредиты юрлицам",
    "47407": "Конверсионные операции (банк должен)",
    "47408": "Конверсионные операции (банку должны)",
    "47416": "Невыясненные / возвраты платежей",
    "47422": "Прочие обязательства банка (комиссии, ведомости)",
    "47423": "Прочие требования банка (комиссии)",
    "47426": "Проценты по депозитам",
    "47427": "Проценты по кредитам",
    "47443": "Плата за лимит",
    "60309": "НДС банка",
    "60312": "Расчёты банка с поставщиками",
    "70601": "Доходы банка (комиссии, курсовая)",
    "70603": "Переоценка (доход)",
    "70608": "Переоценка (расход)",
}



# Строки, на которые действуют резолверы контрагентов: все, кроме переводов
# между своими счетами (в том числе строки с КБК)
CP_ELIGIBLE = {"intercompany": False}

# Строки резолверов счетов: без КБК и не между своими
BA_LINES = {"kbk__isnull": True, "intercompany": False}

# ИНН, под которым в выписках больше стольких разных названий, — общий
# (банк, через который платят физлица): контрагент определяется по названию
SHARED_INN_NAMES = 5

AUTO_KINDS = (ResolverKind.KBK, ResolverKind.BA, ResolverKind.IC)

LINE_FIELDS = ("id", "direction", "amount", "description", "kbk", "intercompany",
               "inn_adjust", "cp_name", "ba_resolver")


def _ic_lines():
    """Переводы между своими + валюта нашего счёта и второго счёта (знаки 6–8)."""
    return BSLine.objects.filter(intercompany=True).annotate(
        our_cur=Substr("ba_account__number", 6, 3),
        cp_cur=Substr("cp_ba", 6, 3),
    )


def name_variants(match_name: str) -> list[str]:
    """Все написания названия в выписках (регистр и пробелы не важны)."""
    target = norm_name(match_name)
    names = (
        BSLine.objects.filter(**CP_ELIGIBLE).exclude(cp_name__isnull=True)
        .order_by().values_list("cp_name", flat=True).distinct()
    )
    return [n for n in names if norm_name(n) == target]


def lines(resolver: Resolver):
    """Все строки резолвера."""
    if resolver.kind == ResolverKind.IC:
        our_cur, _, cp_cur = resolver.key.partition("/")
        return _ic_lines().filter(direction=resolver.direction, our_cur=our_cur, cp_cur=cp_cur)

    qs = BSLine.objects.filter(direction=resolver.direction)
    if resolver.kind == ResolverKind.KBK:
        return qs.filter(kbk=resolver.key)
    if resolver.kind == ResolverKind.CP:
        qs = qs.filter(**CP_ELIGIBLE)
        if resolver.match_name:
            return qs.filter(cp_name__in=name_variants(resolver.match_name))
        return qs.filter(inn_adjust=resolver.key)
    return qs.filter(kbk__isnull=True, intercompany=False, ba_resolver=resolver.key)


def orphan_lines():
    """Строки без резолвера счёта (счёт контрагента не 20 знаков) — только контрагент."""
    return BSLine.objects.filter(ba_resolver__isnull=True, **BA_LINES)


_SPACES = re.compile(r"\s+")
_RX_SPACES = re.compile(r" {2,}")


def norm_text(text) -> str:
    """Назначение для правил: двойные пробелы, неразрывные пробелы и переносы — один пробел."""
    return _SPACES.sub(" ", text or "").strip()


class _RuleText:
    """«Назначение содержит»: подходит и как обычный текст, и как регулярка.

    Скобки, точки, «+» в тексте — частая вещь («товары (или услуги)»): как
    регулярка такой текст не совпадает, поэтому сначала проверяем его как есть.
    Пусто — «всё остальное».
    """

    def __init__(self, raw: str):
        self.text = _RX_SPACES.sub(" ", (raw or "").strip())
        self.plain = self.text.casefold()
        try:
            self.rx = re.compile(self.text, re.IGNORECASE) if self.text else None
        except re.error:
            self.rx = None

    def matches(self, text: str, folded: str) -> bool:
        if not self.text:
            return True
        if self.plain in folded:
            return True
        return bool(self.rx and self.rx.search(text))


class _Matcher:
    """Правила всех резолверов — чтобы для строки быстро выбрать статью."""

    def __init__(self):
        self.rules = defaultdict(list)
        for r in ResolverRule.objects.all():
            self.rules[r.resolver_id].append(r)
        for rid, rules in self.rules.items():
            # сначала правила с текстом по порядку, «всё остальное» — в конце
            rules.sort(key=lambda r: (not r.text_regex.strip(), r.order, r.id))
            self.rules[rid] = [(r, _RuleText(r.text_regex)) for r in rules]

        self.ba, self.kbk, self.cp_inn, self.cp_name = {}, {}, {}, {}
        for r in Resolver.objects.exclude(kind=ResolverKind.IC):
            if r.kind == ResolverKind.BA:
                self.ba[(r.key, r.direction)] = r.id
            elif r.kind == ResolverKind.KBK:
                self.kbk[(r.key, r.direction)] = r.id
            elif r.match_name:
                self.cp_name[(norm_name(r.match_name), r.direction)] = r.id
            else:
                self.cp_inn[(r.key, r.direction)] = r.id

    def _first(self, resolver_id, text):
        folded = text.casefold()
        for rule, check in self.rules.get(resolver_id, ()):
            if check.matches(text, folded):
                return rule
        return None

    def cp_resolver_ids(self, ln: dict) -> list[int]:
        """Резолверы контрагента для строки: сначала по названию, потом по ИНН."""
        if ln["intercompany"]:
            return []
        d = ln["direction"]
        found = []
        if self.cp_name and ln["cp_name"]:
            found.append(self.cp_name.get((norm_name(ln["cp_name"]), d)))
        if self.cp_inn and ln["inn_adjust"]:
            found.append(self.cp_inn.get((ln["inn_adjust"], d)))
        return [rid for rid in found if rid]

    def pick(self, ln: dict, own_id: int | None = None):
        """Правило для строки. own_id — резолвер, чьи строки сейчас разбираются."""
        text = norm_text(ln["description"])
        for rid in self.cp_resolver_ids(ln):
            rule = self._first(rid, text)
            if rule:
                return rule
        if own_id is None and not ln["intercompany"]:
            # строка контрагента, которую его правила не взяли: КБК или счёт
            if ln["kbk"] is not None:
                own_id = self.kbk.get((ln["kbk"], ln["direction"]))
            else:
                own_id = self.ba.get((ln["ba_resolver"], ln["direction"]))
        return self._first(own_id, text) if own_id else None


def _refresh_marts() -> None:
    """Витрина дашборда «Контрагенты и статьи» — после того, как разноска записана."""
    from dashboard.services.marts import schedule_cp_audit

    schedule_cp_audit()


def _alloc(rule: ResolverRule, ln: dict) -> BSLineAlloc:
    """Одна разноска на всю сумму строки. Удержанная комиссия / долг
    раскладываются не здесь, а в отчёте ДДС (sql/bs/cash_flow.sql)."""
    return BSLineAlloc(
        line_id=ln["id"],
        direction=ln["direction"],
        amount=ln["amount"],
        cf_item_id=rule.cf_item_id,
        resolver_rule_id=rule.id,
    )


def _apply(matcher: _Matcher, qs, own_id: int | None = None) -> None:
    """Стереть автоматическую разноску строк qs и построить заново."""
    free = qs.exclude(allocs__manual=True)

    # .values("pk"): у строк «Своих» есть аннотации — в подзапросе нужен только id
    BSLineAlloc.objects.filter(manual=False, line__in=free.values("pk")).delete()

    allocs = []
    for ln in free.values(*LINE_FIELDS):
        rule = matcher.pick(ln, own_id)
        if rule:
            allocs.append(_alloc(rule, ln))
    BSLineAlloc.objects.bulk_create(allocs, batch_size=2000)


# ------------------------------------------------------------------
# Счётчики
# ------------------------------------------------------------------

def _recount_rules() -> None:
    """hits / hits_amount всех правил — по фактической разноске."""
    fact = {
        row["resolver_rule"]: (row["n"], row["s"] or Decimal(0))
        for row in BSLineAlloc.objects.filter(resolver_rule__isnull=False)
        .order_by().values("resolver_rule").annotate(n=Count("id"), s=Sum("amount"))
    }
    changed = []
    for rule in ResolverRule.objects.only("id", "hits", "hits_amount"):
        n, amount = fact.get(rule.id, (0, Decimal(0)))
        if rule.hits != n or rule.hits_amount != amount:
            rule.hits, rule.hits_amount = n, amount
            changed.append(rule)
    ResolverRule.objects.bulk_update(changed, ["hits", "hits_amount"], batch_size=500)


def _recount_resolver(resolver: Resolver) -> None:
    all_lines = lines(resolver)
    total = all_lines.aggregate(n=Count("id"), s=Sum("amount"))
    still_open = all_lines.filter(allocs__isnull=True).aggregate(n=Count("id"), s=Sum("amount"))
    Resolver.objects.filter(pk=resolver.pk).update(
        lines_count=total["n"] or 0,
        lines_amount=total["s"] or 0,
        open_count=still_open["n"] or 0,
        open_amount=still_open["s"] or 0,
    )


def _recount_cp() -> None:
    """Счётчики всех резолверов контрагентов — двумя запросами на всех."""
    resolvers = list(Resolver.objects.filter(kind=ResolverKind.CP))
    if not resolvers:
        return

    def grouped(qs):
        by_inn, by_name = defaultdict(lambda: [0, Decimal(0)]), defaultdict(lambda: [0, Decimal(0)])
        rows = (qs.order_by().values("direction", "inn_adjust", "cp_name")
                .annotate(n=Count("id"), s=Sum("amount")))
        for r in rows:
            for bucket, key in ((by_inn, r["inn_adjust"]), (by_name, norm_name(r["cp_name"]))):
                if key:
                    g = bucket[(key, r["direction"])]
                    g[0] += r["n"]
                    g[1] += r["s"] or Decimal(0)
        return by_inn, by_name

    eligible = BSLine.objects.filter(**CP_ELIGIBLE)
    total = grouped(eligible)
    still_open = grouped(eligible.filter(allocs__isnull=True))

    for r in resolvers:
        i, key = (1, norm_name(r.match_name)) if r.match_name else (0, r.key)
        n, amount = total[i].get((key, r.direction), (0, Decimal(0)))
        open_n, open_amount = still_open[i].get((key, r.direction), (0, Decimal(0)))
        Resolver.objects.filter(pk=r.pk).update(
            lines_count=n, lines_amount=amount, open_count=open_n, open_amount=open_amount,
        )


def _neighbours(qs, direction: int) -> list[Resolver]:
    """Резолверы КБК и счетов, которым принадлежат строки qs (строки контрагента)."""
    pairs = set(qs.order_by().values_list("kbk", "ba_resolver").distinct())
    kbks = [k for k, _ in pairs if k is not None]
    bas = [b for k, b in pairs if k is None and b]
    return list(
        Resolver.objects.filter(kind=ResolverKind.KBK, direction=direction, key__in=kbks)
    ) + list(
        Resolver.objects.filter(kind=ResolverKind.BA, direction=direction, key__in=bas)
    )


# ------------------------------------------------------------------
# Разноска
# ------------------------------------------------------------------

@transaction.atomic
def resolve(resolver: Resolver) -> None:
    matcher = _Matcher()

    if resolver.kind == ResolverKind.CP:
        # строки контрагента: его правила, а что не подошло — резолверу КБК / счёта
        touched = _neighbours(lines(resolver), resolver.direction)
        _apply(matcher, lines(resolver))
        for other in touched:
            _recount_resolver(other)
    else:
        _apply(matcher, lines(resolver), own_id=resolver.id)
        _recount_resolver(resolver)

    _recount_rules()
    if resolver.kind != ResolverKind.IC:
        _recount_cp()
    _refresh_marts()

def _payee(qs) -> str | None:
    """Один контрагент — его имя, несколько — их число. Считается в SQL."""
    names = (
        qs.exclude(cp_name__isnull=True).exclude(cp_name="")
        .order_by()
        .values("cp_name")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    total = names.count()
    if not total:
        return None
    top = names[0]["cp_name"]
    if total == 1:
        return top[:300]
    return f"{total} контрагентов, чаще всего: {top}"[:300]


def sync() -> int:
    """Создаёт резолверы для всех КБК и счетов, которые есть в строках. Возвращает число новых."""
    created = 0
    existing = set(Resolver.objects.values_list("kind", "key", "direction"))

    # .order_by() обязателен: иначе сортировка модели (-op_date, id) попадает
    # в SELECT DISTINCT и вместо десятков пар приходят десятки тысяч строк
    kbk_pairs = (
        BSLine.objects.filter(kbk__isnull=False)
        .order_by().values_list("kbk", "direction").distinct()
    )
    for kbk, direction in kbk_pairs:
        if (ResolverKind.KBK, kbk, direction) in existing:
            continue
        Resolver.objects.create(
            kind=ResolverKind.KBK, key=kbk, direction=direction,
            payee=_payee(BSLine.objects.filter(kbk=kbk, direction=direction)),
        )
        created += 1

    ba_pairs = (
        BSLine.objects.filter(kbk__isnull=True, intercompany=False, ba_resolver__isnull=False)
        .order_by().values_list("ba_resolver", "direction").distinct()
    )
    for key, direction in ba_pairs:
        if (ResolverKind.BA, key, direction) in existing:
            continue
        Resolver.objects.create(
            kind=ResolverKind.BA, key=key, direction=direction,
            name=BA_NAMES.get(key),
            payee=_payee(BSLine.objects.filter(
                kbk__isnull=True, intercompany=False, ba_resolver=key, direction=direction,
            )),
        )
        created += 1

    created += _sync_ic(existing)

    return created


# Стартовые правила для «Своих»: конвертация по тексту, остальное — по валютам
IC_CONVERSION_RX = r"купли-продажи|конверс|конвертац"
IC_ITEMS = {
    # (направление, конвертация?) → код статьи
    (Direction.INFLOW, False): "410100",
    (Direction.OUTFLOW, False): "420100",
    (Direction.INFLOW, True): "410200",
    (Direction.OUTFLOW, True): "420200",
}


def _sync_ic(existing: set) -> int:
    created = 0
    fx = dict(Fx.objects.values_list("numeric_code", "code"))
    fx["643"] = fx.get("643") or "RUB"
    items = {code: item for code, item in CFItem.objects.filter(code__in=IC_ITEMS.values())
             .values_list("code", "pk")}

    pairs = _ic_lines().order_by().values_list("our_cur", "cp_cur", "direction").distinct()
    for our_cur, cp_cur, direction in pairs:
        key = f"{our_cur}/{cp_cur}"
        if (ResolverKind.IC, key, direction) in existing:
            continue

        conversion = our_cur != cp_cur
        resolver = Resolver.objects.create(
            kind=ResolverKind.IC, key=key, direction=direction,
            name=f"{fx.get(our_cur, our_cur)} → {fx.get(cp_cur, cp_cur)} · "
                 f"{'конвертация' if conversion else 'перевод'}",
        )
        created += 1

        # стартовые правила — дальше правятся в админке как обычно
        conv_item = items.get(IC_ITEMS[(direction, True)])
        main_item = items.get(IC_ITEMS[(direction, conversion)])
        # «купли-продажи» в паре одной ВАЛЮТЫ (156/156, 356/356) — это приход купленной
        # валюты, вторая нога конвертации 810/156. В рублях (810/810) такого не бывает.
        if conv_item and not conversion and our_cur not in ("810", "643"):
            ResolverRule.objects.create(
                resolver=resolver, order=10, text_regex=IC_CONVERSION_RX, cf_item_id=conv_item,
            )
        if main_item:
            ResolverRule.objects.create(
                resolver=resolver, order=20, text_regex="", cf_item_id=main_item,
            )
    return created



def resolve_all(kind: str | None = None) -> dict:
    """Переразнести всё (или один тип). Контрагенты разбираются вместе с КБК и счетами."""
    kinds = list(AUTO_KINDS)
    if kind == ResolverKind.CP:
        kinds = [ResolverKind.KBK, ResolverKind.BA]
    elif kind:
        kinds = [kind]

    with transaction.atomic():
        matcher = _Matcher()
        resolvers = list(Resolver.objects.filter(kind__in=kinds))
        for resolver in resolvers:
            _apply(matcher, lines(resolver), own_id=resolver.id)
        if ResolverKind.BA in kinds:
            _apply(matcher, orphan_lines())

        for resolver in resolvers:
            _recount_resolver(resolver)
        _recount_rules()
        _recount_cp()
        _refresh_marts()

    qs = Resolver.objects.filter(kind=kind) if kind else Resolver.objects.filter(kind__in=AUTO_KINDS)
    agg = qs.aggregate(n=Sum("lines_count"), o=Sum("open_count"))
    return {"resolvers": qs.count(), "lines": agg["n"] or 0, "open": agg["o"] or 0}


# ------------------------------------------------------------------
# Контрагенты
# ------------------------------------------------------------------

def shared_inns() -> set[str]:
    """ИНН, общие на многих контрагентов (банки, через которые платят физлица)."""
    names = defaultdict(set)
    pairs = (
        BSLine.objects.filter(**CP_ELIGIBLE).exclude(inn_adjust__isnull=True).exclude(inn_adjust="")
        .order_by().values_list("inn_adjust", "cp_name").distinct()
    )
    for inn, name in pairs:
        names[inn].add(norm_name(name))
    return {inn for inn, found in names.items() if len(found) > SHARED_INN_NAMES}


def cp_key(inn, name, shared: set[str]) -> tuple[str, str]:
    """Как узнаём контрагента: ("inn", ИНН) или ("name", название)."""
    if inn and inn not in shared:
        return "inn", inn
    return "name", norm_name(name)


def pin_cp(inn, name, direction: int, shared: set[str] | None = None) -> tuple[Resolver, bool]:
    """Резолвер контрагента: найти или завести. Возвращает (резолвер, создан ли)."""
    mode, value = cp_key(inn, name, shared_inns() if shared is None else shared)
    clean_name = " ".join(str(name or "").split())

    if mode == "inn":
        top = (
            BSLine.objects.filter(inn_adjust=inn, direction=direction, **CP_ELIGIBLE)
            .exclude(cp_name__isnull=True).order_by()
            .values("cp_name").annotate(n=Count("id")).order_by("-n").first()
        )
        defaults = {"name": ((top or {}).get("cp_name") or clean_name)[:200] or None}
        return Resolver.objects.get_or_create(
            kind=ResolverKind.CP, key=inn, direction=direction, defaults=defaults,
        )

    if not value:
        raise ValueError("У контрагента нет ни ИНН, ни названия")
    defaults = {"name": clean_name[:200], "match_name": clean_name[:300]}
    return Resolver.objects.get_or_create(
        kind=ResolverKind.CP, key=name_key(clean_name), direction=direction, defaults=defaults,
    )


@transaction.atomic
def release_cp(resolver: Resolver) -> None:
    """Удалить резолвер контрагента и вернуть его строки резолверам КБК / счетов."""
    freed = lines(resolver)
    touched = _neighbours(freed, resolver.direction)
    resolver.delete()

    _apply(_Matcher(), freed)
    for other in touched:
        _recount_resolver(other)
    _recount_rules()
    _recount_cp()
    _refresh_marts()


def set_catch_all(resolver: Resolver, cf_item: CFItem) -> ResolverRule:
    """Правило «всё остальное → статья» (создать или заменить статью)."""
    rule = resolver.rules.filter(text_regex="").first()
    if rule:
        if rule.cf_item_id != cf_item.id:
            rule.cf_item = cf_item
            rule.save(update_fields=["cf_item"])
        return rule
    return ResolverRule.objects.create(resolver=resolver, order=90, text_regex="", cf_item=cf_item)


# ------------------------------------------------------------------
# Неразнесённое
# ------------------------------------------------------------------

def open_patterns(resolver: Resolver, limit: int = 50, find: str = "", find_inn: str = "") -> list[dict]:
    """Неразнесённые строки по шаблону назначения; внутри шаблона — по контрагентам.
    find / find_inn — только строки этого контрагента (переход из дашборда)."""
    qs = lines(resolver).filter(allocs__isnull=True)
    if find or find_inn:
        cond = Q()
        if find:
            cond |= Q(cp_name__icontains=find)
        if find_inn:
            cond |= Q(inn_adjust=find_inn)
        qs = qs.filter(cond)

    groups: dict[str, dict] = {}
    for pattern, desc, amount, inn, name in (
        qs.values_list("desc_pattern", "description", "amount", "inn_adjust", "cp_name")
    ):
        amount = Decimal(str(amount))
        g = groups.setdefault(pattern or "", {
            "pattern": pattern or "", "example": desc or "", "count": 0, "amount": Decimal(0),
            "cps": {},
        })
        g["count"] += 1
        g["amount"] += amount

        cp = g["cps"].setdefault((inn or "", norm_name(name)), {
            "inn": inn or "", "name": name or "", "count": 0, "amount": Decimal(0),
        })
        cp["count"] += 1
        cp["amount"] += amount

    top = sorted(groups.values(), key=lambda g: -g["amount"])[:limit]
    for g in top:
        g["cps"] = sorted(g["cps"].values(), key=lambda c: -c["amount"])
    return top
