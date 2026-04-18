import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import time
import json
import os
import re

# --- CONFIG ---
API_KEY = os.getenv("RAINFOREST_API_KEY")

# --- AUTH ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.environ.get("GOOGLE_CREDENTIALS", "{}"))

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    creds_dict,
    scope
)

client = gspread.authorize(creds)
sheet = client.open("OnBuy_Feed_Master").sheet1

data = sheet.get_all_records()

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-GB,en;q=0.9"
}

# --- ASIN EXTRACT ---
def extract_asin(url):
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})"
    ]
    for p in patterns:
        match = re.search(p, url)
        if match:
            return match.group(1)
    return None


# --- AMAZON (API) ---
def get_amazon_data(url):
    try:
        asin = extract_asin(url)

        if not asin:
            print("❌ ASIN not found:", url)
            return None, None

        params = {
            "api_key": API_KEY,
            "type": "product",
            "amazon_domain": "amazon.co.uk",
            "asin": asin
        }

        res = requests.get("https://api.rainforestapi.com/request", params=params, timeout=20)

        if res.status_code != 200:
            print("❌ API error:", res.status_code)
            return None, None

        data = res.json()
        product = data.get("product", {})

        if not product:
            return None, None

        # --- PRICE ---
        price = None

        buybox = product.get("buybox_winner")
        if buybox and buybox.get("price"):
            price = buybox["price"]["value"]
        elif product.get("price"):
            price = product["price"].get("value")

        # --- STOCK ---
        availability = product.get("availability", "").lower()

        if "in stock" in availability:
            stock = 10
        elif "out of stock" in availability:
            stock = 0
        else:
            stock = 5

        print(f"Amazon API | {asin} | Stock: {stock} | Price: {price}")

        return stock, price

    except Exception as e:
        print("Amazon API error:", e)
        return None, None


# --- EBAY ---
def get_ebay_data(url):
    try:
        res = requests.get(url, headers=headers, timeout=15)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(res.text, "html.parser")

        price = None
        price_tag = soup.select_one(".x-price-primary span")

        if price_tag:
            try:
                price = float(price_tag.text.replace("£", "").replace(",", "").strip())
            except:
                pass

        stock = 1
        if "out of stock" in soup.text.lower():
            stock = 0

        return stock, price

    except Exception as e:
        print("eBay error:", e)
        return None, None


# --- XML ROOT ---
root = ET.Element("products")

# --- MAIN LOOP ---
for i, row in enumerate(data, start=2):

    # --- SAFE SUPPLIER DETECTION ---
    raw_supplier = str(row.get("Supplier") or row.get("Source") or "")
    supplier = raw_supplier.strip().lower()

    url = row.get("Supplier URL")

    stock, price = None, None

    # --- DEBUG ---
    print(f"Supplier raw: {raw_supplier} → normalized: {supplier}")

    # --- FETCH ---
    if "amazon" in supplier:
        stock, price = get_amazon_data(url)

    elif "ebay" in supplier:
        stock, price = get_ebay_data(url)

    # --- DEBUG RESULT ---
    print(f"Result → Stock: {stock}, Price: {price}")

    # --- FALLBACK ---
    if price is None:
        price = row.get("Cost Price (£)", 0)

    if stock is None:
        stock = row.get("Stock", 0)

    # --- CALCULATE ---
    cost_price = round(price, 2)
    selling_price = round(price * 1.35, 2)

    status = "ACTIVE" if stock > 0 else "INACTIVE"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- UPDATE SHEET ---
    try:
        sheet.update(range_name=f"H{i}:O{i}", values=[[
            cost_price,
            "", "", "",
            stock,
            selling_price,
            status,
            timestamp
        ]])
    except Exception as e:
        print("Sheet error:", e)

    # --- XML ---
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
    time.sleep(2)
# --- SAVE XML ---
tree = ET.ElementTree(root)
tree.write("feed.xml", encoding="utf-8", xml_declaration=True)

print("XML Updated Successfully!")
