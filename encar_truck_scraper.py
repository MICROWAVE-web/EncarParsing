import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

import requests

COOKIES_FILE = "encar_cookies.json"
OUTPUT_FILE = "encar_truck_results.json"
LOG_FILE = "encar_truck_scraper.log"

BASE_API_URL = "https://api.encar.com/search/car/list/premium"
START_YEAR = 2025
MIN_YEAR = 1990
PAGE_SIZE = 100
OFFSET_STEP = PAGE_SIZE
INITIAL_OFFSET = 0
REQUEST_TIMEOUT = 15
REQUEST_PAUSE_SECONDS = 1.5


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


def create_session_with_cookies(cookies: List[Dict]) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies.clear()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36', 'Referer': 'https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!%7B%22action%22%3A%22(And.Hidden.N._.CarType.N.)%22%2C%22toggle%22%3A%7B%7D%2C%22layer%22%3A%22%22%2C%22sort%22%3A%22ModifiedDate%22%2C%22page%22%3A1%2C%22limit%22%3A20%2C%22searchKey%22%3A%22%22%2C%22loginCheck%22%3Afalse%7D'})

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

    session = create_session_with_cookies(cookies)
    collected, seen_ids = load_existing_results(logger)

    for year in range(START_YEAR, MIN_YEAR - 1, -1):
        year_range = build_year_range(year)
        logger.info("=== Обработка диапазона %s ===", year_range)

        offset = INITIAL_OFFSET

        # Счетчик полностью паовторяющейся выборки
        all_repeat_count = 5
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
    start_ts = time.time()
    try:
        scrape_trucks(logger)
    finally:
        duration = time.time() - start_ts
        logger.info("Завершено за %.2f секунд", duration)


if __name__ == "__main__":
    main()
