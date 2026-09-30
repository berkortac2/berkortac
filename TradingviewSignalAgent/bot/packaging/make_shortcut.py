"""Creates the 'TSA Bot' desktop shortcut for a source installation (used by kur.bat)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tsabot import winsys  # noqa: E402

ok = winsys.create_shortcut(winsys.launch_command(minimized=False))
print("Masaüstü kısayolu oluşturuldu." if ok else "Kısayol oluşturulamadı (TSABot.pyw'yi çift tıklayarak da açabilirsin).")
