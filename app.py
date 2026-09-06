
import time
import requests
import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime, timezone

BASE_URL = "https://data-api.binance.vision"
S = requests.Session()
S.headers.update({"User-Agent": "BinanceMarketScannerV2/2.0"})

st.set_page_config(page_title="Futures Trade Scanner V2", layout="wide")

def api(path, params=None):
    r = S.get(BASE_URL + path, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=300)
def symbols():
    x = api("/fapi/v1/exchangeInfo")
    return [s["symbol"] for s in x["symbols"]
            if s.get("quoteAsset")=="USDT" and s.get("contractType")=="PERPETUAL"
            and s.get("status")=="TRADING"]

@st.cache_data(ttl=60)
def tickers():
    rows=[]
    for x in api("/fapi/v1/ticker/24hr"):
        try:
            rows.append((x["symbol"],float(x["quoteVolume"]),float(x["priceChangePercent"])))
        except: pass
    return pd.DataFrame(rows,columns=["symbol","qv","chg"])

def klines(sym, tf, limit=220):
    x=api("/fapi/v1/klines",{"symbol":sym,"interval":tf,"limit":limit})
    cols=["t","open","high","low","close","volume","ct","qv","n","tb","tq","i"]
    d=pd.DataFrame(x,columns=cols)
    for c in ["open","high","low","close","volume"]:
        d[c]=pd.to_numeric(d[c])
    d["t"]=pd.to_datetime(d["t"],unit="ms",utc=True)
    return d

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def rsi(s,n=14):
    z=s.diff(); g=z.clip(lower=0); l=-z.clip(upper=0)
    ag=g.ewm(alpha=1/n,adjust=False).mean()
    al=l.ewm(alpha=1/n,adjust=False).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).fillna(50)

def atr(d,n=14):
    pc=d["close"].shift(1)
    tr=pd.concat([(d["high"]-d["low"]),(d["high"]-pc).abs(),(d["low"]-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def supertrend(d,n=10,m=3.0):
    a=atr(d,n); mid=(d["high"]+d["low"])/2
    ub=mid+m*a; lb=mid-m*a
    fu=ub.copy(); fl=lb.copy()
    trend=pd.Series(1,index=d.index,dtype=int)
    line=pd.Series(index=d.index,dtype=float)
    line.iloc[0]=fl.iloc[0]
    for i in range(1,len(d)):
        fl.iloc[i]=lb.iloc[i] if (lb.iloc[i]>fl.iloc[i-1] or d["close"].iloc[i-1]<fl.iloc[i-1]) else fl.iloc[i-1]
        fu.iloc[i]=ub.iloc[i] if (ub.iloc[i]<fu.iloc[i-1] or d["close"].iloc[i-1]>fu.iloc[i-1]) else fu.iloc[i-1]
        if trend.iloc[i-1]==-1 and d["close"].iloc[i]>fu.iloc[i-1]: trend.iloc[i]=1
        elif trend.iloc[i-1]==1 and d["close"].iloc[i]<fl.iloc[i-1]: trend.iloc[i]=-1
        else: trend.iloc[i]=trend.iloc[i-1]
        line.iloc[i]=fl.iloc[i] if trend.iloc[i]==1 else fu.iloc[i]
    return line,trend

def enrich(d):
    d=d.copy()
    for n in [7,25,99]: d[f"ema{n}"]=ema(d["close"],n)
    d["rsi"]=rsi(d["close"],14)
    d["atr"]=atr(d)
    mac=ema(d["close"],12)-ema(d["close"],26)
    sig=ema(mac,9)
    d["macd"]=mac; d["macsig"]=sig; d["hist"]=mac-sig
    d["vma"]=d["volume"].rolling(20).mean()
    d["vr"]=d["volume"]/d.vma.replace(0,np.nan)
    d["st"],d["stdir"]=supertrend(d)
    return d

def direction(d):
    x = d.iloc[-1]
    bull = 0
    bear = 0

    if x["close"] > x["ema99"]:
        bull += 2
    else:
        bear += 2

    if x["ema7"] > x["ema25"] > x["ema99"]:
        bull += 3
    elif x["ema7"] < x["ema25"] < x["ema99"]:
        bear += 3
    elif x["ema7"] > x["ema25"]:
        bull += 1
    else:
        bear += 1

    if int(x["stdir"]) == 1:
        bull += 2
    else:
        bear += 2

    # IMPORTANT: Series.hist is a pandas plotting METHOD.
    # Always use x["hist"] rather than x.hist.
    if x["macd"] > x["macsig"] and x["hist"] > 0:
        bull += 2
    elif x["macd"] < x["macsig"] and x["hist"] < 0:
        bear += 2

    if 50 <= x["rsi"] <= 70:
        bull += 1
    if 30 <= x["rsi"] < 50:
        bear += 1

    return 1 if bull - bear >= 3 else (-1 if bear - bull >= 3 else 0), bull, bear

def rejection_trigger(d, side):
    # Use the last CLOSED candle. Binance last row can be current/incomplete.
    x = d.iloc[-2]
    prev = d.iloc[-3]

    rng = max(x["high"] - x["low"], 1e-12)
    upper = x["high"] - max(x["open"], x["close"])
    lower = min(x["open"], x["close"]) - x["low"]

    if side == "LONG":
        candle = (
            (x["close"] > x["open"] and lower / rng >= 0.25)
            or (x["close"] > prev["high"] and x["close"] > x["ema7"])
        )
        momentum = x["macd"] >= x["macsig"]
    else:
        candle = (
            (x["close"] < x["open"] and upper / rng >= 0.25)
            or (x["close"] < prev["low"] and x["close"] < x["ema7"])
        )
        momentum = x["macd"] <= x["macsig"]

    return bool(candle and momentum), x

def analyze(sym, btcdir):
    frames={}
    dirs={}
    for tf in ["4h","1h","15m","5m"]:
        frames[tf]=enrich(klines(sym,tf))
        dirs[tf]=direction(frames[tf])[0]

    # Direction hierarchy. 4h and 1h must not oppose; 15m selects setup.
    long_pts=0; short_pts=0
    weights={"4h":24,"1h":26,"15m":30,"5m":12}
    for tf,w in weights.items():
        if dirs[tf]==1: long_pts+=w
        elif dirs[tf]==-1: short_pts+=w

    # BTC context only, not a veto for every alt.
    if btcdir==1: long_pts+=5
    elif btcdir==-1: short_pts+=5

    side="LONG" if long_pts>=short_pts else "SHORT"
    raw=max(long_pts,short_pts)

    # Strict higher-TF alignment
    if side=="LONG":
        aligned=(dirs["4h"]>=0 and dirs["1h"]==1 and dirs["15m"]==1)
    else:
        aligned=(dirs["4h"]<=0 and dirs["1h"]==-1 and dirs["15m"]==-1)

    d5=frames["5m"]; x=d5.iloc[-2]
    trigger,_=rejection_trigger(d5,side)
    av=float(x["atr"])
    p=float(x["close"])
    vr=float(x["vr"]) if pd.notna(x["vr"]) else 1.0

    # Penalize extreme chase conditions.
    chase=False
    if side=="LONG":
        chase=(p>x.ema7+1.0*av) or x["rsi"]>=74
    else:
        chase=(p<x.ema7-1.0*av) or x["rsi"]<=26

    # Entry around the closed 5m trigger candle / fast EMA.
    if side=="LONG":
        entry_lo=min(p,float(x["ema7"]))
        entry_hi=p+0.15*av
        stop=min(float(x["low"])-0.20*av,float(x["ema25"])-0.25*av)
        risk=max(entry_lo-stop,0.001*p)
        tp1=entry_lo+1.5*risk; tp2=entry_lo+2.5*risk
    else:
        entry_lo=p-0.15*av
        entry_hi=max(p,float(x["ema7"]))
        stop=max(float(x["high"])+0.20*av,float(x["ema25"])+0.25*av)
        risk=max(stop-entry_hi,0.001*p)
        tp1=entry_hi-1.5*risk; tp2=entry_hi-2.5*risk

    if entry_lo>entry_hi: entry_lo,entry_hi=entry_hi,entry_lo

    # Validity: trigger candle closes, then allow next 3 x 5m candles (~15 min).
    trigger_close=x["t"] + pd.Timedelta(minutes=5)
    expires=trigger_close + pd.Timedelta(minutes=15)
    now=pd.Timestamp.now(tz="UTC")

    # Current live price from latest kline.
    current=float(d5.iloc[-1].close)
    in_zone=entry_lo <= current <= entry_hi
    missed = (side=="LONG" and current>entry_hi+0.35*av) or (side=="SHORT" and current<entry_lo-0.35*av)

    if not aligned:
        status="REJECT"
    elif chase:
        status="DO NOT CHASE"
    elif not trigger:
        status="WAIT 5m CLOSE"
    elif now>expires:
        status="EXPIRED"
    elif missed:
        status="MISSED"
    elif in_zone:
        status="ENTER WINDOW"
    else:
        status="READY / WAIT RETEST"

    # Quality is categorical, not claimed probability.
    q=raw
    if aligned: q+=8
    if trigger: q+=6
    if vr>=1.25: q+=4
    if chase: q-=12
    if q>=88 and aligned and trigger and not chase: grade="A+"
    elif q>=78 and aligned and not chase: grade="A"
    elif q>=68: grade="B"
    else: grade="C"

    # Risk distance warning for leverage.
    risk_pct=abs((stop-((entry_lo+entry_hi)/2))/((entry_lo+entry_hi)/2))*100
    lev_note="HIGH RISK @20x" if risk_pct>=2.0 else ("CAUTION @20x" if risk_pct>=1.0 else "TIGHT STRUCTURE")

    reason=f'4H {dirs["4h"]:+d}, 1H {dirs["1h"]:+d}, 15m {dirs["15m"]:+d}, 5m {dirs["5m"]:+d}; RSI {x["rsi"]:.1f}; Vol {vr:.2f}x'

    return {
        "Coin":sym,"Side":side,"Grade":grade,"Status":status,"Current":current,
        "Entry Low":entry_lo,"Entry High":entry_hi,"Stop":stop,"TP1":tp1,"TP2":tp2,
        "Risk %":round(risk_pct,2),"Leverage note":lev_note,
        "Trigger candle UTC":trigger_close.strftime("%H:%M"),
        "Valid until UTC":expires.strftime("%H:%M"),
        "Reason":reason
    }

def btc_direction():
    try:
        d=enrich(klines("BTCUSDT","1h"))
        return direction(d)[0]
    except: return 0

st.title("Binance Futures Trade Scanner V2")
st.caption("Multi-timeframe decision-support scanner. A+/A are setup grades, NOT win probabilities. No orders are placed.")

with st.sidebar:
    st.header("Scanner settings")
    n=st.slider("Liquid contracts in first pass",20,120,60,10)
    qvm=st.number_input("Minimum 24h volume ($M)",5.0,1000.0,20.0,5.0)
    finalists=st.slider("Full multi-timeframe analysis",10,40,20,5)
    show_grades=st.multiselect("Show grades",["A+","A","B","C"],default=["A+","A","B"])
    run=st.button("RUN MARKET SCAN",type="primary",width="stretch")


if "v2" not in st.session_state:
    st.session_state.v2 = pd.DataFrame()
if "scan_errors" not in st.session_state:
    st.session_state.scan_errors = pd.DataFrame()
if "scan_meta" not in st.session_state:
    st.session_state.scan_meta = {}

# Always show a connectivity diagnostic before scanning.
st.subheader("System check")
try:
    ping = api("/fapi/v1/ping")
    st.success("Binance Futures public API: CONNECTED")
except Exception as e:
    st.error(f"Binance Futures public API: FAILED — {type(e).__name__}: {e}")

if run or (st.session_state.v2.empty and st.session_state.scan_errors.empty):
    rows = []
    errors = []
    try:
        sy = set(symbols())
        t = tickers()

        if t.empty:
            raise RuntimeError("Binance returned no 24h ticker rows.")

        t = t[(t.symbol.isin(sy)) & (t.qv >= qvm * 1e6)]
        t = t.sort_values("qv", ascending=False).head(n)

        st.session_state.scan_meta = {
            "Eligible liquid contracts": int(len(t)),
            "Requested first pass": int(n),
            "Deep scan target": int(finalists),
        }

        if t.empty:
            raise RuntimeError(
                "No contracts passed the liquidity filter. Lower Minimum 24h volume and try again."
            )

        prelim = []
        bar = st.progress(0, text="First pass: checking 15m directional strength...")
        for i, sym in enumerate(t.symbol.tolist()):
            try:
                d = enrich(klines(sym, "15m"))
                dr, bull, bear = direction(d)
                strength = abs(bull - bear)
                prelim.append((sym, strength))
            except Exception as e:
                errors.append({
                    "Stage": "15m first pass",
                    "Coin": sym,
                    "Error": f"{type(e).__name__}: {e}"
                })
            bar.progress((i + 1) / max(len(t), 1))
        bar.empty()

        if not prelim:
            raise RuntimeError(
                "Every symbol failed during the 15m first pass. See the Error diagnostics table below."
            )

        chosen = [x[0] for x in sorted(prelim, key=lambda z: z[1], reverse=True)[:finalists]]

        bd = btc_direction()
        bar = st.progress(0, text="Deep scan: 4H → 1H → 15m → 5m...")
        for i, sym in enumerate(chosen):
            try:
                rows.append(analyze(sym, bd))
            except Exception as e:
                errors.append({
                    "Stage": "deep scan",
                    "Coin": sym,
                    "Error": f"{type(e).__name__}: {e}"
                })
            bar.progress((i + 1) / max(len(chosen), 1))
        bar.empty()

        st.session_state.v2 = pd.DataFrame(rows)
        st.session_state.scan_errors = pd.DataFrame(errors)

    except Exception as e:
        errors.append({
            "Stage": "scanner",
            "Coin": "-",
            "Error": f"{type(e).__name__}: {e}"
        })
        st.session_state.v2 = pd.DataFrame(rows)
        st.session_state.scan_errors = pd.DataFrame(errors)

meta = st.session_state.scan_meta
if meta:
    m1, m2, m3 = st.columns(3)
    m1.metric("Eligible liquid contracts", meta.get("Eligible liquid contracts", 0))
    m2.metric("First-pass limit", meta.get("Requested first pass", 0))
    m3.metric("Deep-scan target", meta.get("Deep scan target", 0))

all_results = st.session_state.v2.copy()
errs = st.session_state.scan_errors.copy()

if not all_results.empty:
    # Always show ALL analyzed rows in a diagnostic tab, regardless of grade filters.
    filtered = all_results[all_results.Grade.isin(show_grades)].copy()

    priority = {
        "ENTER WINDOW": 0,
        "READY / WAIT RETEST": 1,
        "WAIT 5m CLOSE": 2,
        "DO NOT CHASE": 3,
        "MISSED": 4,
        "EXPIRED": 5,
        "REJECT": 6
    }
    grade_order = {"A+": 0, "A": 1, "B": 2, "C": 3}

    for frame in [filtered, all_results]:
        if not frame.empty:
            frame["_p"] = frame.Status.map(priority).fillna(9)
            frame["_g"] = frame.Grade.map(grade_order).fillna(9)

    if not filtered.empty:
        filtered = filtered.sort_values(["_p", "_g"]).drop(columns=["_p", "_g"])
    if not all_results.empty:
        all_results = all_results.sort_values(["_p", "_g"]).drop(columns=["_p", "_g"])

    c1, c2, c3 = st.columns(3)
    c1.metric("ENTER WINDOW", int((all_results.Status == "ENTER WINDOW").sum()))
    c2.metric("A+ / A setups", int(all_results.Grade.isin(["A+", "A"]).sum()))
    c3.metric("Successfully analyzed", len(all_results))

    tab1, tab2, tab3 = st.tabs(["Action board", "All analyzed", "Errors / diagnostics"])

    with tab1:
        st.subheader("Action board")
        if filtered.empty:
            st.info(
                "The scanner worked, but none of the successfully analyzed coins match the selected grade filter. "
                "Select A+, A, B and C to inspect everything."
            )
        else:
            st.dataframe(filtered, width="stretch", hide_index=True)

    with tab2:
        st.subheader("All successfully analyzed coins")
        st.dataframe(all_results, width="stretch", hide_index=True)

    with tab3:
        if errs.empty:
            st.success("No per-symbol scan errors.")
        else:
            st.warning(f"{len(errs)} scan error(s) occurred.")
            st.dataframe(errs, width="stretch", hide_index=True)

else:
    st.error("No coins were successfully analyzed.")
    if not errs.empty:
        st.subheader("Error diagnostics")
        st.dataframe(errs, width="stretch", hide_index=True)
        first_err = str(errs.iloc[0]["Error"])
        if "429" in first_err:
            st.warning("Binance rate limit was reached. Reduce the first-pass contracts to 20–30 and scan again.")
        elif "451" in first_err:
            st.warning("Binance Futures public API appears unavailable from this network/region.")
        elif "Connection" in first_err or "Timeout" in first_err:
            st.warning("This looks like a network/API connectivity problem rather than a trading-strategy problem.")
    else:
        st.info("Press RUN MARKET SCAN.")

st.subheader("Signal timing rules")
st.markdown("""
- **4H:** market regime.
- **1H:** primary direction.
- **15m:** setup formation.
- **5m:** actual entry confirmation.
- The scanner uses the **last fully closed 5-minute candle** for the trigger.
- A confirmed trigger remains actionable for roughly the **next 15 minutes**, but only while price remains close to the calculated entry zone.
- **WAIT 5m CLOSE** means do not enter yet.
- **ENTER WINDOW** is the only status intended to indicate that the entry conditions are currently satisfied.
- **MISSED / EXPIRED / DO NOT CHASE / REJECT** means no new trade entry.
""")

st.warning(
    "A+/A are quality grades, not win probabilities. No indicator combination can provide near-100% win probability, "
    "especially with leveraged crypto futures."
)
