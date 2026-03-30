"""
chip_module/fetchers/insider.py
從 SEC EDGAR 抓取 Form 4。
primaryDocument 可能是 .htm 包裝，用 lxml recover 模式解析，
或從同一個 submission 資料夾找獨立的 .xml 檔。
"""

import re
import time
import requests
import sqlite3
from datetime import date, timedelta
from typing import List, Optional

from ..db.schema import get_conn

USER_AGENT = "StockLab research@youremail.com"
EDGAR_BASE = "https://data.sec.gov"
EDGAR_WWW  = "https://www.sec.gov"
HEADERS    = {"User-Agent": USER_AGENT}
SLEEP_SEC  = 0.15


def _get_json(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        time.sleep(SLEEP_SEC)
        return r.json()
    except Exception as e:
        print(f"  [EDGAR] JSON 失敗 {url}: {e}")
        time.sleep(1)
        return None


def _get_raw(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        time.sleep(SLEEP_SEC)
        return r.content
    except requests.HTTPError as e:
        if e.response.status_code != 404:
            print(f"  [EDGAR] HTTP {e.response.status_code}: {url}")
        return None
    except Exception:
        return None


def _ticker_to_cik(ticker: str) -> Optional[str]:
    data = _get_json(f"{EDGAR_WWW}/files/company_tickers.json")
    if not data:
        return None
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    return None


def fetch_insider(tickers: List[str], days_back: int = 30, db_path=None):
    conn  = get_conn(db_path) if db_path else get_conn()
    since = (date.today() - timedelta(days=days_back)).isoformat()

    for ticker in tickers:
        print(f"[insider] 處理 {ticker}...")
        cik = _ticker_to_cik(ticker)
        if not cik:
            print(f"  找不到 CIK，跳過")
            continue

        sub = _get_json(f"{EDGAR_BASE}/submissions/CIK{cik}.json")
        if not sub:
            continue

        filings      = sub.get("filings", {}).get("recent", {})
        forms        = filings.get("form", [])
        dates        = filings.get("filingDate", [])
        accessions   = filings.get("accessionNumber", [])
        primary_docs = filings.get("primaryDocument", [])

        inserted = 0
        for form, filed_date, accession, primary_doc in zip(
            forms, dates, accessions, primary_docs
        ):
            if form != "4":
                continue
            if filed_date < since:
                break

            trades = _parse_form4(cik, accession, primary_doc, ticker, filed_date)
            if not trades:
                continue

            try:
                conn.executemany("""
                    INSERT INTO insider_trades
                        (ticker, report_date, trade_date, insider_name, insider_title,
                         transaction_type, shares, price_per_share, total_value,
                         shares_owned_after, accession_number)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(accession_number) DO NOTHING
                """, trades)
                conn.commit()
                inserted += len(trades)
            except sqlite3.Error as e:
                print(f"  DB 寫入失敗: {e}")

        print(f"  {ticker}: {inserted} 筆交易 inserted")

    conn.close()


def _parse_form4(
    cik: str, accession: str, primary_doc: str, ticker: str, filed_date: str
) -> list:
    acc_clean  = accession.replace("-", "")
    base_url   = f"{EDGAR_WWW}/Archives/edgar/data/{int(cik)}/{acc_clean}"

    # 候選 XML 檔名順序：
    # EDGAR 的 primaryDocument 有時是 "xslF345X06/wk-form4_xxx.xml" —
    # 帶目錄前綴的是 XSLT 渲染後的 HTML，真正的 XML 在 accession 根目錄同名檔。
    # 1. primaryDocument 去掉目錄前綴後的純檔名（最常見的真實 XML 位置）
    # 2. primaryDocument 直接是 .xml（無前綴情況）
    # 3. primaryDocument 換副檔名成 .xml
    # 4. 常見固定名稱
    basename = primary_doc.split("/")[-1] if primary_doc else ""
    stem = basename.rsplit(".", 1)[0] if basename and "." in basename else basename
    candidates = []
    if basename and basename.lower().endswith(".xml"):
        candidates.append(basename)                  # 去掉目錄前綴的純檔名
    if primary_doc and primary_doc.lower().endswith(".xml") and "/" not in primary_doc:
        candidates.append(primary_doc)               # 無前綴時直接用
    if stem:
        candidates.append(f"{stem}.xml")
    candidates += ["form4.xml", "doc4.xml", f"{accession}.xml"]

    raw = None
    for fname in dict.fromkeys(candidates):   # 去重保序
        raw = _get_raw(f"{base_url}/{fname}")
        if raw:
            break

    # 找不到獨立 XML → 嘗試從 primaryDocument (.htm) 裡抽 XML 片段
    if not raw and primary_doc:
        htm = _get_raw(f"{base_url}/{primary_doc}")
        if htm:
            raw = _extract_xml_from_htm(htm)

    if not raw:
        return []

    return _parse_xml(raw, ticker, filed_date, accession)


def _extract_xml_from_htm(htm: bytes) -> Optional[bytes]:
    """Form 4 .htm 有時把 XML 夾在 <XML>...</XML> 標籤裡"""
    text = htm.decode("utf-8", errors="replace")
    m = re.search(r"<XML>(.*?)</XML>", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip().encode("utf-8")
    return None


def _parse_xml(raw: bytes, ticker: str, filed_date: str, accession: str) -> list:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # 標準解析失敗 → 用 lxml recover 模式
        try:
            from lxml import etree
            root = etree.fromstring(raw, parser=etree.XMLParser(recover=True))
            # lxml root → 轉回 ET 相容介面（用 lxml 本身查詢）
            return _extract_trades_lxml(root, ticker, filed_date, accession)
        except ImportError:
            # lxml 沒裝：嘗試正則表達式直接抓欄位
            return _extract_trades_regex(raw.decode("utf-8", errors="replace"),
                                         ticker, filed_date, accession)
        except Exception:
            return []

    return _extract_trades_et(root, ticker, filed_date, accession)


def _extract_trades_et(root, ticker, filed_date, accession):
    import xml.etree.ElementTree as ET
    insider_name  = _xml_text_et(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
    insider_title = _xml_text_et(root, ".//reportingOwner/reportingOwnerRelationship/officerTitle")
    trades = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        txn_type = _xml_text_et(txn, "transactionCoding/transactionCode")
        if txn_type not in ("P", "S"):
            continue
        trade_date  = _xml_text_et(txn, "transactionDate/value")
        shares      = _xml_float(_xml_text_et(txn, "transactionAmounts/transactionShares/value"))
        price       = _xml_float(_xml_text_et(txn, "transactionAmounts/transactionPricePerShare/value"))
        owned_after = _xml_float(_xml_text_et(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
        trades.append((ticker, filed_date, trade_date, insider_name, insider_title,
                       txn_type, shares, price, (shares or 0)*(price or 0) or None,
                       owned_after, accession))
    return trades


def _extract_trades_lxml(root, ticker, filed_date, accession):
    def txt(node, path):
        el = node.find(path)
        return el.text.strip() if el is not None and el.text else None

    insider_name  = txt(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
    insider_title = txt(root, ".//reportingOwner/reportingOwnerRelationship/officerTitle")
    trades = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        txn_type = txt(txn, "transactionCoding/transactionCode")
        if txn_type not in ("P", "S"):
            continue
        trade_date  = txt(txn, "transactionDate/value")
        shares      = _xml_float(txt(txn, "transactionAmounts/transactionShares/value"))
        price       = _xml_float(txt(txn, "transactionAmounts/transactionPricePerShare/value"))
        owned_after = _xml_float(txt(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
        trades.append((ticker, filed_date, trade_date, insider_name, insider_title,
                       txn_type, shares, price, (shares or 0)*(price or 0) or None,
                       owned_after, accession))
    return trades


def _extract_trades_regex(text: str, ticker, filed_date, accession) -> list:
    """lxml 也沒裝時的最後退路，用 regex 直接抽欄位"""
    def tag(t):
        m = re.search(rf"<{t}[^>]*>\s*<value>(.*?)</value>", text, re.DOTALL)
        return m.group(1).strip() if m else None

    txn_type = tag("transactionCode")
    if txn_type not in ("P", "S"):
        return []

    name  = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", text)
    title = re.search(r"<officerTitle>(.*?)</officerTitle>", text)
    trade_date = tag("transactionDate")
    shares     = _xml_float(tag("transactionShares"))
    price      = _xml_float(tag("transactionPricePerShare"))
    owned      = _xml_float(tag("sharesOwnedFollowingTransaction"))

    return [(ticker, filed_date, trade_date,
             name.group(1) if name else None,
             title.group(1) if title else None,
             txn_type, shares, price,
             (shares or 0)*(price or 0) or None,
             owned, accession)]


def _xml_text_et(node, path):
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else None

def _xml_float(v) -> Optional[float]:
    try:
        return float(v) if v else None
    except ValueError:
        return None
