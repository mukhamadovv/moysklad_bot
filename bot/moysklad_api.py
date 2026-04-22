import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE = "https://api.moysklad.ru/api/remap/1.2"


def _headers():
    return {
        "Authorization": f"Bearer {settings.MOYSKLAD_TOKEN}",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip",
    }


def get_all_counterparties(limit: int = 200):
    """Fetch individual (person) counterparties from MoySklad, ordered by name.
    Excludes companies (ООО, etc.) and default retail buyer.
    Returns list of dicts with 'id' and 'name'.
    """
    url = f"{BASE}/entity/counterparty"
    results = []
    offset = 0
    while True:
        resp = requests.get(url, headers=_headers(), params={
            "limit": 100,
            "offset": offset,
            "order": "name,asc",
            "filter": "companyType=individual",
        }, timeout=10)
        if resp.status_code != 200:
            logger.error("Failed to get counterparties: %s %s", resp.status_code, resp.text[:200])
            break
        data = resp.json()
        rows = data.get("rows", [])
        for row in rows:
            name = row.get("name", "").strip()
            if not name or name.lower() in ("розничный покупатель",):
                continue
            results.append({"id": row["id"], "name": name})
        if len(results) >= limit or len(rows) < 100:
            break
        offset += 100
    return results[:limit]


def find_counterparty_by_phone(phone: str):
    if not phone:
        return None
    phones_to_try = [phone]
    if phone.startswith("+"):
        phones_to_try.append(phone[1:])
    for p in phones_to_try:
        url = f"{BASE}/entity/counterparty"
        resp = requests.get(url, headers=_headers(), params={"filter": f"phone={p}"}, timeout=10)
        if resp.status_code == 200:
            rows = resp.json().get("rows", [])
            if rows:
                return rows[0]
    return None


def create_counterparty(name: str, phone: str, **kwargs):
    data = {
        "name": name,
        "phone": phone,
        "companyType": "individual",
    }
    if kwargs.get("description"):
        data["description"] = kwargs["description"]
    resp = requests.post(f"{BASE}/entity/counterparty", headers=_headers(), json=data, timeout=10)
    if resp.status_code in (200, 201):
        return resp.json()
    logger.error("Failed to create counterparty: %s %s", resp.status_code, resp.text)
    return None


def update_counterparty(cp_id: str, data: dict):
    resp = requests.put(f"{BASE}/entity/counterparty/{cp_id}", headers=_headers(), json=data, timeout=10)
    if resp.status_code == 200:
        return resp.json()
    logger.error("Failed to update counterparty: %s %s", resp.status_code, resp.text)
    return None


def get_entity(href: str):
    resp = requests.get(href, headers=_headers(), timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return None


def get_order(order_href: str):
    return get_entity(order_href)


def get_counterparty_balance(cp_id: str):
    """Get counterparty balance from MoySklad report.
    Returns value in rubles. Positive = customer owes, negative = overpaid.
    """
    url = f"{BASE}/report/counterparty/{cp_id}"
    resp = requests.get(url, headers=_headers(), timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        # balance is in kopeks (1/100), positive = owes money
        return data.get("balance", 0) / 100
    logger.error("Failed to get counterparty balance: %s %s", resp.status_code, resp.text[:200])
    return 0


def get_organization():
    """Get the first organization from MoySklad (needed for creating documents)."""
    url = f"{BASE}/entity/organization"
    resp = requests.get(url, headers=_headers(), params={"limit": 1}, timeout=10)
    if resp.status_code == 200:
        rows = resp.json().get("rows", [])
        if rows:
            return rows[0]
    return None


def create_payment_in(counterparty_id: str, amount: float, organization_id: str):
    """Create Входящий платеж (Payment In) to reduce debt in MoySklad."""
    data = {
        "organization": {"meta": {"href": f"{BASE}/entity/organization/{organization_id}", "type": "organization", "mediaType": "application/json"}},
        "agent": {"meta": {"href": f"{BASE}/entity/counterparty/{counterparty_id}", "type": "counterparty", "mediaType": "application/json"}},
        "sum": int(amount * 100),
    }
    resp = requests.post(f"{BASE}/entity/paymentin", headers=_headers(), json=data, timeout=10)
    if resp.status_code in (200, 201):
        return resp.json()
    logger.error("Failed to create paymentin: %s %s", resp.status_code, resp.text)
    return None


def get_counterparty_documents(cp_id: str):
    """Fetch all historical documents for a counterparty from MoySklad.
    Returns list of dicts with type, id, name, moment, sum, etc.
    """
    agent_href = f"{BASE}/entity/counterparty/{cp_id}"
    documents = []

    entity_types = [
        ("demand", "sale"),
        ("retaildemand", "sale"),
        ("salesreturn", "return"),
        ("paymentin", "payment_in"),
        ("paymentout", "payment_out"),
        ("cashin", "cash_in"),
        ("cashout", "cash_out"),
    ]

    for entity_type, tx_type in entity_types:
        url = f"{BASE}/entity/{entity_type}"
        # For retaildemand the counterparty field is 'agent'
        filter_param = f"agent={agent_href}"
        try:
            resp = requests.get(url, headers=_headers(), params={
                "filter": filter_param,
                "limit": 1000,
                "order": "moment,asc",
            }, timeout=30)
            if resp.status_code == 200:
                rows = resp.json().get("rows", [])
                for row in rows:
                    documents.append({
                        "entity_type": entity_type,
                        "tx_type": tx_type,
                        "id": row.get("id", ""),
                        "name": row.get("name", ""),
                        "moment": row.get("moment", ""),
                        "sum": row.get("sum", 0) / 100,  # kopeks to rubles
                        "raw": row,
                    })
            else:
                logger.warning("Failed to fetch %s for cp %s: %s", entity_type, cp_id, resp.status_code)
        except Exception as e:
            logger.error("Error fetching %s for cp %s: %s", entity_type, cp_id, e)

    # Sort by moment
    documents.sort(key=lambda d: d.get("moment", ""))
    return documents


def get_demand_positions(demand_id: str, entity_type: str = "demand"):
    """Get positions. entity_type can be 'demand', 'retaildemand', 'salesreturn'."""
    url = f"{BASE}/entity/{entity_type}/{demand_id}/positions"
    resp = requests.get(url, headers=_headers(), timeout=10)
    if resp.status_code == 200:
        return resp.json().get("rows", [])
    logger.warning("Failed to get positions for %s/%s: %s", entity_type, demand_id, resp.status_code)
    return []


def get_product(product_href: str):
    resp = requests.get(product_href, headers=_headers(), timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return None


def calculate_bonus_for_demand(demand_id: str, entity_type: str = "demand"):
    """Calculate bonus based on product 'Бонусная сумма' attribute."""
    positions = get_demand_positions(demand_id, entity_type)
    total_bonus = 0
    for pos in positions:
        assortment = pos.get("assortment", {})
        product_href = assortment.get("meta", {}).get("href", "")
        if not product_href:
            continue
        product = get_product(product_href)
        if not product:
            continue
        attributes = product.get("attributes", [])
        for attr in attributes:
            if attr.get("name") == "Бонусная сумма":
                try:
                    bonus_per_unit = float(attr.get("value", 0))
                    quantity = float(pos.get("quantity", 0))
                    total_bonus += bonus_per_unit * quantity
                    logger.debug("Bonus: product=%s, per_unit=%s, qty=%s", product.get("name"), bonus_per_unit, quantity)
                except (ValueError, TypeError):
                    pass
                break
    logger.info("Total bonus for %s/%s: %s", entity_type, demand_id, total_bonus)
    return total_bonus


def create_cash_out(counterparty_id: str, amount: float, organization_id: str, expense_item_name: str = "Бонус"):
    url = f"{BASE}/entity/expenseitem"
    resp = requests.get(url, headers=_headers(), params={"filter": f"name={expense_item_name}"}, timeout=10)
    expense_item = None
    if resp.status_code == 200:
        rows = resp.json().get("rows", [])
        if rows:
            expense_item = rows[0]

    data = {
        "organization": {"meta": {"href": f"{BASE}/entity/organization/{organization_id}", "type": "organization", "mediaType": "application/json"}},
        "agent": {"meta": {"href": f"{BASE}/entity/counterparty/{counterparty_id}", "type": "counterparty", "mediaType": "application/json"}},
        "sum": int(amount * 100),
    }
    if expense_item:
        data["expenseItem"] = {"meta": expense_item["meta"]}

    resp = requests.post(f"{BASE}/entity/cashout", headers=_headers(), json=data, timeout=10)
    if resp.status_code in (200, 201):
        return resp.json()
    logger.error("Failed to create cashout: %s %s", resp.status_code, resp.text)
    return None


def get_product_groups():
    url = f"{BASE}/entity/productfolder"
    resp = requests.get(url, headers=_headers(), params={"limit": 100}, timeout=10)
    if resp.status_code == 200:
        return resp.json().get("rows", [])
    return []


def get_sales_report_by_product_group(group_names: list, date_from: str, date_to: str):
    url = f"{BASE}/report/profit/byproduct"
    params = {
        "momentFrom": date_from,
        "momentTo": date_to,
        "limit": 1000,
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    if resp.status_code == 200:
        return resp.json().get("rows", [])
    return []


def register_webhooks(host: str, secret: str = ""):
    """Register MoySklad webhooks for all relevant entity types."""
    webhook_url = f"{host}/webhook/moysklad/"
    entity_types = [
        "customerorder", "demand", "retaildemand", "salesreturn",
        "paymentin", "paymentout", "cashin", "cashout",
    ]
    actions = ["CREATE", "UPDATE"]
    results = []

    # Fetch existing webhooks
    url = f"{BASE}/entity/webhook"
    resp = requests.get(url, headers=_headers(), timeout=10)
    existing = []
    if resp.status_code == 200:
        existing = resp.json().get("rows", [])

    for et in entity_types:
        for action in actions:
            data = {
                "url": webhook_url,
                "action": action,
                "entityType": et,
            }
            if secret:
                data["secret"] = secret

            # Check if already registered for this url+entityType+action
            match = next((
                w for w in existing
                if w.get("entityType") == et
                and w.get("action") == action
                and w.get("url") == webhook_url
            ), None)

            if match:
                # Update existing webhook to ensure secret is in sync
                wh_id = match["id"]
                r = requests.put(f"{url}/{wh_id}", headers=_headers(), json=data, timeout=10)
                results.append({
                    "entityType": et, "action": action,
                    "status": r.status_code,
                    "response": r.json() if r.content else {},
                })
            else:
                r = requests.post(url, headers=_headers(), json=data, timeout=10)
                results.append({
                    "entityType": et, "action": action,
                    "status": r.status_code,
                    "response": r.json() if r.content else {},
                })

    return results
