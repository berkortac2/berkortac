"""Write pine/TradingviewSignalAgent_Scanner5m.pine from the bot/Pine 5m model.

python scripts/11_export_scanner.py [--model bot/model/model_5m.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.export.pine_scanner import generate_scanner  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "bot" / "model" / "model_5m.json"))
    a = ap.parse_args()
    model = json.loads(Path(a.model).read_text())
    out = ROOT / "pine" / "TradingviewSignalAgent_Scanner5m.pine"
    out.write_text(generate_scanner(model))
    print("written", out, len(out.read_text().splitlines()), "lines")


if __name__ == "__main__":
    main()
