# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Модели данных (закладка, папка) и вспомогательные функции
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import urlsplit

# Глобальный счётчик для генерации уникальных UID закладок
_UID_COUNTER = itertools.count(1)

# Регулярное выражение для извлечения домена из URL без схемы
_DOMAIN_FALLBACK_RE = re.compile(r"^(?:[a-zA-Z][\w+.-]*://)?([^/:?#]+)")


@dataclass(slots=True)
class Bookmark:
    # Одна закладка браузера
    url: str
    title: str = ""
    add_date: str = ""
    parent: Folder | None = None
    # Уникальный идентификатор для идентификации при перетаскивании
    uid: int = field(
        default_factory=lambda: next(_UID_COUNTER), compare=False, repr=False
    )
    # Статус проверки: "" | "online" | "offline" | "redirect"
    status: str = ""
    # Кешированные нормализованные ключи для поиска дубликатов
    _norm_url: str = field(default="", compare=False, repr=False, init=False)
    _norm_title: str = field(default="", compare=False, repr=False, init=False)
    # Кешированные ключи для поиска по подстроке
    _search_title: str = field(default="", compare=False, repr=False, init=False)
    _search_url: str = field(default="", compare=False, repr=False, init=False)

    def __post_init__(self):
        self._invalidate_norm()

    # Пересчитать нормализованные ключи при изменении
    def _invalidate_norm(self):
        self._norm_url = self.url.strip()
        self._norm_title = (self.title or self.url).casefold()
        self._search_title = (self.title or "").casefold()
        self._search_url = self.url.casefold()


@dataclass(slots=True)
class Folder:
    # Группа закладок с поддержкой вложенности
    name: str
    parent: Folder | None = None
    folders: list[Folder] = field(default_factory=list)
    bookmarks: list[Bookmark] = field(default_factory=list)


# --- Обход дерева ---


# Рекурсивный обход всех папок в глубину
def iter_folders(folders: list[Folder]):
    for folder in folders:
        yield folder
        yield from iter_folders(folder.folders)


# Все закладки коллекции: корневые и вложенные в папки
def iter_bookmarks(folders: list[Folder], root_bookmarks: list[Bookmark]):
    yield from root_bookmarks
    for folder in iter_folders(folders):
        yield from folder.bookmarks


# Число закладок в папке с учётом вложенных
def count_bookmarks(folder: Folder) -> int:
    return sum(1 for _ in iter_bookmarks(folder.folders, folder.bookmarks))


# Восстановление ссылок parent после слияния коллекций
def fix_parents(
    folders: list[Folder], bookmarks: list[Bookmark], parent: Folder | None = None
) -> None:
    for folder in folders:
        folder.parent = parent
        fix_parents(folder.folders, folder.bookmarks, folder)
    for bookmark in bookmarks:
        bookmark.parent = parent


# --- Утилиты ---


# Извлечение домена из адреса
def extract_domain(url: str) -> str:
    url = (url or "").strip()
    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        netloc = ""
    # Если стандартный парсинг не дал результата - используем регулярное
    if not netloc:
        match = _DOMAIN_FALLBACK_RE.match(url)
        netloc = match.group(1) if match else ""
    # Убираем логин и порт из адреса
    netloc = netloc.split("@")[-1].split(":")[0].lower()
    return netloc.removeprefix("www.")


@lru_cache(maxsize=4096)
# Преобразование даты добавления в формат "ГГГГ-ММ-ДД"
# Поддерживает секунды, миллисекунды и микросекунды с 1601 года
def format_add_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw.isdigit() or int(raw) == 0:
        return ""
    value = int(raw)
    try:
        # Chrome: микросекунды с 1601 года
        if value >= 10**15:
            dt = datetime(1601, 1, 1) + timedelta(microseconds=value)
        # Миллисекунды
        elif value >= 10**12:
            dt = datetime.fromtimestamp(value / 1000)
        # Секунды
        else:
            dt = datetime.fromtimestamp(value)
        return dt.strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


# Полный путь группы от корня: "Родитель / Дочерняя / ..."
def folder_path(folder: Folder | None, sep: str = " / ") -> str:
    if folder is None:
        return ""
    parts = []
    node = folder
    while node is not None:
        parts.append(node.name)
        node = node.parent
    return sep.join(reversed(parts))
