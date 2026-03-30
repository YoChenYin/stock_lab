"""
fetchers/options_flow.py
每日抓取個股選擇權鏈快照，存入 options_flow 表。

每天存一次，隔天才能比較 OI 變化。
建議在收盤後（台灣時間 05:30+ 或排程 23:30）執行。

異常偵測指標：
- volume / OI ratio > 3：當日新開大量倉位（非平倉）
- OTM call surge：行權價 > 當前價 5% 的 call 量異常
- avg_call_iv vs avg_put_iv 偏斜（skew）
"""

import yfinance as yf
import pandas as pd
from datetime import date
from typing import List

from ..db.schema import get_conn

OTM_THRESHOLD = 1.05   # call strike > price * 1.05 才算 OTM
VOL_OI_ALERT  = 3.0    # volume/OI > 此值視為異常
MAX_EXPIRIES  = 4      # 只取最近 N 個到期日（避免太慢）


def fetch_options_flow(tickers: List[str], db_path=None):
    conn  = get_conn(db_path) if db_path else get_conn()
    today = date.today().isoformat()

    for ticker in tickers:
        try:
            tk    = yf.Ticker(ticker)
            price = _get_price(tk)
            if price is None:
                print(f"[options_flow] {ticker}: 無法取得股價，跳過")
                continue

            exps = tk.options
            if not exps:
                print(f"[options_flow] {ticker}: 無選擇權資料")
                continue

            agg = _aggregate(tk, exps[:MAX_EXPIRIES], price)

            conn.execute("""
                INSERT INTO options_flow (
                    ticker, date, underlying_price,
                    call_volume, call_oi,
                    put_volume,  put_oi,
                    otm_call_volume, otm_call_oi,
                    unusual_call_strikes, unusual_put_strikes,
                    max_call_vol_oi_ratio, max_put_vol_oi_ratio,
                    avg_call_iv, avg_put_iv
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticker, date) DO UPDATE SET
                    underlying_price=excluded.underlying_price,
                    call_volume=excluded.call_volume,
                    call_oi=excluded.call_oi,
                    put_volume=excluded.put_volume,
                    put_oi=excluded.put_oi,
                    otm_call_volume=excluded.otm_call_volume,
                    otm_call_oi=excluded.otm_call_oi,
                    unusual_call_strikes=excluded.unusual_call_strikes,
                    unusual_put_strikes=excluded.unusual_put_strikes,
                    max_call_vol_oi_ratio=excluded.max_call_vol_oi_ratio,
                    max_put_vol_oi_ratio=excluded.max_put_vol_oi_ratio,
                    avg_call_iv=excluded.avg_call_iv,
                    avg_put_iv=excluded.avg_put_iv
            """, (
                ticker, today, price,
                agg["call_volume"], agg["call_oi"],
                agg["put_volume"],  agg["put_oi"],
                agg["otm_call_volume"], agg["otm_call_oi"],
                agg["unusual_call_strikes"], agg["unusual_put_strikes"],
                agg["max_call_vol_oi_ratio"], agg["max_put_vol_oi_ratio"],
                agg["avg_call_iv"], agg["avg_put_iv"],
            ))
            conn.commit()

            print(
                f"[options_flow] {ticker:6s} "
                f"C/P vol={agg['call_volume']:,}/{agg['put_volume']:,} "
                f"OTM_call={agg['otm_call_volume']:,} "
                f"異常strikes={agg['unusual_call_strikes']}C/{agg['unusual_put_strikes']}P"
            )

        except Exception as e:
            print(f"[options_flow] {ticker} 失敗: {e}")

    conn.close()


def _get_price(tk) -> float | None:
    try:
        info = tk.fast_info
        return float(info.last_price)
    except Exception:
        try:
            hist = tk.history(period="1d")
            return float(hist["Close"].iloc[-1]) if not hist.empty else None
        except Exception:
            return None


def _aggregate(tk, expiries: list, price: float) -> dict:
    agg = dict(
        call_volume=0, call_oi=0,
        put_volume=0,  put_oi=0,
        otm_call_volume=0, otm_call_oi=0,
        unusual_call_strikes=0, unusual_put_strikes=0,
        max_call_vol_oi_ratio=0.0, max_put_vol_oi_ratio=0.0,
        all_call_iv=[], all_put_iv=[],
    )

    for exp in expiries:
        try:
            chain = tk.option_chain(exp)
        except Exception:
            continue
        _process_calls(chain.calls, price, agg)
        _process_puts(chain.puts, price, agg)

    agg["avg_call_iv"] = _mean(agg.pop("all_call_iv"))
    agg["avg_put_iv"]  = _mean(agg.pop("all_put_iv"))
    return agg


def _process_calls(df: pd.DataFrame, price: float, agg: dict):
    if df.empty:
        return
    df = df.copy()
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df["openInterest"] = pd.to_numeric(df["openInterest"], errors="coerce").fillna(0)
    df["impliedVolatility"] = pd.to_numeric(df["impliedVolatility"], errors="coerce")

    agg["call_volume"] += int(df["volume"].sum())
    agg["call_oi"]     += int(df["openInterest"].sum())

    # OTM calls
    otm = df[df["strike"] > price * OTM_THRESHOLD]
    agg["otm_call_volume"] += int(otm["volume"].sum())
    agg["otm_call_oi"]     += int(otm["openInterest"].sum())

    # 異常大單（vol/OI > threshold，且 OI > 0 排除零成交）
    valid = df[(df["openInterest"] > 0)]
    valid = valid.copy()
    valid["ratio"] = valid["volume"] / valid["openInterest"]
    unusual = valid[valid["ratio"] > VOL_OI_ALERT]
    agg["unusual_call_strikes"] += len(unusual)
    if not unusual.empty:
        agg["max_call_vol_oi_ratio"] = max(
            agg["max_call_vol_oi_ratio"], float(unusual["ratio"].max())
        )

    iv_vals = df["impliedVolatility"].dropna().tolist()
    agg["all_call_iv"].extend(iv_vals)


def _process_puts(df: pd.DataFrame, price: float, agg: dict):
    if df.empty:
        return
    df = df.copy()
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df["openInterest"] = pd.to_numeric(df["openInterest"], errors="coerce").fillna(0)
    df["impliedVolatility"] = pd.to_numeric(df["impliedVolatility"], errors="coerce")

    agg["put_volume"] += int(df["volume"].sum())
    agg["put_oi"]     += int(df["openInterest"].sum())

    valid = df[(df["openInterest"] > 0)].copy()
    valid["ratio"] = valid["volume"] / valid["openInterest"]
    unusual = valid[valid["ratio"] > VOL_OI_ALERT]
    agg["unusual_put_strikes"] += len(unusual)
    if not unusual.empty:
        agg["max_put_vol_oi_ratio"] = max(
            agg["max_put_vol_oi_ratio"], float(unusual["ratio"].max())
        )

    iv_vals = df["impliedVolatility"].dropna().tolist()
    agg["all_put_iv"].extend(iv_vals)


def _mean(lst: list) -> float | None:
    valid = [x for x in lst if x == x]  # filter NaN
    return round(sum(valid) / len(valid), 4) if valid else None
