"""Writes packaging/tsabot.ico (the same diamond icon as the tray) for the Windows executable."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from tsabot.desktop import icon_image  # noqa: E402

img = icon_image("stopped", 256)
img.save(HERE / "tsabot.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(HERE / "tsabot.ico")
