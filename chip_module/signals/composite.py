"""
signals/composite.py  (v2)
合成所有信號 → 三個週期分數 + whale_alert + entry_timing

加權比例：
               短線    波段    中線
    volume     35%     20%     10%
    opt_flow   30%     20%      5%   ← 個股選擇權異常
    opt_mkt    15%     10%      5%   ← SPY P/C 市場情緒
    short      10%     20%     20%
    insider    10%     20%     25%
    institutional 0%  10%     35%

whale_alert 觸發條件（任一）：
  - insider_score >= 70（cluster 偵測：多人短窗口買入）
  - 近 30 天有 SC 13D 申報（主動大戶進場）
  - options_flow_score >= 80 AND unusual_call_strikes >= 5

entry_timing 觸發條件（ALL）：
  - whale_alert = 1
  - volume_score >= 55（量能確認）
  - short_score >= 45（空頭沒有明顯壓力）
"""

import json
import argparse
from datetime import date, timedelta
from typing import List

from .insider_signal        import score as insider_score
from .short_signal          import score as short_score
from .volume_signal         import score as volume_score
from .options_signal        import score as options_mkt_score
from .options_flow_signal   import score as options_flow_score
from .institutional_signal  import score as institutional_score
from ..db.schema            import get_conn

WEIGHTS = {
    "short": {
        "volume": 0.35, "opt_flow": 0.30, "opt_mkt": 0.15,
        "short":  0.10, "insider": 0.10,  "institutional": 0.00,
    },
    "swing": {
        "volume": 0.20, "opt_flow": 0.20, "opt_mkt": 0.10,
        "short":  0.20, "insider": 0.20,  "institutional": 0.10,
    },
    "mid": {
        "volume": 0.10, "opt_flow": 0.05, "opt_mkt": 0.05,
        "short":  0.20, "insider": 0.25,  "institutional": 0.35,
    },
}


def _composite(scores: dict, timeframe: str) -> float:
    w = WEIGHTS[timeframe]
    return round(sum(scores[k] * w[k] for k in w), 2)


def _whale_alert(scores: dict, ticker: str, as_of: str, db_path) -> bool:
    # 條件1：insider cluster
    if scores["insider"] >= 70:
        return True

    # 條件2：近 30 天有 SC 13D（主動大戶）
    since = (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()
    conn  = get_conn(db_path) if db_path else get_conn()
    row   = conn.execute("""
        SELECT 1 FROM large_holders
        WHERE ticker=? AND filed_date >= ? AND form_type LIKE '%13D%'
        LIMIT 1
    """, (ticker, since)).fetchone()
    conn.close()
    if row:
        return True

    # 條件3：個股選擇權異常大量
    if scores["opt_flow"] >= 80:
        conn2 = get_conn(db_path) if db_path else get_conn()
        r = conn2.execute("""
            SELECT unusual_call_strikes FROM options_flow
            WHERE ticker=? AND date <= ? ORDER BY date DESC LIMIT 1
        """, (ticker, as_of)).fetchone()
        conn2.close()
        if r and (r["unusual_call_strikes"] or 0) >= 5:
            return True

    return False


def _entry_timing(scores: dict, whale: bool) -> bool:
    return (
        whale
        and scores["volume"] >= 55
        and scores["short"]  >= 45
    )


def _signal_flags(scores: dict, whale: bool, entry: bool) -> list:
    flags = []
    if entry:
        flags.append("entry_timing")
    if whale:
        flags.append("whale_alert")
    if scores["insider"] >= 70:
        flags.append("insider_cluster")
    if scores["insider"] <= 15:
        flags.append("insider_selling")
    if scores["short"] <= 25:
        flags.append("high_short_interest")
    if scores["volume"] >= 75:
        flags.append("volume_accumulation")
    elif scores["volume"] <= 25:
        flags.append("volume_distribution")
    if scores["opt_flow"] >= 75:
        flags.append("unusual_options")
    return flags


def run(tickers: List[str], as_of: str = None, db_path=None):
    if as_of is None:
        as_of = date.today().isoformat()

    conn    = get_conn(db_path) if db_path else get_conn()
    opt_mkt = options_mkt_score(as_of=as_of, db_path=db_path)

    for ticker in tickers:
        try:
            scores = {
                "insider":       insider_score(ticker,      as_of=as_of, db_path=db_path),
                "short":         short_score(ticker,        as_of=as_of, db_path=db_path),
                "volume":        volume_score(ticker,       as_of=as_of, db_path=db_path),
                "opt_flow":      options_flow_score(ticker, as_of=as_of, db_path=db_path),
                "opt_mkt":       opt_mkt,
                "institutional": institutional_score(ticker,as_of=as_of, db_path=db_path),
            }

            whale = _whale_alert(scores, ticker, as_of, db_path)
            entry = _entry_timing(scores, whale)
            flags = _signal_flags(scores, whale, entry)

            c_short = _composite(scores, "short")
            c_swing = _composite(scores, "swing")
            c_mid   = _composite(scores, "mid")

            conn.execute("""
                INSERT INTO chip_scores (
                    ticker, date,
                    insider_score, short_score, volume_score,
                    options_flow_score, options_mkt_score, institutional_score,
                    composite_short, composite_swing, composite_mid,
                    whale_alert, entry_timing, signal_flags
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticker, date) DO UPDATE SET
                    insider_score=excluded.insider_score,
                    short_score=excluded.short_score,
                    volume_score=excluded.volume_score,
                    options_flow_score=excluded.options_flow_score,
                    options_mkt_score=excluded.options_mkt_score,
                    institutional_score=excluded.institutional_score,
                    composite_short=excluded.composite_short,
                    composite_swing=excluded.composite_swing,
                    composite_mid=excluded.composite_mid,
                    whale_alert=excluded.whale_alert,
                    entry_timing=excluded.entry_timing,
                    signal_flags=excluded.signal_flags,
                    updated_at=datetime('now')
            """, (
                ticker, as_of,
                scores["insider"], scores["short"], scores["volume"],
                scores["opt_flow"], scores["opt_mkt"], scores["institutional"],
                c_short, c_swing, c_mid,
                int(whale), int(entry),
                json.dumps(flags),
            ))

            whale_icon = "🐋" if whale else "  "
            entry_icon = "⚡" if entry else "  "
            print(
                f"[composite] {ticker:6s} "
                f"in={scores['insider']:4.0f} sh={scores['short']:4.0f} "
                f"vol={scores['volume']:4.0f} of={scores['opt_flow']:4.0f} "
                f"inst={scores['institutional']:4.0f}  "
                f"短={c_short:.0f} 波={c_swing:.0f} 中={c_mid:.0f}  "
                f"{whale_icon}{entry_icon}"
                + (f"  [{', '.join(flags)}]" if flags else "")
            )

        except Exception as e:
            print(f"[composite] {ticker} 失敗: {e}")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="計算籌碼綜合分數 v2")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--date", dest="as_of", default=None,
                        help="計算基準日 YYYY-MM-DD，預設今天")
    args = parser.parse_args()
    run(tickers=args.tickers, as_of=args.as_of)
