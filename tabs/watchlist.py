"""
tabs/watchlist.py — 持倉追蹤儀表板

功能：
  - 追蹤清單管理（加入/移除）
  - 策略卡視覺化：進場條顯示現價相對位置
  - 狀態自動判斷：可進場 / 可加碼 / 持有中 / 接近目標 / 注意停損
  - 90 天策略卡快取，一季才呼叫一次 Gemini

user_id 目前固定為 "default"，之後換 Google login 只需改一行。
"""

import streamlit as st
import pandas as pd
import numpy as np
from sector_data import STOCK_POOL
from engine.watchlist import (
    get_watchlist, add_to_watchlist, remove_from_watchlist,
    ensure_cards_for_watchlist, force_refresh_card,
)

# 之後換 Google login：USER_ID = st.experimental_user.email
USER_ID = "default"


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _safe_float(val, default=None):
    try:
        return float(str(val).replace(",", ""))
    except Exception:
        return default


def _determine_zone(curr: float, entry_low: float, entry_high: float,
                    stop: float, t1: float) -> tuple[str, str]:
    """
    Returns (zone_label, badge_color_class)
    Zone logic:
      接近停損  — curr <= stop * 1.03
      進場區間  — entry_low <= curr <= entry_high
      可加碼    — curr just above entry zone (< entry_high * 1.05) + room to t1
      接近目標一 — curr >= t1 * 0.97
      持有觀察  — everything else
    """
    if stop and curr <= stop * 1.05:
        return "⚠️ 接近停損", "stop"
    if t1 and curr >= t1 * 0.97:
        return "🎯 接近目標一", "target"
    if entry_low and entry_high and entry_low <= curr <= entry_high:
        return "🟢 進場區間", "entry"
    if entry_high and curr <= entry_high * 1.06:
        return "➕ 可加碼", "add"
    return "📊 持有觀察", "hold"


def _position_bar(curr: float, entry_low: float, stop: float, t1: float) -> str:
    """
    Returns HTML for the position bar.
    Range shown: stop → t1
    Marker position: where curr sits in that range.
    """
    if not all([stop, t1, entry_low, curr]):
        return "<span style='color:#94a3b8;font-size:11px;'>—</span>"

    total = t1 - stop
    if total <= 0:
        return "<span style='color:#94a3b8;font-size:11px;'>—</span>"

    pct = max(0.0, min(1.0, (curr - stop) / total))
    bar_pct = round(pct * 100, 1)

    # Colour: red near stop, green near target
    if pct < 0.2:
        fill = "#ef4444"
    elif pct < 0.45:
        fill = "#f59e0b"
    elif pct < 0.75:
        fill = "#10b981"
    else:
        fill = "#6366f1"

    marker = (
        f"<div style='position:absolute;left:{bar_pct}%;top:-4px;"
        f"width:2px;height:14px;background:#1e293b;border-radius:1px;"
        f"transform:translateX(-50%);z-index:2;'></div>"
    )
    bar = (
        f"<div style='width:100%;background:#f1f5f9;border-radius:4px;"
        f"height:6px;position:relative;'>"
        f"<div style='width:{bar_pct}%;background:{fill};height:6px;"
        f"border-radius:4px;'></div>"
        f"{marker}</div>"
    )

    stop_label  = f"<span style='font-size:10px;color:#b91c1c;'>{stop:.0f}</span>"
    t1_label    = f"<span style='font-size:10px;color:#6b21a8;'>{t1:.0f}</span>"

    return (
        f"<div style='display:flex;align-items:center;gap:6px;'>"
        f"{stop_label}"
        f"<div style='flex:1;'>{bar}</div>"
        f"{t1_label}</div>"
    )


ZONE_STYLES = {
    "entry":  "background:#eff6ff;color:#1d4ed8;",
    "add":    "background:#f0fdf4;color:#15803d;",
    "hold":   "background:#f8fafc;color:#475569;",
    "stop":   "background:#fef2f2;color:#b91c1c;",
    "target": "background:#faf5ff;color:#6b21a8;",
}

SMS_COLOR = {
    "high":   "#10b981",
    "medium": "#f59e0b",
    "low":    "#ef4444",
}


def _sms_dot(score) -> str:
    s = float(score) if score else 0
    c = SMS_COLOR["high"] if s >= 75 else SMS_COLOR["medium"] if s >= 50 else SMS_COLOR["low"]
    return (f"<span style='display:inline-block;width:8px;height:8px;"
            f"border-radius:50%;background:{c};margin-right:4px;'></span>"
            f"<span style='font-size:11px;'>{s:.0f}</span>")


# ─────────────────────────────────────────────────────────
# Main render
# ─────────────────────────────────────────────────────────

def render(engine):
    st.header("📋 持倉追蹤")
    st.caption("追蹤中的標的策略卡每季自動更新一次（90 天快取），現價每次即時抓取。")

    sids = get_watchlist(USER_ID)

    # ── Add stock ──
    with st.expander("➕ 加入追蹤標的", expanded=len(sids) == 0):
        all_sids = list(STOCK_POOL.keys())
        watched  = set(sids)
        options  = [s for s in all_sids if s not in watched]

        col_sel, col_btn = st.columns([3, 1])
        with col_sel:
            new_sid = st.selectbox(
                "選擇標的",
                options,
                format_func=lambda x: f"{x} {STOCK_POOL.get(x, x)}",
                label_visibility="collapsed",
            )
        with col_btn:
            if st.button("加入", type="primary", use_container_width=True):
                add_to_watchlist(USER_ID, new_sid)
                st.rerun()

    if not sids:
        st.info("追蹤清單是空的，從上方加入第一支標的。")
        return

    # ── Load current prices ──
    curr_prices = {}
    with st.spinner("載入最新股價..."):
        for sid in sids:
            try:
                df, _ = engine.fetch_data(sid)
                if not df.empty:
                    curr_prices[sid] = float(df["close"].iloc[-1])
            except Exception:
                pass

    # ── Load / generate strategy cards ──
    needs_gen = [s for s in sids if s not in curr_prices or True]  # always check cache
    progress_placeholder = st.empty()

    def on_progress(i, total, sid):
        name = STOCK_POOL.get(sid, sid)
        progress_placeholder.info(
            f"⚙️ 生成策略卡 {i+1}/{total}：{name}（首次或快取過期，之後 90 天免重跑）"
        )

    cards = ensure_cards_for_watchlist(engine, USER_ID, sids, on_progress)
    progress_placeholder.empty()

    # ── Build table rows ──
    rows = []
    for sid in sids:
        name  = STOCK_POOL.get(sid, sid)
        curr  = curr_prices.get(sid)
        card  = cards.get(sid, {})

        entry_zone = card.get("entry", {}).get("ideal_zone", "")
        entry_low, entry_high = None, None
        if "–" in str(entry_zone) or "-" in str(entry_zone):
            parts = str(entry_zone).replace("–", "-").split("-")
            if len(parts) == 2:
                entry_low  = _safe_float(parts[0])
                entry_high = _safe_float(parts[1])

        stop   = _safe_float(card.get("risk", {}).get("stop_loss"))
        t1     = _safe_float(card.get("targets", [{}])[0].get("price")) if card.get("targets") else None
        t2     = _safe_float(card.get("targets", [{}])[1].get("price")) if len(card.get("targets", [])) > 1 else None
        rr_raw = ""
        if curr and stop and t1 and curr > stop:
            rr_val = (t1 - curr) / (curr - stop)
            rr_raw = f"1 : {rr_val:.1f}"

        zone_label, zone_key = ("—", "hold")
        if curr and stop and t1:
            zone_label, zone_key = _determine_zone(
                curr, entry_low or (curr * 0.98), entry_high or curr, stop, t1)

        cache_age = card.get("_cache_age_days", 0)
        cache_info = f"{cache_age}天前生成" if card.get("_generated_date") else "剛生成"

        rows.append({
            "sid": sid, "name": name, "curr": curr,
            "entry_low": entry_low, "entry_high": entry_high,
            "stop": stop, "t1": t1, "t2": t2,
            "rr": rr_raw, "zone_label": zone_label, "zone_key": zone_key,
            "cache_info": cache_info,
            "card": card,
        })

    # Sort: stop zone first, then entry, then others
    zone_order = {"stop": 0, "entry": 1, "add": 2, "target": 3, "hold": 4}
    rows.sort(key=lambda r: zone_order.get(r["zone_key"], 9))

    # ── Filter ──
    col_f1, col_f2 = st.columns([2, 5])
    with col_f1:
        filter_zone = st.selectbox(
            "篩選狀態",
            ["全部", "⚠️ 接近停損", "🟢 進場區間", "➕ 可加碼",
             "🎯 接近目標一", "📊 持有觀察"],
            label_visibility="collapsed",
        )

    if filter_zone != "全部":
        rows = [r for r in rows if r["zone_label"] == filter_zone]

    # ── Summary metrics ──
    n_stop   = sum(1 for r in rows if r["zone_key"] == "stop")
    n_entry  = sum(1 for r in rows if r["zone_key"] == "entry")
    n_target = sum(1 for r in rows if r["zone_key"] == "target")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("追蹤標的", len(sids))
    m2.metric("⚠️ 接近停損", n_stop,  delta="需注意" if n_stop  else None, delta_color="inverse")
    m3.metric("🟢 可進場",  n_entry,  delta="機會" if n_entry  else None)
    m4.metric("🎯 接近目標", n_target, delta="可減倉" if n_target else None)

    st.divider()

    # ── Table header ──
    header_cols = st.columns([1.2, 0.8, 0.5, 2.5, 0.9, 0.8, 0.8, 0.8, 0.8, 0.8])
    for col, label in zip(header_cols, [
        "標的", "現價", "SMS", "進場 ── 現價 ── 目標一",
        "進場區間", "停損", "目標一", "目標二", "風報比", "狀態"
    ]):
        col.markdown(
            f"<div style='font-size:11px;font-weight:500;color:#64748b;"
            f"padding:4px 0;'>{label}</div>",
            unsafe_allow_html=True,
        )

    # ── Table rows ──
    for row in rows:
        zone_css = ZONE_STYLES.get(row["zone_key"], "")
        bg = "background:rgba(254,242,242,0.4);" if row["zone_key"] == "stop" else ""

        c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns(
            [1.2, 0.8, 0.5, 2.5, 0.9, 0.8, 0.8, 0.8, 0.8, 0.8])

        with c1:
            st.markdown(
                f"<div style='padding:6px 0;{bg}'>"
                f"<div style='font-weight:500;font-size:13px;'>{row['sid']}</div>"
                f"<div style='font-size:11px;color:#64748b;'>{row['name']}</div>"
                f"</div>", unsafe_allow_html=True)

        with c2:
            curr_str = f"{row['curr']:.1f}" if row['curr'] else "—"
            st.markdown(
                f"<div style='padding:6px 0;font-size:13px;font-weight:500;{bg}'>"
                f"{curr_str}</div>", unsafe_allow_html=True)

        with c3:
            # SMS from fresh data
            try:
                df, _ = engine.fetch_data(row["sid"])
                from engine.smart_money import calc_smart_money_score
                sms_r = calc_smart_money_score(df) if not df.empty and "f_net" in df.columns else {"score": 0}
                sms_html = _sms_dot(sms_r["score"])
            except Exception:
                sms_html = "—"
            st.markdown(f"<div style='padding:6px 0;{bg}'>{sms_html}</div>",
                        unsafe_allow_html=True)

        with c4:
            bar_html = _position_bar(
                row["curr"], row["entry_low"], row["stop"], row["t1"])
            st.markdown(f"<div style='padding:8px 0;{bg}'>{bar_html}</div>",
                        unsafe_allow_html=True)

        with c5:
            ez = f"{row['entry_low']:.0f}–{row['entry_high']:.0f}" \
                if row['entry_low'] and row['entry_high'] else \
                row['card'].get("entry", {}).get("ideal_zone", "—")
            st.markdown(
                f"<div style='padding:6px 0;font-size:11px;color:#1d4ed8;{bg}'>{ez}</div>",
                unsafe_allow_html=True)

        with c6:
            sl = f"{row['stop']:.0f}" if row['stop'] else "—"
            warn = " ⚠️" if row["zone_key"] == "stop" else ""
            st.markdown(
                f"<div style='padding:6px 0;font-size:11px;color:#b91c1c;font-weight:"
                f"{'600' if warn else '400'};{bg}'>{sl}{warn}</div>",
                unsafe_allow_html=True)

        with c7:
            t1s = f"{row['t1']:.0f}" if row['t1'] else "—"
            st.markdown(
                f"<div style='padding:6px 0;font-size:11px;color:#6b21a8;{bg}'>{t1s}</div>",
                unsafe_allow_html=True)

        with c8:
            t2s = f"{row['t2']:.0f}" if row['t2'] else "—"
            st.markdown(
                f"<div style='padding:6px 0;font-size:11px;color:#6b21a8;{bg}'>{t2s}</div>",
                unsafe_allow_html=True)

        with c9:
            st.markdown(
                f"<div style='padding:6px 0;font-size:11px;font-weight:500;{bg}'>"
                f"{row['rr'] or '—'}</div>", unsafe_allow_html=True)

        with c10:
            badge = (
                f"<span style='display:inline-block;padding:2px 7px;border-radius:4px;"
                f"font-size:10px;font-weight:600;{zone_css}'>{row['zone_label']}</span>"
            )
            st.markdown(f"<div style='padding:6px 0;{bg}'>{badge}</div>",
                        unsafe_allow_html=True)

        # Expandable detail row
        with st.expander(f"  {row['sid']} {row['name']} 詳細 · {row['cache_info']}", expanded=False):
            card = row["card"]
            if card.get("_empty"):
                st.warning("策略卡生成失敗，請確認 Gemini API Key。")
            else:
                d1, d2, d3 = st.columns(3)
                d1.markdown(f"**進場觸發**\n\n{card.get('entry',{}).get('trigger','—')}")
                d2.markdown(f"**停損邏輯**\n\n{card.get('risk',{}).get('stop_reason','—')}")
                d3.markdown(f"**部位建議**\n\n{card.get('position_size',{}).get('suggested_pct','—')}")

                t_col1, t_col2 = st.columns(2)
                if card.get("targets"):
                    t = card["targets"]
                    t_col1.markdown(
                        f"**目標一** {t[0].get('price','—')}\n\n"
                        f"{t[0].get('reason','—')} → {t[0].get('action','—')}")
                    if len(t) > 1:
                        t_col2.markdown(
                            f"**目標二** {t[1].get('price','—')}\n\n"
                            f"{t[1].get('reason','—')} → {t[1].get('action','—')}")

                st.caption(
                    f"策略卡生成日：{card.get('_generated_date','—')} "
                    f"（{card.get('_cache_age_days',0)} 天前）· "
                    f"有效期：{card.get('validity','—')}"
                )

            col_rm, col_rf, _ = st.columns([1, 1, 4])
            with col_rm:
                if st.button("🗑️ 移除追蹤", key=f"rm_{row['sid']}"):
                    remove_from_watchlist(USER_ID, row["sid"])
                    st.rerun()
            with col_rf:
                if st.button("🔄 重新生成策略卡", key=f"rf_{row['sid']}"):
                    force_refresh_card(USER_ID, row["sid"])
                    st.rerun()

    st.divider()
    st.caption(
        "策略卡由 Gemini 生成，90 天快取。現價即時抓取。"
        "停損位僅供參考，投資決策請自行負責。"
    )
