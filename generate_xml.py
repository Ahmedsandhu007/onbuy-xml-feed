import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from datetime import datetime
import time

# --- AUTH ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

import json
import os

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
)

client = gspread.authorize(creds)
sheet = client.open("OnBuy_Feed_Master").sheet1

data = sheet.get_all_records()

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-GB,en;q=0.9"
}

# --- AMAZON ---
def get_amazon_data(url):
    try:
        res = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")

        price = None

        selectors = [
            ".a-price .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice"
        ]

        for sel in selectors:
            tag = soup.select_one(sel)
            if tag:
                text = tag.text.replace("£", "").replace(",", "").strip()
                try:
                    price = float(text)
                    break
                except:
                    continue

        # --- STOCK LOGIC ---
        stock = 0
        availability = soup.select_one("#availability")

        if availability:
            text = availability.text.lower()

            if "in stock" in text or "available" in text:
                stock = 10
            elif "only" in text:
                stock = 5
            else:
                stock = 0

        return stock, price

    except Exception as e:
        print("Amazon error:", e)
        return None, None


# --- EBAY ---
def get_ebay_data(url):
    try:
        res = requests.get(url, headers=headers, timeout=15)
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

    supplier = row.get("Supplier")
    url = row.get("Supplier URL")

    stock, price = None, None

    # --- FETCH DATA ---
    if supplier == "Amazon":
        stock, price = get_amazon_data(url)

    elif supplier == "eBay":
        stock, price = get_ebay_data(url)

    # --- DEBUG ---
    print(f"{supplier} | Stock: {stock} | Price: {price}")

    # --- UPDATE LOCAL ROW (CRITICAL FIX) ---
    if stock is not None:
        row["Stock"] = stock

    if price is not None:
        cost_price = round(price, 2)
        selling_price = round(price * 1.35, 2)

        row["Cost Price (£)"] = cost_price
        row["Selling Price (£)"] = selling_price

    # --- STATUS ---
    status = "ACTIVE"
    if stock == 0:
        status = "INACTIVE"
        row["Status"] = status

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- SHEET UPDATE (BATCH) ---
    try:
        sheet.update(f"H{i}:O{i}", [[
            row.get("Cost Price (£)", ""),
            "", "", "",
            row.get("Stock", ""),
            row.get("Selling Price (£)", ""),
            status,
            timestamp
        ]])
    except Exception as e:
        print("Sheet update error:", e)

    # --- XML GENERATION ---
    if row.get("Status") == "ACTIVE":

        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = str(row.get("SKU", ""))
        ET.SubElement(product, "title").text = row.get("Title", "")
        ET.SubElement(product, "description").text = row.get("Description", "")
        ET.SubElement(product, "price").text = str(row.get("Selling Price (£)", ""))
        ET.SubElement(product, "quantity").text = str(row.get("Stock", ""))
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
