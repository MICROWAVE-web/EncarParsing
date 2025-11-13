import json
import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import requests
import time
import logging

logging.basicConfig(level=logging.INFO)

COOKIES_FILE = "encar_cookies.json"


def save_cookies(cookies):
    with open(COOKIES_FILE, "w") as f:
        json.dump({
            "saved_at": time.time(),
            "cookies": cookies
        }, f)



def load_cookies():
    if not os.path.exists(COOKIES_FILE):
        return None

    with open(COOKIES_FILE, "r") as f:
        data = json.load(f)

    # Проверяем не устарели ли куки (берем самый короткий срок - 30 минут)
    if time.time() - data["saved_at"] > 30 * 60:  # 30 минут
        logging.info("🕐 Куки устарели (прошло больше 30 минут)")
        return None

    logging.info("✅ Используем сохраненные куки")
    return data["cookies"]

def test():

    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    driver = webdriver.Chrome(options=options)

    try:

        print("1. Opening Encar page...")
        url = "https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!%7B%22action%22%3A%22(And.Hidden.N._.CarType.N.)%22%2C%22toggle%22%3A%7B%7D%2C%22layer%22%3A%22%22%2C%22sort%22%3A%22ModifiedDate%22%2C%22page%22%3A1%2C%22limit%22%3A20%2C%22searchKey%22%3A%22%22%2C%22loginCheck%22%3Afalse%7D"
        driver.get(url)
        time.sleep(5)
        print(f"   Page title: {driver.title}")

        cookies = driver.get_cookies()
        print(f"   Got {len(cookies)} cookies")
        save_cookies(cookies)

        print("2. Making API request with browser...")
        session = requests.Session()
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'])

        new_headers = {
            'User-Agent': driver.execute_script("return navigator.userAgent"),
            'Referer': driver.current_url
        }
        print(new_headers)
        session.headers.update(new_headers)

        api_url = "https://api.encar.com/search/car/list/premium"
        params = {"count": "true", "q": "(And.Hidden.N._.CarType.N.)", "sr": "|ModifiedDate|20|20"}

        resp = session.get(api_url, params=params, timeout=10)
        print(f"   API status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"   Found {len(data.get('SearchResults', []))} cars")
        else:
            print(f"   Error: {resp.text[:200]}")

    finally:
        driver.quit()


if __name__ == "__main__":
    test()

