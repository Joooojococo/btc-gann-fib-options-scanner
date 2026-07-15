#!/usr/bin/env python3
"""
============================================================
  江恩正方 + FIB通道 + FIB時間擴展 × Derive.xyz 期權掃描機器人
  Version 3.0 — CALL + PUT 雙向掃描 + 雙開策略建議
============================================================
用法: python3 option_scanner_v3.py

需要 OpenAI API Key（可在 platform.openai.com 獲取）
如果沒有 API Key，直接按 Enter 跳過 LLM 分析
============================================================
"""

import urllib.request
import urllib.error
import json
import math
import sys
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
#  顏色輸出
# ─────────────────────────────────────────────
class C:
    RED    = '\033[91m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    CYAN   = '\033[96m'
    WHITE  = '\033[97m'
    BOLD   = '\033[1m'
    DIM    = '\033[2m'
    RESET  = '\033[0m'

# ─────────────────────────────────────────────
#  模組 0：已驗證江恩正方 + 撈底計劃（鎖定數字，唔再重新計）
#  來源：Agent 1-5 終極整合報告 @ 2026-06-26
# ─────────────────────────────────────────────
VERIFIED_GANN = {
    'pivot_date': '2022-11-21',
    'pivot_price': 15360.0,
    'grid_per_day': 113.36,
    'fine_grid': 500,
    'lines': [
        # (price, name, strength, role)
        (60866, 'Gann 1×8 DOWN', 3, '多空分界'),
        (59489, 'Gann 1×6 DOWN', 2, '中期阻力'),
        (59075, '三重共振支撐', 3, '最強支撐共振'),
        (56733, 'Gann 1×4 DOWN', 2, '🟢 第一站撈底'),
        (54261, 'Gann 1×2 主升浪線', 3, '🟢 長線最強支撐'),
        (48466, 'Gann 1×2 DOWN', 1, '🔴 終極防線'),
        (44856, 'Fib 0.238 終極支撐', 2, '🔴 終極支撐'),
    ],
    'vacuum_warning': '$48,466 → $44,856 之間係無底真空區，一旦穿終極防線會加速下跌',
}

# 三個撈底位 × 最優CALL Strike × 掛單價（Agent 5 鎖定）
DIP_BUY_PLAN = {
    'target_rebound': 60000,   # 目標反彈價
    'rebound_name': '$60,000',
    'entries': [
        {
            'dip_price': 56733,
            'label': '第一站 Gann 1×4 DOWN',
            'best_strike': 62000,
            'theo_price_per_contract': 2328,
            'order_price_per_contract': 2095,  # theo × 0.9
            'cost_001': 20.95,
            'rebound_value': 3347,
            'rebound_profit': 12.53,
            'roi_pct': 59.8,
        },
        {
            'dip_price': 54261,
            'label': '最強支撐 Gann 1×2 主升浪線',
            'best_strike': 57000,
            'theo_price_per_contract': 2507,
            'order_price_per_contract': 2256,  # theo × 0.9
            'cost_001': 22.56,
            'rebound_value': 5164,
            'rebound_profit': 29.08,
            'roi_pct': 128.9,
        },
        {
            'dip_price': 48466,
            'label': '終極防線 Gann 1×2 DOWN',
            'best_strike': 55000,
            'theo_price_per_contract': 612,
            'order_price_per_contract': 551,   # theo × 0.9
            'cost_001': 5.51,
            'rebound_value': 2712,
            'rebound_profit': 21.61,
            'roi_pct': 392.2,
        },
    ],
    'total_cost': 49.02,
    'take_profit_1': (59075, '三重共振支撐', 50),
    'take_profit_2': (60866, '多空分界 Gann 1×8 DOWN', 50),
    'stop_loss_pct': 2.0,  # 撈底位下方2%
}

def hdr(text):
    print(f"\n{C.BOLD}{C.CYAN}{'═'*62}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {text}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'═'*62}{C.RESET}\n")

def sec(text):
    print(f"\n{C.BOLD}{C.YELLOW}▶ {text}{C.RESET}")
    print(f"{C.DIM}{'─'*55}{C.RESET}")

def ok(t):   print(f"  {C.GREEN}✅ {t}{C.RESET}")
def bad(t):  print(f"  {C.RED}❌ {t}{C.RESET}")
def tip(t):  print(f"  {C.CYAN}💡 {t}{C.RESET}")

# ─────────────────────────────────────────────
#  Derive.xyz API
# ─────────────────────────────────────────────
BASE    = 'https://api.lyra.finance'
HEADERS = {'Accept': 'application/json', 'User-Agent': 'OptionScannerV3/1.0'}

def api_get(url: str, timeout: int = 20):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except Exception:
        return None

def get_spot(currency: str) -> float:
    d = api_get(f'{BASE}/public/get_ticker?instrument_name={currency}-PERP')
    return float(d['result']['mark_price']) if d and 'result' in d else 0.0

def get_instruments(currency: str) -> list:
    d = api_get(f'{BASE}/public/get_instruments?currency={currency}&instrument_type=option&expired=false')
    return d['result'] if d and 'result' in d else []

def get_ticker(name: str) -> dict:
    d = api_get(f'{BASE}/public/get_ticker?instrument_name={name}')
    return d['result'] if d and 'result' in d else {}

# ─────────────────────────────────────────────
#  模組 1：江恩正方（自動計算每格大小）
# ─────────────────────────────────────────────
def auto_grid_size(spot_price: float) -> float:
    """根據現價自動決定合理的江恩格子大小"""
    if spot_price > 50000:   return 1000.0   # BTC
    if spot_price > 1000:    return 100.0    # 高價幣
    if spot_price > 100:     return 10.0
    if spot_price > 10:      return 1.0
    return 0.1

def gann_targets_for_range(pivot_date: datetime, pivot_price: float,
                            grid_size: float, today: datetime,
                            horizon_days: int = 30) -> dict:
    """計算未來 horizon_days 天內的江恩角度線目標"""
    angles = {'4×1': 4.0, '3×1': 3.0, '2×1': 2.0,
              '1×1': 1.0, '1×2': 0.5, '1×3': 1/3, '1×4': 0.25}
    targets = {}
    for days_ahead in range(0, horizon_days + 1, 1):
        dt = today + timedelta(days=days_ahead)
        total_days = (dt - pivot_date).total_seconds() / 86400
        for name, ratio in angles.items():
            price = round(pivot_price + total_days * ratio * grid_size, 0)
            key = f'{name}'
            if key not in targets and price > pivot_price:
                targets[key] = price
    return targets


# ─────────────────────────────────────────────
#  模組 2：FIB 通道
# ─────────────────────────────────────────────
FIB_EXT = [0.618, 0.786, 1.0, 1.272, 1.414, 1.618, 2.0, 2.618]
FIB_LBL = {
    0.618: '0.618 黃金支撐',
    0.786: '0.786',
    1.0:   '1.000 波段等長',
    1.272: '1.272 延伸⭐',
    1.414: '1.414',
    1.618: '1.618 黃金延伸🔥',
    2.0:   '2.000',
    2.618: '2.618 超強',
}

def fib_levels(start: float, end: float) -> dict:
    wave = end - start
    return {f: round(start + wave * f, 2) for f in FIB_EXT}

# ─────────────────────────────────────────────
#  模組 3：FIB 時間
# ─────────────────────────────────────────────
FIB_TIME = [0.382, 0.5, 0.618, 1.0, 1.272, 1.618, 2.0, 2.618, 3.0, 4.236]

def fib_time_nodes(start: datetime, end: datetime) -> dict:
    wave_days = (end - start).total_seconds() / 86400
    return {r: start + timedelta(days=wave_days * r) for r in FIB_TIME}

def upcoming_nodes(nodes: dict, today: datetime, max_days: int = 60) -> list:
    result = []
    for r, dt in nodes.items():
        diff = (dt - today).total_seconds() / 86400
        if 0 <= diff <= max_days:
            result.append((r, dt, diff))
    return sorted(result, key=lambda x: x[2])

# ─────────────────────────────────────────────
#  模組 4：共振評分（雙向：向上 + 向下）
# ─────────────────────────────────────────────
def convergence(spot: float, gann: dict, fibs: dict,
                time_nodes: list, max_pain: float) -> dict:
    signals = []
    price_map = {}   # price_bucket → [sources]

    def add(bucket, label):
        if bucket not in price_map:
            price_map[bucket] = []
        price_map[bucket].append(label)

    # 江恩目標（只看 2×1 和 3×1，最常用）
    for name in ['2×1', '3×1', '1×1']:
        if name in gann and gann[name] > spot:
            b = round(gann[name] / 1000) * 1000
            add(b, f'江恩{name}(${gann[name]:,.0f})')

    # FIB 水平（向上目標，高於現價）
    for f, price in sorted(fibs.items(), key=lambda x: x[1]):
        if price > spot * 1.02:
            b = round(price / 500) * 500
            label = FIB_LBL.get(f, f'FIB{f}')
            add(b, f'{label}(${price:,.0f})')

    # FIB 水平（向下目標，低於現價 — 淡倉專用）
    for f, price in sorted(fibs.items(), key=lambda x: x[1], reverse=True):
        if price < spot * 0.98:
            b = round(price / 500) * 500
            label = FIB_LBL.get(f, f'FIB{f}')
            add(b, f'{label}↓淡倉(${price:,.0f})')

    # Max Pain
    if max_pain > 0:
        mp_b = int(max_pain)
        add(mp_b, f'Max Pain(${max_pain:,.0f})')
        signals.append(f"🎯 Max Pain = ${max_pain:,.0f}")

    # 共振識別
    resonant = []
    single = []
    for bucket, srcs in sorted(price_map.items()):
        if len(srcs) >= 2:
            resonant.append((bucket, srcs))
            signals.append(f"🔥 共振 ${bucket:,}: {' + '.join(srcs)}")
        else:
            single.append((bucket, srcs))
            signals.append(f"📍 目標 ${bucket:,}: {srcs[0]}")

    # 時間節點
    for r, dt, diff in time_nodes:
        signals.append(f"⏰ FIB時間 {r} = {dt.strftime('%Y-%m-%d')} (還有{diff:.1f}天)")

    # 評分
    score = min(len(resonant) * 30 + len(time_nodes) * 15 + (25 if max_pain > 0 else 0), 100)

    # 分離向上/向下目標
    all_strikes_up   = [(b, len(s), s) for b, s in resonant if b > spot] + [(b, 1, s) for b, s in single if b > spot]
    all_strikes_down = [(b, len(s), s) for b, s in resonant if b < spot] + [(b, 1, s) for b, s in single if b < spot]
    all_strikes_up.sort(key=lambda x: (-x[1], x[0]))
    all_strikes_down.sort(key=lambda x: (-x[1], -x[0]))  # 低價排前（淡倉最接近目標）

    return {
        'score': score,
        'strikes_up': all_strikes_up[:6],
        'strikes_down': all_strikes_down[:6],
        'time_nodes': time_nodes,
        'signals': signals,
    }

# ─────────────────────────────────────────────
#  模組 5：掃描期權（支援 CALL + PUT）
# ─────────────────────────────────────────────
def scan(currency: str, spot: float, target_strikes: list,
         target_expiries: list, max_pain: float,
         direction: str = 'CALL',
         otm_max: float = 35, dte_max: float = 60) -> list:

    is_call = direction.upper() in ('C', 'CALL')
    opt_type = 'C' if is_call else 'P'
    label    = 'CALL' if is_call else 'PUT'

    print(f"  拉取 {currency} 期權清單...", end='', flush=True)
    insts = get_instruments(currency)
    options = [i for i in insts if i['option_details']['option_type'] == opt_type]
    print(f" {len(options)} 個{label}")

    today = datetime.now(tz=timezone.utc)
    relevant = []
    for c in options:
        opts = c['option_details']
        strike = int(opts['strike'])
        exp_ts = opts['expiry']
        dte = (datetime.fromtimestamp(exp_ts, tz=timezone.utc) - today).total_seconds() / 86400

        # CALL: OTM = strike > spot; PUT: OTM = strike < spot
        if is_call:
            otm = (strike - spot) / spot * 100
        else:
            otm = (spot - strike) / spot * 100

        if 0 <= otm <= otm_max and 2 <= dte <= dte_max:
            relevant.append(c)

    print(f"  過濾後：{len(relevant)} 個 (OTM 0-{otm_max:.0f}%, DTE 2-{dte_max:.0f}天)")
    print(f"  拉取報價...", end='', flush=True)

    results = []
    for i, c in enumerate(relevant):
        name = c['instrument_name']
        t = get_ticker(name)
        if not t:
            continue

        opts = t['option_details']
        strike = int(opts['strike'])
        exp_ts = opts['expiry']
        dte = (datetime.fromtimestamp(exp_ts, tz=timezone.utc) - today).total_seconds() / 86400

        if is_call:
            otm = (strike - spot) / spot * 100
        else:
            otm = (spot - strike) / spot * 100

        exp_date = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

        mark  = float(t.get('mark_price', 0))
        bid   = float(t.get('best_bid_price', 0) or 0)
        ask   = float(t.get('best_ask_price', 0) or 0)
        min_a = float(t.get('minimum_amount', '0.01'))

        p = t.get('option_pricing', {})
        delta = float(p.get('delta', 0) or 0)
        gamma = float(p.get('gamma', 0) or 0)
        theta = float(p.get('theta', 0) or 0)
        iv    = float(p.get('iv', 0) or 0)

        if bid <= 0 and ask <= 0:
            continue

        mid    = (bid + ask) / 2 if (bid + ask) > 0 else ask
        spr    = (ask - bid) / mid * 100 if mid > 0 else 200

        # 🚫 流動性過濾：spread > 50% 直接踢走，唔trade得
        if spr > 50:
            continue

        prem   = mark * min_a
        g_eff  = gamma / (mark + 0.0001) if mark > 0 else 0

        # 共振加分
        res_score = 0
        res_tags  = []

        for tp, strength, _ in target_strikes:
            diff_pct = abs(strike - tp) / tp * 100 if tp > 0 else 100
            if diff_pct < 2:
                res_score += 30 * strength
                res_tags.append(f'Strike共振${tp:,}')
            elif diff_pct < 5:
                res_score += 15 * strength
                res_tags.append(f'Strike近${tp:,}')

        if max_pain > 0:
            mp_diff = abs(strike - max_pain) / max_pain * 100
            if mp_diff < 3:
                res_score += 25; res_tags.append('Max Pain')
            elif mp_diff < 8:
                res_score += 10; res_tags.append('近Max Pain')

        for _, node_dt, _ in target_expiries:
            exp_diff = abs((exp_date - node_dt).total_seconds() / 86400)
            if exp_diff <= 3:
                res_score += 20; res_tags.append('FIB時間精確')
            elif exp_diff <= 7:
                res_score += 10; res_tags.append('FIB時間近')

        # 基礎評分
        base = 0
        if prem < 3:    base += 30
        elif prem < 8:  base += 25
        elif prem < 20: base += 18
        elif prem < 60: base += 10

        if 8 <= otm <= 20:  base += 22
        elif 4 <= otm < 8:  base += 16
        elif 20 < otm <= 30:base += 12
        elif 0 <= otm < 4:  base += 8

        if spr < 5:   base += 22
        elif spr < 10: base += 17
        elif spr < 20: base += 10
        elif spr < 35: base += 5

        if 7 <= dte <= 18:  base += 16
        elif 18 < dte <= 35:base += 12
        elif 3 <= dte < 7:  base += 10

        if g_eff > 0.001:  base += 10
        elif g_eff > 0.0005:base += 7
        elif g_eff > 0.0002:base += 4

        # ROI 計算
        def roi(tp):
            if is_call:
                pay = max(0, (tp - strike) * min_a)
            else:
                pay = max(0, (strike - tp) * min_a)
            return (pay - prem) / prem * 100 if prem > 0 else -100

        roi_map = {tp: round(roi(tp), 0) for tp, _, _ in target_strikes[:4]}
        if max_pain > 0:
            roi_map[int(max_pain)] = round(roi(max_pain), 0)

        results.append({
            'name': name, 'strike': strike, 'direction': label,
            'expiry': exp_date.strftime('%Y-%m-%d'), 'dte': round(dte, 1),
            'otm': round(otm, 1), 'bid': bid, 'ask': ask,
            'spread': round(spr, 1), 'delta': round(delta, 4),
            'gamma': round(gamma, 6), 'theta': round(theta, 2),
            'iv': round(iv * 100, 1), 'prem': round(prem, 2),
            'g_eff': round(g_eff, 7),
            'base': base, 'res': res_score,
            'total': base + res_score,
            'tags': res_tags, 'roi': roi_map,
        })

        if (i + 1) % 20 == 0:
            print('.', end='', flush=True)

    print(f" 完成({len(results)}個)")
    results.sort(key=lambda x: -x['total'])
    return results

# ─────────────────────────────────────────────
#  LLM 分析（OpenAI / DeepSeek）
# ─────────────────────────────────────────────
def llm_analyze(api_key: str, provider: str, currency: str, spot: float,
                conv_result: dict, top_call: list, top_put: list,
                max_pain: float) -> str:
    """呼叫 LLM（DeepSeek / OpenAI）生成中文分析報告"""

    def fmt_options(opts, label):
        text = f"=== {label} ===\n"
        for i, r in enumerate(opts[:2]):
            text += (f"  {i+1}. {r['name']} | 入場費${r['prem']:.2f} | "
                     f"OTM{r['otm']:.1f}% | Spread{r['spread']:.1f}% | IV{r['iv']}% | "
                     f"Delta{r['delta']:.4f} | 共振標籤: {', '.join(r['tags'][:2]) or '無'}\n")
        return text

    signals_text = '\n'.join(conv_result['signals'][:10])
    options_text = fmt_options(top_call, "CALL好倉") + fmt_options(top_put, "PUT淡倉")

    roi_text = ''
    all_top = top_call[:1] + top_put[:1]
    for r in all_top:
        if r.get('roi'):
            roi_text += f"  {r['direction']} {r['name']}: "
            for tp, roi_val in list(r['roi'].items())[:2]:
                sign = '+' if roi_val > 0 else ''
                roi_text += f"到${tp:,}: {sign}{roi_val:.0f}% | "
            roi_text += '\n'

    prompt = f"""你係有30年經驗的頂級期權交易員。請用廣東話（粵語）分析以下期權掃描報告，給出專業簡潔的分析。

=== 市場數據 ===
幣種: {currency}
現價: ${spot:,.2f}
Max Pain: ${max_pain:,.0f} ({'+' if max_pain > spot else ''}{(max_pain-spot)/spot*100:.1f}%)
三工具共振評分: {conv_result['score']}/100

=== 技術分析信號 ===
{signals_text}

=== 推薦期權（好淡雙向）===
{options_text}

=== 首選合約ROI情景 ===
{roi_text}

請提供：
1. **市場判斷**（2-3句）：依家方向偏好多定淡？雙開有冇著數？
2. **好倉分析**（2句）：首選CALL勝在哪？
3. **淡倉分析**（2句）：首選PUT勝在哪？  
4. **雙向策略建議**（2-3句）：Long Strangle定係單邊？點揀？
5. **風險提示**（1句）：最大風險係咩？

用簡潔直接語氣。"""

    # 根據 provider 選擇 API 端點和模型
    if provider == 'deepseek':
        api_url = 'https://api.deepseek.com/v1/chat/completions'
        model   = 'deepseek-chat'
    else:
        api_url = 'https://api.openai.com/v1/chat/completions'
        model   = 'gpt-4o-mini'

    payload = json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 900,
        'temperature': 0.7
    }).encode('utf-8')

    try:
        req = urllib.request.Request(
            api_url,
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}'
            }
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=45).read().decode())
        return resp['choices'][0]['message']['content']
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        return f"LLM 呼叫失敗: HTTP {e.code} — {err_body[:300]}"
    except Exception as e:
        return f"LLM 呼叫失敗: {e}"

# ─────────────────────────────────────────────
#  輸出報告（雙向：CALL + PUT）
# ─────────────────────────────────────────────
def print_report(currency, spot, conv, options_call, options_put, max_pain):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    hdr(f"🔍 {currency} 雙向期權掃描報告 — {now}")

    sec("市場現況")
    print(f"  {C.BOLD}{currency} 現價:{C.RESET}  {C.GREEN}${spot:,.2f}{C.RESET}")
    if max_pain > 0:
        diff = max_pain - spot
        pct  = diff / spot * 100
        col  = C.GREEN if diff > 0 else C.RED
        print(f"  {C.BOLD}Max Pain:{C.RESET}   {col}${max_pain:,.0f} ({'↑' if diff>0 else '↓'}{abs(pct):.1f}%){C.RESET}")

    sec("三工具共振分析")
    score = conv['score']
    slbl  = ('🔥 高確信度！' if score >= 70 else '📊 中等確信度' if score >= 40 else '⚠️  低確信度，謹慎')
    scol  = C.GREEN if score >= 70 else (C.YELLOW if score >= 40 else C.RED)
    print(f"  共振評分: {scol}{C.BOLD}{score}/100 {slbl}{C.RESET}\n")
    for s in conv['signals']:
        print(f"  {s}")

    sec("建議好倉目標（CALL）")
    if conv.get('strikes_up'):
        print(f"  {C.BOLD}建議 Strike Price (好):{C.RESET}")
        for i, (price, strength, srcs) in enumerate(conv['strikes_up'][:4]):
            stars = '⭐' * min(strength, 3)
            print(f"    {i+1}. ${price:,} {stars}  ({', '.join(srcs[:2])})")

    sec("建議淡倉目標（PUT）")
    if conv.get('strikes_down'):
        print(f"  {C.BOLD}建議 Strike Price (淡):{C.RESET}")
        for i, (price, strength, srcs) in enumerate(conv['strikes_down'][:4]):
            stars = '⭐' * min(strength, 3)
            print(f"    {i+1}. ${price:,} {stars}  ({', '.join(srcs[:2])})")
    else:
        print(f"  {C.DIM}暫無明顯向下目標（現價已近通道底部）{C.RESET}")

    if conv['time_nodes']:
        print(f"\n  {C.BOLD}建議到期日:{C.RESET}")
        for r, dt, diff in conv['time_nodes'][:3]:
            print(f"    FIB {r}: {dt.strftime('%Y-%m-%d')} (還有 {diff:.1f} 天)")

    # ─── CALL 推薦 ───
    if options_call:
        sec("📈 CALL 好倉推薦 (Top 6)")
        print(f"  {'#':<3} {'合約':<28} {'到期':<12} {'DTE':<5} {'OTM%':<7} "
              f"{'Bid':<8} {'Ask':<8} {'Spr%':<6} {'IV%':<6} {'Δ':<7} {'入場費':<8} {'共振':<5} {'總分'}")
        print(f"  {'─'*3} {'─'*28} {'─'*12} {'─'*5} {'─'*7} "
              f"{'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*7} {'─'*8} {'─'*5} {'─'*5}")
        for i, r in enumerate(options_call[:6]):
            col = C.GREEN if i < 2 else (C.YELLOW if i < 4 else C.RESET)
            rtag = ','.join(r['tags'][:2])
            rstr = f"+{r['res']}" if r['res'] > 0 else ''
            print(f"  {col}{C.BOLD}{i+1:<3}{C.RESET} "
                  f"{r['name']:<28} {r['expiry']:<12} {r['dte']:<5.1f} "
                  f"{r['otm']:<7.1f} {r['bid']:<8.1f} {r['ask']:<8.1f} "
                  f"{r['spread']:<6.1f} {r['iv']:<6.1f} {r['delta']:<7.4f} "
                  f"${r['prem']:<7.2f} {col}{rstr:<5}{C.RESET} {col}{r['total']}{C.RESET}")
            if rtag:
                print(f"       {C.CYAN}↳ {rtag}{C.RESET}")

    # ─── PUT 推薦 ───
    if options_put:
        sec("📉 PUT 淡倉推薦 (Top 6)")
        print(f"  {'#':<3} {'合約':<28} {'到期':<12} {'DTE':<5} {'OTM%':<7} "
              f"{'Bid':<8} {'Ask':<8} {'Spr%':<6} {'IV%':<6} {'Δ':<7} {'入場費':<8} {'共振':<5} {'總分'}")
        print(f"  {'─'*3} {'─'*28} {'─'*12} {'─'*5} {'─'*7} "
              f"{'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*7} {'─'*8} {'─'*5} {'─'*5}")
        for i, r in enumerate(options_put[:6]):
            col = C.RED if i < 2 else (C.YELLOW if i < 4 else C.RESET)
            rtag = ','.join(r['tags'][:2])
            rstr = f"+{r['res']}" if r['res'] > 0 else ''
            # PUT delta 係負數，顯示絕對值
            d_abs = abs(r['delta'])
            print(f"  {col}{C.BOLD}{i+1:<3}{C.RESET} "
                  f"{r['name']:<28} {r['expiry']:<12} {r['dte']:<5.1f} "
                  f"{r['otm']:<7.1f} {r['bid']:<8.1f} {r['ask']:<8.1f} "
                  f"{r['spread']:<6.1f} {r['iv']:<6.1f} {d_abs:<7.4f} "
                  f"${r['prem']:<7.2f} {col}{rstr:<5}{C.RESET} {col}{r['total']}{C.RESET}")
            if rtag:
                print(f"       {C.CYAN}↳ {rtag}{C.RESET}")

    # ─── CALL 情景回報 ───
    if options_call:
        sec("📈 CALL 情景回報 (Top 4)")
        top4c = options_call[:4]
        all_tp = sorted(set(tp for r in top4c for tp in r['roi'].keys()))[:4]
        if all_tp:
            hdr_r = f"  {'合約':<28} {'入場費':<9}"
            for tp in all_tp:
                hdr_r += f" {'@'+str(tp//1000)+'K':>10}"
            print(hdr_r)
            print(f"  {'─'*28} {'─'*9}" + " " + "─" * (11 * len(all_tp)))
            for r in top4c:
                row = f"  {r['name']:<28} ${r['prem']:<8.2f}"
                for tp in all_tp:
                    roi_v = r['roi'].get(tp)
                    if roi_v is None:
                        row += f" {'N/A':>10}"
                    elif roi_v > 0:
                        row += f" {C.GREEN}{'+'+str(int(roi_v))+'%':>10}{C.RESET}"
                    else:
                        row += f" {C.RED}{str(int(roi_v))+'%':>10}{C.RESET}"
                print(row)

    # ─── PUT 情景回報 ───
    if options_put:
        sec("📉 PUT 情景回報 (Top 4)")
        top4p = options_put[:4]
        all_tp = sorted(set(tp for r in top4p for tp in r['roi'].keys()), reverse=True)[:4]
        if all_tp:
            hdr_r = f"  {'合約':<28} {'入場費':<9}"
            for tp in all_tp:
                hdr_r += f" {'@'+str(tp//1000)+'K':>10}"
            print(hdr_r)
            print(f"  {'─'*28} {'─'*9}" + " " + "─" * (11 * len(all_tp)))
            for r in top4p:
                row = f"  {r['name']:<28} ${r['prem']:<8.2f}"
                for tp in all_tp:
                    roi_v = r['roi'].get(tp)
                    if roi_v is None:
                        row += f" {'N/A':>10}"
                    elif roi_v > 0:
                        row += f" {C.GREEN}{'+'+str(int(roi_v))+'%':>10}{C.RESET}"
                    else:
                        row += f" {C.RED}{str(int(roi_v))+'%':>10}{C.RESET}"
                print(row)

    # ─── 雙向策略建議 ───
    sec("🎯 雙向策略建議")
    # 計算好淡力量對比
    call_best = options_call[0] if options_call else None
    put_best  = options_put[0] if options_put else None

    # 🔀 Long Strangle 配對：揀 OTM% 最對稱嘅 CALL+PUT 組合
    strangle_pair = None
    strangle_cost = 0
    best_symmetry = 999
    if options_call and options_put:
        for c in options_call[:8]:    # 睇前8張CALL
            for p in options_put[:8]:  # 睇前8張PUT
                if c['strike'] <= spot or p['strike'] >= spot:
                    continue
                c_otm = c['otm']
                p_otm = p['otm']
                # 要求兩邊OTM差距唔超過5%，且OTM在4-20%範圍（太近冇肉食，太遠到唔到）
                if 4 <= c_otm <= 20 and 4 <= p_otm <= 20:
                    diff = abs(c_otm - p_otm)
                    if diff < best_symmetry and diff < 5:
                        best_symmetry = diff
                        strangle_pair = (c, p)
                        strangle_cost = c['prem'] + p['prem']

    if call_best and put_best:
        call_score = call_best['total']
        put_score  = put_best['total']
        call_prem  = call_best['prem']
        put_prem   = put_best['prem']
        combo_cost = call_prem + put_prem

        print(f"\n  {C.BOLD}好倉首選:{C.RESET} {call_best['name']} (${call_prem:.2f}) — 評分 {call_score}")
        print(f"  {C.BOLD}淡倉首選:{C.RESET} {put_best['name']} (${put_prem:.2f}) — 評分 {put_score}")

        print(f"\n  {C.BOLD}{C.CYAN}策略選項:{C.RESET}")
        print(f"  {C.GREEN}🟢 看好: {C.RESET}買 {call_best['name']} (${call_prem:.2f})")
        print(f"  {C.RED}🔴 看淡: {C.RESET}買 {put_best['name']} (${put_prem:.2f})")

        if strangle_pair:
            c_str, p_str = strangle_pair
            print(f"  {C.YELLOW}🔀 雙開(對稱): {C.RESET}{c_str['name']} + {p_str['name']}")
            print(f"      成本 ${strangle_cost:.2f} | CALL OTM {c_str['otm']:.1f}% ↔ PUT OTM {p_str['otm']:.1f}% | OTM差距 {best_symmetry:.1f}%")
            up_brk   = round((c_str['strike'] + strangle_cost - spot) / spot * 100, 1)
            down_brk = round((spot - (p_str['strike'] - strangle_cost)) / spot * 100, 1)
            print(f"      打和點: 升{up_brk}% / 跌{down_brk}%")
        else:
            print(f"  {C.DIM}🔀 雙開: 暫無OTM對稱嘅CALL+PUT組合（兩邊OTM差>5%）{C.RESET}")
            print(f"  {C.DIM}   建議用單邊策略或等IV變化後再睇{C.RESET}")

    elif call_best:
        print(f"\n  {C.BOLD}只有好倉可選:{C.RESET} {call_best['name']} (${call_best['prem']:.2f})")
        print(f"  {C.DIM}PUT淡倉暫無符合條件的合約{C.RESET}")
    elif put_best:
        print(f"\n  {C.BOLD}只有淡倉可選:{C.RESET} {put_best['name']} (${put_best['prem']:.2f})")
        print(f"  {C.DIM}CALL好倉暫無符合條件的合約{C.RESET}")

    print(f"\n  {C.DIM}⚠️  技術分析輔助工具，不構成投資建議。期權可能全損。{C.DIM}方向判斷請結合市場環境。{C.RESET}\n")


# ─────────────────────────────────────────────
#  主程式
# ─────────────────────────────────────────────
def ask(prompt, default=None):
    d = f" [{default}]" if default is not None else ""
    while True:
        try:
            val = input(f"  {prompt}{d}: ").strip()
            if val == '' and default is not None:
                return str(default)
            if val:
                return val
        except (EOFError, KeyboardInterrupt):
            print("\n\n退出"); sys.exit(0)

def parse_date(s: str) -> datetime:
    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y']:
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except:
            pass
    raise ValueError(f"日期格式錯誤: {s}")

def main():
    hdr("江恩+FIB+Derive.xyz 雙向期權掃描機器人 v3.0 🤖")

    # ── 第一步：幣種 ──
    sec("Step 1：選擇幣種")
    currency = ask("幣種 (BTC/ETH)", "BTC").upper()
    if currency not in ('BTC', 'ETH'):
        currency = 'BTC'

    # ── 第二步：波段資料 ──
    sec("Step 2：輸入波段資料（從 TradingView 看）")
    tip("在 TradingView 找最近的重要低點（起點）和高點（終點）")
    tip("例：從5月低點$56,500 到 5月高點$71,000")
    print()

    while True:
        try:
            ws_date = parse_date(ask("波段起點日期 (YYYY-MM-DD)，例：2026-05-01"))
            break
        except ValueError as e:
            print(f"  {C.RED}錯誤: {e}{C.RESET}")

    while True:
        try:
            ws_price = float(ask("波段起點價格，例：56500"))
            break
        except ValueError:
            print(f"  {C.RED}請輸入數字{C.RESET}")

    while True:
        try:
            we_date = parse_date(ask("波段高點日期 (YYYY-MM-DD)，例：2026-05-20"))
            break
        except ValueError as e:
            print(f"  {C.RED}錯誤: {e}{C.RESET}")

    while True:
        try:
            we_price = float(ask("波段高點價格，例：71000"))
            break
        except ValueError:
            print(f"  {C.RED}請輸入數字{C.RESET}")

    # ── 第三步：Max Pain ──
    sec("Step 3：Max Pain（選填）")
    tip("去 https://www.coinglass.com/zh/option 查看")
    tip("選幣種 → 選最近到期日 → 找 'Max Pain' 數字")
    mp_str = ask("Max Pain 價格（不知道輸入 0）", "0")
    try:
        max_pain = float(mp_str)
    except ValueError:
        max_pain = 0.0

    # ── 第四步：LLM API Key ──
    sec("Step 4：LLM AI 分析（選填）")
    tip("支援 DeepSeek（平，推薦）或 OpenAI")
    tip("DeepSeek API Key: 去 platform.deepseek.com 獲取（極平，約 $0.001/次）")
    tip("OpenAI API Key:   去 platform.openai.com 獲取（約 $0.01/次）")
    tip("沒有的話直接按 Enter 跳過")
    api_key = ask("API Key（sk-...），沒有請按 Enter 跳過", "跳過")
    use_llm = api_key.startswith('sk-')
    provider = 'deepseek' if (use_llm and len(api_key) < 50) else 'openai'
    if use_llm:
        print(f"  {C.CYAN}使用: {'DeepSeek' if provider=='deepseek' else 'OpenAI GPT-4o-mini'}{C.RESET}")

    # ── 開始計算 ──
    print(f"\n{C.BOLD}{C.CYAN}🔄 開始計算...{C.RESET}")
    today = datetime.now(tz=timezone.utc)

    # 1. 現價
    print(f"\n  [1/6] 拉取 {currency} 現價...", end='', flush=True)
    spot = get_spot(currency)
    if spot <= 0:
        bad("無法獲取現價，請檢查網路連線"); return
    print(f" {C.GREEN}${spot:,.2f}{C.RESET}")

    # 2. 江恩正方
    print(f"  [2/6] 計算江恩正方...")
    grid = auto_grid_size(spot)
    gann = gann_targets_for_range(ws_date, ws_price, grid, today, horizon_days=60)

    # 3. FIB 通道（雙向：向上回調 + 向下延伸）
    print(f"  [3/6] 計算 FIB 通道（含向下延伸）...")
    fibs = fib_levels(ws_price, we_price)

    # 4. FIB 時間
    print(f"  [4/6] 計算 FIB 時間節點...")
    time_nd = fib_time_nodes(ws_date, we_date)
    up_nodes = upcoming_nodes(time_nd, today, max_days=60)

    # 5. 共振評分（雙向）
    print(f"  [5/6] 三工具共振評分（好淡雙向）...")
    conv = convergence(spot, gann, fibs, up_nodes, max_pain)

    # 6. 期權掃描 — CALL + PUT
    expiry_for_scan = up_nodes
    strike_for_up   = conv['strikes_up']
    strike_for_down = conv['strikes_down']

    sec(f"掃描 {currency} CALL 期權（好倉）...")
    options_call = scan(
        currency=currency, spot=spot,
        target_strikes=strike_for_up,
        target_expiries=expiry_for_scan,
        max_pain=max_pain, direction='CALL',
    )

    sec(f"掃描 {currency} PUT 期權（淡倉）...")
    options_put = scan(
        currency=currency, spot=spot,
        target_strikes=strike_for_down,
        target_expiries=expiry_for_scan,
        max_pain=max_pain, direction='PUT',
    )

    # 輸出報告
    print_report(currency, spot, conv, options_call, options_put, max_pain)

    # LLM 分析
    analysis = ''
    if use_llm and (options_call or options_put):
        sec("🤖 LLM AI 分析")
        print(f"  {C.DIM}分析中，請稍候...{C.RESET}")
        analysis = llm_analyze(api_key, provider, currency, spot, conv,
                               options_call, options_put, max_pain)
        print(f"\n{C.BOLD}{C.GREEN}{'─'*62}{C.RESET}")
        print(f"{C.BOLD}AI 分析報告：{C.RESET}\n")
        print(analysis)
        print(f"{C.BOLD}{C.GREEN}{'─'*62}{C.RESET}\n")

    # 儲存報告
    save = ask("儲存報告到文字檔？(y/N)", "N").lower()
    if save == 'y':
        fname = f"scan_{currency}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(f"{currency} 雙向掃描報告 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"現價: ${spot:,.2f} | Max Pain: ${max_pain:,.0f}\n")
            f.write(f"共振評分: {conv['score']}/100\n\n")
            f.write("信號:\n" + '\n'.join(conv['signals']) + "\n\n")

            f.write("=== CALL 好倉 ===\n")
            for i, r in enumerate(options_call[:6]):
                f.write(f"{i+1}. {r['name']} | ${r['prem']:.2f} | Spr{r['spread']:.0f}% | IV{r['iv']}% | 分:{r['total']}\n")

            f.write("\n=== PUT 淡倉 ===\n")
            for i, r in enumerate(options_put[:6]):
                f.write(f"{i+1}. {r['name']} | ${r['prem']:.2f} | Spr{r['spread']:.0f}% | IV{r['iv']}% | 分:{r['total']}\n")

            if use_llm and analysis:
                f.write(f"\nAI 分析:\n{analysis}\n")
        ok(f"報告已儲存: {fname}")


# ─────────────────────────────────────────────
#  --dip 模式：三層撈底終極掃描（已驗證數據）
#  用法：python3 option_scanner_v3.py --dip
# ─────────────────────────────────────────────
def run_dip_scan():
    hdr("🔮 江恩三層撈底 × Jul31 CALL 掃描 🤖")
    print(f"  基於：5大Agent終極整合 @ 2026-06-26")
    print(f"  已驗證江恩正方起點：{VERIFIED_GANN['pivot_date']} @ ${VERIFIED_GANN['pivot_price']:,}")
    print()

    currency = 'BTC'
    today = datetime.now(tz=timezone.utc)

    # 1. 現價
    print(f"  [1/4] 拉取 {currency} 現價...", end='', flush=True)
    spot = get_spot(currency)
    if spot <= 0:
        bad("無法獲取現價"); return
    print(f" {C.GREEN}${spot:,.2f}{C.RESET}")

    # 2. 打印已驗證江恩線
    sec("📐 已驗證江恩線（$60K→$44K）")
    print(f"  {'價格':<10} {'名稱':<28} {'強度':<6} {'角色'}")
    print(f"  {'─'*10} {'─'*28} {'─'*6} {'─'*20}")
    for price, name, strength, role in VERIFIED_GANN['lines']:
        stars = '🔥' * strength
        dist = spot - price
        dist_pct = dist / spot * 100
        arrow = '⬇' if dist > 0 else '⬆'
        marker = f"{arrow} {abs(dist_pct):.1f}%"
        col = C.RED if dist > 0 else C.GREEN
        print(f"  ${price:<9,} {name:<28} {stars:<6} {col}{role} ({marker}){C.RESET}")
    print(f"\n  {C.YELLOW}⚠️  {VERIFIED_GANN['vacuum_warning']}{C.RESET}")

    # 3. 打印撈底計劃
    plan = DIP_BUY_PLAN
    sec(f"🎯 三層撈底計劃 — 目標反彈價：{plan['rebound_name']}")
    print(f"  {'撈底位':<10} {'最優CALL':<10} {'理論價/張':<10} {'掛單價/張':<10} {'0.01張成本':<11} {'彈$60K賺':<11} {'ROI%'}")
    print(f"  {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*11} {'─'*11} {'─'*6}")
    for e in plan['entries']:
        print(f"  ${e['dip_price']:<9,} ${e['best_strike']:<9,} ${e['theo_price_per_contract']:<9,} "
              f"${e['order_price_per_contract']:<9,} ${e['cost_001']:<10.2f} ${e['rebound_profit']:<10.2f} {e['roi_pct']}%")
    print(f"\n  {C.BOLD}三張總成本: ${plan['total_cost']:.2f}{C.RESET}")
    print(f"  {C.BOLD}食糊1: ${plan['take_profit_1'][0]:,} ({plan['take_profit_1'][1]}) → 平{plan['take_profit_1'][2]}%{C.RESET}")
    print(f"  {C.BOLD}食糊2: ${plan['take_profit_2'][0]:,} ({plan['take_profit_2'][1]}) → 平{plan['take_profit_2'][2]}%{C.RESET}")
    print(f"  {C.RED}止蝕: 撈底位下方 {plan['stop_loss_pct']}%{C.RESET}")

    # 4. 掃描 Jul31 CALL（每個撈底位嘅最優Strike）
    all_strikes = [e['best_strike'] for e in plan['entries']]
    strike_labels = {e['best_strike']: f"${e['dip_price']:,}撈底" for e in plan['entries']}

    sec(f"📈 掃描 Jul31 CALL 真實報價（目標Strike: {', '.join(f'${s:,}' for s in all_strikes)}）")
    print(f"  拉取 {currency} 期權清單...", end='', flush=True)
    insts = get_instruments(currency)
    options = [i for i in insts if i['option_details']['option_type'] == 'C']
    print(f" {len(options)} 個CALL")

    # 過濾：Jul31到期 + 目標Strike + 附近Strike
    target_strikes_set = set(all_strikes)
    nearby_strikes = set()
    for ts in all_strikes:
        for s in [ts - 1000, ts + 1000, ts + 2000, ts + 3000, ts + 5000]:
            nearby_strikes.add(s)
    all_target = target_strikes_set | nearby_strikes

    relevant = []
    for c in options:
        opts = c['option_details']
        strike = int(opts['strike'])
        exp_ts = opts['expiry']
        exp_date = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        dte = (exp_date - today).total_seconds() / 86400

        # Jul31 = 35 DTE, 範圍 30-38 日
        if 28 <= dte <= 40 and strike in all_target:
            relevant.append(c)

    if not relevant:
        print(f"\n  {C.RED}❌ 無符合條件的 Jul31 CALL 期權{C.RESET}")
        return

    print(f"  拉取 {len(relevant)} 個 Jul31 CALL 報價...")
    results = []
    for c in relevant:
        name = c['instrument_name']
        t = get_ticker(name)
        if not t:
            continue

        opts = t['option_details']
        strike = int(opts['strike'])
        exp_ts = opts['expiry']
        exp_date = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        dte = (exp_date - today).total_seconds() / 86400
        otm = (strike - spot) / spot * 100

        mark = float(t.get('mark_price', 0))
        bid = float(t.get('best_bid_price', 0) or 0)
        ask = float(t.get('best_ask_price', 0) or 0)
        min_a = float(t.get('minimum_amount', '0.01'))

        p = t.get('option_pricing', {})
        delta = float(p.get('delta', 0) or 0)
        gamma = float(p.get('gamma', 0) or 0)
        theta = float(p.get('theta', 0) or 0)
        iv = float(p.get('iv', 0) or 0)

        if bid <= 0 and ask <= 0:
            continue

        mid = (bid + ask) / 2 if (bid + ask) > 0 else ask
        spr = (ask - bid) / mid * 100 if mid > 0 else 200

        prem_001 = mark * 0.01  # 0.01張成本
        prem_1 = mark * 1.0    # 1張成本

        # Mark這個Strike是否是撈底計劃的目標
        is_target = strike in target_strikes_set
        match_label = strike_labels.get(strike, '')

        results.append({
            'name': name, 'strike': strike, 'expiry': exp_date.strftime('%Y-%m-%d'),
            'dte': round(dte, 1), 'otm': round(otm, 1),
            'bid': bid, 'ask': ask, 'mark': mark,
            'spread': round(spr, 1), 'delta': round(delta, 4),
            'gamma': round(gamma, 6), 'theta': round(theta, 2),
            'iv': round(iv * 100, 1),
            'prem_001': round(prem_001, 2), 'prem_1': round(prem_1, 2),
            'is_target': is_target, 'match_label': match_label,
        })

    results.sort(key=lambda x: (not x['is_target'], x['strike']))

    # 輸出表格
    print(f"\n  {'合約':<30} {'到期':<12} {'DTE':<5} {'OTM%':<7} "
          f"{'Bid':<8} {'Ask':<8} {'Spr%':<6} {'IV%':<6} {'Δ':<7} {'0.01張':<9} {'1張':<9} {'標記'}")
    print(f"  {'─'*30} {'─'*12} {'─'*5} {'─'*7} "
          f"{'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*7} {'─'*9} {'─'*9} {'─'*12}")

    for r in results:
        col = C.GREEN if r['is_target'] else C.DIM
        tag = f"🎯 {r['match_label']}" if r['is_target'] else ''
        print(f"  {col}{r['name']:<30}{C.RESET} {r['expiry']:<12} {r['dte']:<5.1f} "
              f"{r['otm']:<7.1f} {r['bid']:<8.1f} {r['ask']:<8.1f} "
              f"{r['spread']:<6.1f} {r['iv']:<6.1f} {r['delta']:<7.4f} "
              f"${r['prem_001']:<8.2f} ${r['prem_1']:<8.2f} {col}{tag}{C.RESET}")

    # 對比：理論價 vs 真實價
    sec("📊 理論價 vs 真實市場價 對比")
    print(f"  {'撈底位':<10} {'Strike':<8} {'理論價/張(B-S)':<17} {'真實Mark/張':<14} {'真實0.01張':<12} {'差距'}")
    print(f"  {'─'*10} {'─'*8} {'─'*17} {'─'*14} {'─'*12} {'─'*15}")
    for entry in plan['entries']:
        strike = entry['best_strike']
        theo = entry['theo_price_per_contract']
        real_data = [r for r in results if r['strike'] == strike]
        if real_data:
            rd = real_data[0]
            real_mark = rd['mark']
            real_001 = rd['prem_001']
            diff = real_mark - theo
            diff_pct = diff / theo * 100 if theo > 0 else 0
            diff_col = C.RED if diff > 0 else C.GREEN
            print(f"  ${entry['dip_price']:<9,} ${strike:<7,} ${theo:<16,} "
                  f"${real_mark:<13,.0f} ${real_001:<11.2f} "
                  f"{diff_col}{diff:+.0f} ({diff_pct:+.0f}%){C.RESET}")
        else:
            print(f"  ${entry['dip_price']:<9,} ${strike:<7,} ${theo:<16,} "
                  f"{C.RED}⚠️ 無報價{C.RESET}")

    # 總結
    sec("🔑 執行要點")
    print(f"  {C.BOLD}1. 以上報價為實時 Derive.xyz 數據，掛單前請再次確認")
    print(f"  2. 限價單用 Ask/Bid 中間價掛單，唔好市價追")
    print(f"  3. Jul10 結算前輕倉，結算後先主菜（Jul31）")
    print(f"  4. 每個撈底位只買一次 0.01 張，唔溝淡")
    print(f"  5. 任何一張到食糊目標（$59,075 / $60,866）就執行{C.RESET}")
    print(f"\n  {C.RED}{C.BOLD}⚠️  期權可能全損。以上不構成投資建議。{C.RESET}\n")

    # 儲存
    fname = f"dip_scan_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(f"BTC 三層撈底 Jul31 CALL 掃描 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"現價: ${spot:,.2f}\n\n")
        f.write("=== 已驗證江恩線 ===\n")
        for price, name, strength, role in VERIFIED_GANN['lines']:
            pct = (spot - price) / spot * 100
            f.write(f"${price:,} - {name} (強度:{strength}) - {role} - 距現價{pct:+.1f}%\n")
        f.write(f"\n=== 撈底計劃 ===\n")
        for e in plan['entries']:
            f.write(f"${e['dip_price']:,} → ${e['best_strike']:,} CALL | 掛單${e['order_price_per_contract']}/張 | 0.01張=${e['cost_001']} | 彈$60K賺${e['rebound_profit']}\n")
        f.write(f"總成本: ${plan['total_cost']}\n\n")
        f.write("=== Jul31 CALL 真實報價 ===\n")
        for r in results:
            tag = f" 🎯{r['match_label']}" if r['is_target'] else ''
            f.write(f"{r['name']} | Mark${r['mark']:.0f} | Bid{r['bid']:.1f}/Ask{r['ask']:.1f} | IV{r['iv']}% | Δ{r['delta']:.4f} | 0.01張${r['prem_001']}{tag}\n")
    ok(f"報告已儲存: {fname}")


if __name__ == '__main__':
    if '--dip' in sys.argv:
        run_dip_scan()
    else:
        main()
