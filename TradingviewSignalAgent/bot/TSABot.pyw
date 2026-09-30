"""TSA Bot desktop app launcher (double-click, or: pythonw TSABot.pyw). No console window."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsabot.desktop import main  # noqa: E402

sys.exit(main())
