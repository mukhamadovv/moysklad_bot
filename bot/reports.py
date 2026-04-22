import io
from datetime import datetime
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

from bot.models import ReportSettings
from bot.moysklad_api import get_sales_report_by_product_group


# ── Styles ────────────────────────────────────────────────────────────────────

_thin = Side(style="thin", color="999999")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_header_font = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
_header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
_title_font = Font(name="Calibri", bold=True, size=14)
_subtitle_font = Font(name="Calibri", size=11, italic=True, color="666666")
_data_font = Font(name="Calibri", size=11)
_money_fmt = '#,##0.00'
_neg_money_font = Font(name="Calibri", size=11, color="CC0000")
_pos_money_font = Font(name="Calibri", size=11, color="228B22")
_center = Alignment(horizontal="center", vertical="center")
_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
_right = Alignment(horizontal="right", vertical="center")
_stripe_fill = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
_summary_font = Font(name="Calibri", bold=True, size=11)
_summary_fill = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
_balance_neg_fill = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")
_balance_pos_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")


def _style_header(ws, row, col_count):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = _header_font
        cell.fill = _header_fill
        cell.alignment = _header_align
        cell.border = _border


def _style_data_cell(cell, is_stripe=False):
    cell.font = _data_font
    cell.border = _border
    if is_stripe:
        cell.fill = _stripe_fill


def _fmt(value):
    """Format number Russian-style: −2 860 333,33"""
    v = Decimal(str(value))
    sign = "−" if v < 0 else ""
    v = abs(v)
    integer_part = int(v)
    decimal_part = f"{v - integer_part:.2f}"[2:]
    s = f"{integer_part:,}".replace(",", " ")
    return f"{sign}{s},{decimal_part}"


# ── Purchase History Excel ────────────────────────────────────────────────────

def generate_purchase_history(customer, transactions):
    wb = Workbook()
    ws = wb.active
    ws.title = "История операций"
    ws.sheet_properties.tabColor = "4472C4"

    # ── Title block ──
    ws.merge_cells("A1:H1")
    title_cell = ws.cell(row=1, column=1, value=f"📋 История операций — {customer.full_name}")
    title_cell.font = _title_font
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells("A2:H2")
    today = datetime.now().strftime("%d.%m.%Y %H:%M")
    ws.cell(row=2, column=1, value=f"Сформировано: {today}  |  Телефон: {customer.phone}").font = _subtitle_font
    ws.row_dimensions[2].height = 20

    # ── Balance summary block ──
    balance = customer.debt_balance
    bonus = customer.bonus_balance

    ws.merge_cells("A3:D3")
    bal_fill = _balance_neg_fill if balance < 0 else _balance_pos_fill if balance > 0 else _summary_fill
    bal_cell = ws.cell(row=3, column=1, value=f"💰 Баланс (МойСклад): {_fmt(balance)}")
    bal_cell.font = Font(name="Calibri", bold=True, size=12)
    bal_cell.fill = bal_fill
    bal_cell.border = _border
    for c in range(2, 5):
        ws.cell(row=3, column=c).fill = bal_fill
        ws.cell(row=3, column=c).border = _border

    ws.merge_cells("E3:H3")
    bon_cell = ws.cell(row=3, column=5, value=f"💎 Бонусы: {_fmt(bonus)}")
    bon_cell.font = Font(name="Calibri", bold=True, size=12)
    bon_cell.fill = _summary_fill
    bon_cell.border = _border
    for c in range(6, 9):
        ws.cell(row=3, column=c).fill = _summary_fill
        ws.cell(row=3, column=c).border = _border
    ws.row_dimensions[3].height = 26

    # ── Data table ──
    header_row = 5
    headers = ["№", "Дата", "Тип операции", "Номер документа",
               "Приход (+)", "Расход (−)", "Бонусы", "Описание (товары)"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=header_row, column=col, value=h)
    _style_header(ws, header_row, len(headers))
    ws.row_dimensions[header_row].height = 28

    total_income = Decimal("0")
    total_expense = Decimal("0")
    total_bonus = Decimal("0")

    # Determine which types are "income" vs "expense" from client's perspective
    income_types = {"payment_in", "cash_in", "return"}  # money coming back to client or reducing debt
    expense_types = {"sale", "payment_out", "cash_out"}  # money client pays or owes

    for idx, t in enumerate(transactions, 1):
        row = header_row + idx
        is_stripe = idx % 2 == 0

        is_income = t.type in income_types
        is_expense = t.type in expense_types

        income_val = float(t.amount) if is_income else ""
        expense_val = float(t.amount) if is_expense else ""

        if is_income:
            total_income += t.amount
        elif is_expense:
            total_expense += t.amount

        cells = [
            ws.cell(row=row, column=1, value=idx),
            ws.cell(row=row, column=2, value=t.document_date.strftime("%d.%m.%Y") if t.document_date else ""),
            ws.cell(row=row, column=3, value=t.get_type_display()),
            ws.cell(row=row, column=4, value=t.document_number),
            ws.cell(row=row, column=5, value=income_val),
            ws.cell(row=row, column=6, value=expense_val),
            ws.cell(row=row, column=7, value=float(t.bonus_amount) if t.bonus_amount != 0 else ""),
            ws.cell(row=row, column=8, value=t.description),
        ]

        for c in cells:
            _style_data_cell(c, is_stripe)

        cells[0].alignment = _center
        cells[1].alignment = _center
        cells[2].alignment = _left
        cells[3].alignment = _center

        # Income column — green
        cells[4].alignment = _right
        if isinstance(cells[4].value, (int, float)):
            cells[4].number_format = _money_fmt
            cells[4].font = _pos_money_font

        # Expense column — red
        cells[5].alignment = _right
        if isinstance(cells[5].value, (int, float)):
            cells[5].number_format = _money_fmt
            cells[5].font = _neg_money_font

        # Bonus column
        cells[6].alignment = _right
        if isinstance(cells[6].value, (int, float)):
            cells[6].number_format = _money_fmt
            if cells[6].value > 0:
                cells[6].font = _pos_money_font
            elif cells[6].value < 0:
                cells[6].font = _neg_money_font

        cells[7].alignment = _left

        total_bonus += t.bonus_amount

    # ── Totals row ──
    n = len(transactions)
    tr = header_row + n + 1
    ws.merge_cells(start_row=tr, start_column=1, end_row=tr, end_column=4)
    lbl = ws.cell(row=tr, column=1, value="ИТОГО")
    lbl.font = _summary_font
    lbl.fill = _summary_fill
    lbl.alignment = _right
    lbl.border = _border
    for c in range(2, 5):
        ws.cell(row=tr, column=c).fill = _summary_fill
        ws.cell(row=tr, column=c).border = _border

    inc_cell = ws.cell(row=tr, column=5, value=float(total_income) if total_income else "")
    inc_cell.font = Font(name="Calibri", bold=True, size=11, color="228B22")
    inc_cell.fill = _summary_fill
    inc_cell.number_format = _money_fmt
    inc_cell.alignment = _right
    inc_cell.border = _border

    exp_cell = ws.cell(row=tr, column=6, value=float(total_expense) if total_expense else "")
    exp_cell.font = Font(name="Calibri", bold=True, size=11, color="CC0000")
    exp_cell.fill = _summary_fill
    exp_cell.number_format = _money_fmt
    exp_cell.alignment = _right
    exp_cell.border = _border

    bon_total = ws.cell(row=tr, column=7, value=float(total_bonus) if total_bonus else "")
    bon_total.font = _summary_font
    bon_total.fill = _summary_fill
    bon_total.number_format = _money_fmt
    bon_total.alignment = _right
    bon_total.border = _border

    ws.cell(row=tr, column=8).fill = _summary_fill
    ws.cell(row=tr, column=8).border = _border

    # ── Column widths ──
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 16
    ws.column_dimensions["G"].width = 14
    ws.column_dimensions["H"].width = 45
    ws.freeze_panes = f"A{header_row + 1}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ── Admin Report Excel ────────────────────────────────────────────────────────

def generate_admin_report(date_from: datetime, date_to: datetime):
    rs = ReportSettings.get()
    rows = get_sales_report_by_product_group(
        rs.product_groups,
        date_from.strftime("%Y-%m-%d 00:00:00"),
        date_to.strftime("%Y-%m-%d 23:59:59"),
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Отчет по продажам"
    ws.sheet_properties.tabColor = "4472C4"

    period = f"{date_from.strftime('%d.%m.%Y')} — {date_to.strftime('%d.%m.%Y')}"

    ws.merge_cells("A1:F1")
    ws.cell(row=1, column=1, value=f"Отчет по продажам за {period}").font = _title_font
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A2:F2")
    ws.cell(row=2, column=1, value=f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}").font = _subtitle_font

    header_row = 4
    headers = ["№", "Товар", "Группа", "Кол-во продаж", "Сумма продаж", "Кол-во возвратов"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=header_row, column=col, value=h)
    _style_header(ws, header_row, len(headers))

    for idx, r in enumerate(rows, 1):
        row = header_row + idx
        is_stripe = idx % 2 == 0
        assortment = r.get("assortment", {})
        pf = assortment.get("productFolder", {})
        cells = [
            ws.cell(row=row, column=1, value=idx),
            ws.cell(row=row, column=2, value=assortment.get("name", "")),
            ws.cell(row=row, column=3, value=pf.get("name", "") if isinstance(pf, dict) else ""),
            ws.cell(row=row, column=4, value=r.get("sellQuantity", 0)),
            ws.cell(row=row, column=5, value=r.get("sellSum", 0) / 100),
            ws.cell(row=row, column=6, value=r.get("returnQuantity", 0)),
        ]
        for c in cells:
            _style_data_cell(c, is_stripe)
        cells[0].alignment = _center
        cells[3].alignment = _center
        cells[4].alignment = _right
        cells[4].number_format = _money_fmt
        cells[5].alignment = _center

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 18
    ws.freeze_panes = f"A{header_row + 1}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
