import json
import logging
import os
import platform
import time

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

logging.basicConfig(level=logging.INFO)

COOKIES_FILE = "encar_cookies.json"




# Запускаем виртуальный дисплей только при необходимости
def start_virtual_display_if_needed():
    system = platform.system().lower()

    # Linux VPS (нет внешнего дисплея)
    if system == "linux" and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            logging.info("🟢 Virtual display started (Xvfb)")
            return display
        except Exception as e:
            logging.error(f"❌ Failed to start virtual display: {e}")
    else:
        logging.info("ℹ️ Virtual display not needed on this OS")

    return None


def save_browser_data(cookies, headers):
    with open(COOKIES_FILE, "w") as f:
        json.dump({
            "saved_at": time.time(),
            "cookies": cookies,
            "headers": headers,
        }, f)



def load_cookies():
    if not os.path.exists(COOKIES_FILE):
        return None

    with open(COOKIES_FILE, "r") as f:
        data = json.load(f)

    logging.info("✅ Используем сохраненные куки")
    return data["cookies"]


def test():
    display = start_virtual_display_if_needed()

    options = Options()

    # 🔧 Отключаем загрузку картинок
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=9222")
    # options.add_argument("--headless")  # если на сервере без GUI



    if os.system == "linux" and not os.environ.get("DISPLAY"):
        service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
    else:
        service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=options)
    # 🔹 Блокируем запросы к Google Ads
    driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["*googleadservices.com*"]})
    driver.execute_cdp_cmd("Network.enable", {})
    try:

        print("1. Opening Encar page...")
        url = "https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!%7B%22action%22%3A%22(And.Hidden.N._.CarType.N.)%22%2C%22toggle%22%3A%7B%7D%2C%22layer%22%3A%22%22%2C%22sort%22%3A%22ModifiedDate%22%2C%22page%22%3A1%2C%22limit%22%3A20%2C%22searchKey%22%3A%22%22%2C%22loginCheck%22%3Afalse%7D"
        driver.get(url)
        time.sleep(5)
        print(f"   Page title: {driver.title}")

        cookies = driver.get_cookies()
        print(f"   Got {len(cookies)} cookies")

        print("2. Making API request with browser...")
        session = requests.Session()
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'])

        new_headers = {
            'User-Agent': driver.execute_script("return navigator.userAgent"),
            'Referer': driver.current_url
        }

        session.headers.update(new_headers)

        api_url = "https://api.encar.com/search/car/list/premium"
        params = {"count": "true", "q": "(And.Hidden.N._.CarType.N.)", "sr": "|ModifiedDate|20|20"}

        resp = session.get(api_url, params=params, timeout=10)
        print(f"   API status: {resp.status_code}")
        if resp.status_code == 200:
            save_browser_data(cookies, new_headers)
            data = resp.json()
            print(f"   API data: {data}")
            print(f"   Found {len(data.get('SearchResults', []))} cars")
        else:
            print(f"   Error: {resp.text[:200]}")

    finally:
        driver.quit()
        if display:
            display.stop()


if __name__ == "__main__":
    test()
