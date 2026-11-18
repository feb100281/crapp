"""
Excel-книга со сводной таблицей ДДС (нативная сводная, без макросов).

Лист «Сводная ДДС» — сводная по умной таблице raw_cf с листа «Данные».
Кэш сводной не хранится (saveData=0, refreshOnLoad=1): Excel строит её при
открытии. Книга собирается целиком из XML — без шаблона, сохранённого в Excel.

Фильтры отчёта: Банк, Банк / касса. Строки: деятельность → направление →
статья → подстатья → контрагент. Колонки: год → месяц (свёрнуты до года).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape

MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")

AMOUNT_FMT = 164  # #,##0;(#,##0);"–"


@dataclass
class Field:
    name: str
    kind: str = "s"      # s — текст, n — число, d — дата
    width: float = 14
    axis: str = ""       # row / col / page
    collapsed: bool = False


FIELDS = [
    Field("Дата", "d", 11),
    Field("Год", width=7, axis="col", collapsed=True),
    Field("Месяц", width=12, axis="col"),
    Field("Банк", width=26, axis="page"),
    Field("Банк / касса", width=34, axis="page"),
    Field("Валюта", width=8),
    Field("Деятельность", width=30, axis="row"),
    Field("Направление", width=16, axis="row"),
    Field("Статья", width=40, axis="row", collapsed=True),
    Field("Подстатья", width=40, axis="row", collapsed=True),
    Field("Контрагент", width=40, axis="row"),
    Field("ИНН", width=13),
    Field("Назначение", width=60),
    Field("Сумма, ₽", "n", 16),
    Field("Сумма в валюте", "n", 16),
    Field("Источник", width=12),
]
ROW_FIELDS = [i for i, f in enumerate(FIELDS) if f.axis == "row"]
COL_FIELDS = [i for i, f in enumerate(FIELDS) if f.axis == "col"]
PAGE_FIELDS = [i for i, f in enumerate(FIELDS) if f.axis == "page"]
DATA_FIELD = FIELDS.index(next(f for f in FIELDS if f.name == "Сумма, ₽"))

SOURCES = {"BANK": "Банк", "UNALLOC": "Банк", "FEE": "Комиссия", "DEBT": "Удержание",
           "MANUAL": "Проводка", "CASH": "Касса", "FX": "Курсовая"}

_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _txt(value) -> str:
    return _BAD.sub(" ", str(value))[:32000]


def _code_name(code, name, empty="Не разнесено") -> str:
    name = (name or "").strip() or empty
    return f"{code} {name}" if code else name


def rows_from(qs):
    """Строки витрины dashboard_cash_flow → строки листа «Данные» (порядок FIELDS)."""
    cols = ("date", "bank_name", "account_name", "currency", "activity", "activity_name",
            "direction", "direction_name", "article_code", "article_name", "cf_code", "cf_name",
            "cp_name", "inn", "description", "amount_rub", "amount_cur", "source")
    for r in qs.exclude(source="OPENING").order_by("date", "id").values_list(*cols):
        (d, bank, account, cur, act, act_name, dirn, dir_name, a_code, a_name, c_code, c_name,
         cp, inn, descr, rub, amount_cur, source) = r
        act_name = (act_name or "").strip() or "Не разнесено"
        if act is None or act_name == "Не разнесено":
            act = 9
        yield [
            d,
            str(d.year),
            f"{d.month:02d}. {MONTHS[d.month - 1]}",
            bank or "—",
            account or "—",
            cur or "",
            f"{act} · {act_name}",
            f"{dirn} · {dir_name}" if dirn else (dir_name or "—"),
            _code_name(a_code, a_name),
            _code_name(c_code, c_name),
            (cp or "").strip() or "(без контрагента)",
            inn or "",
            descr or "",
            round(rub or 0, 2),
            round(amount_cur, 2) if amount_cur is not None else None,
            SOURCES.get(source, source or ""),
        ]


# ----------------------------------------------------------------------
# XML
# ----------------------------------------------------------------------

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _a(value) -> str:
    return escape(str(value), {'"': "&quot;"})


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


def _rels(items) -> str:
    body = "".join(f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{target}"/>'
                   for rid, kind, target in items)
    return HEAD + f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{body}</Relationships>'


class _Strings:
    def __init__(self):
        self.index: dict[str, int] = {}
        self.count = 0

    def __call__(self, text: str) -> int:
        self.count += 1
        i = self.index.get(text)
        if i is None:
            i = self.index[text] = len(self.index)
        return i

    def xml(self) -> str:
        items = "".join(f'<si><t xml:space="preserve">{escape(t)}</t></si>' for t in self.index)
        return HEAD + f'<sst {NS} count="{self.count}" uniqueCount="{len(self.index)}">{items}</sst>'


def _data_sheet(rows: list, sst: _Strings) -> str:
    out = [HEAD, f'<worksheet {NS} {NS_R}><sheetViews><sheetView workbookViewId="0">'
                 '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                 '</sheetView></sheetViews><cols>']
    out += [f'<col min="{i + 1}" max="{i + 1}" width="{f.width}" customWidth="1"/>' for i, f in enumerate(FIELDS)]
    out.append('</cols><sheetData><row r="1">')
    out += [f'<c r="{_col(i)}1" t="s" s="3"><v>{sst(f.name)}</v></c>' for i, f in enumerate(FIELDS)]
    out.append("</row>")
    for n, row in enumerate(rows, start=2):
        cells = []
        for i, (f, v) in enumerate(zip(FIELDS, row)):
            if v is None or v == "":
                continue
            ref = f"{_col(i)}{n}"
            if f.kind == "d":
                cells.append(f'<c r="{ref}" s="1"><v>{_serial(v)}</v></c>')
            elif f.kind == "n":
                cells.append(f'<c r="{ref}" s="2"><v>{v!r}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{sst(_txt(v))}</v></c>')
        out.append(f'<row r="{n}">{"".join(cells)}</row>')
    out.append('</sheetData><tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>')
    return "".join(out)


def _table(last_row: int) -> str:
    ref = f"A1:{_col(len(FIELDS) - 1)}{last_row}"
    cols = "".join(f'<tableColumn id="{i + 1}" name="{_a(f.name)}"/>' for i, f in enumerate(FIELDS))
    return (HEAD + f'<table {NS} id="1" name="raw_cf" displayName="raw_cf" ref="{ref}" totalsRowShown="0">'
            f'<autoFilter ref="{ref}"/><tableColumns count="{len(FIELDS)}">{cols}</tableColumns>'
            '<tableStyleInfo name="CosmoTable" showFirstColumn="0" showLastColumn="0" '
            'showRowStripes="1" showColumnStripes="0"/></table>')


def _normalize(rows: list) -> None:
    """
    Excel сравнивает элементы сводной без учёта регистра и не принимает длиннее
    255 символов: «ИП Иванов» и «ИП ИВАНОВ» — один элемент. Приводим такие значения
    к первому написанию, иначе Excel считает книгу повреждённой.
    """
    for i in ROW_FIELDS + COL_FIELDS + PAGE_FIELDS:
        seen: dict[str, str] = {}
        for r in rows:
            v = " ".join(_txt(r[i]).split())[:255].rstrip() or "—"
            r[i] = seen.setdefault(v.casefold(), v)


def _items(rows: list) -> dict[int, list[str]]:
    """Значения полей сводной (строки, колонки, фильтры) — отсортированные."""
    return {i: sorted({r[i] for r in rows}, key=lambda v: (v.casefold(), v))
            for i in ROW_FIELDS + COL_FIELDS + PAGE_FIELDS}


def _cache(rows: list, items: dict) -> str:
    fields = []
    for i, f in enumerate(FIELDS):
        values = [r[i] for r in rows if r[i] not in (None, "")]
        blank = len(values) < len(rows)
        if i in items:
            shared = "".join(f'<s v="{_a(v)}"/>' for v in items[i])
            attrs = f'count="{len(items[i])}"'
            fields.append(f'<cacheField name="{_a(f.name)}" numFmtId="0"><sharedItems {attrs}>{shared}</sharedItems></cacheField>')
        elif f.kind == "d" and values:
            lo, hi = min(values), max(values)
            fields.append(f'<cacheField name="{_a(f.name)}" numFmtId="14"><sharedItems containsSemiMixedTypes="0" '
                          f'containsNonDate="0" containsDate="1" containsString="0" '
                          f'minDate="{lo:%Y-%m-%d}T00:00:00" maxDate="{hi:%Y-%m-%d}T00:00:00"/></cacheField>')
        elif f.kind == "n" and values:
            extra = ' containsBlank="1"' if blank else ""
            semi = "" if blank else ' containsSemiMixedTypes="0"'
            fields.append(f'<cacheField name="{_a(f.name)}" numFmtId="0"><sharedItems{semi} containsString="0" '
                          f'containsNumber="1"{extra} minValue="{min(values)!r}" maxValue="{max(values)!r}"/></cacheField>')
        else:
            extra = ' containsBlank="1"' if blank else ""
            fields.append(f'<cacheField name="{_a(f.name)}" numFmtId="0"><sharedItems{extra}/></cacheField>')
    return (HEAD + f'<pivotCacheDefinition {NS} {NS_R} refreshOnLoad="1" saveData="0" '
            f'refreshedBy="crapp" refreshedDate="{_excel_now()}" createdVersion="6" refreshedVersion="6" '
            f'minRefreshableVersion="3" recordCount="0">'
            '<cacheSource type="worksheet"><worksheetSource name="raw_cf"/></cacheSource>'
            f'<cacheFields count="{len(FIELDS)}">{"".join(fields)}</cacheFields></pivotCacheDefinition>')


def _excel_now() -> str:
    now = datetime.now()
    return f"{_serial(now.date()) + (now.hour * 3600 + now.minute * 60) / 86400:.6f}"


def _pivot(items: dict) -> str:
    pf = []
    for i, f in enumerate(FIELDS):
        if i in items:
            axis = {"row": "axisRow", "col": "axisCol", "page": "axisPage"}[f.axis]
            sd = ' sd="0"' if f.collapsed else ""
            its = "".join(f'<item x="{k}"{sd}/>' for k in range(len(items[i])))
            pf.append(f'<pivotField axis="{axis}" showAll="0"><items count="{len(items[i]) + 1}">'
                      f'{its}<item t="default"/></items></pivotField>')
        elif i == DATA_FIELD:
            pf.append(f'<pivotField dataField="1" numFmtId="{AMOUNT_FMT}" showAll="0"/>')
        else:
            pf.append('<pivotField showAll="0"/>')
    rows = "".join(f'<field x="{i}"/>' for i in ROW_FIELDS)
    cols = "".join(f'<field x="{i}"/>' for i in COL_FIELDS)
    pages = "".join(f'<pageField fld="{i}" hier="-1"/>' for i in PAGE_FIELDS)
    top = len(PAGE_FIELDS) + 2
    return (HEAD + f'<pivotTableDefinition {NS} name="СводнаяДДС" cacheId="1" applyNumberFormats="0" '
            'applyBorderFormats="0" applyFontFormats="0" applyPatternFormats="0" applyAlignmentFormats="0" '
            'applyWidthHeightFormats="1" dataCaption="Значения" grandTotalCaption="ИТОГО" updatedVersion="6" minRefreshableVersion="3" '
            'useAutoFormatting="1" itemPrintTitles="1" createdVersion="6" indent="0" outline="1" outlineData="1" '
            'multipleFieldFilters="0" rowHeaderCaption=" " colHeaderCaption=" ">'
            f'<location ref="A{top}:B{top + 2}" firstHeaderRow="1" firstDataRow="2" firstDataCol="1" '
            f'rowPageCount="{len(PAGE_FIELDS)}" colPageCount="1"/>'
            f'<pivotFields count="{len(FIELDS)}">{"".join(pf)}</pivotFields>'
            f'<rowFields count="{len(ROW_FIELDS)}">{rows}</rowFields>'
            '<rowItems count="1"><i t="grand"><x/></i></rowItems>'
            f'<colFields count="{len(COL_FIELDS)}">{cols}</colFields>'
            '<colItems count="1"><i t="grand"><x/></i></colItems>'
            f'<pageFields count="{len(PAGE_FIELDS)}">{pages}</pageFields>'
            f'<dataFields count="1"><dataField name="в российских рублях (RUB)" fld="{DATA_FIELD}" baseField="0" baseItem="0" '
            f'numFmtId="{AMOUNT_FMT}"/></dataFields>'
            '<pivotTableStyleInfo name="CosmoPivot" showRowHeaders="1" showColHeaders="1" '
            'showRowStripes="0" showColStripes="0" showLastColumn="1"/></pivotTableDefinition>')


def _pivot_sheet() -> str:
    return (HEAD + f'<worksheet {NS} {NS_R}><sheetViews><sheetView showGridLines="0" tabSelected="1" workbookViewId="0"/>'
            '</sheetViews><sheetFormatPr defaultRowHeight="16"/><cols><col min="1" max="1" width="60" customWidth="1"/>'
            '<col min="2" max="80" width="15" customWidth="1"/></cols><sheetData/></worksheet>')


# цвета — как в «красивом» Excel-отчёте (core.reports.xlsx)
GREEN, GREEN_2, GREEN_3 = "2F6656", "3D7A67", "1F5E4E"
LIGHT, LIGHT_2, ZEBRA, LINE = "E7F1ED", "F3F8F6", "F7F7F7", "D9D9D9"


def _dxf(fill=None, color=None, bold=False, **borders) -> str:
    """borders: сторона=(стиль, цвет), стороны — left/right/top/bottom/vertical/horizontal."""
    font = ""
    if bold or color:
        font = "<font>" + ("<b/>" if bold else "") + (f'<color rgb="FF{color}"/>' if color else "") + "</font>"
    fill_xml = (f'<fill><patternFill patternType="solid"><fgColor rgb="FF{fill}"/>'
                f'<bgColor rgb="FF{fill}"/></patternFill></fill>') if fill else ""
    border = ""
    if borders:
        border = "<border>" + "".join(
            f'<{side} style="{borders[side][0]}"><color rgb="FF{borders[side][1]}"/></{side}>'
            for side in ("left", "right", "top", "bottom", "vertical", "horizontal") if side in borders) + "</border>"
    return f"<dxf>{font}{fill_xml}{border}</dxf>"


GRID = ("thin", LINE)
DXFS = [
    _dxf(left=GRID, right=GRID, vertical=GRID, bottom=GRID, horizontal=("thin", "EDEDED")),  # 0 сетка
    _dxf(fill=GREEN, color="FFFFFF", bold=True, vertical=("thin", "FFFFFF"),
         horizontal=("thin", "FFFFFF")),                                                   # 1 шапка
    _dxf(fill=LIGHT, color=GREEN_3, bold=True, top=("medium", GREEN_3),
         bottom=("double", GREEN_3)),                                                      # 2 ИТОГО
    _dxf(fill=LIGHT, color=GREEN_3, bold=True, top=("thin", GREEN)),                        # 3 деятельность
    _dxf(fill=LIGHT_2),                                                                    # 4 направление
    _dxf(),                                                                                # 5 статья
    _dxf(color=GREEN_3, bold=True),                                                        # 6 подписи фильтров
    _dxf(fill=LIGHT_2, color=GREEN_3, bold=True, bottom=("thin", GREEN)),                   # 7 значения фильтров
    _dxf(fill=ZEBRA),                                                                      # 8 полоса данных
]
PIVOT_STYLE = [("wholeTable", 0), ("headerRow", 1), ("firstHeaderCell", 1), ("totalRow", 2),
               ("firstRowSubheading", 3), ("secondRowSubheading", 4), ("thirdRowSubheading", 5),
               ("pageFieldLabels", 6), ("pageFieldValues", 7)]
TABLE_STYLE = [("wholeTable", 0), ("headerRow", 1), ("firstRowStripe", 8)]


def _table_style(name: str, pivot: bool, elements) -> str:
    els = "".join(f'<tableStyleElement type="{t}" dxfId="{d}"/>' for t, d in elements)
    kind = 'table="0"' if pivot else 'pivot="0"'
    return f'<tableStyle name="{name}" {kind} count="{len(elements)}">{els}</tableStyle>'


STYLES = (HEAD + f'<styleSheet {NS}>'
          f'<numFmts count="1"><numFmt numFmtId="{AMOUNT_FMT}" formatCode="#,##0;(#,##0);&quot;–&quot;"/></numFmts>'
          '<fonts count="2"><font><sz val="11"/><name val="Aptos Narrow"/><family val="2"/><scheme val="minor"/></font>'
          '<font><b/><sz val="11"/><name val="Aptos Narrow"/><family val="2"/><scheme val="minor"/></font></fonts>'
          '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          f'<xf numFmtId="{AMOUNT_FMT}" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
          '<cellStyles count="1"><cellStyle name="Обычный" xfId="0" builtinId="0"/></cellStyles>'
          f'<dxfs count="{len(DXFS)}">{"".join(DXFS)}</dxfs>'
          '<tableStyles count="2" defaultTableStyle="CosmoTable" defaultPivotStyle="CosmoPivot">'
          + _table_style("CosmoPivot", True, PIVOT_STYLE) + _table_style("CosmoTable", False, TABLE_STYLE)
          + '</tableStyles></styleSheet>')

CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"
CONTENT_TYPES = (
    HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/xl/workbook.xml" ContentType="{CT}.sheet.main+xml"/>'
    f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{CT}.worksheet+xml"/>'
    f'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="{CT}.worksheet+xml"/>'
    f'<Override PartName="/xl/tables/table1.xml" ContentType="{CT}.table+xml"/>'
    f'<Override PartName="/xl/pivotTables/pivotTable1.xml" ContentType="{CT}.pivotTable+xml"/>'
    f'<Override PartName="/xl/pivotCache/pivotCacheDefinition1.xml" ContentType="{CT}.pivotCacheDefinition+xml"/>'
    f'<Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    f'<Override PartName="/xl/styles.xml" ContentType="{CT}.styles+xml"/>'
    f'<Override PartName="/xl/sharedStrings.xml" ContentType="{CT}.sharedStrings+xml"/>'
    '</Types>')

WORKBOOK = (HEAD + f'<workbook {NS} {NS_R}><bookViews><workbookView activeTab="0"/></bookViews><sheets>'
            '<sheet name="Сводная ДДС" sheetId="1" r:id="rId1"/><sheet name="Данные" sheetId="2" r:id="rId2"/>'
            '</sheets><calcPr calcId="191029"/>'
            '<pivotCaches><pivotCache cacheId="1" r:id="rId5"/></pivotCaches></workbook>')


def _theme() -> str:
    """Тема Office со шрифтами Aptos: без темы Excel рисует кнопки сводной мелкими."""
    from openpyxl.writer.theme import theme_xml

    return (theme_xml.replace('<a:latin typeface="Cambria"/>', '<a:latin typeface="Aptos Display"/>')
            .replace('<a:latin typeface="Calibri"/>', '<a:latin typeface="Aptos Narrow"/>'))


def build(rows) -> bytes:
    """rows — строки в порядке FIELDS (см. rows_from)."""
    rows = list(rows)
    if not rows:
        rows = [[date.today(), str(date.today().year), f"{date.today().month:02d}. {MONTHS[date.today().month - 1]}",
                 "—", "—", "", "—", "—", "—", "—", "—", "", "", 0.0, None, ""]]
    rows = [list(r) for r in rows]
    _normalize(rows)
    items = _items(rows)
    sst = _Strings()
    data = _data_sheet(rows, sst)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", _rels([("rId1", "officeDocument", "xl/workbook.xml")]))
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", _rels([
            ("rId1", "worksheet", "worksheets/sheet1.xml"),
            ("rId2", "worksheet", "worksheets/sheet2.xml"),
            ("rId3", "styles", "styles.xml"),
            ("rId4", "sharedStrings", "sharedStrings.xml"),
            ("rId5", "pivotCacheDefinition", "pivotCache/pivotCacheDefinition1.xml"),
            ("rId6", "theme", "theme/theme1.xml"),
        ]))
        z.writestr("xl/styles.xml", STYLES)
        z.writestr("xl/theme/theme1.xml", _theme())
        z.writestr("xl/worksheets/sheet1.xml", _pivot_sheet())
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels", _rels([("rId1", "pivotTable", "../pivotTables/pivotTable1.xml")]))
        z.writestr("xl/pivotTables/pivotTable1.xml", _pivot(items))
        z.writestr("xl/pivotTables/_rels/pivotTable1.xml.rels",
                   _rels([("rId1", "pivotCacheDefinition", "../pivotCache/pivotCacheDefinition1.xml")]))
        z.writestr("xl/pivotCache/pivotCacheDefinition1.xml", _cache(rows, items))
        z.writestr("xl/worksheets/sheet2.xml", data)
        z.writestr("xl/worksheets/_rels/sheet2.xml.rels", _rels([("rId1", "table", "../tables/table1.xml")]))
        z.writestr("xl/tables/table1.xml", _table(len(rows) + 1))
        z.writestr("xl/sharedStrings.xml", sst.xml())
    return buf.getvalue()
