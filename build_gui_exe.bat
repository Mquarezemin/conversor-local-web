@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo  Build do Conversor GIV Web
echo ===============================================
echo.

rem O DSN do GIV (SQL Anywhere) e 32 bits: o executavel PRECISA ser 32 bits.
rem Um build com Python 64 bits gera IM014 "incompatibilidade de arquiteturas".
set "PY32=py -3.14-32"
%PY32% -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==4 else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python 32 bits nao encontrado. Instale-o e ajuste PY32 neste arquivo.
    pause
    exit /b 1
)
echo [OK] Usando Python 32 bits para o build.

%PY32% -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo PyInstaller nao encontrado. Instalando...
    %PY32% -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo [ERRO] Nao foi possivel instalar o PyInstaller.
        pause
        exit /b 1
    )
)

%PY32% -m PyInstaller ^
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
