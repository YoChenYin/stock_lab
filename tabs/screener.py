"""tabs/screener.py — Page 1: 選股"""

import streamlit as st
import pandas as pd
import numpy as np

from engine.smart_money import calc_smart_money_score, calc_revenue_accel_score
from engine.rocket_detector import detect_coiling
from engine.alerts import check_alerts, show_alert_toasts
from sector_data import STOCK_POOL, SECTOR_GROUPS


def _streak(series) -> int:
    """從最近一日往回數連買(+N)或連賣(-N)天數"""
    vals = series.dropna().values
    if len(vals) == 0:
        return 0
    sign = 1 if vals[-1] > 0 else (-1 if vals[-1] < 0 else 0)
    if sign == 0:
        return 0
    count = 0
    for v in reversed(vals):
        if (v > 0) == (sign > 0):
            count += 1
        else:
            break
    return sign * count


def _fmt_streak(n: int) -> str:
    if n > 0:
        return f"買+{n}天"
    if n < 0:
        return f"賣{n}天"
    return "—"


def _get_capital(engine, sid: str) -> float:
    try:
        import datetime
        start = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        fin   = engine._smart_fetch(sid, "financial_stat",
                                    engine.dl.taiwan_stock_financial_statement, start_date=start)
        ni  = fin[fin["type"].isin(["ConsolidatedNetIncome", "IncomeAfterTaxes"])]
        eps = fin[fin["type"] == "EPS"]
        if not ni.empty and not eps.empty:
            v_ni, v_eps = ni.iloc[-1]["value"], eps.iloc[-1]["value"]
            if v_eps > 0:
                return (v_ni / v_eps) * 10
    except Exception:
        pass
    return 0


def _has_flipped_to_buy(series, max_days: int = 3) -> bool:
    """
    判斷是否在近 max_days 天內由賣超轉買超：
    - 近 1~max_days 天全為買超（streak 在 1~max_days）
    - 轉折前一天為賣超（確認是真正的翻轉，不是一直在買）
    """
    vals = series.dropna().values
    if len(vals) < max_days + 2:
        return False
    s = _streak(series)
    if not (1 <= s <= max_days):
        return False
    # 轉折前一天必須是賣超
    return vals[-(s + 1)] < 0


@st.cache_data(ttl=43200)
def _run_scan(_engine, stock_map_items):
    results, inst_results, flip_results = [], [], []

    for sid, name in stock_map_items:
        df, rev = _engine.fetch_data(sid)
        if df.empty:
            continue

        df["ma5"]  = df["close"].rolling(5).mean()
        df["ma10"] = df["close"].rolling(10).mean()
        c_p = df["close"].iloc[-1]
        m5, m10, m20 = df["ma5"].iloc[-1], df["ma10"].iloc[-1], df["ma20"].iloc[-1]
        is_aligned = "🟢" if (m5 > m10 > m20 and c_p > m5) else "⚪"

        sms   = calc_smart_money_score(df) if "f_net" in df.columns else {"score": 0, "signal": "—"}
        coil  = detect_coiling(df)
        raccel= calc_revenue_accel_score(rev)

        # Revenue screener
        if not rev.empty and len(rev) >= 15:
            rs  = rev.sort_values("date").reset_index(drop=True)
            v   = rs["revenue"].tail(3).values
            if len(v) == 3 and v[2] > v[1] > v[0]:
                yoy = []
                for i in range(-1, -4, -1):
                    curr = rs["revenue"].iloc[i]
                    prev = rs["revenue"].iloc[i - 12]
                    yoy.append(((curr / prev) - 1) * 100 if prev > 0 else 0)
                cap = _get_capital(_engine, sid)
                results.append({
                    "代碼": sid, "名稱": name, "均線": is_aligned,
                    "SMS":     sms["score"],  "籌碼訊號": sms["signal"],
                    "蓄力":    coil["label"], "營收加速": raccel["label"],
                    "加速分數":raccel["accel_score"],
                    "YoY最新": f"{yoy[0]:.1f}%",
                    "YoY前月": f"{yoy[1]:.1f}%",
                    "最新營收(百萬)": f"{v[2]/1_000_000:.2f}",
                })

        # Chip screener
        if len(df) >= 20:
            vols = {p: df["trading_volume"].tail(p).sum() + 1e-9 for p in [5, 10, 15, 20]}
            conc = {p: (df["f_net"].tail(p).sum() + df["it_net"].tail(p).sum()) / vols[p] * 100
                    for p in [5, 10, 15, 20]}
            if conc[5] > conc[10] > conc[15] > conc[20]:
                d20   = df.tail(20)
                fb    = d20[d20["f_net"] > 0]
                itb   = d20[d20["it_net"] > 0]
                fc    = (fb["close"] * fb["f_net"]).sum() / fb["f_net"].sum() if not fb.empty else 0
                itc   = (itb["close"] * itb["it_net"]).sum() / itb["it_net"].sum() if not itb.empty else 0
                vol5  = d20["trading_volume"].tail(5).sum()
                inst_results.append({
                    "代碼": sid, "名稱": name, "均線": is_aligned,
                    "SMS":  sms["score"],  "籌碼訊號": sms["signal"],
                    "蓄力": coil["label"],
                    "1週集中度%":  f"{conc[5]:.2f}",
                    "外資連買/賣": _fmt_streak(_streak(df["f_net"])),
                    "投信連買/賣": _fmt_streak(_streak(df["it_net"])),
                    "外資乖離%":   f"{((c_p/fc)-1)*100:.2f}" if fc > 0 else "N/A",
                    "投信乖離%":   f"{((c_p/itc)-1)*100:.2f}" if itc > 0 else "N/A",
                    "外資力道(5D)": f"{df['f_net'].tail(5).sum()/vol5*100:.2f}" if vol5 else "0",
                    "投信力道(5D)": f"{df['it_net'].tail(5).sum()/vol5*100:.2f}" if vol5 else "0",
                })

        # 外資/投信由賣轉買偵測
        if len(df) >= 10 and ("f_net" in df.columns or "it_net" in df.columns):
            f_flip  = _has_flipped_to_buy(df["f_net"])  if "f_net"  in df.columns else False
            it_flip = _has_flipped_to_buy(df["it_net"]) if "it_net" in df.columns else False
            if f_flip or it_flip:
                f_s  = _streak(df["f_net"])  if "f_net"  in df.columns else 0
                it_s = _streak(df["it_net"]) if "it_net" in df.columns else 0
                flip_results.append({
                    "代碼": sid, "名稱": name, "均線": is_aligned,
                    "SMS":  sms["score"], "籌碼訊號": sms["signal"],
                    "蓄力": coil["label"],
                    "外資":  f"↑轉買{f_s}天"  if f_flip  else "—",
                    "投信":  f"↑轉買{it_s}天" if it_flip else "—",
                    "觸發": ("外資+投信" if f_flip and it_flip
                             else "外資" if f_flip else "投信"),
                })

    res_df   = pd.DataFrame(results).sort_values("SMS", ascending=False)   if results   else pd.DataFrame()
    inst_df  = pd.DataFrame(inst_results).sort_values("SMS", ascending=False) if inst_results else pd.DataFrame()
    flip_df  = pd.DataFrame(flip_results).sort_values("SMS", ascending=False) if flip_results else pd.DataFrame()
    return res_df, inst_df, flip_df


def render(engine):
    st.header("🔍 潛力標的掃描")

    # Alert bar
    with st.expander("🚨 即時預警", expanded=False):
        with st.spinner("掃描預警中..."):
            alerts = check_alerts(engine, STOCK_POOL)
        show_alert_toasts(alerts)
        if alerts:
            for a in alerts:
                icon = "🚨" if a["priority"] == "high" else "📌"
                st.markdown(f"{icon} {a['msg']}")
        else:
            st.info("目前無預警訊號")

    if not st.button("🚀 開始掃描", type="primary", use_container_width=True):
        return

    with st.spinner("掃描全市場，請稍候..."):
        res_df, inst_df, flip_df = _run_scan(engine, tuple(STOCK_POOL.items()))

    if not res_df.empty:
        st.subheader("📈 營收加速 + SMS 評分排行")
        st.caption("SMS = 外資連買 + 集中度加速 + 成本安全墊 + 散戶退場（0-100，越高越好）")
        st.dataframe(res_df.style.background_gradient(
            subset=["SMS", "加速分數"], cmap="YlGn").format(precision=2),
            use_container_width=True)

    if not inst_df.empty:
        st.subheader("👥 法人籌碼加速 + SMS 排行")
        st.dataframe(inst_df.style.background_gradient(
            subset=["SMS"], cmap="OrRd").format(precision=2),
            use_container_width=True)

    if not flip_df.empty:
        st.subheader("🔀 外資/投信由賣轉買（3日內）")
        st.caption("近 1~3 個交易日由賣超翻轉為買超，轉折前一天確認為賣超")
        st.dataframe(flip_df.style.background_gradient(
            subset=["SMS"], cmap="Blues").format(precision=2),
            use_container_width=True)
    else:
        st.subheader("🔀 外資/投信由賣轉買（3日內）")
        st.info("目前無外資或投信在 3 日內由賣轉買的標的。")

    # Combined screen
    st.subheader("🚀 雙強標的：營收加速 × 籌碼加速")
    combined = []
    for sector, stocks in SECTOR_GROUPS.items():
        for sid in stocks:
            df, rev = engine.fetch_data(sid)
            if df.empty or rev.empty or len(rev) < 15:
                continue
            rs = rev.sort_values("date")
            v  = rs["revenue"].tail(3).values
            c5  = (df["f_net"].tail(5).sum()  + df["it_net"].tail(5).sum())  / (df["trading_volume"].tail(5).sum()  + 1e-9) * 100
            c20 = (df["f_net"].tail(20).sum() + df["it_net"].tail(20).sum()) / (df["trading_volume"].tail(20).sum() + 1e-9) * 100
            if len(v) == 3 and v[2] > v[1] > v[0] and c5 > c20:
                sms   = calc_smart_money_score(df)
                raccel= calc_revenue_accel_score(rev)
                coil  = detect_coiling(df)
                yoy   = (v[2] / (rs["revenue"].iloc[-13] + 1e-9) - 1) * 100
                combined.append({
                    "族群": sector, "代碼": sid, "名稱": STOCK_POOL.get(sid, sid),
                    "SMS": sms["score"], "籌碼訊號": sms["signal"],
                    "營收加速": raccel["label"], "蓄力": coil["label"],
                    "最新YoY%": round(yoy, 1), "集中度5D": round(c5, 2),
                    "流動性": "✅" if df["trading_volume"].tail(5).mean() >= 1_000_000 else "⚠️",
                })
    if combined:
        cdf = pd.DataFrame(combined).sort_values("SMS", ascending=False)
        st.dataframe(cdf.style.background_gradient(
            subset=["SMS", "最新YoY%", "集中度5D"], cmap="YlGn")
            .format({"最新YoY%": "{:.1f}%", "集中度5D": "{:.2f}%"}),
            use_container_width=True)
    else:
        st.info("目前無標的同時符合營收連增且籌碼加速條件。")
