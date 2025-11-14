import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import requests

COOKIES_FILE = "encar_cookies.json"
OUTPUT_FILE = "encar_truck_results.json"
LOG_FILE = "encar_truck_scraper.log"
DB_FILE = "encar_cars.db"

BASE_API_URL = "https://api.encar.com/search/car/list/premium"
START_YEAR = 2025
MIN_YEAR = 2008
PAGE_SIZE = 1000
OFFSET_STEP = PAGE_SIZE
INITIAL_OFFSET = 0
REQUEST_TIMEOUT = 15
REQUEST_PAUSE_SECONDS = 1
DOUBLE_PAGES_TO_SKIP = 10
CYCLE_PAUSE = 60  # Пауза между циклами парсинга в секундах


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("encar_truck_scraper")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.debug("Логирование инициализировано")
    return logger


def load_cookies(logger: logging.Logger) -> Optional[List[Dict]]:
    if not os.path.exists(COOKIES_FILE):
        logger.error("Файл с куками не найден: %s", COOKIES_FILE)
        return None

    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Не удалось загрузить куки из %s: %s", COOKIES_FILE, exc)
        return None

    cookies = data.get("cookies")
    if not cookies:
        logger.error("В файле %s отсутствуют куки", COOKIES_FILE)
        return None

    logger.info("Используем сохраненные куки (%d шт.)", len(cookies))
    return cookies

def load_headers(logger: logging.Logger):
    if not os.path.exists(COOKIES_FILE):
        logger.error("Файл с куками не найден: %s", COOKIES_FILE)
        return None

    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Не удалось загрузить headers из %s: %s", COOKIES_FILE, exc)
        return None

    headers = data.get("headers")
    if not headers:
        logger.error("В файле %s отсутствуют куки", COOKIES_FILE)
        return None

    logger.info("Используем сохраненные headers (%d шт.)", len(headers))
    return headers


def create_session_with_cookies(cookies: List[Dict], logger: logging.Logger) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies.clear()
    session.headers.update(load_headers(logger))

    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name:
            continue
        session.cookies.set(
            name,
            value,
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
            secure=cookie.get("secure", False),
        )

    return session


def build_year_range(year: int) -> str:
    start = year * 100
    end = year * 100 + 99
    return f"{start}..{end}"


def normalize_car_id(raw_id: object) -> Optional[Tuple[str, object]]:
    if raw_id is None:
        return None

    raw_str = str(raw_id).strip()
    if not raw_str:
        return None

    if raw_str.isdigit():
        stored_value: object = int(raw_str)
    else:
        stored_value = raw_str

    return raw_str, stored_value


def fetch_page(
        session: requests.Session,
        logger: logging.Logger,
        year_range: str,
        offset: int,
) -> Optional[List[Dict]]:
    params = {
        "count": "true",
        "q": f"(And.Hidden.N._.Year.range({year_range}).)",
        "sr": f"|ModifiedDate|{offset}|{PAGE_SIZE}",
    }
    logger.info("Запрос год %s, offset %d", year_range, offset)

    try:
        response = session.get(BASE_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("HTTP ошибка при запросе (%s, offset %d): %s", year_range, offset, exc)
        return None

    if response.status_code != 200:
        logger.error(
            "API вернул статус %s для диапазона %s и offset %d: %s",
            response.status_code,
            year_range,
            offset,
            response.text[:500],
        )
        return None

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        logger.error("Не удалось разобрать JSON (%s, offset %d): %s", year_range, offset, exc)
        return None

    results = payload.get("SearchResults", [])
    logger.info("Получено результатов: %d", len(results))
    return results


def load_existing_results(logger: logging.Logger) -> Tuple[List[Dict], Set[str]]:
    if not os.path.exists(OUTPUT_FILE):
        logger.info("Файл результатов %s не найден, начнем с пустого списка", OUTPUT_FILE)
        return [], set()

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Не удалось прочитать существующие результаты из %s: %s", OUTPUT_FILE, exc)
        return [], set()

    if not isinstance(data, list):
        logger.error("Неверный формат в %s, ожидается список", OUTPUT_FILE)
        return [], set()

    collected: List[Dict] = []
    seen_ids: Set[str] = set()

    for entry in data:
        if not isinstance(entry, dict):
            continue

        raw_id = entry.get("id") if "id" in entry else entry.get("Id")
        normalized = normalize_car_id(raw_id)
        if not normalized:
            continue

        id_str, stored_value = normalized
        seen_ids.add(id_str)
        collected.append({"id": stored_value})

    logger.info("Загружено %d существующих записей из %s", len(collected), OUTPUT_FILE)
    return collected, seen_ids


def init_database(logger: logging.Logger) -> None:
    """Инициализирует базу данных SQLite с таблицей для автомобилей"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cars (
                id TEXT PRIMARY KEY,
                condition TEXT,
                manufacturer TEXT,
                model TEXT,
                badge TEXT,
                transmission TEXT,
                fuel_type TEXT,
                year REAL,
                form_year TEXT,
                mileage REAL,
                price REAL,
                sell_type TEXT,
                modified_date TEXT,
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


def save_cars_to_db_batch(logger: logging.Logger, cars: List[Dict], collected_at: str) -> int:
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
                (id, condition, manufacturer, model, badge, transmission, fuel_type, 
                 year, form_year, mileage, price, sell_type, modified_date, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, car_data + (collected_at,))
            saved_count += 1
        
        conn.commit()
        conn.close()
        return saved_count
    except sqlite3.Error as exc:
        logger.error("Ошибка при сохранении автомобилей в БД: %s", exc)
        return 0


def save_results(logger: logging.Logger, cars: List[Dict]) -> None:
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            json.dump(cars, file, ensure_ascii=False, indent=2)
        logger.info("Обновлен файл %s (всего записей: %d)", OUTPUT_FILE, len(cars))
    except OSError as exc:
        logger.error("Не удалось сохранить данные в %s: %s", OUTPUT_FILE, exc)


def scrape_trucks(logger: logging.Logger) -> None:
    cookies = load_cookies(logger)
    if not cookies:
        return

    session = create_session_with_cookies(cookies, logger)
    collected, seen_ids = load_existing_results(logger)

    for year in range(START_YEAR, MIN_YEAR - 1, -1):
        year_range = build_year_range(year)
        logger.info("=== Обработка диапазона %s ===", year_range)

        offset = INITIAL_OFFSET

        # Счетчик полностью повторяющейся выборки
        all_repeat_count = DOUBLE_PAGES_TO_SKIP
        while True:

            page_results = fetch_page(session, logger, year_range, offset)
            if page_results is None:
                logger.warning("Пропускаем offset %d для диапазона %s из-за ошибки", offset, year_range)
                break

            if not page_results:
                logger.info(
                    "Данных больше нет для диапазона %s (offset %d), переходим к следующему году",
                    year_range,
                    offset,
                )
                break

            new_count = 0
            repeat_count = 0
            collected_at = datetime.now().isoformat()
            cars_to_save = []
            
            for car in page_results:
                normalized = normalize_car_id(car.get("Id"))
                if not normalized:
                    continue

                car_id_str, stored_value = normalized
                if car_id_str not in seen_ids:
                    seen_ids.add(car_id_str)
                    collected.append({"id": stored_value})
                    new_count += 1
                else:
                    repeat_count += 1
                
                # Добавляем в список для сохранения в БД (включая обновления)
                cars_to_save.append(car)
            
            # Сохраняем все автомобили со страницы в БД батчем
            if cars_to_save:
                saved_count = save_cars_to_db_batch(logger, cars_to_save, collected_at)
                logger.debug("Сохранено в БД: %d автомобилей", saved_count)
            logger.info(
                "Добавлено записей: %d, Дублей: %d  (всего %d)",
                new_count, repeat_count,
                len(collected),
            )
            if new_count > 0:
                save_results(logger, collected)
            if new_count == 0:
                all_repeat_count -= 1

            if all_repeat_count < 0:
                logger.info(
                    "Повтор дублей! %s (offset %d), переходим к следующему году",
                    year_range,
                    offset,
                )
                break

            offset += OFFSET_STEP
            time.sleep(REQUEST_PAUSE_SECONDS)
        time.sleep(REQUEST_PAUSE_SECONDS)

    save_results(logger, collected)


def main() -> None:
    logger = setup_logging()
    logger.info("Старт парсинга авто Encar")
    
    # Инициализируем базу данных
    init_database(logger)
    
    cycle_number = 0
    while True:
        cycle_number += 1
        logger.info("=" * 60)
        logger.info("Начало цикла парсинга #%d", cycle_number)
        logger.info("=" * 60)
        
        start_ts = time.time()
        try:
            scrape_trucks(logger)
        except Exception as exc:
            logger.error("Ошибка в цикле парсинга: %s", exc, exc_info=True)
        finally:
            duration = time.time() - start_ts
            logger.info("Цикл #%d завершен за %.2f секунд", cycle_number, duration)
        
        logger.info("Пауза %d секунд перед следующим циклом...", CYCLE_PAUSE)
        time.sleep(CYCLE_PAUSE)


if __name__ == "__main__":
    main()
