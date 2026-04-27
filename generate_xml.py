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

# ================= DESCRIPTION =================
def format_description(title, brand=""):
    desc = f"""
{title}

Product Overview:
Premium quality product designed for durability performance and style.

Key Features:
• High quality construction
• Reliable performance
• Long lasting usage
• Excellent value for money
• Designed for everyday use

Why Choose This Product:
• Carefully sourced product
• Trusted quality assurance
• Suitable for multiple use cases

Condition:
Brand New

Shipping:
Fast and secure delivery.
"""
    if brand:
        desc += f"\nBrand: {brand}"
    return desc.strip()

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

        # ✅ FIXED STOCK LOGIC
        stock = 0
        avail = data.get("estimatedAvailabilities", [])

        if avail:
            status = avail[0].get("estimatedAvailabilityStatus")
            if status == "IN_STOCK":
                stock = avail[0].get("estimatedAvailableQuantity", 5)
            else:
                stock = 0

        print(f"eBay → {title} | Stock: {stock}")

        return stock, price, {
            "title": title,
            "image": image,
            "additional": additional_images,
            "category": category
        }

    except Exception as e:
        print("eBay error:", e)
        return None, None, None

# ================= MAIN =================
root = ET.Element("products")
token = get_ebay_token()

for idx, row in enumerate(data):
    i = idx + 2

    url = str(row.get("Supplier URL", "")).lower()
    if "ebay." not in url:
        continue

    stock, cost_price, extra = get_ebay_data(url, token)

    if not cost_price or not extra:
        continue

    # ================= AUTO DATA =================
    title = extra.get("title", "")
    image = extra.get("image", "")
    additional = ", ".join(extra.get("additional", []))
    category = clean_category(extra.get("category"))

    # ✅ FIXED BRAND LOGIC
    brand = row.get("Brand")

    if not brand:
        words = title.split()
        brand = words[0] if words else "Unbranded"

    brand = brand.strip() if brand else "Unbranded"

    description = format_description(title, brand)

    # ================= PRICE =================
    min_price = (cost_price * (1 + MIN_PROFIT)) / (1 - PLATFORM_FEE)
    final_price = round(min_price) - 0.01

    # ================= UPDATE SHEET =================
    sheet.update(
        range_name=f"B{i}:E{i}",
        values=[[title, description, brand, category]]
    )

    sheet.update(
        range_name=f"Q{i}:R{i}",
        values=[[image, additional]]
    )

    sheet.update(
        range_name=f"H{i}:O{i}",
        values=[[
            float(cost_price),
            "", "", "",
            int(stock),
            float(final_price),
            "ACTIVE" if stock > 0 else "INACTIVE",
            datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")
        ]]
    )

    print(f"{i} → FILLED")

    # ================= XML =================
    product = ET.SubElement(root, "product")

    ET.SubElement(product, "sku").text = str(row.get("SKU"))
    ET.SubElement(product, "name").text = title
    ET.SubElement(product, "description").text = description
    ET.SubElement(product, "brand").text = brand
    ET.SubElement(product, "category").text = category
    ET.SubElement(product, "price").text = str(final_price)
    ET.SubElement(product, "quantity").text = str(stock)
    ET.SubElement(product, "image").text = image

    for img in extra.get("additional", []):
        ET.SubElement(product, "additional_image").text = img

    ET.SubElement(product, "condition").text = "new"

    time.sleep(0.3)

# ================= SAVE =================
ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print("\n✅ TEST RUN COMPLETE — ALL ROWS PROCESSED")
