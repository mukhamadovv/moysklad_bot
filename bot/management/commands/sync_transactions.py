"""
Management command: sync_transactions
--------------------------------------
Fetches ALL MoySklad documents for every registered bot user and
creates missing Transaction records. Already-existing records are skipped.

Usage:
    python manage.py sync_transactions
    python manage.py sync_transactions --chat_id 123456789   # single user
"""

import logging
from decimal import Decimal
from datetime import datetime

from django.core.management.base import BaseCommand

from bot.models import Customer, Transaction
from bot.moysklad_api import get_counterparty_documents, get_counterparty_balance

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync all MoySklad transactions for registered bot users"

    def add_arguments(self, parser):
        parser.add_argument(
            "--chat_id",
            type=int,
            default=None,
            help="Sync only for the given Telegram chat_id (optional)",
        )

    def handle(self, *args, **options):
        chat_id_filter = options.get("chat_id")

        qs = Customer.objects.filter(is_registered=True).exclude(moysklad_id="")
        if chat_id_filter:
            qs = qs.filter(chat_id=chat_id_filter)

        total_customers = qs.count()
        self.stdout.write(f"Found {total_customers} registered user(s) with MoySklad ID.")

        total_created = 0
        total_skipped = 0

        for customer in qs:
            self.stdout.write(f"\n→ {customer.full_name or customer.chat_id} (ms_id={customer.moysklad_id})")

            try:
                docs = get_counterparty_documents(customer.moysklad_id)
            except Exception as e:
                self.stderr.write(f"  ❌ Failed to fetch docs: {e}")
                continue

            if not docs:
                self.stdout.write("  No documents found in MoySklad.")
                continue

            # Collect all existing moysklad_entity_ids for this customer
            existing_ids = set(
                Transaction.objects.filter(customer=customer)
                .exclude(moysklad_entity_id="")
                .values_list("moysklad_entity_id", flat=True)
            )

            created = 0
            skipped = 0

            for doc in docs:
                ms_id = doc.get("id", "")

                # Skip if already exists
                if ms_id and ms_id in existing_ids:
                    skipped += 1
                    continue

                # Parse document date
                doc_date = None
                moment = doc.get("moment", "")
                if moment:
                    try:
                        doc_date = datetime.strptime(moment[:19], "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass

                tx_type = doc.get("tx_type", "sale")
                amount = Decimal(str(doc.get("sum", 0)))
                entity_type = doc.get("entity_type", "")

                # Human-readable description
                type_labels = {
                    "demand":        "Отгрузка",
                    "retaildemand":  "Розничная продажа",
                    "salesreturn":   "Возврат",
                    "paymentin":     "Входящий платеж",
                    "paymentout":    "Исходящий платеж",
                    "cashin":        "Приходный ордер",
                    "cashout":       "Расходный ордер",
                }
                description = type_labels.get(entity_type, entity_type)

                Transaction.objects.create(
                    customer=customer,
                    type=tx_type,
                    document_number=doc.get("name", ""),
                    document_date=doc_date,
                    amount=amount,
                    bonus_amount=Decimal("0"),
                    pending_bonus=Decimal("0"),
                    debt_change=Decimal("0"),
                    description=description,
                    moysklad_entity_id=ms_id,
                )
                created += 1

            # Sync debt balance from MoySklad
            ms_balance = get_counterparty_balance(customer.moysklad_id)
            if ms_balance is not None:
                customer.debt_balance = Decimal(str(ms_balance))
                customer.save(update_fields=["debt_balance"])

            self.stdout.write(
                f"  ✅ Created: {created}  |  Skipped (already exist): {skipped}"
                f"  |  Balance: {customer.debt_balance}"
            )
            total_created += created
            total_skipped += skipped

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Total created: {total_created}, skipped: {total_skipped}"
            )
        )
