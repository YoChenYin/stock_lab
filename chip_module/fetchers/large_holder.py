"""
fetchers/large_holder.py
抓取 EDGAR SC 13D / SC 13G 大戶持股申報（持股 > 5%）

13D：主動投資者（可能要求公司改變）
13G：被動持有（ETF、共同基金等）
13D/A、13G/A：修正申報（重要！代表持倉有變化）

使用 EDGAR EFTS 全文搜尋 API，按目標公司 CIK 搜尋相關申報。
"""

import time
import requests
from datetime import date, timedelta
from typing import List, Optional

from ..db.schema import get_conn

EDGAR_BASE  = "https://data.sec.gov"
EDGAR_WWW   = "https://www.sec.gov"
EFTS_BASE   = "https://efts.sec.gov"
HEADERS     = {"User-Agent": "StockLab research@youremail.com"}
SLEEP_SEC   = 0.2

FORM_TYPES  = ["SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A"]


def _get_json(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        time.sleep(SLEEP_SEC)
        return r.json()
    except Exception as e:
        print(f"  [large_holder] 請求失敗 {url}: {e}")
        time.sleep(1)
        return None


def _ticker_to_cik(ticker: str) -> Optional[str]:
    data = _get_json(f"{EDGAR_WWW}/files/company_tickers.json")
    if not data:
        return None
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    return None


def fetch_large_holders(tickers: List[str], days_back: int = 90, db_path=None):
    """
    從 EDGAR 搜尋各 ticker 近期的 SC 13D/13G 申報。
    days_back 建議 90 天，因為 13G 最晚可延遲 45 天申報。
    """
    conn  = get_conn(db_path) if db_path else get_conn()
    since = (date.today() - timedelta(days=days_back)).isoformat()

    for ticker in tickers:
        print(f"[large_holder] 處理 {ticker}...")
        cik = _ticker_to_cik(ticker)
        if not cik:
            print(f"  找不到 CIK，跳過")
            continue

        inserted = _fetch_by_cik(conn, ticker, cik, since)
        print(f"  {ticker}: {inserted} 筆大戶申報 inserted")

    conn.commit()
    conn.close()


def _fetch_by_cik(conn, ticker: str, cik: str, since: str) -> int:
    """
    用 EFTS 搜尋以此 CIK 為主體的 13D/13G 申報。
    EFTS 的 entity_id 欄位對應 subject company CIK。
    """
    forms_param = ",".join(FORM_TYPES)
    url = (
        f"{EFTS_BASE}/LATEST/search-index?"
        f"q=%22{int(cik)}%22"
        f"&forms={forms_param.replace(' ', '+').replace('/', '%2F')}"
        f"&dateRange=custom&startdt={since}&enddt={date.today().isoformat()}"
        f"&_source=file_date,form_type,entity_name,accession_no"
        f"&hits.hits._source=true&hits.hits.total.value=true"
    )

    data = _get_json(url)
    if not data:
        return 0

    hits = data.get("hits", {}).get("hits", [])
    inserted = 0

    for hit in hits:
        src = hit.get("_source", {})
        filed_date   = src.get("file_date", "")
        form_type    = src.get("form_type", "")
        filer_name   = src.get("entity_name", "Unknown")
        accession    = src.get("accession_no", "").replace("-", "")

        if not filed_date or form_type not in FORM_TYPES:
            continue

        try:
            conn.execute("""
                INSERT INTO large_holders
                    (ticker, filed_date, form_type, filer_name, accession_number)
                VALUES (?,?,?,?,?)
                ON CONFLICT(accession_number) DO NOTHING
            """, (ticker, filed_date, form_type, filer_name, accession or None))
            inserted += 1
        except Exception as e:
            print(f"  DB 寫入失敗: {e}")

    return inserted


def get_recent_large_holders(ticker: str, days_back: int = 90,
                              db_path=None) -> list:
    """查詢近期大戶申報紀錄，供 signal 模組使用"""
    since = (date.today() - timedelta(days=days_back)).isoformat()
    conn  = get_conn(db_path) if db_path else get_conn()
    rows  = conn.execute("""
        SELECT filed_date, form_type, filer_name
        FROM large_holders
        WHERE ticker=? AND filed_date >= ?
        ORDER BY filed_date DESC
    """, (ticker, since)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
