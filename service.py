import json
import logging
import os
import sys
from typing import Optional, Tuple, List, Dict

import requests
from decouple import config

COOKIES_FILE = config("COOKIES_FILE", default="encar_cookies.json")
LOG_FILE = config("LOG_FILE", default="encar_cars_scraper.log")

# Настройки микросервиса логирования
NOTIFICATION_SERVICE_NAME = config("NOTIFICATION_SERVICE_NAME", default="EncarParsing")
NOTIFICATION_API_BASE = config("NOTIFICATION_API_BASE")
NOTIFICATION_TIMEOUT = config("NOTIFICATION_TIMEOUT", default=5, cast=int)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("encar_cars_scraper")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    # logger.addHandler(console_handler)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Handler для отправки ERROR сообщений на микросервис
    notification_handler = ErrorNotificationHandler()
    notification_handler.setLevel(logging.ERROR)
    notification_handler.setFormatter(formatter)
    logger.addHandler(notification_handler)

    logger.debug("Логирование инициализировано")
    return logger


def load_browser_data(logger: logging.Logger) -> Optional[Tuple[List[Dict], Dict]]:
    """Загружает cookies и headers из файла"""
    if not os.path.exists(COOKIES_FILE):
        logger.warning("Файл с куками не найден: %s", COOKIES_FILE)
        return None

    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Не удалось загрузить данные из %s: %s", COOKIES_FILE, exc)
        return None

    cookies = data.get("cookies")
    headers = data.get("headers")

    if not cookies:
        logger.error("В файле %s отсутствуют куки", COOKIES_FILE)
        return None
    if not headers:
        logger.error("В файле %s отсутствуют headers", COOKIES_FILE)
        return None

    logger.info("Используем сохраненные данные (cookies: %d шт.)", len(cookies))
    return cookies, headers


def create_session_with_cookies(cookies: List[Dict], headers: Dict) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies.clear()
    session.headers.update(headers)

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


def send_error_notification(content: str, topic: Optional[str] = None) -> None:
    """Отправляет ERROR уведомление на микросервис логирования"""
    try:
        payload = {
            "service": NOTIFICATION_SERVICE_NAME,
            "content": content
        }

        if topic:
            payload["topic"] = topic

        response = requests.post(
            f"{NOTIFICATION_API_BASE}/error/",
            json=payload,
            timeout=NOTIFICATION_TIMEOUT
        )
        # Не логируем ошибки отправки, чтобы избежать рекурсии
        if response.status_code != 200:
            pass
    except Exception:
        # Игнорируем ошибки отправки, чтобы не нарушать основной процесс
        pass


class ErrorNotificationHandler(logging.Handler):
    """Кастомный handler для отправки ERROR сообщений на микросервис"""

    def emit(self, record: logging.LogRecord) -> None:
        """Отправляет ERROR сообщения на микросервис"""
        if record.levelno >= logging.ERROR:
            try:
                # Форматируем сообщение
                message = self.format(record)
                # Отправляем на микросервис
                send_error_notification(message)
            except Exception:
                # Игнорируем ошибки, чтобы не нарушать основной процесс логирования
                self.handleError(record)
