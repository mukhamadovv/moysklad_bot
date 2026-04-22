"""
Usage:
  python manage.py setup_webhooks
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from bot.telegram_api import set_webhook
from bot.moysklad_api import register_webhooks


class Command(BaseCommand):
    help = "Register the Telegram bot webhook and all MoySklad webhooks."

    def handle(self, *args, **options):
        # ── Telegram ─────────────────────────────────────────────────────────
        telegram_url = settings.TELEGRAM_WEBHOOK_URL
        if not telegram_url:
            self.stderr.write("TELEGRAM_WEBHOOK_URL is not set in settings/env.")
        else:
            result = set_webhook(telegram_url)
            if result.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"✅ Telegram webhook set: {telegram_url}"))
            else:
                self.stderr.write(f"❌ Telegram webhook failed: {result}")

        # ── MoySklad ─────────────────────────────────────────────────────────
        host = settings.TELEGRAM_WEBHOOK_URL.rsplit("/webhook/", 1)[0] if settings.TELEGRAM_WEBHOOK_URL else ""
        if not host:
            self.stderr.write("Cannot determine host from TELEGRAM_WEBHOOK_URL.")
        else:
            secret = settings.MOYSKLAD_WEBHOOK_SECRET
            results = register_webhooks(host, secret)
            for r in results:
                status = r.get("status")
                etype = r.get("entityType")
                action = r.get("action")
                if status in (200, 201):
                    self.stdout.write(self.style.SUCCESS(f"✅ MoySklad webhook: {etype}/{action}"))
                else:
                    resp = r.get("response", {})
                    self.stderr.write(f"❌ MoySklad webhook {etype}/{action}: {status} — {resp}")
