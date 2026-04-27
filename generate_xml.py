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

# ================= CATEGORY =================
def map_category(title):
    t = title.lower()
    if "fabric" in t or "cotton" in t:
        return "Clothing"
    elif "shoe" in t:
        return "Footwear"
    elif "watch" in t:
        return "Watches"
    return "General"

# ================= DESCRIPTION =================
def format_description(title):
    return f"""
{title}

Product Overview:
Premium quality product designed for performance durability and style.

Key Features:
• High quality construction
• Reliable performance
• Long lasting usage
• Excellent value

Condition:
Brand New

Shipping:
Fast and secure delivery.
""".strip()

# ================= EBAY =================
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

        additional = [
            img["imageUrl"]
            for img in data.get("additionalImages", [])
            if img.get("imageUrl")
        ]

        stock = 5

        return stock, price, {
            "title": title,
            "image": image,
            "additional": additional
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

    stock, cost, extra = get_ebay_data(url, token)

    if not cost:
        continue

    # ================= AUTO FILL =================
    title = extra.get("title", "")
    image = extra.get("image", "")
    additional = ", ".join(extra.get("additional", []))
    category = map_category(title)
    description = format_description(title)

    # ================= PRICE =================
    min_price = (cost * (1 + MIN_PROFIT)) / (1 - PLATFORM_FEE)
    final_price = round(min_price) - 0.01

    # ================= UPDATE SHEET =================
    sheet.update(
        range_name=f"B{i}:E{i}",
        values=[[title, description, "", category]]
    )

    sheet.update(
        range_name=f"Q{i}:R{i}",
        values=[[image, additional]]
    )

    sheet.update(
        range_name=f"H{i}:O{i}",
        values=[[
            float(cost),
            "", "", "",
            int(stock),
            float(final_price),
            "ACTIVE",
            datetime.now(PK_TZ).strftime("%Y-%m-%d %H:%M:%S")
        ]]
    )

    print(f"{i} → FILLED")

    # ================= XML =================
    product = ET.SubElement(root, "product")

    ET.SubElement(product, "sku").text = str(row.get("SKU"))
    ET.SubElement(product, "name").text = title
    ET.SubElement(product, "description").text = description
    ET.SubElement(product, "price").text = str(final_price)
    ET.SubElement(product, "quantity").text = str(stock)
    ET.SubElement(product, "image").text = image

    for img in extra.get("additional", []):
        ET.SubElement(product, "additional_image").text = img

    time.sleep(0.3)

# ================= SAVE =================
ET.ElementTree(root).write("feed.xml", encoding="utf-8", xml_declaration=True)

print("\n✅ TEST RUN COMPLETE")
