"""Единая конфигурация всех агентов; импорт не обращается к API."""
import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
# Поддержка существующего окружения родительского проекта.
load_dotenv(BASE_DIR.parent / '.env')


def verify_ssl():
    return os.getenv('GIGACHAT_VERIFY_SSL_CERTS', 'true').lower() not in {'0', 'false', 'no'}


class ConfigurationError(RuntimeError):
    """Ошибка настройки без раскрытия её значения."""


def validate_settings():
    key = os.getenv('GIGACHAT_CREDENTIALS', '').strip()
    if not key:
        raise ConfigurationError('Укажите GIGACHAT_CREDENTIALS в файле .env (пример: .env.example).')
    if not key.isascii() or any(char.isspace() for char in key):
        raise ConfigurationError(
            'GIGACHAT_CREDENTIALS содержит недопустимые символы. '
            'Вставьте ключ авторизации из кабинета GigaChat вместо примера; '
            'в ключе не должно быть кириллицы, пробелов или переносов строк.'
        )
    scope = os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS')
    if scope not in {'GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_CORP'}:
        raise ConfigurationError('Некорректный GIGACHAT_SCOPE. Для личного аккаунта укажите GIGACHAT_API_PERS.')
    try:
        max_tokens = int(os.getenv('GIGACHAT_MAX_TOKENS', '3000'))
    except ValueError:
        raise ConfigurationError('GIGACHAT_MAX_TOKENS должен быть целым числом от 256 до 8000.') from None
    if not 256 <= max_tokens <= 8000:
        raise ConfigurationError('GIGACHAT_MAX_TOKENS должен быть целым числом от 256 до 8000.')
    return key


@lru_cache(maxsize=8)
def create_model(temperature=0.3):
    from langchain_gigachat import GigaChat
    key = validate_settings()
    return GigaChat(credentials=key, model=os.getenv('GIGACHAT_MODEL', 'GigaChat-2'),
                    scope=os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS'),
                    verify_ssl_certs=verify_ssl(),
                    ca_bundle_file=os.getenv("GIGACHAT_CA_BUNDLE") or None,
                    temperature=temperature, timeout=60,
                    max_tokens=int(os.getenv('GIGACHAT_MAX_TOKENS', '3000')))
