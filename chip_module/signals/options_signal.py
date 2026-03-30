"""
signals/options_signal.py
SPY Put/Call Ratio Z-Score → 市場情緒（逆向解讀）→ 0-100

逆向邏輯：
- z > +1.5（極度恐慌，大量買 put）→ 逆向做多信號 → 分數高
- z < -1.5（極度樂觀，大量買 call）→ 過熱警示 → 分數低
- 無 z-score 時退回 pc_ratio 直接映射

注意：此分數是市場整體情緒，所有股票在同一天分數相同。
"""

from datetime import date
from ..db.schema import get_conn


def score(as_of: str = None, db_path=None) -> float:
    if as_of is None:
        as_of = date.today().isoformat()

    conn = get_conn(db_path) if db_path else get_conn()
    row = conn.execute("""
        SELECT pc_ratio, pc_zscore_20
        FROM options_sentiment
        WHERE scope='equity' AND date <= ?
        ORDER BY date DESC LIMIT 1
    """, (as_of,)).fetchone()
    conn.close()

    if not row:
        return 50.0

    if row["pc_zscore_20"] is not None:
        z = row["pc_zscore_20"]
        # 逆向：z-score 越高（恐慌）→ 分數越高（逆向做多）
        # score = 50 + z * 15，壓縮至 [0, 100]
        return round(min(100.0, max(0.0, 50 + z * 15)), 2)

    # fallback：用 pc_ratio 直接映射
    pc = row["pc_ratio"]
    if pc is None:
        return 50.0
    # pc < 0.7（樂觀過熱）→ 低分；pc > 1.0（恐慌）→ 高分
    # 以 pc=0.85 為中性，每偏差 0.15 映射 25 分
    return round(min(100.0, max(0.0, 50 + (pc - 0.85) / 0.15 * 25)), 2)
