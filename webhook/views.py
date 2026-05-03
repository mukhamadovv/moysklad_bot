import json
import logging
import hmac
import hashlib
from decimal import Decimal
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from bot.models import Customer, Transaction, ReportSettings
from bot.telegram_api import send_message
from bot.moysklad_api import (
    get_entity, calculate_bonus_for_demand, calculate_bonus_and_bearing_amount,
    create_cash_out, get_counterparty_balance, get_demand_positions, get_product,
)
from bot.formatters import format_event
from bot.bot_handler import handle_update, _fmt

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Telegram webhook
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def telegram_webhook(request):
    """Receives all Telegram updates for the bot."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    try:
        handle_update(data)
    except Exception as exc:
        logger.exception("Error handling Telegram update: %s", exc)

    return HttpResponse("ok")


# ─────────────────────────────────────────────────────────────────────────────
#  MoySklad webhook
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def moysklad_webhook(request):
    """
    Receives event notifications from MoySklad.
    Verifies the request using HMAC-SHA256 signature.
    """
    # ── Signature verification ────────────────────────────────────────────────
    secret = settings.MOYSKLAD_WEBHOOK_SECRET
    if secret:
        body = request.body
        signature = request.headers.get("X-Lognex-Signature", "")
        if signature:
            # Signature present — verify it. Reject if it doesn't match.
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                logger.warning("MoySklad webhook: signature mismatch — request rejected")
                return HttpResponse(status=403)
        else:
            # No signature sent — webhook registered without secret yet.
            # Allow through but warn so the admin knows to re-run setup_webhooks.
            logger.warning(
                "MoySklad webhook: no signature header — run 'manage.py setup_webhooks' "
                "to register webhooks with a secret for full security"
            )

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("MoySklad webhook: invalid JSON")
        return HttpResponse(status=400)

    logger.info("MoySklad webhook received: %s", json.dumps(data, ensure_ascii=False)[:500])

    events = data.get("events", [])
    for event in events:
        try:
            _handle_moysklad_event(event)
        except Exception as exc:
            logger.exception("Error handling MoySklad event: %s", exc)

    return JsonResponse({"ok": True})


def _handle_moysklad_event(event: dict):
    """Process a single MoySklad event and send a Telegram message if relevant."""
    meta = event.get("meta", {})
    entity_type = meta.get("type", "")
    entity_href = meta.get("href", "")
    action = event.get("action", "")

    logger.info("Processing event: type=%s, action=%s, href=%s", entity_type, action, entity_href)

    if not entity_href:
        logger.warning("No entity_href in event")
        return

    # ── DELETE: entity is already gone — work from local Transaction data ─────
    if action == "DELETE":
        entity_id = entity_href.rstrip("/").split("/")[-1]
        txs = Transaction.objects.filter(moysklad_entity_id=entity_id).select_related("customer")
        for tx in txs:
            cust = tx.customer

            # Reverse bonus depending on transaction type
            if tx.type in ("sale",):
                # Demand deleted — reverse earned bonus; pending bonus just cancels
                if tx.bonus_amount > 0:
                    cust.bonus_balance = max(Decimal("0"), cust.bonus_balance - tx.bonus_amount)
            elif tx.type in ("payment_in", "cash_in"):
                # Payment deleted — reverse any bonus that was credited on this payment
                if tx.bonus_amount > 0:
                    cust.bonus_balance = max(Decimal("0"), cust.bonus_balance - tx.bonus_amount)
                # Also restore pending_bonus back on the linked demand transactions
                # (re-open the debt that this payment had covered proportionally)
                # We can't perfectly reconstruct which demands — best effort: leave as-is
                # and let balance sync from МойСклад cover the debt side.

            # Sync balance from МойСклад (the document is already gone there)
            ms_bal = get_counterparty_balance(cust.moysklad_id)
            if ms_bal is not None:
                cust.debt_balance = Decimal(str(ms_bal))
            cust.save()

            # Build notification text based on what was deleted
            if tx.type in ("payment_in", "cash_in"):
                del_text = (
                    f"🗑 <b>Платёж удалён: #{tx.document_number}</b>\n"
                    f"Сумма была: <b>{_fmt(tx.amount)}</b>\n"
                )
                if tx.bonus_amount > 0:
                    del_text += f"❌ Отменено начисление бонуса: <b>{_fmt(tx.bonus_amount)}</b>\n"
            else:
                del_text = (
                    f"🗑 <b>Отгрузка удалена: #{tx.document_number}</b>\n"
                    f"Сумма была: <b>{_fmt(tx.amount)}</b>\n"
                )
                if tx.bonus_amount > 0:
                    del_text += f"❌ Списан начисленный бонус: <b>{_fmt(tx.bonus_amount)}</b>\n"
                if tx.pending_bonus > 0:
                    del_text += f"⏳ Отменён ожидаемый бонус: <b>{_fmt(tx.pending_bonus)}</b>\n"

            del_text += (
                f"\n💎 Бонусы: {_fmt(cust.bonus_balance)}\n"
                f"💰 Баланс: {_fmt(cust.debt_balance)}"
            )
            send_message(cust.chat_id, del_text)
            tx.delete()
            logger.info("DELETE processed: type=%s, entity_id=%s, customer=%s", tx.type, entity_id, cust.chat_id)
        return

    entity = get_entity(entity_href)
    if not entity:
        logger.warning("Could not fetch entity %s", entity_href)
        return

    agent = entity.get("agent")
    if not agent:
        logger.info("No agent in entity %s, skipping", entity_type)
        return

    moysklad_cp_id = ""
    agent_meta = agent.get("meta", {})
    agent_href = agent_meta.get("href", "")
    if agent_href:
        moysklad_cp_id = agent_href.rstrip("/").split("/")[-1]

    if not moysklad_cp_id:
        logger.info("Could not extract counterparty ID from agent")
        return

    logger.info("Counterparty ID: %s", moysklad_cp_id)

    customers = Customer.objects.filter(moysklad_id=moysklad_cp_id)
    if not customers.exists():
        logger.info("No registered customer with moysklad_id=%s", moysklad_cp_id)
        return

    entity_id = entity.get("id", "")
    entity_name = entity.get("name", "")
    total = Decimal(str(entity.get("sum", 0))) / 100
    moment_str = entity.get("moment", "")

    try:
        moment = datetime.fromisoformat(moment_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        moment = None

    for customer in customers:
        # ── Demand or RetailDemand (sale) ─────────────────────────────────
        # demand: only CREATE (UPDATE fires when payment is linked — skip it)
        # retaildemand: CREATE and UPDATE both fine (Kassa)
        if entity_type == "demand" and action == "CREATE":
            bonus, bearing_amount = calculate_bonus_and_bearing_amount(entity_id, entity_type)
            bonus = Decimal(str(bonus))
            bearing_amount = Decimal(str(bearing_amount))

            # Sync balance from MoySklad
            ms_bal = get_counterparty_balance(customer.moysklad_id)
            if ms_bal is not None:
                customer.debt_balance = Decimal(str(ms_bal))
            customer.save()

            Transaction.objects.update_or_create(
                moysklad_entity_id=entity_id, customer=customer,
                defaults={
                    "type": "sale", "document_number": entity_name,
                    "document_date": moment, "amount": total,
                    "bonus_amount": Decimal("0"),
                    "pending_bonus": bonus,
                    "bonus_bearing_amount": bearing_amount,
                    "debt_change": total,
                    "description": f"Отгрузка {entity_name} (в долг)",
                }
            )

            # Inject debt/pending-bonus info so the formatter can show it
            entity["_is_debt"] = True
            entity["_pending_bonus"] = float(bonus)

            logger.info("Demand(debt) processed: customer=%s, total=%s, pending_bonus=%s", customer.chat_id, total, bonus)

        elif entity_type == "demand" and action == "UPDATE":
            new_bonus, new_bearing = calculate_bonus_and_bearing_amount(entity_id, entity_type)
            new_bonus = Decimal(str(new_bonus))
            new_bearing = Decimal(str(new_bearing))

            try:
                tx = Transaction.objects.get(moysklad_entity_id=entity_id, customer=customer)
            except Transaction.DoesNotExist:
                logger.info("Demand UPDATE: no local transaction found for %s, treating as CREATE", entity_id)
                # Fall back: create it as if it were a CREATE event
                ms_bal = get_counterparty_balance(customer.moysklad_id)
                if ms_bal is not None:
                    customer.debt_balance = Decimal(str(ms_bal))
                customer.save()
                Transaction.objects.create(
                    customer=customer, type="sale",
                    moysklad_entity_id=entity_id,
                    document_number=entity_name, document_date=moment,
                    amount=total, bonus_amount=Decimal("0"),
                    pending_bonus=new_bonus, bonus_bearing_amount=new_bearing,
                    debt_change=total,
                    description=f"Отгрузка {entity_name} (в долг)",
                )
                entity["_is_debt"] = True
                entity["_pending_bonus"] = float(new_bonus)
                continue

            # ── Demand voided/unposted ("Снять с проведения") ─────────────────
            # МойСклад fires UPDATE with applicable=false instead of DELETE.
            # Treat this the same as deletion: reverse bonuses and notify.
            if entity.get("applicable") is False:
                if tx.bonus_amount > 0:
                    customer.bonus_balance = max(Decimal("0"), customer.bonus_balance - tx.bonus_amount)
                ms_bal = get_counterparty_balance(customer.moysklad_id)
                if ms_bal is not None:
                    customer.debt_balance = Decimal(str(ms_bal))
                customer.save()

                void_text = (
                    f"🗑 <b>Отгрузка отменена: #{tx.document_number}</b>\n"
                    f"Сумма была: <b>{_fmt(tx.amount)}</b>\n"
                )
                if tx.bonus_amount > 0:
                    void_text += f"❌ Списан начисленный бонус: <b>{_fmt(tx.bonus_amount)}</b>\n"
                if tx.pending_bonus > 0:
                    void_text += f"⏳ Отменён ожидаемый бонус: <b>{_fmt(tx.pending_bonus)}</b>\n"
                void_text += (
                    f"\n💎 Бонусы: {_fmt(customer.bonus_balance)}\n"
                    f"💰 Баланс: {_fmt(customer.debt_balance)}"
                )
                send_message(customer.chat_id, void_text)
                tx.delete()
                logger.info("Demand VOIDED (applicable=false): entity_id=%s, customer=%s", entity_id, customer.chat_id)
                continue

            old_total = tx.amount
            old_bonus_total = tx.bonus_amount + tx.pending_bonus  # original total bonus

            # If nothing meaningful changed (e.g. just a payment-link update), skip
            if total == old_total and new_bonus == old_bonus_total and entity.get("applicable") is not False:
                logger.info("Demand UPDATE: no product changes detected, skipping: %s", entity_id)
                continue

            # Real product/price edit — recompute earned vs pending split
            if old_bonus_total > 0:
                earned_ratio = tx.bonus_amount / old_bonus_total
                new_earned = (new_bonus * earned_ratio).quantize(Decimal("0.01"))
                new_pending = new_bonus - new_earned
            else:
                new_earned = Decimal("0")
                new_pending = new_bonus

            # Apply bonus_balance delta (only the already-earned portion matters)
            bonus_delta = new_earned - tx.bonus_amount
            customer.bonus_balance = max(Decimal("0"), customer.bonus_balance + bonus_delta)

            ms_bal = get_counterparty_balance(customer.moysklad_id)
            if ms_bal is not None:
                customer.debt_balance = Decimal(str(ms_bal))
            customer.save()

            tx.amount = total
            tx.bonus_amount = new_earned
            tx.pending_bonus = new_pending
            tx.bonus_bearing_amount = new_bearing
            tx.document_number = entity_name
            tx.document_date = moment
            tx.debt_change = total
            tx.description = f"Отгрузка {entity_name} (в долг) [изменено]"
            tx.save()

            amount_diff = total - old_total
            bonus_diff = new_bonus - old_bonus_total
            edit_text = (
                f"✏️ <b>Отгрузка изменена: #{entity_name}</b>\n"
                f"Сумма: <b>{_fmt(total)}</b>"
            )
            if amount_diff != 0:
                sign = "+" if amount_diff > 0 else ""
                edit_text += f" ({sign}{_fmt(amount_diff)})"
            edit_text += "\n"
            if new_earned > 0:
                edit_text += f"✅ Начислено бонусов: <b>{_fmt(new_earned)}</b>"
                if bonus_delta != 0:
                    sign = "+" if bonus_delta > 0 else ""
                    edit_text += f" ({sign}{_fmt(bonus_delta)})"
                edit_text += "\n"
            if new_pending > 0:
                edit_text += f"⏳ Ожидают начисления: <b>{_fmt(new_pending)}</b>"
                if bonus_diff != 0 and new_earned == 0:
                    sign = "+" if bonus_diff > 0 else ""
                    edit_text += f" ({sign}{_fmt(bonus_diff)})"
                edit_text += "\n"
            edit_text += (
                f"\n💎 Бонусы: {_fmt(customer.bonus_balance)}\n"
                f"💰 Баланс: {_fmt(customer.debt_balance)}"
            )
            send_message(customer.chat_id, edit_text)
            logger.info(
                "Demand UPDATE processed: customer=%s, total %s→%s, bonus %s→%s",
                customer.chat_id, old_total, total, old_bonus_total, new_bonus,
            )
            continue

        elif entity_type == "retaildemand" and action in ("CREATE", "UPDATE"):
            bonus = Decimal(str(calculate_bonus_for_demand(entity_id, entity_type)))

            if bonus > 0:
                customer.bonus_balance += bonus

            ms_bal = get_counterparty_balance(customer.moysklad_id)
            if ms_bal is not None:
                customer.debt_balance = Decimal(str(ms_bal))
            customer.save()

            Transaction.objects.update_or_create(
                moysklad_entity_id=entity_id, customer=customer,
                defaults={
                    "type": "sale", "document_number": entity_name,
                    "document_date": moment, "amount": total,
                    "bonus_amount": bonus,
                    "debt_change": total,
                    "description": f"Розничная продажа {entity_name}",
                }
            )
            logger.info("RetailDemand processed: customer=%s, total=%s, bonus=%s", customer.chat_id, total, bonus)

        elif entity_type == "salesreturn" and action in ("CREATE", "UPDATE"):
            bonus = Decimal(str(calculate_bonus_for_demand(entity_id, "salesreturn")))
            customer.bonus_balance = max(Decimal("0"), customer.bonus_balance - bonus)

            ms_bal = get_counterparty_balance(customer.moysklad_id)
            if ms_bal is not None:
                customer.debt_balance = Decimal(str(ms_bal))
            customer.save()

            Transaction.objects.update_or_create(
                moysklad_entity_id=entity_id, customer=customer,
                defaults={
                    "type": "return", "document_number": entity_name,
                    "document_date": moment, "amount": total,
                    "bonus_amount": -bonus, "debt_change": -total,
                    "description": f"Возврат {entity_name}",
                }
            )

        elif entity_type == "cashout" and action in ("CREATE", "UPDATE"):
            expense_name = _get_expense_item_name(entity)
            if expense_name == "Бонус":
                customer.bonus_balance = max(Decimal("0"), customer.bonus_balance - total)
                customer.save()
                Transaction.objects.update_or_create(
                    moysklad_entity_id=entity_id, customer=customer,
                    defaults={
                        "type": "cash_out", "document_number": entity_name,
                        "document_date": moment, "amount": total,
                        "bonus_amount": -total,
                        "description": f"Списание бонуса (наличные) {entity_name}",
                    }
                )

        elif entity_type == "paymentout" and action in ("CREATE", "UPDATE"):
            expense_name = _get_expense_item_name(entity)
            if expense_name == "Бонус":
                customer.bonus_balance = max(Decimal("0"), customer.bonus_balance - total)
                customer.save()
                Transaction.objects.update_or_create(
                    moysklad_entity_id=entity_id, customer=customer,
                    defaults={
                        "type": "payment_out", "document_number": entity_name,
                        "document_date": moment, "amount": total,
                        "bonus_amount": -total,
                        "description": f"Списание бонуса (платеж) {entity_name}",
                    }
                )

        elif entity_type in ("cashin", "paymentin") and action in ("CREATE", "UPDATE"):
            # If the bot already created a Transaction for this entity (e.g. debt paid via bonus),
            # skip processing entirely to avoid double notifications.
            if Transaction.objects.filter(moysklad_entity_id=entity_id, customer=customer).exists():
                logger.info("Payment %s already processed by bot, skipping webhook.", entity_id)
                continue

            expense_name = _get_expense_item_name(entity)
            is_debt_repayment = expense_name == "Погашение долга" or any(
                attr.get("name") == "Погашение долга" and attr.get("value") is True
                for attr in entity.get("attributes", [])
            )
            if is_debt_repayment:
                # Debt repayment — deduct bonus, sync balance
                customer.bonus_balance = max(Decimal("0"), customer.bonus_balance - total)
                ms_bal = get_counterparty_balance(customer.moysklad_id)
                if ms_bal is not None:
                    customer.debt_balance = Decimal(str(ms_bal))
                customer.save()
                rs = ReportSettings.get()
                if rs.supplier_id:
                    org_href = entity.get("organization", {}).get("meta", {}).get("href", "")
                    org_id = org_href.rstrip("/").split("/")[-1] if org_href else ""
                    if org_id:
                        create_cash_out(rs.supplier_id, float(total), org_id)
                Transaction.objects.update_or_create(
                    moysklad_entity_id=entity_id, customer=customer,
                    defaults={
                        "type": "payment_in", "document_number": entity_name,
                        "document_date": moment, "amount": total,
                        "bonus_amount": -total, "debt_change": -total,
                        "description": f"Погашение долга {entity_name}",
                    }
                )
            else:
                # Regular payment — find linked demands via 'operations'
                linked_demands = []
                total_bonus = Decimal("0")
                for op in entity.get("operations", []):
                    op_type = op.get("meta", {}).get("type", "")
                    if op_type in ("demand", "retaildemand"):
                        op_href = op.get("meta", {}).get("href", "")
                        op_id = op_href.rstrip("/").split("/")[-1] if op_href else ""
                        if op_id:
                            bonus = Decimal(str(calculate_bonus_for_demand(op_id, op_type)))
                            if bonus > 0:
                                total_bonus += bonus
                                # Move pending_bonus → bonus_amount now that debt is paid
                                Transaction.objects.filter(
                                    customer=customer, moysklad_entity_id=op_id, type="sale"
                                ).update(bonus_amount=bonus, pending_bonus=Decimal("0"))
                            # Get demand name for notification
                            demand_entity = get_entity(op_href)
                            if demand_entity:
                                linked_demands.append(demand_entity.get("name", op_id))

                # ── No linked operations (direct Приходный ордер) ──────────────
                # Transfer pending bonuses proportionally based on payment vs
                # the REMAINING unpaid amount of purchases that carry pending bonuses.
                if not entity.get("operations") and entity_type == "cashin":
                    pending_txs = list(customer.transactions.filter(
                        pending_bonus__gt=0
                    ).order_by("document_date"))

                    total_pending = sum(tx.pending_bonus for tx in pending_txs)

                    if total_pending > 0:
                        # Remaining bonus-bearing amount per purchase =
                        #   bonus_bearing_amount × (pending_bonus / original_pending)
                        # This uses ONLY the portion of each purchase that belongs to
                        # bonus-carrying products (ignoring zero-bonus products), so
                        # partial payments on non-bonus items don't dilute the ratio.
                        pending_purchases_remaining = Decimal("0")
                        for tx in pending_txs:
                            original_pending = tx.bonus_amount + tx.pending_bonus
                            if original_pending > 0:
                                remaining_ratio = tx.pending_bonus / original_pending
                                # Use bonus_bearing_amount if stored; fall back to tx.amount
                                base = tx.bonus_bearing_amount if tx.bonus_bearing_amount > 0 else tx.amount
                                pending_purchases_remaining += base * remaining_ratio

                        if pending_purchases_remaining > 0:
                            ratio = min(total / pending_purchases_remaining, Decimal("1"))
                            bonus_to_transfer = (ratio * total_pending).quantize(Decimal("0.01"))
                        else:
                            bonus_to_transfer = total_pending

                        if bonus_to_transfer > 0:
                            # Drain pending_bonus from oldest transactions first
                            remaining = bonus_to_transfer
                            for tx in pending_txs:
                                if remaining <= 0:
                                    break
                                transfer = min(tx.pending_bonus, remaining)
                                tx.bonus_amount += transfer
                                tx.pending_bonus -= transfer
                                tx.save()
                                remaining -= transfer

                            total_bonus += bonus_to_transfer
                            logger.info(
                                "Proportional bonus transfer: customer=%s, payment=%s, "
                                "pending_remaining=%s, ratio=%s, bonus=%s",
                                customer.chat_id, total, pending_purchases_remaining,
                                ratio, bonus_to_transfer,
                            )

                customer.bonus_balance += total_bonus
                ms_bal = get_counterparty_balance(customer.moysklad_id)
                if ms_bal is not None:
                    customer.debt_balance = Decimal(str(ms_bal))
                customer.save()

                desc = f"Оплата {entity_name}"
                if linked_demands:
                    desc += f" (заказы: {', '.join(linked_demands)})"

                Transaction.objects.update_or_create(
                    moysklad_entity_id=entity_id, customer=customer,
                    defaults={
                        "type": "payment_in", "document_number": entity_name,
                        "document_date": moment, "amount": total,
                        "bonus_amount": total_bonus, "debt_change": -total,
                        "description": desc,
                    }
                )

                # Custom notification for payment with bonus
                pay_text = (
                    f"💳 <b>Оплата получена!</b>\n"
                    f"Платёж: <b>#{entity_name}</b>\n"
                    f"Сумма: <b>{_fmt(total)}</b>\n"
                )
                if linked_demands:
                    pay_text += f"Заказы: {', '.join(linked_demands)}\n"
                if total_bonus > 0:
                    pay_text += f"✅ Начислен бонус: <b>{_fmt(total_bonus)}</b>\n"
                pay_text += (
                    f"\n💎 Бонусы: {_fmt(customer.bonus_balance)}\n"
                    f"💰 Баланс: {_fmt(customer.debt_balance)}"
                )
                send_message(customer.chat_id, pay_text)
                logger.info("Payment: customer=%s, amount=%s, bonus=%s, demands=%s",
                            customer.chat_id, total, total_bonus, linked_demands)
                continue  # skip default notification below

        # Send notification — inject product positions for demand/retaildemand
        if entity_type in ("demand", "retaildemand"):
            try:
                positions = get_demand_positions(entity_id, entity_type)
                pos_list = []
                for pos in positions:
                    assortment = pos.get("assortment", {})
                    p_name = assortment.get("name", "")
                    if not p_name:
                        p_href = assortment.get("meta", {}).get("href", "")
                        if p_href:
                            prod = get_product(p_href)
                            p_name = prod.get("name", "Товар") if prod else "Товар"
                    pos_list.append({
                        "name": p_name,
                        "quantity": pos.get("quantity", 0),
                        "price": pos.get("price", 0),
                    })
                entity["_positions"] = pos_list
            except Exception as e:
                logger.warning("Could not fetch positions for notification: %s", e)

        text = format_event(entity_type, entity, action)
        if text:
            # For debt sales show pending bonus in footer instead of earned bonus
            if entity.get("_is_debt") and entity.get("_pending_bonus", 0) > 0:
                text += (
                    f"\n\n💎 Бонусы: {_fmt(customer.bonus_balance)}\n"
                    f"⏳ Ожидают начисления: {_fmt(entity['_pending_bonus'])}\n"
                    f"💰 Баланс: {_fmt(customer.debt_balance)}"
                )
            else:
                text += (
                    f"\n\n💎 Бонусы: {_fmt(customer.bonus_balance)}\n"
                    f"💰 Баланс: {_fmt(customer.debt_balance)}"
                )
            send_message(customer.chat_id, text)


def _get_expense_item_name(entity: dict) -> str:
    expense_item = entity.get("expenseItem", {})
    name = expense_item.get("name", "")
    if not name:
        ei_href = expense_item.get("meta", {}).get("href", "")
        if ei_href:
            ei = get_entity(ei_href)
            name = ei.get("name", "") if ei else ""
    return name
