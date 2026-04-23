import requests

url = "https://api-sg.aliexpress.com/rest/auth/token/create"

payload = {
    "app_key": "532690",
    "app_secret": "F3r7sfl3IKA2b30ezEhDWK5uVfIq8fJy",
    "code": "3_532690_4AevPJMgmYdq6z9H5DdRQ8eu1350"
}

res = requests.post(url, data=payload)
print(res.json())
