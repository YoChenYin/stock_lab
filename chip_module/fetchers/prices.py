"""
chip_module/fetchers/prices.py
抓取每日 OHLCV 並計算量能指標存入 daily_prices。
同時更新機構持倉 institutional_holders。
"""

import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import date, timedelta
from typing import List

from ..db.schema import get_conn


# ── 技術指標計算 ──────────────────────────────────────────────────

def calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def calc_cmf(high, low, close, volume, period=20) -> pd.Series:
    mfm = ((close - low) - (high - close)) / (high - low + 1e-9)
    mfv = mfm * volume
    return mfv.rolling(period).sum() / volume.rolling(period).sum()


def calc_mfi(high, low, close, volume, period=14) -> pd.Series:
    tp = (high + low + close) / 3
    raw_mf = tp * volume
    pos = raw_mf.where(tp > tp.shift(1), 0)
    neg = raw_mf.where(tp < tp.shift(1), 0)
    mfr = pos.rolling(period).sum() / (neg.rolling(period).sum() + 1e-9)
    return 100 - (100 / (1 + mfr))


# ── 主要 fetcher ──────────────────────────────────────────────────

def fetch_prices(tickers: List[str], lookback_days: int = 60, db_path=None):
    """
    下載最近 N 天的 OHLCV，計算技術指標後 upsert 進 daily_prices。
    lookback_days 預設 60 天，確保指標計算有足夠的歷史窗口。
    """
    conn = get_conn(db_path) if db_path else get_conn()
    start = (date.today() - timedelta(days=lookback_days)).isoformat()

    for ticker in tickers:
        try:
            df = yf.download(ticker, start=start, progress=False, auto_adjust=True)
            if df.empty:
                print(f"[prices] {ticker}: 無資料，跳過")
                continue

            # yfinance 新版回傳 MultiIndex columns，例如 ("Close", "NVDA")
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()

            # 技術指標
            obv = calc_obv(df["close"], df["volume"])
            df["obv"]       = obv
            df["obv_signal"]= obv.ewm(span=20).mean()
            df["cmf_20"]    = calc_cmf(df["high"], df["low"], df["close"], df["volume"])
            df["mfi_14"]    = calc_mfi(df["high"], df["low"], df["close"], df["volume"])
            df["avg_vol_20"]= df["volume"].rolling(20).mean()
            df["vol_ratio"] = df["volume"] / (df["avg_vol_20"] + 1e-9)

            # 只存最新 lookback_days 天（避免重複寫太多舊資料）
            rows = []
            for dt, row in df.iterrows():
                rows.append((
                    ticker,
                    dt.strftime("%Y-%m-%d"),
                    _f(row, "open"), _f(row, "high"), _f(row, "low"),
                    _f(row, "close"), _i(row, "volume"),
                    _f(row, "obv"), _f(row, "obv_signal"),
                    _f(row, "cmf_20"), _f(row, "mfi_14"),
                    _f(row, "avg_vol_20"), _f(row, "vol_ratio"),
                ))

            conn.executemany("""
                INSERT INTO daily_prices
                    (ticker, date, open, high, low, close, volume,
                     obv, obv_signal, cmf_20, mfi_14, avg_vol_20, vol_ratio)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticker, date) DO UPDATE SET
                    close=excluded.close, volume=excluded.volume,
                    obv=excluded.obv, obv_signal=excluded.obv_signal,
                    cmf_20=excluded.cmf_20, mfi_14=excluded.mfi_14,
                    avg_vol_20=excluded.avg_vol_20, vol_ratio=excluded.vol_ratio
            """, rows)
            conn.commit()
            print(f"[prices] {ticker}: {len(rows)} 筆 upserted")

        except Exception as e:
            print(f"[prices] {ticker} 失敗: {e}")

    conn.close()


def fetch_institutional(tickers: List[str], db_path=None):
    """
    從 yfinance 抓機構持倉，存入 institutional_holders。
    季度資料，建議每週跑一次即可。
    """
    conn = get_conn(db_path) if db_path else get_conn()
    today = date.today().isoformat()

    for ticker in tickers:
        try:
            tk = yf.Ticker(ticker)
            holders = tk.institutional_holders
            if holders is None or holders.empty:
                continue

            rows = []
            for _, row in holders.iterrows():
                rows.append((
                    ticker, today,
                    str(row.get("Holder", "")),
                    _safe(row.get("Shares")),
                    _safe(row.get("% Out")),
                    _safe(row.get("Value")),
                ))

            conn.executemany("""
                INSERT INTO institutional_holders
                    (ticker, report_date, institution, shares_held, pct_out, value_usd)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(ticker, report_date, institution) DO UPDATE SET
                    shares_held=excluded.shares_held,
                    pct_out=excluded.pct_out,
                    value_usd=excluded.value_usd
            """, rows)
            conn.commit()
            print(f"[institutional] {ticker}: {len(rows)} 筆 upserted")

        except Exception as e:
            print(f"[institutional] {ticker} 失敗: {e}")

    conn.close()


# ── helpers ───────────────────────────────────────────────────────

def _f(row, col):
    v = row.get(col)
    return float(v) if pd.notna(v) else None

def _i(row, col):
    v = row.get(col)
    return int(v) if pd.notna(v) else None

def _safe(v):
    try:
        return float(v) if pd.notna(v) else None
    except Exception:
        return None
