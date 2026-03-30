"""
chip_module/fetch_universe.py — 抓取美股股票池並存成 JSON

來源：
  - S&P 500  from Wikipedia（503 支，含 GICS 板塊）
  - Nasdaq-100 from Wikipedia（100 支，部分重疊）

輸出：chip_module/us_universe.json
格式：
  {
    "AAPL": {"name": "Apple Inc.", "sector": "Information Technology", "index": "SP500+NDX100"},
    "WMT":  {"name": "Walmart Inc.", "sector": "Consumer Staples", "index": "SP500"},
    ...
  }

執行方式（在 stock_track/ 目錄）：
  python -m chip_module.fetch_universe
"""

import io
import json
from pathlib import Path

import pandas as pd
import requests

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
OUT_PATH = Path(__file__).parent / "us_universe.json"


_HEADERS = {"User-Agent": "Mozilla/5.0 StockLab research@stocklab.app"}


def _normalise_ticker(t: str) -> str:
    """Replace dots with hyphens to match yfinance convention (BRK.B → BRK-B)."""
    return str(t).strip().replace(".", "-")


def _read_html(url: str) -> list:
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text), header=0)


def fetch_sp500() -> dict:
    print("[SP500] 抓取 Wikipedia S&P 500 名單...")
    tables = _read_html(SP500_URL)
    df = tables[0]
    # Expected columns: Symbol, Security, GICS Sector, ...
    result = {}
    for _, row in df.iterrows():
        ticker = _normalise_ticker(row.get("Symbol") or row.get("Ticker", ""))
        name   = str(row.get("Security") or row.get("Company", "")).strip()
        sector = str(row.get("GICS Sector", "Unknown")).strip()
        if ticker:
            result[ticker] = {"name": name, "sector": sector, "index": "SP500"}
    print(f"[SP500] 取得 {len(result)} 支")
    return result


def fetch_ndx100() -> dict:
    print("[NDX100] 抓取 Wikipedia Nasdaq-100 名單...")
    tables = _read_html(NDX100_URL)
    df = None
    # The Nasdaq-100 page table containing tickers — find by column name
    for t in tables:
        cols = [str(c).strip() for c in t.columns]
        if any(c in ("Ticker", "Symbol") for c in cols):
            df = t
            break
    if df is None:
        print("[NDX100] 找不到 Ticker 欄位，跳過")
        return {}

    cols = [str(c).strip() for c in df.columns]
    ticker_col = next(c for c in cols if c in ("Ticker", "Symbol"))
    name_col   = next((c for c in cols if c in ("Company", "Security", "Name")), None)
    sector_col = next((c for c in cols if "Sector" in c), None)

    result = {}
    for _, row in df.iterrows():
        ticker = _normalise_ticker(row.get(ticker_col, ""))
        name   = str(row.get(name_col, "")).strip() if name_col else ""
        sector = str(row.get(sector_col, "Unknown")).strip() if sector_col else "Unknown"
        if ticker:
            result[ticker] = {"name": name, "sector": sector, "index": "NDX100"}
    print(f"[NDX100] 取得 {len(result)} 支")
    return result


def build_universe() -> dict:
    sp500  = fetch_sp500()
    ndx100 = fetch_ndx100()

    universe = sp500.copy()
    overlap = 0
    ndx_only = 0
    for ticker, info in ndx100.items():
        if ticker in universe:
            universe[ticker]["index"] = "SP500+NDX100"
            overlap += 1
        else:
            universe[ticker] = info
            ndx_only += 1

    print(
        f"\n[Universe] 合計 {len(universe)} 支\n"
        f"  SP500 only : {len(sp500) - overlap}\n"
        f"  NDX100 only: {ndx_only}\n"
        f"  重疊       : {overlap}"
    )
    return universe


def save_universe(universe: dict) -> None:
    OUT_PATH.write_text(
        json.dumps(universe, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"[Universe] 已儲存至 {OUT_PATH}")

    # Print sector breakdown
    from collections import Counter
    sector_counts = Counter(v["sector"] for v in universe.values())
    print("\n板塊分佈：")
    for sector, cnt in sorted(sector_counts.items(), key=lambda x: -x[1]):
        print(f"  {sector:<35} {cnt:>4} 支")


if __name__ == "__main__":
    universe = build_universe()
    save_universe(universe)
