# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt
"""Markhive — точка входа."""

import sys
from pathlib import Path

from PySide6.QtCore import (
    QLibraryInfo,
    QLocale,
    QtMsgType,
    QTranslator,
    qInstallMessageHandler,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import markhive
from markhive import APP_AUTHOR, APP_NAME, APP_REPO, APP_VERSION, lang
from markhive.main_window import MainWindow
from markhive.theme import apply_dark_theme


def _qt_message_filter(msg_type, context, message):
    """Фильтр служебных предупреждений сетевой подсистемы."""
    text = str(message)
    category = getattr(context, "category", "") or ""
    if "QSslSocket" in text and "device not open" in text:
        return
    if category.startswith("qt.network.http2") or text.startswith("qt.network.http2:"):
        return
    stream = sys.stdout if msg_type == QtMsgType.QtDebugMsg else sys.stderr
    if stream is not None:
        print(text, file=stream)


qInstallMessageHandler(_qt_message_filter)


def main():
    # Windows: своя иконка в панели задач вместо значка pythonw.
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "darkvayt.markhive.app"
        )
    except (AttributeError, ImportError, OSError):
        pass  # не Windows — пропускаем

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_AUTHOR)
    app.setOrganizationDomain(APP_REPO)

    # иконка программы: заголовок окна, панель задач, Alt+Tab
    icon_path = Path(__file__).parent / "icons" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Системные переводы для выбранного языка (кнопки стандартных диалогов)
    lang_code = lang.current_language()
    if lang_code != "en":
        translator = QTranslator(app)
        paths = [
            QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
            str(Path(markhive.__file__).resolve().parent / "translations"),
        ]
        for path in paths:
            if translator.load(QLocale(lang_code), "qtbase", "_", path):
                app.installTranslator(translator)
                break

    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
