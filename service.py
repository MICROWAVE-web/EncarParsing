import json
import logging
import os
import sys
from typing import Optional, Tuple, List, Dict

import requests
from decouple import config

COOKIES_FILE = config("COOKIES_FILE", default="encar_cookies.json")
LOG_FILE = config("LOG_FILE", default="encar_cars_scraper.log")
PROXY = config("PROXY", default=None)

# Настройки микросервиса логирования
NOTIFICATION_SERVICE_NAME = config("NOTIFICATION_SERVICE_NAME", default="EncarParsing")
NOTIFICATION_API_BASE = config("NOTIFICATION_API_BASE")
NOTIFICATION_TIMEOUT = config("NOTIFICATION_TIMEOUT", default=5, cast=int)


def parse_proxy(proxy_string: Optional[str]) -> Optional[Dict[str, str]]:
    """
    Парсит прокси из формата user:password@ip:port
    Возвращает словарь с прокси для requests или None
    """
    if not proxy_string:
        return None
    
    try:
        # Проверяем, есть ли аутентификация
        if '@' in proxy_string:
            # Формат: user:password@ip:port
            auth_part, server_part = proxy_string.rsplit('@', 1)
            if ':' in auth_part:
                username, password = auth_part.split(':', 1)
            else:
                username, password = auth_part, ''
            
            if ':' in server_part:
                host, port = server_part.split(':', 1)
            else:
                host, port = server_part, '8080'
            
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        else:
            # Формат: ip:port (без аутентификации)
            if ':' in proxy_string:
                host, port = proxy_string.split(':', 1)
            else:
                host, port = proxy_string, '8080'
            proxy_url = f"http://{host}:{port}"
        
        return {
            'http': proxy_url,
            'https': proxy_url
        }
    except Exception as e:
        logging.warning(f"Ошибка при парсинге прокси '{proxy_string}': {e}")
        return None


def get_proxy_config() -> Optional[Dict[str, str]]:
    """Возвращает конфигурацию прокси из переменной окружения"""
    return parse_proxy(PROXY)


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

    # Настраиваем прокси, если указан
    proxy_config = get_proxy_config()
    if proxy_config and PROXY:
        session.proxies.update(proxy_config)
        proxy_display = PROXY.split('@')[-1] if '@' in PROXY else PROXY
        logging.getLogger("encar_cars_scraper").info(f"Используется прокси: {proxy_display}")

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
