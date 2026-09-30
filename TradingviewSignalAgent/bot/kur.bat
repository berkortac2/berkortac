@echo off
rem TSA Bot - Windows kurulumu (kaynak koddan). Python 3.11+ kurulu olmali (python.org, "Add Python to PATH").
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
echo [1/3] Sanal ortam hazirlaniyor...
%PY% -m venv .venv || goto :err
echo [2/3] Gerekli paketler kuruluyor (birkac dakika surebilir)...
.venv\Scripts\python -m pip install --upgrade pip >nul
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-desktop.txt || goto :err
echo [3/3] Masaustu kisayolu olusturuluyor...
.venv\Scripts\python packaging\make_shortcut.py
echo.
echo Kurulum tamam. Masaustundeki "TSA Bot" kisayoluyla ac.
pause
exit /b 0
:err
echo.
echo Kurulum basarisiz. Python 3.11+ kurulu mu? (python.org -> Add Python to PATH)
pause
exit /b 1
