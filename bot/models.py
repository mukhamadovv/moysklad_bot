from django.db import models


class Customer(models.Model):
    GENDER_CHOICES = [
        ("male", "Мужской"),
        ("female", "Женский"),
    ]
    STATE_CHOICES = [
        ("none", "None"),
        ("awaiting_phone", "Awaiting Phone"),
        ("awaiting_name", "Awaiting Name"),
        ("awaiting_region", "Awaiting Region"),
        ("awaiting_gender", "Awaiting Gender"),
        ("awaiting_birthdate", "Awaiting Birthdate"),
        ("awaiting_new_name", "Awaiting New Name"),
        ("awaiting_new_birthdate", "Awaiting New Birthdate"),
        ("awaiting_report_period", "Awaiting Report Period"),
        ("awaiting_store_photo", "Awaiting Store Photo"),
        ("awaiting_store_text", "Awaiting Store Text"),
        ("awaiting_supplier_id", "Awaiting Supplier ID"),
        ("awaiting_cashout_client", "Awaiting Cashout Client"),
        ("awaiting_cashout_amount", "Awaiting Cashout Amount"),
        ("awaiting_debt_client", "Awaiting Debt Client"),
        ("awaiting_debt_amount", "Awaiting Debt Amount"),
    ]

    chat_id = models.BigIntegerField(unique=True)
    phone = models.CharField(max_length=20, blank=True, default="")
    moysklad_id = models.CharField(max_length=100, blank=True, default="")
    first_name = models.CharField(max_length=150, blank=True, default="")
    last_name = models.CharField(max_length=150, blank=True, default="")
    full_name = models.CharField(max_length=300, blank=True, default="")
    username = models.CharField(max_length=150, blank=True, default="")
    region = models.CharField(max_length=200, blank=True, default="")
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, default="")
    birthdate = models.DateField(null=True, blank=True)
    is_admin = models.BooleanField(default=False)
    is_registered = models.BooleanField(default=False)
    bonus_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    debt_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    state = models.CharField(max_length=30, choices=STATE_CHOICES, default="none")
    state_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.full_name or self.chat_id} ({self.phone})"


class StoreInfo(models.Model):
    photo_file_id = models.CharField(max_length=500, blank=True, default="")
    text = models.TextField(blank=True, default="")
    contact_info = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store Info"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ReportSettings(models.Model):
    product_groups = models.JSONField(default=list, blank=True)
    supplier_name = models.CharField(max_length=300, blank=True, default="")
    supplier_id = models.CharField(max_length=100, blank=True, default="")

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Transaction(models.Model):
    TYPE_CHOICES = [
        ("sale", "Продажа"),
        ("return", "Возврат"),
        ("bonus_add", "Начисление бонуса"),
        ("bonus_deduct", "Списание бонуса"),
        ("payment_in", "Входящий платеж"),
        ("payment_out", "Исходящий платеж"),
        ("cash_in", "Приходный ордер"),
        ("cash_out", "Расходный ордер"),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="transactions")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    document_number = models.CharField(max_length=100, blank=True, default="")
    document_date = models.DateTimeField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pending_bonus = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Sum of (price × qty) for products that actually carry a bonus in this demand.
    # Used as the denominator when computing proportional bonus transfer on partial payment.
    bonus_bearing_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    debt_change = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField(blank=True, default="")
    moysklad_entity_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-document_date"]
