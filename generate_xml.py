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

# ================= CATEGORY =================
def clean_category(cat):
    if not cat:
        return "General"
    return cat.split(">")[-1].strip()

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

        additional_images = [
            img["imageUrl"]
            for img in data.get("additionalImages", [])
            if img.get("imageUrl")
        ]

        category = data.get("categoryPath", "")

        # BRAND
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

        # STOCK
        stock = 0
        avail = data.get("estimatedAvailabilities", [])

        if avail:
            status = avail[0].get("estimatedAvailabilityStatus")
            if status == "IN_STOCK":
                stock = avail[0].get("estimatedAvailableQuantity", 5)
            else:
                stock = 0

        return stock, price, {
            "title": title,
            "image": image,
            "additional": additional_images,
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

# 🔥 BATCH CONTROL
current_hour = datetime.now(PK_TZ).hour
batch_index = current_hour % TOTAL_BATCHES

# ================= MAIN (BATCHED) =================
for idx, row in enumerate(data):

    # batching
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
    additional = ", ".join(extra["additional"])
    category = clean_category(extra["category"])
    brand = extra["brand"]

    # description from sheet ONLY
    raw_desc = row.get("Description")
    description = re.sub(r"<.*?>", "", str(raw_desc or "")).strip()

    min_price = (cost_price * (1 + MIN_PROFIT)) / (1 - PLATFORM_FEE)
    final_price = round(min_price) - 0.01

    sheet.update(f"B{i}:E{i}", [[title, description, brand, category]])
    sheet.update(f"Q{i}:R{i}", [[image, additional]])

    sheet.update(f"H{i}:O{i}", [[
        float(cost_price),
        "", "", "",
        int(stock),
        float(final_price),
        "ACTIVE" if stock > 0 else "INACTIVE",
        datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")
    ]])

    print(f"{i} updated")

    time.sleep(0.4)

# ================= FULL XML =================
print("\nBuilding full XML...\n")

for row in data:
    try:
        sku = str(row.get("SKU"))
        price = float(re.sub(r"[^\d.]", "", str(row.get("Selling Price (£)", 0)) or "0"))
        stock = int(row.get("Stock") or 0)

        if price <= 0:
            continue

        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = sku
        ET.SubElement(product, "name").text = str(row.get("Title"))
        ET.SubElement(product, "description").text = str(row.get("Description"))
        ET.SubElement(product, "brand").text = str(row.get("Brand") or "Unbranded")
        ET.SubElement(product, "category").text = str(row.get("Category"))
        ET.SubElement(product, "price").text = str(price)
        ET.SubElement(product, "quantity").text = str(stock)
        ET.SubElement(product, "image").text = str(row.get("Image URL"))

    except:
        continue

ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print(f"\nDONE | API CALLS USED: {api_calls}")
