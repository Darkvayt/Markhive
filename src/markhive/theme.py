# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Тёмная тема оформления: палитра и стили
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Путь к папке с иконками
ICONS_DIR = Path(__file__).parent / "icons"


# Абсолютный путь к иконке в формате для QSS
def _icon_path(name: str) -> str:
    path = ICONS_DIR / name
    return str(path.resolve()).replace("\\", "/")


# QSS-шаблон тёмной темы
DARK_QSS_TEMPLATE = """
QMainWindow, QDialog { background-color:#1b1e24; }
QMenuBar { background-color:#1b1e24; color:#d6dae2; }
QMenuBar::item { padding:4px 10px; background:transparent; }
QMenuBar::item:selected { background:#2a3040; }
QMenu { background-color:#20242c; border:1px solid #2c313c; padding:4px; }
QMenu::item { padding:5px 24px 5px 10px; }
QMenu::item:selected { background:#2f7ff7; color:#ffffff; }
QMenu::item:disabled { color:#565d6b; }
QMenu::separator { height:1px; background:#2c313c; margin:4px 8px; }
QLineEdit { background:#262a33; border:1px solid #343a46;
            padding:5px 8px; selection-background-color:#2f7ff7; }
QLineEdit:focus { border-color:#2f7ff7; }
QToolButton#clearSearch { background:#262a33; border:1px solid #343a46;
                          padding:5px 4px; }
QToolButton#clearSearch:hover { border-color:#2f7ff7; color:#7fb0ff; }
QToolButton#probeButton { background:transparent; border:none; padding:0; }
QToolButton#probeButton:disabled { background:transparent; }
QTreeView, QTableView { background:#20232a; alternate-background-color:#252932;
                        border:1px solid #2c313c; outline:none; }
QTreeView::item { padding:4px 6px; }
QTreeView::item:hover { background:#2a2f3a; }
QTreeView::item:selected, QTableView::item:selected { background:transparent; color:#fff; }
QTableView::item { padding:4px 8px; }
QTreeView::branch { background:#20232a; }
QTreeView::branch:has-children:closed { image: url(%(closed)s); }
QTreeView::branch:has-children:open { image: url(%(open)s); }
QHeaderView::section { background:#1a1d23; color:#9aa3b2; padding:4px 8px;
                       border:none; border-bottom:1px solid #2c313c;
                       border-right:1px solid #262a33; font-weight:600; }
QStatusBar { background:#1b1e24; color:#9aa3b2; }
QStatusBar::item { border:none; }
QScrollBar:vertical { background:#20232a; width:10px; }
QScrollBar::handle:vertical { background:#3a4150; min-height:24px; }
QScrollBar::handle:vertical:hover { background:#4a5266; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { background:#20232a; height:10px; }
QScrollBar::handle:horizontal { background:#3a4150; min-width:24px; }
QSplitter::handle { background:#1b1e24; }
QToolTip { background:#20242c; color:#d6dae2; border:1px solid #2f7ff7; padding:4px; }
QDialog { background-color: #1b1e24; }
QTextBrowser { background-color: #20232a;
               selection-background-color: #2f7ff7;
               selection-color: #ffffff; }
QLabel#sectionHeader { color:#8f97a6; font-weight:700;
                       padding:2px 4px; font-size:16px;
                       font-family:"Segoe UI"; }
QLabel#hintLabel { color:#667085; font-size:11px; padding:2px; }
QLabel#statusCounter { padding:0 2px; }
QPushButton { background:#262a33; border:1px solid #343a46; padding:6px 14px; }
QPushButton:hover { border-color:#2f7ff7; }
QPushButton:default { background:#2f7ff7; border-color:#2f7ff7; color:#fff; }
QMessageBox, QInputDialog, QFileDialog { background:#1b1e24; }
"""


# Применение тёмной палитры и QSS к приложению
def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    # Настройка палитры
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#1b1e24"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#d6dae2"))
    p.setColor(QPalette.ColorRole.Base, QColor("#171a1f"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#20242c"))
    p.setColor(QPalette.ColorRole.Text, QColor("#d6dae2"))
    p.setColor(QPalette.ColorRole.Button, QColor("#20242c"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#d6dae2"))
    p.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Highlight, QColor("#405074"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8f97a6"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#20242c"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#d6dae2"))
    app.setPalette(p)

    # Подстановка путей к иконкам стрелок дерева в QSS
    closed = _icon_path("branch_closed.ico")
    opened = _icon_path("branch_open.ico")
    app.setStyleSheet(DARK_QSS_TEMPLATE % {"closed": closed, "open": opened})
