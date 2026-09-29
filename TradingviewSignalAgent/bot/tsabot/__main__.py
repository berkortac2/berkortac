"""python -m tsabot  ->  starts the local UI at http://127.0.0.1:8765"""
from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .api import create_app
from .secrets import install_redaction


def main():
    ap = argparse.ArgumentParser(description="TSA Bot (Binance USDT-M futures)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if a.host not in ("127.0.0.1", "localhost"):
        sys.exit("Güvenlik: arayüz yalnızca 127.0.0.1 üzerinde çalışır. Uzak erişim için SSH tüneli kullanın.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    install_redaction()
    app = create_app()
    st = app.state.tsa
    url = f"http://127.0.0.1:{a.port}/"
    if st.setup_token:
        print(f"\n  İlk kurulum: tarayıcıda şu adresi açın ve bir şifre belirleyin:\n  {url}#setup={st.setup_token}\n")
    else:
        print(f"\n  TSA Bot arayüzü: {url}\n")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning", server_header=False)


if __name__ == "__main__":
    main()
