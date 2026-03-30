"""
chip_module/fetchers/short_interest.py
空頭興趣資料 — 從 yfinance 直接取得，每日可用。
FINRA CDN 已停用舊路徑，yfinance 封裝的 short data 是最穩定的免費替代方案。
"""

import yfinance as yf
import pandas as pd
from datetime import date
from typing import List

from ..db.schema import get_conn


def fetch_short_interest(tickers: List[str], db_path=None):
    """
    從 yfinance 抓取 short interest 相關指標：
    - shortPercentOfFloat  → short_float_pct
    - shortRatio           → days_to_cover (days to cover)
    - sharesShort          → short_volume (proxy)
    
    yfinance 的資料來自 NASDAQ/Yahoo，約每兩週更新一次，與 FINRA 節奏相近。
    """
    conn = get_conn(db_path) if db_path else get_conn()
    today = date.today().isoformat()

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info

            short_vol       = info.get("sharesShort")
            avg_vol         = info.get("averageDailyVolume10Day") or info.get("averageVolume")
            short_float_pct = info.get("shortPercentOfFloat")
            days_to_cover   = info.get("shortRatio")  # Yahoo 直接提供

            # shortPercentOfFloat 從小數轉百分比（0.035 → 3.5）
            if short_float_pct and short_float_pct < 1:
                short_float_pct = round(short_float_pct * 100, 4)

            conn.execute("""
                INSERT INTO short_interest
                    (ticker, settlement_date, short_volume, avg_daily_vol,
                     short_float_pct, days_to_cover)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(ticker, settlement_date) DO UPDATE SET
                    short_volume=excluded.short_volume,
                    avg_daily_vol=excluded.avg_daily_vol,
                    short_float_pct=excluded.short_float_pct,
                    days_to_cover=excluded.days_to_cover
            """, (ticker, today, short_vol, avg_vol, short_float_pct, days_to_cover))

            _update_change_pct(conn, ticker, today)
            conn.commit()

            sf  = f"{short_float_pct:.2f}%" if short_float_pct else "N/A"
            dtc = f"{days_to_cover:.1f}天"  if days_to_cover   else "N/A"
            print(f"[short] {ticker}: Short Float {sf} | DTC {dtc}")

        except Exception as e:
            print(f"[short] {ticker} 失敗: {e}")

    conn.close()


def _update_change_pct(conn, ticker: str, current_date: str):
    """計算與上一筆相比的空頭量變化率"""
    rows = conn.execute("""
        SELECT settlement_date, short_volume FROM short_interest
        WHERE ticker=? ORDER BY settlement_date DESC LIMIT 2
    """, (ticker,)).fetchall()

    if len(rows) == 2:
        curr, prev = rows[0]["short_volume"], rows[1]["short_volume"]
        if prev and prev > 0 and curr:
            chg = round((curr - prev) / prev * 100, 2)
            conn.execute("""
                UPDATE short_interest SET prev_short_vol=?, chg_pct=?
                WHERE ticker=? AND settlement_date=?
            """, (prev, chg, ticker, current_date))
