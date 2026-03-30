"""
chip_radar/scanner.py — 📡 籌碼雷達（每日選股排行 + 巨鯨警示）

頁面結構：
  1. 市場情緒條（SPY P/C + Z-Score）
  2. 巨鯨動向卡片（whale_alert 股票）
  3. 週期選擇 + 分數排行表
  4. 使用指南
"""

import json
import streamlit as st
import pandas as pd

from ._db  import (
    load_latest_scores, load_market_pulse, load_universe, COMPOSITE_KEY,
    load_insider_trades, load_options_flow, load_large_holders,
)
from ._ui  import (
    market_pulse_bar, whale_card, section_header, guide_box, score_color,
    options_flow_chart,
)


def render():
    # ── 市場情緒條 ────────────────────────────────────────────────
    pulse = load_market_pulse()
    market_pulse_bar(
        pc_ratio=pulse.get("pc_ratio"),
        z_score=pulse.get("pc_zscore_20"),
        date=pulse.get("date"),
    )

    # ── 載入分數資料 ──────────────────────────────────────────────
    df = load_latest_scores()
    universe = load_universe()

    col_hdr, col_cache = st.columns([8, 2])
    with col_cache:
        if st.button("🔄 重新載入", key="chip_clear_cache", use_container_width=True,
                     help="清除快取，重新從資料庫讀取最新資料"):
            st.cache_data.clear()
            st.rerun()

    if df.empty:
        st.info("尚無資料。請先執行 `python -m chip_module.fetch_daily` 抓取資料。")
        _render_empty_guide()
        return

    # 解析 signal_flags JSON
    df["flags"] = df["signal_flags"].apply(
        lambda x: json.loads(x) if x else []
    )

    # ── 巨鯨動向 ──────────────────────────────────────────────────
    whales = df[df["whale_alert"] == 1]
    if not whales.empty:
        section_header("🐋 巨鯨動向", "近期偵測到異常籌碼活動的股票")

        # 取目前選擇的 composite 欄位（預設波段）
        timeframe = st.session_state.get("chip_timeframe", "波段 (1–4週)")
        comp_col  = COMPOSITE_KEY.get(timeframe, "composite_swing")

        cols = st.columns(min(len(whales), 4))
        for i, (_, row) in enumerate(whales.iterrows()):
            with cols[i % 4]:
                whale_card(
                    ticker=row["ticker"],
                    composite=row.get(comp_col) or 50,
                    flags=row["flags"],
                    entry=bool(row.get("entry_timing")),
                    price=None,
                )
                with st.expander(f"📋 {row['ticker']} 詳情"):
                    _render_whale_detail(row["ticker"])

        st.markdown("<div style='margin-bottom:8px;'></div>", unsafe_allow_html=True)

    # ── 週期 + 欄位 + 板塊篩選 ────────────────────────────────────
    with col_hdr:
        section_header("📋 籌碼分數排行")
    col_ctrl1, col_ctrl2, col_ctrl3 = st.columns([2, 3, 3])
    with col_ctrl1:
        timeframe = st.selectbox(
            "持倉週期",
            list(COMPOSITE_KEY.keys()),
            index=1,
            key="chip_timeframe",
            help="選擇你的預期持倉週期，分數排序會隨之改變",
        )
    with col_ctrl2:
        show_cols = st.multiselect(
            "顯示欄位",
            ["內部人", "空頭", "量能", "選擇權流", "機構"],
            default=["內部人", "空頭", "量能", "選擇權流"],
            help="選擇要顯示的信號維度",
        )
    with col_ctrl3:
        all_sectors = sorted({v.get("sector", "Unknown") for v in universe.values()}) if universe else []
        selected_sectors = st.multiselect(
            "板塊篩選",
            all_sectors,
            default=[],
            key="chip_sector_filter",
            help="留空 = 顯示全部；選擇板塊後只顯示該板塊股票",
        )

    comp_col = COMPOSITE_KEY[timeframe]

    # ── 建立顯示用 DataFrame ──────────────────────────────────────
    col_map = {
        "內部人":  "insider_score",
        "空頭":    "short_score",
        "量能":    "volume_score",
        "選擇權流": "options_flow_score",
        "機構":    "institutional_score",
    }
    display_cols = [comp_col] + [col_map[c] for c in show_cols if c in col_map]

    display = df[["ticker", "date"] + display_cols + ["whale_alert", "entry_timing", "flags"]].copy()

    # 板塊篩選
    if selected_sectors and universe:
        in_sector = {t for t, info in universe.items() if info.get("sector") in selected_sectors}
        display = display[display["ticker"].isin(in_sector)].copy()

    display = display.sort_values(comp_col, ascending=False).reset_index(drop=True)

    # 日增減 delta（與昨日比較）
    from datetime import date as _date, timedelta
    yesterday = (_date.today() - timedelta(days=1)).isoformat()
    df_prev = load_latest_scores(as_of=yesterday)
    if not df_prev.empty:
        prev_map = df_prev.set_index("ticker")[comp_col].to_dict()
        def _delta(row):
            prev = prev_map.get(row["ticker"])
            if prev is None:
                return ""
            d = (row.get(comp_col) or 0) - prev
            return f"▲{d:.0f}" if d > 0.5 else (f"▼{abs(d):.0f}" if d < -0.5 else "—")
        display["日增減"] = [_delta(r) for _, r in display.iterrows()]
    else:
        display["日增減"] = ""

    # 信號欄：badge 組合
    _BADGE_MAP = {
        "insider_cluster":    "insider ↑",
        "insider_selling":    "insider ↓",
        "unusual_options":    "options異常",
        "volume_accumulation":"量能↑",
        "volume_distribution":"量能↓",
        "high_short_interest":"空頭高",
    }

    def fmt_flags(row):
        badges = []
        if row.get("whale_alert"):
            badges.append("🐋")
        if row.get("entry_timing"):
            badges.append("⚡")
        flags = row.get("flags", [])
        if not isinstance(flags, list):
            flags = []
        for f in flags:
            label = _BADGE_MAP.get(f)
            if label and label not in badges:
                badges.append(label)
        return "  ".join(badges)

    display["信號"] = [fmt_flags(r) for _, r in display.iterrows()]

    rename = {
        "ticker":              "股票",
        "date":                "更新日",
        comp_col:              f"綜合({timeframe[:2]})",
        "insider_score":       "內部人",
        "short_score":         "空頭",
        "volume_score":        "量能",
        "options_flow_score":  "選擇權流",
        "institutional_score": "機構",
    }
    table = display[["ticker", "date"] + display_cols + ["日增減", "信號"]].rename(columns=rename)

    # pandas Styler — 分數欄上色
    score_display_cols = [rename.get(c, c) for c in display_cols]

    def color_score_cell(val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        color = score_color(v)
        bg = color + "12"
        return f"color:{color};font-weight:700;background:{bg};"

    styled = (
        table.style
        .applymap(color_score_cell, subset=score_display_cols)
        .format({c: "{:.0f}" for c in score_display_cols}, na_rep="—")
        .set_properties(**{
            "font-size": "13px",
            "text-align": "center",
        })
        .set_properties(subset=["股票"], **{
            "font-weight": "700",
            "text-align": "left",
        })
    )

    st.dataframe(styled, use_container_width=True, height=420)

    # 點選查看詳情
    st.markdown("<div style='margin-top:4px;'></div>", unsafe_allow_html=True)
    selected = st.selectbox(
        "🔬 點選股票查看詳情",
        ["—"] + list(df["ticker"].unique()),
        key="chip_selected_ticker",
        help="選擇後在下方展開內部人、選擇權流、大戶持股明細",
    )
    if selected != "—":
        st.session_state["chip_dive_ticker"] = selected
        t1, t2, t3 = st.tabs(["👤 Form 4 內部人交易", "⚙️ 選擇權流量", "🏛️ 大戶持股 13D/13G"])
        with t1:
            _render_insider_detail(selected)
        with t2:
            _render_options_detail(selected)
        with t3:
            _render_holders_detail(selected)

    # ── 使用指南 ──────────────────────────────────────────────────
    with st.expander("? 如何解讀這張表格"):
        guide_box([
            "<b>綜合分數</b> 0–100：整合多個籌碼維度的加權分數，數字越高代表該週期看多信號越強",
            "<b>分數顏色</b>：🔵 75+ 強烈看多 ／ 🟢 55–74 偏多 ／ 🟡 35–54 中性 ／ 🔴 35以下 偏空",
            "<b>🐋 巨鯨動向</b>：觸發條件之一：內部人 Cluster 買入（多人短窗口）、13D 大戶進場、個股選擇權異常大單",
            "<b>⚡ 進場時機</b>：巨鯨信號 + 量能確認 + 空頭壓力低，三條件同時滿足才觸發",
            "<b>持倉週期</b>：短線權重偏量能/選擇權（快速反應），中線權重偏機構/內部人（長線佈局）",
            "資料每日台灣時間 23:30 更新，反映美股當日收盤資料",
        ])


def _render_whale_detail(ticker: str):
    t1, t2, t3 = st.tabs(["👤 內部人", "⚙️ 選擇權", "🏛️ 大戶"])
    with t1:
        _render_insider_detail(ticker)
    with t2:
        _render_options_detail(ticker)
    with t3:
        _render_holders_detail(ticker)


def _render_insider_detail(ticker: str):
    df = load_insider_trades(ticker)
    if df.empty:
        st.caption("近 90 天無內部人公開市場買賣記錄")
        return
    df = df.copy()
    df["total_value"] = df["total_value"].apply(
        lambda x: f"${x:,.0f}" if pd.notna(x) and x else "—"
    )
    df["shares"] = df["shares"].apply(
        lambda x: f"{x:,.0f}" if pd.notna(x) and x else "—"
    )
    df["price_per_share"] = df["price_per_share"].apply(
        lambda x: f"${x:.2f}" if pd.notna(x) and x else "—"
    )
    df["transaction_type"] = df["transaction_type"].map(
        {"P": "🟢 買入", "S": "🔴 賣出"}
    ).fillna(df["transaction_type"])
    df = df.rename(columns={
        "trade_date": "交易日", "insider_name": "內部人",
        "insider_title": "職位", "transaction_type": "類型",
        "shares": "股數", "price_per_share": "均價", "total_value": "總金額",
    })
    st.dataframe(df, use_container_width=True, hide_index=True, height=240)


def _render_options_detail(ticker: str):
    df = load_options_flow(ticker)
    if df.empty:
        st.caption("選擇權流量資料累積中（至少需要 1 天資料）")
        return
    st.plotly_chart(options_flow_chart(df), use_container_width=True)
    latest = df.iloc[-1]
    c1, c2, c3 = st.columns(3)
    with c1:
        cp = latest["call_volume"] / (latest["put_volume"] + 1e-9)
        st.metric("Call/Put 比", f"{cp:.2f}")
    with c2:
        total = latest["call_volume"] or 0
        otm = latest["otm_call_volume"] or 0
        st.metric("OTM Call 佔比", f"{otm/total*100:.1f}%" if total else "—")
    with c3:
        st.metric("異常 Call Strike 數", f"{latest.get('unusual_call_strikes', 0):.0f}")


def _render_holders_detail(ticker: str):
    df = load_large_holders(ticker)
    if df.empty:
        st.caption("近期無 SC 13D/13G 大戶持股申報（持股 > 5% 才觸發）")
        return
    def fmt_form(v):
        if "13D" in str(v) and "/A" not in str(v):
            return f"🔴 {v}（主動持股）"
        if "13D/A" in str(v):
            return f"🟡 {v}（修正）"
        if "13G" in str(v):
            return f"🔵 {v}（被動持股）"
        return v
    df = df.rename(columns={"filed_date": "申報日", "form_type": "表單類型", "filer_name": "申報機構"})
    df["表單類型"] = df["表單類型"].apply(fmt_form)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_empty_guide():
    with st.expander("? 快速開始"):
        guide_box([
            "在 stock_track 目錄執行：<code>python -m chip_module.fetch_daily --tickers NVDA AAPL TSLA MSFT AMD</code>",
            "首次執行約需 2–3 分鐘，之後每日排程自動更新",
            "個股選擇權歷史需累積 3 天以上，異常偵測才會啟用 Z-Score 比較",
        ])
