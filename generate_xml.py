import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from datetime import datetime
import time
import json
import os
import re
import random
import xml.etree.ElementTree as ET
import base64

# ================= CONFIG =================
RAINFOREST_API_KEY = os.getenv("RAINFOREST_API_KEY")
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

FEE = 0.18
MIN_PROFIT = 0.21
MAX_PROFIT = 0.25

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

# ================= EBAY TOKEN =================
def get_ebay_token():
    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        raise Exception("Missing eBay credentials")

    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }

    res = requests.post("https://api.ebay.com/identity/v1/oauth2/token",
                        headers=headers, data=data)

    token = res.json().get("access_token")

    if not token:
        raise Exception(res.text)

    return token

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
            "api_key": RAINFOREST_API_KEY,
            "type": "product",
            "amazon_domain": "amazon.co.uk",
            "asin": asin
        }

        res = requests.get("https://api.rainforestapi.com/request", params=params)
        data = res.json().get("product", {})

        price = (
            data.get("buybox_winner", {}).get("price", {}).get("value")
            or data.get("price", {}).get("value")
        )

        availability = data.get("availability", "").lower()

        stock = 10 if "in stock" in availability else 0 if "out" in availability else 5

        return stock, price

    except:
        return None, None

# ================= EBAY =================
def get_ebay_data(url, token):
    try:
        match = re.search(r"/itm/(\d+)", url)
        if not match:
            return None, None

        item_id = match.group(1)

        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"
        }

        res = requests.get(
            f"https://api.ebay.com/buy/browse/v1/item/v1|{item_id}|0",
            headers=headers
        )

        data = res.json()

        price = float(data.get("price", {}).get("value", 0))

        stock = 0
        avail = data.get("estimatedAvailabilities", [])
        if avail:
            if avail[0].get("estimatedAvailabilityStatus") == "IN_STOCK":
                stock = avail[0].get("estimatedAvailableQuantity", 5)

        return stock, price

    except:
        return None, None


# ================= XML =================
root = ET.Element("products")

# ================= TOKEN =================
ebay_token = get_ebay_token()

# ================= MAIN =================
for i, row in enumerate(data, start=2):

    url = str(row.get("Supplier URL", "")).lower()

    stock, price = None, None

    if "amazon." in url:
        stock, price = get_amazon_data(url)
    elif "ebay." in url:
        stock, price = get_ebay_data(url, ebay_token)

    # fallback
    price = price or row.get("Cost Price (£)", 0)
    stock = stock if stock is not None else row.get("Stock", 0)

    # ===== PRICING (MARKUP CORRECT) =====
       profit = random.uniform(MIN_PROFIT, MAX_PROFIT)

       total_markup = FEE + profit

       selling_price = round(price * (1 + total_markup), 2)

# ===== STATUS (MISSING FIX) =====
       status = "ACTIVE" if stock > 0 else "INACTIVE"

# ===== SHEET UPDATE (FIXED) =====
       sheet.update(f"H{i}:O{i}", [[
       float(price),
       "", "", "",
       int(stock),
       float(selling_price),
       status,
       datetime.now().strftime("%Y-%m-%d %H:%M:%S")
]])
    # ===== XML =====
    if status == "ACTIVE":
        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = str(row.get("SKU", ""))
        ET.SubElement(product, "title").text = row.get("Title", "")
        ET.SubElement(product, "description").text = row.get("Description", "")
        ET.SubElement(product, "price").text = str(selling_price)
        ET.SubElement(product, "quantity").text = str(stock)
        ET.SubElement(product, "brand").text = row.get("Brand", "")
        ET.SubElement(product, "image_url").text = row.get("Image URL", "")
        ET.SubElement(product, "category").text = row.get("Category", "")

    # ===== CLEAN OUTPUT =====
    print(f"{i} | £{price} → £{selling_price} | Stock: {stock}")

    time.sleep(1)

# ================= SAVE XML =================
ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print("DONE")
