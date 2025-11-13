import os
import sys
import time
import logging
import requests
from datetime import datetime

# Очистка переменных окружения прокси
proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
              'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy']
for var in proxy_vars:
    os.environ.pop(var, None)

# ==========================
# ⚙️ Логирование
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# ==========================
# 🔧 Настройки
# ==========================
BASE_URL = "https://www.encar.com"
BASE_API = "https://api.encar.com"


# ==========================
# 🧱 Функции
# ==========================
def create_session():
    """Создает сессию с настройками браузера"""
    session = requests.Session()
    session.trust_env = False
    session.proxies.clear()

    # Базовые заголовки браузера
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'no-cache',
    })

    return session


def visit_encar_page(session):
    """Посещает главную страницу поиска Encar"""
    url = "https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!%7B%22action%22%3A%22(And.Hidden.N._.CarType.N.)%22%2C%22toggle%22%3A%7B%7D%2C%22layer%22%3A%22%22%2C%22sort%22%3A%22ModifiedDate%22%2C%22page%22%3A1%2C%22limit%22%3A20%2C%22searchKey%22%3A%22%22%2C%22loginCheck%22%3Afalse%7D"

    logging.info("🔄 Посещаем страницу Encar...")

    try:
        response = session.get(url, timeout=10)
        logging.info(f"✅ Страница загружена. Статус: {response.status_code}")
        logging.info(f"📏 Размер ответа: {len(response.text)} символов")

        # Сохраняем куки, которые установились
        cookies = session.cookies.get_dict()
        logging.info(f"🍪 Получено куки: {cookies}")

        return True

    except Exception as e:
        logging.error(f"❌ Ошибка при загрузке страницы: {e}")
        return False


def make_api_request(session):
    """Выполняет API-запрос к Encar"""
    url = f"{BASE_API}/search/car/list/premium"

    params = {
        "count": "true",
        "q": "(And.Hidden.N._.CarType.N.)",
        "sr": "|ModifiedDate|20|20"
    }

    # Обновляем заголовки для API-запроса
    api_headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Origin': BASE_URL,
        'Referer': 'https://www.encar.com/fc/fc_carsearchlist.do?carType=for',
        'Sec-Fetch-Site': 'same-site',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Dest': 'empty',
        'X-Requested-With': 'XMLHttpRequest'
    }

    logging.info("🔄 Выполняем API-запрос...")
    logging.info(f"📤 URL: {url}")
    logging.info(f"📋 Параметры: {params}")

    try:
        response = session.get(
            url,
            params=params,
            headers=api_headers,
            timeout=10
        )

        logging.info(f"📥 Ответ API. Статус: {response.status_code}")
        logging.info(f"📏 Размер ответа: {len(response.text)} символов")

        if response.status_code == 200:
            data = response.json()
            logging.info(f"✅ API-запрос успешен!")
            logging.info(f"📊 Найдено автомобилей: {len(data.get('SearchResults', []))}")
            return True
        else:
            logging.error(f"❌ Ошибка API: {response.status_code}")
            logging.error(f"📄 Текст ответа: {response.text[:500]}")
            return False

    except Exception as e:
        logging.error(f"❌ Ошибка при API-запросе: {e}")
        return False


def debug_session_info(session):
    """Выводит отладочную информацию о сессии"""
    logging.info("🔍 Информация о сессии:")
    logging.info(f"   Куки: {session.cookies.get_dict()}")
    logging.info(f"   Заголовки: {dict(session.headers)}")


# ==========================
# 🚀 Основной скрипт
# ==========================
def main():
    logging.info("🚀 Запуск скрипта Encar...")

    # Создаем сессию
    session = create_session()

    # Шаг 1: Посещаем страницу Encar
    if not visit_encar_page(session):
        logging.error("Не удалось загрузить страницу Encar")
        return

    # Небольшая пауза между запросами
    time.sleep(2)

    # Отладочная информация
    debug_session_info(session)

    # Шаг 2: Выполняем API-запрос
    if not make_api_request(session):
        logging.error("Не удалось выполнить API-запрос")
        return

    logging.info("✅ Скрипт завершен успешно!")


# ==========================
# ▶️ Запуск
# ==========================
if __name__ == "__main__":
    main()