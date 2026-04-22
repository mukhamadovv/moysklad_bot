"""
Форматирует человеко-читаемые сообщения Telegram для каждого типа события MoySklad.
"""
from datetime import datetime


# ─── Персонализированные форматтеры для каждого типа события MoySklad ─────────────

def fmt_customer_order(entity: dict, action: str) -> str:
    name = entity.get("name", "—")
    state = entity.get("state", {}).get("name", "—") if entity.get("state") else "—"
    total = entity.get("sum", 0) / 100  # MoySklad хранит суммы в копейках/тиинах
    date = entity.get("moment", "")
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        date_str = dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        date_str = date

    if action == "CREATE":
        return (
            f"🛒 <b>Создан новый заказ</b>\n"
            f"Заказ: <b>#{name}</b>\n"
            f"Дата: {date_str}\n"
            f"Сумма: {total:,.2f} UZS\n"
            f"Статус: {state}"
        )
    else:
        return (
            f"🔄 <b>Заказ обновлён</b>\n"
            f"Заказ: <b>#{name}</b>\n"
            f"Дата: {date_str}\n"
            f"Сумма: {total:,.2f} UZS\n"
            f"Новый статус: <b>{state}</b>"
        )


def fmt_payment(entity: dict, action: str) -> str:
    name = entity.get("name", "—")
    amount = entity.get("sum", 0) / 100
    date = entity.get("moment", "")
    purpose = entity.get("paymentPurpose", "")

    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        date_str = dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        date_str = date

    return (
        f"💳 <b>Платёж получен</b>\n"
        f"Платёж: <b>#{name}</b>\n"
        f"Сумма: {amount:,.2f} UZS\n"
        f"Дата: {date_str}"
        + (f"\nПримечание: {purpose}" if purpose else "")
    )


def fmt_demand(entity: dict, action: str) -> str:
    name = entity.get("name", "—")
    date = entity.get("moment", "")
    total = entity.get("sum", 0) / 100

    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        date_str = dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        date_str = date

    # Build product list from positions (injected by views.py)
    positions = entity.get("_positions", [])
    product_lines = []
    for pos in positions:
        p_name = pos.get("name", "Товар")
        qty = pos.get("quantity", 0)
        price = pos.get("price", 0) / 100  # kopeks → rubles
        line_total = price * qty
        product_lines.append(f"  • {p_name} × {int(qty)} — {line_total:,.2f}")

    is_debt = entity.get("_is_debt", False)
    pending_bonus = entity.get("_pending_bonus", 0)

    if is_debt:
        header = "📦 <b>Покупка в долг!</b>" if action == "CREATE" else "📦 <b>Обновление покупки</b>"
    else:
        header = "📦 <b>Новая покупка!</b>" if action == "CREATE" else "📦 <b>Обновление покупки</b>"

    text = (
        f"{header}\n"
        f"Документ: <b>#{name}</b>\n"
        f"Дата: {date_str}\n"
    )
    if product_lines:
        text += "\n".join(product_lines) + "\n"
    text += f"<b>Итого: {total:,.2f}</b>"

    if is_debt and pending_bonus > 0:
        text += (
            f"\n\n⏳ <b>Бонус за эту покупку: {pending_bonus:,.2f}</b>\n"
            f"<i>Будет начислен после погашения задолженности</i>"
        )

    return text


def fmt_sales_return(entity: dict, action: str) -> str:
    name = entity.get("name", "—")
    total = entity.get("sum", 0) / 100
    date = entity.get("moment", "")

    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        date_str = dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        date_str = date

    return (
        f"↩️ <b>Возврат зарегистрирован</b>\n"
        f"Возврат: <b>#{name}</b>\n"
        f"Сумма: {total:,.2f} UZS\n"
        f"Дата: {date_str}"
    )


# ─── Диспетчер ──────────────────────────────────────────────────────────────

FORMATTERS = {
    "customerorder": fmt_customer_order,
    "paymentin": fmt_payment,
    "cashin": fmt_payment,
    "paymentout": fmt_payment,
    "cashout": fmt_payment,
    "demand": fmt_demand,
    "retaildemand": fmt_demand,
    "salesreturn": fmt_sales_return,
}


def format_event(entity_type: str, entity: dict, action: str) -> str | None:
    """Возвращает отформатированное сообщение или None, если тип не поддерживается."""
    fn = FORMATTERS.get(entity_type.lower())
    if fn:
        return fn(entity, action)
    return None
