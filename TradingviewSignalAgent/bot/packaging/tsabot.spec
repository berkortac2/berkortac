# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for the Windows desktop app (TSABot.exe, one folder, no console window).
# Build (from the TradingviewSignalAgent folder):
#   python bot/packaging/make_icon.py
#   pyinstaller --noconfirm --clean bot/packaging/tsabot.spec --distpath dist --workpath build
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))      # TradingviewSignalAgent
BOT = os.path.join(ROOT, "bot")
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(BOT, "packaging", "tsabot.ico")

a = Analysis(
    [os.path.join(BOT, "TSABot.pyw")],
    pathex=[BOT, SRC],
    datas=[(os.path.join(BOT, "web"), "web"), (os.path.join(BOT, "model"), "model")],
    hiddenimports=[
        "tsa.features.registry", "tsa.indicators.pine_ta",
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
        "pystray._win32", "PIL._tkinter_finder",
    ],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "playwright", "optuna", "lightgbm", "sklearn",
              "scipy", "notebook", "jupyter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TSABot",
    console=False,                      # a real desktop app: no black console window
    icon=ICON if os.path.exists(ICON) else None,
    version=None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TSABot", upx=False)
