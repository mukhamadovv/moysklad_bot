import logging
from datetime import datetime, timezone
from decimal import Decimal

from django.db.models import Sum

from bot.models import Customer, StoreInfo, ReportSettings, Transaction
from bot.telegram_api import (
    send_message, send_photo, send_document, request_contact,
    get_menu_keyboard, profile_keyboard, gender_keyboard, settings_keyboard,
    store_info_keyboard, back_keyboard, confirm_keyboard, region_keyboard,
)
from bot.moysklad_api import (
    find_counterparty_by_phone, create_counterparty, update_counterparty,
    get_counterparty_balance, create_cash_out, create_payment_in, get_organization,
    get_counterparty_documents, get_demand_positions, get_product,
    get_all_counterparties, get_entity,
)
from bot.reports import generate_purchase_history, generate_admin_report

logger = logging.getLogger(__name__)


def _fmt(value):
    """Format number Russian-style: 2 860 333,33"""
    v = Decimal(str(value))
    sign = "−" if v < 0 else ""
    v = abs(v)
    integer_part = int(v)
    decimal_part = f"{v - integer_part:.2f}"[2:]  # "33"
    # Add spaces every 3 digits
    s = f"{integer_part:,}".replace(",", " ")
    return f"{sign}{s},{decimal_part}"

REGIONS = [
    "Ташкент", "Самарканд", "Бухара", "Наманган", "Андижан",
    "Фергана", "Нукус", "Карши", "Навои", "Ургенч",
    "Джизак", "Термез", "Гулистан", "Коканд", "Другой",
]


def handle_update(data: dict):
    message = data.get("message") or data.get("edited_message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    customer, created = Customer.objects.get_or_create(
        chat_id=chat_id,
        defaults={
            "first_name": message["chat"].get("first_name", ""),
            "username": message["chat"].get("username", ""),
        }
    )

    # Contact shared
    if "contact" in message:
        _handle_contact(customer, message)
        return

    # Photo shared (admin store photo)
    if "photo" in message and customer.state == "awaiting_store_photo":
        photo = message["photo"][-1]
        store = StoreInfo.get()
        store.photo_file_id = photo["file_id"]
        store.save()
        customer.state = "none"
        customer.save()
        send_message(chat_id, "✅ Изображение обновлено.", reply_markup=settings_keyboard())
        return

    text = (message.get("text") or "").strip()
    if not text:
        return

    if text == "⬅️ Назад":
        _handle_back(customer)
        return

    # State machine
    if customer.state != "none":
        _handle_state(customer, text)
        return

    # Menu buttons and commands
    if text in ("/start", "/register"):
        if customer.is_registered:
            send_message(chat_id, "👋 Вы уже зарегистрированы.", reply_markup=get_menu_keyboard(customer))
        else:
            customer.state = "awaiting_phone"
            customer.save()
            request_contact(chat_id)

    elif text == "ℹ️ О нас":
        _show_about(customer)

    elif text == "💰 Баланс":
        _show_balance(customer)

    elif text == "📋 История покупок":
        _show_history(customer)

    elif text == "👤 Профиль":
        _show_profile(customer)

    elif text == "✏️ Имя":
        customer.state = "awaiting_new_name"
        customer.save()
        send_message(chat_id, f"Ваше имя: <b>{customer.full_name}</b>\nПожалуйста, введите ваше новое имя и фамилию.", reply_markup=back_keyboard())

    elif text == "📅 Дата рождения":
        bd = customer.birthdate.strftime("%d.%m.%Y") if customer.birthdate else "не указана"
        customer.state = "awaiting_new_birthdate"
        customer.save()
        send_message(chat_id, f"Дата рождения: <b>{bd}</b>\nУкажите свою новую дату рождения в формате дд.мм.гггг.", reply_markup=back_keyboard())

    elif text == "⚧ Пол":
        customer.state = "awaiting_gender"
        customer.save()
        send_message(chat_id, "Выберите пол", reply_markup=gender_keyboard())

    elif text == "📊 Отчет клиентов" and customer.is_admin:
        customer.state = "awaiting_report_period"
        customer.save()
        send_message(chat_id, "Отчетный период следует представлять в формате\nдд.мм.гггг - дд.мм.гггг", reply_markup=back_keyboard())

    elif text == "⚙️ Настройки" and customer.is_admin:
        send_message(chat_id, "Настройки", reply_markup=settings_keyboard())

    elif text == "🏪 Информация о магазине" and customer.is_admin:
        _show_store_info_admin(customer)

    elif text == "🖼 Изменить изображение" and customer.is_admin:
        customer.state = "awaiting_store_photo"
        customer.save()
        send_message(chat_id, "Отправьте фотографию", reply_markup=back_keyboard())

    elif text == "✏️ Изменить текст" and customer.is_admin:
        customer.state = "awaiting_store_text"
        customer.save()
        send_message(chat_id, "Отправьте текст магазина", reply_markup=back_keyboard())

    elif text == "📊 Отчетность по клиентам" and customer.is_admin:
        rs = ReportSettings.get()
        groups = ", ".join(rs.product_groups) if rs.product_groups else "не настроено"
        send_message(chat_id, f"Группы товаров, использованные в отчете:\n{groups}", reply_markup=confirm_keyboard())

    elif text == "🏢 Поставщик" and customer.is_admin:
        rs = ReportSettings.get()
        send_message(
            chat_id,
            f"Данные поставщика\nНаименование: {rs.supplier_name}\nID: {rs.supplier_id}",
            reply_markup={"keyboard": [[{"text": "✏️ Изменить"}], [{"text": "⬅️ Назад"}]], "resize_keyboard": True}
        )

    elif text == "✏️ Изменить" and customer.is_admin:
        customer.state = "awaiting_supplier_id"
        customer.save()
        send_message(chat_id, "Отправьте идентификатор поставщика", reply_markup=back_keyboard())

    elif text == "💳 Выплата бонуса" and customer.is_admin:
        _show_client_list_for_action(customer, "awaiting_cashout_client")

    elif text == "📉 Погашение долга" and customer.is_admin:
        _show_client_list_for_action(customer, "awaiting_debt_client")

    elif text == "/help":
        send_message(chat_id, "ℹ️ <b>Доступные команды</b>\n\n/start — Регистрация / Вход\n/help — Показать это сообщение")

    else:
        if not customer.is_registered:
            customer.state = "awaiting_phone"
            customer.save()
            request_contact(chat_id)
        else:
            send_message(chat_id, "Используйте кнопки меню.", reply_markup=get_menu_keyboard(customer))


def _handle_contact(customer, message):
    contact = message["contact"]
    phone = contact.get("phone_number", "").strip()
    if phone and not phone.startswith("+"):
        phone = "+" + phone

    customer.phone = phone
    chat_id = customer.chat_id

    if customer.is_registered:
        cp = find_counterparty_by_phone(phone)
        if cp:
            customer.moysklad_id = cp["id"]
            customer.state = "none"
            customer.save()
            _import_history_from_moysklad(customer)
            send_message(chat_id, "✅ Вы успешно вошли в систему бота", reply_markup=get_menu_keyboard(customer))
        else:
            customer.state = "none"
            customer.save()
            send_message(chat_id, "❌ Аккаунт не найден. Используйте /start для регистрации.")
        return

    # Registration: check if counterparty exists
    cp = find_counterparty_by_phone(phone)
    if cp:
        customer.moysklad_id = cp["id"]
        customer.full_name = cp.get("name", "")
        customer.is_registered = True
        customer.state = "none"
        customer.save()
        _import_history_from_moysklad(customer)
        send_message(chat_id, "✅ Вы успешно вошли в систему бота", reply_markup=get_menu_keyboard(customer))
    else:
        customer.state = "awaiting_name"
        customer.save()
        send_message(chat_id, "Напишите свое имя и фамилию.", reply_markup=back_keyboard())


def _handle_state(customer, text):
    chat_id = customer.chat_id
    state = customer.state

    if state == "awaiting_name":
        customer.full_name = text
        customer.state = "awaiting_region"
        customer.save()
        send_message(chat_id, "Выберите свой регион", reply_markup=region_keyboard(REGIONS))

    elif state == "awaiting_region":
        customer.region = text
        customer.state = "awaiting_gender"
        customer.save()
        send_message(chat_id, "Выберите пол", reply_markup=gender_keyboard())

    elif state == "awaiting_gender":
        if text == "👨 Мужской":
            customer.gender = "male"
        elif text == "👩 Женский":
            customer.gender = "female"
        else:
            send_message(chat_id, "Пожалуйста, выберите пол кнопкой.", reply_markup=gender_keyboard())
            return

        if customer.is_registered:
            customer.state = "none"
            customer.save()
            send_message(chat_id, "✅ Пол обновлен.", reply_markup=get_menu_keyboard(customer))
            return

        customer.state = "awaiting_birthdate"
        customer.save()
        send_message(chat_id, "Пожалуйста, введите дату своего рождения в формате дд.мм.гггг.", reply_markup=back_keyboard())

    elif state == "awaiting_birthdate":
        try:
            bd = datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            send_message(chat_id, "❌ Неверный формат. Введите дату в формате дд.мм.гггг.", reply_markup=back_keyboard())
            return
        customer.birthdate = bd
        customer.is_registered = True
        customer.state = "none"
        cp = create_counterparty(
            name=customer.full_name, phone=customer.phone,
            description=f"Регион: {customer.region}, Пол: {customer.get_gender_display()}, ДР: {text}",
        )
        if cp:
            customer.moysklad_id = cp["id"]
        customer.save()
        send_message(chat_id, "✅ Вы успешно зарегистрировались!", reply_markup=get_menu_keyboard(customer))

    elif state == "awaiting_new_name":
        customer.full_name = text
        customer.state = "none"
        customer.save()
        if customer.moysklad_id:
            update_counterparty(customer.moysklad_id, {"name": text})
        send_message(chat_id, "✅ Имя обновлено.", reply_markup=get_menu_keyboard(customer))

    elif state == "awaiting_new_birthdate":
        try:
            bd = datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            send_message(chat_id, "❌ Неверный формат. Введите дату в формате дд.мм.гггг.", reply_markup=back_keyboard())
            return
        customer.birthdate = bd
        customer.state = "none"
        customer.save()
        send_message(chat_id, "✅ Дата рождения обновлена.", reply_markup=get_menu_keyboard(customer))

    elif state == "awaiting_report_period":
        _handle_report_period(customer, text)

    elif state == "awaiting_store_text":
        store = StoreInfo.get()
        store.text = text
        store.save()
        customer.state = "none"
        customer.save()
        send_message(chat_id, "✅ Текст обновлен.", reply_markup=settings_keyboard())

    elif state == "awaiting_supplier_id":
        cp = get_entity(f"https://api.moysklad.ru/api/remap/1.2/entity/counterparty/{text}")
        rs = ReportSettings.get()
        if cp:
            rs.supplier_id = text
            rs.supplier_name = cp.get("name", "")
            rs.save()
            customer.state = "none"
            customer.save()
            send_message(chat_id, f"✅ Поставщик обновлен: {rs.supplier_name}", reply_markup=settings_keyboard())
        else:
            send_message(chat_id, "❌ Поставщик не найден. Проверьте ID.", reply_markup=back_keyboard())

    elif state == "awaiting_cashout_client":
        _handle_select_client(customer, text, "awaiting_cashout_amount", "💳 Выплата бонуса")

    elif state == "awaiting_cashout_amount":
        _handle_cashout_amount(customer, text)

    elif state == "awaiting_debt_client":
        _handle_select_client(customer, text, "awaiting_debt_amount", "📉 Погашение долга")

    elif state == "awaiting_debt_amount":
        _handle_debt_amount(customer, text)


def _handle_back(customer):
    customer.state = "none"
    customer.save()
    if customer.is_registered:
        send_message(customer.chat_id, "Главное меню", reply_markup=get_menu_keyboard(customer))
    else:
        send_message(customer.chat_id, "Нажмите /start для начала.", reply_markup={"remove_keyboard": True})


def _import_history_from_moysklad(customer):
    """Import all historical documents from MoySklad for this customer.
    Creates Transaction records for history/Excel. Does NOT calculate bonus
    from historical data — new customers start with bonus_balance = 0.
    Syncs balance (debt) directly from MoySklad.
    """
    if not customer.moysklad_id:
        return

    try:
        docs = get_counterparty_documents(customer.moysklad_id)
    except Exception as e:
        logger.error("Failed to import history for %s: %s", customer, e)
        return

    existing_ids = set(
        Transaction.objects.filter(customer=customer)
        .exclude(moysklad_entity_id="")
        .values_list("moysklad_entity_id", flat=True)
    )

    imported = 0
    for doc in docs:
        if doc["id"] in existing_ids:
            continue

        doc_date = None
        try:
            moment = doc["moment"]
            if moment:
                doc_date = datetime.strptime(moment[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            doc_date = datetime.now(tz=timezone.utc)

        tx_type = doc["tx_type"]
        amount = Decimal(str(doc["sum"]))
        entity_type = doc["entity_type"]

        description_map = {
            "demand": "Продажа",
            "retaildemand": "Розничная продажа",
            "salesreturn": "Возврат",
            "paymentin": "Входящий платеж",
            "paymentout": "Исходящий платеж",
            "cashin": "Приходный ордер",
            "cashout": "Расходный ордер",
        }
        description = description_map.get(entity_type, entity_type)

        # For sales/returns, fetch product names
        if entity_type in ("demand", "retaildemand", "salesreturn"):
            try:
                positions = get_demand_positions(doc["id"], entity_type)
                product_names = []
                for pos in positions:
                    assortment = pos.get("assortment", {})
                    p_href = assortment.get("meta", {}).get("href", "")
                    qty = pos.get("quantity", 0)
                    p_name = assortment.get("name", "")
                    if not p_name and p_href:
                        prod = get_product(p_href)
                        if prod:
                            p_name = prod.get("name", "")
                    if p_name:
                        product_names.append(f"{p_name} ×{int(qty)}" if qty else p_name)
                if product_names:
                    description += ": " + ", ".join(product_names[:5])
                    if len(product_names) > 5:
                        description += f" (+{len(product_names) - 5})"
            except Exception as e:
                logger.warning("Could not fetch positions for %s/%s: %s", entity_type, doc["id"], e)

        Transaction.objects.create(
            customer=customer,
            type=tx_type,
            document_number=doc["name"],
            document_date=doc_date,
            amount=amount,
            bonus_amount=Decimal("0"),
            debt_change=Decimal("0"),
            description=description,
            moysklad_entity_id=doc["id"],
        )
        imported += 1

    # Sync balance from MoySklad (do NOT touch bonus_balance — stays 0 for new)
    ms_balance = get_counterparty_balance(customer.moysklad_id)
    if ms_balance is not None:
        customer.debt_balance = Decimal(str(ms_balance))
    customer.save()

    logger.info("Imported %d new docs for %s, MoySklad balance=%s", imported, customer, customer.debt_balance)


def _show_about(customer):
    store = StoreInfo.get()
    text = store.text or "Информация о магазине пока не добавлена."
    if store.photo_file_id:
        send_photo(customer.chat_id, store.photo_file_id, caption=text)
    else:
        send_message(customer.chat_id, text, reply_markup=get_menu_keyboard(customer))


def _show_balance(customer):
    # Sync balance from MoySklad
    ms_balance = Decimal("0")
    if customer.moysklad_id:
        raw = get_counterparty_balance(customer.moysklad_id)
        if raw is not None:
            ms_balance = Decimal(str(raw))
            customer.debt_balance = ms_balance
            customer.save()
    else:
        ms_balance = customer.debt_balance

    pending = customer.transactions.filter(pending_bonus__gt=0).aggregate(
        total=Sum('pending_bonus')
    )['total'] or Decimal('0')

    msg = (
        f"📊 <b>Ваш баланс</b>\n\n"
        f"💎 Бонусы: <b>{_fmt(customer.bonus_balance)}</b>\n"
    )
    if pending > 0:
        msg += (
            f"⏳ Ожидают начисления (Бонус): <b>{_fmt(pending)}</b>\n"
        )
    msg += f"💰 Баланс (МойСклад): <b>{_fmt(ms_balance)}</b>"

    send_message(
        customer.chat_id,
        msg,
        reply_markup=get_menu_keyboard(customer),
    )


def _show_history(customer):
    transactions = Transaction.objects.filter(customer=customer).order_by("-document_date")[:200]
    if not transactions:
        send_message(customer.chat_id, "📋 История покупок пуста.", reply_markup=get_menu_keyboard(customer))
        return
    file_bytes = generate_purchase_history(customer, transactions)
    today = datetime.now().strftime("%d.%m.%Y")
    send_document(
        customer.chat_id,
        (f"История_покупок_{today}.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        caption=f"📋 История покупок на {today}",
    )


def _show_profile(customer):
    gender_display = customer.get_gender_display() if customer.gender else "не указан"
    bd = customer.birthdate.strftime("%d.%m.%Y") if customer.birthdate else "не указана"
    send_message(
        customer.chat_id,
        f"👤 <b>Профиль</b>\n\nИмя: {customer.full_name}\nДата рождения: {bd}\nПол: {gender_display}",
        reply_markup=profile_keyboard(),
    )


def _show_store_info_admin(customer):
    store = StoreInfo.get()
    text = store.text or "Текст не задан."
    if store.photo_file_id:
        send_photo(customer.chat_id, store.photo_file_id, caption=text, reply_markup=store_info_keyboard())
    else:
        send_message(customer.chat_id, text, reply_markup=store_info_keyboard())


def _handle_report_period(customer, text):
    chat_id = customer.chat_id
    try:
        parts = text.replace("–", "-").split("-")
        date_from = datetime.strptime(parts[0].strip(), "%d.%m.%Y")
        date_to = datetime.strptime(parts[1].strip(), "%d.%m.%Y")
    except (ValueError, IndexError):
        send_message(chat_id, "❌ Неверный формат. Используйте: дд.мм.гггг - дд.мм.гггг", reply_markup=back_keyboard())
        return

    customer.state = "none"
    customer.save()
    try:
        file_bytes = generate_admin_report(date_from, date_to)
        period = f"{date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}"
        send_document(
            chat_id,
            (f"Отчет_{period}.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            caption=f"📊 Отчет клиентов за {period}",
        )
    except Exception as e:
        logger.exception("Report generation error")
        send_message(chat_id, f"❌ Ошибка генерации отчета: {e}", reply_markup=get_menu_keyboard(customer))


# ─────────────────────────────────────────────────────────────────────────────
#  Admin: Bonus cashout & Debt repayment
# ─────────────────────────────────────────────────────────────────────────────

def _show_client_list_for_action(admin: Customer, next_state: str):
    """Show list of registered bot users as buttons for admin to select."""
    clients = Customer.objects.filter(is_registered=True).order_by("full_name")
    if not clients.exists():
        send_message(admin.chat_id, "❌ Нет зарегистрированных пользователей.", reply_markup=get_menu_keyboard(admin))
        return

    # Build name -> client.id mapping stored in state_data for lookup on selection
    client_map = {}
    kb = []
    for client in clients:
        name = client.full_name or client.phone or str(client.chat_id)
        # Make key unique in case of duplicate names
        key = f"{name}#{client.id}"
        client_map[key] = client.id
        if next_state == "awaiting_cashout_client":
            label = f"{name} | 💎 {client.bonus_balance}"
        else:
            label = name
        kb.append([{"text": label}])
    kb.append([{"text": "⬅️ Назад"}])

    admin.state = next_state
    admin.state_data = {"client_map": client_map}
    admin.save()

    if next_state == "awaiting_cashout_client":
        msg = "💳 <b>Выплата бонуса</b>\nВыберите клиента:"
    else:
        msg = "📉 <b>Погашение долга</b>\nВыберите клиента:"

    send_message(admin.chat_id, msg, reply_markup={"keyboard": kb, "resize_keyboard": True})


def _handle_select_client(admin: Customer, text: str, next_state: str, action_label: str):
    """Parse client selection from button text and look up via client_map stored in state_data."""
    client_name = text.split("|")[0].strip()

    client_map = admin.state_data.get("client_map", {})
    # key format is "name#id"
    client_id = None
    for key, cid in client_map.items():
        if key.split("#")[0] == client_name:
            client_id = cid
            break

    if not client_id:
        send_message(admin.chat_id, "❌ Клиент не найден. Выберите из списка.", reply_markup=back_keyboard())
        return

    client = Customer.objects.filter(id=client_id).first()
    if not client:
        send_message(admin.chat_id, "❌ Клиент не найден.", reply_markup=get_menu_keyboard(admin))
        return

    admin.state = next_state
    admin.state_data = {"client_id": client.id}
    admin.save()

    if next_state == "awaiting_cashout_amount":
        send_message(
            admin.chat_id,
            f"💳 <b>Выплата бонуса</b>\n\n"
            f"Клиент: <b>{client.full_name}</b>\n"
            f"Бонусный баланс: <b>{_fmt(client.bonus_balance)}</b>\n\n"
            f"Из <b>{_fmt(client.bonus_balance)}</b> бонусов сколько хотите выплатить?\n"
            f"Введите сумму:",
            reply_markup=back_keyboard(),
        )
    else:
        # Fetch real balance from MoySklad
        debt = client.debt_balance
        if client.moysklad_id:
            ms_debt = get_counterparty_balance(client.moysklad_id)
            if ms_debt is not None:
                debt = Decimal(str(ms_debt))
                client.debt_balance = debt
                client.save()

        send_message(
            admin.chat_id,
            f"📉 <b>Погашение долга</b>\n\n"
            f"Клиент: <b>{client.full_name}</b>\n"
            f"💎 Бонусы: <b>{_fmt(client.bonus_balance)}</b>\n"
            f"💰 Баланс (МойСклад): <b>{_fmt(debt)}</b>\n\n"
            f"Введите сумму для погашения:",
            reply_markup=back_keyboard(),
        )


def _handle_cashout_amount(admin: Customer, text: str):
    """Process bonus cashout: deduct bonus, create cashout in MoySklad, notify client."""
    chat_id = admin.chat_id
    try:
        amount = Decimal(text.replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError
    except (ValueError, ArithmeticError):
        send_message(chat_id, "❌ Введите корректную сумму.", reply_markup=back_keyboard())
        return

    client_id = admin.state_data.get("client_id")
    client = Customer.objects.filter(id=client_id).first()
    if not client:
        admin.state = "none"
        admin.state_data = {}
        admin.save()
        send_message(chat_id, "❌ Клиент не найден.", reply_markup=get_menu_keyboard(admin))
        return

    if amount > client.bonus_balance:
        send_message(
            chat_id,
            f"❌ Недостаточно бонусов.\n"
            f"Баланс клиента: <b>{client.bonus_balance}</b>\n"
            f"Вы ввели: <b>{amount}</b>",
            reply_markup=back_keyboard(),
        )
        return

    # Create cashout in MoySklad (Расходный ордер with "Бонус" expense item)
    ms_result = None
    if client.moysklad_id:
        org = get_organization()
        if org:
            ms_result = create_cash_out(client.moysklad_id, float(amount), org["id"])

    # Deduct bonus
    client.bonus_balance -= amount
    client.save()

    # Log transaction
    doc_num = ms_result.get("name", "") if ms_result else f"CASHOUT-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    Transaction.objects.create(
        customer=client,
        type="cash_out",
        document_number=doc_num,
        document_date=datetime.now(),
        amount=amount,
        bonus_amount=-amount,
        description=f"Выплата бонуса наличными (админ: {admin.full_name})",
        moysklad_entity_id=ms_result.get("id", "") if ms_result else "",
    )

    admin.state = "none"
    admin.state_data = {}
    admin.save()

    ms_status = "✅ Документ создан в МойСклад" if ms_result else "⚠️ Не удалось создать документ в МойСклад"
    send_message(
        chat_id,
        f"✅ <b>Выплата выполнена!</b>\n\n"
        f"Клиент: {client.full_name}\n"
        f"Сумма выплаты: {_fmt(amount)}\n"
        f"Остаток бонусов: {_fmt(client.bonus_balance)}\n\n"
        f"{ms_status}",
        reply_markup=get_menu_keyboard(admin),
    )

    # Notify the client
    send_message(
        client.chat_id,
        f"💳 <b>Выплата бонуса</b>\n\n"
        f"Вам выплачено: <b>{_fmt(amount)}</b>\n"
        f"💎 Остаток бонусов: <b>{_fmt(client.bonus_balance)}</b>",
    )


def _handle_debt_amount(admin: Customer, text: str):
    """Process debt repayment with bonus: deduct from bonus, create payment in MoySklad to reduce debt, notify."""
    chat_id = admin.chat_id
    try:
        amount = Decimal(text.replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError
    except (ValueError, ArithmeticError):
        send_message(chat_id, "❌ Введите корректную сумму.", reply_markup=back_keyboard())
        return

    client_id = admin.state_data.get("client_id")
    client = Customer.objects.filter(id=client_id).first()
    if not client:
        admin.state = "none"
        admin.state_data = {}
        admin.save()
        send_message(chat_id, "❌ Клиент не найден.", reply_markup=get_menu_keyboard(admin))
        return

    # Refresh debt from MoySklad
    if client.moysklad_id:
        ms_debt = get_counterparty_balance(client.moysklad_id)
        if ms_debt is not None:
            client.debt_balance = Decimal(str(ms_debt))

    if amount > client.bonus_balance:
        send_message(
            chat_id,
            f"❌ Недостаточно бонусов.\n"
            f"Бонусы клиента: <b>{client.bonus_balance}</b>\n"
            f"Вы ввели: <b>{amount}</b>",
            reply_markup=back_keyboard(),
        )
        return

    # Debt is stored as a negative number (e.g. -75000 means 75000 owed)
    debt_amount = abs(client.debt_balance)
    if amount > debt_amount:
        send_message(
            chat_id,
            f"❌ Сумма превышает задолженность.\n"
            f"Задолженность: <b>{_fmt(debt_amount)}</b>\n"
            f"Вы ввели: <b>{amount}</b>",
            reply_markup=back_keyboard(),
        )
        return

    # Create payment in MoySklad (Входящий платеж) to reduce debt
    ms_payment = None
    if client.moysklad_id:
        org = get_organization()
        if org:
            ms_payment = create_payment_in(client.moysklad_id, float(amount), org["id"])

    # Deduct from bonus
    client.bonus_balance -= amount
    # Debt is negative, so adding the payment amount reduces the debt
    client.debt_balance += amount
    client.save()

    # Log transaction
    doc_num = ms_payment.get("name", "") if ms_payment else f"DEBTPAY-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    Transaction.objects.create(
        customer=client,
        type="payment_in",
        document_number=doc_num,
        document_date=datetime.now(),
        amount=amount,
        bonus_amount=-amount,
        debt_change=-amount,
        description=f"Погашение долга бонусами (админ: {admin.full_name})",
        moysklad_entity_id=ms_payment.get("id", "") if ms_payment else "",
    )

    admin.state = "none"
    admin.state_data = {}
    admin.save()

    ms_status = "✅ Платеж создан в МойСклад" if ms_payment else "⚠️ Не удалось создать платеж в МойСклад"
    send_message(
        chat_id,
        f"✅ <b>Долг погашен!</b>\n\n"
        f"Клиент: {client.full_name}\n"
        f"Сумма: {_fmt(amount)}\n"
        f"💎 Остаток бонусов: {_fmt(client.bonus_balance)}\n"
        f"💰 Баланс: {_fmt(client.debt_balance)}\n\n"
        f"{ms_status}",
        reply_markup=get_menu_keyboard(admin),
    )

    # Notify the client
    send_message(
        client.chat_id,
        f"📉 <b>Погашение задолженности</b>\n\n"
        f"Списано бонусов: <b>{_fmt(amount)}</b>\n"
        f"Задолженность уменьшена на: <b>{_fmt(amount)}</b>\n"
        f"💎 Остаток бонусов: <b>{_fmt(client.bonus_balance)}</b>\n"
        f"💰 Баланс: <b>{_fmt(client.debt_balance)}</b>",
    )
