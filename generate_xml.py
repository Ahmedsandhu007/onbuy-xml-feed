import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from datetime import datetime, timedelta
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

FEE = 0.18
MIN_PROFIT = 0.21
MAX_PROFIT = 0.25
UNDERCUT_FACTOR = 0.98

TOTAL_BATCHES = 5
SKIP_HOURS = 0
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
        f"{EBAY_CLIENT_ID.strip()}:{EBAY_CLIENT_SECRET.strip()}".encode()
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

# ================= ALIEXPRESS (FIXED SCRAPER) =================
def get_aliexpress_data(url):
    try:
        print("Ali URL:", url)

        matches = re.findall(r'%21([\d\.]+)%21', url)

        # 🔥 filter only realistic prices
        prices = []
        for m in matches:
            try:
                val = float(m)

                # ✅ keep only valid price range
                if 0.5 < val < 1000:
                    prices.append(val)

            except:
                continue

        if prices:
            price = prices[0] if len(prices) == 1 else prices[1]
            stock = 5

            print(f"AliExpress (FINAL) → Stock: {stock}, Price: {price}")
            return stock, price

        print("AliExpress → No valid price found")
        return None, None

    except Exception as e:
        print("AliExpress error:", e)
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

    #if idx % TOTAL_BATCHES != batch_index:
        #continue

    i = idx + 2

    if api_calls >= DAILY_API_LIMIT:
        print("API LIMIT REACHED — STOPPING")
        break

    last_checked_str = row.get("Last Checked Time", "")

    if last_checked_str:
        try:
            last_checked = datetime.strptime(
                last_checked_str, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=PK_TZ)

            if datetime.now(PK_TZ) - last_checked < timedelta(hours=SKIP_HOURS):
                print(f"{i} | SKIPPED")
                continue
        except:
            pass

    url = str(row.get("Supplier URL", "")).lower()

    stock, price = None, None

    # ===== SOURCE =====
    if "ebay." in url:
        stock, price = get_ebay_data(url, ebay_token)

    elif "aliexpress" in url:
        stock, price = get_aliexpress_data(url)

    api_calls += 1

    # ===== FALLBACK =====
    if not price or price == 0:
        price = row.get("Cost Price (£)", 0)

    if stock is None:
        stock = row.get("Stock", 0)

    # ===== PRICING =====
    profit = random.uniform(MIN_PROFIT, MAX_PROFIT)

    min_price = price * (1 + FEE + profit)
    competitive_price = price * UNDERCUT_FACTOR

    selling_price = round(max(min_price, competitive_price), 2)
    selling_price = round(selling_price) - 0.01

    # ===== CHANGE CHECK =====
    old_price = float(row.get("Selling Price", 0))
    old_stock = int(row.get("Stock", 0))

    if abs(old_price - selling_price) < 0.5 and old_stock == stock:
        print(f"{i} | NO CHANGE")
        continue

    status = "ACTIVE" if stock > 0 else "INACTIVE"
    now_pk = datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # ===== SHEET UPDATE =====
    sheet.update(
        range_name=f"H{i}:O{i}",
        values=[[
            float(price),
            "", "", "",
            int(stock),
            float(selling_price),
            status,
            now_pk
        ]]
    )

    sheet.update(range_name=f"T{i}", values=[[now_pk]])

    # ===== ONBUY =====
    update_onbuy_product(
        sku=row.get("SKU"),
        price=selling_price,
        quantity=stock
    )

    # ===== XML =====
    if status == "ACTIVE":
        product = ET.SubElement(root, "product")
        ET.SubElement(product, "sku").text = str(row.get("SKU", ""))
        ET.SubElement(product, "title").text = row.get("Title", "")
        ET.SubElement(product, "price").text = str(selling_price)
        ET.SubElement(product, "quantity").text = str(stock)

    print(f"{i} | £{price} → £{selling_price} | Stock: {stock}")

    time.sleep(0.5)

# ================= SAVE XML =================
ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print(f"DONE | API CALLS USED: {api_calls}")
