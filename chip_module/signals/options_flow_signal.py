"""
signals/options_flow_signal.py
個股選擇權異常流量信號 → 0-100 分

偵測維度（加權）：
1. OTM call surge（40%）
   - otm_call_volume / call_volume 比例高 → 有人在佈局遠期看漲
2. 異常大單密度（35%）
   - unusual_call_strikes 數量 → 多個 strike 同時出現 vol/OI > 3
3. Call/Put 偏斜（25%）
   - call_volume / put_volume > 歷史均值 → 整體偏多
   - 注意：此處不逆向，直接反映 call 需求

需要至少 3 天歷史才能計算 z-score 比較，
否則僅用靜態指標（OTM 比例 + 異常 strike 數）。

分數方向：高分 = 有巨鯨在偷偷買 call（看漲信號）
"""

from datetime import date
from ..db.schema import get_conn


def score(ticker: str, as_of: str = None, db_path=None) -> float:
    if as_of is None:
        as_of = date.today().isoformat()

    conn = get_conn(db_path) if db_path else get_conn()

    # 取最近 20 天歷史（用於計算 z-score）
    rows = conn.execute("""
        SELECT date, call_volume, put_volume, call_oi, put_oi,
               otm_call_volume, unusual_call_strikes,
               unusual_put_strikes, underlying_price,
               avg_call_iv, avg_put_iv
        FROM options_flow
        WHERE ticker=? AND date <= ?
        ORDER BY date DESC LIMIT 20
    """, (ticker, as_of)).fetchall()
    conn.close()

    if not rows:
        return 50.0

    today_row = dict(rows[0])

    # ── 1. OTM call surge score ───────────────────────────────────
    otm_s = _otm_score(today_row, rows)

    # ── 2. 異常大單密度 score ─────────────────────────────────────
    unusual_s = _unusual_score(today_row, rows)

    # ── 3. Call/Put 偏斜 score ────────────────────────────────────
    skew_s = _skew_score(today_row, rows)

    return round(0.40 * otm_s + 0.35 * unusual_s + 0.25 * skew_s, 2)


def _otm_score(today: dict, history: list) -> float:
    """OTM call 佔總 call volume 的比例，與歷史比較"""
    call_vol = today.get("call_volume") or 0
    otm_vol  = today.get("otm_call_volume") or 0

    if call_vol == 0:
        return 50.0

    today_ratio = otm_vol / call_vol

    # 計算歷史均值與標準差（排除今天）
    hist_ratios = []
    for r in history[1:]:
        cv = r["call_volume"] or 0
        ov = r["otm_call_volume"] or 0
        if cv > 0:
            hist_ratios.append(ov / cv)

    if len(hist_ratios) < 3:
        # 靜態映射：otm 比例 0% → 30分，30% → 70分，50%+ → 90分
        return round(min(90.0, 30 + today_ratio * 120), 2)

    mean_r = sum(hist_ratios) / len(hist_ratios)
    std_r  = (sum((x - mean_r) ** 2 for x in hist_ratios) / len(hist_ratios)) ** 0.5
    z      = (today_ratio - mean_r) / (std_r + 1e-9)

    return round(min(100.0, max(0.0, 50 + z * 15)), 2)


def _unusual_score(today: dict, history: list) -> float:
    """異常大單 strike 數，與歷史比較"""
    uc = today.get("unusual_call_strikes") or 0

    hist_uc = [r["unusual_call_strikes"] or 0 for r in history[1:]]
    if not hist_uc:
        # 靜態：0個=30, 3個=60, 5個+=80, 10個+=95
        if uc == 0:
            return 30.0
        return round(min(95.0, 30 + uc * 10), 2)

    mean_uc = sum(hist_uc) / len(hist_uc)
    std_uc  = (sum((x - mean_uc) ** 2 for x in hist_uc) / len(hist_uc)) ** 0.5
    z       = (uc - mean_uc) / (std_uc + 1e-9)

    return round(min(100.0, max(0.0, 50 + z * 15)), 2)


def _skew_score(today: dict, history: list) -> float:
    """Call/Put volume 比，比歷史偏高代表偏多"""
    cv = today.get("call_volume") or 0
    pv = today.get("put_volume")  or 0

    if cv + pv == 0:
        return 50.0

    today_cp = cv / (pv + 1e-9)

    hist_cp = []
    for r in history[1:]:
        c = r["call_volume"] or 0
        p = r["put_volume"]  or 0
        if c + p > 0:
            hist_cp.append(c / (p + 1e-9))

    if len(hist_cp) < 3:
        # 靜態：c/p=1 → 50分，c/p=2 → 70分，c/p=3+ → 85分
        return round(min(90.0, 50 + (today_cp - 1) * 20), 2)

    mean_cp = sum(hist_cp) / len(hist_cp)
    std_cp  = (sum((x - mean_cp) ** 2 for x in hist_cp) / len(hist_cp)) ** 0.5
    z       = (today_cp - mean_cp) / (std_cp + 1e-9)

    return round(min(100.0, max(0.0, 50 + z * 12)), 2)
