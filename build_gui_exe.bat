@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo  Build do Conversor GIV Web
echo ===============================================
echo.

python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo PyInstaller nao encontrado. Instalando...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo [ERRO] Nao foi possivel instalar o PyInstaller.
        pause
        exit /b 1
    )
)

python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name ConversorGIVWeb ^
    --hidden-import converter ^
    --hidden-import pyodbc ^
    --hidden-import pg8000 ^
    --hidden-import requests ^
    converter_gui.py

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao gerar o executavel.
    pause
    exit /b 1
)

echo.
echo [OK] Executavel gerado em dist\ConversorGIVWeb.exe
pause
