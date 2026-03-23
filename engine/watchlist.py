"""
engine/watchlist.py — 持倉追蹤清單 + 策略卡快取

設計原則：
- 每個 user_id 有獨立的追蹤清單（現在用固定 "default"，之後換 Google email）
- 策略卡存入 SQLite，90 天內不重新呼叫 Gemini
- 現價每次從 fetch_data 取得，不快取（保持即時）
- 加入新標的時才觸發一次 Gemini，之後一季才更新

DB schema：
  watchlist      (user_id, sid, added_date)
  strategy_cache (user_id, sid, generated_date, card_json)
"""

import os
import sqlite3
import datetime
import json
import time
from typing import Optional

_DATA_DIR = "/data" if os.path.isdir("/data") else "."
WATCHLIST_DB = os.path.join(_DATA_DIR, "watchlist.db")

STRATEGY_TTL_DAYS = 90   # 一季更新一次


def _conn():
    return sqlite3.connect(WATCHLIST_DB)


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                user_id     TEXT NOT NULL,
                sid         TEXT NOT NULL,
                added_date  TEXT NOT NULL,
                PRIMARY KEY (user_id, sid)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS strategy_cache (
                user_id        TEXT NOT NULL,
                sid            TEXT NOT NULL,
                generated_date TEXT NOT NULL,
                card_json      TEXT NOT NULL,
                PRIMARY KEY (user_id, sid)
            )
        """)


# ─────────────────────────────────────────────────────────
# Watchlist CRUD
# ─────────────────────────────────────────────────────────

def get_watchlist(user_id: str) -> list[str]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT sid FROM watchlist WHERE user_id=? ORDER BY added_date DESC",
            (user_id,)
        ).fetchall()
    return [r[0] for r in rows]


def add_to_watchlist(user_id: str, sid: str):
    init_db()
    today = datetime.date.today().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?)",
            (user_id, sid, today)
        )


def remove_from_watchlist(user_id: str, sid: str):
    init_db()
    with _conn() as c:
        c.execute(
            "DELETE FROM watchlist WHERE user_id=? AND sid=?",
            (user_id, sid)
        )
    # Also clear cached strategy card
    with _conn() as c:
        c.execute(
            "DELETE FROM strategy_cache WHERE user_id=? AND sid=?",
            (user_id, sid)
        )


# ─────────────────────────────────────────────────────────
# Strategy card cache
# ─────────────────────────────────────────────────────────

def get_cached_card(user_id: str, sid: str) -> Optional[dict]:
    """Return cached strategy card if within TTL, else None."""
    init_db()
    with _conn() as c:
        row = c.execute(
            "SELECT generated_date, card_json FROM strategy_cache WHERE user_id=? AND sid=?",
            (user_id, sid)
        ).fetchone()
    if not row:
        return None
    generated_date = datetime.date.fromisoformat(row[0])
    age_days = (datetime.date.today() - generated_date).days
    if age_days > STRATEGY_TTL_DAYS:
        return None
    try:
        card = json.loads(row[1])
        card["_cache_age_days"] = age_days
        card["_generated_date"] = row[0]
        return card
    except Exception:
        return None


def save_card(user_id: str, sid: str, card: dict):
    """Save strategy card to SQLite cache."""
    init_db()
    today = datetime.date.today().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO strategy_cache VALUES (?, ?, ?, ?)",
            (user_id, sid, today, json.dumps(card))
        )


def force_refresh_card(user_id: str, sid: str):
    """Delete cached card so it gets regenerated on next load."""
    init_db()
    with _conn() as c:
        c.execute(
            "DELETE FROM strategy_cache WHERE user_id=? AND sid=?",
            (user_id, sid)
        )


# ─────────────────────────────────────────────────────────
# Bulk strategy card generation (respects rate limit)
# ─────────────────────────────────────────────────────────

def ensure_cards_for_watchlist(
    engine,
    user_id: str,
    sids: list[str],
    progress_callback=None,
) -> dict[str, dict]:
    """
    For each sid in watchlist:
      - If cached card exists and within TTL → use it
      - If not → call Gemini, save to DB, sleep 2s to avoid rate limit

    progress_callback(i, total, sid) is called for each stock.
    Returns {sid: card_dict}.
    """
    results = {}
    needs_generation = []

    # First pass: load from cache
    for sid in sids:
        card = get_cached_card(user_id, sid)
        if card:
            results[sid] = card
        else:
            needs_generation.append(sid)

    # Second pass: generate missing cards one by one
    for i, sid in enumerate(needs_generation):
        if progress_callback:
            progress_callback(i, len(needs_generation), sid)

        try:
            df, rev = engine.fetch_data(sid)
            df_ml   = engine.fetch_ml_ready_data(sid)
            mops    = engine.fetch_latest_mops_pdf_info(sid)

            if df.empty:
                results[sid] = _empty_card(sid)
                continue

            curr_price = df["close"].iloc[-1]
            low52      = df["close"].tail(252).min()
            high52     = df["close"].tail(252).max()

            f_cost = (
                df_ml["f_cost"].iloc[-1]
                if not df_ml.empty and "f_cost" in df_ml.columns
                else curr_price
            )

            from engine.smart_money import calc_smart_money_score
            sms = calc_smart_money_score(df) if "f_net" in df.columns else {"score": 0}

            # Simple hit_rate from ML if available
            hit_rate = 0.5
            if not df_ml.empty:
                import xgboost as xgb, numpy as np
                features = ["ma5", "ma20", "bias_f_cost", "conc", "f_streak", "volatility"]
                clean = df_ml.replace([np.inf, -np.inf], np.nan).dropna(
                    subset=features + ["target_max_ret"])
                if len(clean) >= 100:
                    model = xgb.XGBRegressor(n_estimators=100, max_depth=5,
                                              learning_rate=0.05, random_state=42)
                    model.fit(clean[features], clean["target_max_ret"])
                    hc = clean[model.predict(clean[features]) > 0.10]
                    hit_rate = float((hc["target_max_ret"] > 0.10).mean()) if not hc.empty else 0.5

            name = engine.cache.__class__.__name__  # fallback
            from sector_data import STOCK_POOL
            name = STOCK_POOL.get(sid, sid)

            card = engine.get_strategy_card_ai(
                sid, name,
                curr_price, low52, high52,
                float(f_cost), float(sms["score"]),
                mops.get("event", ""), hit_rate,
            )

            if card:
                save_card(user_id, sid, card)
                results[sid] = card
            else:
                results[sid] = _empty_card(sid)

        except Exception as e:
            print(f"[watchlist] card gen error {sid}: {e}")
            results[sid] = _empty_card(sid)

        # Rate limit protection: 2s between Gemini calls
        if i < len(needs_generation) - 1:
            time.sleep(2)

    return results


def _empty_card(sid: str) -> dict:
    return {
        "strategy_type": "—",
        "entry": {"ideal_zone": "—", "trigger": "資料不足"},
        "risk":  {"stop_loss": "—", "stop_reason": "—", "max_loss_pct": "—"},
        "targets": [{"price": "—", "reason": "—", "action": "—"}],
        "position_size": {"kelly_fraction": 0, "suggested_pct": "—", "rationale": "—"},
        "validity": "—",
        "confidence": 0,
        "_empty": True,
    }
