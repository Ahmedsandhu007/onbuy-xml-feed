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

# ================= CONFIG =================
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

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

# ================= HELPERS =================
def is_changed(old, new):
    try:
        return str(old).strip() != str(new).strip()
    except:
        return True

def clean_category(cat):
    if not cat:
        return "General"
    return cat.split(">")[-1].strip()

# 🔥 EAN FIX
def get_ean(sku):
    sku = str(sku).strip()

    if sku.isdigit() and len(sku) >= 12:
        return sku[:13]

    return "950" + str(abs(hash(sku)))[:10]

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

# ================= EBAY FETCH =================
def get_ebay_data(url, token):
    try:
        match = re.search(r"/itm/(\d+)", url)
        if not match:
            return None, None, None

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
        title = data.get("title", "")
        image = data.get("image", {}).get("imageUrl", "")
        category = data.get("categoryPath", "")

        brand = None
        if data.get("brand"):
            brand = data.get("brand")

        if not brand:
            for aspect in data.get("localizedAspects", []):
                if aspect.get("name", "").lower() == "brand":
                    brand = aspect.get("value")
                    break

        if not brand:
            brand = "Unbranded"

        stock = 0
        avail = data.get("estimatedAvailabilities", [])
        if avail:
            if avail[0].get("estimatedAvailabilityStatus") == "IN_STOCK":
                stock = avail[0].get("estimatedAvailableQuantity", 5)

        return stock, price, {
            "title": title,
            "image": image,
            "category": category,
            "brand": brand
        }

    except Exception as e:
        print("eBay error:", e)
        return None, None, None

# ================= INIT =================
root = ET.Element("products")
token = get_ebay_token()

api_calls = 0
current_hour = datetime.now(PK_TZ).hour
batch_index = current_hour % TOTAL_BATCHES

# ================= MAIN (BATCHED SHEET UPDATE) =================
for idx, row in enumerate(data):

    if idx % TOTAL_BATCHES != batch_index:
        continue

    if api_calls >= DAILY_API_LIMIT:
        break

    i = idx + 2

    url = str(row.get("Supplier URL", "")).lower()
    if "ebay." not in url:
        continue

    stock, cost_price, extra = get_ebay_data(url, token)
    api_calls += 1

    if not cost_price or not extra:
        continue

    title = extra["title"]
    image = extra["image"]
    category = clean_category(extra["category"])
    brand = extra["brand"]

    description = re.sub(r"<.*?>", "", str(row.get("Description") or "")).strip()

    min_price = (cost_price * (1 + MIN_PROFIT)) / (1 - PLATFORM_FEE)
    final_price = round(min_price) - 0.01

    changed = (
        is_changed(row.get("Title"), title) or
        is_changed(row.get("Brand"), brand) or
        is_changed(row.get("Category"), category) or
        is_changed(row.get("Image URL"), image) or
        is_changed(row.get("Selling Price (£)"), final_price) or
        is_changed(row.get("Stock"), stock)
    )

    if changed:
        sheet.update(f"B{i}:E{i}", [[title, description, brand, category]])
        sheet.update(f"H{i}:O{i}", [[
            float(cost_price),
            "", "", "",
            int(stock),
            float(final_price),
            "ACTIVE" if stock > 0 else "INACTIVE",
            datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")
        ]])
        print(f"{i} updated")
    else:
        print(f"{i} skipped")

    time.sleep(0.4)

# ================= XML (ONBUY CREATE FEED) =================
for row in data:
    try:
        sku = str(row.get("SKU")).strip()
        title = str(row.get("Title")).strip()[:150]
        desc = str(row.get("Description")).strip()
        image = str(row.get("Image URL")).strip()
        brand = str(row.get("Brand") or "Unbranded").strip()
        category = str(row.get("Category")).strip()
        price = float(re.sub(r"[^\d.]", "", str(row.get("Selling Price (£)", 0)) or "0"))
        stock = int(row.get("Stock") or 0)

        condition = str(row.get("Condition") or "new").lower()
        ean = get_ean(sku)

        # 🔥 STRICT FILTER
        if not all([sku, title, desc, image, brand, category, ean]):
            continue

        if any(bad in image.lower() for bad in ["imgur", "alicdn", "fruugo"]):
            continue

        if price <= 0:
            continue

        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = sku
        ET.SubElement(product, "name").text = title
        ET.SubElement(product, "description").text = desc
        ET.SubElement(product, "image").text = image
        ET.SubElement(product, "brand").text = brand
        ET.SubElement(product, "category").text = category
        ET.SubElement(product, "ean").text = ean
        ET.SubElement(product, "condition").text = condition
        ET.SubElement(product, "price").text = str(price)
        ET.SubElement(product, "quantity").text = str(stock)

    except Exception as e:
        print("Skipped row:", e)
        continue

# ================= SAVE =================
ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print(f"\n✅ DONE | API CALLS USED: {api_calls}")
