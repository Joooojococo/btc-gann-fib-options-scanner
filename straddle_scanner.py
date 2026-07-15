#!/usr/bin/env python3
"""
============================================================
  Long Straddle / Strangle 雙開策略掃描器
  用法: python3 straddle_scanner.py
  依賴: option_scanner_v3.py (VERIFIED_GANN, DIP_BUY_PLAN, get_spot, etc.)
============================================================
"""

import urllib.request
import urllib.error
import json
import math
import sys
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── 顏色 ──
class C:
    RED    = '\033[91m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    CYAN   = '\033[96m'
    WHITE  = '\033[97m'
    BOLD   = '\033[1m'
    DIM    = '\033[2m'
    RESET  = '\033[0m'

def hdr(t): print(f"\n{C.BOLD}{C.CYAN}{'='*62}{C.RESET}\n  {t}\n{C.BOLD}{C.CYAN}{'='*62}{C.RESET}\n")
def sec(t): print(f"\n{C.BOLD}{C.YELLOW}▶ {t}{C.RESET}\n{C.DIM}{'─'*55}{C.RESET}")
def ok(t):  print(f"  {C.GREEN}✅ {t}{C.RESET}")
def bad(t): print(f"  {C.RED}❌ {t}{C.RESET}")

# ── API ──
BASE    = 'https://api.lyra.finance'
HEADERS = {'Accept': 'application/json', 'User-Agent': 'StraddleScanner/1.0'}

def api_get(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except: return None


# ── LLM 方向分析 ──
def llm_direction_analysis(currency, spot, straddle_info, gann_lines, hv_30d):
    """Call LLM 判方向，出 directional 建議而唔係盲買 straddle"""
    api_key = os.getenv('OPENAI_API_KEY', '') or os.getenv('DEEPSEEK_API_KEY', '')
    if not api_key:
        return None

    provider = 'deepseek' if len(api_key) < 50 else 'openai'

    gann_text = '\n'.join(f"  ${p:,} {n} ({r}) — 距現價 {abs(p-spot)/spot*100:.1f}%" for p, n, s, r in gann_lines)

    straddle_text = ''
    if straddle_info:
        s = straddle_info
        straddle_text = f"""=== 掃到平 Straddle ===
  幣種: {currency}
  Strike: ${s['strike']:,} (距 spot {s['otm_pct']:.1f}%)
  Straddle 成本: ${s['cost']:.2f} ({s['cost_pct']:.1f}% of spot)
  IV: {s['iv']:.1f}%
  HV(30d): {hv_30d:.1f}% → IV {'折價' if s['iv'] < hv_30d else '溢價'} {abs(s['iv']-hv_30d)/hv_30d*100:.0f}%
  Call: ${s['call_price']:.2f} (IV {s['call_iv']:.1f}%)
  Put:  ${s['put_price']:.2f} (IV {s['put_iv']:.1f}%)
  BE上: ${s['be_up']:,.0f} ({(s['be_up']-spot)/spot*100:+.1f}%)
  BE下: ${s['be_down']:,.0f} ({(s['be_down']-spot)/spot*100:+.1f}%)
  到期: {s['expiry']} (0d, 高Gamma)"""

    prompt = f"""你係有30年經驗的頂級期權交易員。請用廣東話（粵語）分析，給出專業簡潔的方向判斷同策略建議。

=== 市場數據 ===
幣種: {currency}
現價: ${spot:,.2f}
HV(30d): {hv_30d:.1f}%

=== 江恩關鍵位 ===
{gann_text}

{straddle_text}

請提供：
1. **方向判斷**（2-3句）：依家偏好多定淡？定係橫行？引用江恩位支持你嘅判斷
2. **Straddle 值唔值買**（1-2句）：IV 折價但係方向明確嘅話，straddle 可能唔抵。定係橫行先至啱買 straddle？
3. **Directional 建議**（2-3句）：如果方向明確，建議買 CALL 定 PUT？邊個 Strike？目標價？止損位？
4. **替代策略**（1-2句）：如果方向唔明確，除咗 straddle 仲有咩選擇？（例如 Iron Condor、等待等）
5. **風險提示**（1句）：最大風險係咩？

用簡潔直接語氣。如果方向明確，明確建議單邊而唔好建議 straddle。"""

    if provider == 'deepseek':
        api_url = 'https://api.deepseek.com/v1/chat/completions'
        model = 'deepseek-chat'
    else:
        api_url = 'https://api.openai.com/v1/chat/completions'
        model = 'gpt-4o-mini'

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
    except Exception as e:
        return f"LLM 呼叫失敗: {e}"


def get_hv_30d(currency='BTC'):
    """用 daily candles 計 30日 historical volatility"""
    try:
        d = api_get(f'{BASE}/public/get_historical_prices?instrument_name={currency}-PERP&resolution=1D&limit=31')
        if not d or 'result' not in d or 'prices' not in d['result']:
            return 0
        prices = [float(c['close']) for c in d['result']['prices']]
        if len(prices) < 2:
            return 0
        returns = [math.log(prices[i]/prices[i-1]) for i in range(1, len(prices))]
        mean = sum(returns) / len(returns)
        var = sum((r - mean)**2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var) * math.sqrt(365) * 100
    except:
        return 0

def get_spot(currency='BTC'):
    d = api_get(f'{BASE}/public/get_ticker?instrument_name={currency}-PERP')
    return float(d['result']['mark_price']) if d and 'result' in d else 0.0

def get_instruments(currency='BTC'):
    d = api_get(f'{BASE}/public/get_instruments?currency={currency}&instrument_type=option&expired=false')
    return d['result'] if d and 'result' in d else []

def get_ticker(name):
    d = api_get(f'{BASE}/public/get_ticker?instrument_name={name}')
    return d['result'] if d and 'result' in d else {}

# ── 已驗證江恩數據（鎖定） ──
VERIFIED_GANN = {
    'lines': [
        (60866, 'Gann 1x8 DOWN', 3, '多空分界'),
        (59489, 'Gann 1x6 DOWN', 2, '中期阻力'),
        (59075, '三重共振支撐', 3, '最強支撐共振'),
        (56733, 'Gann 1x4 DOWN', 2, '第一站撈底'),
        (54261, 'Gann 1x2 主升浪線', 3, '長線最強支撐'),
        (48466, 'Gann 1x2 DOWN', 1, '終極防線'),
        (44856, 'Fib 0.238', 2, '終極支撐'),
    ],
}

DIP_BUY_PLAN = {
    'entries': [{
        'dip_price': 56733, 'best_strike': 62000,
        'order_price_per_contract': 2095, 'cost_001': 20.95,
    }],
}

# ── 主函數 ──
def run():
    hdr("BTC Long Straddle / Strangle 雙開策略掃描")

    today = datetime.now(tz=timezone.utc)

    # 1. 現價
    print(f"  [1/3] 拉取 BTC 現價...", end='', flush=True)
    spot = get_spot()
    if spot <= 0: bad("無法獲取現價"); return
    print(f" {C.GREEN}${spot:,.2f}{C.RESET}")

    # 2. 江恩線 + 入場判定
    sec("江恩線定位 — 雙開入場判定")
    above_lines, below_lines = [], []
    for price, name, strength, role in VERIFIED_GANN['lines']:
        stars = '🔥' * strength
        dist_pct = (spot - price) / spot * 100
        col = C.RED if dist_pct > 0 else C.GREEN
        arrow = '⬇' if dist_pct > 0 else '⬆'
        print(f"  ${price:<9,} {name:<24} {stars:<6} {col}{arrow} {abs(dist_pct):.1f}%{C.RESET}")
        if price > spot: above_lines.append((price, name, strength))
        else: below_lines.append((price, name, strength))

    above_lines.sort(key=lambda x: x[0])
    below_lines.sort(key=lambda x: -x[0])
    nr = above_lines[0] if above_lines else (spot*1.1, 'N/A', 0)
    ns = below_lines[0] if below_lines else (spot*0.9, 'N/A', 0)
    vsp = abs((nr[0] - ns[0]) / spot * 100)

    print(f"\n  {C.BOLD}最近阻力:{C.RESET} ${nr[0]:,} — {nr[1]}")
    print(f"  {C.BOLD}最近支撐:{C.RESET} ${ns[0]:,} — {ns[1]}")
    print(f"  {C.BOLD}波動空間:{C.RESET} {vsp:.1f}%")

    if vsp >= 8: verdict = f"{C.GREEN}✅ 適合雙開！波動空間 {vsp:.1f}% > 8%{C.RESET}"
    elif vsp >= 5: verdict = f"{C.YELLOW}⚠️ 勉強可雙開，需精選Strike{C.RESET}"
    else: verdict = f"{C.RED}❌ 不適合雙開，波動空間僅 {vsp:.1f}%{C.RESET}"
    print(f"  {verdict}")

    # 3. 掃描 Jul31 CALL + PUT
    sec("掃描 Jul31 CALL + PUT（ATM +/- $6K）")
    print(f"  拉取期權清單...", end='', flush=True)
    insts = get_instruments()
    calls_raw = [i for i in insts if i['option_details']['option_type'] == 'C']
    puts_raw = [i for i in insts if i['option_details']['option_type'] == 'P']
    print(f" {len(calls_raw)} CALL, {len(puts_raw)} PUT")

    strike_min = int(spot) - 7000
    strike_max = int(spot) + 7000

    def fetch(options_list):
        data = {}
        for c in options_list:
            opts = c['option_details']
            strike = int(opts['strike'])
            exp_ts = opts['expiry']
            exp_date = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
            dte = (exp_date - today).total_seconds() / 86400
            if dte < 0 or dte > 90: continue
            if not (strike_min <= strike <= strike_max): continue
            t = get_ticker(c['instrument_name'])
            if not t: continue
            mark = float(t.get('mark_price', 0))
            bid = float(t.get('best_bid_price', 0) or 0)
            ask = float(t.get('best_ask_price', 0) or 0)
            if bid <= 0 and ask <= 0: continue
            p = t.get('option_pricing', {})
            iv = float(p.get('iv', 0) or 0)
            mid = (bid + ask) / 2 if (bid + ask) > 0 else ask
            spr = (ask - bid) / mid * 100 if mid > 0 else 200
            data[strike] = {
                'name': c['instrument_name'], 'strike': strike,
                'expiry': exp_date.strftime('%Y-%m-%d'), 'dte': round(dte, 1),
                'bid': bid, 'ask': ask, 'mark': mark,
                'spread': round(spr, 1), 'iv': round(iv * 100, 1),
                'prem_001': round(mark * 0.01, 2), 'prem_1': round(mark, 0),
            }
        return data

    call_data = fetch(calls_raw)
    put_data = fetch(puts_raw)

    # ── Straddle 表格 ──
    sec("Long Straddle（同Strike CALL+PUT）")
    common = sorted(set(call_data.keys()) & set(put_data.keys()))
    if not common:
        print(f"  {C.RED}無同時有CALL+PUT的Strike{C.RESET}")
    else:
        print(f"  {'Strike':<8} {'CALL':<10} {'PUT':<10} {'總成本':<10} {'BE上':<10} {'BE下':<10} {'上距':<7} {'下距':<7}")
        print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*7} {'─'*7}")
        for s in common:
            c = call_data[s]; p = put_data[s]
            t = c['prem_001'] + p['prem_001']
            be_up = s + t * 100; be_down = s - t * 100
            atm = abs(s - spot) / spot < 0.02
            col = C.GREEN if atm else C.DIM
            print(f"  {col}${s:<7,}{C.RESET} ${c['prem_001']:<9.2f} ${p['prem_001']:<9.2f} "
                  f"${t:<9.2f} ${be_up:<9,.0f} ${be_down:<9,.0f} "
                  f"{(be_up-spot)/spot*100:+.1f}%{'':>3} {(spot-be_down)/spot*100:.1f}%{'':>3} "
                  f"{col}{'🎯ATM' if atm else ''}{C.RESET}")
            # Show BE vs Gann
            for gp, gn, gs, gr in VERIFIED_GANN['lines']:
                if abs(be_up - gp) / gp < 0.03:
                    print(f"    ↳ BE上 ${be_up:,.0f} 接近 {C.GREEN}{gn} ${gp:,}{C.RESET}")
                if abs(be_down - gp) / gp < 0.03:
                    print(f"    ↳ BE下 ${be_down:,.0f} 接近 {C.GREEN}{gn} ${gp:,}{C.RESET}")

    sgl = []  # Initialize strangle list

    # ── Strangle 表格 ──
    sec("Long Strangle（OTM CALL + OTM PUT 降成本）")
    call_otm = sorted([s for s in call_data if s > spot])
    put_otm = sorted([s for s in put_data if s < spot], reverse=True)
    if not call_otm or not put_otm:
        print(f"  {C.RED}無足夠OTM合約做Strangle{C.RESET}")
    else:
        print(f"  {'CALL K':<8} {'PUT K':<8} {'CALL':<10} {'PUT':<10} {'總成本':<10} {'BE上':<10} {'BE下':<10} {'上距':<7} {'下距':<7} {'對稱'}")
        print(f"  {'─'*8} {'─'*8} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*7} {'─'*7} {'─'*5}")
        sgl = []
        for cs in call_otm[:5]:
            for ps in put_otm[:5]:
                if cs <= ps: continue
                c = call_data[cs]; p = put_data[ps]
                t = c['prem_001'] + p['prem_001']
                bu = cs + t * 100; bd = ps - t * 100
                co = (cs-spot)/spot*100; po = (spot-ps)/spot*100
                sym = abs(co - po)
                sgl.append({'cs':cs,'ps':ps,'cp':c['prem_001'],'pp':p['prem_001'],
                    't':t,'bu':bu,'bd':bd,'bu_p':(bu-spot)/spot*100,'bd_p':(spot-bd)/spot*100,'sym':sym})
        sgl.sort(key=lambda x: (x['sym'], x['t']))
        for r in sgl[:8]:
            sc = C.GREEN if r['sym'] < 3 else (C.YELLOW if r['sym'] < 6 else C.RESET)
            print(f"  ${r['cs']:<7,} ${r['ps']:<7,} ${r['cp']:<9.2f} ${r['pp']:<9.2f} "
                  f"${r['t']:<9.2f} ${r['bu']:<9,.0f} ${r['bd']:<9,.0f} "
                  f"{r['bu_p']:+.1f}%{'':>3} {r['bd_p']:.1f}%{'':>3} {sc}Δ{r['sym']:.1f}%{C.RESET}")

    # ── 三策略對比 ──
    sec("三策略終極對比")
    dip_e = DIP_BUY_PLAN['entries'][0]
    dc, dbe = dip_e['cost_001'], dip_e['best_strike'] + dip_e['order_price_per_contract']

    # 最佳Straddle (最接近現價)
    # ── 🏆 Straddle 入場評分 Alert ──
    sec("🏆 Straddle 入場評分 Alert（條件：CALL≈PUT + IV≈ + Δ≈0.5）")
    if common:
        print(f"  {'Strike':<8} {'CALL':<10} {'PUT':<10} {'價差':<8} {'Δ CALL':<8} {'Δ PUT':<8} {'IV差':<7} {'Spr':<7} {'評分':<6} {'判定'}")
        print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*6} {'─'*10}")
        best_alert = None
        best_alert_score = 0
        for s in common:
            c = call_data[s]; p = put_data[s]
            price_diff = abs(c['prem_001'] - p['prem_001'])
            iv_diff = abs(c['iv'] - p['iv'])
            avg_spr = (c['spread'] + p['spread']) / 2
            # 需要 ticker data 獲取 Delta — 用 mark 推算
            call_delta = round(0.5 + (s - spot) / spot * 2, 2)  # ATM 近似
            put_delta = round(-0.5 + (spot - s) / spot * 2, 2)
            # 評分
            score = 0
            if price_diff < 1: score += 30
            elif price_diff < 2: score += 20
            elif price_diff < 3: score += 10
            if iv_diff < 2: score += 30
            elif iv_diff < 4: score += 20
            elif iv_diff < 6: score += 10
            if avg_spr < 8: score += 25
            elif avg_spr < 15: score += 15
            elif avg_spr < 25: score += 5
            atm_bonus = abs(s - spot) / spot < 0.015
            if atm_bonus: score += 15
            # 判定
            if score >= 70: verdict = f"{C.GREEN}🔥 最佳入場{C.RESET}"
            elif score >= 50: verdict = f"{C.YELLOW}✅ 可考慮{C.RESET}"
            elif score >= 30: verdict = f"{C.DIM}⚠️ 一般{C.RESET}"
            else: verdict = f"{C.RED}❌ 唔建議{C.RESET}"
            col = C.GREEN if score >= 70 else (C.YELLOW if score >= 50 else C.DIM)
            print(f"  {col}${s:<7,}{C.RESET} ${c['prem_001']:<9.2f} ${p['prem_001']:<9.2f} "
                  f"${price_diff:<7.2f} {call_delta:<8.2f} {put_delta:<8.2f} "
                  f"{iv_diff:<6.1f}% {avg_spr:<6.1f}% "
                  f"{col}{score:<5}/{C.RESET} {verdict}")
            if score > best_alert_score:
                best_alert_score = score
                best_alert = (s, c, p, price_diff, iv_diff, avg_spr, score, call_delta, put_delta)
        if best_alert and best_alert_score >= 50:
            s, c, p, pd, ivd, sp, sc, cd, ptd = best_alert
            t = c['prem_001'] + p['prem_001']
            print(f"\n  {C.BOLD}{C.GREEN}🔔 ALERT: 最佳Straddle = \${s:,} @ \${t:.2f}{C.RESET}")
            print(f"    CALL \${c['prem_001']:.2f} | PUT \${p['prem_001']:.2f} | 價差 \${pd:.2f} | IV差 {ivd:.1f}%")
            print(f"    BE⬆ \${s+t*100:,.0f} | BE⬇ \${s-t*100:,.0f} | 最大蝕 \${t:.2f} | 評分 {sc}/100")
    else:
        print(f"  {C.RED}無Straddle組合{C.RESET}")

    bs = None
    if common:
        atm_s = [s for s in common if abs(s-spot)/spot < 0.03]
        if atm_s:
            s0 = min(atm_s, key=lambda s: abs(s-spot))
            c0, p0 = call_data[s0], put_data[s0]
            t0 = c0['prem_001'] + p0['prem_001']
            bs = (s0, t0, s0 + t0 * 100, s0 - t0 * 100)

    bsg = sgl[0] if sgl else None

    print(f"\n  {C.BOLD}{'策略':<22} {'成本':<13} {'BE上':<12} {'BE下':<12} {'適合場景'}{C.RESET}")
    print(f"  {'─'*22} {'─'*13} {'─'*12} {'─'*12} {'─'*28}")

    print(f"  {C.GREEN}🥇 CALL撈底 $56.7K{C.RESET}     ${dc:<12.2f} ${dbe:<11,.0f} {'N/A':<12} 確信跌到$56.7K→彈$60K")

    if bs:
        print(f"  {C.YELLOW}🔀 Straddle ATM{C.RESET}        ${bs[1]:<12.2f} ${bs[2]:<11,.0f} ${bs[3]:<11,.0f} 方向不明，賭大波動")
    else:
        print(f"  {C.DIM}🔀 Straddle — 無ATM報價{C.RESET}")

    if bsg:
        print(f"  {C.YELLOW}🔀 Strangle C${bsg['cs']:<3,}P${bsg['ps']:<3,}{C.RESET}    ${bsg['t']:<12.2f} ${bsg['bu']:<11,.0f} ${bsg['bd']:<11,.0f} 降成本，需更大波幅")
    else:
        print(f"  {C.DIM}🔀 Strangle — 無合約{C.RESET}")

    # BE vs Gann
    if bs or bsg:
        print(f"\n  {C.BOLD}📐 BE對照江恩線：{C.RESET}")
        for gp, gn, gs, gr in VERIFIED_GANN['lines']:
            hits = []
            if bs:
                if abs(bs[2]-gp)/gp < 0.03: hits.append(f"Straddle BE上 ${bs[2]:,.0f}")
                if abs(bs[3]-gp)/gp < 0.03: hits.append(f"Straddle BE下 ${bs[3]:,.0f}")
            if bsg:
                if abs(bsg['bu']-gp)/gp < 0.03: hits.append(f"Strangle BE上 ${bsg['bu']:,.0f}")
                if abs(bsg['bd']-gp)/gp < 0.03: hits.append(f"Strangle BE下 ${bsg['bd']:,.0f}")
            if hits: print(f"    ${gp:,} ({gn}): {C.GREEN}{', '.join(hits)}{C.RESET}")

    # ── 入場判定 ──
    sec("入場判定框架（按雙開指引）")
    print(f"  {C.BOLD}✅ 適合雙開條件：{C.RESET}")
    print(f"    1. 技術面瓶頸位 — BTC踩$59,489 Gann 1x6 DOWN")
    print(f"    2. 波動空間 — {ns[0]:,}↔{nr[0]:,} = {vsp:.1f}%")
    print(f"    3. IV — Jul31 ~42-49%，中等非極高")
    print()
    print(f"  {C.RED}❌ 風險：{C.RESET}")
    print(f"    1. 現有PUT Jul10 $55K已提供下方保護，雙開可能重複")
    print(f"    2. IV Crush — Jul10結算後IV或暴跌")
    print(f"    3. Theta — 35日DTE每日衰減")
    print()
    print(f"  {C.YELLOW}💡 建議：{C.RESET}")
    print(f"    堅守撈底計劃。等$56.7K先出手CALL Jul31。")
    print(f"    如想做雙開，等Jul10結算後(7/11)先用Jul31做。")
    print(f"    現有PUT Jul10 $55K已提供下方保護，無需額外雙開。")
    print(f"\n  {C.RED}{C.BOLD}⚠️ 期權可能全損。以上不構成投資建議。{C.RESET}\n")

    # ── LLM 方向分析 ──
    api_key = os.getenv('OPENAI_API_KEY', '') or os.getenv('DEEPSEEK_API_KEY', '')
    if api_key:
        sec("🤖 LLM 方向分析")
        print(f"  {C.DIM}分析中，請稍候...{C.RESET}")

        # 準備最佳 straddle info
        straddle_info = None
        if bs:
            s0, t0, be_up, be_down = bs
            c0 = call_data[s0]
            p0 = put_data[s0]
            straddle_info = {
                'strike': s0,
                'otm_pct': abs(s0 - spot) / spot * 100,
                'cost': t0,
                'cost_pct': t0 / spot * 100,
                'iv': (c0['iv'] + p0['iv']) / 2,
                'call_price': c0['prem_001'],
                'put_price': p0['prem_001'],
                'call_iv': c0['iv'],
                'put_iv': p0['iv'],
                'be_up': be_up,
                'be_down': be_down,
                'expiry': c0['expiry'],
            }

        hv = get_hv_30d('BTC')
        if hv > 0:
            print(f"  HV(30d): {hv:.1f}%", flush=True)

        analysis = llm_direction_analysis('BTC', spot, straddle_info, VERIFIED_GANN['lines'], hv if hv > 0 else 33.0)
        if analysis:
            print(f"\n{C.BOLD}{C.GREEN}{'─'*62}{C.RESET}")
            print(f"{C.BOLD}AI 方向分析報告：{C.RESET}\n")
            print(analysis)
            print(f"{C.BOLD}{C.GREEN}{'─'*62}{C.RESET}\n")
        else:
            print(f"  {C.RED}LLM 分析失敗{C.RESET}\n")
    else:
        tip("設定 OPENAI_API_KEY 或 DEEPSEEK_API_KEY 環境變數啟用 LLM 方向分析")

    # 儲存
    fname = f"straddle_scan_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(f"BTC Straddle/Strangle 掃描 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"現價: ${spot:,.2f}\n\n")
        f.write(f"江恩波動空間: {vsp:.1f}% ({ns[0]:,}↔{nr[0]:,})\n\n")
        f.write("=== Straddle ===\n")
        for s in common:
            c = call_data[s]; p = put_data[s]
            t = c['prem_001'] + p['prem_001']
            f.write(f"${s:,}: ${t:.2f} | BE上${s+t*100:,.0f} BE下${s-t*100:,.0f}\n")
        if sgl:
            f.write("\n=== Strangle ===\n")
            for r in sgl[:4]:
                f.write(f"C${r['cs']:,}+P${r['ps']:,}: ${r['t']:.2f} | BE上${r['bu']:,.0f} BE下${r['bd']:,.0f}\n")
        f.write(f"\n=== 三策略對比 ===\n")
        f.write(f"純CALL撈底: ${dc:.2f} | BE ${dbe:,.0f}\n")
        if bs: f.write(f"Straddle: ${bs[1]:.2f} | BE上${bs[2]:,.0f} BE下${bs[3]:,.0f}\n")
        if bsg: f.write(f"Strangle: ${bsg['t']:.2f} | BE上${bsg['bu']:,.0f} BE下${bsg['bd']:,.0f}\n")
    ok(f"報告已儲存: {fname}")

if __name__ == '__main__':
    run()