# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Конструктор интерфейса: виджеты, делегаты, меню, статус-бар
# Дерево папок состоит из двух зон:
#   tree_top - закреплённая зона без прокрутки
#   tree - прокручиваемое дерево папок
from __future__ import annotations

from typing import override

from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDrag,
    QIcon,
    QKeySequence,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from markhive import lang
from markhive.dialogs import AboutDialog, HotkeysDialog
from markhive.lang import tr
from markhive.models import Folder
from markhive.theme import ICONS_DIR


# Заголовок панели: иконка и текст
def _header_with_icon(icon_name: str, text: str) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    icon_label = QLabel()
    icon_label.setPixmap(QIcon(str(ICONS_DIR / icon_name)).pixmap(24, 24))
    icon_label.setFixedSize(24, 24)

    text_label = QLabel(text)
    text_label.setObjectName("sectionHeader")

    layout.addWidget(icon_label)
    layout.addWidget(text_label)
    layout.addStretch(1)
    return widget


# MIME-тип для перетаскивания закладок
MIME_BOOKMARKS = "application/x-markhive-bookmarks"


# --- Делегаты отрисовки ---


# Выделение синей рамкой и подсветка приёмника при перетаскивании
class _DropHighlightDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, index)

        # Отрисовка рамки выделения
        if selected:
            painter.save()
            painter.setBrush(QColor(154, 163, 178, 80))
            painter.setPen(QPen(QColor("#2f7ff7"), 0))
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()

        # Подсветка целевой папки при перетаскивании
        tree = self.parent()
        highlight = getattr(tree, "_highlight_item", None)
        if highlight is not None:
            item = tree.itemFromIndex(index)
            if item is highlight:
                painter.save()
                painter.setBrush(QColor(64, 80, 116, 80))
                painter.setPen(QColor("#405074"))
                painter.drawRect(option.rect.adjusted(1, 1, -1, -1))
                painter.restore()


# Затемнение строк таблицы вне переносимого набора
class _MutedRowDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        model = index.model()
        muted = getattr(model, "_muted", None)
        if muted:
            bookmark = model.bookmark_at(index.row())
            if bookmark.uid not in muted:
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(27, 30, 36, 180))
                painter.drawRect(option.rect)
                painter.restore()


# --- Виджеты ---


# Дерево групп: мультивыбор, перетаскивание с подсветкой, режим перемещения
class FolderTreeWidget(QTreeWidget):
    bookmarks_dropped = Signal(object, list)  # Сигнал завершения перетаскивания
    empty_area_clicked = Signal()  # Сигнал клика по пустой области

    def __init__(self, parent=None):
        super().__init__(parent)
        self._highlight_item = None  # Подсвеченный элемент при перетаскивании
        self.setItemDelegate(_DropHighlightDelegate(self))
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

    # Доступ к списку перемещаемых UID из главного окна
    def _move_uids(self):
        return getattr(self.window(), "_move_uids", None)

    # Определение целевой папки для перетаскивания
    def _drop_target(self, item):
        if item is None:
            return False
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, Folder):
            return data
        if data == "none":
            return None
        return False

    # Установка или сброс подсветки элемента
    def _set_highlight(self, item):
        if self._highlight_item is item:
            return
        self._highlight_item = item
        self.viewport().update()

    # Обработка событий мыши в режиме перемещения
    @override
    def eventFilter(self, watched, event):
        if watched is self.viewport() and self._move_uids():
            etype = event.type()

            # Подсветка элемента под курсором
            if etype == QEvent.Type.MouseMove:
                item = self.itemAt(event.position().toPoint())
                target = self._drop_target(item)
                self._set_highlight(item if target is not False else None)
                QApplication.changeOverrideCursor(
                    Qt.CursorShape.DragMoveCursor
                    if target is not False
                    else Qt.CursorShape.ForbiddenCursor
                )
            # Клик по целевой папке  завершение перемещения
            elif (
                etype == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                item = self.itemAt(event.position().toPoint())
                target = self._drop_target(item)
                if target is not False:
                    self.window()._complete_move_mode(target)
                else:
                    self.window()._cancel_move_mode()
                return True
            elif etype == QEvent.Type.Leave:
                self._set_highlight(None)
                QApplication.changeOverrideCursor(Qt.CursorShape.ForbiddenCursor)

        return super().eventFilter(watched, event)

    # Клик по пустой области - снятие выделения
    @override
    def mousePressEvent(self, event):
        empty = self.itemAt(event.position().toPoint()) is None
        if event.button() == Qt.MouseButton.LeftButton and empty:
            self.empty_area_clicked.emit()
        if event.button() == Qt.MouseButton.RightButton and empty:
            event.accept()
            return
        super().mousePressEvent(event)

    # --- Перетаскивание ---

    # Приём перетаскивания только с нашим MIME-типом
    @override
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_BOOKMARKS):
            event.acceptProposedAction()
        else:
            event.ignore()

    # Подсветка целевой папки при движении
    @override
    def dragMoveEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        target = self._drop_target(item)
        if target is not False:
            self._set_highlight(item)
            event.acceptProposedAction()
        else:
            self._set_highlight(None)
            event.ignore()

    # Сброс подсветки при уходе курсора
    @override
    def dragLeaveEvent(self, event):
        self._set_highlight(None)
        super().dragLeaveEvent(event)

    # Обработка завершения перетаскивания
    @override
    def dropEvent(self, event):
        self._set_highlight(None)
        target = self._drop_target(self.itemAt(event.position().toPoint()))
        if target is False:
            event.ignore()
            return
        # Извлечение UID из MIME-данных
        raw = bytes(event.mimeData().data(MIME_BOOKMARKS)).decode()
        uids = [int(x) for x in raw.split(",") if x]
        if uids:
            self.bookmarks_dropped.emit(target, uids)
            event.acceptProposedAction()
        else:
            event.ignore()


# Таблица закладок: мультивыделение и перетаскивание
class BookmarkTableView(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(False)
        self._press_pos = None  # Точка нажатия мыши
        self._drag_rows: list[int] = []  # Строки для перетаскивания

    # Список выбранных строк
    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.selectionModel().selectedIndexes()})

    # Выделение указанных строк целиком
    def _select_rows(self, rows) -> None:
        selection = QItemSelection()
        last_col = self.model().columnCount() - 1
        for row in rows:
            selection.select(
                self.model().index(row, 0), self.model().index(row, last_col)
            )
        self.selectionModel().select(
            selection, QItemSelectionModel.SelectionFlag.ClearAndSelect
        )

    # Начало перетаскивания: запоминание выбранных строк
    @override
    def mousePressEvent(self, event):
        # ПКМ по пустой области  контекстное меню
        if (
            event.button() == Qt.MouseButton.RightButton
            and not self.indexAt(event.position().toPoint()).isValid()
        ):
            event.accept()
            return

        self._press_pos = None
        self._drag_rows = []

        if event.button() == Qt.MouseButton.LeftButton:
            # Клик по таблице отменяет режим перемещения
            if getattr(self.window(), "_move_uids", None):
                self.window()._cancel_move_mode()

            pos = event.position().toPoint()
            self._press_pos = pos
            pressed = self.indexAt(pos)
            selected = set(self._selected_rows())
            no_mods = not (
                event.modifiers()
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.ShiftModifier
                )
            )
            # Начало перетаскивания при клике по уже выбранной строке
            if no_mods and pressed.isValid() and pressed.row() in selected:
                self._drag_rows = sorted(selected)

        super().mousePressEvent(event)
        if self._drag_rows:
            self._select_rows(self._drag_rows)

    # Инициация перетаскивания при движении мыши
    @override
    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._press_pos is None:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        delta = event.position().toPoint() - self._press_pos
        if delta.manhattanLength() < QApplication.startDragDistance():
            return

        self._press_pos = None

        # Перетаскивание только при наличии папок
        if not getattr(self.window(), "root_folders", None):
            return
        if not self._drag_rows:
            return

        rows = self._drag_rows
        self._select_rows(rows)
        drag_uids = {self.model().bookmark_at(r).uid for r in rows}
        # Приглушение строк вне перетаскиваемого набора
        self.model().set_muted_uids(drag_uids)
        self.viewport().repaint()
        QApplication.processEvents()

        # Запуск перетаскивания с MIME-данными
        uids = ",".join(str(u) for u in sorted(drag_uids))
        mime = QMimeData()
        mime.setData(MIME_BOOKMARKS, uids.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)
        self.model().set_muted_uids(None)

    # Сброс состояния перетаскивания
    @override
    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._press_pos = None
        self._drag_rows = []


# --- Конструктор интерфейса главного окна ---


# Миксин: сборка виджетов, меню и статус-бара
class MainWindowUI:
    # Создание действия меню с клавишей и обработчиком
    def _action(self, text, shortcut=None, triggered=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        if triggered:
            action.triggered.connect(triggered)
        return action

    # Общая настройка дерева папок
    def _configure_tree(self, tree, indentation=16):
        tree.setIconSize(QSize(20, 20))
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.setAcceptDrops(True)
        tree.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        tree.setDropIndicatorShown(True)
        tree.setIndentation(indentation)
        tree.setUniformRowHeights(True)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    # Сборка основных виджетов окна
    def _build_widgets(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)

        # --- Поле поиска ---
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search.placeholder"))
        self.search_edit.setFixedHeight(32)

        self.search_clear = QToolButton()
        self.search_clear.setIcon(QIcon(str(ICONS_DIR / "clear.ico")))
        self.search_clear.setText("")
        self.search_clear.setIconSize(QSize(24, 24))
        self.search_clear.setFixedSize(32, 32)
        self.search_clear.setObjectName("clearSearch")
        self.search_clear.setToolTip(tr("search.clear_tooltip"))

        self.search_counter = QLabel(tr("search.counter", count=0))
        self.search_counter.setObjectName("statusCounter")

        search_row = QHBoxLayout()
        search_icon = QLabel()
        search_icon.setPixmap(QIcon(str(ICONS_DIR / "search.ico")).pixmap(24, 24))
        search_icon.setFixedSize(24, 24)
        search_row.addWidget(search_icon)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_clear)
        search_row.addWidget(self.search_counter)
        root.addLayout(search_row)

        # --- Закреплённая зона дерева ---
        self.tree_top = FolderTreeWidget()
        self._configure_tree(self.tree_top, indentation=0)
        self.tree_top.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # --- Прокручиваемое дерево папок ---
        self.tree = FolderTreeWidget()
        self._configure_tree(self.tree)

        # --- Таблица закладок ---
        self.table = BookmarkTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegate(_MutedRowDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 320)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 200)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # --- Кнопка проверки адресов ---
        self.btn_probe = QToolButton()
        self.btn_probe.setObjectName("probeButton")
        self.btn_probe.setIconSize(QSize(24, 24))
        self.btn_probe.setFixedSize(24, 24)
        self.btn_probe.setToolTip(tr("probe.tooltip"))
        self.btn_probe.setIcon(QIcon(str(ICONS_DIR / "url_check_off.ico")))
        self.btn_probe.setEnabled(False)
        self.btn_probe.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # --- Левая панель: папки ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_title = _header_with_icon("folders.ico", tr("panel.groups"))
        left_hint = QLabel("")
        left_hint.setObjectName("hintLabel")

        left_layout.addWidget(left_title)
        left_layout.addWidget(self.tree_top)
        left_layout.addWidget(self.tree, 1)
        left_layout.addWidget(left_hint)

        # --- Правая панель: закладки ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_title = _header_with_icon("bookmarks.ico", tr("panel.bookmarks"))
        right_hint = QLabel("")
        right_hint.setObjectName("hintLabel")

        self.lbl_file_name = QLabel(tr("statusbar.file_not_loaded"))
        self.lbl_file_name.setObjectName("hintLabel")
        self.lbl_file_name.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_file_name.setToolTip(tr("statusbar.file_tooltip.open"))

        hint_row = QHBoxLayout()
        hint_row.addWidget(right_hint)
        hint_row.addStretch(1)
        hint_row.addWidget(self.lbl_file_name)

        right_header = QHBoxLayout()
        right_header.setContentsMargins(0, 0, 0, 0)
        right_header.addWidget(right_title)
        right_header.addStretch(1)
        right_header.addWidget(self.btn_probe)

        right_layout.addLayout(right_header)
        right_layout.addWidget(self.table, 1)
        right_layout.addLayout(hint_row)

        # --- Разделитель панелей ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 950])
        root.addWidget(splitter, 1)

    # Сборка меню окна
    def _build_menus(self):
        bar = self.menuBar()

        # Меню "Файл"
        file_menu = bar.addMenu(tr("menu.file"))
        file_menu.addAction(
            self._action(tr("menu.file.open_file"), "Ctrl+O", self.open_file)
        )
        file_menu.addAction(
            self._action(tr("menu.file.open_folder"), "Ctrl+Shift+O", self.open_folder)
        )
        file_menu.addSeparator()
        self.act_save = self._action(tr("menu.file.save"), "Ctrl+S", self.save_current)
        file_menu.addAction(self.act_save)
        self.act_save_as = self._action(
            tr("menu.file.save_as"), "Ctrl+Shift+S", self.save_as
        )
        file_menu.addAction(self.act_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self._action(tr("menu.file.exit"), "Ctrl+Q", self.close))

        # Меню "Редактирование"
        edit_menu = bar.addMenu(tr("menu.edit"))
        edit_menu.addAction(
            self._action(
                tr("menu.edit.new_group"), "Ctrl+Shift+G", lambda: self.new_group()
            )
        )
        self.act_new_subgroup = self._action(
            tr("menu.edit.new_subgroup"), "Ctrl+Alt+G", self.new_subgroup
        )
        edit_menu.addAction(self.act_new_subgroup)
        edit_menu.addAction(
            self._action(
                tr("menu.edit.new_bookmark"), "Ctrl+N", lambda: self.new_bookmark()
            )
        )
        edit_menu.addSeparator()
        self.act_rename_group = self._action(
            tr("menu.edit.rename_group"), "F2", self.rename_selected_group
        )
        edit_menu.addAction(self.act_rename_group)
        self.act_edit_bookmark = self._action(
            tr("menu.edit.edit_bookmark"), "Ctrl+E", self.edit_bookmark
        )
        edit_menu.addAction(self.act_edit_bookmark)
        edit_menu.addSeparator()
        self.act_delete_group = self._action(
            tr("menu.edit.delete_group"), "Ctrl+Shift+Del", self.delete_selected_groups
        )
        edit_menu.addAction(self.act_delete_group)
        self.act_delete_bookmarks = self._action(
            tr("menu.edit.delete_bookmarks"), "Delete", self.delete_selected_bookmarks
        )
        edit_menu.addAction(self.act_delete_bookmarks)
        edit_menu.addSeparator()
        self.act_cut_groups = self._action(
            tr("menu.edit.cut_groups"), "Ctrl+X", self.cut_folders
        )
        edit_menu.addAction(self.act_cut_groups)
        self.act_paste_groups = self._action(
            tr("menu.edit.paste_groups"), "Ctrl+V", lambda: self.paste_folders()
        )
        edit_menu.addAction(self.act_paste_groups)

        # Меню "Вид"
        view_menu = bar.addMenu(tr("menu.view"))
        self.act_expand_all = self._action(
            tr("menu.view.expand_all"),
            QKeySequence(QKeySequence.StandardKey.ZoomIn),
            self.tree.expandAll,
        )
        view_menu.addAction(self.act_expand_all)
        self.act_collapse_all = self._action(
            tr("menu.view.collapse_all"),
            QKeySequence(QKeySequence.StandardKey.ZoomOut),
            self.tree.collapseAll,
        )
        view_menu.addAction(self.act_collapse_all)

        # Меню "Инструменты"
        tools_menu = bar.addMenu(tr("menu.tools"))
        self.act_sort_bookmarks = self._action(
            tr("menu.tools.sort_bookmarks"), "Ctrl+T", self.sort_bookmarks
        )
        tools_menu.addAction(self.act_sort_bookmarks)
        self.act_sort_groups = self._action(
            tr("menu.tools.sort_groups"), "Ctrl+Shift+T", self.sort_groups
        )
        tools_menu.addAction(self.act_sort_groups)
        tools_menu.addSeparator()
        self.act_auto_group = self._action(
            tr("menu.tools.auto_group"), "Ctrl+G", self.auto_group_by_domain
        )
        tools_menu.addAction(self.act_auto_group)
        self.act_ungroup_singles = self._action(
            tr("menu.tools.ungroup_singles"), "Ctrl+Shift+U", self.ungroup_singles
        )
        tools_menu.addAction(self.act_ungroup_singles)
        self.act_ungroup_all = self._action(
            tr("menu.tools.ungroup_all"), "Ctrl+Alt+U", self.ungroup_all
        )
        tools_menu.addAction(self.act_ungroup_all)
        tools_menu.addSeparator()
        self.act_dedup_urls = self._action(
            tr("menu.tools.dedup_urls"), "Ctrl+D", self.dedup_urls
        )
        tools_menu.addAction(self.act_dedup_urls)
        self.act_dedup_titles = self._action(
            tr("menu.tools.dedup_titles"), "Ctrl+Shift+D", self.dedup_titles
        )
        tools_menu.addAction(self.act_dedup_titles)
        self.act_group_duplicates = self._action(
            tr("menu.tools.group_duplicates"), "Ctrl+Alt+D", self.group_duplicates
        )
        tools_menu.addAction(self.act_group_duplicates)
        tools_menu.addSeparator()
        self.act_check_urls = self._action(
            tr("menu.tools.check_urls"), "Ctrl+U", self.check_urls
        )
        tools_menu.addAction(self.act_check_urls)
        tools_menu.addSeparator()
        self.act_remove_empty = self._action(
            tr("menu.tools.remove_empty"), "Ctrl+Shift+P", self.remove_empty_groups
        )
        tools_menu.addAction(self.act_remove_empty)

        # Меню "Язык"
        language_menu = bar.addMenu(tr("menu.view.language"))
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        current = lang.current_language()
        for code, display_name in lang.available_languages().items():
            action = QAction(display_name, self)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(lambda checked, c=code: self._set_language(c))
            lang_group.addAction(action)
            language_menu.addAction(action)

        # Меню "Справка"
        help_menu = bar.addMenu(tr("menu.help"))
        help_menu.addAction(
            self._action(
                tr("menu.help.hotkeys"), "F1", lambda: HotkeysDialog(self).exec()
            )
        )
        help_menu.addAction(
            self._action(tr("menu.help.about"), None, lambda: AboutDialog(self).exec())
        )

        # Горячая клавиша поиска
        self.addAction(
            self._action(
                tr("menu.search"),
                "Ctrl+F",
                lambda: (self.search_edit.setFocus(), self.search_edit.selectAll()),
            )
        )

    # Сборка статус-бара с счётчиками
    def _build_statusbar(self):
        bar = self.statusBar()
        bar.layout().setSpacing(0)
        bar.setMinimumHeight(26)

        # Создание счётчика с иконкой или без
        def counter(tooltip: str, icon_name: str = "") -> QLabel:
            if icon_name:
                box = QWidget()
                row = QHBoxLayout(box)
                row.setContentsMargins(2, 0, 2, 0)
                row.setSpacing(4)
                pic = QLabel()
                pic.setPixmap(QIcon(str(ICONS_DIR / icon_name)).pixmap(16, 16))
                pic.setFixedSize(16, 16)
                text = QLabel()
                row.addWidget(pic)
                row.addWidget(text)
                box.setToolTip(tooltip)
                bar.addWidget(box)
            else:
                text = QLabel()
                text.setObjectName("statusCounter")
                text.setToolTip(tooltip)
                bar.addWidget(text)
            return text

        # Разделитель между счётчиками и сообщениями
        def separator():
            sep = QLabel("│")
            sep.setStyleSheet("color:#2c313c; padding:0 2px;")
            bar.addWidget(sep)

        self.lbl_total = counter(tr("statusbar.total"), "bookmarks_count.ico")
        self.lbl_folders = counter(tr("statusbar.folders"), "folders_count.ico")
        self.lbl_scope = counter(tr("statusbar.scope"), "in_folder.ico")
        self.lbl_dup_url = counter(tr("statusbar.dup_url"), "dub_url.ico")
        self.lbl_dup_title = counter(tr("statusbar.dup_title"), "dub_name.ico")
        self.lbl_sel_folders = counter(tr("statusbar.selected_folders"))
        self.lbl_sel_folders.hide()
        self.lbl_sel = counter(tr("statusbar.selected_bookmarks"))
        self.lbl_sel.hide()
        self.lbl_sel_folders.setStyleSheet("color:#2f7ff7;")
        self.lbl_sel.setStyleSheet("color:#2f7ff7;")

        separator()

        self.lbl_message = QLabel("")
        self.lbl_message.setObjectName("statusCounter")
        self.lbl_message.setStyleSheet("color:#667085;")
        bar.addWidget(self.lbl_message, 1)
