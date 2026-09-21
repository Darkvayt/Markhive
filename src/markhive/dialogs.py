# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Диалоговые окна: закладка, горячие клавиши, о программе
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QTextBrowser,
    QVBoxLayout,
)

from markhive import APP_AUTHOR, APP_NAME, APP_REPO, APP_VERSION
from markhive.lang import tr, tr_list

# Полный перечень горячих клавиш: комбинация, ключ перевода
HOTKEYS: list[tuple[str, str]] = [
    ("Ctrl + O", "hotkey.open_file"),
    ("Ctrl + Shift + O", "hotkey.open_folder"),
    ("Ctrl + S", "hotkey.save"),
    ("Ctrl + Shift + S", "hotkey.save_as"),
    ("Ctrl + Q", "hotkey.exit"),
    ("Ctrl + Shift + G", "hotkey.new_group"),
    ("Ctrl + Alt + G", "hotkey.new_subgroup"),
    ("Ctrl + N", "hotkey.new_bookmark"),
    ("F2", "hotkey.rename_group"),
    ("Ctrl + E", "hotkey.edit_bookmark"),
    ("Ctrl + Shift + Del", "hotkey.delete_group"),
    ("Delete", "hotkey.delete_bookmarks"),
    ("Ctrl + X", "hotkey.cut_groups"),
    ("Ctrl + V", "hotkey.paste_groups"),
    ("Ctrl + +", "hotkey.expand_all"),
    ("Ctrl + -", "hotkey.collapse_all"),
    ("Ctrl + T", "hotkey.sort_bookmarks"),
    ("Ctrl + Shift + T", "hotkey.sort_groups"),
    ("Ctrl + G", "hotkey.auto_group"),
    ("Ctrl + Shift + U", "hotkey.ungroup_singles"),
    ("Ctrl + Alt + U", "hotkey.ungroup_all"),
    ("Ctrl + D", "hotkey.dedup_urls"),
    ("Ctrl + Shift + D", "hotkey.dedup_titles"),
    ("Ctrl + Alt + D", "hotkey.group_duplicates"),
    ("Ctrl + U", "hotkey.check_urls"),
    ("Ctrl + Shift + P", "hotkey.remove_empty"),
    ("Ctrl + F", "hotkey.search"),
    ("Esc", "hotkey.escape"),
    ("F1", "hotkey.hotkeys"),
]


# Диалог создания и редактирования закладки
class BookmarkDialog(QDialog):
    def __init__(self, parent=None, title: str | None = None, name="", url=""):
        super().__init__(parent)
        self.setWindowTitle(title or tr("dialog.bookmark.new_title"))
        self.setMinimumWidth(440)

        layout = QFormLayout(self)
        self.name_edit = QLineEdit(name)
        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://example.com/page")
        # Сброс красной рамки при вводе
        self.url_edit.textChanged.connect(lambda _text: self.url_edit.setStyleSheet(""))

        layout.addRow(tr("dialog.bookmark.name_label"), self.name_edit)
        layout.addRow(tr("dialog.bookmark.url_label"), self.url_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("common.ok"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            tr("common.cancel")
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.adjustSize()
        self.setFixedSize(self.size())

    # Валидация: адрес обязателен; при ошибке - красная рамка
    def _validate_and_accept(self):
        if not self.url_edit.text().strip():
            self.url_edit.setStyleSheet("border-color:#e05252;")
            self.url_edit.setFocus()
            return
        self.accept()

    # Возврат пары: название, адрес
    def result_data(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.url_edit.text().strip()


# Диалог со списком всех горячих клавиш
class HotkeysDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog.hotkeys.title"))
        self.setFixedSize(460, 560)

        view = QTextBrowser()
        rows = "\n".join(
            f'<tr><td><span style="color:#2f7ff7; font-weight:600;">{seq}</span></td>'
            f'<td><span style="color:#8f97a6;">{tr(desc)}</td></tr>'
            for seq, desc in HOTKEYS
        )
        view.setHtml(
            "<table cellpadding='3' cellspacing='0'>"
            f"<tr><th style='text-align:left'>{tr('dialog.hotkeys.combo')}</th>"
            f"<th>{tr('dialog.hotkeys.action')}</th></tr>"
            f"{rows}</table>"
        )

        layout = QVBoxLayout(self)
        layout.addWidget(view)


# Диалог "О программе"
class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog.about.title", app_name=APP_NAME))
        self.setFixedSize(560, 480)

        view = QTextBrowser()
        view.setOpenExternalLinks(True)

        # Список возможностей приложения
        features = "".join(
            f'<p style="margin-left:6px; margin-top:0px; margin-bottom:2px;">'
            f'<span style="color:#2f7ff7;">&bull;</span>&nbsp;'
            f'<span style="color:#8f97a6;">{item}</span></p>'
            for item in tr_list("dialog.about.features")
        )

        view.setHtml(f"""
        <h2>{APP_NAME} <span style="color:#8f97a6;">v{APP_VERSION}</span></h2>
        <p style="color:#8f97a6;">{tr("dialog.about.description")}</p>
        <h3>{tr("dialog.about.features_title")}</h3> {features}
        <p><b>{tr("dialog.about.author")}:</b> <span style="color:#2f7ff7;">
        {APP_AUTHOR}</span><br>
        <b>{tr("dialog.about.license")}:</b> <span style="color:#8f97a6;">MIT
        </span><br>
        <b>{tr("dialog.about.source")}:</b> <a href="{APP_REPO}" style="color:#8f97a6;">
        {APP_REPO}</a></p>
        """)

        layout = QVBoxLayout(self)
        layout.addWidget(view)
