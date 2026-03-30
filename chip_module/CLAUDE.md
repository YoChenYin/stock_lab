# Stock Lab — Chip Module 開發文件

## 專案背景

這是 `stock_track` 專案下的一個子模組，目標是建立一套**美股籌碼動能追蹤系統**，整合進現有的 Streamlit + SQLite stock lab（部署在 Zeabur）。

---

## 技術棧

- **前端**：Python + Streamlit
- **資料庫**：SQLite（`chip_module/chip.db`）
- **部署**：Zeabur（需掛 Persistent Volume 防止容器重啟資料消失）
- **排程**：Zeabur Cron Job，每天台灣時間 23:30 觸發

---

## 模組結構

```
stock_track/
└── chip_module/
    ├── __init__.py
    ├── fetch_daily.py          # 每日排程主入口
    ├── verify.py               # 資料驗證工具
    ├── requirements.txt
    ├── zeabur_cron_example.yaml
    ├── db/
    │   ├── __init__.py
    │   └── schema.py           # SQLite table 定義 + init_db()
    ├── fetchers/
    │   ├── __init__.py
    │   ├── prices.py           # yfinance OHLCV + OBV/CMF/MFI
    │   ├── insider.py          # SEC EDGAR Form 4 ← 目前有問題
    │   ├── short_interest.py   # yfinance short data
    │   └── options_sentiment.py # SPY 選擇權鏈 P/C Ratio
    └── signals/                # 尚未實作
        └── __init__.py
```

執行方式（需在 `chip_module/` 的上一層 `stock_track/` 目錄執行）：
```bash
python -m chip_module.fetch_daily --tickers NVDA AAPL TSLA
python -m chip_module.verify --tickers NVDA AAPL TSLA
```

---

## 各資料源狀態

| 資料源 | 狀態 | 來源 | 更新頻率 |
|--------|------|------|----------|
| 股價 + 技術指標 | ✅ 正常 | yfinance | 每日 |
| 空頭興趣 | ✅ 正常 | yfinance (`sharesShort`, `shortRatio`) | 每日 |
| 選擇權 P/C Ratio | ✅ 正常 | yfinance SPY 選擇權鏈計算 | 每日 |
| 機構持倉 | ✅ 正常 | yfinance `institutional_holders` | 每季 |
| 內部人交易 Form 4 | ❌ 0 筆插入 | SEC EDGAR | 每日 |

---

## ❌ 待修問題：EDGAR Form 4 內部人交易

### 症狀
```
[insider] 處理 NVDA...
  NVDA: 0 筆交易 inserted
```
沒有任何錯誤訊息，但也沒有插入任何資料。

### 已嘗試的修法（均無效）
1. 原始版：用 `-index.json` 找 XML → `404 Not Found`
2. 改用 `www.sec.gov/Archives/` 路徑 → 仍然 `404`
3. 改用 `primaryDocument` 欄位直接構造 XML URL → `mismatched tag` XML 解析錯誤
4. 加入 `.htm` 中抽取 `<XML>...</XML>` 片段 + lxml recover 模式 → 0 筆，無報錯

### EDGAR API 資訊
- CIK 查詢（正常）：`https://www.sec.gov/files/company_tickers.json`
- Submissions API（正常）：`https://data.sec.gov/submissions/CIK{cik}.json`
  - 回傳欄位包含：`form`, `filingDate`, `accessionNumber`, `primaryDocument`
  - NVDA 的 CIK = `1045810`
- Archives 路徑（有問題）：`https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{file}`

### 診斷建議
在 `insider.py` 的 `_parse_form4` 裡加入 print 確認實際在拿什麼：

```python
# 在 fetch_insider 的 for loop 裡，找到 form == "4" 之後加：
print(f"  accession={accession}, primaryDoc={primary_doc}, date={filed_date}")
```

然後手動驗證其中一個 URL 是否可以存取：
```bash
# 用真實的 accession number 測試
curl -H "User-Agent: StockLab research@test.com" \
  "https://www.sec.gov/Archives/edgar/data/1045810/{acc_clean}/{primary_doc}"
```

### 可能的根本原因
- `primaryDocument` 對應到的檔案可能不是 Form 4 XML 本體，而是 EDGAR 的包裝格式
- Form 4 的實際 XML 可能叫 `doc4.xml` 或 `wf-form4_*.xml`，需要先拿 filing index 確認
- 建議改用 EDGAR full-text search API 直接搜尋：
  `https://efts.sec.gov/LATEST/search-index?q=%22form+4%22&dateRange=custom&startdt={since}&enddt={today}&entity={ticker}`

### 替代方案（如果 EDGAR 持續有問題）
使用 Financial Modeling Prep API（250 calls/日免費）：
```python
url = f"https://financialmodelingprep.com/api/v4/insider-trading?symbol={ticker}&limit=20&apikey={API_KEY}"
```

---

## SQLite Schema 重點

```sql
-- 內部人交易
CREATE TABLE insider_trades (
    ticker TEXT, report_date TEXT, trade_date TEXT,
    insider_name TEXT, insider_title TEXT,
    transaction_type TEXT,  -- P=買入, S=賣出
    shares REAL, price_per_share REAL, total_value REAL,
    shares_owned_after REAL,
    accession_number TEXT UNIQUE  -- 防重複 key
);

-- 籌碼綜合分數（尚未實作）
CREATE TABLE chip_scores (
    ticker TEXT, date TEXT,
    insider_score REAL,       -- 0-100
    short_score REAL,
    volume_score REAL,
    options_score REAL,
    institutional_score REAL,
    composite_short REAL,     -- 短線加權
    composite_swing REAL,     -- 波段加權
    composite_mid REAL,       -- 中線加權
    signal_flags TEXT,        -- JSON
    UNIQUE(ticker, date)
);
```

---

## 下一步開發計畫

### Phase 2：signals/ 模組（尚未開始）
計算每日籌碼綜合分數，存入 `chip_scores`：

```
signals/
├── insider_signal.py     # 近30/90天買賣比 → 0-100分
├── short_signal.py       # 空頭變化趨勢 → 0-100分
├── volume_signal.py      # OBV背離偵測 → 0-100分
├── options_signal.py     # P/C Z-Score → 0-100分
├── institutional_signal.py # 機構持倉變化 → 0-100分
└── composite.py          # 加權合成，輸出三個週期分數
```

### Phase 3：Streamlit UI（4個 page）
1. **Daily Scanner** — 依 composite_swing 排序的每日選股清單
2. **Deep Dive** — 單股全籌碼儀表板（圖表 + 歷史趨勢）
3. **Backtest** — 信號歷史有效性回測
4. **Monitor** — 自選股警示（分數突破閾值通知）

---

## 注意事項

- `USER_AGENT` 在 `insider.py` 裡需換成真實 email（SEC 要求）
- yfinance 偶有 rate limit，重試等待即可
- SQLite 在 Zeabur 需掛 Persistent Volume，否則重新部署後資料消失
- 所有 fetcher 都是冪等設計（重複執行不會壞資料），用 `ON CONFLICT DO UPDATE/NOTHING`
