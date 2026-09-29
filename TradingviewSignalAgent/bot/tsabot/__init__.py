"""TSA Bot - Binance USDT-M futures trading bot driven by the Tradingview Signal Agent 5m model.

Safety invariants (enforced in code and tests):
* only USDT-margined perpetual symbols (XXXUSDT) are traded;
* the exchange client can only call an explicit allow-list of endpoints; wallet
  endpoints (withdraw, transfer, deposit, ...) are unreachable;
* the API secret never leaves this process: it is never returned by the web API,
  never logged, and stored only encrypted (or read from environment variables);
* total margin in use never exceeds the user's budget.
"""
import sys
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BOT_ROOT.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

__version__ = "1.0.0"
