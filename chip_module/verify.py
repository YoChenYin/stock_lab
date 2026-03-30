"""
chip_module/verify.py
驗證所有資料來源是否正常抓取，可在每次 fetch 後執行確認。

用法：
    python -m chip_module.verify                        # 驗證全部
    python -m chip_module.verify --source insider       # 只驗證特定來源
    python -m chip_module.verify --tickers NVDA TSLA    # 指定股票
"""

import argparse
import sqlite3
import requests
from datetime import date, timedelta
from .db.schema import get_conn

PASS  = "✓"
WARN  = "!"
FAIL  = "✗"

# 預設驗證用的少量股票（跑快一點）
SAMPLE_TICKERS = ["NVDA", "AAPL", "TSLA"]


def header(title: str):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")


def ok(msg):   print(f"  {PASS}  {msg}")
def warn(msg): print(f"  {WARN}  {msg}")
def fail(msg): print(f"  {FAIL}  {msg}")


# ── 1. 網路連線 ───────────────────────────────────────────────────

def check_network():
    header("網路連線")
    endpoints = {
        "SEC EDGAR":  "https://data.sec.gov/files/company_tickers.json",
        "FINRA":      "https://cdn.finra.org",
        "CBOE":       "https://cdn.cboe.com/api/global/us_indices/daily_prices/PCR-EOD_EQUITY.csv",
        "Yahoo Finance": "https://finance.yahoo.com",
    }
    all_ok = True
    for name, url in endpoints.items():
        try:
            r = requests.get(url, timeout=8)
            if r.status_code < 400:
                ok(f"{name} ({r.status_code})")
            else:
                warn(f"{name} 回應 {r.status_code}")
                all_ok = False
        except Exception as e:
            fail(f"{name}: {e}")
            all_ok = False
    return all_ok


# ── 2. DB schema ──────────────────────────────────────────────────

def check_schema():
    header("DB Schema")
    expected_tables = [
        "daily_prices", "insider_trades", "short_interest",
        "options_sentiment", "institutional_holders", "chip_scores",
    ]
    try:
        conn = get_conn()
        existing = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()

        all_ok = True
        for t in expected_tables:
            if t in existing:
                ok(f"Table `{t}` 存在")
            else:
                fail(f"Table `{t}` 不存在 → 請先執行 init_db()")
                all_ok = False
        return all_ok
    except Exception as e:
        fail(f"DB 連線失敗: {e}")
        return False


# ── 3. 股價資料 ───────────────────────────────────────────────────

def check_prices(tickers):
    header("股價 & 量能指標 (daily_prices)")
    conn = get_conn()
    all_ok = True
    cutoff = (date.today() - timedelta(days=3)).isoformat()  # 允許週末落差

    for ticker in tickers:
        rows = conn.execute(
            "SELECT date, close, volume, obv, cmf_20, mfi_14 FROM daily_prices "
            "WHERE ticker=? ORDER BY date DESC LIMIT 5",
            (ticker,)
        ).fetchall()

        if not rows:
            fail(f"{ticker}: 完全沒有資料")
            all_ok = False
            continue

        latest = rows[0]
        latest_date = latest["date"]
        missing_cols = [c for c in ["obv", "cmf_20", "mfi_14"] if latest[c] is None]

        if latest_date < cutoff:
            warn(f"{ticker}: 最新資料 {latest_date}（可能未更新）")
            all_ok = False
        else:
            ok(f"{ticker}: 最新 {latest_date}，close={latest['close']:.2f}，共 {len(rows)} 筆")

        if missing_cols:
            warn(f"  → 缺少技術指標欄位: {', '.join(missing_cols)}")

    conn.close()
    return all_ok


# ── 4. 內部人交易 ─────────────────────────────────────────────────

def check_insider(tickers):
    header("內部人交易 Form 4 (insider_trades)")
    conn = get_conn()
    all_ok = True
    cutoff_30d = (date.today() - timedelta(days=30)).isoformat()

    for ticker in tickers:
        rows = conn.execute(
            "SELECT report_date, insider_name, transaction_type, shares, total_value "
            "FROM insider_trades WHERE ticker=? ORDER BY report_date DESC LIMIT 5",
            (ticker,)
        ).fetchall()

        count_30d = conn.execute(
            "SELECT COUNT(*) FROM insider_trades WHERE ticker=? AND report_date>=?",
            (ticker, cutoff_30d)
        ).fetchone()[0]

        if not rows:
            warn(f"{ticker}: 近期無申報（可能真的沒有，非錯誤）")
        else:
            latest = rows[0]
            ok(f"{ticker}: 最新 {latest['report_date']}，"
               f"{latest['transaction_type']} {int(latest['shares'] or 0):,} 股，"
               f"近30天共 {count_30d} 筆")

            # 顯示最近幾筆摘要
            for r in rows[:3]:
                direction = "買入" if r["transaction_type"] == "P" else "賣出"
                val = f"${r['total_value']:,.0f}" if r["total_value"] else "N/A"
                print(f"       {r['report_date']} | {direction} | {int(r['shares'] or 0):,} 股 | {val}")

    conn.close()
    return all_ok


# ── 5. 空頭興趣 ───────────────────────────────────────────────────

def check_short(tickers):
    header("空頭興趣 (short_interest)")
    conn = get_conn()
    all_ok = True

    for ticker in tickers:
        rows = conn.execute(
            "SELECT settlement_date, short_volume, short_float_pct, days_to_cover, chg_pct "
            "FROM short_interest WHERE ticker=? ORDER BY settlement_date DESC LIMIT 3",
            (ticker,)
        ).fetchall()

        if not rows:
            warn(f"{ticker}: 無資料（FINRA 可能尚未發布本期）")
            all_ok = False
            continue

        latest = rows[0]
        chg = f"{latest['chg_pct']:+.1f}%" if latest["chg_pct"] is not None else "N/A"
        dtc  = f"{latest['days_to_cover']:.1f}天" if latest["days_to_cover"] else "N/A"
        sf   = f"{latest['short_float_pct']:.2f}%" if latest["short_float_pct"] else "N/A"

        ok(f"{ticker}: {latest['settlement_date']} | Short Float {sf} | DTC {dtc} | 變化 {chg}")

    conn.close()
    return all_ok


# ── 6. 選擇權情緒 ─────────────────────────────────────────────────

def check_options():
    header("選擇權情緒 P/C Ratio (options_sentiment)")
    conn = get_conn()
    all_ok = True
    cutoff = (date.today() - timedelta(days=5)).isoformat()

    for scope in ["equity", "index", "total"]:
        row = conn.execute(
            "SELECT date, pc_ratio, pc_ma20, pc_zscore_20 "
            "FROM options_sentiment WHERE scope=? ORDER BY date DESC LIMIT 1",
            (scope,)
        ).fetchone()

        if not row:
            fail(f"scope={scope}: 無資料")
            all_ok = False
            continue

        sentiment = "偏多" if (row["pc_ratio"] or 1) < 0.7 else \
                    "偏空" if (row["pc_ratio"] or 1) > 1.0 else "中性"
        zscore = f"{row['pc_zscore_20']:+.2f}" if row["pc_zscore_20"] else "N/A"

        if row["date"] < cutoff:
            warn(f"scope={scope}: 最新 {row['date']}（資料可能過舊）")
            all_ok = False
        else:
            ok(f"scope={scope}: {row['date']} | P/C={row['pc_ratio']:.3f} "
               f"| Z={zscore} | {sentiment}")

    conn.close()
    return all_ok


# ── 7. 機構持倉 ───────────────────────────────────────────────────

def check_institutional(tickers):
    header("機構持倉 (institutional_holders)")
    conn = get_conn()

    for ticker in tickers:
        rows = conn.execute(
            "SELECT report_date, institution, pct_out "
            "FROM institutional_holders WHERE ticker=? ORDER BY report_date DESC LIMIT 5",
            (ticker,)
        ).fetchall()

        if not rows:
            warn(f"{ticker}: 無資料（可能尚未執行 fetch_institutional）")
        else:
            ok(f"{ticker}: {rows[0]['report_date']}，前幾大機構：")
            for r in rows[:3]:
                pct = f"{r['pct_out']*100:.2f}%" if r["pct_out"] else "N/A"
                print(f"       {r['institution'][:35]:<35} {pct}")

    conn.close()


# ── 主流程 ────────────────────────────────────────────────────────

def run_all(tickers, source=None):
    print("\n========================================")
    print("  chip_module 資料驗證工具")
    print(f"  執行日期：{date.today().isoformat()}")
    print(f"  驗證股票：{', '.join(tickers)}")
    print("========================================")

    results = {}

    if source in (None, "network"):
        results["network"] = check_network()

    if source in (None, "schema"):
        results["schema"] = check_schema()

    if source in (None, "prices"):
        results["prices"] = check_prices(tickers)

    if source in (None, "insider"):
        results["insider"] = check_insider(tickers)

    if source in (None, "short"):
        results["short"] = check_short(tickers)

    if source in (None, "options"):
        results["options"] = check_options()

    if source in (None, "institutional"):
        check_institutional(tickers)

    # 總結
    print(f"\n{'═'*50}")
    print("  驗證結果總覽")
    print(f"{'═'*50}")
    all_passed = True
    for name, passed in results.items():
        status = f"{PASS} PASS" if passed else f"{FAIL} FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n  所有資料來源正常，可以進入 signals 計算階段。")
    else:
        print("\n  部分資料有問題，請依上方提示排查後重新執行 fetch_daily。")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=SAMPLE_TICKERS)
    parser.add_argument(
        "--source",
        choices=["network", "schema", "prices", "insider", "short", "options", "institutional"],
        default=None,
        help="只驗證特定來源"
    )
    args = parser.parse_args()
    run_all(tickers=args.tickers, source=args.source)
