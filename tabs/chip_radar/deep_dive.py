"""
chip_radar/deep_dive.py — 🔬 個股深潛（單股完整籌碼分析）

頁面結構：
  1. 股票選擇 + 週期切換
  2. 標頭：分數總覽 + 信號 badge
  3. 五個維度分數卡
  4. 雷達圖 + 歷史趨勢並排
  5. 詳情 tabs：Form4 / 選擇權流量 / 大戶持股
  6. 使用指南
"""

import json
import streamlit as st
import pandas as pd

from ._db import (
    load_latest_scores, load_score_history,
    load_insider_trades, load_options_flow,
    load_large_holders, load_universe, COMPOSITE_KEY,
)
from ._ui import (
    score_card, section_header, guide_box, score_color, score_label,
    score_history_chart, options_flow_chart,
)


_SCORE_CARDS = [
    ("insider_score",       "內部人交易",   "👤",
     "Form 4 Cluster 分析：多人短窗口買入為強信號，CEO/CFO 買入比董事更有意義"),
    ("short_score",         "空頭動能",     "📉",
     "空頭比例越低、下降趨勢越明顯，分數越高（偏多）"),
    ("volume_score",        "量能信號",     "📊",
     "OBV 背離偵測：OBV 上漲但股價橫盤 = 主力吸籌（分數高）"),
    ("options_flow_score",  "選擇權流量",   "⚙️",
     "個股選擇權：OTM call 暴增 + 異常大單 = 有人在偷偷佈局"),
    ("institutional_score", "機構持倉",     "🏛️",
     "季報機構持倉加權增減比，變化緩慢但代表長線資金方向"),
]


def render():
    # ── 股票選擇 ──────────────────────────────────────────────────
    all_scores = load_latest_scores()
    universe   = load_universe()
    tickers    = sorted(universe.keys()) if universe else (
        sorted(all_scores["ticker"].tolist()) if not all_scores.empty else []
    )

    col_pick, col_tf, _ = st.columns([2, 2, 4])
    with col_pick:
        default_idx = 0
        saved = st.session_state.get("chip_dive_ticker")
        if saved and saved in tickers:
            default_idx = tickers.index(saved)
        ticker = st.selectbox(
            "選擇股票",
            tickers if tickers else ["—"],
            index=default_idx,
            key="chip_dive_ticker",
            help="輸入股票代號搜尋",
        )
    with col_tf:
        timeframe = st.selectbox(
            "持倉週期",
            list(COMPOSITE_KEY.keys()),
            index=1,
            key="chip_dive_timeframe",
            help="切換週期查看不同時間框架的籌碼分數",
        )

    if not tickers or ticker == "—":
        st.info("請先執行每日資料抓取，或在左側籌碼雷達選擇股票。")
        return

    comp_col = COMPOSITE_KEY[timeframe]

    # ── 取資料 ────────────────────────────────────────────────────
    row = all_scores[all_scores["ticker"] == ticker]
    if row.empty:
        st.warning(f"找不到 {ticker} 的籌碼資料。")
        return
    row = row.iloc[0].to_dict()

    flags      = json.loads(row.get("signal_flags") or "[]")
    composite  = row.get(comp_col) or 50
    whale      = bool(row.get("whale_alert"))
    entry      = bool(row.get("entry_timing"))
    update_date = row.get("date", "—")

    # ── 標頭 ──────────────────────────────────────────────────────
    _render_header(ticker, composite, timeframe, flags, whale, entry, update_date)

    # ── 五個維度分數卡 ────────────────────────────────────────────
    cols = st.columns(5)
    for i, (key, label, icon, tooltip) in enumerate(_SCORE_CARDS):
        with cols[i]:
            score_card(label, row.get(key), icon=icon, tooltip=tooltip)

    st.markdown("<div style='margin:12px 0 4px;'></div>", unsafe_allow_html=True)

    # ── 分數歷史趨勢 ─────────────────────────────────────────────
    history = load_score_history(ticker, days=60)
    section_header(
        "分數歷史趨勢",
        "灰色虛線 = 50 分中性基準" if not history.empty else "資料累積中（每日更新後顯示）"
    )
    if len(history) >= 2:
        st.plotly_chart(score_history_chart(history, comp_col), use_container_width=True)
    else:
        st.caption("歷史資料累積中，3 天後顯示趨勢圖")

    # ── 詳情 tabs ─────────────────────────────────────────────────
    t1, t2, t3 = st.tabs(["👤 Form 4 內部人交易", "⚙️ 選擇權流量", "🏛️ 大戶持股 13D/13G"])

    with t1:
        _render_insider(ticker)
    with t2:
        _render_options(ticker)
    with t3:
        _render_large_holders(ticker)

    # ── 使用指南 ──────────────────────────────────────────────────
    with st.expander("? 如何解讀個股分析"):
        guide_box([
            "<b>歷史趨勢圖</b>：分數 0–100，灰虛線為 50 中性基準，趨勢持續向上優於單日飆高",
            "<b>內部人 Cluster</b>：短窗口（5天內）多人同時用自有資金買入，信號強度遠高於單人",
            "<b>OTM call 暴增</b>：行權價 > 現價 5% 的 call 成交量突然放大，暗示有人預期近期大漲",
            "<b>空頭動能</b>：空頭比例下降 = 空頭在撤退，做多壓力減小",
            "<b>信號疊加</b>：巨鯨 + 量能 + 空頭都指向同一方向，可信度最高",
            "<b>中線分析</b>：機構持倉季報 45 天延遲，分數反映的是一季前的機構動向，僅供參考",
        ])


# ── 子區塊渲染 ────────────────────────────────────────────────────

def _render_header(ticker, composite, timeframe, flags, whale, entry, update_date):
    color = score_color(composite)
    label = score_label(composite)

    badges = ""
    if whale:
        badges += '<span style="background:#eff6ff;color:#2563eb;padding:3px 10px;border-radius:12px;font-size:0.78em;font-weight:700;margin-right:6px;">🐋 巨鯨動向</span>'
    if entry:
        badges += '<span style="background:#fef3c7;color:#d97706;padding:3px 10px;border-radius:12px;font-size:0.78em;font-weight:700;margin-right:6px;">⚡ 進場時機</span>'

    flag_map = {
        "insider_cluster":    ("insider ↑ cluster", "#10b981"),
        "insider_selling":    ("insider ↓ 賣出", "#ef4444"),
        "unusual_options":    ("選擇權異常", "#2563eb"),
        "volume_accumulation":("量能吸籌", "#10b981"),
        "volume_distribution":("量能出貨", "#ef4444"),
        "high_short_interest":("空頭偏高", "#f59e0b"),
    }
    for f in flags:
        if f in flag_map and f not in ("whale_alert", "entry_timing"):
            name, fc = flag_map[f]
            badges += (
                f'<span style="background:{fc}18;color:{fc};padding:3px 10px;'
                f'border-radius:12px;font-size:0.78em;font-weight:600;margin-right:6px;">{name}</span>'
            )

    bar_pct = int(composite)
    st.markdown(f"""
    <div style="background:white;border:1px solid #e2e8f0;border-left:5px solid {color};
                border-radius:12px;padding:20px 24px;margin-bottom:16px;
                box-shadow:0 2px 8px rgba(0,0,0,0.04);">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
        <div>
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <span style="background:#1e293b;color:white;padding:4px 12px;
                         border-radius:6px;font-weight:700;font-size:0.9em;">{ticker}</span>{badges}
          </div>
          <div style="color:#64748b;font-size:0.8em;">
            更新日：{update_date} ／ 週期：{timeframe}
          </div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:0.72em;font-weight:600;color:#64748b;
                      letter-spacing:0.4px;margin-bottom:2px;">籌碼綜合分數</div>
          <div style="font-size:2.8em;font-weight:900;color:{color};line-height:1;">{composite:.0f}</div>
          <div style="color:#94a3b8;font-size:0.75em;">{label}</div>
        </div>
      </div>
      <div style="margin-top:12px;background:#f1f5f9;border-radius:6px;height:7px;">
        <div style="width:{bar_pct}%;background:{color};height:7px;border-radius:6px;"></div>
      </div>
    </div>
    """, unsafe_allow_html=True)


def _render_insider(ticker):
    df = load_insider_trades(ticker)
    if df.empty:
        st.caption("近 90 天無內部人公開市場買賣記錄（或資料尚未抓取）")
        return

    # 格式化
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
        "trade_date":        "交易日",
        "insider_name":      "內部人",
        "insider_title":     "職位",
        "transaction_type":  "類型",
        "shares":            "股數",
        "price_per_share":   "均價",
        "total_value":       "總金額",
    })

    st.dataframe(df, use_container_width=True, hide_index=True, height=280)

    with st.expander("? 解讀說明"):
        guide_box([
            "只顯示公開市場 <b>買入(P)</b> 和 <b>賣出(S)</b>，排除行使選擇權",
            "多人在短時間內買入（Cluster）比單人買入信號更強",
            "CEO/CFO 買入可信度 > 一般董事（職位越高，掌握內部資訊越充分）",
            "金額 < $50,000 的買入通常是員工持股計畫，信號強度較低",
        ])


def _render_options(ticker):
    df = load_options_flow(ticker)
    if df.empty:
        st.caption("選擇權流量資料累積中（每日收盤後更新，至少需要 1 天資料）")
        return

    # 圖表
    st.plotly_chart(options_flow_chart(df), use_container_width=True)

    # 最新一天數據指標
    latest = df.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cp = latest["call_volume"] / (latest["put_volume"] + 1e-9)
        st.metric("Call/Put 比", f"{cp:.2f}",
                  help="大於 1 代表 call 需求高於 put（偏多情緒）")
    with c2:
        total = latest["call_volume"] or 0
        otm   = latest["otm_call_volume"] or 0
        pct   = otm / total * 100 if total else 0
        st.metric("OTM Call 佔比", f"{pct:.1f}%",
                  help="行權價 > 現價 5% 的 call 佔比，越高代表有人在押注大漲")
    with c3:
        st.metric("異常 Call Strike 數", f"{latest.get('unusual_call_strikes', 0):.0f}",
                  help="volume/OI > 3 的行權價數量，越多代表異常佈局越明顯")
    with c4:
        iv_c = latest.get("avg_call_iv") or 0
        iv_p = latest.get("avg_put_iv") or 0
        skew = iv_p - iv_c
        st.metric("IV Skew (Put-Call)", f"{skew:.3f}",
                  help="正值代表 put 隱含波動率高於 call，市場偏謹慎")

    with st.expander("? 解讀說明"):
        guide_box([
            "<b>OTM call 暴增</b>：行權價高出現價 5% 以上的 call 成交量突然增加，往往暗示有人預期近期大漲",
            "<b>異常 Strike</b>：同一天有多個不同行權價的 call 都出現 volume/OI > 3，代表整體偏多佈局",
            "<b>綠色柱 = Call，紅色柱 = Put</b>（向下顯示）",
            "<b>藍色虛線 = OTM Call 成交量</b>，趨勢向上值得關注",
        ])


def _render_large_holders(ticker):
    df = load_large_holders(ticker)
    if df.empty:
        st.caption("近期無 SC 13D/13G 大戶持股申報（持股 > 5% 的大戶進出才會觸發）")
        _explain_13dg()
        return

    df = df.rename(columns={
        "filed_date": "申報日",
        "form_type":  "表單類型",
        "filer_name": "申報機構",
    })

    def fmt_form(v):
        if "13D" in str(v) and "/A" not in str(v):
            return f"🔴 {v}（主動持股）"
        if "13D/A" in str(v):
            return f"🟡 {v}（修正）"
        if "13G" in str(v):
            return f"🔵 {v}（被動持股）"
        return v

    df["表單類型"] = df["表單類型"].apply(fmt_form)
    st.dataframe(df, use_container_width=True, hide_index=True)
    _explain_13dg()


def _explain_13dg():
    with st.expander("? 13D/13G 是什麼"):
        guide_box([
            "<b>SC 13D（主動）</b>：持股超過 5%，且有意影響公司決策（activist investor）。最強的巨鯨信號之一",
            "<b>SC 13G（被動）</b>：持股超過 5%，但不打算干預公司，多為 ETF 或指數基金",
            "<b>SC 13D/A / 13G/A</b>：修正申報，代表持倉有變化（增減）",
            "申報期限：取得後 10 天內（13D）/ 45 天內（13G），有延遲性",
        ])
