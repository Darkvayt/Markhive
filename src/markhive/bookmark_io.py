# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Darkvayt

# Чтение, слияние и запись файлов экспорта закладок (формат Netscape HTML)
from __future__ import annotations

import html
import time
from html.parser import HTMLParser
from pathlib import Path

from markhive.lang import tr
from markhive.models import Bookmark, Folder, fix_parents

# Заголовок файла экспорта закладок
FILE_HEADER = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file.
It will be read and overwritten.
DO NOT EDIT! -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>"""


# Потоковый парсер формата экспорта закладок браузера
class _NetscapeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root_folders: list[Folder] = []
        self.root_bookmarks: list[Bookmark] = []
        self._stack: list[Folder] = []  # Стек вложенности папок
        self._pending: Folder | None = None  # Папка, ожидающая открытия
        self._current: Bookmark | None = None  # Текущая закладка
        self._buf: list[str] = []  # Буфер текстового содержимого

    # Обработка открывающих тегов
    def handle_starttag(self, tag, attrs):
        # Сбрасываем незавершённую папку, если встретили не <dl>
        if tag != "dl" and self._pending is not None:
            self._pending = None

        if tag == "h3":
            # Начало новой папки
            folder = Folder(name="")
            container = self._stack[-1].folders if self._stack else self.root_folders
            folder.parent = self._stack[-1] if self._stack else None
            container.append(folder)
            self._pending = folder
            self._buf = []
        elif tag == "a":
            # Новая закладка
            attrs = dict(attrs)
            href = attrs.get("href", "")
            if href:
                bookmark = Bookmark(url=href, add_date=attrs.get("add_date", ""))
                bookmark.parent = self._stack[-1] if self._stack else None
                (
                    self._stack[-1].bookmarks if self._stack else self.root_bookmarks
                ).append(bookmark)
                self._current = bookmark
                self._buf = []
        elif tag == "dl" and self._pending is not None:
            # Вход в содержимое папки
            self._stack.append(self._pending)
            self._pending = None

    # Обработка закрывающих тегов
    def handle_endtag(self, tag):
        if tag == "h3" and self._pending is not None:
            # Завершение имени папки
            self._pending.name = "".join(self._buf).strip() or tr(
                "parser.folder_unnamed"
            )
        elif tag == "a" and self._current is not None:
            # Завершение закладки
            self._current.title = "".join(self._buf).strip()
            self._current._invalidate_norm()
            self._current = None
        elif tag == "dl" and self._stack:
            # Выход из папки
            self._stack.pop()

    # Сбор текстового содержимого тегов
    def handle_data(self, data):
        if self._current is not None or self._pending is not None:
            self._buf.append(data)


# --- Публичный API ---


# Чтение одного HTML-файла экспорта потоково
def load_bookmarks(path: Path) -> tuple[list[Folder], list[Bookmark]]:
    parser = _NetscapeParser()
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        while True:
            # Чтение по 1 МБ для экономии памяти
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            parser.feed(chunk)
    parser.close()
    fix_parents(parser.root_folders, parser.root_bookmarks)
    return parser.root_folders, parser.root_bookmarks


# Рекурсивное слияние папок верхнего уровня по имени
def _merge_folder_lists(base: list[Folder], incoming: list[Folder]) -> None:
    index = {f.name: f for f in base}
    for folder in incoming:
        target = index.get(folder.name)
        if target is None:
            base.append(folder)
            index[folder.name] = folder
        else:
            _merge_folder_lists(target.folders, folder.folders)
            target.bookmarks.extend(folder.bookmarks)


# Объединение нескольких файлов экспорта в одну коллекцию
def merge_collections(base_folders, base_bookmarks, add_folders, add_bookmarks):
    _merge_folder_lists(base_folders, add_folders)
    base_bookmarks.extend(add_bookmarks)


# Запись коллекции в файл формата, совместимого с импортом браузера
def save_bookmarks(
    path: Path, folders: list[Folder], bookmarks: list[Bookmark]
) -> None:
    now = str(int(time.time()))
    phase_open = 0
    phase_close = 1

    with Path(path).open("w", encoding="utf-8", newline="\n") as out:
        out.write(FILE_HEADER.rstrip("\n"))
        out.write("\n<DL><p>\n")

        # Запись одной строки с переводом строки
        def write_line(line: str) -> None:
            out.write(line)
            out.write("\n")

        # Запись одной закладки в формате <DT><A>
        def emit_bookmark(b: Bookmark, indent: int) -> None:
            pad = "    " * indent
            title = html.escape(b.title or b.url)
            href = html.escape(b.url, quote=True)
            add = f' ADD_DATE="{b.add_date}"' if b.add_date else ""
            write_line(f'{pad}<DT><A HREF="{href}"{add}>{title}</A>')

        # Стек для итеративного обхода дерева папок
        stack: list[tuple[object, int, int]] = []
        stack.extend((folder, 1, phase_open) for folder in reversed(folders))

        while stack:
            obj, indent, phase = stack.pop()

            # Закрытие папки
            if phase == phase_close:
                pad = "    " * indent
                write_line(f"{pad}</DL><p>")
                continue

            # Запись закладки
            if isinstance(obj, Bookmark):
                emit_bookmark(obj, indent)
                continue

            # Открытие папки и добавление содержимого в стек
            folder = obj
            pad = "    " * indent
            write_line(
                f'{pad}<DT><H3 ADD_DATE="{now}" LAST_MODIFIED="{now}">'
                f"{html.escape(folder.name)}</H3>"
            )
            write_line(f"{pad}<DL><p>")
            stack.append((folder, indent, phase_close))
            stack.extend(
                (b, indent + 1, phase_open) for b in reversed(folder.bookmarks)
            )
            stack.extend(
                (child, indent + 1, phase_open) for child in reversed(folder.folders)
            )

        # Закладки без папки
        for b in bookmarks:
            emit_bookmark(b, 1)
        out.write("</DL><p>\n")
