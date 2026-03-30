"""
signals/insider_signal.py  (v2 — cluster 偵測)
Form 4 內部人交易信號 → 0-100 分

升級點：
1. Cluster 偵測：短時間內多人同時買入信號強度 >> 單人
2. 職位權重：CEO/CFO 買入 > 普通董事
3. 金額門檻：< $50k 的公開市場買入不計（可能是員工持股計劃）
4. 時間衰減：近 5 天 cluster > 近 30 天 > 近 90 天
5. 排除行使選擇權（transaction_type = 'A'）

最終分數構成：
  cluster_score (60%) + net_ratio_score (40%)
  cluster_score 由最優 5 天窗口決定
"""

from datetime import date, timedelta
from typing import Optional
from ..db.schema import get_conn

# 職位關鍵字 → 權重（越高代表該職位內部資訊越充分）
TITLE_WEIGHTS = [
    (["CEO", "CHIEF EXECUTIVE"],         3.0),
    (["CFO", "CHIEF FINANCIAL"],         3.0),
    (["COO", "CHIEF OPERATING"],         2.5),
    (["PRESIDENT"],                      2.5),
    (["CTO", "CHIEF TECHNOLOGY",
      "CRO", "CHIEF REVENUE",
      "CMO", "CHIEF MARKETING"],         2.0),
    (["EVP", "EXECUTIVE VICE"],          1.8),
    (["SVP", "SENIOR VICE"],             1.5),
    (["VP", "VICE PRESIDENT"],           1.2),
    (["10%", "BENEFICIAL OWNER",
      "PRINCIPAL"],                      2.0),
    (["DIRECTOR"],                       1.0),
]

MIN_AMOUNT   = 50_000    # 低於此金額的買入忽略（單位：美元）
CLUSTER_DAYS = 5         # cluster 偵測窗口（天）


def _title_weight(title: Optional[str]) -> float:
    if not title:
        return 1.0
    t = title.upper()
    for keywords, weight in TITLE_WEIGHTS:
        if any(kw in t for kw in keywords):
            return weight
    return 1.0


def score(ticker: str, as_of: str = None, db_path=None) -> float:
    if as_of is None:
        as_of = date.today().isoformat()

    date_90 = (date.fromisoformat(as_of) - timedelta(days=90)).isoformat()

    conn = get_conn(db_path) if db_path else get_conn()
    rows = conn.execute("""
        SELECT transaction_type, total_value, trade_date, report_date,
               insider_name, insider_title
        FROM insider_trades
        WHERE ticker=? AND report_date BETWEEN ? AND ?
          AND transaction_type IN ('P', 'S')
        ORDER BY trade_date
    """, (ticker, date_90, as_of)).fetchall()
    conn.close()

    if not rows:
        return 50.0

    rows = [dict(r) for r in rows]

    # ── cluster score ─────────────────────────────────────────────
    cluster_s = _cluster_score(rows, as_of)

    # ── net ratio score（近90天加權買賣比）────────────────────────
    buy_w = sell_w = 0.0
    for r in rows:
        v = r["total_value"] or 0
        if v < MIN_AMOUNT and r["transaction_type"] == "P":
            continue
        tw = _title_weight(r["insider_title"])
        if r["transaction_type"] == "P":
            buy_w  += v * tw
        else:
            sell_w += v * tw

    net_ratio = buy_w / (buy_w + sell_w + 1e-9)
    net_s     = round(net_ratio * 100, 2)

    return round(0.6 * cluster_s + 0.4 * net_s, 2)


def _cluster_score(rows: list, as_of: str) -> float:
    """
    掃描所有 CLUSTER_DAYS 天窗口，找出買入集中度最高的一個。
    回傳 0-100 分：
      - 單人 CEO 大額買入 ≈ 70
      - 3+ 人同時買入（含高職位）≈ 85-100
    """
    buys = [r for r in rows
            if r["transaction_type"] == "P"
            and (r["total_value"] or 0) >= MIN_AMOUNT]

    if not buys:
        return 0.0

    # 取所有交易日，每個日期為窗口起點
    trade_dates = sorted(set(r["trade_date"] for r in buys if r["trade_date"]))
    best = 0.0

    for start_dt_str in trade_dates:
        try:
            start_dt = date.fromisoformat(start_dt_str)
        except ValueError:
            continue
        end_dt = start_dt + timedelta(days=CLUSTER_DAYS)
        end_str = end_dt.isoformat()

        window = [r for r in buys
                  if r["trade_date"] and start_dt_str <= r["trade_date"] <= end_str]

        unique_insiders = len(set(r["insider_name"] for r in window))
        total_value     = sum(r["total_value"] or 0 for r in window)
        title_score     = sum(_title_weight(r["insider_title"]) for r in window)

        # 人數分（1人=20, 2人=40, 3人=60, 4人+=80）
        people_s = min(80, unique_insiders * 20)
        # 金額分（100萬=20, 500萬=40, 1000萬+=60）
        amount_s = min(60, total_value / 1_000_000 * 12)
        # 職位分（最多 20 分）
        title_s  = min(20, title_score * 3)

        raw = people_s + amount_s + title_s

        # 時間衰減：離 as_of 越近，分數越高
        days_ago = (date.fromisoformat(as_of) - start_dt).days
        if days_ago <= 5:
            decay = 1.0
        elif days_ago <= 30:
            decay = 0.85
        else:
            decay = 0.65

        best = max(best, min(100.0, raw * decay))

    return round(best, 2)


def get_cluster_details(ticker: str, as_of: str = None, db_path=None) -> list:
    """
    回傳近 90 天所有 cluster 事件的詳細資訊，供 UI 顯示用。
    """
    if as_of is None:
        as_of = date.today().isoformat()

    date_90 = (date.fromisoformat(as_of) - timedelta(days=90)).isoformat()
    conn = get_conn(db_path) if db_path else get_conn()
    rows = conn.execute("""
        SELECT trade_date, insider_name, insider_title,
               transaction_type, shares, price_per_share, total_value
        FROM insider_trades
        WHERE ticker=? AND report_date BETWEEN ? AND ?
          AND transaction_type='P' AND total_value >= ?
        ORDER BY trade_date DESC
    """, (ticker, date_90, as_of, MIN_AMOUNT)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
