"""
Enhanced multi-indicator scan — 19 indicators, 6 timeframes
Indicators:
  SuperTrend (10,3) | EMA stack (20/50/200) | ARVA RSI | MACD (12,26,9)
  ADX+DI | Bollinger Bands (20,2) | Aroon (25) | ATR Trailing Stop
  B-Trend % | Percentile SuperTrend | Stochastic RSI | Ichimoku Cloud
  CMF (20) | OBV trend | MFI (14) | CCI (20) | Hull MA (20) | Parabolic SAR
  Volume Surge
Timeframes: 1H | 2H | 4H | 6H | 12H | 1D
Upgrades: A11 (1D RSI extended cap) | A12 (volume surge signal) | A13 (RS vs BTC bonus)
          A14 (funding rate signal) | A15 (RSI divergence detection) | A16 (pre-pump: BB squeeze/OBV div/ATR coil)
"""

import requests, time, sys, argparse, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ACCOUNT_USDT = 100_578.48
RISK_PCT     = 0.015
BYBIT_API    = "https://api.bybit.com"

# Timeframes: (label, bybit_interval, weight)
TIMEFRAMES = [
    ("1D",  "D",   0.28),
    ("12H", "720", 0.20),
    ("6H",  "360", 0.15),
    ("4H",  "240", 0.15),
    ("2H",  "120", 0.12),
    ("1H",  "60",  0.10),
]

def fetch_all_perps(min_vol_usdt=1_000_000):
    """Return all active Bybit linear USDT perpetuals above min 24h turnover."""
    try:
        r1 = requests.get(f"{BYBIT_API}/v5/market/instruments-info",
                          params={"category": "linear", "limit": 1000}, timeout=15)
        instruments = {
            d["symbol"]: d["baseCoin"]
            for d in r1.json()["result"]["list"]
            if d["status"] == "Trading"
            and d["quoteCoin"] == "USDT"
            and d["contractType"] == "LinearPerpetual"
        }
    except Exception as e:
        print(f"ERROR fetching instruments: {e}"); sys.exit(1)

    try:
        r2 = requests.get(f"{BYBIT_API}/v5/market/tickers",
                          params={"category": "linear"}, timeout=15)
        tickers = r2.json()["result"]["list"]
    except Exception as e:
        print(f"ERROR fetching tickers: {e}"); sys.exit(1)

    candidates = []
    funding_rates = {}
    for t in tickers:
        sym = t["symbol"]
        if sym not in instruments: continue
        vol = float(t.get("turnover24h") or 0)
        if vol < min_vol_usdt: continue
        funding_rates[sym] = float(t.get("fundingRate") or 0)
        candidates.append((sym, "linear", instruments[sym]))

    candidates.sort(key=lambda x: x[2])
    return candidates, funding_rates


# ── data ─────────────────────────────────────────────────────────────────────

def fetch(symbol, category, interval="D", limit=200):
    try:
        r = requests.get(f"{BYBIT_API}/v5/market/kline",
                         params={"category": category, "symbol": symbol,
                                 "interval": interval, "limit": limit},
                         timeout=10)
        raw = r.json().get("result", {}).get("list", [])
        raw.reverse()
        return [{"o": float(c[1]), "h": float(c[2]),
                 "l": float(c[3]), "c": float(c[4]), "v": float(c[5])}
                for c in raw]
    except Exception:
        return []


# ── base math ────────────────────────────────────────────────────────────────

def ema(vals, p):
    k, res = 2/(p+1), [None]*len(vals)
    for i, v in enumerate(vals):
        if i < p-1: continue
        res[i] = sum(vals[i-p+1:i+1])/p if i == p-1 else v*k + res[i-1]*(1-k)
    return res

def wma(vals, p):
    res = [None]*len(vals)
    weights = list(range(1, p+1))
    total_w = sum(weights)
    for i in range(p-1, len(vals)):
        w = vals[i-p+1:i+1]
        res[i] = sum(w[j]*weights[j] for j in range(p)) / total_w
    return res

def sma(vals, p):
    res = [None]*len(vals)
    for i in range(p-1, len(vals)):
        res[i] = sum(vals[i-p+1:i+1]) / p
    return res

def stdev(vals, p):
    res = [None]*len(vals)
    for i in range(p-1, len(vals)):
        w = vals[i-p+1:i+1]
        m = sum(w)/p
        res[i] = (sum((x-m)**2 for x in w)/p)**0.5
    return res


# ── indicators ───────────────────────────────────────────────────────────────

def atr_series(candles, p=14):
    trs = [candles[0]["h"]-candles[0]["l"]]
    for i in range(1, len(candles)):
        trs.append(max(candles[i]["h"]-candles[i]["l"],
                       abs(candles[i]["h"]-candles[i-1]["c"]),
                       abs(candles[i]["l"]-candles[i-1]["c"])))
    res = [None]*len(candles)
    if len(trs) < p: return res
    res[p-1] = sum(trs[:p])/p
    for i in range(p, len(candles)):
        res[i] = (res[i-1]*(p-1)+trs[i])/p
    return res

def rsi_series(closes, p=14):
    res = [None]*len(closes)
    if len(closes) < p+1: return res
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses= [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[:p])/p; al = sum(losses[:p])/p
    for i in range(p, len(closes)):
        res[i] = 100 if al==0 else 100-100/(1+ag/al)
        if i < len(closes)-1:
            d = closes[i+1]-closes[i]
            ag=(ag*(p-1)+max(d,0))/p; al=(al*(p-1)+max(-d,0))/p
    return res

def macd_series(closes, fast=12, slow=26, sig=9):
    ef = ema(closes, fast); es = ema(closes, slow)
    macd = [ef[i]-es[i] if ef[i] is not None and es[i] is not None else None
            for i in range(len(closes))]
    valid = [v for v in macd if v is not None]
    sig_vals = [None]*len(closes)
    base = next((i for i,v in enumerate(macd) if v is not None), None)
    if base is None or len(valid) < sig: return macd, sig_vals, [None]*len(closes)
    sig_list = ema(valid, sig)
    for i, sv in enumerate(sig_list):
        if sv is not None: sig_vals[base+i] = sv
    hist = [macd[i]-sig_vals[i] if macd[i] is not None and sig_vals[i] is not None else None
            for i in range(len(closes))]
    return macd, sig_vals, hist

def supertrend_series(candles, p=10, m=3.0):
    atrs = atr_series(candles, p)
    up=[None]*len(candles); dn=[None]*len(candles); tr=[None]*len(candles)
    for i in range(p, len(candles)):
        hl2=(candles[i]["h"]+candles[i]["l"])/2; a=atrs[i]
        if a is None: continue
        bu=hl2-m*a; bd=hl2+m*a
        up[i]=bu if up[i-1] is None or bu>up[i-1] or candles[i-1]["c"]<up[i-1] else up[i-1]
        dn[i]=bd if dn[i-1] is None or bd<dn[i-1] or candles[i-1]["c"]>dn[i-1] else dn[i-1]
        if tr[i-1] is None:   tr[i]=1 if candles[i]["c"]>dn[i] else -1
        elif tr[i-1]==-1 and candles[i]["c"]>(dn[i-1] or 0): tr[i]=1
        elif tr[i-1]==1  and candles[i]["c"]<(up[i-1] or 1e9): tr[i]=-1
        else: tr[i]=tr[i-1]
    return tr, up, dn

def adx_series(candles, p=14):
    plus_dm=[0.0]; minus_dm=[0.0]; tr_=[candles[0]["h"]-candles[0]["l"]]
    for i in range(1, len(candles)):
        h,l,ph,pl=candles[i]["h"],candles[i]["l"],candles[i-1]["h"],candles[i-1]["l"]
        plus_dm.append(max(h-ph,0) if h-ph>pl-l else 0)
        minus_dm.append(max(pl-l,0) if pl-l>h-ph else 0)
        tr_.append(max(h-l, abs(h-candles[i-1]["c"]), abs(l-candles[i-1]["c"])))
    def wilder(arr, p):
        res=[None]*len(arr)
        if len(arr)<p: return res
        res[p-1]=sum(arr[:p])
        for i in range(p, len(arr)): res[i]=res[i-1]-res[i-1]/p+arr[i]
        return res
    atr_w=wilder(tr_,p); pdm_w=wilder(plus_dm,p); ndm_w=wilder(minus_dm,p)
    adx_=[None]*len(candles); pdi_=[None]*len(candles); ndi_=[None]*len(candles)
    dx_=[]
    for i in range(len(candles)):
        if atr_w[i] and atr_w[i]>0:
            pdi_[i]=(pdm_w[i]/atr_w[i])*100
            ndi_[i]=(ndm_w[i]/atr_w[i])*100
            dx_.append(abs(pdi_[i]-ndi_[i])/(pdi_[i]+ndi_[i])*100 if pdi_[i]+ndi_[i]>0 else 0)
        else:
            dx_.append(None)
    valid_dx=[v for v in dx_ if v is not None]
    if len(valid_dx)<p: return adx_,pdi_,ndi_
    adx_smooth=wilder(valid_dx,p)
    base=next((i for i,v in enumerate(dx_) if v is not None), None)
    if base is not None:
        for i, av in enumerate(adx_smooth):
            if av is not None and base+i<len(adx_): adx_[base+i]=av/p
    return adx_,pdi_,ndi_

def bollinger(closes, p=20, k=2.0):
    sm=sma(closes,p); sd=stdev(closes,p)
    upper=[sm[i]+k*sd[i] if sm[i] else None for i in range(len(closes))]
    lower=[sm[i]-k*sd[i] if sm[i] else None for i in range(len(closes))]
    return sm, upper, lower

def keltner_series(candles, p=20, atr_p=10, mult=1.5):
    closes=[c["c"] for c in candles]
    mid=ema(closes,p); atrs=atr_series(candles,atr_p)
    upper=[mid[i]+mult*atrs[i] if mid[i] is not None and atrs[i] is not None else None for i in range(len(candles))]
    lower=[mid[i]-mult*atrs[i] if mid[i] is not None and atrs[i] is not None else None for i in range(len(candles))]
    return upper, mid, lower

def aroon(candles, p=25):
    highs=[c["h"] for c in candles]; lows=[c["l"] for c in candles]
    up=[None]*len(candles); dn=[None]*len(candles)
    for i in range(p, len(candles)):
        hw=highs[i-p:i+1]; lw=lows[i-p:i+1]
        up[i]=((p-hw[::-1].index(max(hw)))/p)*100
        dn[i]=((p-lw[::-1].index(min(lw)))/p)*100
    return up, dn

def atr_trailing_stop(candles, p=14, m=3.0):
    atrs=atr_series(candles,p)
    trail=[None]*len(candles)
    for i in range(p, len(candles)):
        a=atrs[i]
        if a is None: continue
        c=candles[i]["c"]; pc=candles[i-1]["c"]
        if trail[i-1] is None: trail[i]=c-m*a; continue
        if c>trail[i-1] and pc>trail[i-1]:
            trail[i]=max(trail[i-1], c-m*a)
        elif c<trail[i-1] and pc<trail[i-1]:
            trail[i]=min(trail[i-1], c+m*a)
        elif c>trail[i-1]:
            trail[i]=c-m*a
        else:
            trail[i]=c+m*a
    bull=[candles[i]["c"]>trail[i] if trail[i] is not None else None for i in range(len(candles))]
    return trail, bull

def percentile_supertrend(candles, p=14, pct_low=25, pct_high=75):
    closes=[c["c"] for c in candles]; window=20
    trend=[None]*len(candles)
    for i in range(window+p, len(candles)):
        recent=closes[i-window:i]; sorted_c=sorted(recent)
        low_pct=sorted_c[int(len(sorted_c)*pct_low/100)]
        high_pct=sorted_c[int(len(sorted_c)*pct_high/100)]
        if closes[i]>high_pct: trend[i]=1
        elif closes[i]<low_pct: trend[i]=-1
        else: trend[i]=trend[i-1] if trend[i-1] else 0
    return trend

def b_trend_pct(candles, fast=4, slow=12):
    closes=[c["c"] for c in candles]
    ef=ema(closes,fast); es=ema(closes,slow)
    res=[None]*len(closes)
    for i in range(len(closes)):
        if ef[i] and es[i] and es[i]!=0:
            res[i]=(ef[i]-es[i])/es[i]*100
    return res

def arva_rsi(closes, rsi_p=14, atr_mult=2.5):
    rv=rsi_series(closes,rsi_p)
    rsi_clean=[v if v is not None else 50 for v in rv]
    sd_=stdev(rsi_clean,rsi_p)
    upper=[rv[i]+atr_mult*sd_[i] if rv[i] and sd_[i] else None for i in range(len(closes))]
    lower=[rv[i]-atr_mult*sd_[i] if rv[i] and sd_[i] else None for i in range(len(closes))]
    return rv, upper, lower

def stoch_rsi_series(closes, rsi_p=14, stoch_p=14, k_p=3, d_p=3):
    rv = rsi_series(closes, rsi_p)
    rv_clean = [v if v is not None else 50.0 for v in rv]
    stoch = [None]*len(closes)
    for i in range(stoch_p-1, len(closes)):
        window = rv_clean[i-stoch_p+1:i+1]
        lo, hi = min(window), max(window)
        stoch[i] = (rv_clean[i]-lo)/(hi-lo)*100 if hi!=lo else 50.0
    stoch_clean = [v if v is not None else 50.0 for v in stoch]
    k_line = sma(stoch_clean, k_p)
    k_clean = [v if v is not None else 50.0 for v in k_line]
    d_line = sma(k_clean, d_p)
    return k_line, d_line

def ichimoku(candles, t=9, k=26, s=52):
    highs=[c["h"] for c in candles]; lows=[c["l"] for c in candles]
    n=len(candles)
    tenkan=[None]*n; kijun=[None]*n; senkou_a=[None]*n; senkou_b=[None]*n
    for i in range(t-1, n):
        tenkan[i]=(max(highs[i-t+1:i+1])+min(lows[i-t+1:i+1]))/2
    for i in range(k-1, n):
        kijun[i]=(max(highs[i-k+1:i+1])+min(lows[i-k+1:i+1]))/2
    for i in range(k-1, n):
        if tenkan[i] is not None and kijun[i] is not None:
            senkou_a[i]=(tenkan[i]+kijun[i])/2
    for i in range(s-1, n):
        senkou_b[i]=(max(highs[i-s+1:i+1])+min(lows[i-s+1:i+1]))/2
    return tenkan, kijun, senkou_a, senkou_b

def cmf_series(candles, p=20):
    res=[None]*len(candles)
    for i in range(p-1, len(candles)):
        vol_sum=mf_sum=0.0
        for c in candles[i-p+1:i+1]:
            hl=c["h"]-c["l"]
            if hl==0: continue
            mf_sum+=((c["c"]-c["l"])-(c["h"]-c["c"]))/hl*c["v"]
            vol_sum+=c["v"]
        res[i]=mf_sum/vol_sum if vol_sum else 0.0
    return res

def obv_series(candles):
    obv=[0.0]*len(candles)
    for i in range(1, len(candles)):
        if   candles[i]["c"]>candles[i-1]["c"]: obv[i]=obv[i-1]+candles[i]["v"]
        elif candles[i]["c"]<candles[i-1]["c"]: obv[i]=obv[i-1]-candles[i]["v"]
        else:                                    obv[i]=obv[i-1]
    return obv

def mfi_series(candles, p=14):
    res=[None]*len(candles)
    tp=[(c["h"]+c["l"]+c["c"])/3 for c in candles]
    rmf=[tp[i]*candles[i]["v"] for i in range(len(candles))]
    for i in range(p, len(candles)):
        pos=sum(rmf[j] for j in range(i-p+1,i+1) if tp[j]>tp[j-1])
        neg=sum(rmf[j] for j in range(i-p+1,i+1) if tp[j]<=tp[j-1])
        res[i]=100 if neg==0 else 100-100/(1+pos/neg)
    return res

def cci_series(candles, p=20):
    tp=[(c["h"]+c["l"]+c["c"])/3 for c in candles]
    res=[None]*len(candles)
    for i in range(p-1, len(candles)):
        w=tp[i-p+1:i+1]; m=sum(w)/p
        md=sum(abs(x-m) for x in w)/p
        res[i]=(tp[i]-m)/(0.015*md) if md else 0.0
    return res

def hull_ma(closes, p=20):
    hp=max(p//2, 2); sp=max(int(p**0.5), 2)
    wh=wma(closes, hp); wf=wma(closes, p)
    raw=[2*wh[i]-wf[i] if wh[i] is not None and wf[i] is not None else None
         for i in range(len(closes))]
    raw_clean=[v if v is not None else 0.0 for v in raw]
    return wma(raw_clean, sp)

def parabolic_sar(candles, af_start=0.02, af_step=0.02, af_max=0.2):
    n=len(candles)
    if n<3: return [None]*n, [None]*n
    bull=candles[1]["c"]>candles[0]["c"]
    ep=candles[0]["h"] if bull else candles[0]["l"]
    af=af_start
    sar_=[candles[0]["l"] if bull else candles[0]["h"]] + [None]*(n-1)
    for i in range(1, n):
        ps=sar_[i-1]
        ns=ps+af*(ep-ps)
        if bull:
            ns=min(ns, candles[i-1]["l"], candles[max(0,i-2)]["l"])
            if candles[i]["l"]<ns:
                bull=False; ns=ep; ep=candles[i]["l"]; af=af_start
            else:
                if candles[i]["h"]>ep: ep=candles[i]["h"]; af=min(af+af_step,af_max)
        else:
            ns=max(ns, candles[i-1]["h"], candles[max(0,i-2)]["h"])
            if candles[i]["h"]>ns:
                bull=True; ns=ep; ep=candles[i]["h"]; af=af_start
            else:
                if candles[i]["l"]<ep: ep=candles[i]["l"]; af=min(af+af_step,af_max)
        sar_[i]=ns
    bull_arr=[candles[i]["c"]>sar_[i] if sar_[i] is not None else None for i in range(n)]
    return sar_, bull_arr


# ── pre-pump detectors (A16) ─────────────────────────────────────────────────

def bb_squeeze_signal(candles):
    """TTM Squeeze: BB inside Keltner Channels = volatility coiling before expansion."""
    if len(candles) < 25: return False
    closes=[c["c"] for c in candles]; price=closes[-1]
    _,bu,bl=bollinger(closes,20,2.0)
    ku,_,kl=keltner_series(candles,20,10,1.5)
    bu_n=next((v for v in reversed(bu) if v is not None),None)
    bl_n=next((v for v in reversed(bl) if v is not None),None)
    ku_n=next((v for v in reversed(ku) if v is not None),None)
    kl_n=next((v for v in reversed(kl) if v is not None),None)
    if None in (bu_n,bl_n,ku_n,kl_n): return False
    inside_kc=bl_n>=kl_n and bu_n<=ku_n
    return (bu_n-bl_n)/price*100 < 4.5 or inside_kc

def obv_divergence_signal(candles, n=12):
    """Price flat/down while OBV rising = smart money accumulating quietly."""
    if len(candles) < n+5: return False
    obv=obv_series(candles)
    price_chg=(candles[-1]["c"]-candles[-n]["c"])/(candles[-n]["c"]+1e-9)
    obv_chg=(obv[-1]-obv[-n])/(abs(obv[-n])+1e-9)
    return price_chg<=0.03 and obv_chg>0.02

def atr_contraction_signal(candles):
    """Current ATR < 70% of 30-bar avg = spring loading before expansion."""
    if len(candles) < 35: return False
    atrs=atr_series(candles,14); price=candles[-1]["c"]
    if not price: return False
    valid=[a for a in atrs[-30:] if a is not None]
    if len(valid)<10 or atrs[-1] is None: return False
    return atrs[-1]/price < (sum(valid)/len(valid))/price*0.7

def vol_building_signal(candles):
    """Recent 6h vol 1.2–4.5× baseline = early ramp, not yet pumped."""
    if len(candles) < 30: return False
    vols=[c["v"] for c in candles]
    base=sum(vols[-30:-6])/24
    if base<=0: return False
    ratio=sum(vols[-6:])/6/base
    return 1.2<=ratio<=4.5

def higher_lows_signal(candles):
    """Three consecutive higher 5-bar swing lows = early uptrend structure."""
    if len(candles) < 15: return False
    c=[x["c"] for x in candles]
    lows=[(i,c[i]) for i in range(2,len(c)-2)
          if c[i]<c[i-1] and c[i]<c[i+1] and c[i]<c[i-2] and c[i]<c[i+2]]
    return len(lows)>=3 and lows[-1][1]>lows[-2][1]>lows[-3][1]

def prepump_bonus(candles_1h):
    """Pre-pump detector on 1H candles. Returns (bonus_pts, signal_list)."""
    if len(candles_1h) < 30: return 0, []
    sigs=[]; pts=0
    if bb_squeeze_signal(candles_1h):       sigs.append("BB_SQUEEZE"); pts+=15
    if obv_divergence_signal(candles_1h):   sigs.append("OBV_DIV");    pts+=12
    if atr_contraction_signal(candles_1h):  sigs.append("ATR_COIL");   pts+=10
    if vol_building_signal(candles_1h):     sigs.append("VOL_BUILD");  pts+=8
    if higher_lows_signal(candles_1h):      sigs.append("HIGHR_LOWS"); pts+=7
    return pts, sigs


# ── divergence (A15) ─────────────────────────────────────────────────────────

def find_pivots(series, left=3, right=3):
    """Return (highs, lows) as list of (index, value) tuples."""
    highs, lows = [], []
    for i in range(left, len(series)-right):
        v = series[i]
        if v is None: continue
        nb = [series[i-j] for j in range(1,left+1)] + [series[i+j] for j in range(1,right+1)]
        if any(x is None for x in nb): continue
        if all(v >= x for x in nb): highs.append((i, v))
        if all(v <= x for x in nb): lows.append((i, v))
    return highs, lows

def rsi_divergence(candles, rsi_vals, left=3, right=3):
    """
    Detect classic RSI divergence on last 60 bars.
    Bullish: price lower low + RSI higher low  → +12 pts
    Bearish: price higher high + RSI lower high → -10 pts
    Returns (type_str, score_adj)
    """
    n = min(60, len(candles))
    closes = [c["c"] for c in candles[-n:]]
    rsi_w  = [v if v is not None else 50.0 for v in rsi_vals[-n:]]

    p_highs, p_lows = find_pivots(closes, left, right)
    r_highs, r_lows = find_pivots(rsi_w,  left, right)

    if len(p_lows) >= 2 and len(r_lows) >= 2:
        if (p_lows[-1][1]  < p_lows[-2][1]  * 0.995 and
                r_lows[-1][1] > r_lows[-2][1] * 1.005):
            return 'bullish', 12

    if len(p_highs) >= 2 and len(r_highs) >= 2:
        if (p_highs[-1][1]  > p_highs[-2][1]  * 1.005 and
                r_highs[-1][1] < r_highs[-2][1] * 0.995):
            return 'bearish', -10

    return None, 0


# ── scoring ───────────────────────────────────────────────────────────────────

def get_last(arr):
    return next((v for v in reversed(arr) if v is not None), None)

def score_tf(candles, label):
    """Score a single timeframe. Returns (score, meta_dict)."""
    if len(candles) < 60:
        return 0, {}

    closes=[c["c"] for c in candles]
    price=closes[-1]
    sc=0
    meta={}

    # 1. SuperTrend (10,3) — max 20
    st,up,dn=supertrend_series(candles,10,3)
    st_now=get_last(st)
    meta["st"]=st_now
    if st_now==1: sc+=20

    # 2. EMA stack 20/50/200 — max 15
    e20=get_last(ema(closes,20)); e50=get_last(ema(closes,50))
    e200=get_last(ema(closes,200))
    if e20 and e50 and e200:
        if price>e20>e50>e200: sc+=15
        elif price>e20>e50:    sc+=10
        elif price>e50:        sc+=5

    # 3. ARVA RSI — max 15
    rv,_,__=arva_rsi(closes)
    rsi_now=get_last(rv)
    meta["rsi"]=rsi_now
    if rsi_now:
        if 40<=rsi_now<=65:   sc+=15
        elif 65<rsi_now<=72:  sc+=8
        elif rsi_now>72:      sc+=2
        elif rsi_now<30:      sc+=8

    # 4. MACD — max 17
    ml,sl_,hl=macd_series(closes)
    macd_now=get_last(ml); sig_now=get_last(sl_); hist_now=get_last(hl)
    if macd_now is not None and sig_now is not None:
        if macd_now>sig_now:  sc+=12
        if hist_now and hist_now>0: sc+=5

    # 5. ADX + DI — max 15
    adx_,pdi_,ndi_=adx_series(candles,14)
    adx_now=get_last(adx_); pdi_now=get_last(pdi_); ndi_now=get_last(ndi_)
    meta["pdi"]=pdi_now; meta["ndi"]=ndi_now; meta["adx"]=adx_now
    if pdi_now and ndi_now and pdi_now>ndi_now:
        sc+=10
        if adx_now and adx_now>25: sc+=5

    # 6. Bollinger Bands — max 8
    bm,bu,bl=bollinger(closes,20,2)
    bm_n=get_last(bm); bu_n=get_last(bu); bl_n=get_last(bl)
    bb_pct=None
    if bm_n and bu_n and bl_n and bu_n!=bl_n:
        bb_pct=(price-bl_n)/(bu_n-bl_n)
        if 0.3<=bb_pct<=0.7:  sc+=8
        elif bb_pct<0.2:      sc+=5
    meta["bb_pct"]=bb_pct

    # 7. Aroon (25) — max 10
    ar_up_s,ar_dn_s=aroon(candles,25)
    ar_up=get_last(ar_up_s); ar_dn=get_last(ar_dn_s)
    meta["aroon_up"]=ar_up; meta["aroon_dn"]=ar_dn
    if ar_up and ar_dn:
        if ar_up>70 and ar_dn<30: sc+=10
        elif ar_up>ar_dn:         sc+=5

    # 8. ATR Trailing Stop — max 8
    _,ats_bull=atr_trailing_stop(candles,14,3)
    ats_now=get_last(ats_bull)
    if ats_now: sc+=8

    # 9. B-Trend % — max 7
    bt=b_trend_pct(candles)
    bt_now=get_last(bt)
    if bt_now and bt_now>0: sc+=7

    # 10. Percentile SuperTrend — max 5
    pst=percentile_supertrend(candles)
    pst_now=get_last(pst)
    if pst_now==1: sc+=5

    # 11. Stochastic RSI — max 15
    k_line,d_line=stoch_rsi_series(closes)
    k_now=get_last(k_line); d_now=get_last(d_line)
    meta["stoch_k"]=k_now; meta["stoch_d"]=d_now
    if k_now is not None and d_now is not None:
        if k_now>d_now and k_now<80: sc+=15   # bullish cross, not overbought
        elif k_now>d_now:            sc+=7    # bullish but extended
        elif k_now<20:               sc+=5    # oversold bounce

    # 12. Ichimoku Cloud — max 15
    tenkan,kijun,senkou_a,senkou_b=ichimoku(candles)
    tk=get_last(tenkan); kj=get_last(kijun)
    sa=get_last(senkou_a); sb=get_last(senkou_b)
    if tk and kj and sa and sb:
        cloud_bull=sa>sb        # green cloud
        above_cloud=price>max(sa,sb)
        tk_kj_bull=tk>kj
        meta["ichi_cloud"]="bull" if cloud_bull else "bear"
        if above_cloud and cloud_bull and tk_kj_bull: sc+=15
        elif above_cloud and cloud_bull:              sc+=10
        elif above_cloud:                             sc+=5
        elif tk_kj_bull and cloud_bull:               sc+=5

    # 13. CMF — max 10
    cmf=cmf_series(candles,20)
    cmf_now=get_last(cmf)
    meta["cmf"]=cmf_now
    if cmf_now is not None:
        if cmf_now>0.1:   sc+=10
        elif cmf_now>0:   sc+=6
        elif cmf_now>-0.05: sc+=2

    # 14. OBV trend (slope over last 10 bars) — max 10
    obv=obv_series(candles)
    if len(obv)>=10:
        obv_slope=obv[-1]-obv[-10]
        if obv_slope>0: sc+=10

    # 15. MFI — max 10
    mfi=mfi_series(candles,14)
    mfi_now=get_last(mfi)
    meta["mfi"]=mfi_now
    if mfi_now is not None:
        if 40<=mfi_now<=70:   sc+=10
        elif mfi_now>70:      sc+=4
        elif mfi_now<30:      sc+=6  # oversold

    # 16. CCI — max 8
    cci=cci_series(candles,20)
    cci_now=get_last(cci)
    meta["cci"]=cci_now
    if cci_now is not None:
        if 0<cci_now<=100:    sc+=8
        elif cci_now>100:     sc+=3
        elif -100<=cci_now<0: sc+=2

    # 17. Hull MA — max 8
    hma=hull_ma(closes,20)
    hma_now=get_last(hma)
    prev_hma=next((v for v in reversed(hma[:-1]) if v is not None), None)
    if hma_now and prev_hma:
        if price>hma_now and hma_now>prev_hma: sc+=8  # price above rising HMA
        elif price>hma_now:                    sc+=4

    # 18. Parabolic SAR — max 7
    _,psar_bull=parabolic_sar(candles)
    psar_now=get_last(psar_bull)
    if psar_now: sc+=7

    # 19. Volume Surge — max 8 (A12: confirms breakout with elevated volume)
    vols=[c["v"] for c in candles]
    vsma=sma(vols,20); vsma_now=get_last(vsma)
    vol_ratio=None
    if vsma_now and vsma_now>0 and candles:
        vol_ratio=candles[-1]["v"]/vsma_now
        if vol_ratio>1.5:   sc+=8
        elif vol_ratio>1.2: sc+=4
    meta["vol_ratio"]=round(vol_ratio,2) if vol_ratio is not None else None

    return sc, meta


def score_token(candles_by_tf):
    """candles_by_tf: dict label -> candles list"""
    tf_scores  = {}
    tf_metas   = {}
    total      = 0.0

    for label, interval, weight in TIMEFRAMES:
        candles = candles_by_tf.get(label, [])
        sc, meta = score_tf(candles, label)
        tf_scores[label] = sc
        tf_metas[label]  = meta
        total += sc * weight

    # Collect 1D meta for trade plan + display
    m1d = tf_metas.get("1D", {})
    c1d = candles_by_tf.get("1D", [])
    c1h = candles_by_tf.get("1H", [])

    def _safe_atr(c): return get_last(atr_series(c,14)) or 0 if len(c)>=15 else 0
    def _safe_st(c):  return get_last(supertrend_series(c,10,3)[0]) if len(c)>=55 else None

    st_counts = sum(
        1 for lbl,_,__ in TIMEFRAMES
        if tf_metas.get(lbl,{}).get("st")==1
    )

    # A15: RSI divergence on 1D (genuinely different signal class)
    rsi_div_type = None
    if len(c1d) >= 20:
        closes_div = [c["c"] for c in c1d]
        rsi_div_full = rsi_series(closes_div, 14)
        rsi_div_type, div_adj = rsi_divergence(c1d, rsi_div_full)
        total += div_adj

    # A16: Pre-pump signals on 1H (detect accumulation before breakout)
    pp_bonus, pp_sigs = prepump_bonus(c1h)
    total += pp_bonus

    info = {
        "tf_scores":  tf_scores,
        "tf_metas":   tf_metas,
        "price":      c1d[-1]["c"] if c1d else 0,
        "atr_1d":     _safe_atr(c1d),
        "atr_1h":     _safe_atr(c1h),
        "pdi_1d":     m1d.get("pdi"),
        "ndi_1d":     m1d.get("ndi"),
        "rsi_1d":     m1d.get("rsi"),
        "rsi_1h":     tf_metas.get("1H",{}).get("rsi"),
        "cmf_1d":     m1d.get("cmf"),
        "mfi_1d":     m1d.get("mfi"),
        "cci_1d":     m1d.get("cci"),
        "stoch_k_1d": m1d.get("stoch_k"),
        "ichi_1d":    m1d.get("ichi_cloud"),
        "aroon_up":   m1d.get("aroon_up"),
        "st_confluent": st_counts,
        "rsi_div":    rsi_div_type,
        "prepump_signals": pp_sigs,
        "prepump_bonus":   pp_bonus,
    }
    return total, info


# ── trade plan ────────────────────────────────────────────────────────────────

def trade_plan(price, atr_1d, atr_1h):
    sl   = max(price - 1.5*atr_1d, price*0.88)
    sl   = min(sl, price*0.97)
    risk = price - sl
    if risk <= 0: return {}
    qty  = (ACCOUNT_USDT * RISK_PCT) / risk
    size = min(qty * price, 20_000)
    tp1  = price + 1.5*atr_1d
    tp2  = price + 3.0*atr_1d
    tp3  = price + 5.0*atr_1d
    rr   = (tp2-price)/risk
    return {
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "size": size, "rr": rr,
        "sl_pct":  (price-sl)/price*100,
        "tp1_pct": (tp1-price)/price*100,
        "tp2_pct": (tp2-price)/price*100,
        "tp3_pct": (tp3-price)/price*100,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Enhanced Scan — 18 indicators × 6 TFs")
    parser.add_argument("--min-vol", type=float, default=1_000_000,
                        help="Min 24h USDT turnover to include (default: 1,000,000)")
    parser.add_argument("--strong-only", action="store_true",
                        help="Print only STRONG signals (faster output)")
    args = parser.parse_args()

    THRESH_STRONG = 120
    THRESH_LONG   = 90
    THRESH_WATCH  = 65
    ST_STRONG     = 5
    ST_LONG       = 3

    print("="*76)
    print("  ENHANCED SCAN — 19 indicators × 6 timeframes (1H/2H/4H/6H/12H/1D)")
    print("  SuperTrend | EMA | RSI | MACD | ADX | BB | Aroon | ATS | B-Trend |")
    print("  PctST | StochRSI | Ichimoku | CMF | OBV | MFI | CCI | HullMA | PSAR | VolSurge")
    print("  A11: 1D RSI<78 gate  |  A12: Vol Surge  |  A13: RS vs BTC  |  A14: Funding Rate  |  A15: RSI Divergence  |  A16: Pre-Pump")
    print("="*76)
    print(f"\n  Fetching Bybit perp list (min 24h vol ${args.min_vol:,.0f})...")

    CANDIDATES, FUNDING = fetch_all_perps(args.min_vol)
    eta_min = len(CANDIDATES) * 6 * 0.15 / 60
    print(f"  {len(CANDIDATES)} tokens loaded  |  ETA ~{eta_min:.0f}–{eta_min*1.8:.0f} min\n")

    # A13: fetch BTC 7d performance once for RS comparison
    btc_daily = fetch("BTCUSDT", "linear", "D", 20)
    btc_7d_pct = ((btc_daily[-1]["c"]-btc_daily[-8]["c"])/btc_daily[-8]["c"]*100
                  if len(btc_daily)>=8 else 0)
    print(f"  BTC 7d return: {btc_7d_pct:+.1f}%  (RS bonus for outperformers)\n")

    results = []
    for sym, cat, disp in CANDIDATES:
        print(f"  {disp:<12}", end=" ", flush=True)

        candles_by_tf = {}
        for label, interval, _ in TIMEFRAMES:
            candles_by_tf[label] = fetch(sym, cat, interval, 200)
            time.sleep(0.15)

        c1d = candles_by_tf.get("1D", [])
        if len(c1d) < 60:
            print("skip (no data)")
            continue

        total, info = score_token(candles_by_tf)

        # A13: RS vs BTC bonus (+10 if token outperforms BTC on 7d)
        c1d_rs = candles_by_tf.get("1D", [])
        rs_vs_btc = None
        if len(c1d_rs)>=8 and btc_7d_pct!=0:
            tok_7d=(c1d_rs[-1]["c"]-c1d_rs[-8]["c"])/c1d_rs[-8]["c"]*100
            rs_vs_btc=tok_7d-btc_7d_pct
            if rs_vs_btc>0: total+=10
        info["rs_vs_btc"]=rs_vs_btc

        # A14: Funding rate signal (perp-specific data, no technical indicator captures this)
        funding = FUNDING.get(sym, 0.0)
        if funding < -0.0001:        # shorts paying longs — squeeze potential
            total += 8
            funding_note = f"{funding*100:.4f}%  [SHORTS PAYING]"
        elif funding < 0:
            total += 3
            funding_note = f"{funding*100:.4f}%  [slightly neg]"
        elif funding > 0.001:        # >0.1% per 8h — overcrowded longs
            total -= 5
            funding_note = f"{funding*100:.4f}%  [!OVERCROWDED]"
        else:
            funding_note = f"{funding*100:.4f}%"
        info["funding_note"] = funding_note

        sc  = info["tf_scores"]
        st_c = info["st_confluent"]
        pdi = info.get("pdi_1d") or 0
        ndi = info.get("ndi_1d") or 0
        dmi_bull = pdi > ndi
        rsi_1d = info.get("rsi_1d") or 0

        # A11: 1D RSI > 78 = extended daily — cap at LONG regardless of other signals
        signal = "SKIP"
        if total >= THRESH_STRONG and st_c >= ST_STRONG and dmi_bull and rsi_1d < 78:
            signal = "STRONG"
        elif total >= THRESH_LONG and st_c >= ST_LONG and dmi_bull:
            signal = "LONG"
        elif total >= THRESH_WATCH and st_c >= 2:
            signal = "WATCH"

        tp = trade_plan(info["price"], info["atr_1d"], info["atr_1h"])
        results.append({
            "disp": disp, "sym": sym, "cat": cat,
            "total": total, "signal": signal,
            "info": info, "tp": tp,
        })
        print(f"ST[{st_c}/6]  "
              f"1D={sc.get('1D',0):>3} 12H={sc.get('12H',0):>3} "
              f"6H={sc.get('6H',0):>3} 4H={sc.get('4H',0):>3} "
              f"2H={sc.get('2H',0):>3} 1H={sc.get('1H',0):>3}  "
              f"total={total:>5.1f}  {signal}")

    results.sort(key=lambda x: x["total"], reverse=True)
    strong = [r for r in results if r["signal"]=="STRONG"]
    longs  = [r for r in results if r["signal"]=="LONG"]
    watch  = [r for r in results if r["signal"]=="WATCH"]
    skip   = [r for r in results if r["signal"]=="SKIP"]

    # ── STRONG ────────────────────────────────────────────────────────────────
    print()
    print("="*76)
    print(f"  STRONG ENTRIES ({len(strong)}) -- ST {ST_STRONG}+/6 TFs + DMI bull + score>={THRESH_STRONG} + 1D RSI<78")
    print("="*76)
    total_deployed = 0
    for i, r in enumerate(strong[:8], 1):
        p=r["info"]["price"]; tp=r["tp"]; inf=r["info"]
        if not tp: continue
        total_deployed += tp["size"]
        sc=inf["tf_scores"]
        rsi_1d_val=(inf.get("rsi_1d") or 0)
        rsi_warn=" [!RSI EXTENDED]" if rsi_1d_val>75 else ""
        rs=inf.get("rs_vs_btc")
        rs_str=(f"+{rs:.1f}% vs BTC" if rs and rs>0 else f"{rs:.1f}% vs BTC") if rs is not None else "—"
        div=inf.get("rsi_div")
        div_str=(" [BULLISH DIV]" if div=="bullish" else " [!BEARISH DIV]" if div=="bearish" else "—")
        print(f"""
  #{i} {r['disp']}  (score {r['total']:.1f}  |  ST [{inf['st_confluent']}/6])
     TF Scores : 1D={sc.get('1D',0)} / 12H={sc.get('12H',0)} / 6H={sc.get('6H',0)} / 4H={sc.get('4H',0)} / 2H={sc.get('2H',0)} / 1H={sc.get('1H',0)}
     Price     : {p:.6g}
     SL        : {tp['sl']:.6g}  (-{tp['sl_pct']:.1f}%)
     TP1       : {tp['tp1']:.6g}  (+{tp['tp1_pct']:.1f}%)  [take 40%]
     TP2       : {tp['tp2']:.6g}  (+{tp['tp2_pct']:.1f}%)  [take 40%]
     TP3       : {tp['tp3']:.6g}  (+{tp['tp3_pct']:.1f}%)  [trail 20%]
     Size      : ${tp['size']:,.0f}  |  R:R {tp['rr']:.1f}x
     RSI 1D    : {rsi_1d_val:.1f}{rsi_warn}   RSI 1H : {(inf.get('rsi_1h') or 0):.1f}
     RS vs BTC : {rs_str}
     Funding   : {inf.get('funding_note','—')}
     RSI Div   : {div_str}
     DMI       : +DI {(inf.get('pdi_1d') or 0):.1f} / -DI {(inf.get('ndi_1d') or 0):.1f}
     StochRSI  : K={inf.get('stoch_k_1d') or 0:.1f}
     Ichimoku  : {inf.get('ichi_1d','—')}
     CMF/MFI   : {(inf.get('cmf_1d') or 0):.3f} / {(inf.get('mfi_1d') or 0):.1f}
     CCI       : {(inf.get('cci_1d') or 0):.1f}
     Aroon     : {(inf.get('aroon_up') or 0):.0f}
     Pre-pump  : {' | '.join(inf.get('prepump_signals') or ['—'])}  [+{(inf.get('prepump_bonus') or 0):.0f}pts]""")

    print(f"\n  Total deployed : ${total_deployed:,.0f}")
    print(f"  Remaining cash : ${ACCOUNT_USDT-total_deployed:,.0f}")

    # ── LONG ──────────────────────────────────────────────────────────────────
    if args.strong_only:
        print(f"\n  (--strong-only: LONG/WATCH suppressed — {len(longs)} LONG, {len(watch)} WATCH found)")
        print("="*76)
        return

    print()
    print(f"  LONG -- {ST_LONG}+/6 TFs, DMI bull, score>={THRESH_LONG}  ({len(longs)} tokens)")
    print("  "+"-"*72)
    for r in longs[:12]:
        p=r["info"]["price"]; tp=r["tp"]
        if not tp: continue
        pp=r["info"].get("prepump_signals") or []
        pp_str=f"  [{'+'.join(pp)}]" if pp else ""
        print(f"    {r['disp']:<12} score={r['total']:.1f}  ST[{r['info']['st_confluent']}/6]  "
              f"SL-{tp['sl_pct']:.1f}%  TP2+{tp['tp2_pct']:.1f}%  "
              f"size=${tp['size']:,.0f}  "
              f"RSI:{(r['info']['rsi_1d'] or 0):.0f}  "
              f"CMF:{(r['info']['cmf_1d'] or 0):.2f}  "
              f"MFI:{(r['info']['mfi_1d'] or 0):.0f}{pp_str}")

    # ── WATCH ─────────────────────────────────────────────────────────────────
    if watch:
        print()
        print(f"  WATCH ({len(watch)}) — needs more TF alignment")
        for r in watch[:10]:
            print(f"    {r['disp']:<12} score={r['total']:.1f}  ST[{r['info']['st_confluent']}/6]  "
                  f"RSI:{(r['info']['rsi_1d'] or 0):.0f}  "
                  f"Ichi:{r['info'].get('ichi_1d','—')}")

    print()
    print(f"  SKIP ({len(skip)}): {', '.join(r['disp'] for r in skip)}")
    print("="*76)


if __name__ == "__main__":
    main()
