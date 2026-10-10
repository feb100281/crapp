"""
Курсы валют ЦБ: витрина dashboard_fx_rate (бот обновляет её сам, включая курс на завтра).
Пока её нет — курсы из витрины остатков (только по дням выписок).
Сводка изменений, выводы и график PNG для Telegram.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Max, Sum
from django.utils import timezone

from ..models import CashBalance, FxRate
from . import fx_feed

SYMBOLS = {"USD": "$", "EUR": "€", "CNY": "¥", "INR": "₹", "GBP": "£", "AED": "AED", "KZT": "₸", "TRY": "₺"}
FLAG_NAMES = {"USD": "Доллар США", "EUR": "Евро", "CNY": "Юань", "INR": "Индийская рупия", "AED": "Дирхам ОАЭ",
              "AMD": "Армянский драм", "BYN": "Белорусский рубль", "KGS": "Киргизский сом", "KZT": "Тенге"}
FIRST = ("USD", "EUR", "CNY")


def _feed() -> bool:
    return fx_feed.has_data() and FxRate.objects.exists()


def currencies() -> list[str]:
    if _feed():
        codes = set(FxRate.objects.order_by().values_list("code", flat=True).distinct())
    else:
        codes = set(CashBalance.objects.exclude(currency="RUB").exclude(currency__isnull=True)
                    .order_by().values_list("currency", flat=True).distinct())
    return sorted(codes, key=lambda c: (FIRST.index(c) if c in FIRST else len(FIRST), c))


def series(cur: str, days: int = 90, until: date | None = None) -> list[tuple[date, float]]:
    if _feed():
        until = until or timezone.localdate()
        rows = (FxRate.objects.filter(code=cur, date__gt=until - timedelta(days=days), date__lte=until)
                .order_by("date").values_list("date", "rate"))
        return [(d, float(r)) for d, r in rows]
    until = until or CashBalance.objects.filter(currency=cur).aggregate(d=Max("date"))["d"]
    if not until:
        return []
    rows = (CashBalance.objects.filter(currency=cur, date__gt=until - timedelta(days=days), date__lte=until)
            .exclude(rate__isnull=True).order_by("date").values_list("date", "rate").distinct())
    seen, out = set(), []
    for d, r in rows:
        if d not in seen:
            seen.add(d)
            out.append((d, float(r)))
    return out


def _next(cur: str, today: date) -> tuple[date, float] | None:
    """Курс, который ЦБ уже установил на завтра (публикует к вечеру рабочего дня)."""
    if not _feed():
        return None
    row = FxRate.objects.filter(code=cur, date__gt=today).order_by("date").values_list("date", "rate").first()
    return (row[0], float(row[1])) if row else None


def summary(cur: str, days: int = 90) -> dict | None:
    data = series(cur, days)
    if not data:
        return None
    last_d, last = data[-1]

    def back(n):
        target = last_d - timedelta(days=n)
        prev = [r for d, r in data if d <= target]
        if not prev:
            return None
        return {"days": n, "rate": prev[-1], "diff": last - prev[-1], "pct": (last / prev[-1] - 1) * 100}

    month = [r for d, r in data if d > last_d - timedelta(days=30)]
    today = timezone.localdate()
    nxt = _next(cur, today)
    return {
        "today": today, "live": _feed(),
        # курс субботы действует до понедельника — это и есть курс «на сегодня»
        "is_today": (today - last_d).days <= 3 and _feed(),
        "next": nxt and {"date": nxt[0], "rate": nxt[1], "diff": nxt[1] - last, "pct": (nxt[1] / last - 1) * 100},
        "cur": cur, "name": FLAG_NAMES.get(cur, cur), "symbol": SYMBOLS.get(cur, cur),
        "date": last_d, "rate": last,
        "changes": [c for c in (back(1), back(7), back(30), back(90) if days >= 90 else None,
                                back(365) if days >= 365 else None) if c],
        "min30": min(month), "max30": max(month),
        "series": data,
    }


def fmt_rate(v: float) -> str:
    return f"{v:,.4f}".replace(",", " ").replace(".", ",")


def day_label(s: dict) -> str:
    if s["is_today"]:
        return f"на сегодня, {s['today']:%d.%m.%Y}"
    return f"на {s['date']:%d.%m.%Y}"


def next_note(s: dict) -> str:
    """Пояснение про курс на завтра: есть ли он и когда ЦБ его установит."""
    if not s.get("live"):
        return ""
    if s["next"]:
        return "〰️ Пунктир на графике — курс на завтра: ЦБ его уже установил."
    if fx_feed.msk_now().weekday() >= 5:
        return ("ℹ️ В выходные ЦБ курс не меняет: курс субботы действует до понедельника. "
                "Следующий ЦБ установит в понедельник — он будет действовать со вторника.")
    return ("ℹ️ Курс на завтра ЦБ устанавливает в рабочие дни по рынку на 15:30 МСК "
            "и публикует ближе к вечеру, обычно до 18:00 МСК. Как только опубликует — "
            "покажу его здесь и пунктиром на графике.")


def text(s: dict) -> str:
    def arrow(c):
        return "🟢" if c["diff"] > 0 else ("🔴" if c["diff"] < 0 else "⚪")

    names = {1: "за день", 7: "за неделю", 30: "за 30 дней", 90: "за 90 дней", 365: "за год"}
    lines = [f"💱 <b>{s['name']} ({s['cur']})</b>", f"Курс ЦБ {day_label(s)}: <b>{fmt_rate(s['rate'])} ₽</b>"]
    if s["next"]:
        n = s["next"]
        sign = "+" if n["diff"] > 0 else ""
        lines.append(f"{arrow(n)} на завтра, {n['date']:%d.%m}: <b>{fmt_rate(n['rate'])} ₽</b> "
                     f"({sign}{fmt_rate(n['diff'])} ₽)")
    lines.append("")
    for c in s["changes"]:
        sign = "+" if c["diff"] > 0 else ""
        pct = f"{c['pct']:.2f}".replace(".", ",")
        lines.append(f"{arrow(c)} {names[c['days']]}: {sign}{fmt_rate(c['diff'])} ₽ ({sign}{pct} %)")
    lines += ["", f"30 дней: мин {fmt_rate(s['min30'])} · макс {fmt_rate(s['max30'])}"]
    tips = conclusions(s)
    if tips:
        lines += ["", "<b>Выводы</b>"] + [f"• {t}" for t in tips]
    note = next_note(s)
    if note:
        lines += ["", f"<i>{note}</i>"]
    return "\n".join(lines)


def _pct(v: float) -> str:
    return f"{abs(v):.1f}".replace(".", ",") + " %"


def _rub(v: float) -> str:
    v = abs(v)
    if v >= 1e6:
        return f"{v / 1e6:.2f}".replace(".", ",") + " млн ₽"
    if v >= 1e3:
        return f"{v / 1e3:.0f} тыс. ₽"
    return f"{v:.0f} ₽"


def conclusions(s: dict) -> list[str]:
    """Выводы по правилам: тренд, где курс в диапазоне месяца, влияние на наши остатки."""
    out = []
    by = {c["days"]: c for c in s["changes"]}
    n = s.get("next")
    if n and abs(n["pct"]) >= 0.05:
        out.append(f"ЦБ уже установил курс на {n['date']:%d.%m}: {'выше' if n['diff'] > 0 else 'ниже'} "
                   f"сегодняшнего на {_pct(n['pct'])}.")
    m = by.get(30)
    if m:
        word = "вырос" if m["diff"] > 0 else "снизился"
        out.append(f"За 30 дней курс {s['cur']} {word} на {_pct(m['pct'])}.")
    w = by.get(7)
    if m and w and (w["diff"] > 0) != (m["diff"] > 0) and abs(w["pct"]) >= 0.5:
        out.append("За последнюю неделю движение развернулось — " + ("курс растёт." if w["diff"] > 0 else "курс снижается."))
    month = [r for d, r in s["series"] if d > s["date"] - timedelta(days=30)]
    avg = sum(month) / len(month)
    span = s["max30"] - s["min30"]
    if span > 0:
        pos = (s["rate"] - s["min30"]) / span
        if pos <= 0.1:
            out.append("Сейчас курс у минимума месяца.")
        elif pos >= 0.9:
            out.append("Сейчас курс у максимума месяца.")
    gap = (s["rate"] / avg - 1) * 100
    if abs(gap) >= 0.5:
        out.append(f"Курс {'выше' if gap > 0 else 'ниже'} среднего за 30 дней на {_pct(gap)}.")

    last_bal = CashBalance.objects.filter(currency=s["cur"]).aggregate(d=Max("date"))["d"]
    held = (CashBalance.objects.filter(currency=s["cur"], date=last_bal)
            .aggregate(v=Sum("base_eb"))["v"] or 0) if last_bal else 0
    if abs(held) >= 1:
        cur_amt = f"{held:,.0f}".replace(",", " ")
        for days, label in ((1, "за день"), (7, "за неделю"), (30, "за 30 дней")):
            c = by.get(days)
            if c and abs(held * c["diff"]) >= 1:
                eff = held * c["diff"]
                out.append(f"Наш остаток {cur_amt} {s['cur']} {label} {'подорожал' if eff > 0 else 'подешевел'} "
                           f"на {_rub(eff)} в рублях.")
                break
    return out


def chart_svg(s: dict, width: int = 900, height: int = 420) -> str:
    """Линия курса с подписями: без библиотек, одна ось."""
    pts = s["series"]
    nxt = s.get("next")
    vals = [r for _, r in pts] + ([nxt["rate"]] if nxt else [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 1
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 70, 96, 64, 46
    w, h = width - left - right, height - top - bottom
    n = max(len(pts) - 1 + (1 if nxt else 0), 1)

    def x(i):
        return left + w * i / n

    def y(v):
        return top + h * (1 - (v - lo) / (hi - lo))

    line = " ".join(f"{x(i):.1f},{y(r):.1f}" for i, (_, r) in enumerate(pts))
    area = f"{left},{top + h} {line} {x(len(pts) - 1):.1f},{top + h}"
    grid, labels = [], []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        gy = y(v)
        grid.append(f'<line x1="{left}" x2="{left + w}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="#E1E8ED"/>')
        labels.append(f'<text x="{left - 10}" y="{gy + 4:.1f}" text-anchor="end">{f"{v:.2f}".replace(".", ",")}</text>')
    ticks = []
    last_i = len(pts) - 1
    for i in sorted({0, last_i // 3, 2 * last_i // 3, last_i}):
        ticks.append(f'<text x="{x(i):.1f}" y="{top + h + 26}" text-anchor="middle">{pts[i][0]:%d.%m}</text>')
    lx, ly = x(last_i), y(pts[-1][1])
    color = "#18324A"
    tail = ""
    if nxt:
        tx, ty = x(n), y(nxt["rate"])
        tail = (f'<line x1="{lx:.1f}" y1="{ly:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" stroke="#B8862E" '
                f'stroke-width="2.5" stroke-dasharray="5 4"/>'
                f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="5" fill="#fff" stroke="#B8862E" stroke-width="2.5"/>'
                f'<text class="v" x="{tx + 10:.1f}" y="{ty + 5:.1f}">{fmt_rate(nxt["rate"])}</text>'
                f'<text x="{tx + 10:.1f}" y="{ty + 24:.1f}">завтра</text>'
                f'<text class="v" x="{lx - 10:.1f}" y="{ly - 14:.1f}" text-anchor="end">{fmt_rate(pts[-1][1])}</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>text{{font:14px Inter,Arial,sans-serif;fill:#6B7A87}} .t{{font:600 20px Inter,Arial,sans-serif;fill:#1D2935}}
.s{{font:14px Inter,Arial,sans-serif;fill:#6B7A87}}
.v{{font:700 16px Inter,Arial,sans-serif;fill:#18324A;paint-order:stroke;stroke:#fff;stroke-width:5px}}</style>
<rect width="100%" height="100%" fill="#ffffff"/>
<rect width="100%" height="4" fill="#B8862E"/>
<text class="t" x="{left}" y="34">{s['name']} ({s['cur']}) · курс ЦБ, ₽</text>
<text class="s" x="{width - right}" y="34" text-anchor="end">{pts[0][0]:%d.%m.%Y} — {(nxt["date"] if nxt else pts[-1][0]):%d.%m.%Y}</text>
{''.join(grid)}{''.join(labels)}{''.join(ticks)}
<polygon points="{area}" fill="{color}" fill-opacity=".07"/>
<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>
<circle cx="{lx:.1f}" cy="{ly:.1f}" r="5" fill="{color}" stroke="#fff" stroke-width="2"/>
{tail if nxt else f'<text class="v" x="{lx + 10:.1f}" y="{ly + 5:.1f}">{fmt_rate(pts[-1][1])}</text>'}
</svg>'''


def chart_png(s: dict, scale: float = 1280 / 900) -> bytes:
    """SVG → PNG через тот же Chromium, что печатает PDF."""
    from .day_render import PdfUnavailable

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PdfUnavailable("pip install playwright && playwright install chromium") from exc
    svg = chart_svg(s)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                raise PdfUnavailable("playwright install chromium") from exc
            raise
        try:
            # Telegram ужимает фото до 1280 px по длинной стороне — рисуем ровно 1280, без пережатия
            page = browser.new_page(viewport={"width": 900, "height": 420}, device_scale_factor=scale)
            page.set_content(f"<html><body style='margin:0'>{svg}</body></html>")
            return page.screenshot(type="png")
        finally:
            browser.close()
