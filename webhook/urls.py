from django.urls import path
from . import views

urlpatterns = [
    path("telegram/", views.telegram_webhook, name="telegram_webhook"),
    path("moysklad/", views.moysklad_webhook, name="moysklad_webhook"),
]
