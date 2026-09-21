# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Главное окно: файлы, редактирование, инструменты, проверка адресов,
# перемещение закладок, блокировка неактивных действий
from __future__ import annotations

import contextlib
import time
from collections import deque
from html import escape as html_escape
from pathlib import Path
from typing import override

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTreeWidgetItem,
)

from markhive import APP_NAME, bookmark_io, lang
from markhive import version as version
from markhive.dialogs import BookmarkDialog
from markhive.lang import tr
from markhive.models import (
    Bookmark,
    Folder,
    count_bookmarks,
    extract_domain,
    fix_parents,
    folder_path,
    iter_bookmarks,
    iter_folders,
)
from markhive.table_model import BookmarkTableModel
from markhive.theme import ICONS_DIR
from markhive.ui_window import MainWindowUI

# --- Проверка адресов: параметры ---

# Максимальное число одновременных сетевых запросов
MAX_THREAD_COUNT = 32

# Таймаут ожидания ответа в секундах
TIME_RESPONSE = 10

# Соответствие кнопок диалогов ключам перевода
_BUTTON_KEY = {
    QMessageBox.StandardButton.Ok: "common.ok",
    QMessageBox.StandardButton.Cancel: "common.cancel",
    QMessageBox.StandardButton.Yes: "common.yes",
    QMessageBox.StandardButton.No: "common.no",
    QMessageBox.StandardButton.Save: "common.save",
    QMessageBox.StandardButton.Discard: "common.discard",
}


# Локализация кнопок диалога
def _localize_message_buttons(box: QMessageBox) -> None:
    for button, key in _BUTTON_KEY.items():
        btn = box.button(button)
        if btn is not None:
            btn.setText(tr(key))


# --- Главное окно ---


class MainWindow(QMainWindow, MainWindowUI):
    def __init__(self):
        super().__init__()

        # Данные коллекции
        self.root_folders: list[Folder] = []  # Корневые папки
        self.root_bookmarks: list[Bookmark] = []  # Закладки без папки
        self.source_path: Path | None = None  # Путь к открытому файлу
        self.modified = False  # Есть несохранённые изменения
        self._scope_token: object = "all"  # Текущая область видимости
        self._move_uids: set[int] = set()  # UID в режиме перемещения
        self._cut_folders: list[Folder] = []  # Вырезанные папки
        self._sort_bookmarks_desc = False  # Направление сортировки
        self._sort_groups_desc = False
        self._syncing = False  # Флаг синхронизации выделения

        # Кеш производительности
        self._scope_cache: list[Bookmark] | None = None
        self._probe_rows: dict[int, int] = {}
        self._probe_rows_valid = False

        # Кеш для блокировки неактивных действий
        self._stat_total = 0  # Общее число закладок
        self._stat_folders = 0  # Общее число папок
        self._tree_folder_sel_count = 0  # Выбрано папок в дереве
        self._table_selected_count = 0  # Выбрано закладок в таблице
        self._stat_dup = 0  # Общее число дубликатов
        self._stat_dup_url = 0  # Дубликаты по адресу
        self._stat_dup_title = 0  # Дубликаты по названию
        self._status_counts: dict[str, int] = {
            "online": 0,
            "redirect": 0,
            "offline": 0,
        }
        self._status_counts_valid = False

        # Проверка адресов: сетевой менеджер и состояние
        self._probe_net = QNetworkAccessManager(self)
        self._probe_net.finished.connect(self._on_reply_finished)
        self._active_replies: set[QNetworkReply] = set()
        self._probe_dispatching = False
        self._probe_map: dict[int, Bookmark] = {}
        self._probe_uids: set[int] = set()
        self._probe_total = 0
        self._probe_done = 0
        self._probe_stats: dict[str, int] = {}
        self._probe_active = False
        self._probe_paused = False
        self._probe_queue: deque[Bookmark] = deque()
        self._probe_running = 0

        # Таймер мигания индикатора проверки
        self._blink_on = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(300)
        self._blink_timer.timeout.connect(self._blink_toggle)

        # Модель таблицы
        self.model = BookmarkTableModel(self)
        self.model.modelReset.connect(self._invalidate_probe_rows)

        # Таймер задержки фильтрации поиска
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filter)

        # Таймер автоскрытия сообщений статус-бара
        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(lambda: self.lbl_message.clear())

        # Сборка интерфейса
        self._build_widgets()
        self._build_menus()
        self._build_statusbar()
        self._connect_signals()

        # Горячая клавиша выхода из режимов
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._escape_pressed)

        self.resize(1280, 800)
        self.rebuild_tree()
        self._refresh()

    # Показ модального диалога с локализованными кнопками
    def _message_box(self, icon, title: str, text: str, buttons, default=None) -> int:
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default is not None:
            box.setDefaultButton(default)
        _localize_message_buttons(box)
        return box.exec()

    # Смена языка интерфейса с уведомлением о перезапуске
    def _set_language(self, code: str) -> None:
        if lang.current_language() == code:
            return
        lang.set_language(code)
        self._message_box(
            QMessageBox.Icon.Information,
            tr("common.info"),
            tr("dialog.language.restart"),
            QMessageBox.StandardButton.Ok,
        )

    # Подключение всех сигналов интерфейса
    def _connect_signals(self):
        self.search_edit.textChanged.connect(lambda _t: self._filter_timer.start())
        self.search_clear.clicked.connect(self.search_edit.clear)
        self.btn_probe.customContextMenuRequested.connect(self._show_probe_menu)
        self.tree_top.itemSelectionChanged.connect(self._on_top_selection)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree_top.customContextMenuRequested.connect(self._show_top_context_menu)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree_top.bookmarks_dropped.connect(self._on_bookmarks_dropped)
        self.tree.bookmarks_dropped.connect(self._on_bookmarks_dropped)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.doubleClicked.connect(lambda _i: self.edit_bookmark())
        self.table.selectionModel().selectionChanged.connect(
            lambda *_a: self._update_selected_counter()
        )
        self.tree.empty_area_clicked.connect(self._clear_tree_selections)
        self.tree_top.empty_area_clicked.connect(self._clear_tree_selections)
        self.tree.itemExpanded.connect(lambda it: self._on_expand_changed(it, True))
        self.tree.itemCollapsed.connect(lambda it: self._on_expand_changed(it, False))

    # --- Дерево папок ---

    # Рекурсивный обход всех элементов дерева
    def _iter_tree_items(self, tree=None):
        tree = tree if tree is not None else self.tree

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                yield child
                yield from walk(child)

        yield from walk(tree.invisibleRootItem())

    # Все элементы обоих деревьев
    def _all_tree_items(self):
        yield from self._iter_tree_items(self.tree_top)
        yield from self._iter_tree_items(self.tree)

    # Создание элемента дерева для папки с вложенными
    def _make_folder_item(self, folder: Folder) -> QTreeWidgetItem:
        item = QTreeWidgetItem([folder.name])
        item.setData(0, Qt.ItemDataRole.UserRole, folder)
        item.setToolTip(0, folder.name)
        item.setIcon(0, self._folder_icon(False))
        # Визуальное оформление вырезанных папок
        if any(folder is cut for cut in self._cut_folders):
            font = item.font(0)
            font.setStrikeOut(True)
            item.setFont(0, font)
            item.setForeground(0, QColor("#667085"))
        for child in folder.folders:
            item.addChild(self._make_folder_item(child))
        return item

    # Поиск элемента дерева по токену (папка или строка)
    def _find_item(self, token):
        for item in self._all_tree_items():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data is token or (isinstance(token, str) and data == token):
                return item
        return None

    # Полная перестройка дерева папок с сохранением состояния
    def rebuild_tree(self):
        # Сохраняем развёрнутые и выбранные элементы
        expanded_ids = set()
        prev_tokens = []
        for it in self._all_tree_items():
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if it.isExpanded() and isinstance(data, Folder):
                expanded_ids.add(id(data))
            if it.isSelected():
                prev_tokens.append(data)

        self.tree_top.clear()
        self.tree.clear()

        # Закреплённая зона: "Все закладки" и "Без категории"
        all_item = QTreeWidgetItem([tr("tree.all_bookmarks")])
        all_item.setData(0, Qt.ItemDataRole.UserRole, "all")
        all_item.setToolTip(0, tr("tree.all_bookmarks"))
        all_item.setIcon(0, QIcon(str(ICONS_DIR / "all_bookmarks.ico")))

        none_item = QTreeWidgetItem([tr("tree.no_group")])
        none_item.setData(0, Qt.ItemDataRole.UserRole, "none")
        none_item.setToolTip(0, tr("tree.no_group"))
        none_item.setIcon(0, QIcon(str(ICONS_DIR / "uncategorized.ico")))

        self.tree_top.addTopLevelItems([all_item, none_item])

        # Прокручиваемое дерево папок
        for folder in self.root_folders:
            self.tree.addTopLevelItem(self._make_folder_item(folder))

        # Восстановление развёрнутости
        for item in self._iter_tree_items(self.tree):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, Folder) and id(data) in expanded_ids:
                item.setExpanded(True)

        # Восстановление выделения
        tokens = [t for t in prev_tokens if self._find_item(t) is not None] or ["all"]
        self._syncing = True
        self.tree_top.clearSelection()
        self.tree.clearSelection()
        for token in tokens:
            item = self._find_item(token)
            if item:
                item.setSelected(True)
        self._syncing = False

        # Прокрутка к текущему элементу
        target = self._find_item(tokens[0]) or all_item
        owner = target.treeWidget()
        owner.setCurrentItem(target)
        owner.scrollToItem(target)

        # Фиксация высоты закреплённой зоны
        self.tree_top.setFixedHeight(
            self.tree_top.sizeHintForRow(0) * self.tree_top.topLevelItemCount()
            + self.tree_top.frameWidth() * 2
        )
        self.tree.doItemsLayout()
        self.tree.viewport().update()
        self._handle_selection_changed()

    # Иконка папки: открытая или закрытая
    def _folder_icon(self, expanded: bool):
        name = "folder_open.ico" if expanded else "folder_closed.ico"
        return QIcon(str(ICONS_DIR / name))

    # Смена иконки папки при разворачивании и сворачивании
    def _on_expand_changed(self, item: QTreeWidgetItem, expanded: bool) -> None:
        if not isinstance(item.data(0, Qt.ItemDataRole.UserRole), Folder):
            return
        if item.childCount() == 0:
            item.setIcon(0, self._folder_icon(False))
            return
        item.setIcon(0, self._folder_icon(expanded))

    # --- Выделение ---

    # Выделение в закреплённой зоне снимает выделение в дереве
    def _on_top_selection(self):
        if self._syncing:
            return
        self._syncing = True
        self.tree.clearSelection()
        self._syncing = False
        self._handle_selection_changed()

    # Выделение в дереве снимает выделение в закреплённой зоне
    def _on_tree_selection(self):
        if self._syncing:
            return
        self._syncing = True
        self.tree_top.clearSelection()
        self._syncing = False
        self._handle_selection_changed()

    # Снятие выделения со всех деревьев
    def _clear_tree_selections(self):
        if self._syncing:
            return
        self._syncing = True
        self.tree_top.clearSelection()
        self.tree.clearSelection()
        self._syncing = False
        self._handle_selection_changed()

    # Токены всех выбранных элементов дерева
    def _selected_tokens(self):
        return [
            it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree_top.selectedItems()
        ] + [it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()]

    # Обработка изменения выделения: определение области видимости
    def _handle_selection_changed(self):
        self._tree_folder_sel_count = sum(
            1
            for it in self.tree.selectedItems()
            if isinstance(it.data(0, Qt.ItemDataRole.UserRole), Folder)
        )
        top_sel = self.tree_top.selectedItems()
        tree_sel = self.tree.selectedItems()

        # Определение основного токена области видимости
        if not top_sel and not tree_sel:
            token = "all"
        elif top_sel:
            token = top_sel[0].data(0, Qt.ItemDataRole.UserRole)
        else:
            item = self.tree.currentItem()
            if item is None or item not in tree_sel:
                item = tree_sel[0]
            token = item.data(0, Qt.ItemDataRole.UserRole)

        if not isinstance(token, Folder) and token not in ("all", "none"):
            token = "all"

        self._scope_token = token
        self._scope_cache = None
        self._update_folder_selected_counter()
        self._apply_filter()

    # --- Фильтрация и счётчики ---

    # Закладки текущей области видимости с кешированием
    def _scope_bookmarks(self) -> list[Bookmark]:
        if self._scope_cache is not None:
            return self._scope_cache

        tokens = self._selected_tokens()
        if not tokens:
            self._scope_cache = []
            return self._scope_cache

        if "all" in tokens:
            result = list(iter_bookmarks(self.root_folders, self.root_bookmarks))
        else:
            folders = [t for t in tokens if isinstance(t, Folder)]
            result = []
            for data in tokens:
                if data == "none":
                    result.extend(self.root_bookmarks)
                elif isinstance(data, Folder):
                    # Пропускаем вложенные папки, если родитель уже выбран
                    if any(
                        data is not other and self._is_within(data, other)
                        for other in folders
                    ):
                        continue
                    result.extend(iter_bookmarks(data.folders, data.bookmarks))

        self._scope_cache = result
        return self._scope_cache

    # Применение фильтра поиска к текущей области видимости
    def _apply_filter(self):
        scope = self._scope_bookmarks()
        self.lbl_scope.setText(f"{len(scope)}")

        query = self.search_edit.text().strip().casefold()
        if query:
            scope = [
                b for b in scope if query in b._search_title or query in b._search_url
            ]

        self.search_counter.setText(tr("search.counter", count=len(scope)))
        self.model.set_rows(scope)
        self._update_selected_counter()
        self._fit_folder_column(scope)

    # Подбор ширины колонки папки по содержимому
    def _fit_folder_column(self, rows):
        try:
            # Ограничиваем выборку для производительности
            sample = rows[:200] if len(rows) > 200 else rows
            names = {folder_path(b.parent) for b in sample if b.parent}
            if not names:
                return
            metrics = self.table.fontMetrics()
            width = max(metrics.horizontalAdvance(n) for n in names) + 24
            self.table.setColumnWidth(4, min(max(width, 120), 420))
        except Exception:
            pass

    # Обновление счётчиков общего состояния коллекции
    def _update_status_counters(self):
        total = 0
        unique_urls: set[str] = set()
        unique_titles: set[str] = set()
        dup_uids: set[int] = set()

        for bookmark in iter_bookmarks(self.root_folders, self.root_bookmarks):
            total += 1
            url = bookmark._norm_url
            title = bookmark._norm_title
            if url in unique_urls or title in unique_titles:
                dup_uids.add(bookmark.uid)
            unique_urls.add(url)
            unique_titles.add(title)

        self._stat_total = total
        self._stat_folders = sum(1 for _ in iter_folders(self.root_folders))

        dup_url = total - len(unique_urls)
        dup_title = total - len(unique_titles)
        self._stat_dup_url = dup_url
        self._stat_dup_title = dup_title
        self._stat_dup = dup_url + dup_title

        self.model.set_dup_uids(dup_uids)
        self.lbl_total.setText(str(total))
        self.lbl_folders.setText(str(self._stat_folders))
        self.lbl_dup_url.setText(f"{dup_url}")
        self.lbl_dup_url.setStyleSheet("color:#ffd84d;" if dup_url > 0 else "")
        self.lbl_dup_title.setText(f"{dup_title}")
        self.lbl_dup_title.setStyleSheet("color:#ffd84d;" if dup_title > 0 else "")

    # Обновление счётчика выбранных закладок в таблице
    def _update_selected_counter(self):
        rows = {i.row() for i in self.table.selectionModel().selectedIndexes()}
        self._table_selected_count = len(rows)
        if rows:
            self.lbl_sel.setText(f"SB: {len(rows)}")
            self.lbl_sel.show()
        else:
            self.lbl_sel.hide()
        self._update_actions_state()

    # Обновление счётчика выбранных папок в дереве
    def _update_folder_selected_counter(self):
        n = self._tree_folder_sel_count
        if n:
            self.lbl_sel_folders.setText(f"SG: {n}")
            self.lbl_sel_folders.show()
        else:
            self.lbl_sel_folders.hide()

    # Блокировка неактивных действий меню
    def _update_actions_state(self):
        has_folders = self._stat_folders > 0
        has_bookmarks = self._stat_total > 0
        visible = self.model.rowCount() > 0
        folder_sel = self._tree_folder_sel_count > 0
        single_folder = self._tree_folder_sel_count == 1
        bm_sel = self._table_selected_count
        cut_busy = bool(self._cut_folders)

        self.act_save.setEnabled(
            self.source_path is not None
            and self.source_path.is_file()
            and self.modified
        )
        self.act_save_as.setEnabled(has_folders or has_bookmarks)
        self.act_expand_all.setEnabled(has_folders)
        self.act_collapse_all.setEnabled(has_folders)
        self.act_sort_groups.setEnabled(self._stat_folders >= 2)
        self.act_ungroup_singles.setEnabled(has_folders)
        self.act_ungroup_all.setEnabled(has_folders)
        self.act_remove_empty.setEnabled(has_folders)
        self.act_sort_bookmarks.setEnabled(self.model.rowCount() >= 2)
        self.act_auto_group.setEnabled(visible)
        self.act_dedup_urls.setEnabled(self._stat_dup_url > 0)
        self.act_dedup_titles.setEnabled(self._stat_dup_title > 0)
        self.act_group_duplicates.setEnabled(self._stat_dup > 0)
        self.act_check_urls.setEnabled(visible and not self._probe_active)
        self.act_new_subgroup.setEnabled(single_folder)
        self.act_rename_group.setEnabled(single_folder)
        self.act_delete_group.setEnabled(folder_sel)
        self.act_cut_groups.setEnabled(folder_sel and not cut_busy)
        self.act_paste_groups.setEnabled(cut_busy)
        self.act_edit_bookmark.setEnabled(bm_sel == 1)
        self.act_delete_bookmarks.setEnabled(bm_sel > 0)

    # Показ временного сообщения в статус-баре
    def _status_message(self, text: str, timeout: int = 4000) -> None:
        self.lbl_message.setText(text)
        if timeout > 0:
            self._msg_timer.start(timeout)
        else:
            self._msg_timer.stop()

    # Обновление заголовка окна с индикатором изменений
    def _update_title(self):
        self.setWindowTitle(f"{APP_NAME} v{version}")
        star = ' <span style="color:#2f7ff7;">●</span>' if self.modified else ""

        if self.source_path is None:
            has_data = bool(self.root_folders) or bool(self.root_bookmarks)
            if has_data or self.modified:
                name = tr("statusbar.needs_save")
                tooltip = tr("statusbar.needs_save")
            else:
                name = f'<span style="color:#667085;">{tr("statusbar.file_not_loaded")}</span>'
                tooltip = tr("statusbar.file_not_loaded")
            self.lbl_file_name.setText(
                f'<span style="color:#ffffff;">{name}</span>{star}'
            )
            self.lbl_file_name.setToolTip(tooltip)
        else:
            name = html_escape(self.source_path.name)
            self.lbl_file_name.setText(
                f'<span style="color:#ffffff;">{name}</span>{star}'
            )
            tooltip = tr("statusbar.file_tooltip.needs_save") if self.modified else ""
            self.lbl_file_name.setToolTip(f"{tooltip}{self.source_path}")

    # Полное обновление интерфейса после изменения данных
    def _refresh(self):
        self._scope_cache = None
        self._apply_filter()
        self._update_status_counters()
        self._update_title()
        self._update_actions_state()

    # Обработка нажатия клавиши выхода
    def _escape_pressed(self):
        if self._move_uids:
            self._cancel_move_mode()
        elif self._cut_folders:
            self._cut_folders = []
            self.rebuild_tree()
            self._refresh()
            self._status_message(tr("status.cut_cancelled"), 3000)

    # Выбранные закладки в таблице
    def _selected_bookmarks(self) -> list[Bookmark]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedIndexes()})
        return [self.model.bookmark_at(r) for r in rows]

    # Целевая папка текущей области видимости
    def _target_folder(self) -> Folder | None:
        return self._scope_token if isinstance(self._scope_token, Folder) else None

    # Проверка вложенности узла в предка
    @staticmethod
    def _is_within(node: Folder | None, ancestor: Folder) -> bool:
        while node is not None:
            if node is ancestor:
                return True
            node = node.parent
        return False

    # --- Файл ---

    # Подтверждение сохранения несохранённых изменений
    def _confirm_discard(self) -> bool:
        if not self.modified:
            return True
        reply = self._message_box(
            QMessageBox.Icon.Warning,
            tr("dialog.unsaved.title"),
            tr("dialog.unsaved.text"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            return bool(self.save_as())
        return reply == QMessageBox.StandardButton.Discard

    # Открытие одного файла экспорта закладок
    def open_file(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("dialog.open_file.title"),
            "",
            tr("dialog.open_file.filter"),
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            folders, bookmarks = bookmark_io.load_bookmarks(Path(path))
        except OSError as error:
            self._message_box(
                QMessageBox.Icon.Critical,
                tr("common.error"),
                tr("dialog.open_file.error", error=error),
                QMessageBox.StandardButton.Ok,
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        # Сброс состояния проверки и перемещения
        self._probe_cancel(silent=True)
        if self._move_uids:
            self._cancel_move_mode()

        self.root_folders, self.root_bookmarks = folders, bookmarks
        self.source_path = Path(path)
        self.modified = False
        self._scope_token = "all"
        self._cut_folders = []
        self.rebuild_tree()
        self._refresh()

    # Открытие папки с несколькими файлами и слияние
    def open_folder(self):
        if not self._confirm_discard():
            return
        directory = QFileDialog.getExistingDirectory(
            self, tr("dialog.open_folder.title")
        )
        if not directory:
            return

        files = sorted(Path(directory).glob("*.html")) + sorted(
            Path(directory).glob("*.htm")
        )
        if not files:
            self._message_box(
                QMessageBox.Icon.Warning,
                tr("common.warning"),
                tr("dialog.open_folder.no_files"),
                QMessageBox.StandardButton.Ok,
            )
            return

        folders: list[Folder] = []
        bookmarks: list[Bookmark] = []
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            for file in files:
                f, b = bookmark_io.load_bookmarks(file)
                bookmark_io.merge_collections(folders, bookmarks, f, b)
            fix_parents(folders, bookmarks)
        except OSError as error:
            self._message_box(
                QMessageBox.Icon.Critical,
                tr("common.error"),
                tr("dialog.open_folder.error", error=error),
                QMessageBox.StandardButton.Ok,
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._probe_cancel(silent=True)
        if self._move_uids:
            self._cancel_move_mode()

        self.root_folders, self.root_bookmarks = folders, bookmarks
        self.source_path = Path(directory)
        self.modified = False
        self._scope_token = "all"
        self._cut_folders = []
        self.rebuild_tree()
        self._refresh()
        self._status_message(tr("status.merge.files", count=len(files)), 5000)

    # Сохранение в текущий файл
    def save_current(self):
        if (
            self.source_path is None
            or not self.source_path.is_file()
            or not self.modified
        ):
            return
        try:
            bookmark_io.save_bookmarks(
                self.source_path, self.root_folders, self.root_bookmarks
            )
        except OSError as error:
            self._message_box(
                QMessageBox.Icon.Critical,
                tr("common.error"),
                tr("dialog.save.error", error=error),
                QMessageBox.StandardButton.Ok,
            )
            return
        self.modified = False
        self._update_title()
        self._update_actions_state()
        self._status_message(tr("status.saved", path=self.source_path), 5000)

    # Сохранение в новый файл
    def save_as(self):
        if self.source_path:
            src = Path(self.source_path)
            suggested = str(src if src.is_file() else src / "bookmarks.html")
        else:
            suggested = "bookmarks.html"
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("dialog.save.title"),
            suggested,
            tr("dialog.save.filter"),
        )
        if not path:
            return False
        try:
            bookmark_io.save_bookmarks(
                Path(path), self.root_folders, self.root_bookmarks
            )
        except OSError as error:
            self._message_box(
                QMessageBox.Icon.Critical,
                tr("common.error"),
                tr("dialog.save.error", error=error),
                QMessageBox.StandardButton.Ok,
            )
            return False
        self.source_path = Path(path)
        self.modified = False
        self._update_title()
        self._update_actions_state()
        self._status_message(tr("status.saved", path=path), 5000)
        return True

    # Закрытие окна с проверкой несохранённых изменений
    @override
    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        self._probe_cancel(silent=True)
        with contextlib.suppress(RuntimeError, TypeError):
            if hasattr(self._probe_net, "clearAccessCache"):
                self._probe_net.clearAccessCache()
            self._probe_net.finished.disconnect(self._on_reply_finished)
        event.accept()

    # --- Редактирование ---

    # Диалог ввода текста (для имён папок)
    def _get_text(self, title: str, label: str, text: str = "") -> tuple[str, bool]:
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextValue(text)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.adjustSize()
        dialog.setFixedSize(dialog.size())
        accepted = dialog.exec() == QInputDialog.DialogCode.Accepted
        return dialog.textValue(), accepted

    # Создание новой папки
    def new_group(self, parent: Folder | None = None):
        name, ok = self._get_text(
            tr("dialog.new_group.title"), tr("dialog.new_group.label")
        )
        if not ok or not name.strip():
            return
        folder = Folder(name=name.strip(), parent=parent)
        (parent.folders if parent else self.root_folders).append(folder)
        self.modified = True
        self.rebuild_tree()

        # Разворачиваем родителей и прокручиваем к новой папке
        item = self._find_item(folder)
        if item:
            ancestor = item.parent()
            while (
                ancestor is not None and ancestor is not self.tree.invisibleRootItem()
            ):
                ancestor.setExpanded(True)
                ancestor = ancestor.parent()
        self._refresh()

    # Создание подпапки в выбранной папке
    def new_subgroup(self):
        parent = self._target_folder()
        if parent is None:
            self._message_box(
                QMessageBox.Icon.Information,
                tr("dialog.new_subgroup.title"),
                tr("dialog.new_subgroup.info"),
                QMessageBox.StandardButton.Ok,
            )
            return
        self.new_group(parent=parent)

    # Создание новой закладки
    def new_bookmark(self, folder: Folder | None = None):
        if folder is None:
            folder = (
                self._scope_token if isinstance(self._scope_token, Folder) else None
            )
        dialog = BookmarkDialog(self)
        if dialog.exec() != BookmarkDialog.DialogCode.Accepted:
            return
        name, url = dialog.result_data()
        bookmark = Bookmark(
            url=url,
            title=name or url,
            parent=folder,
            add_date=str(int(time.time())),
        )
        (folder.bookmarks if folder else self.root_bookmarks).append(bookmark)
        self.modified = True
        self.rebuild_tree()

        # Разворачиваем родителей и выделяем новую закладку
        target_token = folder if folder is not None else "none"
        item = self._find_item(target_token)
        if item:
            parent_item = item.parent()
            while (
                parent_item is not None
                and parent_item is not self.tree.invisibleRootItem()
            ):
                parent_item.setExpanded(True)
                parent_item = parent_item.parent()
            owner = item.treeWidget()
            owner.setCurrentItem(item)
            owner.scrollToItem(item)
        self._refresh()

        # Выделение новой строки в таблице
        for i, row_bookmark in enumerate(self.model._rows):
            if row_bookmark.uid == bookmark.uid:
                self.table.selectRow(i)
                self.table.scrollTo(self.model.index(i, 0))
                break

    # Переименование выбранной папки
    def rename_selected_group(self):
        if self._tree_folder_sel_count == 1:
            self.rename_group(self._scope_token)

    # Переименование папки
    def rename_group(self, folder: Folder):
        name, ok = self._get_text(
            tr("dialog.rename_group.title"),
            tr("dialog.rename_group.label"),
            folder.name,
        )
        if not ok or not name.strip():
            return
        folder.name = name.strip()
        self.modified = True
        self.rebuild_tree()
        self._refresh()

    # Редактирование выбранной закладки
    def edit_bookmark(self):
        selected = self._selected_bookmarks()
        if not selected:
            return
        bookmark = selected[0]
        dialog = BookmarkDialog(
            self, tr("dialog.bookmark.edit_title"), bookmark.title, bookmark.url
        )
        if dialog.exec() != BookmarkDialog.DialogCode.Accepted:
            return
        name, url = dialog.result_data()
        bookmark.title, bookmark.url = name or url, url
        bookmark.add_date = str(int(time.time()))
        bookmark._invalidate_norm()
        self.modified = True
        self._refresh()

    # Удаление выбранных папок
    def delete_selected_groups(self):
        folders = [
            it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()
        ]
        folders = [f for f in folders if isinstance(f, Folder)]
        if not folders:
            return
        self.delete_groups(folders)

    # Удаление папок из контекстного меню
    def _delete_groups_context(self, clicked: Folder):
        folders = [
            it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()
        ]
        folders = [f for f in folders if isinstance(f, Folder)]
        if not any(f is clicked for f in folders):
            folders = [clicked]
        self.delete_groups(folders)

    # Удаление списка папок с подтверждением
    def delete_groups(self, folders: list[Folder]):
        # Исключаем вложенные папки, если родитель уже в списке
        top = [
            f
            for f in folders
            if not any(
                f is not other and self._is_within(f, other) for other in folders
            )
        ]
        if not top:
            return

        total = sum(count_bookmarks(f) for f in top)
        names = ", ".join(f"'{f.name}'" for f in top[:5])
        if len(top) > 5:
            names += tr("dialog.delete_groups.more", count=len(top) - 5)

        reply = self._message_box(
            QMessageBox.Icon.Warning,
            tr("dialog.delete_groups.title"),
            tr("dialog.delete_groups.text", names=names, count=total),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for folder in top:
            (folder.parent.folders if folder.parent else self.root_folders).remove(
                folder
            )
            # Убираем вырезанные папки, если они внутри удалённых
            self._cut_folders = [
                c for c in self._cut_folders if not self._is_within(c, folder)
            ]

        if self._find_item(self._scope_token) is None:
            self._scope_token = "all"
        self.modified = True
        self.rebuild_tree()
        self._refresh()

    # Удаление выбранных закладок
    def delete_selected_bookmarks(self):
        selected = self._selected_bookmarks()
        if not selected:
            return
        reply = self._message_box(
            QMessageBox.Icon.Warning,
            tr("dialog.delete_bookmarks.title"),
            tr("dialog.delete_bookmarks.text", count=len(selected)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for bookmark in selected:
            (
                bookmark.parent.bookmarks if bookmark.parent else self.root_bookmarks
            ).remove(bookmark)
        self.modified = True
        self._refresh()

    # --- Вырезать и вставить папки ---

    # Вырезание выбранных папок
    def cut_folders(self):
        folders = [
            it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()
        ]
        folders = [f for f in folders if isinstance(f, Folder)]
        if not folders:
            return
        self._cut_folders = folders
        self.rebuild_tree()
        self._refresh()
        self._status_message(tr("status.cut.prompt", count=len(folders)), 0)

    # Вставка вырезанных папок в целевую папку
    def paste_folders(self, target: Folder | str = "scope"):
        if not self._cut_folders:
            return
        if target == "scope":
            target = (
                self._scope_token if isinstance(self._scope_token, Folder) else None
            )
        # Запрет вставки папки в саму себя
        if target is not None and any(
            self._is_within(target, cut) for cut in self._cut_folders
        ):
            self._message_box(
                QMessageBox.Icon.Warning,
                tr("dialog.paste_into_self.title"),
                tr("dialog.paste_into_self.text"),
                QMessageBox.StandardButton.Ok,
            )
            return

        moved = 0
        for cut in self._cut_folders:
            # Пропускаем вложенные папки, если родитель уже перемещён
            if any(cut is not p and self._is_within(cut, p) for p in self._cut_folders):
                continue
            if cut.parent is target:
                continue
            (cut.parent.folders if cut.parent else self.root_folders).remove(cut)
            cut.parent = target
            (target.folders if target else self.root_folders).append(cut)
            moved += 1

        self._cut_folders = []
        self.modified = True
        self.rebuild_tree()
        self._refresh()
        self._status_message(tr("status.pasted", count=moved), 4000)

    # --- Инструменты ---

    # Сортировка закладок по названию с чередованием направления
    def sort_bookmarks(self):
        self._sort_bookmarks_desc = not self._sort_bookmarks_desc
        reverse = self._sort_bookmarks_desc

        def key_b(b):
            return (b.title or b.url).casefold()

        self.root_bookmarks.sort(key=key_b, reverse=reverse)
        for folder in iter_folders(self.root_folders):
            folder.bookmarks.sort(key=key_b, reverse=reverse)
        self.modified = True
        self._refresh()
        self._status_message(
            tr("status.sorted_bookmarks", order=("Z–A" if reverse else "A–Z")), 4000
        )

    # Сортировка папок по имени с чередованием направления
    def sort_groups(self):
        self._sort_groups_desc = not self._sort_groups_desc
        reverse = self._sort_groups_desc

        def key_f(f):
            return f.name.casefold()

        self.root_folders.sort(key=key_f, reverse=reverse)
        for folder in iter_folders(self.root_folders):
            folder.folders.sort(key=key_f, reverse=reverse)
        self.modified = True
        self.rebuild_tree()
        self._refresh()
        self._status_message(
            tr("status.sorted_groups", order=("Z–A" if reverse else "A–Z")), 4000
        )

    # Автоматическая группировка закладок по домену
    def auto_group_by_domain(self):
        buckets: dict[str, list[Bookmark]] = {}
        for bookmark in self.root_bookmarks:
            domain = extract_domain(bookmark.url)
            if domain:
                buckets.setdefault(domain, []).append(bookmark)

        created = moved = 0
        remove_uids: set[int] = set()

        for domain, items in sorted(buckets.items()):
            # Группируем только домены с двумя и более закладками
            if len(items) < 2:
                continue
            # Ищем существующую папку с таким именем
            folder = next(
                (f for f in self.root_folders if f.name.casefold() == domain), None
            )
            if folder is None:
                folder = Folder(name=domain)
                self.root_folders.append(folder)
                created += 1
            for bookmark in items:
                remove_uids.add(bookmark.uid)
                bookmark.parent = folder
                folder.bookmarks.append(bookmark)
                moved += 1

        # Удаление перемещённых закладок из корневого списка
        if remove_uids:
            self.root_bookmarks = [
                b for b in self.root_bookmarks if b.uid not in remove_uids
            ]

        if moved:
            self.modified = True
            self.rebuild_tree()
            self._refresh()

        if moved:
            text = tr("dialog.auto_group.result", created=created, moved=moved)
        else:
            text = tr("dialog.auto_group.none")
        self._message_box(
            QMessageBox.Icon.Information,
            tr("dialog.auto_group.title"),
            text,
            QMessageBox.StandardButton.Ok,
        )

    # Разгруппировка папок с одной закладкой
    def ungroup_singles(self):
        moved = 0

        def flatten_single(folder: Folder) -> None:
            nonlocal moved
            for child in list(folder.folders):
                flatten_single(child)
            # Папка с одной закладкой и без подпапок - разгруппировать
            if len(folder.bookmarks) == 1 and not folder.folders:
                bookmark = folder.bookmarks.pop()
                bookmark.parent = None
                self.root_bookmarks.append(bookmark)
                (folder.parent.folders if folder.parent else self.root_folders).remove(
                    folder
                )
                moved += 1

        for top in list(self.root_folders):
            flatten_single(top)

        if moved:
            self.modified = True
            self.rebuild_tree()
            self._refresh()
        self._message_box(
            QMessageBox.Icon.Information,
            tr("dialog.ungroup_singles.title"),
            tr("dialog.ungroup_singles.result", moved=moved, removed=moved),
            QMessageBox.StandardButton.Ok,
        )

    # Разгруппировка всех папок
    def ungroup_all(self):
        all_folders = list(iter_folders(self.root_folders))
        if not all_folders:
            return
        total = sum(len(f.bookmarks) for f in all_folders)

        reply = self._message_box(
            QMessageBox.Icon.Warning,
            tr("dialog.ungroup_all.title"),
            tr("dialog.ungroup_all.text", total=total, count=len(all_folders)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for folder in all_folders:
            for bookmark in folder.bookmarks:
                bookmark.parent = None
                self.root_bookmarks.append(bookmark)
            folder.bookmarks.clear()
        self.root_folders.clear()
        self._cut_folders = []
        self.modified = True
        self.rebuild_tree()
        self._refresh()
        self._status_message(tr("status.all_groups_removed"), 4000)

    # Разгруппировка выбранных папок
    def ungroup_selected(self):
        folders = [
            it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()
        ]
        folders = [f for f in folders if isinstance(f, Folder)]
        if not folders:
            return

        top = [
            f
            for f in folders
            if not any(
                f is not other and self._is_within(f, other) for other in folders
            )
        ]
        if not top:
            return

        for folder in top:
            for sub in iter_folders([folder]):
                for bookmark in sub.bookmarks:
                    bookmark.parent = None
                    self.root_bookmarks.append(bookmark)
                sub.bookmarks.clear()
            (folder.parent.folders if folder.parent else self.root_folders).remove(
                folder
            )
            self._cut_folders = [
                c for c in self._cut_folders if not self._is_within(c, folder)
            ]

        if self._find_item(self._scope_token) is None:
            self._scope_token = "all"
        self.modified = True
        self.rebuild_tree()
        self._refresh()
        self._status_message(tr("status.ungrouped", count=len(top)), 4000)

    # Удаление пустых папок
    def remove_empty_groups(self):
        removed = 0

        def prune(folder: Folder) -> bool:
            nonlocal removed
            for child in list(folder.folders):
                if prune(child):
                    folder.folders.remove(child)
            empty = not folder.bookmarks and not folder.folders
            if empty:
                removed += 1
            return empty

        for top in list(self.root_folders):
            if prune(top):
                self.root_folders.remove(top)

        if removed:
            self.modified = True
            self.rebuild_tree()
            self._refresh()
        self._message_box(
            QMessageBox.Icon.Information,
            tr("dialog.remove_empty.title"),
            tr("dialog.remove_empty.result", count=removed),
            QMessageBox.StandardButton.Ok,
        )

    # Удаление дубликатов по указанному ключу
    def _remove_duplicates(self, key, title_key: str):
        seen = set()
        removed = []
        for bookmark in iter_bookmarks(self.root_folders, self.root_bookmarks):
            value = key(bookmark)
            if value in seen:
                removed.append(bookmark)
            else:
                seen.add(value)
        for bookmark in removed:
            (
                bookmark.parent.bookmarks if bookmark.parent else self.root_bookmarks
            ).remove(bookmark)
        if removed:
            self.modified = True
            self._refresh()
        self._message_box(
            QMessageBox.Icon.Information,
            tr(title_key),
            tr("dialog.dedup.result", removed=len(removed), remaining=len(seen)),
            QMessageBox.StandardButton.Ok,
        )

    # Удаление дубликатов по адресу
    def dedup_urls(self):
        self._remove_duplicates(lambda b: b._norm_url, "dialog.dedup_urls.title")

    # Удаление дубликатов по названию
    def dedup_titles(self):
        self._remove_duplicates(lambda b: b._norm_title, "dialog.dedup_titles.title")

    # Перемещение дубликатов в отдельную папку
    def group_duplicates(self):
        seen_url: set[str] = set()
        seen_title: set[str] = set()
        to_move: list[Bookmark] = []

        for bookmark in iter_bookmarks(self.root_folders, self.root_bookmarks):
            url = bookmark._norm_url
            title = bookmark._norm_title
            if url in seen_url or title in seen_title:
                to_move.append(bookmark)
            seen_url.add(url)
            seen_title.add(title)

        if not to_move:
            return

        dup_folder = next(
            (f for f in self.root_folders if f.name == tr("dub.folder.name")), None
        )
        moved = 0
        for bookmark in to_move:
            # Пропускаем закладки уже в папке дубликатов
            if dup_folder is not None and bookmark.parent is dup_folder:
                continue
            if dup_folder is None:
                dup_folder = Folder(name=tr("dub.folder.name"))
                self.root_folders.append(dup_folder)
            (
                bookmark.parent.bookmarks if bookmark.parent else self.root_bookmarks
            ).remove(bookmark)
            bookmark.parent = dup_folder
            dup_folder.bookmarks.append(bookmark)
            moved += 1

        if not moved:
            self._status_message(
                tr("status.duplicates.already", folder=tr("dub.folder.name")), 4000
            )
            return

        self.modified = True
        self.rebuild_tree()
        self._refresh()
        self._status_message(
            tr("status.duplicates.moved", folder=tr("dub.folder.name"), count=moved),
            5000,
        )

    # --- Проверка адресов ---

    # Сброс кеша строк проверки
    def _invalidate_probe_rows(self) -> None:
        self._probe_rows_valid = False
        self._probe_rows.clear()
        self._invalidate_status_counts()

    # Номер строки закладки по UID с кешированием
    def _probe_row_index(self, uid: int) -> int | None:
        if not self._probe_rows_valid:
            self._probe_rows = {b.uid: i for i, b in enumerate(self.model._rows)}
            self._probe_rows_valid = True
        return self._probe_rows.get(uid)

    # Запуск проверки адресов текущей области видимости
    def check_urls(self):
        if self._probe_active:
            return
        scope = self._scope_bookmarks()
        if not scope:
            return

        self._probe_active = True
        self._probe_paused = False
        self._probe_map = {b.uid: b for b in scope}
        self._probe_uids = set(self._probe_map)
        self._probe_queue = deque(scope)
        self._probe_running = 0
        self._probe_total = len(scope)
        self._probe_done = 0
        self._probe_stats = {"online": 0, "offline": 0, "redirect": 0}
        self._active_replies.clear()
        self._probe_dispatching = False

        self._status_message(
            tr("status.probe.progress", done=0, total=self._probe_total), 0
        )
        self.btn_probe.setEnabled(True)
        self._set_probe_icon(True)
        self._blink_timer.start()
        self._update_actions_state()
        self._probe_dispatch()

    # Создание сетевого запроса для проверки адреса
    def _make_probe_request(self, url: QUrl) -> QNetworkRequest:
        request = QNetworkRequest(url)
        timeout_ms = int(TIME_RESPONSE * 1000)
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(timeout_ms)

        # Ручная обработка перенаправлений
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )

        # Заголовки для имитации браузерного запроса
        request.setRawHeader(
            b"User-Agent",
            b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            b"AppleWebKit/537.36 (KHTML, like Gecko) "
            b"Chrome/126.0.0.0 Safari/537.36",
        )
        request.setRawHeader(
            b"Accept",
            b"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        request.setRawHeader(
            b"Accept-Language",
            b"ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        )
        request.setRawHeader(b"Accept-Encoding", b"identity")
        request.setRawHeader(b"Cache-Control", b"no-cache")
        request.setRawHeader(b"Pragma", b"no-cache")
        request.setRawHeader(b"Sec-Fetch-Dest", b"document")
        request.setRawHeader(b"Sec-Fetch-Mode", b"navigate")
        request.setRawHeader(b"Sec-Fetch-Site", b"none")
        request.setRawHeader(b"Sec-Fetch-User", b"?1")
        request.setRawHeader(b"Upgrade-Insecure-Requests", b"1")
        return request

    # Отправка запросов из очереди с ограничением одновременных
    def _probe_dispatch(self) -> None:
        if not self._probe_active or self._probe_paused or self._probe_dispatching:
            return

        self._probe_dispatching = True
        immediate: list[tuple[int, str]] = []

        try:
            while self._probe_queue and self._probe_running < MAX_THREAD_COUNT:
                bookmark = self._probe_queue.popleft()
                url_text = (bookmark.url or "").strip()
                url = QUrl.fromUserInput(url_text)

                # Невалидные адреса сразу помечаем как недоступные
                if (
                    not url_text.lower().startswith(("http://", "https://"))
                    or not url.isValid()
                    or not url.host()
                ):
                    immediate.append((bookmark.uid, "offline"))
                    continue

                request = self._make_probe_request(url)
                reply = self._probe_net.get(request)
                reply.setProperty("uid", bookmark.uid)
                reply.setProperty("early_aborted", False)

                # Таймер принудительной остановки по таймауту
                timer = QTimer(reply)
                timer.setSingleShot(True)
                timer.setInterval(int(TIME_RESPONSE * 1000))
                timer.timeout.connect(reply.abort)
                timer.start()
                reply.finished.connect(lambda t=timer: t.stop())

                # Ранняя остановка после получения заголовков
                reply.metaDataChanged.connect(
                    lambda r=reply: self._abort_after_headers(r)
                )
                reply.readyRead.connect(lambda r=reply: self._abort_after_headers(r))

                self._active_replies.add(reply)
                self._probe_running += 1

            # Обработка невалидных адресов без сетевых запросов
            for uid, status in immediate:
                self._finish_probe(uid, status)
        finally:
            self._probe_dispatching = False

    # Ранняя остановка запроса после получения заголовков ответа
    def _abort_after_headers(self, reply: QNetworkReply) -> None:
        if reply.property("early_aborted"):
            return

        code_value = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        code = None
        if code_value is not None:
            try:
                code = int(code_value)
            except (TypeError, ValueError):
                code = None

        # Пропускаем информационные коды (100-199)
        if code is not None and code < 200:
            return

        # Проверка наличия перенаправления
        redirect_value = reply.attribute(
            QNetworkRequest.Attribute.RedirectionTargetAttribute
        )
        has_redirect = False
        if redirect_value is not None:
            target = (
                redirect_value
                if isinstance(redirect_value, QUrl)
                else QUrl(redirect_value)
            )
            if target.isValid() and not target.isEmpty():
                has_redirect = True

        if code is None and not has_redirect:
            return

        # Сохраняем результат и прерываем загрузку тела ответа
        reply.setProperty("early_aborted", True)
        if code is not None:
            reply.setProperty("probe_code", code)
        if has_redirect:
            reply.setProperty("probe_redirect", True)
        reply.abort()

    # Обработка завершения сетевого запроса
    def _on_reply_finished(self, reply: QNetworkReply) -> None:
        # Проверка остановлена - только удаляем ответ
        if not self._probe_active:
            reply.deleteLater()
            return
        if reply not in self._active_replies:
            reply.deleteLater()
            return

        self._active_replies.discard(reply)
        uid = reply.property("uid")
        if uid in self._probe_uids:
            self._probe_running = max(0, self._probe_running - 1)
            status = self._status_from_reply(reply)
            reply.deleteLater()
            self._finish_probe(uid, status)
        else:
            reply.deleteLater()

    # Определение статуса по ответу сервера
    def _status_from_reply(self, reply: QNetworkReply) -> str:
        # Перенаправление
        if reply.property("probe_redirect"):
            return "redirect"

        code_value = reply.property("probe_code")
        if code_value is None:
            code_value = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute
            )
        try:
            code = int(code_value) if code_value is not None else 0
        except (TypeError, ValueError):
            code = 0

        redirect_value = reply.attribute(
            QNetworkRequest.Attribute.RedirectionTargetAttribute
        )
        if redirect_value is not None:
            target = (
                redirect_value
                if isinstance(redirect_value, QUrl)
                else QUrl(redirect_value)
            )
            if target.isValid() and not target.isEmpty():
                return "redirect"

        if 300 <= code < 400:
            return "redirect"

        # Сравнение исходного и конечного адресов
        original = reply.request().url().toString().strip().rstrip("/")
        final = reply.url().toString().strip().rstrip("/")
        if original and final and original != final:
            return "redirect"

        if code in (404, 410):
            return "offline"
        if code > 0:
            return "online"
        return "offline"

    # Завершение проверки одной закладки
    def _finish_probe(self, uid: int, status: str) -> None:
        if not self._probe_active or uid not in self._probe_uids:
            return

        bookmark = self._probe_map.get(uid)
        if bookmark is not None:
            bookmark.status = status
            self._invalidate_status_counts()
            # Обновление строки таблицы без полного сброса модели
            row = self._probe_row_index(uid)
            if (
                row is not None
                and row < self.model.rowCount()
                and self.model.bookmark_at(row).uid == uid
            ):
                self.model.dataChanged.emit(
                    self.model.index(row, 0),
                    self.model.index(row, self.model.columnCount() - 1),
                )

        self._probe_stats[status] = self._probe_stats.get(status, 0) + 1
        self._probe_done += 1
        self._probe_uids.discard(uid)

        # Проверка завершена полностью
        if self._probe_done >= self._probe_total:
            self._status_message(self._probe_summary_message(), 8000)
            self._probe_reset_ui()
        else:
            if not self._probe_paused:
                self._status_message(
                    tr(
                        "status.probe.progress",
                        done=self._probe_done,
                        total=self._probe_total,
                    ),
                    0,
                )
            self._probe_dispatch()

    # Итоговое сообщение проверки
    def _probe_summary_message(self) -> str:
        stats = self._probe_stats
        return tr(
            "status.probe.finished",
            online=stats["online"],
            offline=stats["offline"],
            redirect=stats["redirect"],
        )

    # --- Управление процессом проверки ---

    # Приостановка проверки
    def _probe_pause(self):
        if not self._probe_active or self._probe_paused:
            return
        self._probe_paused = True
        self._blink_timer.stop()
        self._set_probe_icon(False)
        self._status_message(tr("status.probe.paused"), 0)

    # Возобновление проверки
    def _probe_resume(self):
        if not self._probe_active or not self._probe_paused:
            return
        self._probe_paused = False
        self._set_probe_icon(True)
        self._blink_timer.start()
        self._status_message(
            tr("status.probe.progress", done=self._probe_done, total=self._probe_total),
            0,
        )
        self._probe_dispatch()

    # Отмена проверки
    def _probe_cancel(self, silent: bool = False):
        if not self._probe_active:
            return
        summary = self._probe_summary_message()
        self._probe_active = False
        self._probe_abort_network()
        self._probe_reset_ui()
        if not silent:
            self._status_message(summary, 8000)

    # Принудительная остановка всех активных сетевых запросов
    def _probe_abort_network(self) -> None:
        for reply in list(self._active_replies):
            with contextlib.suppress(RuntimeError, TypeError):
                timer = reply.findChild(QTimer)
                if timer is not None and hasattr(timer, "stop"):
                    timer.stop()
                reply.metaDataChanged.disconnect()
                reply.readyRead.disconnect()
                reply.finished.disconnect()
            with contextlib.suppress(RuntimeError, TypeError):
                reply.abort()
                reply.deleteLater()
        self._active_replies.clear()
        self._probe_running = 0

    # Сброс интерфейса проверки в исходное состояние
    def _probe_reset_ui(self):
        self._probe_active = False
        self._probe_paused = False
        self._probe_queue.clear()
        self._probe_running = 0
        self._probe_dispatching = False
        self._probe_map = {}
        self._probe_uids = set()
        self._active_replies.clear()
        self._blink_timer.stop()
        self._set_probe_icon(False)
        self.btn_probe.setEnabled(False)
        self._update_actions_state()

    # Переключение мигания индикатора
    def _blink_toggle(self):
        self._blink_on = not self._blink_on
        self._set_probe_icon(self._blink_on)

    # Установка иконки кнопки проверки
    def _set_probe_icon(self, on: bool) -> None:
        name = "url_check_on.ico" if on else "url_check_off.ico"
        self.btn_probe.setIcon(QIcon(str(ICONS_DIR / name)))

    # Контекстное меню кнопки проверки
    def _show_probe_menu(self, pos):
        if not self._probe_active:
            return
        menu = QMenu(self)
        if self._probe_paused:
            menu.addAction(tr("probe.resume"), self._probe_resume)
        else:
            menu.addAction(tr("probe.pause"), self._probe_pause)
        menu.addAction(tr("common.cancel"), self._probe_cancel)
        menu.exec(self.btn_probe.mapToGlobal(pos))

    # --- Перемещение закладок ---

    # Перемещение закладок по UID в целевую папку
    def _move_bookmarks(self, uids, folder) -> int:
        if not uids:
            return 0

        wanted = set(uids)
        targets = [
            b
            for b in iter_bookmarks(self.root_folders, self.root_bookmarks)
            if b.uid in wanted
        ]

        # Исключаем закладки уже в целевой папке
        to_move = [b for b in targets if b.parent is not folder]
        if not to_move:
            return 0

        move_uids = {b.uid for b in to_move}

        # Группировка по текущему родителю для пакетного удаления
        by_parent: dict[Folder | None, list[Bookmark]] = {}
        for bookmark in to_move:
            by_parent.setdefault(bookmark.parent, []).append(bookmark)

        # Однопроходная фильтрация для каждого родителя
        for parent in by_parent:
            if parent is None:
                self.root_bookmarks = [
                    b for b in self.root_bookmarks if b.uid not in move_uids
                ]
            else:
                parent.bookmarks = [
                    b for b in parent.bookmarks if b.uid not in move_uids
                ]

        # Присваивание нового родителя и добавление в целевую папку
        target_list = folder.bookmarks if folder else self.root_bookmarks
        for bookmark in to_move:
            bookmark.parent = folder
            target_list.append(bookmark)

        self.modified = True
        self.rebuild_tree()
        self._refresh()
        self._status_message(tr("status.moved_bookmarks", count=len(to_move)), 4000)
        return len(to_move)

    # Обработка перетаскивания закладок в папку
    def _on_bookmarks_dropped(self, folder, uids):
        self._move_bookmarks(uids, folder)

    # Вход в режим перемещения выбранных закладок
    def _start_move_mode(self):
        if not self.root_folders:
            return
        selected = self._selected_bookmarks()
        if not selected:
            return
        self._move_uids = {b.uid for b in selected}
        self.model.set_muted_uids(self._move_uids)
        QApplication.setOverrideCursor(Qt.CursorShape.ForbiddenCursor)
        self._status_message(tr("status.move_mode.prompt"), 0)

    # Отмена режима перемещения
    def _cancel_move_mode(self):
        if not self._move_uids:
            return
        self._move_uids = set()
        self.model.set_muted_uids(None)
        QApplication.restoreOverrideCursor()
        self.tree._set_highlight(None)
        self.tree_top._set_highlight(None)
        self._status_message(tr("status.move_mode.cancelled"), 3000)

    # Завершение режима перемещения в целевую папку
    def _complete_move_mode(self, target):
        uids = list(self._move_uids)
        self._move_bookmarks(uids, target)
        self._move_uids = set()
        QApplication.restoreOverrideCursor()
        self.model.set_muted_uids(None)
        self.tree._set_highlight(None)
        self.tree_top._set_highlight(None)

    # --- Контекстные меню ---

    # Подсчёт статусов видимых строк с кешированием
    def _get_status_counts(self) -> dict[str, int]:
        if not self._status_counts_valid:
            counts = {"online": 0, "redirect": 0, "offline": 0}
            for row in range(self.model.rowCount()):
                status = self.model.bookmark_at(row).status
                if status in counts:
                    counts[status] += 1
            self._status_counts = counts
            self._status_counts_valid = True
        return self._status_counts

    # Сброс кеша подсчёта статусов
    def _invalidate_status_counts(self) -> None:
        self._status_counts_valid = False

    # Подменю выделения по статусу проверки
    def _add_select_submenu(self, menu: QMenu):
        counts = self._get_status_counts()
        sub = menu.addMenu(tr("context.select_submenu"))

        act = sub.addAction(
            tr("status.online"), lambda: self._select_by_status("online")
        )
        act.setEnabled(counts["online"] > 0)

        act = sub.addAction(
            tr("status.redirect"), lambda: self._select_by_status("redirect")
        )
        act.setEnabled(counts["redirect"] > 0)

        act = sub.addAction(
            tr("status.offline"), lambda: self._select_by_status("offline")
        )
        act.setEnabled(counts["offline"] > 0)

        sub.addAction(tr("context.select_all"), self.table.selectAll)
        return sub

    # Выделение закладок по статусу проверки
    def _select_by_status(self, status: str) -> None:
        rows = [
            r
            for r in range(self.model.rowCount())
            if self.model.bookmark_at(r).status == status
        ]
        if not rows:
            return
        self.table._select_rows(rows)
        self._status_message(tr("status.selected_bookmarks", count=len(rows)), 3000)

    # Выделение всех папок в дереве
    def select_all_folders(self):
        count = 0
        for item in self._iter_tree_items(self.tree):
            if isinstance(item.data(0, Qt.ItemDataRole.UserRole), Folder):
                item.setSelected(True)
                count += 1
        if count:
            self._status_message(tr("status.selected_folders", count=count), 3000)

    # Контекстное меню закреплённой зоны дерева
    def _show_top_context_menu(self, pos):
        item = self.tree_top.itemAt(pos)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        menu = QMenu(self)

        menu.addAction(tr("context.new_group"), lambda: self.new_group(parent=None))
        act = menu.addAction(
            tr("context.paste_group"), lambda: self.paste_folders(target=None)
        )
        act.setEnabled(bool(self._cut_folders))
        menu.addSeparator()

        if data in ("all", "none"):
            sub = self._add_select_submenu(menu)
            sub.setEnabled(self.model.rowCount() > 0)
        else:
            act = menu.addAction(
                tr("context.select_all_folders"), self.select_all_folders
            )
            act.setEnabled(self._stat_folders > 0)

        menu.exec(self.tree_top.viewport().mapToGlobal(pos))

    # Контекстное меню дерева папок
    def _show_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        has_folders = self._stat_folders > 0
        single_folder = self._tree_folder_sel_count <= 1
        folder_sel = self._tree_folder_sel_count > 0
        cut_busy = bool(self._cut_folders)

        menu = QMenu(self)

        if isinstance(data, Folder):
            # Меню для конкретной папки
            act = menu.addAction(
                tr("context.new_subgroup"), lambda: self.new_group(parent=data)
            )
            act.setEnabled(single_folder)
            menu.addAction(
                tr("context.new_bookmark"), lambda: self.new_bookmark(folder=data)
            )
            menu.addSeparator()

            act = menu.addAction(
                tr("context.rename_group"), lambda: self.rename_group(data)
            )
            act.setEnabled(single_folder)
            menu.addAction(
                tr("context.delete_group"), lambda: self._delete_groups_context(data)
            )
            act = menu.addAction(tr("context.ungroup"), self.ungroup_selected)
            act.setEnabled(folder_sel)
            menu.addSeparator()

            act = menu.addAction(tr("context.cut_group"), self.cut_folders)
            act.setEnabled(folder_sel and not cut_busy)
            act = menu.addAction(
                tr("context.paste_group"), lambda: self.paste_folders(target=data)
            )
            act.setEnabled(cut_busy)
            menu.addSeparator()

            menu.addAction(tr("context.select_all_folders"), self.select_all_folders)
            menu.addSeparator()
            menu.addAction(tr("context.expand_group"), lambda: item.setExpanded(True))
            menu.addAction(
                tr("context.collapse_group"), lambda: item.setExpanded(False)
            )
        else:
            # Меню для пустой области дерева
            menu.addAction(tr("context.new_group"), lambda: self.new_group(parent=None))
            act = menu.addAction(
                tr("context.paste_group"), lambda: self.paste_folders(target=None)
            )
            act.setEnabled(cut_busy)
            act = menu.addAction(tr("context.ungroup"), self.ungroup_selected)
            act.setEnabled(folder_sel)
            menu.addSeparator()

            act = menu.addAction(
                tr("context.select_all_folders"), self.select_all_folders
            )
            act.setEnabled(has_folders)
            menu.addSeparator()

            act = menu.addAction(tr("context.expand_all"), self.tree.expandAll)
            act.setEnabled(has_folders)
            act = menu.addAction(tr("context.collapse_all"), self.tree.collapseAll)
            act.setEnabled(has_folders)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # Контекстное меню таблицы закладок
    def _show_table_context_menu(self, pos):
        menu = QMenu(self)

        if self.table.indexAt(pos).isValid():
            # Меню для выбранной закладки
            selected = self._selected_bookmarks()
            single = len(selected) == 1
            any_sel = len(selected) > 0

            act = menu.addAction(tr("context.edit_bookmark"), self.edit_bookmark)
            act.setEnabled(single)

            act = menu.addAction(
                tr("context.delete_bookmark"), self.delete_selected_bookmarks
            )
            act.setEnabled(any_sel)

            act = menu.addAction(tr("context.move"), self._start_move_mode)
            act.setEnabled(any_sel and self._stat_folders > 0)
            menu.addSeparator()

            sub = self._add_select_submenu(menu)
            sub.setEnabled(self.model.rowCount() > 0)
            menu.addSeparator()

            act = menu.addAction(tr("context.copy_url"), self.copy_urls)
            act.setEnabled(single)

            act = menu.addAction(tr("context.open_in_browser"), self.open_in_browser)
            act.setEnabled(single)
        else:
            # Меню для пустой области таблицы
            menu.addAction(tr("context.new_bookmark"), lambda: self.new_bookmark())
            sub = self._add_select_submenu(menu)
            sub.setEnabled(self.model.rowCount() > 0)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    # Копирование адресов выбранных закладок в буфер обмена
    def copy_urls(self):
        urls = [b.url for b in self._selected_bookmarks()]
        if urls:
            QGuiApplication.clipboard().setText("\n".join(urls))
            self._status_message(tr("status.copied_urls", count=len(urls)), 4000)

    # Открытие выбранных закладок в браузере
    def open_in_browser(self):
        for bookmark in self._selected_bookmarks():
            QDesktopServices.openUrl(QUrl(bookmark.url))
