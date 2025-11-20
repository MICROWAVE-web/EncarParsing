import json
import logging
import sqlite3
import time
import traceback
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
READSIDE_API_URL = config("READSIDE_API_URL", default="https://api.encar.com/v1/readside/vehicles")
START_YEAR = config("START_YEAR", default=2025, cast=int)
MIN_YEAR = config("MIN_YEAR", default=2008, cast=int)
READSIDE_BATCH_SIZE = 20  # Лимит ID в одном запросе к readside API
PAGE_SIZE = config("PAGE_SIZE", default=1000, cast=int)
OFFSET_STEP = config("OFFSET_STEP", default=1000, cast=int)
INITIAL_OFFSET = config("INITIAL_OFFSET", default=0, cast=int)
REQUEST_TIMEOUT = config("REQUEST_TIMEOUT", default=30, cast=int)
REQUEST_PAUSE_SECONDS = config("REQUEST_PAUSE_SECONDS", default=1.6, cast=float)
DOUBLE_PAGES_TO_SKIP = config("DOUBLE_PAGES_TO_SKIP", default=10, cast=int)
CYCLE_PAUSE = config("CYCLE_PAUSE", default=60, cast=int)  # Пауза между циклами парсинга в секундах

# Настройки микросервиса логирования
NOTIFICATION_SERVICE_NAME = config("NOTIFICATION_SERVICE_NAME", default="EncarParsing")
NOTIFICATION_API_BASE = config("NOTIFICATION_API_BASE", default="http://188.225.73.94/api/notifications")
NOTIFICATION_TIMEOUT = config("NOTIFICATION_TIMEOUT", default=5, cast=int)
INFINITY = config("INFINITY", cast=bool, default=False)

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
            if response.status_code in (401, 403, 407):
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
                brand_fk TEXT,
                model_fk TEXT,
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cars_details (
                vehicleId TEXT PRIMARY KEY,
                yearMonth TEXT,
                displacement INTEGER,
                manufacturerEnglishName TEXT,
                modelGroupEnglishName TEXT,
                gradeEnglishName TEXT,
                gradeDetailEnglishName TEXT,
                colorName TEXT,
                seatCount INTEGER,
                vehicleNo TEXT,
                vin TEXT,
                advertisement_status TEXT,
                finish REAL,
                collected_at TEXT,
                UNIQUE(vehicleId)
            )
        """)

        conn.commit()
        conn.close()
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


def save_cars_to_db_batch(cars: List[Dict], collected_at: str) -> Tuple[int, int]:
    """Сохраняет список автомобилей в SQLite батчем
    Возвращает кортеж (количество новых, количество обновленных)"""
    if not cars:
        return 0, 0

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        new_count = 0
        updated_count = 0
        
        for car in cars:
            car_data = extract_car_data(car)
            if not car_data:
                continue

            car_id = car_data[0]
            
            # Проверяем, существует ли запись с таким ID
            cursor.execute("SELECT id FROM cars WHERE id = ?", (car_id,))
            exists = cursor.fetchone() is not None

            cursor.execute("""
                INSERT OR REPLACE INTO cars 
                (id, condition, brand_fk, model_fk, badgeDetailNm, transmission, fuelNm, 
                 year, formYear, mileage, price, sell_type, mdfDt, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, car_data + (collected_at,))
            
            if exists:
                updated_count += 1
            else:
                new_count += 1

        conn.commit()
        conn.close()
        return new_count, updated_count
    except sqlite3.Error:
        traceback.print_exc()
        return 0, 0


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
    total_new = 0
    total_updated = 0

    for year in range(START_YEAR, MIN_YEAR - 1, -1):
        year_start_time = time.time()
        year_range = build_year_range(year)
        year_processed_count = 0
        year_new = 0
        year_updated = 0

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
            new_ids_in_page = 0

            for car in page_results:
                car_id_str = normalize_car_id(car.get("Id"))
                if not car_id_str:
                    continue

                if car_id_str not in seen_ids_in_cycle:
                    seen_ids_in_cycle.add(car_id_str)
                    new_ids_in_page += 1

                cars_to_save.append(car)

            if cars_to_save:
                new_count, updated_count = save_cars_to_db_batch(cars_to_save, collected_at)
                total_count = new_count + updated_count
                year_processed_count += total_count
                year_new += new_count
                year_updated += updated_count
                logger.info("Год %s, offset %d: обработано %d авто (Новых: %d, Обновлено: %d)", 
                           year_range, offset, total_count, new_count, updated_count)

            if new_ids_in_page == 0:
                all_repeat_count -= 1

            if all_repeat_count < 0:
                break

            offset += OFFSET_STEP
            time.sleep(REQUEST_PAUSE_SECONDS)

        year_duration = time.time() - year_start_time
        total_processed += year_processed_count
        total_new += year_new
        total_updated += year_updated
        logger.info("Год %s: обработано %d авто (Новых: %d, Обновлено: %d) за %.2f сек", 
                   year_range, year_processed_count, year_new, year_updated, year_duration)
        time.sleep(REQUEST_PAUSE_SECONDS)

    cycle_duration = time.time() - cycle_start_time
    logger.info("=== Итоги цикла: обработано %d авто (Новых: %d, Обновлено: %d) за %.2f сек (%.2f сек/год) ===",
                total_processed, total_new, total_updated, cycle_duration, 
                cycle_duration / (START_YEAR - MIN_YEAR + 1))


def get_all_car_ids(logger: logging.Logger) -> List[str]:
    """Получает все ID автомобилей из таблицы cars"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM cars")
        ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        logger.info("Получено %d ID автомобилей из таблицы cars", len(ids))
        return ids
    except sqlite3.Error as exc:
        logger.error("Ошибка при получении ID из таблицы cars: %s", exc)
        return []


def fetch_cars_details(
    session: requests.Session,
    vehicle_ids: List[str],
    logger: logging.Logger
) -> Optional[List[Dict]]:
    """Получает детальную информацию об автомобилях через readside API"""
    if not vehicle_ids:
        return None
    
    # Формируем строку с ID через запятую
    ids_string = ",".join(vehicle_ids)
    
    params = {
        "vehicleIds": ids_string,
        "include": "SPEC,ADVERTISEMENT,CATEGORY,OPTIONS"
    }
    
    try:
        response = session.get(READSIDE_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            if response.status_code in (401, 403, 407):
                logger.warning("Получен статус %d при запросе детальной информации, возможно cookies устарели", response.status_code)
            else:
                logger.warning("Получен статус %d при запросе детальной информации", response.status_code)
            return None
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return []
    except (requests.RequestException, json.JSONDecodeError) as exc:
        logger.error("Ошибка при запросе детальной информации: %s", exc)
        return None


def process_cars_details(logger: logging.Logger) -> None:
    """Обрабатывает детальную информацию об автомобилях:
    - Получает все ID из таблицы cars
    - Запрашивает детальную информацию через readside API
    - Удаляет авто со статусами BOOKED, DELETE, LEASE_SALE, SOLD, WAIT из cars
    - Удаляет авто с брендом "other" из cars
    - Сохраняет остальные в cars_details
    """
    logger.info("Начало обработки детальной информации об автомобилях")
    start_time = time.time()
    
    # Статусы, при которых нужно удалять авто
    STATUSES_TO_DELETE = {"BOOKED", "DELETE", "LEASE_SALE", "SOLD", "WAIT"}
    
    # Получаем все ID из таблицы cars
    all_ids = get_all_car_ids(logger)
    if not all_ids:
        logger.info("Нет автомобилей для обработки")
        return
    
    # Получаем cookies и headers для запросов
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
    
    # Разбиваем на батчи по 20 ID
    total_processed = 0
    total_deleted = 0
    total_saved = 0

    for i in range(0, len(all_ids), READSIDE_BATCH_SIZE):
        batch_ids = all_ids[i:i + READSIDE_BATCH_SIZE]
        logger.info("Обработка батча %d-%d из %d", i + 1, min(i + len(batch_ids), len(all_ids)), len(all_ids))
        
        # Запрашиваем детальную информацию
        details_data = fetch_cars_details(session, batch_ids, logger)
        if details_data is None:

            # Пробуем обновить cookies и повторить
            if refresh_cookies(logger):
                browser_data = load_browser_data(logger)
                if browser_data:
                    cookies, headers = browser_data
                    session = create_session_with_cookies(cookies, headers)
                    details_data = fetch_cars_details(session, batch_ids, logger)
                    if details_data is None:
                        continue
                else:
                    continue
            else:
                continue

        
        # Обрабатываем каждый автомобиль
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        for car_detail in details_data:
            vehicle_id = str(car_detail.get("vehicleId", ""))
            if not vehicle_id:
                continue
            
            advertisement = car_detail.get("advertisement", {})
            status = advertisement.get("status", "")
            category = car_detail.get("category", {})
            manufacturer_english_name = category.get("manufacturerEnglishName", "")

            # Проверяем статус - если в списке для удаления, удаляем из cars
            if status in STATUSES_TO_DELETE:
                try:
                    cursor.execute("DELETE FROM cars WHERE id = ?", (vehicle_id,))
                    total_deleted += 1
                    logger.debug("Удален автомобиль %s со статусом %s", vehicle_id, status)
                except sqlite3.Error as exc:
                    logger.error("Ошибка при удалении автомобиля %s: %s", vehicle_id, exc)
                total_processed += 1
                continue

            # Проверяем бренд - если содержит "other", удаляем из cars
            if manufacturer_english_name and "other" in manufacturer_english_name.lower():
                try:
                    cursor.execute("DELETE FROM cars WHERE id = ?", (vehicle_id,))
                    total_deleted += 1
                    logger.debug("Удален автомобиль %s с брендом 'other'", vehicle_id)
                except sqlite3.Error as exc:
                    logger.error("Ошибка при удалении автомобиля %s: %s", vehicle_id, exc)
                total_processed += 1
                continue

            # Сохраняем в cars_details
            spec = car_detail.get("spec", {})

            year_month = category.get("yearMonth")
            displacement = spec.get("displacement")
            model_group_english_name = category.get("modelGroupEnglishName")
            grade_english_name = category.get("gradeEnglishName")
            grade_detail_english_name = category.get("gradeDetailEnglishName")
            color_name = spec.get("colorName")
            seat_count = spec.get("seatCount")
            vehicle_no = car_detail.get("vehicleNo")
            vin = car_detail.get("vin")

            # Получаем цену из advertisement и умножаем на 10000
            price_in_currency = advertisement.get("price")
            finish = None
            if price_in_currency is not None:
                try:
                    finish = float(price_in_currency) * 10000
                except (ValueError, TypeError):
                    finish = None

            collected_at = datetime.now().isoformat()

            try:
                cursor.execute("""
                                    INSERT OR REPLACE INTO cars_details 
                                    (vehicleId, yearMonth, displacement, manufacturerEnglishName,
                                     modelGroupEnglishName, gradeEnglishName, gradeDetailEnglishName,
                                     colorName, seatCount, vehicleNo, vin, 
                                     advertisement_status, finish, collected_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                    vehicle_id, year_month, displacement,
                    manufacturer_english_name, model_group_english_name,
                    grade_english_name, grade_detail_english_name,
                    color_name, seat_count, vehicle_no, vin,
                    status, finish, collected_at
                ))
                total_saved += 1
            except sqlite3.Error as exc:
                logger.error("Ошибка при сохранении детальной информации для автомобиля %s: %s", vehicle_id, exc)
            
            total_processed += 1
        
        conn.commit()
        conn.close()
        
        # Пауза между запросами
        time.sleep(REQUEST_PAUSE_SECONDS)
    
    duration = time.time() - start_time
    logger.info("=== Итоги обработки детальной информации: обработано %d авто, удалено: %d, сохранено в cars_details: %d за %.2f сек ===",
                total_processed, total_deleted, total_saved, duration)


def main() -> None:
    logger = setup_logging()
    logger.info("Старт парсинга авто Encar")
    # Инициализируем базу данных
    init_database(logger)
    process_cars_details(logger)
    exit()
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
            # После сбора информации обрабатываем детальную информацию
            logger.info("=" * 60)
            logger.info("Начало обработки детальной информации об автомобилях")
            logger.info("=" * 60)
            try:
                process_cars_details(logger)
            except Exception as exc:
                logger.error("Ошибка при обработке детальной информации: %s", exc, exc_info=True)
        except Exception as exc:
            logger.error("Ошибка в цикле парсинга: %s", exc, exc_info=True)
        finally:
            duration = time.time() - start_ts
            logger.info("Цикл #%d завершен за %.2f секунд", cycle_number, duration)

        if not INFINITY:
            break

        logger.info("Пауза %d секунд перед следующим циклом...", CYCLE_PAUSE)
        time.sleep(CYCLE_PAUSE)




if __name__ == "__main__":
    main()
