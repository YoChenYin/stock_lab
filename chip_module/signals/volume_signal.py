"""
signals/volume_signal.py
OBV 背離 + CMF + MFI 合成量能分數 → 0-100

子分數（加權平均）：
- obv_score  (40%): OBV-signal 斜率 vs 價格斜率之背離
  - OBV 上漲但價格橫盤/下跌 → 主力吸籌，分數高
  - OBV 下跌但價格橫盤/上漲 → 主力出貨，分數低
- cmf_score  (35%): CMF [-1,+1] 線性映射 [0,100]
- mfi_score  (25%): MFI 直接使用，極端區不反轉（本模組偏量能，非逆向）
"""

from datetime import date
from ..db.schema import get_conn


def score(ticker: str, as_of: str = None, n: int = 20, db_path=None) -> float:
    if as_of is None:
        as_of = date.today().isoformat()

    conn = get_conn(db_path) if db_path else get_conn()
    rows = conn.execute("""
        SELECT close, obv_signal, cmf_20, mfi_14
        FROM daily_prices
        WHERE ticker=? AND date <= ? AND close IS NOT NULL
        ORDER BY date DESC LIMIT ?
    """, (ticker, as_of, n)).fetchall()
    conn.close()

    if len(rows) < 5:
        return 50.0

    rows = list(reversed(rows))  # 由舊到新

    prices     = [r["close"]      for r in rows]
    obv_sigs   = [r["obv_signal"] for r in rows]
    cmf_latest = rows[-1]["cmf_20"]
    mfi_latest = rows[-1]["mfi_14"]

    obv_s = _obv_divergence_score(prices, obv_sigs)
    cmf_s = ((cmf_latest + 1) / 2 * 100) if cmf_latest is not None else 50.0
    mfi_s = mfi_latest if mfi_latest is not None else 50.0

    return round(0.40 * obv_s + 0.35 * cmf_s + 0.25 * mfi_s, 2)


def _obv_divergence_score(prices: list, obv_signals: list) -> float:
    """
    計算 OBV-signal 相對於價格的背離程度 → 0-100

    用最近 10 個點的斜率（百分比變化）：
    - divergence = obv_slope - price_slope
    - 正背離（OBV 跑贏價格）→ 分數 > 50
    - 負背離（OBV 跑輸價格）→ 分數 < 50
    """
    valid = [(p, o) for p, o in zip(prices, obv_signals) if o is not None]
    if len(valid) < 5:
        return 50.0

    n = min(len(valid), 10)
    p_start, o_start = valid[-n]
    p_end,   o_end   = valid[-1]

    price_slope = (p_end - p_start) / (abs(p_start) + 1e-9)
    obv_slope   = (o_end - o_start) / (abs(o_start) + 1e-9)

    # 壓縮到 [-1, 1]
    price_slope = max(-1.0, min(1.0, price_slope * 5))
    obv_slope   = max(-1.0, min(1.0, obv_slope   * 5))

    divergence = obv_slope - price_slope  # 範圍約 [-2, 2]
    return round(min(100.0, max(0.0, 50 + divergence * 25)), 2)
