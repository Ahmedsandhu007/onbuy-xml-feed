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

creds = ServiceAccountCredentials.from_json_keyfile_name(
    "credentials.json", scope
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
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")

        price = None
        selectors = [
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            ".a-price .a-offscreen"
        ]

        for sel in selectors:
            tag = soup.select_one(sel)
            if tag:
                price = float(tag.text.replace("£", "").replace(",", "").strip())
                break

        stock = 1
        if "Currently unavailable" in soup.text:
            stock = 0

        return stock, price

    except Exception as e:
        print("Amazon error:", e)
        return None, None


# --- EBAY ---
def get_ebay_data(url):
    try:
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")

        price_tag = soup.select_one(".x-price-primary span")
        price = None
        if price_tag:
            price = float(price_tag.text.replace("£", "").replace(",", "").strip())

        stock = 1
        if "Out of stock" in soup.text:
            stock = 0

        return stock, price

    except Exception as e:
        print("eBay error:", e)
        return None, None


# --- XML ROOT ---
root = ET.Element("products")

# --- MAIN LOOP ---
for i, row in enumerate(data, start=2):

    supplier = row["Supplier"]
    url = row["Supplier URL"]

    stock, price = None, None

    if supplier == "Amazon":
        stock, price = get_amazon_data(url)

    elif supplier == "eBay":
        stock, price = get_ebay_data(url)

    # --- UPDATE SHEET ---
    if stock is not None:
        sheet.update_cell(i, 13, stock)

    if price:
        sheet.update_cell(i, 8, price)

        # Auto pricing (35% margin)
        new_price = round(price * 1.35, 2)
        sheet.update_cell(i, 12, new_price)

    # Disable if out of stock
    if stock == 0:
        sheet.update_cell(i, 14, "INACTIVE")

    # Timestamp
    sheet.update_cell(i, 15, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # --- ADD TO XML ---
    if row["Status"] == "ACTIVE":

        product = ET.SubElement(root, "product")

        ET.SubElement(product, "sku").text = str(row["SKU"])
        ET.SubElement(product, "title").text = row["Title"]
        ET.SubElement(product, "description").text = row["Description"]
        ET.SubElement(product, "price").text = str(row["Selling Price (£)"])
        ET.SubElement(product, "quantity").text = str(row["Stock"])
        ET.SubElement(product, "brand").text = row["Brand"]
        ET.SubElement(product, "image_url").text = row["Image URL"]
        ET.SubElement(product, "additional_images").text = row["Additional Images"]
        ET.SubElement(product, "category").text = row["Category"]
        ET.SubElement(product, "condition").text = row["Condition"]

    print(f"Processed row {i}")

    time.sleep(2)

# --- SAVE XML ---
tree = ET.ElementTree(root)
tree.write("feed.xml", encoding="utf-8", xml_declaration=True)

print("XML Updated Successfully!")
