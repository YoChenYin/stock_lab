"""
chip_module/fetchers/options_sentiment.py
Put/Call Ratio — 從 yfinance 抓 SPY 選擇權鏈計算，最穩定的免費方案。
SPY 是美股流動性最高的 ETF，其 P/C Ratio 是業界公認的市場情緒代理指標。
"""

import yfinance as yf
import pandas as pd
from datetime import date
from typing import Optional

from ..db.schema import get_conn


def fetch_options_sentiment(db_path=None):
    """
    從 SPY 選擇權鏈計算 Put/Call Ratio（以成交量為準）。
    抓近兩個到期日的選擇權，加總計算整體 P/C Ratio。
    """
    conn  = get_conn(db_path) if db_path else get_conn()
    today = date.today().isoformat()

    try:
        spy   = yf.Ticker("SPY")
        exps  = spy.options          # 所有到期日清單

        if not exps:
            print("[options] SPY 無選擇權資料")
            conn.close()
            return

        # 取最近兩個到期日，加總成交量
        total_put_vol  = 0
        total_call_vol = 0

        for exp in exps[:2]:
            chain = spy.option_chain(exp)

            call_vol = chain.calls["volume"].fillna(0).sum()
            put_vol  = chain.puts["volume"].fillna(0).sum()

            total_call_vol += call_vol
            total_put_vol  += put_vol

        if total_call_vol == 0:
            print("[options] Call 成交量為 0，跳過")
            conn.close()
            return

        pc_ratio = round(total_put_vol / total_call_vol, 4)

        conn.execute("""
            INSERT INTO options_sentiment (date, scope, pc_ratio, pc_ma10, pc_ma20, pc_zscore_20)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(date, scope) DO UPDATE SET pc_ratio=excluded.pc_ratio
        """, (today, "equity", pc_ratio, None, None, None))
        conn.commit()

        # 重算 MA 和 Z-Score
        _recalc_stats(conn, "equity")

        sentiment = "偏多" if pc_ratio < 0.7 else "偏空" if pc_ratio > 1.0 else "中性"
        print(f"[options] SPY P/C={pc_ratio:.3f}（{sentiment}），"
              f"Put={int(total_put_vol):,} / Call={int(total_call_vol):,}")

    except Exception as e:
        print(f"[options] 失敗: {e}")

    conn.close()


def _recalc_stats(conn, scope: str):
    rows = conn.execute("""
        SELECT date, pc_ratio FROM options_sentiment
        WHERE scope=? ORDER BY date
    """, (scope,)).fetchall()

    if len(rows) < 2:
        return

    dates  = [r["date"]     for r in rows]
    ratios = [r["pc_ratio"] for r in rows]
    s      = pd.Series(ratios, index=dates)

    ma10  = s.rolling(10).mean()
    ma20  = s.rolling(20).mean()
    std20 = s.rolling(20).std()
    zsc   = ((s - ma20) / (std20 + 1e-9)).round(4)

    for i, dt in enumerate(dates):
        conn.execute("""
            UPDATE options_sentiment
            SET pc_ma10=?, pc_ma20=?, pc_zscore_20=?
            WHERE date=? AND scope=?
        """, (
            round(ma10.iloc[i], 4) if pd.notna(ma10.iloc[i]) else None,
            round(ma20.iloc[i], 4) if pd.notna(ma20.iloc[i]) else None,
            round(zsc.iloc[i],  4) if pd.notna(zsc.iloc[i])  else None,
            dt, scope,
        ))
    conn.commit()


def get_latest_pc(scope: str = "equity", db_path=None) -> Optional[dict]:
    conn = get_conn(db_path) if db_path else get_conn()
    row  = conn.execute("""
        SELECT * FROM options_sentiment WHERE scope=? ORDER BY date DESC LIMIT 1
    """, (scope,)).fetchone()
    conn.close()
    return dict(row) if row else None
