import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from datetime import datetime
import time
import json
import os
import re

# ================= CONFIG =================
API_KEY = os.getenv("RAINFOREST_API_KEY")
ONBUY_BASE64 = os.getenv("ONBUY_BASE64")

# ================= AUTH =================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

sheet = client.open("OnBuy_Feed_Master").sheet1
data = sheet.get_all_records()

headers = {
    "User-Agent": "Mozilla/5.0"
}

# ================= ONBUY TOKEN =================
def get_onbuy_token():
    try:
        url = "https://api.onbuy.com/gb/v2/oauth/token"

        headers = {
            "Authorization": f"Basic {ONBUY_BASE64}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {"grant_type": "client_credentials"}

        res = requests.post(url, headers=headers, data=data)
        token = res.json().get("access_token")

        print("OnBuy Token:", "OK" if token else "FAILED")

        return token

    except Exception as e:
        print("OnBuy token error:", e)
        return None


# ================= ONBUY UPDATE =================
def update_onbuy_product(sku, price, quantity, token):
    try:
        url = "https://api.onbuy.com/gb/v2/products/update"

        headers = {
            "Authorization": f"Bearer {token}",
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

        print(f"OnBuy → {sku} → {res.status_code} → {res.text}")

    except Exception as e:
        print("OnBuy update error:", e)


# ================= AMAZON =================
def extract_asin(url):
    match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{10})", url)
    return match.group(1).upper() if match else None


def get_amazon_data(url):
    try:
        asin = extract_asin(url)
        if not asin:
            return None, None

        params = {
            "api_key": API_KEY,
            "type": "product",
            "amazon_domain": "amazon.co.uk",
            "asin": asin
        }

        res = requests.get("https://api.rainforestapi.com/request", params=params)
        data = res.json().get("product", {})

        price = None
        if data.get("buybox_winner"):
            price = data["buybox_winner"]["price"]["value"]
        elif data.get("price"):
            price = data["price"]["value"]

        availability = data.get("availability", "").lower()

        if "in stock" in availability:
            stock = 10
        elif "out of stock" in availability:
            stock = 0
        else:
            stock = 5

        print(f"Amazon → Stock: {stock}, Price: {price}")
        return stock, price

    except Exception as e:
        print("Amazon error:", e)
        return None, None


# ================= EBAY =================
def get_ebay_data(url):
    try:
        from bs4 import BeautifulSoup

        res = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.text.lower()

        price = None

        for script in soup.find_all("script"):
            if script.string and "price" in script.string:
                m = re.findall(r'"price":"?([0-9]+\.[0-9]+)"?', script.string)
                if m:
                    price = float(m[0])
                    break

        if price is None:
            matches = re.findall(r"£\s?([0-9]+(?:\.[0-9]{1,2})?)", text)
            if matches:
                price = float(matches[0])

        stock = None

        qty_match = re.search(r"(\d+)\s+available", text)
        if qty_match:
            stock = int(qty_match.group(1))

        if stock is None:
            stock = 1

        print(f"eBay → Stock: {stock}, Price: {price}")
        return stock, price

    except Exception as e:
        print("eBay error:", e)
        return None, None


# ================= MAIN =================
onbuy_token = get_onbuy_token()

for i, row in enumerate(data[:1], start=2):  # TEST WITH 1 PRODUCT

    url = str(row.get("Supplier URL", "")).lower()

    stock, price = None, None

    if "amazon." in url:
        stock, price = get_amazon_data(url)

    elif "ebay." in url:
        stock, price = get_ebay_data(url)

    print(f"Result → Stock: {stock}, Price: {price}")

    if price is None:
        price = row.get("Cost Price (£)", 0)

    if stock is None:
        stock = row.get("Stock", 0)

    selling_price = round(price * 1.35, 2)

    status = "ACTIVE" if stock > 0 else "INACTIVE"

    # Update Sheet
    sheet.update(range_name=f"H{i}:O{i}", values=[[
        price,
        "", "", "",
        stock,
        selling_price,
        status,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]])

    # 🚀 ONBUY UPDATE
    if onbuy_token:
        update_onbuy_product(
            sku=row.get("SKU"),
            price=selling_price,
            quantity=stock,
            token=onbuy_token
        )

    print(f"Processed row {i}")
    time.sleep(1)

print("DONE")
