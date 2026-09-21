@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
set PYTHONPATH=src

rem ============ версия из __init__.py (единственный источник) ============
echo [INFO] Read the version...
set "RAW="
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"APP_VERSION" src\markhive\__init__.py') do set "RAW=%%a"
set "RAW=!RAW: =!"
set "VERSION=!RAW:"=!"
if not defined VERSION (
    echo [ERROR] APP_VERSION not found in src\markhive\__init__.py
    pause
    exit /b 1
)
echo [INFO] Build Markhive v!VERSION! ...
echo.

rem ============ сборка ============
.venv\Scripts\python.exe -m nuitka ^
  --onefile ^
  --enable-plugin=pyside6 ^
  --include-package=markhive ^
  --include-data-dir=src\markhive\icons=markhive\icons ^
  --include-data-dir=src\markhive\lang=markhive\lang ^
  --include-data-files=.venv\Lib\site-packages\PySide6\translations\qtbase_ru.qm=markhive\translations\qtbase_ru.qm ^
  --include-qt-plugins=sensible ^
  --python-flag=-OO ^
  --lto=yes ^
  --remove-output ^
  --output-dir=dist ^
  --assume-yes-for-downloads ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=src\markhive\icons\app.ico ^
  --company-name=Darkvayt ^
  --product-name=Markhive ^
  --file-description="Markhive — менеджер закладок браузера" ^
  --copyright="Copyright (c) 2026 Darkvayt" ^
  --product-version=!VERSION! ^
  --file-version=!VERSION!.0 ^
  src\markhive\main.py

if errorlevel 1 (
	echo.
    echo [ERROR] the build failed!
    pause
    exit /b 1
)

rem ============ упаковка: exe с версией ============
if exist "dist\Markhive-!VERSION!.exe" del /q "dist\Markhive-!VERSION!.exe"
ren "dist\main.exe" "Markhive-!VERSION!.exe"

echo.
echo [INFO] Successfully: dist\Markhive-!VERSION!.exe
pause
