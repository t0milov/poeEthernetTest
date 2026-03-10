@echo off
setlocal

py -m PyInstaller --noconfirm --onefile --windowed --name tkinter_app main.py
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Build completed. EXE is in dist\tkinter_app.exe
