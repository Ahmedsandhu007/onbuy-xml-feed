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
import csv

# ================= CONFIG =================
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

# 🔥 FULL REFRESH FOR THIS RUN
FULL_REFRESH = False

# 🔥 PROCESS ALL PRODUCTS THIS RUN
MAX_PRODUCTS_PER_RUN = 12

# 🔥 KEEP FALSE NORMALLY
RUN_CATEGORY_MAPPING = True

PK_TZ = ZoneInfo("Asia/Karachi")

# ================= GOOGLE SHEET =================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(
    os.environ["GOOGLE_CREDENTIALS"]
)

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    creds_dict,
    scope
)

client = gspread.authorize(creds)

sheet = client.open(
    "OnBuy_Feed_Master"
).sheet1

data = sheet.get_all_records()

headers = sheet.row_values(1)

col_map = {
    col: idx + 1
    for idx, col in enumerate(headers)
}

print(f"📊 TOTAL ROWS IN SHEET: {len(data)}")

# ================= ONBUY CATEGORY DATA =================
ONBUY_CATEGORIES = []

with open(
    "onbuy_categories_only.csv",
    newline='',
    encoding='utf-8'
) as csvfile:

    reader = csv.DictReader(csvfile)

    for row in reader:

        category = row.get(
            "OnBuy Category Path"
        )

        if category:
            ONBUY_CATEGORIES.append(category)

print(
    f"📂 Loaded {len(ONBUY_CATEGORIES)} "
    f"OnBuy categories"
)

VALID_ONBUY_CATEGORIES = set(
    cat.strip().lower()
    for cat in ONBUY_CATEGORIES
)

# ================= HELPERS =================
def to_jpg(url):

    if not url:
        return ""

    url = re.sub(
        r"\.webp.*$",
        ".jpg",
        url
    )

    url = re.sub(
        r"\.(png|jpeg).*?$",
        ".jpg",
        url
    )

    return url

def clean_additional_images(images):

    if not images:
        return ""

    imgs = [
        to_jpg(i.strip())
        for i in str(images).split(",")
        if i.strip()
    ]

    return ",".join(imgs[:5])

def clean_category(cat):

    if not cat:
        return ""

    cat = str(cat).replace(
        "\n",
        " "
    ).strip()

    if "|" in cat:
        cat = cat.split("|")[-1]

    cat = re.sub(
        r"\s+",
        " ",
        cat
    ).strip()

    return cat

def col_letter(n):

    result = ""

    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result

    return result

def tokenize(text):

    return set(
        re.findall(
            r'\w+',
            str(text).lower()
        )
    )

def is_valid_onbuy_category(category):

    return (
        str(category).strip().lower()
        in VALID_ONBUY_CATEGORIES
    )

def parse_time(value):

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        )

    except:
        return datetime(2000, 1, 1)

# ================= CATEGORY MATCHER =================
def map_onbuy_category(
    title,
    current_category,
    description=""
):

    product_text = f"""
    {title}
    {current_category}
    {description}
    """.lower()

    product_words = tokenize(product_text)

    best_match = None
    best_score = 0

    for category_path in ONBUY_CATEGORIES:

        category_words = tokenize(
            category_path
        )

        score = len(
            product_words.intersection(
                category_words
            )
        )

        for word in product_words:

            if word in category_path.lower():
                score += 2

        if score > best_score:

            best_score = score
            best_match = category_path

    if best_match and best_score >= 2:
        return best_match

    return current_category

# ================= EBAY =================
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

    return res.json().get(
        "access_token"
    )

def get_ebay_data(url, token):

    try:

        match = re.search(
            r"/itm/(\d+)",
            url
        )

        if not match:
            return None, None

        item_id = match.group(1)

        # ✅ CORRECT LEGACY ENDPOINT
        res = requests.get(
            "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"
            },
            params={
                "legacy_item_id": item_id
            }
        )

        # 🚨 TRUE REMOVED LISTING
        if res.status_code == 404:

            print(f"REMOVED LISTING: {item_id}")

            return 0, 0

        data = res.json()

        estimated = data.get(
            "estimatedAvailabilities",
            []
        )

        availability_status = ""

        stock = None

        if estimated:

            availability_status = estimated[0].get(
                "estimatedAvailabilityStatus",
                ""
            )

            # 🚨 TRUE OUT OF STOCK
            if availability_status in [
                "OUT_OF_STOCK",
                "UNAVAILABLE"
            ]:

                print(f"OUT OF STOCK: {item_id}")

                return 0, 0

            stock = estimated[0].get(
                "estimatedAvailableQuantity"
            )

        price_data = data.get(
            "price",
            {}
        )

        price = None

        if price_data:

            try:
                price = float(
                    price_data.get(
                        "value",
                        0
                    )
                )

            except:
                price = None

        # 🚨 PARTIAL API RESPONSE
        if price is None:

            print(
                f"PARTIAL API DATA: {item_id}"
            )

            return None, None

        # FALLBACK STOCK
        if not stock or stock <= 0:
            stock = 5

        return stock, price

    except Exception as e:

        print(f"eBay fetch error: {e}")

        return None, None

# ================= CATEGORY AUTO-MAPPING =================
if RUN_CATEGORY_MAPPING:

    print("\n🔄 Updating OnBuy Categories...")

    category_updates = 0

    bulk_category_updates = []

    for idx, row in enumerate(data):

        i = idx + 2

        title = str(
            row.get("Title") or ""
        )

        current_category = str(
            row.get("Category") or ""
        ).strip()

        description = str(
            row.get("Description") or ""
        )

        if is_valid_onbuy_category(current_category):
            continue

        mapped_category = map_onbuy_category(
            title,
            current_category,
            description
        )

        if mapped_category != current_category:

            bulk_category_updates.append({
                "range": f"{col_letter(col_map['Category'])}{i}",
                "values": [[mapped_category]]
            })

            category_updates += 1

            print(
                f"Prepared category row {i}"
            )

    if bulk_category_updates:

        sheet.batch_update(
            bulk_category_updates
        )

    print(
        f"\n✅ Categories Updated: "
        f"{category_updates}"
    )

# ================= SMART PRIORITY SYNC =================
sorted_data = sorted(
    enumerate(data),
    key=lambda x: parse_time(
        x[1].get("Last Checked Time", "")
    )
)

print(
    f"🔁 Smart Sync | "
    f"Processing {len(sorted_data[:MAX_PRODUCTS_PER_RUN])} products"
)

# ================= UPDATE LOOP =================
token = get_ebay_token()

updated_count = 0
skipped_count = 0

for idx, row in sorted_data[:MAX_PRODUCTS_PER_RUN]:

    i = idx + 2

    url = str(
        row.get("Supplier URL", "")
    ).lower()

    if "ebay." not in url:
        continue

    stock, cost_price = get_ebay_data(
        url,
        token
    )

    # 🚨 PARTIAL API RESPONSE
    if stock is None or cost_price is None:

        skipped_count += 1

        print(f"Skipped row {i}")

        continue

    if stock == 0:

        final_price = 0

    else:

        minimum_price = cost_price * 1.15
        calculated_price = cost_price * 1.4

        final_price = round(
            max(
                minimum_price,
                calculated_price
            ),
            2
        )

    updates = [
        {
            "range": f"{col_letter(col_map['Cost Price (£)'])}{i}",
            "values": [[float(cost_price or 0)]]
        },
        {
            "range": f"{col_letter(col_map['Stock'])}{i}",
            "values": [[int(stock or 0)]]
        },
        {
            "range": f"{col_letter(col_map['Selling Price (£)'])}{i}",
            "values": [[float(final_price)]]
        },
        {
            "range": f"{col_letter(col_map['Status'])}{i}",
            "values": [[
                "ACTIVE"
                if stock > 0
                else "INACTIVE"
            ]]
        },
        {
            "range": f"{col_letter(col_map['Last Updated'])}{i}",
            "values": [[
                datetime.now(PK_TZ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ]]
        },
        {
            "range": f"{col_letter(col_map['Last Checked Time'])}{i}",
            "values": [[
                datetime.now(PK_TZ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ]]
        }
    ]

    sheet.batch_update(updates)

    updated_count += 1

    print(f"Updated row {i}")

    time.sleep(0.5)

# ================= XML =================
root = ET.Element("products")

count = 0
skipped_xml = 0

for idx, row in enumerate(data):

    try:

        sku = str(
            row.get("SKU") or ""
        ).strip()

        title = str(
            row.get("Title") or ""
        ).strip()

        desc = str(
            row.get("Description") or ""
        ).strip()

        image = to_jpg(
            str(
                row.get("Image URL")
                or ""
            )
        )

        brand = str(
            row.get("Brand") or ""
        ).strip()

        category = clean_category(
            row.get("Category")
        )

        price = float(
            re.sub(
                r"[^\d.]",
                "",
                str(
                    row.get(
                        "Selling Price (£)"
                    ) or "0"
                )
            ) or 0
        )

        stock = int(
            row.get("Stock") or 0
        )

        if (
            not all([
                sku,
                title,
                desc,
                image,
                brand,
                category
            ])
        ):

            skipped_xml += 1
            continue

        p = ET.SubElement(
            root,
            "product"
        )

        ET.SubElement(p, "sku").text = sku
        ET.SubElement(p, "product_name").text = title[:150]
        ET.SubElement(p, "description").text = desc
        ET.SubElement(p, "image_url").text = image

        add_imgs = clean_additional_images(
            row.get("Additional Images")
        )

        if add_imgs:
            ET.SubElement(
                p,
                "additional_image_urls"
            ).text = add_imgs

        ET.SubElement(p, "brand").text = brand
        ET.SubElement(p, "category").text = category
        ET.SubElement(p, "ean").text = sku
        ET.SubElement(p, "condition").text = "New"
        ET.SubElement(p, "price").text = str(price)
        ET.SubElement(p, "quantity").text = str(stock)

        count += 1

    except:
        skipped_xml += 1

ET.ElementTree(root).write(
    "feed.xml",
    encoding="utf-8",
    xml_declaration=True
)

print("\n✅ DONE")

print(f"📦 Updated rows: {updated_count}")
print(f"⏭ Skipped updates: {skipped_count}")
print(f"📦 Feed products: {count}")
print(f"⚠ Skipped in feed: {skipped_xml}")
