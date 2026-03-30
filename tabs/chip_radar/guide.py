"""
chip_radar/guide.py — 📖 美股籌碼操作指南

分三個路徑：
  全部看 / 選股掃描 / 個股深潛
"""

import streamlit as st


# ── Helpers ──────────────────────────────────────────────────────

def _section_header(icon: str, title: str, subtitle: str = ""):
    sub = (f'<div style="font-size:0.83em;color:#64748b;margin-top:2px;">{subtitle}</div>'
           if subtitle else "")
    st.markdown(f"""<div style="padding:16px 0 8px;">
      <div style="font-size:1.05em;font-weight:700;color:#0f172a;">{icon} {title}</div>{sub}
    </div>""", unsafe_allow_html=True)


def _step_card(num: int, title: str, body: str, tip: str = "", rule: str = ""):
    tip_html = (f'<div style="background:#f0fdf4;border-left:3px solid #10b981;'
                f'padding:8px 12px;border-radius:0 6px 6px 0;font-size:0.8em;'
                f'color:#166534;margin-top:8px;">💡 {tip}</div>') if tip else ""
    rule_html = (f'<div style="background:#fff7ed;border-left:3px solid #f59e0b;'
                 f'padding:8px 12px;border-radius:0 6px 6px 0;font-size:0.8em;'
                 f'color:#92400e;margin-top:6px;">📐 操作規則：{rule}</div>') if rule else ""
    st.markdown(f"""
    <div style="border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px;margin-bottom:10px;">
      <div style="display:flex;align-items:flex-start;gap:12px;">
        <div style="background:#1e293b;color:white;border-radius:6px;padding:3px 9px;
                    font-size:0.78em;font-weight:700;flex-shrink:0;margin-top:1px;">步驟 {num}</div>
        <div style="flex:1;">
          <div style="font-size:0.95em;font-weight:600;color:#1e293b;margin-bottom:4px;">{title}</div>
          <div style="font-size:0.83em;color:#475569;line-height:1.6;">{body}</div>{tip_html}{rule_html}
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)


def _score_legend():
    st.markdown("""
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0;">
      <div style="background:#eff6ff;border-radius:8px;padding:10px;text-align:center;">
        <div style="font-size:1.1em;">🔵</div>
        <div style="font-size:0.72em;font-weight:700;color:#1d4ed8;margin-top:4px;">75–100</div>
        <div style="font-size:0.68em;color:#1e40af;">強烈看多</div>
      </div>
      <div style="background:#f0fdf4;border-radius:8px;padding:10px;text-align:center;">
        <div style="font-size:1.1em;">🟢</div>
        <div style="font-size:0.72em;font-weight:700;color:#15803d;margin-top:4px;">55–74</div>
        <div style="font-size:0.68em;color:#166534;">偏多</div>
      </div>
      <div style="background:#fefce8;border-radius:8px;padding:10px;text-align:center;">
        <div style="font-size:1.1em;">🟡</div>
        <div style="font-size:0.72em;font-weight:700;color:#854d0e;margin-top:4px;">35–54</div>
        <div style="font-size:0.68em;color:#92400e;">中性</div>
      </div>
      <div style="background:#fff1f2;border-radius:8px;padding:10px;text-align:center;">
        <div style="font-size:1.1em;">🔴</div>
        <div style="font-size:0.72em;font-weight:700;color:#b91c1c;margin-top:4px;">0–34</div>
        <div style="font-size:0.68em;color:#991b1b;">偏空</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── Main ─────────────────────────────────────────────────────────

def render():
    st.header("📖 美股籌碼 — 操作指南")
    st.caption(
        "三個功能頁（籌碼雷達 / 個股深潛 / 監控警示）的完整說明。"
        "資料每日台灣時間 23:30 更新，反映美股當日收盤資料。"
    )

    mode = st.radio(
        "選擇要閱讀的部分：",
        ["🔍 全部看（推薦第一次閱讀）", "📡 籌碼雷達 & 選股", "🔬 個股深潛", "🔔 監控警示"],
        horizontal=True,
    )
    show_all    = "全部" in mode
    show_scan   = show_all or "雷達" in mode
    show_dive   = show_all or "深潛" in mode
    show_alert  = show_all or "監控" in mode

    st.divider()

    # ── Part 1 — 五維指標總覽（共用）────────────────────────────
    _section_header("🧭", "Part 1 — 五個籌碼維度解讀",
                    "每支股票的籌碼分數由五個維度各自 0–100 分，再依週期加權合成")

    dims = [
        ("👤", "內部人交易", "insider_score",
         "Form 4 申報：公司高管用自有資金在公開市場買入（P）或賣出（S）。"
         "多人短窗口內同時買入（Cluster）比單人信號更強。CEO/CFO 買入優於一般董事。"),
        ("📉", "空頭動能", "short_score",
         "空頭比例（Short Interest）佔流通股的比例，以及近期變化趨勢。"
         "分數高 = 空頭比例低且在下降，代表做空壓力在消退（對多頭有利）。"),
        ("📊", "量能信號", "volume_score",
         "OBV（能量潮）背離偵測：OBV 上漲但股價橫盤 = 主力在悄悄吸籌，分數高。"
         "反之 OBV 下滑而股價尚未跌 = 出貨信號，分數低。"),
        ("⚙️", "選擇權流量", "options_flow_score",
         "個股選擇權分析：OTM Call 暴增（行權價 > 現價 5%）+ 異常大單 = 有人提前佈局看漲。"
         "搭配 IV Skew（Put IV − Call IV）判斷市場情緒偏向。"),
        ("🏛️", "機構持倉", "institutional_score",
         "13F 季報：持股 > 1 億美元的機構申報。加權計算機構增減持比例。"
         "注意：13F 有 45 天申報延遲，分數反映的是一季前的機構動向，僅供中線參考。"),
    ]

    cols = st.columns(5)
    for col, (icon, name, key, desc) in zip(cols, dims):
        with col:
            st.markdown(f"""
            <div style="border:1px solid #e2e8f0;border-radius:10px;padding:12px;min-height:180px;">
              <div style="font-size:1.2em;margin-bottom:4px;">{icon}</div>
              <div style="font-size:0.88em;font-weight:700;color:#1e293b;margin-bottom:6px;">{name}</div>
              <div style="font-size:0.75em;color:#64748b;line-height:1.55;">{desc}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<div style='margin:12px 0 4px;'></div>", unsafe_allow_html=True)
    _score_legend()

    st.divider()

    # ── Part 2 — 綜合分數 & 週期（共用）────────────────────────
    _section_header("⚖️", "Part 2 — 綜合分數與持倉週期",
                    "三個週期對五個維度的加權比例不同")

    st.markdown("""
    | 週期 | 主要參考維度 | 適合誰 |
    |------|-------------|--------|
    | **短線 (1–5天)** | 量能 40% + 選擇權 30% + 內部人 20% + 空頭 10% | 當沖 / 短期交易者 |
    | **波段 (1–4週)** | 量能 25% + 選擇權 25% + 內部人 25% + 空頭 15% + 機構 10% | 多數使用者的預設 |
    | **中線 (1–3月)** | 機構 35% + 內部人 30% + 空頭 20% + 量能 15% | 中長線佈局 |
    """)

    st.markdown("""
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                padding:12px 16px;font-size:0.82em;color:#475569;line-height:1.7;margin-top:8px;">
    <b>信號疊加原則</b>：當 2 個以上的維度同時偏多，可信度遠高於單一維度觸發。<br>
    最強組合：🐋 <b>內部人 Cluster 買入</b> + 📊 <b>OBV 背離向上</b> + ⚙️ <b>OTM Call 異常</b> 三者同時出現。
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ── Part 3 — 籌碼雷達操作流程 ────────────────────────────────
    if show_scan:
        _section_header("📡", "Part 3 — 籌碼雷達操作流程",
                        "每日選股排行 + 巨鯨動向的完整使用步驟")

        _step_card(1, "確認市場情緒（頂部橫條）",
            "頁面頂部的市場情緒條顯示 <b>SPY P/C Ratio</b> 的 Z-Score："
            "<br>Z > +1.5σ = 市場極度恐慌，逆向偏多；"
            "<br>Z < −1.5σ = 市場過度樂觀，注意回調風險。"
            "<br>這是大盤背景，不代表個股方向，但能幫你判斷進場時機。",
            tip="恐慌期進場成功率歷史上較高，但需搭配個股信號確認",
            rule="Z-Score 中性（−0.5 到 +0.5）時按個股信號操作；極端值時加大或縮小倉位")

        _step_card(2, "選擇持倉週期",
            "右上角選擇你預期的持倉時間：短線 / 波段 / 中線。"
            "不同週期的加權不同，排行結果會明顯改變。"
            "建議先用<b>波段</b>作為預設，熟悉後再切換。",
            tip="同一支股票在不同週期的分數可能差很多，波段強不代表短線也強")

        _step_card(3, "用板塊篩選縮小範圍",
            "「板塊篩選」下拉選單讓你只看特定 GICS 板塊（如 Information Technology）。"
            "全市場 517 支股票一次看太多，建議鎖定 1–2 個你熟悉的板塊。",
            tip="板塊輪動時先找近期資金流入的板塊，再在板塊內找個股",
            rule="板塊 ETF（如 XLK、XLV）分數高 = 資金正在進入該板塊，個股選板塊內高分者")

        _step_card(4, "觀察巨鯨動向卡片",
            "頁面上方的 🐋 卡片是當日觸發 <b>whale_alert</b> 的股票，條件最嚴格："
            "<br>• 內部人 Cluster（5 天內 3+ 人同時買入）<br>• 13D 大戶新進場<br>• 個股選擇權異常大單。"
            "<br>點擊卡片下方「📋 詳情」可展開內部人清單、選擇權流量、大戶申報。",
            tip="巨鯨卡片數量少（通常 0–5 支）才有意義；一次出現 20+ 支通常是資料問題",
            rule="先看 🐋 → 再確認 ⚡ 進場時機（三條件同時）→ 最後查個股深潛驗證")

        _step_card(5, "解讀分數排行表",
            "「日增減」欄顯示今日分數與昨日的差值（▲ 上升 / ▼ 下降），"
            "快速找出<b>今日分數突然跳升</b>的股票。"
            "<br>點擊表格下方「🔬 點選股票查看詳情」可直接展開完整分析，"
            "不需要切換到個股深潛頁面。",
            tip="單日 ▲10+ 值得優先關注，但需確認不是資料異常（如選擇權到期日）",
            rule="綜合分數 65+ 且日增減 ▲5+ = 籌碼正在快速轉強，為主要關注清單")

        st.divider()

    # ── Part 4 — 個股深潛操作流程 ────────────────────────────────
    if show_dive:
        _section_header("🔬", "Part 4 — 個股深潛操作流程",
                        "單股完整籌碼分析的解讀方法")

        _step_card(6, "確認分數歷史趨勢",
            "分數歷史趨勢圖顯示近 60 天的綜合分數走勢。"
            "<br>• <b>趨勢持續向上</b>：籌碼在穩定累積，信號可信度高。"
            "<br>• <b>單日飆高</b>：可能是一次性事件（如選擇權到期），需交叉確認。"
            "<br>• <b>分數剛從低點反轉</b>：可能是早期進場機會。",
            tip="50 分以上且趨勢向上持續 5 天以上 = 籌碼健康",
            rule="分數 < 35 且持續下降 = 暫時迴避，無論其他信號多好看")

        _step_card(7, "解讀 Form 4 內部人交易（👤 tab）",
            "重點看三件事："
            "<br>① <b>Cluster 買入</b>：5 天內 3 人以上同時用自有資金買入，信號最強。"
            "<br>② <b>職位</b>：CEO/CFO 買入 > 董事買入 > 一般高管。"
            "<br>③ <b>金額</b>：> $100,000 的買入才有實質意義，小額可能是例行持股計畫。",
            tip="賣出信號比買入弱，因為賣出原因很多（稅務、個人需求）",
            rule="近 30 天有 Cluster 買入（3人+，各 > $50K）= 內部人信號確認")

        _step_card(8, "解讀選擇權流量（⚙️ tab）",
            "重點看三個指標："
            "<br>① <b>OTM Call 佔比 > 30%</b>：有人在押注近期大漲（不只是對沖）。"
            "<br>② <b>異常 Call Strike 數 > 3</b>：多個不同行權價同時異常，是整體偏多佈局。"
            "<br>③ <b>IV Skew（Put IV − Call IV）< 0</b>：Call IV 高於 Put IV，罕見但是強力多頭信號。",
            tip="選擇權到期日（每月第三週五）前後的異常數據需打折看",
            rule="OTM Call 佔比 > 30% + 異常 Strike > 3 + Call/Put 比 > 1.5 = 選擇權確認進場")

        _step_card(9, "解讀大戶持股 13D/13G（🏛️ tab）",
            "<b>13D（主動持股）</b>：持股超過 5%，打算影響公司決策。"
            "這是最強的巨鯨進場信號，通常代表有人認為公司被低估並準備施壓管理層。"
            "<br><b>13G（被動持股）</b>：超過 5% 但不干預，多為 ETF/指數基金，信號弱。"
            "<br><b>13D/A 修正</b>：持倉增加 = 大戶加碼，持倉減少 = 大戶撤退。",
            tip="13D 新申報後的 10 天是市場消化期，往往伴隨股價波動加大",
            rule="13D 新申報（非修正）= 主動大戶進場，是最強的中線多頭信號之一")

        _step_card(10, "信號疊加 — 最終決策",
            "單一信號的可信度有限。決策前確認信號疊加數量："
            "<br>• 1 個信號 → 觀察清單，不行動"
            "<br>• 2 個信號同方向 → 可考慮小倉位試水"
            "<br>• 3+ 個信號同方向（尤其包含內部人 + 量能）→ 高可信度進場條件",
            tip="巨鯨標誌（🐋）= 至少 2 個強信號同時觸發，系統已幫你做了第一層篩選",
            rule="進場前問：分數是否 ≥ 60？趨勢是否向上？有無 2+ 個維度確認？")

        st.divider()

    # ── Part 5 — 監控警示操作流程 ────────────────────────────────
    if show_alert:
        _section_header("🔔", "Part 5 — 監控警示操作流程",
                        "自動篩選超過閾值的股票")

        _step_card(11, "設定適合自己的閾值",
            "「警示條件設定」中的四個滑桿控制篩選敏感度。"
            "<br>• <b>閾值太低</b>（如 40 分）：太多雜訊，每天幾十支股票觸發。"
            "<br>• <b>閾值太高</b>（如 85 分）：太嚴格，可能錯過早期信號。"
            "<br>建議起點：綜合分數 65 分，內部人 70 分。",
            tip="先用預設值觀察一週，看觸發數量是否合理（5–15 支為佳）",
            rule="波段綜合 65+ 為警示起點；若每天觸發 > 20 支則調高 5 分")

        _step_card(12, "只顯示巨鯨篩選",
            "勾選「只顯示巨鯨動向觸發的股票」是最嚴格的篩選模式。"
            "只有 whale_alert = 1（多個強信號同時觸發）的股票才顯示。"
            "適合每天只想看最重要的 1–3 支股票的使用者。",
            tip="建議每天先開巨鯨篩選，確認有無高優先級標的，再切回分數篩選看更多")

        _step_card(13, "近 7 天趨勢確認",
            "警示頁底部的近 7 天橫條圖顯示每天觸發警示的股票數量。"
            "<br>• 數量突然大增：可能是大盤情緒轉變，需搭配市場情緒條解讀。"
            "<br>• 連續多天同一支股票觸發：信號持續性強，比單日觸發更可信。",
            tip="同一支股票連續 3 天以上觸發警示 = 籌碼持續累積，優先關注")

        st.divider()

    # ── Part 6 — 資料更新頻率（共用）────────────────────────────
    _section_header("📅", "資料更新頻率與延遲",
                    "了解每個指標的時效性，避免誤判")

    st.markdown("""
    | 資料 | 更新頻率 | 延遲 | 說明 |
    |------|---------|------|------|
    | 股價 / 量能 / OBV | 每日 | 收盤後約 1 小時 | yfinance 即時更新 |
    | 空頭興趣 | 每兩週 | FINRA 申報延遲約 1 週 | 月中 + 月底更新 |
    | 選擇權流量 | 每日 | 收盤後約 2 小時 | 個股選擇權鏈 |
    | Form 4 內部人交易 | 每日 | SEC 申報期限 2 個交易日 | 高管必須在交易後 2 日內申報 |
    | 13D/13G 大戶持股 | 不定期 | 取得後 10 天（13D）/ 45 天（13G）| 持股超過 5% 才觸發申報 |
    | 機構持倉 13F | 每季 | 季末後 45 天 | 最新數據反映的是上一季末的持倉 |
    | SPY P/C Ratio | 每日 | 收盤後 | 大盤情緒指標 |
    """)

    st.divider()
    st.caption(
        "📌 本指南基於平台資料邏輯撰寫，不構成投資建議。"
        "所有操作決策由使用者自行負責。市場有風險，投資前請確認自身風險承受能力。"
    )
