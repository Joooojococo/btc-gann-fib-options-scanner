#!/usr/bin/env python3
"""
Derive.xyz Options Straddle Scanner

Scan 所有 BTC options expiry + strike 嘅 straddle（call + put）成本，
搵 IV 最低 / 最平嘅 straddle，Discord 通知。

用法：python3 derive_straddle_scanner.py
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("straddle")

# ═══════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════

DERIVE_API = "https://api.lyra.finance"
CURRENCY = os.getenv("STRADDLE_CURRENCY", "BTC,ETH,HYPE")
POLL_INTERVAL = int(os.getenv("STRADDLE_POLL_INTERVAL", "300"))  # 5 min
IV_THRESHOLD = float(os.getenv("STRADDLE_IV_THRESHOLD", "0.28"))  # alert when avg IV < 28%
# Per-currency IV threshold override (e.g. STRADDLE_IV_THRESHOLD_HYPE=1.20)
def get_iv_threshold(currency: str) -> float:
    val = os.getenv(f"STRADDLE_IV_THRESHOLD_{currency}", "")
    return float(val) if val else IV_THRESHOLD
MAX_STRIKE_DISTANCE_PCT = float(os.getenv("STRADDLE_MAX_STRIKE_DIST", "0.10"))  # within 10% of spot
DISCORD_WEBHOOK = os.getenv("ITA_DISCORD_WEBHOOK_URL", "")
HKT = timezone(timedelta(hours=8))

# Cost % 門檻：短期（≤14日）同長期（>14日）分開
COST_PCT_SHORT = float(os.getenv("STRADDLE_COST_PCT_SHORT", "4.0"))   # ≤14日：cost < 4% of spot
COST_PCT_LONG = float(os.getenv("STRADDLE_COST_PCT_LONG", "8.0"))    # >14日：cost < 8% of spot

# 定期 summary：每 N 次 scan 發一次 Discord summary（即使 0 pass）
SUMMARY_EVERY = int(os.getenv("STRADDLE_SUMMARY_EVERY", "12"))  # 12 × 5min = 1hr

# HV 計算
HV_DAYS = int(os.getenv("STRADDLE_HV_DAYS", "30"))  # 30日歷史波動率
HV_BUFFER = float(os.getenv("STRADDLE_HV_BUFFER", "1.1"))  # IV < HV × buffer (容許 10% buffer)
_HV_CACHE: Dict[str, Tuple[float, datetime]] = {}  # currency -> (hv_value, fetch_time)

# Dedup：每個 expiry 每日最多 alert 1 次（持久化到 file，restart 唔重複）
_last_alerted: Dict[str, str] = {}  # key = "YYYYMMDD-currency-expiry" -> alert timestamp
DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".straddle_dedup.json")

def _load_dedup():
    global _last_alerted
    try:
        with open(DEDUP_FILE) as f:
            _last_alerted = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _last_alerted = {}

def _save_dedup():
    try:
        with open(DEDUP_FILE, "w") as f:
            json.dump(_last_alerted, f)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
#  Historical Volatility (from Binance klines)
# ═══════════════════════════════════════════════════════

async def fetch_hv(session: httpx.AsyncClient, currency: str) -> Optional[float]:
    """從 Derive.xyz 攞日線，計算 N 日歷史波動率（年化）。Cache 1 小時 per currency。"""
    global _HV_CACHE
    cache_key = currency
    if cache_key in _HV_CACHE and (datetime.now(timezone.utc) - _HV_CACHE[cache_key][1]).total_seconds() < 3600:
        return _HV_CACHE[cache_key][0]

    try:
        end_ts = int(datetime.now(timezone.utc).timestamp())
        start_ts = end_ts - (HV_DAYS + 1) * 86400
        resp = await session.post(
            f"{DERIVE_API}/public/get_index_chart_data",
            json={"currency": currency, "start_timestamp": start_ts, "end_timestamp": end_ts, "period": 86400},
            timeout=15,
        )
        data = resp.json()
        result = data.get("result", [])
        candles = result.get("candles", result) if isinstance(result, dict) else result
        closes = [float(c["close_price"]) for c in candles]
        if len(closes) < 2:
            return None

        import math
        returns = []
        for i in range(1, len(closes)):
            returns.append(math.log(closes[i] / closes[i - 1]))

        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        daily_vol = math.sqrt(variance)
        annualized_hv = daily_vol * math.sqrt(365)

        _HV_CACHE[cache_key] = (annualized_hv, datetime.now(timezone.utc))
        log.info(f"HV({currency},{HV_DAYS}d): {annualized_hv:.1%} (from {len(closes)-1} daily returns)")
        return annualized_hv
    except Exception as e:
        log.error(f"fetch_hv: {e}")
        return None


# ═══════════════════════════════════════════════════════
#  Derive API
# ═══════════════════════════════════════════════════════

async def fetch_instruments(session: httpx.AsyncClient, currency: str) -> List[dict]:
    """攞所有 active options for given currency"""
    try:
        resp = await session.post(
            f"{DERIVE_API}/public/get_instruments",
            json={"currency": currency, "expired": False, "instrument_type": "option"},
            timeout=15,
        )
        data = resp.json()
        return data.get("result", [])
    except Exception as e:
        log.error(f"fetch_instruments: {e}")
        return []


async def fetch_tickers(session: httpx.AsyncClient, currency: str, expiry_date: str) -> dict:
    """攞某個 expiry 嘅所有 ticker"""
    try:
        resp = await session.post(
            f"{DERIVE_API}/public/get_tickers",
            json={
                "currency": currency,
                "instrument_type": "option",
                "expiry_date": expiry_date,
            },
            timeout=15,
        )
        data = resp.json()
        return data.get("result", {}).get("tickers", {})
    except Exception as e:
        log.error(f"fetch_tickers: {e}")
        return {}


# ═══════════════════════════════════════════════════════
#  Straddle Analysis
# ═══════════════════════════════════════════════════════

def parse_instruments(instruments: List[dict]) -> Dict[str, List[dict]]:
    """按 expiry 分組，返回 {expiry_date: [instruments]}"""
    by_expiry: Dict[str, List[dict]] = {}
    for inst in instruments:
        od = inst.get("option_details") or {}
        expiry_ts = od.get("expiry")
        if not expiry_ts:
            continue
        expiry_dt = datetime.fromtimestamp(int(expiry_ts), tz=timezone.utc)
        expiry_key = expiry_dt.strftime("%Y%m%d")
        by_expiry.setdefault(expiry_key, []).append(inst)
    return by_expiry


def build_straddles(tickers: dict, spot: float) -> List[dict]:
    """從 tickers 構建 straddle 數據"""
    calls: Dict[int, dict] = {}
    puts: Dict[int, dict] = {}
    names: Dict[int, str] = {}

    for name, t in tickers.items():
        parts = name.split("-")
        if len(parts) < 4:
            continue
        strike = int(parts[2])
        opt_type = parts[3]

        ask = float(t.get("a", 0))
        bid = float(t.get("b", 0))
        if ask <= 0:
            continue

        iv = t.get("option_pricing", {})
        ask_iv = float(iv.get("ai", 0))

        if opt_type == "C":
            calls[strike] = {"ask": ask, "bid": bid, "iv": ask_iv, "name": name, "pricing": iv}
        else:
            puts[strike] = {"ask": ask, "bid": bid, "iv": ask_iv, "name": name, "pricing": iv}

    straddles = []
    for strike in sorted(set(calls.keys()) & set(puts.keys())):
        c = calls[strike]
        p = puts[strike]
        cost = c["ask"] + p["ask"]
        avg_iv = (c["iv"] + p["iv"]) / 2 if c["iv"] > 0 and p["iv"] > 0 else max(c["iv"], p["iv"])
        distance_pct = abs(strike - spot) / spot

        straddles.append({
            "strike": strike,
            "call_ask": c["ask"],
            "put_ask": p["ask"],
            "straddle_cost": cost,
            "straddle_pct": cost / spot * 100,
            "avg_iv": avg_iv,
            "call_iv": c["iv"],
            "put_iv": p["iv"],
            "distance_pct": distance_pct * 100,
            "call_name": c["name"],
            "put_name": p["name"],
        })

    return straddles


# ═══════════════════════════════════════════════════════
#  Discord
# ═══════════════════════════════════════════════════════

async def discord_notify(session: httpx.AsyncClient, content: str):
    if not DISCORD_WEBHOOK:
        return
    try:
        await session.post(f"{DISCORD_WEBHOOK}?wait=true", json={"content": content}, timeout=10)
    except Exception as e:
        log.warning(f"Discord: {e}")


# ═══════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════

def fmt_expiry(expiry_key: str) -> str:
    """20260717 -> Jul 17"""
    dt = datetime.strptime(expiry_key, "%Y%m%d")
    return dt.strftime("%b %d")


def days_to_expiry(expiry_key: str) -> int:
    dt = datetime.strptime(expiry_key, "%Y%m%d").replace(tzinfo=timezone.utc)
    return (dt - datetime.now(timezone.utc)).days


# ═══════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════

async def scan_once(session: httpx.AsyncClient, currency: str) -> Optional[dict]:
    """Scan 一次，返回最平嘅 straddle info"""
    instruments = await fetch_instruments(session, currency)
    if not instruments:
        return None

    by_expiry = parse_instruments(instruments)
    log.info(f"Found {len(instruments)} instruments across {len(by_expiry)} expiries")

    all_straddles = []
    spot = None

    for expiry_key in sorted(by_expiry.keys()):
        days = days_to_expiry(expiry_key)
        if days < 0:
            continue

        tickers = await fetch_tickers(session, currency, expiry_key)
        if not tickers:
            continue

        # Get spot from first ticker
        if spot is None:
            for t in tickers.values():
                idx = float(t.get("I", 0))
                if idx > 0:
                    spot = idx
                    break

        if spot is None:
            continue

        straddles = build_straddles(tickers, spot)
        for s in straddles:
            s["expiry"] = expiry_key
            s["days_to_expiry"] = days
            # Filter: within strike distance
            if s["distance_pct"] > MAX_STRIKE_DISTANCE_PCT * 100:
                continue
            all_straddles.append(s)

    if not all_straddles or spot is None:
        log.warning("No straddles found")
        return None

    # Sort by IV (lowest first)
    all_straddles.sort(key=lambda x: x["avg_iv"])

    return {"spot": spot, "straddles": all_straddles}


CURRENCIES = [c.strip() for c in CURRENCY.split(",") if c.strip()]


async def scan_currency(session: httpx.AsyncClient, currency: str, scan_count: int) -> bool:
    """Scan one currency. Returns True if alerted."""
    result = await scan_once(session, currency)
    if not result:
        return False

    spot = result["spot"]
    straddles = result["straddles"]
    hv = await fetch_hv(session, currency)

    today = datetime.now(HKT).strftime("%Y%m%d")
    iv_thresh = get_iv_threshold(currency)
    filtered = []
    for s in straddles:
        if s["avg_iv"] <= 0 or s["avg_iv"] > iv_thresh:
            continue
        if hv and s["avg_iv"] >= hv * HV_BUFFER:
            continue
        cost_limit = COST_PCT_SHORT if s["days_to_expiry"] <= 14 else COST_PCT_LONG
        if s["straddle_pct"] > cost_limit:
            continue
        filtered.append(s)

    log.info(f"{currency} spot: ${spot:,.0f} | {len(straddles)} straddles, {len(filtered)} pass filter"
             f" (IV<{iv_thresh:.0%}" + (f", IV<HV×{HV_BUFFER} {hv*HV_BUFFER:.1%}" if hv else "") + f", cost<{COST_PCT_SHORT}%/{COST_PCT_LONG}%)")
    for s in straddles[:5]:
        cost_limit = COST_PCT_SHORT if s["days_to_expiry"] <= 14 else COST_PCT_LONG
        pass_icon = "✅" if s in filtered else "❌"
        log.info(
            f"  {pass_icon} {fmt_expiry(s['expiry'])} ({s['days_to_expiry']}d) "
            f"Strike {s['strike']} | Straddle ${s['straddle_cost']:,.0f} "
            f"({s['straddle_pct']:.1f}%, limit {cost_limit:.0f}%) | IV {s['avg_iv']:.1%}"
        )

    if not filtered:
        if scan_count % SUMMARY_EVERY == 0:
            now_hkt = datetime.now(HKT).strftime("%H:%M")
            top3 = straddles[:3]
            lines = [f"📊 {currency} Straddle Summary — {now_hkt} HKT"]
            lines.append(f"```\n  {currency} spot: ${spot:,.0f} | {len(straddles)} straddles, 0 pass filter")
            if hv:
                lines.append(f"  HV({HV_DAYS}d): {hv:.1%} | IV threshold: {iv_thresh:.0%}")
            lines.append(f"  Cost limit: <{COST_PCT_SHORT}%/{COST_PCT_LONG}% (≤14d/>14d)\n")
            for s in top3:
                cost_limit = COST_PCT_SHORT if s["days_to_expiry"] <= 14 else COST_PCT_LONG
                lines.append(
                    f"  {fmt_expiry(s['expiry'])} ({s['days_to_expiry']}d) K{s['strike']:,.0f} "
                    f"| ${s['straddle_cost']:,.0f} ({s['straddle_pct']:.1f}%, lim {cost_limit:.0f}%) "
                    f"| IV {s['avg_iv']:.1%}"
                )
            lines.append("```")
            await discord_notify(session, "\n".join(lines))
            log.info(f"📋 {currency} summary sent (scan #{scan_count})")
        return False

    best = filtered[0]
    alert_key = f"{today}-{currency}-{best['expiry']}"
    if alert_key in _last_alerted:
        log.info(f"Already alerted today for {currency}-{best['expiry']}, skipping")
        return False

    _last_alerted[alert_key] = datetime.now(timezone.utc).isoformat()
    keys_to_remove = [k for k in _last_alerted if not k.startswith(today)]
    for k in keys_to_remove:
        del _last_alerted[k]
    _save_dedup()

    hv_str = f"\n  HV({HV_DAYS}d): {hv:.1%} → IV 折價 {(hv - best['avg_iv'])/hv*100:.0f}%" if hv else ""
    msg = (
        f"📊 便宜 {currency} Straddle 發現\n"
        f"```\n"
        f"  Expiry: {fmt_expiry(best['expiry'])} ({best['days_to_expiry']}d)\n"
        f"  Strike: ${best['strike']:,.0f} (距 spot {best['distance_pct']:.1f}%)\n"
        f"  Straddle: ${best['straddle_cost']:,.0f} ({best['straddle_pct']:.1f}% of spot)\n"
        f"  IV: {best['avg_iv']:.1%}{hv_str}\n"
        f"    Call: ${best['call_ask']:,.0f} (IV {best['call_iv']:.1%})\n"
        f"    Put:  ${best['put_ask']:,.0f} (IV {best['put_iv']:.1%})\n"
        f"  {currency} spot: ${spot:,.0f}\n"
        f"```"
    )
    await discord_notify(session, msg)
    log.info(f"🔔 Alert: {alert_key} IV {best['avg_iv']:.1%}" +
             (f" < HV {hv:.1%}" if hv else ""))
    return True


async def main():
    log.info("=" * 55)
    log.info(f"📊 Derive.xyz Straddle Scanner — {', '.join(CURRENCIES)}")
    log.info(f"   IV threshold: {IV_THRESHOLD:.0%} | Poll: {POLL_INTERVAL}s")
    log.info(f"   Max strike distance: {MAX_STRIKE_DISTANCE_PCT:.0%}")
    log.info("=" * 55)

    session = httpx.AsyncClient(timeout=15.0)
    _scan_count = 0
    _load_dedup()

    while True:
        try:
            _scan_count += 1
            for currency in CURRENCIES:
                await scan_currency(session, currency, _scan_count)
            await asyncio.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Main loop: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    await session.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n終止")
