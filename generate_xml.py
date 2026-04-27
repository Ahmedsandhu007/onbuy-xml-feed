import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import json
import os
import re
import xml.etree.ElementTree as ET
import base64
from bs4 import BeautifulSoup

# ================= CONFIG =================
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

ONBUY_CONSUMER_KEY = os.getenv("ONBUY_CONSUMER_KEY")
ONBUY_SECRET_KEY = os.getenv("ONBUY_SECRET_KEY")

MIN_PROFIT = 0.15
PLATFORM_FEE = 0.18

TOTAL_BATCHES = 5
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

    data = res.json()
    if "access_token" not in data:
        print("❌ TOKEN ERROR:", data)
        return None

    return data["access_token"]

# ================= EBAY =================

def extract_item_id(url):
    match = re.search(r"/itm/(?:.*?/)?(\d{9,12})", str(url))
    return match.group(1) if match else None


def fetch_item(item_id, token):
    res = requests.get(
        f"https://api.ebay.com/buy/browse/v1/item/{item_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    if res.status_code != 200:
        return None
    return res.json()


def search_fallback(query, token):
    res = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "limit": 1}
    )
    data = res.json()
    items = data.get("itemSummaries", [])
    return items[0].get("itemId") if items else None


def scrape_ebay(url):
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")

        price = None
        selectors = ["#prcIsum", "#mm-saleDscPrc", ".x-price-primary span"]

        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                price = float(re.sub(r"[^\d.]", "", el.get_text()))
                break

        stock = 5 if price else 0
        return stock, price

    except:
        return None, None


def get_ebay_data(url, token):
    try:
        item_id = extract_item_id(url)

        data = None

        if item_id:
            print(f"🔎 ID: {item_id}")
            data = fetch_item(item_id, token)

            if not data:
                print("⚠️ API failed → fallback search")
                item_id = search_fallback(item_id, token)
                if item_id:
                    data = fetch_item(item_id, token)

        if not data:
            print("⚠️ API failed → scraping")
            return scrape_ebay(url)

        price = float(data.get("price", {}).get("value", 0))

        stock = 0
        avail = data.get("estimatedAvailabilities", [])
        if avail and avail[0].get("estimatedAvailabilityStatus") == "IN_STOCK":
            stock = avail[0].get("estimatedAvailableQuantity", 5)

        return stock, price

    except Exception as e:
        print("❌ eBay error:", e)
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
        print("❌ OnBuy error:", e)

# ================= INIT =================
root = ET.Element("products")
token = get_ebay_token()

if not token:
    exit()

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

    url = str(row.get("Supplier URL", "")).strip()

    if "ebay." not in url:
        continue

    stock, cost = get_ebay_data(url, token)
    api_calls += 1

    if not cost:
        continue

    # ================= PRICING =================
    min_price = (cost * (1 + MIN_PROFIT)) / (1 - PLATFORM_FEE)

    user_price = float(row.get("Selling Price", 0) or 0)

    if user_price > 0:
        net = user_price * (1 - PLATFORM_FEE)
        profit_ratio = (net - cost) / cost
        final_price = user_price if profit_ratio >= MIN_PROFIT else min_price
    else:
        final_price = min_price

    final_price = round(final_price) - 0.01

    # ================= STOCK =================
    if stock <= 0:
        final_stock = 0
        status = "INACTIVE"
    elif stock <= 2:
        final_stock = 1
        status = "ACTIVE"
    else:
        final_stock = min(stock, 10)
        status = "ACTIVE"

    # ================= ONBUY =================
    update_onbuy_product(
        sku=row.get("SKU"),
        price=final_price,
        quantity=final_stock
    )

    # ================= SHEET =================
    now = datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")

    sheet.update(
        range_name=f"H{i}:O{i}",
        values=[[
            float(cost),
            "", "", "",
            int(final_stock),
            float(final_price),
            status,
            now
        ]]
    )

    # ================= XML =================
    if status == "ACTIVE":
        product = ET.SubElement(root, "product")
        ET.SubElement(product, "sku").text = str(row.get("SKU", ""))
        ET.SubElement(product, "price").text = str(final_price)
        ET.SubElement(product, "quantity").text = str(final_stock)

    # ================= LOG =================
    net = final_price * (1 - PLATFORM_FEE)
    profit = net - cost

    print(f"{i} | £{cost} → £{final_price} | Profit £{round(profit,2)} | Stock {final_stock}")

    time.sleep(0.5)

# ================= SAVE =================
ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print(f"\nDONE | API CALLS USED: {api_calls}")
