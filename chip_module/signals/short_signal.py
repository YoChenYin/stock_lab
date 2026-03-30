"""
signals/short_signal.py
空頭比例反向 + 趨勢 → 0-100 分（分數高 = 空頭壓力小 = 偏多）

- base: short_float_pct 越低分數越高（分段線性映射）
- trend: chg_pct 下降（空頭在減少）加分，上升扣分
"""

from datetime import date
from ..db.schema import get_conn


def score(ticker: str, as_of: str = None, db_path=None) -> float:
    if as_of is None:
        as_of = date.today().isoformat()

    conn = get_conn(db_path) if db_path else get_conn()
    row = conn.execute("""
        SELECT short_float_pct, chg_pct
        FROM short_interest
        WHERE ticker=? AND settlement_date <= ?
        ORDER BY settlement_date DESC LIMIT 1
    """, (ticker, as_of)).fetchone()
    conn.close()

    if not row or row["short_float_pct"] is None:
        return 50.0

    pct = row["short_float_pct"]

    # 分段線性映射（空頭比例 → 基礎分）
    if pct <= 3:
        base = 90.0
    elif pct <= 8:
        base = 90 - (pct - 3) / 5 * 20    # 90 → 70
    elif pct <= 15:
        base = 70 - (pct - 8) / 7 * 20    # 70 → 50
    elif pct <= 25:
        base = 50 - (pct - 15) / 10 * 20  # 50 → 30
    else:
        base = max(10.0, 30 - (pct - 25))

    # 趨勢加成
    chg = row["chg_pct"]
    trend = 0.0
    if chg is not None:
        if chg < -10:
            trend = 10.0
        elif chg < 0:
            trend = 5.0
        elif chg > 10:
            trend = -10.0
        elif chg > 0:
            trend = -5.0

    return round(min(100.0, max(0.0, base + trend)), 2)
