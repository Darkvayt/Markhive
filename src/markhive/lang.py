# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Локализация интерфейса: загрузка словарей и функция перевода
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QLocale, QSettings

# Путь к папке с языковыми файлами
LANG_DIR = Path(__file__).parent / "lang"
DEFAULT_LANGUAGE = "en"

# Кеш загруженных словарей
_cache: dict[str, dict] = {}
_current_language: str | None = None


# Доступные языки: код -> отображаемое имя
def available_languages() -> dict[str, str]:
    langs: dict[str, str] = {}
    for path in sorted(LANG_DIR.glob("*.json")):
        code = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            langs[code] = data.get("language_name", code)
        except Exception:
            langs[code] = code
    return langs


# Загрузка словаря для указанного языка с кешированием
def _load(code: str) -> dict:
    if code not in _cache:
        path = LANG_DIR / f"{code}.json"
        if not path.exists():
            path = LANG_DIR / f"{DEFAULT_LANGUAGE}.json"
        try:
            _cache[code] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _cache[code] = {}
    return _cache[code]


# Текущий язык: сохранённый в настройках -> системный -> английский
def current_language() -> str:
    global _current_language
    if _current_language is None:
        settings = QSettings()
        saved = settings.value("language", None, type=str)
        if saved and (LANG_DIR / f"{saved}.json").exists():
            _current_language = saved
        else:
            sys_lang = QLocale.system().name().split("_")[0]
            if (LANG_DIR / f"{sys_lang}.json").exists():
                _current_language = sys_lang
            else:
                _current_language = DEFAULT_LANGUAGE
    return _current_language


# Сохранение выбора языка в настройках
def set_language(code: str) -> None:
    global _current_language
    if (LANG_DIR / f"{code}.json").exists():
        _current_language = code
        QSettings().setValue("language", code)


# Перевод по ключу; при отсутствии - английский, затем сам ключ
def tr(key: str, **kwargs) -> str:
    lang = current_language()
    text = _load(lang).get(key)
    if text is None and lang != DEFAULT_LANGUAGE:
        text = _load(DEFAULT_LANGUAGE).get(key)
    if text is None:
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


# Возврат списка строк по ключу (например, список возможностей)
def tr_list(key: str) -> list[str]:
    lang = current_language()
    value = _load(lang).get(key)
    if value is None and lang != DEFAULT_LANGUAGE:
        value = _load(DEFAULT_LANGUAGE).get(key)
    return value if isinstance(value, list) else []
