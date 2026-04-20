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

creds_raw = os.getenv("GOOGLE_CREDENTIALS")
if not creds_raw:
    raise Exception("GOOGLE_CREDENTIALS missing")

creds_dict = json.loads(creds_raw)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

sheet = client.open("OnBuy_Feed_Master").sheet1
data = sheet.get_all_records()

# ================= EBAY TOKEN (FIXED) =================
def get_ebay_token():
    import requests
    import base64
    import os

    # 🔥 STRIP to remove hidden spaces/newlines
    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()

    # 🚨 HARD CHECK
    if not client_id or not client_secret:
        raise Exception("Missing eBay credentials")

    print("ID LENGTH:", len(client_id))
    print("SECRET LENGTH:", len(client_secret))

    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }

    res = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers=headers,
        data=data
    )

    print("TOKEN RESPONSE:", res.text)  # 🔍 DEBUG

    token = res.json().get("access_token")

    if not token:
        raise Exception(f"Failed to get eBay token: {res.text}")

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

        res = requests.get("https://api.rainforestapi.com/request", params=params, timeout=20)
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


# ================= EBAY (OFFICIAL API) =================
def extract_ebay_id(url):
    match = re.search(r"/itm/(\d+)", url)
    return match.group(1) if match else None


def get_ebay_data(url, token):
    try:
        import requests
        import re

        # Extract item ID
        match = re.search(r"/itm/(\d+)", url)
        if not match:
            return None, None

        item_id = match.group(1)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"
        }

        endpoint = f"https://api.ebay.com/buy/browse/v1/item/v1|{item_id}|0"

        res = requests.get(endpoint, headers=headers)
        data = res.json()

        print("eBay RAW:", data)

        # ✅ PRICE FIX
        price = None
        if "price" in data:
            price = float(data["price"]["value"])

        # ✅ STOCK FIX (REAL VALUE)
        stock = 0
        if "estimatedAvailabilities" in data:
            avail = data["estimatedAvailabilities"][0]

            if avail.get("estimatedAvailabilityStatus") == "IN_STOCK":
                stock = avail.get("estimatedAvailableQuantity", 5)

        print(f"eBay(API OFFICIAL) → Stock: {stock}, Price: {price}")
        return stock, price

    except Exception as e:
        print("eBay API error:", e)
        return None, None


# ================= XML ROOT =================
root = ET.Element("products")

# ================= GET TOKEN ONCE =================
ebay_token = get_ebay_token()

# ================= MAIN =================
for i, row in enumerate(data, start=2):

    url = str(row.get("Supplier URL", "")).lower()

    stock, price = None, None

    if "amazon." in url:
        stock, price = get_amazon_data(url)

    elif "ebay." in url:
        stock, price = get_ebay_data(url, ebay_token)

    print(f"Result → Stock: {stock}, Price: {price}")

    # FALLBACKS
    if price is None:
        price = row.get("Cost Price (£)", 0)

    if stock is None:
        stock = row.get("Stock", 0)

    # PRICING
    profit = random.uniform(MIN_PROFIT, MAX_PROFIT)

    if (FEE + profit) >= 1:
        profit = MIN_PROFIT

    selling_price = round(price / (1 - FEE - profit), 2)

    status = "ACTIVE" if stock > 0 else "INACTIVE"

    # SHEET UPDATE
    sheet.update(range_name=f"H{i}:O{i}", values=[[
        price,
        "", "", "",
        stock,
        selling_price,
        status,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]])

    # XML
    if status == "ACTIVE":

        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = str(row.get("SKU", ""))
        ET.SubElement(product, "title").text = row.get("Title", "")
        ET.SubElement(product, "description").text = row.get("Description", "")
        ET.SubElement(product, "price").text = str(selling_price)
        ET.SubElement(product, "quantity").text = str(stock)
        ET.SubElement(product, "brand").text = row.get("Brand", "")
        ET.SubElement(product, "image_url").text = row.get("Image URL", "")
        ET.SubElement(product, "additional_images").text = row.get("Additional Images", "")
        ET.SubElement(product, "category").text = row.get("Category", "")
        ET.SubElement(product, "condition").text = row.get("Condition", "")

    print(f"Processed row {i}")
    time.sleep(1)

# ================= SAVE XML =================
tree = ET.ElementTree(root)
tree.write("feed.xml", encoding="utf-8", xml_declaration=True)

print("XML GENERATED SUCCESSFULLY")
