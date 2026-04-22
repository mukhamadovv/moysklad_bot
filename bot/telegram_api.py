import requests
from django.conf import settings

API = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(f"{API}/sendMessage", json=payload, timeout=10)


def send_photo(chat_id, photo, caption="", reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(f"{API}/sendPhoto", json=payload, timeout=10)


def send_document(chat_id, document, caption=""):
    return requests.post(
        f"{API}/sendDocument",
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"document": document},
        timeout=30,
    )


def request_contact(chat_id, text="Отправьте свой номер телефона, нажав на кнопку ниже."):
    markup = {
        "keyboard": [
            [{"text": "📱 Поделиться номером телефона", "request_contact": True}],
            [{"text": "⬅️ Назад"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
    return send_message(chat_id, text, reply_markup=markup)


def remove_keyboard(chat_id, text):
    markup = {"remove_keyboard": True}
    return send_message(chat_id, text, reply_markup=markup)


def client_menu_keyboard():
    return {
        "keyboard": [
            [{"text": "ℹ️ О нас"}, {"text": "💰 Баланс"}],
            [{"text": "📋 История покупок"}],
            [{"text": "👤 Профиль"}],
        ],
        "resize_keyboard": True,
    }


def admin_menu_keyboard():
    return {
        "keyboard": [
            [{"text": "ℹ️ О нас"}, {"text": "💰 Баланс"}],
            [{"text": "📋 История покупок"}],
            [{"text": "👤 Профиль"}],
            [{"text": "💳 Выплата бонуса"}, {"text": "📉 Погашение долга"}],
            [{"text": "📊 Отчет клиентов"}, {"text": "⚙️ Настройки"}],
        ],
        "resize_keyboard": True,
    }


def get_menu_keyboard(customer):
    if customer.is_admin:
        return admin_menu_keyboard()
    return client_menu_keyboard()


def profile_keyboard():
    return {
        "keyboard": [
            [{"text": "✏️ Имя"}, {"text": "📅 Дата рождения"}, {"text": "⚧ Пол"}],
            [{"text": "⬅️ Назад"}],
        ],
        "resize_keyboard": True,
    }


def gender_keyboard():
    return {
        "keyboard": [
            [{"text": "👨 Мужской"}, {"text": "👩 Женский"}],
            [{"text": "⬅️ Назад"}],
        ],
        "resize_keyboard": True,
    }


def settings_keyboard():
    return {
        "keyboard": [
            [{"text": "🏪 Информация о магазине"}],
            [{"text": "📊 Отчетность по клиентам"}],
            [{"text": "🏢 Поставщик"}],
            [{"text": "⬅️ Назад"}],
        ],
        "resize_keyboard": True,
    }


def store_info_keyboard():
    return {
        "keyboard": [
            [{"text": "🖼 Изменить изображение"}],
            [{"text": "✏️ Изменить текст"}],
            [{"text": "⬅️ Назад"}],
        ],
        "resize_keyboard": True,
    }


def back_keyboard():
    return {
        "keyboard": [[{"text": "⬅️ Назад"}]],
        "resize_keyboard": True,
    }


def confirm_keyboard():
    return {
        "keyboard": [
            [{"text": "✅ Подтвердить"}],
            [{"text": "⬅️ Назад"}],
        ],
        "resize_keyboard": True,
    }


def region_keyboard(regions):
    kb = [[{"text": r}] for r in regions]
    kb.append([{"text": "⬅️ Назад"}])
    return {"keyboard": kb, "resize_keyboard": True}


def set_webhook(url: str):
    """Set the Telegram bot webhook URL."""
    resp = requests.post(f"{API}/setWebhook", json={"url": url}, timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return {"ok": False, "description": resp.text}
