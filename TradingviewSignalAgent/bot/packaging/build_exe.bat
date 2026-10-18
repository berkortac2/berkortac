@echo off
rem TSA Bot - TSABot.exe'yi bu bilgisayarda derler (internet uzerinden derleme gerekmez).
rem Once kur.bat calistirilmis olmali. Sonuc: bot\dist\TSABot\TSABot.exe
chcp 65001 >nul
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo Once kur.bat'i calistir.
  pause
  exit /b 1
)
echo [1/4] PyInstaller kuruluyor...
.venv\Scripts\python -m pip install pyinstaller >nul || goto :err
echo [2/4] Simge hazirlaniyor...
.venv\Scripts\python packaging\make_icon.py || goto :err
echo [3/4] TSABot.exe derleniyor (birkac dakika surer)...
.venv\Scripts\python -m PyInstaller --noconfirm --clean packaging\tsabot.spec --distpath dist --workpath build || goto :err
echo [4/4] Derlenen program kendini test ediyor...
start "" /wait dist\TSABot\TSABot.exe --selftest --data-dir "%TEMP%\tsabot-selftest"
if errorlevel 1 goto :err
type "%TEMP%\tsabot-selftest\selftest.json"
echo.
echo Tamam: dist\TSABot klasoru hazir. Calisan TSA Bot'u tepsiden "Tamamen kapat", sonra bu klasoru
echo C:\TSABot uzerine kopyala. Ayarlar ve gecmis %%LOCALAPPDATA%%\TSABot icinde durur, kaybolmaz.
pause
exit /b 0
:err
echo.
echo Derleme / test basarisiz. Yukaridaki hatayi Claude'a gonder.
pause
exit /b 1
