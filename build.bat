@echo off
setlocal

rem Check if Python launcher is available
where py >nul 2>&1
if errorlevel 1 (
  echo Python is not installed or not in PATH.
  echo Skipping cleanup and build.
  exit /b 1
)

rem Remove previous build artifacts (only if Python is available)
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

py -m PyInstaller --noconfirm --onefile --windowed --name tkinter_app main.py
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Build completed. EXE is in dist\tkinter_app.exe
