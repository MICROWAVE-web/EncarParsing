import os
import json
import time
import requests
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

COOKIES_FILE = "encar_cookies.json"


def save_cookies(cookies):
    """Сохраняет куки в файл с временем сохранения"""
    with open(COOKIES_FILE, "w") as f:
        json.dump({
            "saved_at": time.time(),
            "cookies": cookies
        }, f, indent=4)


def load_cookies():
    """Загружает куки из файла, если они еще действительны"""
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


def make_api_request_with_cookies(cookies):
    """Делает API запрос с переданными куками"""
    session = requests.Session()
    session.trust_env = False

    # Устанавливаем куки
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'])

    # Заголовки как в браузере
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Origin': 'https://www.encar.com',
        'Referer': 'https://www.encar.com/fc/fc_carsearchlist.do',
    })

    api_url = "https://api.encar.com/search/car/list/premium"
    params = {
        "count": "true",
        "q": "(And.Hidden.N._.CarType.N.)",
        "sr": "|ModifiedDate|20|20"
    }

    try:
        response = session.get(api_url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            logging.info(f"✅ API успешен! Найдено {len(data.get('SearchResults', []))} авто")
            return True
        else:
            logging.error(f"❌ API ошибка: {response.status_code}")
            return False
    except Exception as e:
        logging.error(f"❌ Ошибка запроса: {e}")
        return False


def get_fresh_cookies_with_selenium():
    """Получает свежие куки через Selenium (вызывается только если сохраненные не работают)"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    logging.info("🔄 Получаем свежие куки через Selenium...")

    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(options=options)
    try:
        url = "https://www.encar.com/fc/fc_carsearchlist.do?carType=for"
        driver.get(url)
        time.sleep(5)

        cookies = driver.get_cookies()
        save_cookies(cookies)
        logging.info(f"💾 Сохранено {len(cookies)} куки")
        return cookies
    finally:
        driver.quit()


def main():
    # Пытаемся использовать сохраненные куки
    cookies = load_cookies()

    if cookies:
        logging.info("🔄 Пробуем API с сохраненными куки...")
        if make_api_request_with_cookies(cookies):
            return  # Успех!

    # Если сохраненных нет или они не работают - получаем свежие
    logging.info("🔄 Сохраненные куки не работают, получаем свежие...")
    cookies = get_fresh_cookies_with_selenium()

    # Пробуем API с новыми куки
    if make_api_request_with_cookies(cookies):
        logging.info("✅ Успех с новыми куки!")
    else:
        logging.error("❌ Даже с новыми куки не работает")


if __name__ == "__main__":
    main()