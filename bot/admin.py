from django.contrib import admin
from .models import Customer, Transaction, StoreInfo, ReportSettings


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("chat_id", "full_name", "phone", "is_admin", "is_registered", "bonus_balance", "debt_balance")
    list_filter = ("is_admin", "is_registered", "gender")
    search_fields = ("full_name", "phone", "chat_id")
    list_editable = ("is_admin",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("customer", "type", "document_number", "document_date", "amount", "bonus_amount")
    list_filter = ("type",)
    search_fields = ("document_number", "customer__full_name")


@admin.register(StoreInfo)
class StoreInfoAdmin(admin.ModelAdmin):
    pass


@admin.register(ReportSettings)
class ReportSettingsAdmin(admin.ModelAdmin):
    pass
