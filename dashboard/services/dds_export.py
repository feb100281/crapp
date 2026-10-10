"""
Отчёт ДДС в Excel — тот же «красивый» файл, что кнопка Excel на дашборде
(помесячно, дни под плюсиками, расшифровка, операции). Для бота и Mini App.
"""

from __future__ import annotations

from django.contrib import admin

from ..models import CashFlow


def years() -> list[int]:
    return sorted(CashFlow.objects.order_by().values_list("year", flat=True).distinct())


def book(year: int | None = None):
    """Возвращает (Book, имя файла, report) или (None, '', None) если данных нет."""
    from ..admin.cash_flow_report_admin import CashFlowReportAdmin

    ys = years()
    if not ys:
        return None, "", None
    year = year if year in ys else ys[-1]
    view = CashFlowReportAdmin(CashFlow, admin.site)
    base = CashFlow.objects.all()
    report = view._report(None, base, {}, year, "m")
    if report.get("empty"):
        return None, "", None
    return view._book(None, base, {}, year, report), f"Отчёт ДДС {report['period_name']}", report
