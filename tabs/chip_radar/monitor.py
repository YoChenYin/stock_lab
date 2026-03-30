"""
chip_radar/monitor.py — 🔔 監控警示

頁面結構：
  1. 警示條件設定（閾值滑桿）
  2. 目前觸發的警示
  3. 歷史警示記錄
  4. 使用指南
"""

import json
import streamlit as st
import pandas as pd
from datetime import date, timedelta

from ._db  import load_latest_scores, load_score_history, COMPOSITE_KEY
from ._ui  import section_header, guide_box, score_color


_DEFAULT_THRESHOLDS = {
    "composite_swing": 65,
    "insider_score":   70,
    "volume_score":    60,
    "options_flow_score": 70,
}


def render():
    # ── 警示條件設定 ──────────────────────────────────────────────
    section_header("🔔 警示條件設定", "分數超過閾值時，股票會出現在下方警示列表")

    with st.expander("⚙️ 調整閾值", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            th_composite = st.slider(
                "綜合分數（波段）閾值", 0, 100,
                st.session_state.get("th_composite", _DEFAULT_THRESHOLDS["composite_swing"]),
                key="th_composite",
                help="波段綜合分數超過此值才顯示在警示列表",
            )
            th_insider = st.slider(
                "內部人信號閾值", 0, 100,
                st.session_state.get("th_insider", _DEFAULT_THRESHOLDS["insider_score"]),
                key="th_insider",
                help="觸發條件之一（OR 邏輯，任一條件滿足即顯示）",
            )
        with col2:
            th_volume = st.slider(
                "量能信號閾值", 0, 100,
                st.session_state.get("th_volume", _DEFAULT_THRESHOLDS["volume_score"]),
                key="th_volume",
            )
            th_options = st.slider(
                "選擇權流量閾值", 0, 100,
                st.session_state.get("th_options", _DEFAULT_THRESHOLDS["options_flow_score"]),
                key="th_options",
            )

        whale_only = st.checkbox(
            "只顯示巨鯨動向觸發的股票",
            value=st.session_state.get("whale_only", False),
            key="whale_only",
            help="勾選後只顯示 whale_alert=1 的股票，忽略其他閾值條件",
        )

    # ── 載入資料 ──────────────────────────────────────────────────
    df = load_latest_scores()
    if df.empty:
        st.info("尚無資料。請先執行每日資料抓取。")
        return

    df["flags"] = df["signal_flags"].apply(lambda x: json.loads(x) if x else [])

    # ── 套用篩選條件 ──────────────────────────────────────────────
    if whale_only:
        triggered = df[df["whale_alert"] == 1].copy()
    else:
        mask = (
            (df["composite_swing"]    >= th_composite) |
            (df["insider_score"]      >= th_insider)   |
            (df["volume_score"]       >= th_volume)    |
            (df["options_flow_score"] >= th_options)
        )
        triggered = df[mask].copy()

    triggered = triggered.sort_values("composite_swing", ascending=False)

    # ── 警示列表 ──────────────────────────────────────────────────
    section_header(
        f"🚨 目前觸發警示 ({len(triggered)} 支)",
        f"更新日：{df['date'].max() if not df.empty else '—'}"
    )

    if triggered.empty:
        st.success("目前沒有股票符合警示條件。可以降低閾值或取消「只顯示巨鯨」篩選。")
    else:
        _render_alert_cards(triggered)

    # ── 近 7 天歷史警示趨勢 ───────────────────────────────────────
    section_header("📅 近 7 天警示趨勢", "每天觸發警示的股票數量")
    _render_alert_history(df["ticker"].tolist(), th_composite, th_insider, th_volume, th_options)

    # ── 使用指南 ──────────────────────────────────────────────────
    with st.expander("? 如何使用監控警示"):
        guide_box([
            "設定你的閾值後，系統每天資料更新後自動重新篩選",
            "<b>建議警示策略</b>：先開巨鯨篩選，確認有 whale_alert 的股票，再搭配量能確認",
            "閾值過低 → 太多雜訊；過高 → 可能錯過早期信號。建議波段綜合分數 65+ 作為起點",
            "觸發警示後建議切換到「🔬 個股深潛」查看各維度詳情，不要只看綜合分數",
            "⚡ 進場時機 = 最嚴格的條件：巨鯨 + 量能 + 空頭壓力同時滿足",
        ])


# ── 子區塊 ────────────────────────────────────────────────────────

def _render_alert_cards(df: pd.DataFrame):
    for i in range(0, len(df), 3):
        cols = st.columns(3)
        for j, (_, row) in enumerate(df.iloc[i:i+3].iterrows()):
            with cols[j]:
                _alert_card(row)


def _alert_card(row):
    comp  = row.get("composite_swing") or 50
    color = score_color(comp)
    flags = row.get("flags", [])
    whale = bool(row.get("whale_alert"))
    entry = bool(row.get("entry_timing"))

    flag_labels = {
        "insider_cluster":    "insider↑",
        "insider_selling":    "insider↓",
        "unusual_options":    "options異常",
        "volume_accumulation":"量能↑",
        "high_short_interest":"空頭高",
    }
    badges_html = ""
    if whale:
        badges_html += (
            '<span style="background:#eff6ff;color:#2563eb;padding:1px 7px;'
            'border-radius:8px;font-size:0.72em;font-weight:700;margin-right:3px;">🐋</span>'
        )
    if entry:
        badges_html += (
            '<span style="background:#fef3c7;color:#d97706;padding:1px 7px;'
            'border-radius:8px;font-size:0.72em;font-weight:700;margin-right:3px;">⚡</span>'
        )
    for f in flags:
        if f in flag_labels:
            fc = "#10b981" if "↑" in flag_labels[f] or "accumulation" in f else "#ef4444"
            badges_html += (
                f'<span style="background:{fc}18;color:{fc};padding:1px 7px;'
                f'border-radius:8px;font-size:0.72em;margin-right:3px;">{flag_labels[f]}</span>'
            )

    def _mini(label, val):
        c = score_color(val)
        return (
            f'<div style="text-align:center;">'
            f'<div style="font-size:0.65em;color:#94a3b8;">{label}</div>'
            f'<div style="font-weight:700;color:{c};font-size:0.9em;">{val:.0f}</div>'
            f'</div>'
        )

    mini_row = "".join([
        _mini("內部人", row.get("insider_score") or 50),
        _mini("空頭",   row.get("short_score")   or 50),
        _mini("量能",   row.get("volume_score")   or 50),
        _mini("選擇權", row.get("options_flow_score") or 50),
    ])

    st.markdown(f"""
    <div style="background:white;border:1px solid #e2e8f0;border-top:3px solid {color};
                border-radius:10px;padding:14px 16px;margin-bottom:10px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div>
          <span style="font-size:1.05em;font-weight:800;color:#1e293b;">{row['ticker']}</span>
          <div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:2px;">{badges_html}</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:1.8em;font-weight:900;color:{color};line-height:1;">{comp:.0f}</div>
          <div style="font-size:0.68em;color:#94a3b8;">波段</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:10px;
                  background:#f8fafc;border-radius:6px;padding:8px;">
        {mini_row}
      </div>
    </div>
    """, unsafe_allow_html=True)


def _render_alert_history(tickers: list, th_comp, th_ins, th_vol, th_opt):
    """查詢近 7 天每天的觸發數量，顯示簡易趨勢"""
    from ._db import get_conn
    conn = get_conn()

    today = date.today()
    rows  = []
    for delta in range(6, -1, -1):
        d = (today - timedelta(days=delta)).isoformat()
        count = conn.execute("""
            SELECT COUNT(*) FROM chip_scores
            WHERE date=? AND (
                composite_swing >= ? OR
                insider_score   >= ? OR
                volume_score    >= ? OR
                options_flow_score >= ?
            )
        """, (d, th_comp, th_ins, th_vol, th_opt)).fetchone()[0]
        rows.append({"日期": d, "觸發數": count})

    conn.close()
    hist = pd.DataFrame(rows)

    if hist["觸發數"].sum() == 0:
        st.caption("近 7 天無歷史警示記錄")
        return

    # 簡易橫條
    for _, r in hist.iterrows():
        n    = r["觸發數"]
        bar  = "█" * n if n <= 20 else "█" * 20 + f" (+{n-20})"
        date_str = r["日期"][5:]  # MM-DD
        color = "#2563eb" if n > 0 else "#e2e8f0"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;">'
            f'<span style="color:#64748b;font-size:0.78em;width:40px;">{date_str}</span>'
            f'<span style="color:{color};font-size:0.9em;letter-spacing:-1px;">{bar}</span>'
            f'<span style="color:#94a3b8;font-size:0.75em;">{n} 支</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
