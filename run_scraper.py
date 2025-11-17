import json
import logging
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import requests
from decouple import config

from browser_data_updater import refresh_cookies
from service import load_browser_data, create_session_with_cookies, setup_logging

COOKIES_FILE = config("COOKIES_FILE", default="encar_cookies.json")
LOG_FILE = config("LOG_FILE", default="encar_truck_scraper.log")
DB_FILE = config("DB_FILE", default="encar_cars.db")

BASE_API_URL = config("BASE_API_URL", default="https://api.encar.com/search/car/list/premium")
START_YEAR = config("START_YEAR", default=2025, cast=int)
MIN_YEAR = config("MIN_YEAR", default=2008, cast=int)
PAGE_SIZE = config("PAGE_SIZE", default=1000, cast=int)
OFFSET_STEP = config("OFFSET_STEP", default=1000, cast=int)
INITIAL_OFFSET = config("INITIAL_OFFSET", default=0, cast=int)
REQUEST_TIMEOUT = config("REQUEST_TIMEOUT", default=15, cast=int)
REQUEST_PAUSE_SECONDS = config("REQUEST_PAUSE_SECONDS", default=1.6, cast=float)
DOUBLE_PAGES_TO_SKIP = config("DOUBLE_PAGES_TO_SKIP", default=10, cast=int)
CYCLE_PAUSE = config("CYCLE_PAUSE", default=60, cast=int)  # Пауза между циклами парсинга в секундах

# Настройки микросервиса логирования
NOTIFICATION_SERVICE_NAME = config("NOTIFICATION_SERVICE_NAME", default="EncarParsing")
NOTIFICATION_API_BASE = config("NOTIFICATION_API_BASE", default="http://188.225.73.94/api/notifications")
NOTIFICATION_TIMEOUT = config("NOTIFICATION_TIMEOUT", default=5, cast=int)


def check_browser_data_validity(logger: logging.Logger) -> bool:
    """Проверяет пригодность cookies и headers, делая тестовый запрос к API"""
    browser_data = load_browser_data(logger)
    if not browser_data:
        return False

    cookies, headers = browser_data
    session = create_session_with_cookies(cookies, headers)

    # Делаем тестовый запрос с минимальными параметрами
    test_params = {
        "count": "true",
        "q": "(And.Hidden.N._.Year.range(202500..202599).)",
        "sr": f"|ModifiedDate|0|1",
    }

    try:
        response = session.get(BASE_API_URL, params=test_params, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            payload = response.json()
            # Проверяем, что получили валидный ответ
            if "SearchResults" in payload:
                logger.info("Проверка cookies/headers: данные валидны")
                return True
            else:
                logger.warning("Проверка cookies/headers: получен неожиданный формат ответа")
                return False
        else:
            logger.warning("Проверка cookies/headers: API вернул статус %d", response.status_code)
            return False
    except Exception as exc:
        logger.warning("Проверка cookies/headers: ошибка при тестовом запросе: %s", exc)
        return False


def build_year_range(year: int) -> str:
    start = year * 100
    end = year * 100 + 99
    return f"{start}..{end}"


def normalize_car_id(raw_id: object) -> Optional[str]:
    """Нормализует ID автомобиля в строку"""
    if raw_id is None:
        return None
    car_id_str = str(raw_id).strip()
    return car_id_str if car_id_str else None


def fetch_page(
        session: requests.Session,
        year_range: str,
        offset: int,
        logger: logging.Logger,
) -> Optional[List[Dict]]:
    """Получает страницу результатов из API"""
    params = {
        "count": "true",
        "q": f"(And.Hidden.N._.Year.range({year_range}).)",
        "sr": f"|ModifiedDate|{offset}|{PAGE_SIZE}",
    }

    try:
        response = session.get(BASE_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            # Если получили 401/403, возможно cookies устарели
            if response.status_code in (401, 403):
                logger.warning("Получен статус %d, возможно cookies устарели", response.status_code)
            return None
        payload = response.json()
        return payload.get("SearchResults", [])
    except (requests.RequestException, json.JSONDecodeError):
        return None


def init_database(logger: logging.Logger) -> None:
    """Инициализирует базу данных SQLite с таблицей для автомобилей"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cars (
                id TEXT PRIMARY KEY,
                condition TEXT,
                brand_fk.name TEXT,
                model_fk.name TEXT,
                badgeDetailNm TEXT,
                transmission TEXT,
                fuelNm TEXT,
                year REAL,
                formYear TEXT,
                mileage REAL,
                price REAL,
                sell_type TEXT,
                mdfDt TEXT,
                collected_at TEXT,
                UNIQUE(id)
            )
        """)

        conn.commit()
        conn.close()
        logger.info("База данных инициализирована: %s", DB_FILE)
    except sqlite3.Error as exc:
        logger.error("Ошибка при инициализации базы данных: %s", exc)


def extract_car_data(car: Dict) -> Optional[Tuple]:
    """Извлекает данные об автомобиле для сохранения в БД"""
    car_id = str(car.get("Id", ""))
    if not car_id:
        return None

    condition = json.dumps(car.get("Condition", []), ensure_ascii=False) if car.get("Condition") else None
    manufacturer = car.get("Manufacturer")
    model = car.get("Model")
    badge = car.get("Badge")
    transmission = car.get("Transmission")
    fuel_type = car.get("FuelType")
    year = car.get("Year")
    form_year = car.get("FormYear")
    mileage = car.get("Mileage")
    price = car.get("Price")
    sell_type = car.get("SellType")
    modified_date = car.get("ModifiedDate")

    return (
        car_id, condition, manufacturer, model, badge, transmission, fuel_type,
        year, form_year, mileage, price, sell_type, modified_date
    )


def save_cars_to_db_batch(cars: List[Dict], collected_at: str) -> int:
    """Сохраняет список автомобилей в SQLite батчем"""
    if not cars:
        return 0

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        saved_count = 0
        for car in cars:
            car_data = extract_car_data(car)
            if not car_data:
                continue

            cursor.execute("""
                INSERT OR REPLACE INTO cars 
                (id, condition, brand_fk.name, model_fk.name, badgeDetailNm, transmission, fuelNm, 
                 year, formYear, mileage, price, sell_type, mdfDt, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, car_data + (collected_at,))
            saved_count += 1

        conn.commit()
        conn.close()
        return saved_count
    except sqlite3.Error:
        return 0


def scrape_cars(logger: logging.Logger) -> None:
    """Основная функция парсинга автомобилей"""
    browser_data = load_browser_data(logger)
    if not browser_data:
        logger.warning("Попытка обновить cookies и headers...")
        if refresh_cookies(logger):
            browser_data = load_browser_data(logger)
            if not browser_data:
                logger.error("Не удалось загрузить данные после обновления")
                return
        else:
            logger.error("Не удалось обновить cookies и headers")
            return

    cookies, headers = browser_data
    session = create_session_with_cookies(cookies, headers)

    seen_ids_in_cycle: Set[str] = set()
    cycle_start_time = time.time()
    total_processed = 0

    for year in range(START_YEAR, MIN_YEAR - 1, -1):
        year_start_time = time.time()
        year_range = build_year_range(year)
        year_processed_count = 0

        offset = INITIAL_OFFSET
        all_repeat_count = DOUBLE_PAGES_TO_SKIP

        while True:
            page_results = fetch_page(session, year_range, offset, logger)
            if page_results is None:
                # Если несколько раз подряд не получаем ответ, возможно cookies устарели
                logger.warning("Не удалось получить данные для года %s, offset %d", year_range, offset)
                # Пробуем обновить cookies и повторить запрос
                if refresh_cookies(logger):
                    browser_data = load_browser_data(logger)
                    if browser_data:
                        cookies, headers = browser_data
                        session = create_session_with_cookies(cookies, headers)
                        # Повторяем запрос
                        page_results = fetch_page(session, year_range, offset, logger)
                        if page_results is None:
                            break
                    else:
                        break
                else:
                    break
            if not page_results:
                break

            collected_at = datetime.now().isoformat()
            cars_to_save = []
            new_count = 0

            for car in page_results:
                car_id_str = normalize_car_id(car.get("Id"))
                if not car_id_str:
                    continue

                if car_id_str not in seen_ids_in_cycle:
                    seen_ids_in_cycle.add(car_id_str)
                    new_count += 1

                cars_to_save.append(car)

            if cars_to_save:
                saved_count = save_cars_to_db_batch(cars_to_save, collected_at)
                year_processed_count += saved_count
                logger.info("Год %s, offset %d: обработано %d авто", year_range, offset, saved_count)

            if new_count == 0:
                all_repeat_count -= 1

            if all_repeat_count < 0:
                break

            offset += OFFSET_STEP
            time.sleep(REQUEST_PAUSE_SECONDS)

        year_duration = time.time() - year_start_time
        total_processed += year_processed_count
        logger.info("Год %s: обработано %d авто за %.2f сек", year_range, year_processed_count, year_duration)
        time.sleep(REQUEST_PAUSE_SECONDS)

    cycle_duration = time.time() - cycle_start_time
    logger.info("=== Итоги цикла: обработано %d авто за %.2f сек (%.2f сек/год) ===",
                total_processed, cycle_duration, cycle_duration / (START_YEAR - MIN_YEAR + 1))


def main() -> None:
    logger = setup_logging()
    logger.info("Старт парсинга авто Encar")
    # Инициализируем базу данных
    init_database(logger)

    # Проверяем пригодность cookies/headers при запуске
    if not check_browser_data_validity(logger):
        logger.warning("Cookies/headers невалидны или отсутствуют. Попытка обновления...")
        if not refresh_cookies(logger):
            logger.error("Не удалось получить валидные cookies/headers. Завершение работы.")
            return
        # Повторная проверка после обновления
        if not check_browser_data_validity(logger):
            logger.error("Cookies/headers все еще невалидны после обновления. Завершение работы.")
            return

    cycle_number = 0
    while True:
        cycle_number += 1
        logger.info("=" * 60)
        logger.info("Начало цикла парсинга #%d", cycle_number)
        logger.info("=" * 60)

        start_ts = time.time()
        try:
            scrape_cars(logger)
        except Exception as exc:
            logger.error("Ошибка в цикле парсинга: %s", exc, exc_info=True)
        finally:
            duration = time.time() - start_ts
            logger.info("Цикл #%d завершен за %.2f секунд", cycle_number, duration)

        logger.info("Пауза %d секунд перед следующим циклом...", CYCLE_PAUSE)
        time.sleep(CYCLE_PAUSE)


if __name__ == "__main__":
    main()
