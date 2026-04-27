import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import json
import os
import re
import random
import xml.etree.ElementTree as ET
import base64

# ================= CONFIG =================
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

ONBUY_CONSUMER_KEY = os.getenv("ONBUY_CONSUMER_KEY")
ONBUY_SECRET_KEY = os.getenv("ONBUY_SECRET_KEY")

MIN_PROFIT = 0.15
PLATFORM_FEE = 0.18

TOTAL_BATCHES = 5
SKIP_HOURS = 6
DAILY_API_LIMIT = 4800

PK_TZ = ZoneInfo("Asia/Karachi")

# ================= GOOGLE SHEET =================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

sheet = client.open("OnBuy_Feed_Master").sheet1
data = sheet.get_all_records()

# ================= EBAY TOKEN =================
def get_ebay_token():
    encoded = base64.b64encode(
        f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()
    ).decode()

    res = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"
        }
    )

    return res.json().get("access_token")

# ================= EBAY =================
def get_ebay_data(url, token):
    try:
        match = re.search(r"/itm/(\d+)", url)
        if not match:
            return None, None

        item_id = match.group(1)

        res = requests.get(
            f"https://api.ebay.com/buy/browse/v1/item/v1|{item_id}|0",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"
            }
        )

        data = res.json()

        price = float(data.get("price", {}).get("value", 0))

        stock = 0
        avail = data.get("estimatedAvailabilities", [])
        if avail and avail[0].get("estimatedAvailabilityStatus") == "IN_STOCK":
            stock = avail[0].get("estimatedAvailableQuantity", 5)

        print(f"eBay → Stock: {stock}, Price: {price}")
        return stock, price

    except Exception as e:
        print("eBay error:", e)
        return None, None

# ================= ONBUY =================
def update_onbuy_product(sku, price, quantity):
    try:
        url = "https://api.onbuy.com/v2/products/update"

        headers = {
            "Authorization": "Basic " + base64.b64encode(
                f"{ONBUY_CONSUMER_KEY}:{ONBUY_SECRET_KEY}".encode()
            ).decode(),
            "Content-Type": "application/json"
        }

        payload = {
            "products": [
                {
                    "sku": str(sku),
                    "price": float(price),
                    "quantity": int(quantity)
                }
            ]
        }

        res = requests.post(url, json=payload, headers=headers)
        print(f"OnBuy → {sku} → {res.status_code}")

    except Exception as e:
        print("OnBuy error:", e)

# ================= INIT =================
root = ET.Element("products")
ebay_token = get_ebay_token()

api_calls = 0
current_hour = datetime.now(PK_TZ).hour
batch_index = current_hour % TOTAL_BATCHES

# ================= MAIN =================
for idx, row in enumerate(data):

    if idx % TOTAL_BATCHES != batch_index:
        continue

    i = idx + 2

    if api_calls >= DAILY_API_LIMIT:
        break

    url = str(row.get("Supplier URL", "")).lower()

    if "ebay." not in url:
        continue

    stock, cost_price = get_ebay_data(url, ebay_token)
    api_calls += 1

    if not cost_price:
        continue

    # ================= FIXED PRICING (FINAL) =================

    raw_price = row.get("Selling Price (£)")

    try:
        clean = re.sub(r"[^\d.]", "", str(raw_price))
        current_price = float(clean) if clean else 0
    except:
        current_price = 0

    min_price = (cost_price * (1 + MIN_PROFIT)) / (1 - PLATFORM_FEE)

    if current_price > 0 and current_price >= min_price:
        final_price = current_price
    else:
        final_price = min_price

    final_price = round(final_price) - 0.01

    # ================= UPDATE =================
    status = "ACTIVE" if stock > 0 else "INACTIVE"
    now_pk = datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")

    sheet.update(
        range_name=f"H{i}:O{i}",
        values=[[
            float(cost_price),
            "", "", "",
            int(stock),
            float(final_price),
            status,
            now_pk
        ]]
    )

    sheet.update(range_name=f"T{i}", values=[[now_pk]])

    update_onbuy_product(
        sku=row.get("SKU"),
        price=final_price,
        quantity=stock
    )

    # ================= XML =================
    if status == "ACTIVE":
        product = ET.SubElement(root, "product")
        ET.SubElement(product, "sku").text = str(row.get("SKU", ""))
        ET.SubElement(product, "price").text = str(final_price)
        ET.SubElement(product, "quantity").text = str(stock)

    print(f"{i} | £{cost_price} → £{final_price} | Stock: {stock}")

    time.sleep(0.5)

ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print(f"DONE | API CALLS USED: {api_calls}")
