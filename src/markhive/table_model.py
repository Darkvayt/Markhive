# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Модель таблицы закладок
# Колонки: 0 Название | 1 Адрес | 2 Статус | 3 Дата | 4 Группа (Папка)
from __future__ import annotations

from typing import override

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from markhive.lang import tr
from markhive.models import Bookmark, folder_path, format_add_date

# Ключи перевода для заголовков колонок
COLUMNS = (
    "table.column.name",
    "table.column.url",
    "table.column.status",
    "table.column.date",
    "table.column.group",
)

_INVALID_INDEX = QModelIndex()

# Подписи и цвета статусов проверки
STATUS_KEY = {
    "online": "status.online",
    "offline": "status.offline",
    "redirect": "status.redirect",
}
STATUS_COLOR = {
    "online": QColor("#2f7ff7"),
    "offline": QColor("#5a6270"),
    "redirect": QColor("#d6dae2"),
}

# Кеш переводов (строится один раз при инициализации)
_status_label_cache: dict[str, str] = {}
_header_cache: list[str] = []


# Перестроение кеша переводов
def _rebuild_translation_cache() -> None:
    global _header_cache
    _status_label_cache.clear()
    for key in STATUS_KEY.values():
        _status_label_cache[key] = tr(key)
    _status_label_cache["status.not_checked"] = tr("status.not_checked")
    _status_label_cache["table.tooltip.no_group"] = tr("table.tooltip.no_group")
    _header_cache = [tr(col) for col in COLUMNS]


class BookmarkTableModel(QAbstractTableModel):
    # Табличная модель закладок

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[Bookmark] = []  # Видимые строки
        self._muted: set[int] = set()  # UID приглушённых строк
        self._dup_uids: set[int] = set()  # UID дублирующих закладок
        _rebuild_translation_cache()

    # Полная замена видимых строк (поиск или смена группы)
    def set_rows(self, rows: list[Bookmark]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    # Приглушение строк вне набора UID (режим перемещения)
    def set_muted_uids(self, uids) -> None:
        self._muted = set(uids) if uids else set()
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
            )

    # Установка множества UID дублирующих закладок
    def set_dup_uids(self, uids: set[int]) -> None:
        if uids == self._dup_uids:
            return
        self._dup_uids = uids
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, self.columnCount() - 1),
            )

    @override
    def rowCount(self, parent: QModelIndex = _INVALID_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    @override
    def columnCount(self, parent: QModelIndex = _INVALID_INDEX) -> int:
        return len(COLUMNS)

    # Данные ячейки: текст, подсказка, цвет
    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        bookmark = self._rows[index.row()]
        col = index.column()

        # Текст ячейки
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return bookmark.title or bookmark.url
            if col == 1:
                return bookmark.url
            if col == 2:
                key = STATUS_KEY.get(bookmark.status)
                return _status_label_cache.get(key, "") if key else ""
            if col == 3:
                return format_add_date(bookmark.add_date)
            return folder_path(bookmark.parent) if bookmark.parent else ""

        # Подсказки ячеек
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 0:
                return bookmark.title or bookmark.url
            if col == 2:
                key = STATUS_KEY.get(bookmark.status)
                return (
                    _status_label_cache.get(key, "")
                    if key
                    else _status_label_cache.get("status.not_checked", "")
                )
            if col == 3:
                return format_add_date(bookmark.add_date)
            if col == 4:
                if bookmark.parent:
                    return folder_path(bookmark.parent)
                return _status_label_cache.get("table.tooltip.no_group", "")
            return bookmark.url

        # Цвет текста ячейки
        if role == Qt.ItemDataRole.ForegroundRole:
            # Приглушённые строки в режиме перемещения
            if self._muted and bookmark.uid not in self._muted:
                return QColor("#5a6270")
            # Подсветка дубликатов жёлтым
            if col in (0, 1) and bookmark.uid in self._dup_uids:
                return QColor("#ffd84d")
            # Цвет статуса проверки
            if col == 2 and bookmark.status in STATUS_COLOR:
                return STATUS_COLOR[bookmark.status]
            return None

    # Заголовки колонок
    @override
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return _header_cache[section]
        return None

    # Закладка по номеру строки
    def bookmark_at(self, row: int) -> Bookmark:
        return self._rows[row]
