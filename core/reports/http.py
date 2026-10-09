"""Ответы-файлы для выгрузок: Excel и CSV (под русский Excel)."""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _filename(name: str, ext: str) -> str:
    return f"{name} {timezone.localtime():%Y-%m-%d %H%M}.{ext}"


def xlsx_response(book, name: str) -> HttpResponse:
    """book — core.reports.xlsx.Book или готовые байты."""
    data = book if isinstance(book, bytes) else book.save()
    response = HttpResponse(data, content_type=XLSX)
    response["Content-Disposition"] = content_disposition_header(True, _filename(name, "xlsx"))
    return response


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (float, Decimal)):
        return f"{value:.2f}".replace(".", ",")
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value).replace("\r", " ").replace("\n", " ")


def csv_response(header: list, rows, name: str) -> HttpResponse:
    """UTF-8 с BOM, разделитель «;», десятичная запятая — открывается в Excel двойным кликом."""
    out = StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_value(v) for v in row])

    response = HttpResponse(out.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, _filename(name, "csv"))
    return response
