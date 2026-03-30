"""
signals/institutional_signal.py
機構持倉季度增減加權平均 → 0-100 分

- 取最新一期的各機構 chg_pct，以 shares_held 為權重求加權平均
- weighted_chg = +50% → 約 90 分；0% → 50 分；-50% → 約 10 分
- 無資料 → 50（中性）
"""

from datetime import date
from ..db.schema import get_conn


def score(ticker: str, as_of: str = None, db_path=None) -> float:
    if as_of is None:
        as_of = date.today().isoformat()

    conn = get_conn(db_path) if db_path else get_conn()

    # 取最新一期申報日
    latest = conn.execute("""
        SELECT report_date FROM institutional_holders
        WHERE ticker=? AND report_date <= ?
        ORDER BY report_date DESC LIMIT 1
    """, (ticker, as_of)).fetchone()

    if not latest:
        conn.close()
        return 50.0

    rows = conn.execute("""
        SELECT chg_pct, shares_held
        FROM institutional_holders
        WHERE ticker=? AND report_date=?
          AND chg_pct IS NOT NULL AND shares_held > 0
    """, (ticker, latest["report_date"])).fetchall()
    conn.close()

    if not rows:
        return 50.0

    total_shares = sum(r["shares_held"] for r in rows)
    if total_shares == 0:
        return 50.0

    weighted_chg = sum(r["chg_pct"] * r["shares_held"] for r in rows) / total_shares
    # 每 1% 變化映射 0.8 分（±50% ≈ ±40 分偏移）
    return round(min(100.0, max(0.0, 50 + weighted_chg * 0.8)), 2)
