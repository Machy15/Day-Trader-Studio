import os
import sys
import re
import json
import time
import math
import subprocess
import threading
import urllib.request
import urllib.parse
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
import tkinter as tk
from tkinter import ttk, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NY_TZ = ZoneInfo("America/New_York")
CET_TZ = ZoneInfo("Europe/Prague")

# Short (prodej na krátko) jde u XTB jen přes CFD, ne přes reálné akcie.
#   False = u bearish scénáře se negeneruje žádný pokyn ("VYHNOUT SE LONGU").
#   True  = navrhne se i short pokyn (platí jen pro CFD obchodování).
ALLOW_SHORT_CFD = False

# Maximální vzdálenost Stop Lossu od vstupu v násobcích ATR(14) (všechny scénáře stejně).
MAX_SL_ATR = 0.35

# Popisky setupů z trader-scanner (scan.py) -> česky
SETUP_LABELS = {
    "breakout_20d": "průraz 20denního maxima (close)",
    "bull_flag": "bull flag",
    "reclaim_ma20": "návrat nad 20denní průměr",
    "oversold": "přeprodáno (RSI < 30)",
    "oversold_bounce": "odraz z přeprodanosti",
    "orb": "opening range breakout",
}


def first_valid(*values):
    """Vrátí první hodnotu, která není None a není prázdný řetězec (zachová 0.0)."""
    for v in values:
        if v is not None and v != "":
            return v
    return None


def round_tick(price):
    """Zaokrouhlí cenu na platný burzovní tick ($0.01 pro ceny >= $1, $0.0001 pro < $1)."""
    if price is None or math.isnan(price):
        return None
    return round(float(price), 2 if abs(price) >= 1.0 else 4)


def fmt_usd(val, default="N/A"):
    """Bezpečně zformátuje dolarovou částku bez nutnosti vnořených f-stringů."""
    if val is None:
        return default
    try:
        return f"${float(val):.2f}"
    except Exception:
        return default


def fmt_pct(val, default="N/A"):
    """Procento se znaménkem; chybějící hodnota je N/A (ne +0.00 %)."""
    if val is None:
        return default
    try:
        return f"{float(val):+.2f}%"
    except Exception:
        return default


def fmt_int(val, default="N/A"):
    if val is None:
        return default
    try:
        return f"{int(round(float(val))):,}"
    except Exception:
        return default


def tm_side(entry, stop):
    """'sell' když je stop nad vstupem (short), jinak 'buy'. Time Machine jinak short odmítne."""
    try:
        return "sell" if float(stop) > float(entry) else "buy"
    except Exception:
        return None


def data_age_seconds(d):
    """Stáří živých dat podle fetched_at_ny; None u dat ze souboru."""
    try:
        txt = str(d.get("fetched_at_ny", "")).replace(" NY", "").strip()
        t = datetime.strptime(txt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY_TZ)
        return (datetime.now(NY_TZ) - t).total_seconds()
    except Exception:
        return None


def get_us_market_phase(dt_ny=None):
    """
    Určí přesnou fázi amerického trhu podle času v New Yorku.
    Vrací: (kód_fáze, český_popis)
    """
    if dt_ny is None:
        dt_ny = datetime.now(NY_TZ)
    else:
        dt_ny = dt_ny.astimezone(NY_TZ)

    if dt_ny.weekday() >= 5:
        return "CLOSED_WEEKEND", "Víkend (Trh zavřený)"

    t = dt_ny.time()
    if dtime(4, 0) <= t < dtime(9, 30):
        return "PREMARKET", "Pre-Market (04:00–09:30 NY)"
    elif dtime(9, 30) <= t < dtime(16, 0):
        return "REGULAR", "Hlavní seance otevřena (09:30–16:00 NY)"
    elif dtime(16, 0) <= t < dtime(20, 0):
        return "AFTERHOURS", "After-Hours (16:00–20:00 NY)"
    else:
        return "CLOSED_NIGHT", "Mimo obchodní hodiny (20:00–04:00 NY)"


def compute_wilder_atr(highs, lows, closes, period=14):
    """Spočítá Wilderův ATR(14) z uzavřených denních svíček."""
    trs = []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        if h is not None and l is not None and pc is not None:
            tr = max(float(h) - float(l), abs(float(h) - float(pc)), abs(float(l) - float(pc)))
            trs.append(tr)
    if not trs:
        return None
    if len(trs) <= period:
        return sum(trs) / len(trs)

    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def fetch_yahoo_market_snapshot(symbol):
    """
    Stáhne denní a intradenní 1m data z Yahoo Finance a striktně oddělí:
    - Včerejší uzavřený Close (prev_close) z denních svíček podle NY data
    - Pre-Market (04:00–09:30 NY): pm_price, pmh, pml, pm_vol, pm_vwap
    - Hlavní seanci (09:30–16:00 NY): reg_price, reg_high, reg_low, reg_vol, reg_vwap
    - After-Hours (16:00–20:00 NY): ah_price, ah_vol
    - Wilderův ATR(14) a 20denní průměrný objem pouze z uzavřených denních svíček
    - RVOL porovnávající stejný typ objemu (časově vážený RTH RVOL nebo Pre-Market RVOL)
    """
    sym = symbol.strip().upper()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    now_ny = datetime.now(NY_TZ)
    now_cet = datetime.now(CET_TZ)
    phase_code, phase_label = get_us_market_phase(now_ny)

    out = {
        "symbol": sym,
        "fetched_at_ny": now_ny.strftime("%Y-%m-%d %H:%M:%S NY"),
        "fetched_at_cet": now_cet.strftime("%Y-%m-%d %H:%M:%S CET"),
        "market_phase": phase_code,
        "market_phase_label": phase_label,
        "source": "Yahoo_Live_v8",
        "prev_close": None,
        "prev_close_date": None,
        "daily_last_date": None,
        "daily_last_is_today_open": False,
        "atr": None,
        "avg_daily_vol": None,
        "pm_price": None,
        "pmh": None,
        "pml": None,
        "pm_vol": 0,
        "pm_vwap": None,
        "reg_price": None,
        "reg_open": None,
        "reg_high": None,
        "reg_low": None,
        "reg_vol": 0,
        "reg_vwap": None,
        "ah_price": None,
        "ah_vol": 0,
        "session_date": None,
        "stale_session": False,
        "active_price": None,
        "active_price_type": None,
        "vwap": None,
        "vwap_type": None,
        "gap_pct": None,
        "gap_reference": None,
        "rvol": None,
        "rvol_type": None,
        "pm_vol_ratio_est": None,
        "prediction": "Čeká na analýzu",
        "warnings": [],
        "raw_meta_debug": {}
    }

    # 1. Denní svíčky (range=2mo&interval=1d)
    url_daily = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
        f"?interval=1d&range=2mo&_ts={int(time.time()*1000)}"
    )
    req1 = urllib.request.Request(url_daily, headers=headers)
    with urllib.request.urlopen(req1, timeout=10) as resp:
        d_json = json.loads(resp.read().decode("utf-8"))

    res1 = d_json.get("chart", {}).get("result", [])
    daily_bars = []
    if res1:
        meta1 = res1[0].get("meta", {})
        out["raw_meta_debug"]["daily_meta_chartPreviousClose"] = meta1.get("chartPreviousClose")
        out["raw_meta_debug"]["daily_meta_regularMarketPrice"] = meta1.get("regularMarketPrice")

        ts_list = res1[0].get("timestamp", [])
        q1 = res1[0].get("indicators", {}).get("quote", [{}])[0]
        opens = q1.get("open", [])
        highs = q1.get("high", [])
        lows = q1.get("low", [])
        closes = q1.get("close", [])
        vols = q1.get("volume", [])

        for ts, o, h, l, c, v in zip(ts_list, opens, highs, lows, closes, vols):
            if c is None or h is None or l is None:
                continue
            dt_ny = datetime.fromtimestamp(ts, tz=NY_TZ)
            daily_bars.append({
                "date": dt_ny.strftime("%Y-%m-%d"),
                "open": float(o) if o is not None else float(c),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": int(v) if (v is not None and v > 0) else 0
            })

    # 2. Intradenní 1m svíčky (range=5d&interval=1m&includePrePost=true)
    url_intra = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
        f"?interval=1m&range=5d&includePrePost=true&_ts={int(time.time()*1000)}"
    )
    req2 = urllib.request.Request(url_intra, headers=headers)
    with urllib.request.urlopen(req2, timeout=10) as resp:
        i_json = json.loads(resp.read().decode("utf-8"))

    res2 = i_json.get("chart", {}).get("result", [])
    intra_by_ny_date = {}

    if res2:
        ts2 = res2[0].get("timestamp", [])
        q2 = res2[0].get("indicators", {}).get("quote", [{}])[0]
        io = q2.get("open", [])
        ih = q2.get("high", [])
        il = q2.get("low", [])
        ic = q2.get("close", [])
        iv = q2.get("volume", [])

        for ts, o, h, l, c, v in zip(ts2, io, ih, il, ic, iv):
            if c is None:
                continue
            dt_ny = datetime.fromtimestamp(ts, tz=NY_TZ)
            d_str = dt_ny.strftime("%Y-%m-%d")
            t_ny = dt_ny.time()
            intra_by_ny_date.setdefault(d_str, []).append({
                "dt_ny": dt_ny,
                "time_obj": t_ny,
                "time_str": dt_ny.strftime("%H:%M"),
                "open": float(o) if o is not None else float(c),
                "high": float(h) if h is not None else float(c),
                "low": float(l) if l is not None else float(c),
                "close": float(c),
                "volume": int(v) if (v is not None and v >= 0) else 0
            })

    ny_dates_sorted = sorted(intra_by_ny_date.keys())
    if ny_dates_sorted:
        session_date = ny_dates_sorted[-1]
    elif daily_bars:
        session_date = daily_bars[-1]["date"]
    else:
        session_date = now_ny.strftime("%Y-%m-%d")
    out["session_date"] = session_date

    # Dnešní seance musí mít data, jinak by se včerejší/starší data tvářila jako živá
    # (svátek, nebo ticker dnes v premarketu ještě neobchodoval).
    today_ny_expected = now_ny.strftime("%Y-%m-%d")
    if phase_code in ("PREMARKET", "REGULAR", "AFTERHOURS") and session_date != today_ny_expected:
        out["stale_session"] = True
        out["warnings"].append(
            f"Dnešní seance ({today_ny_expected}) nemá v Yahoo žádné obchody (svátek, nebo ticker dnes "
            f"ještě neobchodoval). Zobrazená data jsou ze seance {session_date}."
        )

    # 3. Určení správného prev_close, ATR(14) a avg_vol z uzavřených dnů
    if daily_bars:
        out["daily_last_date"] = daily_bars[-1]["date"]
        closed_before_session = [b for b in daily_bars if b["date"] < session_date]

        if closed_before_session:
            prev_bar = closed_before_session[-1]
            out["prev_close"] = prev_bar["close"]
            out["prev_close_date"] = prev_bar["date"]
        elif len(daily_bars) >= 2:
            prev_bar = daily_bars[-2]
            out["prev_close"] = prev_bar["close"]
            out["prev_close_date"] = prev_bar["date"]
        else:
            out["prev_close"] = daily_bars[-1]["close"]
            out["prev_close_date"] = daily_bars[-1]["date"]
            out["warnings"].append("Nedostatek historie pro oddělení včerejšího Close.")

        today_ny_str = now_ny.strftime("%Y-%m-%d")
        is_last_bar_unclosed_today = (
            daily_bars[-1]["date"] == today_ny_str and phase_code in ("PREMARKET", "REGULAR")
        )
        out["daily_last_is_today_open"] = is_last_bar_unclosed_today

        completed_daily = daily_bars[:-1] if is_last_bar_unclosed_today else daily_bars
        if len(completed_daily) >= 2:
            c_highs = [b["high"] for b in completed_daily]
            c_lows = [b["low"] for b in completed_daily]
            c_closes = [b["close"] for b in completed_daily]
            out["atr"] = compute_wilder_atr(c_highs, c_lows, c_closes, period=14)

            valid_vols = [b["volume"] for b in completed_daily[-20:] if b["volume"] > 0]
            if valid_vols:
                out["avg_daily_vol"] = sum(valid_vols) / len(valid_vols)

    # 4. Rozdělení intradenních 1m svíček na Pre-Market, RTH a After-Hours
    day_1m = intra_by_ny_date.get(session_date, [])
    pm_bars = []
    reg_bars = []
    ah_bars = []

    for b in day_1m:
        t = b["time_obj"]
        if dtime(4, 0) <= t < dtime(9, 30):
            pm_bars.append(b)
        elif dtime(9, 30) <= t < dtime(16, 0):
            reg_bars.append(b)
        elif dtime(16, 0) <= t <= dtime(20, 0):
            ah_bars.append(b)

    if pm_bars:
        out["pm_price"] = pm_bars[-1]["close"]
        out["pmh"] = max(b["high"] for b in pm_bars)
        out["pml"] = min(b["low"] for b in pm_bars)
        out["pm_vol"] = sum(b["volume"] for b in pm_bars)
        pv_num = sum(((b["high"] + b["low"] + b["close"]) / 3.0) * b["volume"] for b in pm_bars if b["volume"] > 0)
        pv_den = sum(b["volume"] for b in pm_bars if b["volume"] > 0)
        if pv_den > 0:
            out["pm_vwap"] = pv_num / pv_den
        else:
            out["pm_vwap"] = sum((b["high"] + b["low"] + b["close"]) / 3.0 for b in pm_bars) / len(pm_bars)
            out["warnings"].append("Pre-Market 1m svíčky mají nulový objem v Yahoo feedu (PM VWAP spočítán jako TWAP).")

    if reg_bars:
        out["reg_open"] = reg_bars[0]["open"]
        out["reg_price"] = reg_bars[-1]["close"]
        out["reg_high"] = max(b["high"] for b in reg_bars)
        out["reg_low"] = min(b["low"] for b in reg_bars)
        out["reg_vol"] = sum(b["volume"] for b in reg_bars)
        rv_num = sum(((b["high"] + b["low"] + b["close"]) / 3.0) * b["volume"] for b in reg_bars if b["volume"] > 0)
        rv_den = sum(b["volume"] for b in reg_bars if b["volume"] > 0)
        if rv_den > 0:
            out["reg_vwap"] = rv_num / rv_den

    if ah_bars:
        out["ah_price"] = ah_bars[-1]["close"]
        out["ah_vol"] = sum(b["volume"] for b in ah_bars)

    # 5. Volba aktivní ceny, VWAPu, Gapu a RVOL
    pc = out["prev_close"]

    if phase_code == "PREMARKET" and out["pm_price"] is not None:
        out["active_price"] = out["pm_price"]
        out["active_price_type"] = "Pre-Market Last"
        out["vwap"] = out["pm_vwap"]
        out["vwap_type"] = "Pre-Market VWAP (04:00–09:30 NY)"
        if pc and pc > 0:
            out["gap_pct"] = ((out["pm_price"] - pc) / pc) * 100.0
            out["gap_reference"] = f"PM cena vs. Close ({out['prev_close_date']})"
    elif reg_bars:
        out["active_price"] = out["reg_price"]
        out["active_price_type"] = "RTH Regular Session Last" if phase_code == "REGULAR" else "RTH Regular Close (16:00 NY)"
        out["vwap"] = out["reg_vwap"]
        out["vwap_type"] = "RTH Session VWAP (09:30–16:00 NY)"
        if pc and pc > 0:
            ref_open = out["pm_price"] if (phase_code == "PREMARKET" and out["pm_price"]) else out["reg_open"]
            out["gap_pct"] = ((ref_open - pc) / pc) * 100.0 if ref_open else ((out["reg_price"] - pc) / pc) * 100.0
            ref_open_str = fmt_usd(ref_open)
            out["gap_reference"] = f"RTH Open ({ref_open_str}) vs. Prev Close ({out['prev_close_date']})"
            out["chg_from_prev_close_pct"] = ((out["reg_price"] - pc) / pc) * 100.0
    elif out["pm_price"] is not None:
        out["active_price"] = out["pm_price"]
        out["active_price_type"] = "Pre-Market Last"
        out["vwap"] = out["pm_vwap"]
        out["vwap_type"] = "Pre-Market VWAP (04:00–09:30 NY)"
        if pc and pc > 0:
            out["gap_pct"] = ((out["pm_price"] - pc) / pc) * 100.0
            out["gap_reference"] = f"PM cena vs. Prev Close ({out['prev_close_date']})"
    elif pc is not None:
        out["active_price"] = pc
        out["active_price_type"] = "Prev Daily Close"
        out["gap_pct"] = 0.0
        out["gap_reference"] = "Žádná nová seance"

    # Time-of-Day RVOL
    prev_ny_dates = [d for d in ny_dates_sorted if d < session_date]
    if reg_bars:
        last_reg_time = reg_bars[-1]["time_obj"]
        hist_cum_vols = []
        for pd in prev_ny_dates:
            p_bars = [
                b for b in intra_by_ny_date[pd]
                if dtime(9, 30) <= b["time_obj"] <= last_reg_time
            ]
            v_sum = sum(b["volume"] for b in p_bars)
            if v_sum > 0:
                hist_cum_vols.append(v_sum)
        if hist_cum_vols:
            avg_tod_vol = sum(hist_cum_vols) / len(hist_cum_vols)
            out["rvol"] = out["reg_vol"] / avg_tod_vol
            out["rvol_type"] = f"RTH Time-of-Day (09:30–{reg_bars[-1]['time_str']} NY vs. {len(hist_cum_vols)}d průměr)"
        elif out["avg_daily_vol"] and out["avg_daily_vol"] > 0:
            mins_elapsed = max(1, (last_reg_time.hour * 60 + last_reg_time.minute) - (9 * 60 + 30) + 1)
            expected_vol = out["avg_daily_vol"] * min(1.0, mins_elapsed / 390.0)
            out["rvol"] = out["reg_vol"] / max(expected_vol, 1.0)
            out["rvol_type"] = f"Odhad RTH (váženo {mins_elapsed}/390 min z 20d avg)"
    elif pm_bars:
        last_pm_time = pm_bars[-1]["time_obj"]
        hist_pm_vols = []
        for pd in prev_ny_dates:
            p_bars = [
                b for b in intra_by_ny_date[pd]
                if dtime(4, 0) <= b["time_obj"] <= last_pm_time
            ]
            v_sum = sum(b["volume"] for b in p_bars)
            if v_sum > 0:
                hist_pm_vols.append(v_sum)
        if hist_pm_vols:
            avg_pm_tod = sum(hist_pm_vols) / len(hist_pm_vols)
            out["rvol"] = out["pm_vol"] / avg_pm_tod
            out["rvol_type"] = f"Pre-Market Time-of-Day (04:00–{pm_bars[-1]['time_str']} NY vs. {len(hist_pm_vols)}d PM průměr)"
        elif out["avg_daily_vol"] and out["avg_daily_vol"] > 0:
            out["pm_vol_ratio_est"] = (out["pm_vol"] / out["avg_daily_vol"]) * 100.0
            out["rvol"] = None
            out["rvol_type"] = f"PM podíl na denním avg ({out['pm_vol_ratio_est']:.1f}% z 20d RTH avg)"

    # 6. Klasifikace scénáře (Gap & Go vs. Gap Fade vs. Trend/Chop)
    gap_v = out["gap_pct"] if out["gap_pct"] is not None else 0.0
    cur_p = out["active_price"]
    vwap_v = out["vwap"]
    rvol_v = out["rvol"] or 1.0

    ref_h = out["pmh"] if (phase_code == "PREMARKET" and out["pmh"]) else (out["reg_high"] or out["pmh"])
    ref_l = out["pml"] if (phase_code == "PREMARKET" and out["pml"]) else (out["reg_low"] or out["pml"])

    if out["gap_pct"] is None:
        out["prediction"] = "⚪ NELZE KLASIFIKOVAT (chybí Prev Close / gap)"
    elif cur_p is not None and ref_h is not None and ref_l is not None and vwap_v is not None:
        rng = max(ref_h - ref_l, 0.01)
        pos_in_rng = (cur_p - ref_l) / rng
        has_meaningful_gap = abs(gap_v) >= 1.5

        if gap_v >= 1.5 and cur_p >= vwap_v and pos_in_rng >= 0.60 and (rvol_v >= 1.2 or out["pm_vol"] >= 50000):
            out["prediction"] = f"🟢 GAP & GO (Gap {gap_v:+.2f}%, nad VWAP ${vwap_v:.2f}, drží u High ${ref_h:.2f})"
        elif gap_v >= 1.5 and (cur_p < vwap_v or pos_in_rng <= 0.40):
            out["prediction"] = f"🔴 GAP FADE (Gap {gap_v:+.2f}% selhává pod VWAP ${vwap_v:.2f}, tlak k ${ref_l:.2f})"
        elif gap_v <= -1.5 and cur_p <= vwap_v and pos_in_rng <= 0.40:
            out["prediction"] = f"🔴 GAP DOWN CONTINUATION (Gap {gap_v:+.2f}%, pod VWAP ${vwap_v:.2f}, u Low ${ref_l:.2f})"
        elif gap_v <= -1.5 and cur_p > vwap_v and pos_in_rng >= 0.55:
            out["prediction"] = f"🟢 GAP DOWN REVERSAL (Gap {gap_v:+.2f}% skoupen nad VWAP ${vwap_v:.2f})"
        elif not has_meaningful_gap:
            if cur_p >= vwap_v and pos_in_rng >= 0.65:
                out["prediction"] = f"🟢 INTRADAY TREND UP (Bez výrazného gapu {gap_v:+.2f}%, síla nad VWAP ${vwap_v:.2f})"
            elif cur_p < vwap_v and pos_in_rng <= 0.35:
                out["prediction"] = f"🔴 INTRADAY WEAKNESS (Bez výrazného gapu {gap_v:+.2f}%, pod VWAP ${vwap_v:.2f})"
            else:
                out["prediction"] = f"⚪ KONSOLIDACE / BEZ GAPU (Gap {gap_v:+.2f}%, u VWAP ${vwap_v:.2f})"
        else:
            out["prediction"] = f"⚪ KONSOLIDACE U VWAP ${vwap_v:.2f} (Gap {gap_v:+.2f}%, čeká na průraz ${ref_l:.2f}–${ref_h:.2f})"

    if out["stale_session"]:
        out["gap_pct"] = None
        out["gap_reference"] = "Dnešní seance nemá žádná data"
        out["rvol"] = None
        out["rvol_type"] = "N/A (zastaralá seance)"
        out["prediction"] = f"⚠️ BEZ DNEŠNÍ SEANCE (data jsou ze seance {session_date})"
        if out["active_price_type"]:
            out["active_price_type"] += f" – ZASTARALÉ, seance {session_date}"

    if out["pmh"] is not None and out["pml"] is not None and out["pmh"] < out["pml"]:
        out["warnings"].append(f"Nekonzistentní PMH (${out['pmh']:.2f}) < PML (${out['pml']:.2f}).")
    if out["reg_high"] is not None and out["reg_low"] is not None and out["reg_vwap"] is not None:
        if not (out["reg_low"] - 0.02 <= out["reg_vwap"] <= out["reg_high"] + 0.02):
            out["warnings"].append(
                f"RTH VWAP (${out['reg_vwap']:.2f}) je mimo RTH Low–High (${out['reg_low']:.2f}–${out['reg_high']:.2f})."
            )
    if out["gap_pct"] is not None and abs(out["gap_pct"]) > 80.0:
        out["warnings"].append(f"Extrémní hodnota Gap % ({out['gap_pct']:+.2f}%) – ověř korporátní akce/split.")

    return out


def self_check(symbol="AAPL"):
    """Diagnostická funkce pro ověření všech metrik oproti webu Yahoo Finance / TradingView."""
    d = fetch_yahoo_market_snapshot(symbol)

    prev_close_s = fmt_usd(d.get("prev_close"))
    atr_s = fmt_usd(d.get("atr"))
    avg_vol_s = f"{d['avg_daily_vol']:,.0f}" if d.get("avg_daily_vol") else "N/A"
    pm_price_s = fmt_usd(d.get("pm_price"), "Žádné obchody v PM")
    pmh_s = fmt_usd(d.get("pmh"))
    pml_s = fmt_usd(d.get("pml"))
    pm_vwap_s = fmt_usd(d.get("pm_vwap"))
    reg_open_s = fmt_usd(d.get("reg_open"), "Seance ještě neotevřela")
    reg_price_s = fmt_usd(d.get("reg_price"))
    reg_high_s = fmt_usd(d.get("reg_high"))
    reg_low_s = fmt_usd(d.get("reg_low"))
    reg_vwap_s = fmt_usd(d.get("reg_vwap"))
    ah_price_s = fmt_usd(d.get("ah_price"))
    active_price_s = fmt_usd(d.get("active_price"))
    vwap_s = fmt_usd(d.get("vwap"))
    gap_s = f"{d['gap_pct']:+.2f}%" if d.get("gap_pct") is not None else "N/A"
    rvol_s = f"{d['rvol']:.2f}x" if d.get("rvol") is not None else "N/A"
    warn_s = ", ".join(d["warnings"]) if d.get("warnings") else "Vše v pořádku (OK)"

    lines = [
        "======================================================================",
        f" SELF-CHECK DIAGNOSTIKA PRO TICKER: {d['symbol']}",
        "======================================================================",
        f"Čas stažení (NY)  : {d['fetched_at_ny']}",
        f"Čas stažení (CET) : {d['fetched_at_cet']}",
        f"Fáze trhu         : {d['market_phase']} ({d['market_phase_label']})",
        f"Cílová seance (NY): {d['session_date']}",
        "----------------------------------------------------------------------",
        "1. DENNÍ DATA A REFERENČNÍ CLOSE:",
        f"   - Včerejší uzavřený Close : {prev_close_s} (ze dne {d['prev_close_date']})",
        f"   - Yahoo chartPreviousClose: {d['raw_meta_debug'].get('daily_meta_chartPreviousClose')} (POZOR: hodnota před 2 měsíci, NEPOUŽITO!)",
        f"   - Poslední denní svíčka   : {d['daily_last_date']} (Rozpracovaná dnešní: {d['daily_last_is_today_open']})",
        f"   - Wilderův ATR(14)        : {atr_s} (spočteno pouze z uzavřených dnů)",
        f"   - 20d Průměrný RTH Objem  : {avg_vol_s} akcií",
        "----------------------------------------------------------------------",
        "2. PRE-MARKET (04:00–09:30 NY):",
        f"   - PM Poslední cena        : {pm_price_s}",
        f"   - PM High (PMH)           : {pmh_s}",
        f"   - PM Low (PML)            : {pml_s}",
        f"   - PM VWAP                 : {pm_vwap_s}",
        f"   - PM Objem                : {d['pm_vol']:,} akcií",
        "----------------------------------------------------------------------",
        "3. HLAVNÍ SEANCE RTH (09:30–16:00 NY):",
        f"   - RTH Open (09:30 NY)     : {reg_open_s}",
        f"   - RTH Poslední cena       : {reg_price_s}",
        f"   - RTH High                : {reg_high_s}",
        f"   - RTH Low                 : {reg_low_s}",
        f"   - RTH VWAP                : {reg_vwap_s}",
        f"   - RTH Objem               : {d['reg_vol']:,} akcií",
        "----------------------------------------------------------------------",
        "4. AFTER-HOURS (16:00–20:00 NY):",
        f"   - AH Poslední cena        : {ah_price_s} | AH Objem: {d['ah_vol']:,} akcií",
        "----------------------------------------------------------------------",
        "5. VÝSLEDNÉ METRIKY PRO SKENER A CLAUDE PROMPT:",
        f"   - Aktivní cena            : {active_price_s} ({d['active_price_type']})",
        f"   - Aktivní VWAP            : {vwap_s} ({d['vwap_type']})",
        f"   - Spočtený Gap %          : {gap_s} ({d['gap_reference']})",
        f"   - RVOL                    : {rvol_s} ({d['rvol_type']})",
        f"   - Klasifikace scénáře     : {d['prediction']}",
        f"   - Varování / Sanity Check : {warn_s}",
        "======================================================================"
    ]
    report = "\n".join(lines)
    print(report)
    return report


def enforce_license_check():
    """Bezpečná inicializační kontrola prostředí."""
    return True


class DayTraderMasterStudio:
    def __init__(self, root):
        self.root = root
        self.root.title("Day Trader Studio ULTRA - by Machy")
        self.root.geometry("1440x950")
        self.root.configure(bg="#0b0e14")

        self.items_map = {}
        self.selected_symbol = None
        self.chart_candles = []
        self.chart_meta = {}
        self.tm_real_stats = {}
        self.quick_filter_mode = "ALL"
        self.chart_load_id = 0

        self.visible_bars_count = 75
        self.pan_offset = 0
        self._drag_start_x = None
        self._drag_start_offset = 0

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#0b0e14", foreground="#d1d4dc")
        style.configure("TNotebook", background="#0b0e14", borderwidth=0)
        style.configure("TNotebook.Tab", background="#131722", foreground="#787b86", padding=[18, 9], font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#1e222d")], foreground=[("selected", "#ff2e93")])
        style.configure("Treeview", background="#131722", foreground="#d1d4dc", fieldbackground="#131722", rowheight=29, font=("Consolas", 10), borderwidth=0)
        style.configure("Treeview.Heading", background="#1e222d", foreground="#2962ff", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#2962ff")], foreground=[("selected", "#ffffff")])
        style.configure("Horizontal.TProgressbar", background="#ff2e93", troughcolor="#131722", bordercolor="#1e222d")

        # ----------------- HORNÍ PANEL -----------------
        top_bar = tk.Frame(root, bg="#131722", padx=16, pady=10, highlightbackground="#2a2e39", highlightthickness=1)
        top_bar.pack(fill=tk.X, padx=12, pady=(10, 4))

        tk.Label(top_bar, text="BROKER:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.broker_var = tk.StringVar(value="XTB")
        cb = ttk.Combobox(top_bar, textvariable=self.broker_var, values=["XTB", "Trading 212"], width=12, state="readonly")
        cb.pack(side=tk.LEFT, padx=(6, 14))
        cb.bind("<<ComboboxSelected>>", lambda e: self.recalc_active())

        tk.Label(top_bar, text="KAPITÁL (CZK):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.account_size = tk.Entry(top_bar, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=10, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.account_size.insert(0, "50000")
        self.account_size.pack(side=tk.LEFT, padx=(6, 14))
        self.account_size.bind("<KeyRelease>", lambda e: self.recalc_active())

        tk.Label(top_bar, text="RISK (%):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.risk_pct = tk.Entry(top_bar, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=5, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.risk_pct.insert(0, "1.0")
        self.risk_pct.pack(side=tk.LEFT, padx=(6, 14))
        self.risk_pct.bind("<KeyRelease>", lambda e: self.recalc_active())

        tk.Label(top_bar, text="USD/CZK (ČNB):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.usd_czk = tk.Entry(top_bar, bg="#0b0e14", fg="#089981", insertbackground="white", width=7, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.usd_czk.insert(0, "23.50")
        self.usd_czk.pack(side=tk.LEFT, padx=(6, 4))
        self.usd_czk.bind("<KeyRelease>", lambda e: self.recalc_active())

        self.btn_fx = tk.Button(
            top_bar, text="ČNB ↻", font=("Segoe UI", 8, "bold"),
            bg="#2a2e39", fg="#d1d4dc", activebackground="#363a45", activeforeground="white",
            padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=self.fetch_cnb_rate_async
        )
        self.btn_fx.pack(side=tk.LEFT, padx=(2, 10))

        self.fx_status_lbl = tk.Label(top_bar, text="Načítám kurz ČNB...", font=("Segoe UI", 8), fg="#787b86", bg="#131722")
        self.fx_status_lbl.pack(side=tk.LEFT, padx=(0, 12))

        tk.Button(
            top_bar, text="🔍 Self-Check & Ověření dat", font=("Segoe UI", 8, "bold"),
            bg="#2962ff", fg="white", activebackground="#1e53e5", padx=10, pady=2, relief=tk.FLAT, cursor="hand2",
            command=self.run_gui_self_check
        ).pack(side=tk.RIGHT, padx=(8, 0))

        self.auto_pm_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top_bar, text="Auto Live Yahoo (TOP 15)",
            variable=self.auto_pm_var, bg="#131722", fg="#089981", selectcolor="#0b0e14",
            activebackground="#131722", activeforeground="#089981", font=("Segoe UI", 9, "bold")
        ).pack(side=tk.RIGHT)

        # ----------------- ZÁLOŽKY -----------------
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        self.tab_scanner = tk.Frame(self.notebook, bg="#0b0e14")
        self.notebook.add(self.tab_scanner, text="  📊 1. Live Scanner & Vyhledávání Akcií  ")
        self.build_scanner_tab(self.tab_scanner)

        self.tab_timemachine = tk.Frame(self.notebook, bg="#0b0e14")
        self.notebook.add(self.tab_timemachine, text="  📈 2. Interactive Multi-TF Graf (Zoom, Pan & Skriptová Predikce)  ")
        self.build_timemachine_tab(self.tab_timemachine)

        self.root.bind("<Left>", lambda e: self.pan_chart(10))
        self.root.bind("<Right>", lambda e: self.pan_chart(-10))

        self.root.after(150, self.fetch_cnb_rate_async)
        self.root.after(300, self.autoload_existing_watchlist)

    def run_gui_self_check(self):
        sym = (self.manual_ticker_entry.get().strip() or self.selected_symbol or "AAPL").upper()

        def worker():
            try:
                rep = self_check(sym)
                self.root.after(0, lambda r=rep: self._show_self_check_dialog(sym, r))
            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda m=err: messagebox.showerror("Chyba Self-Checku", m))

        threading.Thread(target=worker, daemon=True).start()

    def _show_self_check_dialog(self, sym, report_text):
        win = tk.Toplevel(self.root)
        win.title(f"Self-Check Diagnostika Yahoo Dat – {sym}")
        win.geometry("820x620")
        win.configure(bg="#0b0e14")
        txt = tk.Text(win, bg="#06080c", fg="#089981", font=("Consolas", 10), padx=12, pady=12, relief=tk.FLAT)
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        txt.insert("1.0", report_text)
        txt.config(state=tk.DISABLED)

    def fetch_cnb_rate_async(self):
        self.fx_status_lbl.config(text="Stahuji ČNB...", fg="#2962ff")

        def worker():
            rate = None
            source_name = ""
            headers = {"User-Agent": "Mozilla/5.0"}
            try:
                cnb_url = f"https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt?_ts={int(time.time())}"
                req = urllib.request.Request(cnb_url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    lines = resp.read().decode("utf-8", errors="replace").splitlines()
                date_header = lines[0].split()[0] if lines else ""
                for line in lines:
                    parts = line.split("|")
                    if len(parts) >= 5 and parts[3].strip() == "USD":
                        qty = float(parts[2].replace(",", "."))
                        val = float(parts[4].replace(",", "."))
                        rate = val / qty
                        source_name = f"ČNB střed ({date_header})"
                        break
            except Exception:
                pass

            if rate is None:
                try:
                    y_url = f"https://query1.finance.yahoo.com/v8/finance/chart/USDCZK=X?interval=1d&range=1d&_ts={int(time.time())}"
                    req2 = urllib.request.Request(y_url, headers=headers)
                    with urllib.request.urlopen(req2, timeout=6) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    meta = data["chart"]["result"][0]["meta"]
                    rate = float(meta.get("regularMarketPrice") or meta.get("previousClose"))
                    source_name = "Live Forex (Yahoo)"
                except Exception:
                    pass

            def update_ui():
                if rate:
                    self.usd_czk.delete(0, tk.END)
                    self.usd_czk.insert(0, f"{rate:.3f}")
                    self.fx_status_lbl.config(text=f"✓ {source_name}", fg="#089981")
                    self.recalc_active()
                else:
                    self.fx_status_lbl.config(text="Offline kurz", fg="#f23645")

            self.root.after(0, update_ui)

        threading.Thread(target=worker, daemon=True).start()

    def recalc_active(self):
        if self.selected_symbol and self.selected_symbol in self.items_map:
            self.update_prediction_banner(self.selected_symbol, update_tm_entries=False)
        self.redraw_chart()

    # =========================================================================
    # ZÁLOŽKA 1: SCANNER & SEARCH BAR
    # =========================================================================
    def build_scanner_tab(self, parent):
        ctrl_frame = tk.Frame(parent, bg="#131722", padx=14, pady=8, highlightbackground="#2a2e39", highlightthickness=1)
        ctrl_frame.pack(fill=tk.X, pady=(6, 4))

        self.scan_mode_var = tk.StringVar(value="premarket")
        tk.Radiobutton(ctrl_frame, text="Pre-Market sken", variable=self.scan_mode_var, value="premarket", bg="#131722", fg="#089981", selectcolor="#0b0e14", activebackground="#131722", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(ctrl_frame, text="Denní sken", variable=self.scan_mode_var, value="daily", bg="#131722", fg="#d1d4dc", selectcolor="#0b0e14", activebackground="#131722", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(ctrl_frame, text="Vlastní tickery:", variable=self.scan_mode_var, value="custom", bg="#131722", fg="#d1d4dc", selectcolor="#0b0e14", activebackground="#131722", font=("Segoe UI", 9)).pack(side=tk.LEFT)

        self.custom_symbols_entry = tk.Entry(ctrl_frame, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=26, relief=tk.FLAT, font=("Consolas", 10), highlightbackground="#2a2e39", highlightthickness=1)
        self.custom_symbols_entry.insert(0, "GOOGL, TSLA, NVDA, AMD, AAPL, PLTR")
        self.custom_symbols_entry.pack(side=tk.LEFT, padx=(6, 12))

        self.btn_run_scan = tk.Button(
            ctrl_frame, text="▶ 1. SPUSTIT SKEN TRHU", font=("Segoe UI", 9, "bold"),
            bg="#089981", fg="white", activebackground="#0aa68c", padx=16, pady=4, relief=tk.FLAT, cursor="hand2", command=self.start_scan
        )
        self.btn_run_scan.pack(side=tk.RIGHT, padx=(6, 0))

        tk.Button(
            ctrl_frame, text="Obnovit watchlist.json", font=("Segoe UI", 9),
            bg="#2a2e39", fg="#d1d4dc", padx=10, pady=4, relief=tk.FLAT, cursor="hand2", command=self.autoload_existing_watchlist
        ).pack(side=tk.RIGHT)

        search_bar = tk.Frame(parent, bg="#181c27", padx=14, pady=8, highlightbackground="#ff2e93", highlightthickness=1)
        search_bar.pack(fill=tk.X, pady=(2, 6))

        tk.Label(search_bar, text="🔍 HLEDAT STOCK / FILTR:", font=("Segoe UI", 9, "bold"), fg="#ff2e93", bg="#181c27").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_bar, textvariable=self.search_var, bg="#0b0e14", fg="#ffffff",
            insertbackground="#ff2e93", width=22, relief=tk.FLAT, font=("Consolas", 11, "bold"),
            highlightbackground="#2a2e39", highlightthickness=1
        )
        self.search_entry.pack(side=tk.LEFT, padx=(8, 6))
        self.search_entry.bind("<KeyRelease>", lambda e: self.apply_table_filter())
        self.search_entry.bind("<Return>", lambda e: self.search_or_fetch_yahoo())

        tk.Button(
            search_bar, text="✕ Vyčistit", font=("Segoe UI", 8, "bold"),
            bg="#2a2e39", fg="#d1d4dc", padx=8, pady=3, relief=tk.FLAT, cursor="hand2",
            command=lambda: (self.search_var.set(""), self.set_quick_filter("ALL"))
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            search_bar, text="+ Stáhnout Ticker z Yahoo", font=("Segoe UI", 8, "bold"),
            bg="#ff2e93", fg="white", activebackground="#ff5ca8", padx=10, pady=3, relief=tk.FLAT, cursor="hand2",
            command=self.search_or_fetch_yahoo
        ).pack(side=tk.LEFT, padx=(0, 18))

        tk.Label(search_bar, text="Rychlý filtr:", font=("Segoe UI", 9), fg="#787b86", bg="#181c27").pack(side=tk.LEFT, padx=(4, 6))
        tk.Button(search_bar, text="Vše", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="white", padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_quick_filter("ALL")).pack(side=tk.LEFT, padx=2)
        tk.Button(search_bar, text="🟢 GAP & GO / UP", font=("Segoe UI", 8, "bold"), bg="#089981", fg="white", padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_quick_filter("BULL")).pack(side=tk.LEFT, padx=2)
        tk.Button(search_bar, text="🔴 FADE / DOWN", font=("Segoe UI", 8, "bold"), bg="#f23645", fg="white", padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_quick_filter("BEAR")).pack(side=tk.LEFT, padx=2)

        self.filter_count_lbl = tk.Label(search_bar, text="Zobrazeno: 0 akcií", font=("Consolas", 9, "bold"), fg="#d1d4dc", bg="#181c27")
        self.filter_count_lbl.pack(side=tk.RIGHT)

        self.scan_status = tk.Label(parent, text="Stav: Připraveno", font=("Segoe UI", 9, "bold"), fg="#2962ff", bg="#0b0e14", anchor="w")
        self.scan_status.pack(fill=tk.X, pady=(0, 2))

        self.scan_progress = ttk.Progressbar(parent, orient="horizontal", mode="indeterminate", style="Horizontal.TProgressbar")
        self.scan_progress.pack(fill=tk.X, pady=(0, 6))

        table_frame = tk.Frame(parent, bg="#131722", highlightbackground="#2a2e39", highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("ticker", "prev_close", "active_price", "gap", "pm_hl", "reg_hl", "vwap", "atr", "rvol", "vol_info", "setup")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)

        headers = {
            "ticker": ("Ticker", 70),
            "prev_close": ("Prev Close", 80),
            "active_price": ("Aktuální $", 85),
            "gap": ("Gap %", 75),
            "pm_hl": ("PM High/Low", 115),
            "reg_hl": ("RTH High/Low", 115),
            "vwap": ("VWAP", 80),
            "atr": ("ATR(14)", 70),
            "rvol": ("RVOL", 65),
            "vol_info": ("PM / RTH Vol", 115),
            "setup": ("Zdroj / Scénář / Pravidlo", 390)
        }
        for col, (text, w) in headers.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w" if col == "setup" else "center")

        self.tree.tag_configure("bull", foreground="#089981")
        self.tree.tag_configure("bear", foreground="#f23645")
        self.tree.tag_configure("neutral", foreground="#d1d4dc")

        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        pred_card = tk.Frame(parent, bg="#131722", padx=14, pady=10, highlightbackground="#ff2e93", highlightthickness=1)
        pred_card.pack(fill=tk.X, pady=(8, 4))

        top_pred = tk.Frame(pred_card, bg="#131722")
        top_pred.pack(fill=tk.X)

        tk.Label(top_pred, text="AKTIVNÍ TICKER:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.manual_ticker_entry = tk.Entry(top_pred, bg="#0b0e14", fg="#ff2e93", insertbackground="white", width=10, relief=tk.FLAT, font=("Consolas", 11, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.manual_ticker_entry.pack(side=tk.LEFT, padx=(6, 10))

        self.btn_live_pm = tk.Button(
            top_pred, text="⚡ Načíst / Ověřit reálná Yahoo data", font=("Segoe UI", 9, "bold"),
            bg="#ff2e93", fg="white", activebackground="#ff5ca8", padx=12, pady=4, relief=tk.FLAT, cursor="hand2", command=self.fetch_single_ticker_live
        )
        self.btn_live_pm.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            top_pred, text="📈 Otevřít v Multi-TF Grafu", font=("Segoe UI", 9, "bold"),
            bg="#2a2e39", fg="#ff77bc", activebackground="#363a45", padx=12, pady=4, relief=tk.FLAT, cursor="hand2",
            command=lambda: (self.notebook.select(self.tab_timemachine), self.load_interactive_chart(auto_reset_levels=True))
        ).pack(side=tk.LEFT)

        self.btn_copy_prompt = tk.Button(
            top_pred, text="📋 3. ZKOPÍROVAT VALIDOVANÝ PROMPT DO CLAUDE", font=("Segoe UI", 10, "bold"),
            bg="#2962ff", fg="white", activebackground="#1e53e5", padx=18, pady=5, relief=tk.FLAT, cursor="hand2", command=self.copy_comprehensive_prompt
        )
        self.btn_copy_prompt.pack(side=tk.RIGHT)

        self.pred_summary_lbl = tk.Label(
            pred_card,
            text="Klikni na akcii v tabulce – načtou se oddělená Pre-Market a RTH data v NY čase, Wilderův ATR(14) a strukturovaný pokyn.",
            font=("Consolas", 9), fg="#d1d4dc", bg="#0b0e14", padx=12, pady=8, anchor="w", justify=tk.LEFT
        )
        self.pred_summary_lbl.pack(fill=tk.X, pady=(8, 2))

        log_frame = tk.Frame(parent, bg="#0b0e14")
        log_frame.pack(fill=tk.X, pady=(2, 0))
        self.scan_log = tk.Text(log_frame, height=3, bg="#06080c", fg="#089981", font=("Consolas", 9), relief=tk.FLAT, padx=8, pady=4)
        self.scan_log.pack(fill=tk.X)

    def set_quick_filter(self, mode):
        self.quick_filter_mode = mode
        self.apply_table_filter()

    def apply_table_filter(self):
        query = self.search_var.get().strip().upper()
        for item in self.tree.get_children():
            self.tree.delete(item)

        shown = 0
        for sym, d in self.items_map.items():
            pred = d.get("prediction", "")
            if self.quick_filter_mode == "BULL" and "🟢" not in pred:
                continue
            if self.quick_filter_mode == "BEAR" and "🔴" not in pred:
                continue

            if query:
                if query not in sym and query not in pred.upper():
                    continue

            self._insert_or_update_tree_row(sym)
            shown += 1

        self.filter_count_lbl.config(text=f"Zobrazeno: {shown} / {len(self.items_map)} akcií")

    def search_or_fetch_yahoo(self):
        q = self.search_var.get().strip().upper()
        if not q:
            return
        self.manual_ticker_entry.delete(0, tk.END)
        self.manual_ticker_entry.insert(0, q)
        self.fetch_single_ticker_live()

    # =========================================================================
    # ZÁLOŽKA 2: MULTI-TIMEFRAME GRAF SE ZOOMEM, POSUNEM A SKRIPTOVOU PREDIKCÍ
    # =========================================================================
    def build_timemachine_tab(self, parent):
        ctrl = tk.Frame(parent, bg="#131722", padx=12, pady=8, highlightbackground="#2a2e39", highlightthickness=1)
        ctrl.pack(fill=tk.X, pady=(6, 4))

        r1 = tk.Frame(ctrl, bg="#131722")
        r1.pack(fill=tk.X, pady=2)

        tk.Label(r1, text="Symbol:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.tm_symbol = tk.Entry(r1, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=7, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_symbol.insert(0, "GOOGL")
        self.tm_symbol.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_symbol.bind("<Return>", lambda e: self.load_interactive_chart(auto_reset_levels=True))

        tk.Label(r1, text="TF:", font=("Segoe UI", 9, "bold"), fg="#ff2e93", bg="#131722").pack(side=tk.LEFT)
        self.tf_var = tk.StringVar(value="5m")
        self.tf_buttons = {}
        for tf_opt in ["1m", "2m", "5m", "15m", "30m", "1h"]:
            b = tk.Button(
                r1, text=tf_opt, font=("Consolas", 9, "bold"), width=3, pady=2, relief=tk.FLAT, cursor="hand2",
                command=lambda t=tf_opt: self.switch_timeframe(t)
            )
            b.pack(side=tk.LEFT, padx=1)
            self.tf_buttons[tf_opt] = b
        self._update_tf_button_styles()

        tk.Label(r1, text="Typ:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT, padx=(8, 2))
        self.chart_type_var = tk.StringVar(value="Svíčky (Candles)")
        type_cb = ttk.Combobox(r1, textvariable=self.chart_type_var, values=["Svíčky (Candles)", "Heikin-Ashi", "Čárový (Line)", "Plošný (Area)"], width=14, state="readonly", font=("Segoe UI", 9))
        type_cb.pack(side=tk.LEFT, padx=(2, 8))
        type_cb.bind("<<ComboboxSelected>>", lambda e: self.redraw_chart())

        tk.Label(r1, text="Seance:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.tm_date = ttk.Combobox(r1, width=13, state="readonly", font=("Consolas", 9, "bold"))
        self.tm_date.pack(side=tk.LEFT, padx=(4, 6))
        self.tm_date.bind("<<ComboboxSelected>>", lambda e: self.on_chart_date_change())

        self.include_pm_chart_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r1, text="Pre-Market", variable=self.include_pm_chart_var,
            bg="#131722", fg="#d1d4dc", selectcolor="#0b0e14", activebackground="#131722",
            font=("Segoe UI", 8), command=lambda: self.load_interactive_chart(auto_reset_levels=False)
        ).pack(side=tk.LEFT, padx=(2, 8))

        zoom_box = tk.Frame(r1, bg="#131722")
        zoom_box.pack(side=tk.LEFT, padx=(4, 4))

        tk.Button(zoom_box, text="◀", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.pan_chart(15)).pack(side=tk.LEFT, padx=1)
        tk.Button(zoom_box, text="▶", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.pan_chart(-15)).pack(side=tk.LEFT, padx=1)
        tk.Button(zoom_box, text="🔍+", font=("Segoe UI", 8, "bold"), bg="#2962ff", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_zoom_step(0.75)).pack(side=tk.LEFT, padx=2)
        tk.Button(zoom_box, text="🔍-", font=("Segoe UI", 8, "bold"), bg="#2962ff", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_zoom_step(1.35)).pack(side=tk.LEFT, padx=1)
        tk.Button(zoom_box, text="60 sv.", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="#089981", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_exact_zoom(60)).pack(side=tk.LEFT, padx=2)
        tk.Button(zoom_box, text="Celý den", font=("Segoe UI", 8), bg="#2a2e39", fg="#d1d4dc", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_exact_zoom(999)).pack(side=tk.LEFT, padx=1)

        self.btn_load_chart = tk.Button(
            r1, text="↻ Obnovit Graf", font=("Segoe UI", 9, "bold"),
            bg="#2962ff", fg="white", activebackground="#1e53e5", padx=10, pady=3, relief=tk.FLAT, cursor="hand2",
            command=lambda: self.load_interactive_chart(auto_reset_levels=False)
        )
        self.btn_load_chart.pack(side=tk.RIGHT)

        r2 = tk.Frame(ctrl, bg="#131722")
        r2.pack(fill=tk.X, pady=(6, 2))

        tk.Label(r2, text="Vstup ($):", font=("Segoe UI", 9, "bold"), fg="#2962ff", bg="#131722").pack(side=tk.LEFT)
        self.tm_entry = tk.Entry(r2, bg="#0b0e14", fg="#2962ff", insertbackground="white", width=8, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_entry.insert(0, "290.72")
        self.tm_entry.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_entry.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Label(r2, text="Stop Loss ($):", font=("Segoe UI", 9, "bold"), fg="#f23645", bg="#131722").pack(side=tk.LEFT)
        self.tm_stop = tk.Entry(r2, bg="#0b0e14", fg="#f23645", insertbackground="white", width=8, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_stop.insert(0, "288.50")
        self.tm_stop.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_stop.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Label(r2, text="Target ($):", font=("Segoe UI", 9, "bold"), fg="#089981", bg="#131722").pack(side=tk.LEFT)
        self.tm_target = tk.Entry(r2, bg="#0b0e14", fg="#089981", insertbackground="white", width=8, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_target.insert(0, "294.20")
        self.tm_target.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_target.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Label(r2, text="Čas svíčky (NY):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.tm_time = tk.Entry(r2, bg="#0b0e14", fg="#ff2e93", insertbackground="white", width=6, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_time.insert(0, "15:43")
        self.tm_time.pack(side=tk.LEFT, padx=(4, 8))
        self.tm_time.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Button(
            r2, text="🎯 Reset Hladin k aktuální svíčce & ATR", font=("Segoe UI", 8, "bold"),
            bg="#2a2e39", fg="#ffffff", activebackground="#363a45", padx=10, pady=3, relief=tk.FLAT, cursor="hand2",
            command=lambda: self.auto_set_levels_from_chart(redraw=True)
        ).pack(side=tk.LEFT, padx=(4, 10))

        self.show_pink_pred_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            r2, text="🩷 Růžová Predikce (VWAP + EMA9 + Skript)", variable=self.show_pink_pred_var,
            bg="#131722", fg="#ff2e93", selectcolor="#0b0e14", activebackground="#131722",
            activeforeground="#ff2e93", font=("Segoe UI", 9, "bold"), command=self.redraw_chart
        ).pack(side=tk.LEFT, padx=(2, 6))

        self.btn_run_tm = tk.Button(
            r2, text="▶ Spustit Skript TimeMachine (60d Backtest)", font=("Segoe UI", 9, "bold"),
            bg="#089981", fg="white", activebackground="#0aa68c", padx=14, pady=3, relief=tk.FLAT, cursor="hand2", command=self.start_timemachine
        )
        self.btn_run_tm.pack(side=tk.RIGHT)

        chart_bar = tk.Frame(parent, bg="#1e222d", padx=10, pady=4)
        chart_bar.pack(fill=tk.X)

        self.chart_info_lbl = tk.Label(
            chart_bar, text="Kolečko myši = ZOOM   |   Šipky ◀ ▶ (nebo tažení myší) = POSUN V ČASE",
            font=("Consolas", 9, "bold"), fg="#d1d4dc", bg="#1e222d"
        )
        self.chart_info_lbl.pack(side=tk.LEFT)

        tk.Label(
            chart_bar,
            text="[Kolečko = Zoom]  [Levé tl. = Vstup]  [Pravé tl. = SL]  [Dvojklik = TP]  |  Žlutá = VWAP, Modrá = EMA(9)",
            font=("Segoe UI", 8, "bold"), fg="#787b86", bg="#1e222d"
        ).pack(side=tk.RIGHT)

        chart_container = tk.Frame(parent, bg="#0b0e14", highlightbackground="#2a2e39", highlightthickness=1)
        chart_container.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self.chart_canvas = tk.Canvas(chart_container, bg="#0b0e14", height=435, highlightthickness=0, cursor="crosshair")
        self.chart_canvas.pack(fill=tk.BOTH, expand=True)

        self.chart_canvas.bind("<Configure>", lambda e: self.redraw_chart())
        self.chart_canvas.bind("<Motion>", self.on_chart_hover)
        self.chart_canvas.bind("<Button-1>", self.on_chart_left_click)
        self.chart_canvas.bind("<B1-Motion>", self.on_chart_drag)
        self.chart_canvas.bind("<ButtonRelease-1>", self.on_chart_release)
        self.chart_canvas.bind("<Button-3>", self.on_chart_right_click)
        self.chart_canvas.bind("<Button-2>", self.on_chart_middle_click)
        self.chart_canvas.bind("<Double-Button-1>", self.on_chart_middle_click)
        self.chart_canvas.bind("<MouseWheel>", self.on_chart_zoom)

        self.tm_progress = ttk.Progressbar(parent, orient="horizontal", mode="indeterminate", style="Horizontal.TProgressbar")
        self.tm_progress.pack(fill=tk.X, pady=(0, 4))

        tm_frame = tk.Frame(parent, bg="#0b0e14", height=135)
        tm_frame.pack(fill=tk.X)
        self.tm_log = tk.Text(tm_frame, height=6, bg="#06080c", fg="#d1d4dc", font=("Consolas", 9), relief=tk.FLAT, padx=10, pady=6)
        tm_scroll = ttk.Scrollbar(tm_frame, orient=tk.VERTICAL, command=self.tm_log.yview)
        self.tm_log.configure(yscroll=tm_scroll.set)
        self.tm_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tm_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # =========================================================================
    # FUNKCE PRO ZOOM A POSUN GRAFU (PANNING)
    # =========================================================================
    def on_chart_zoom(self, event):
        if event.delta > 0:
            self.set_zoom_step(0.80)
        else:
            self.set_zoom_step(1.25)

    def set_zoom_step(self, factor):
        total = max(len(self.chart_candles), 30)
        new_val = int(self.visible_bars_count * factor)
        if new_val == self.visible_bars_count:
            new_val += (-5 if factor < 1 else 5)
        self.visible_bars_count = max(20, min(total, new_val))
        self.redraw_chart()

    def set_exact_zoom(self, count):
        total = max(len(self.chart_candles), 20)
        self.visible_bars_count = min(total, max(20, count))
        if count >= total:
            self.pan_offset = 0
        self.redraw_chart()

    def pan_chart(self, delta_bars):
        if not self.chart_candles:
            return
        max_off = max(0, len(self.chart_candles) - 15)
        self.pan_offset = max(0, min(max_off, self.pan_offset + delta_bars))
        self.redraw_chart()

    def switch_timeframe(self, tf_val):
        self.tf_var.set(tf_val)
        self.pan_offset = 0
        default_zoom_map = {"1m": 75, "2m": 75, "5m": 78, "15m": 60, "30m": 60, "1h": 65}
        self.visible_bars_count = default_zoom_map.get(tf_val, 75)
        self._update_tf_button_styles()
        self.load_interactive_chart(auto_reset_levels=True)

    def _update_tf_button_styles(self):
        cur = self.tf_var.get()
        for tf_opt, btn in self.tf_buttons.items():
            if tf_opt == cur:
                btn.config(bg="#ff2e93", fg="#ffffff")
            else:
                btn.config(bg="#2a2e39", fg="#d1d4dc")

    # =========================================================================
    # STAHOVÁNÍ REÁLNÝCH SVÍČEK PRO MULTI-TF GRAF
    # =========================================================================
    def load_interactive_chart(self, auto_reset_levels=False):
        sym = self.tm_symbol.get().strip().upper()
        if not sym:
            return
        tf = self.tf_var.get().strip() or "5m"
        inc_pm = "true" if self.include_pm_chart_var.get() else "false"

        range_map = {
            "1m": "5d",
            "2m": "5d",
            "5m": "1mo",
            "15m": "1mo",
            "30m": "1mo",
            "1h": "3mo"
        }
        y_range = range_map.get(tf, "5d")

        self.chart_load_id += 1
        current_req_id = self.chart_load_id

        self.btn_load_chart.config(state=tk.DISABLED, bg="#2a2e39")
        self.chart_info_lbl.config(text=f"Stahuji reálný [{tf}] graf z Yahoo Finance pro {sym}...", fg="#ff2e93")

        def worker():
            try:
                url = (
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                    f"?interval={tf}&range={y_range}&includePrePost={inc_pm}&_ts={int(time.time()*1000)}"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                if current_req_id != self.chart_load_id:
                    return

                res = data.get("chart", {}).get("result", [])
                if not res:
                    raise ValueError(f"Yahoo nevrátilo žádná data pro {sym} ({tf}).")

                timestamps = res[0].get("timestamp", [])
                q = res[0].get("indicators", {}).get("quote", [{}])[0]
                opens = q.get("open", [])
                highs = q.get("high", [])
                lows = q.get("low", [])
                closes = q.get("close", [])
                vols = q.get("volume", [])

                by_date = {}
                all_bars = []
                use_pm = self.include_pm_chart_var.get()

                for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, vols):
                    if None in (o, h, l, c):
                        continue
                    dt_ny = datetime.fromtimestamp(ts, tz=NY_TZ)
                    hm = dt_ny.hour * 100 + dt_ny.minute
                    if not use_pm and (hm < 930 or hm >= 1600):
                        continue
                    d_str = dt_ny.strftime("%Y-%m-%d")
                    t_str = dt_ny.strftime("%H:%M")
                    bar_obj = {
                        "date": d_str,
                        "time": t_str,
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": int(v or 0)
                    }
                    by_date.setdefault(d_str, []).append(bar_obj)
                    all_bars.append(bar_obj)

                available_dates = sorted(by_date.keys())
                if not available_dates:
                    raise ValueError("Nebyly nalezeny žádné obchodní svíčky.")

                self.chart_meta["by_date"] = by_date
                self.chart_meta["all_bars"] = all_bars
                self.chart_meta["dates"] = available_dates
                self.chart_meta["symbol"] = sym
                self.chart_meta["tf"] = tf

                def update_ui():
                    if current_req_id != self.chart_load_id:
                        return
                    combo_vals = ["Multi-Day (Vše)"] + available_dates
                    self.tm_date["values"] = combo_vals

                    prev_sel = self.tm_date.get().strip()
                    if tf in ("30m", "1h"):
                        self.tm_date.set("Multi-Day (Vše)")
                        self.chart_candles = all_bars[-160:]
                    else:
                        target_day = prev_sel if prev_sel in by_date else available_dates[-1]
                        self.tm_date.set(target_day)
                        self.chart_candles = by_date[target_day]

                    self.pan_offset = 0
                    if self.chart_candles:
                        last_c = self.chart_candles[-1]
                        self.tm_time.delete(0, tk.END)
                        self.tm_time.insert(0, last_c["time"])

                        cur_entry = self._to_float(self.tm_entry.get())
                        if auto_reset_levels or not cur_entry or abs(cur_entry - last_c["close"]) / max(last_c["close"], 1) > 0.08:
                            self.auto_set_levels_from_chart(redraw=False)

                    self.chart_info_lbl.config(
                        text=f"✓ [{sym} | {tf}] Načteno {len(self.chart_candles)} svíček (Zobrazeno {min(self.visible_bars_count, len(self.chart_candles))} – Kolečko myši = Zoom).",
                        fg="#089981"
                    )
                    self.redraw_chart()
                    self.run_silent_script_prediction()

                self.root.after(0, update_ui)

            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda m=err: self.chart_info_lbl.config(text=f"Chyba načítání [{tf}] grafu: {m}", fg="#f23645"))
            finally:
                self.root.after(0, lambda: self.btn_load_chart.config(state=tk.NORMAL, bg="#2962ff"))

        threading.Thread(target=worker, daemon=True).start()

    def on_chart_date_change(self):
        sel = self.tm_date.get().strip()
        by_date = self.chart_meta.get("by_date", {})
        all_bars = self.chart_meta.get("all_bars", [])
        if sel == "Multi-Day (Vše)":
            self.chart_candles = all_bars[-200:]
        elif sel in by_date:
            self.chart_candles = by_date[sel]

        self.pan_offset = 0
        if self.chart_candles:
            self.tm_time.delete(0, tk.END)
            self.tm_time.insert(0, self.chart_candles[-1]["time"])
            self.auto_set_levels_from_chart(redraw=False)
        self.redraw_chart()
        self.run_silent_script_prediction()

    def auto_set_levels_from_chart(self, redraw=True):
        if not self.chart_candles:
            return
        t_target = self.tm_time.get().strip()
        anchor = self.chart_candles[-1]
        for b in self.chart_candles:
            if b["time"] == t_target:
                anchor = b
                break

        recent = self.chart_candles[-20:]
        bar_atr = sum(b["high"] - b["low"] for b in recent) / max(len(recent), 1)
        bar_atr = max(bar_atr, anchor["close"] * 0.0015)

        cur_p = anchor["close"]
        entry_p = round_tick(cur_p)
        sl_p = round_tick(cur_p - (bar_atr * 2.2))
        tp_p = round_tick(cur_p + (bar_atr * 3.8))

        self.tm_entry.delete(0, tk.END)
        self.tm_entry.insert(0, f"{entry_p:.2f}")
        self.tm_stop.delete(0, tk.END)
        self.tm_stop.insert(0, f"{sl_p:.2f}")
        self.tm_target.delete(0, tk.END)
        self.tm_target.insert(0, f"{tp_p:.2f}")

        if redraw:
            self.redraw_chart()
            self.run_silent_script_prediction()

    def run_silent_script_prediction(self):
        if not self.chart_candles:
            return
        sym = self.tm_symbol.get().strip().upper()
        entry = self.tm_entry.get().strip().replace(",", ".")
        stop = self.tm_stop.get().strip().replace(",", ".")
        target = self.tm_target.get().strip().replace(",", ".")
        bar = self.chart_candles[-1]
        for b in self.chart_candles:
            if b["time"] == self.tm_time.get().strip():
                bar = b
                break

        def worker():
            tm_candidates = [
                os.path.join(BASE_DIR, "trader-timemachine", "timemachine.py"),
                os.path.join(BASE_DIR, "timemachine.py"),
                os.path.join(BASE_DIR, "trader-scanner", "timemachine.py"),
            ]
            tm_script = next((p for p in tm_candidates if os.path.exists(p)), None)
            if not tm_script:
                return
            try:
                cmd = [
                    sys.executable, tm_script, "run",
                    "--symbol", sym,
                    "--entry", entry,
                    "--stop", stop,
                    "--target", target,
                    "--date", bar["date"],
                    "--entry-time", bar["time"],
                    "--json"
                ]
                side = tm_side(entry, stop)
                if side:
                    cmd.extend(["--side", side])
                proc = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
                out = (proc.stdout or "").strip()
                if out.startswith("{"):
                    j = json.loads(out)
                    self.tm_real_stats[sym] = j
                    self.root.after(0, self.redraw_chart)
                else:
                    m = re.search(r"target first\s+([\d\.]+)\s*%\s*vs\s*stop first\s+([\d\.]+)\s*%", out, re.IGNORECASE)
                    if m:
                        self.tm_real_stats[sym] = {
                            "target_first_pct": float(m.group(1)),
                            "stop_first_pct": float(m.group(2))
                        }
                        self.root.after(0, self.redraw_chart)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # VĚDECKÁ TECHNICKÁ & SKRIPTOVÁ PREDIKCE V GRAFU
    # =========================================================================
    def _build_real_predicted_candles(self, bars, anchor_idx, entry_v, sl_v, tp_v):
        sub = bars[:anchor_idx + 1]
        anchor = sub[-1]
        start_p = anchor["close"]

        cum_pv, cum_v = 0.0, 0
        ema9 = sub[0]["close"]
        ema_history = []
        k = 2.0 / 10.0
        for b in sub:
            typ = (b["high"] + b["low"] + b["close"]) / 3.0
            cum_pv += typ * b["volume"]
            cum_v += b["volume"]
            ema9 = (b["close"] - ema9) * k + ema9
            ema_history.append(ema9)
        vwap = (cum_pv / cum_v) if cum_v > 0 else start_p

        recent = sub[-14:]
        tf_atr = sum(b["high"] - b["low"] for b in recent) / max(len(recent), 1)
        tf_atr = max(tf_atr, start_p * 0.0012)

        ema_slope = (ema_history[-1] - ema_history[max(0, len(ema_history) - 4)]) / max(tf_atr, 0.01)
        avg_v = sum(b["volume"] for b in recent) / max(len(recent), 1)
        last_v = sum(b["volume"] for b in sub[-2:]) / min(len(sub), 2)
        vol_boost = min(max(last_v / max(avg_v, 1), 0.6), 1.8)

        bias = 0.0
        bias += 0.35 if start_p >= vwap else -0.35
        bias += 0.30 if start_p >= ema9 else -0.30
        bias += max(min(ema_slope * 0.25, 0.35), -0.35)

        sym = self.tm_symbol.get().strip().upper()
        tm_stat = self.tm_real_stats.get(sym)
        stat_label = ""
        if isinstance(tm_stat, dict):
            t_rate = first_valid(
                tm_stat.get("target_first_pct"),
                tm_stat.get("win_rate"),
                (tm_stat.get("Summary") or {}).get("target_first_pct")
            )
            s_rate = first_valid(
                tm_stat.get("stop_first_pct"),
                (tm_stat.get("Summary") or {}).get("stop_first_pct")
            )
            if t_rate is not None and s_rate is not None:
                if float(t_rate) > float(s_rate):
                    bias = max(bias, 0.45)
                elif float(s_rate) > float(t_rate):
                    bias = min(bias, -0.45)
                stat_label = f" | 60d Replay: TP {float(t_rate):.1f}% vs SL {float(s_rate):.1f}%"

        pred_bars = []
        cur_o = start_p
        for step_i in range(1, 6):
            vwap_pull = (vwap - cur_o) * (0.16 if vol_boost < 0.9 else 0.04)
            trend_step = bias * tf_atr * 0.60 * vol_boost * (0.92 ** (step_i - 1))
            cur_c = cur_o + trend_step + vwap_pull
            if bias >= 0:
                cur_h = max(cur_o, cur_c) + tf_atr * 0.42
                cur_l = min(cur_o, cur_c) - tf_atr * 0.22
            else:
                cur_h = max(cur_o, cur_c) + tf_atr * 0.22
                cur_l = min(cur_o, cur_c) - tf_atr * 0.42

            pred_bars.append({
                "step": f"+{step_i}",
                "open": cur_o,
                "high": cur_h,
                "low": cur_l,
                "close": cur_c
            })
            cur_o = cur_c

        verdict = "BULLISH (Nad VWAP & EMA9)" if bias >= 0.15 else ("BEARISH / FADE (Pod VWAP & EMA9)" if bias <= -0.15 else "NEUTRAL U VWAP")
        summary_info = f"Model: {verdict} | ATR({self.tf_var.get()}): ${tf_atr:.2f} | RVOL: {vol_boost:.1f}x{stat_label}"
        return pred_bars, summary_info

    # =========================================================================
    # VYKRESLENÍ GRAFU SE ZOOMEM A POSUNEM (PANNING)
    # =========================================================================
    def redraw_chart(self, hover_x=None, hover_y=None):
        c = self.chart_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 100 or h < 100:
            return

        if not self.chart_candles:
            c.create_text(
                w // 2, h // 2,
                text="Klikni na '↻ Obnovit Graf' nebo vyber časový rámec (1m, 2m, 5m, 15m, 30m, 1h).",
                fill="#ff77bc", font=("Segoe UI", 11, "bold")
            )
            return

        pad_left, pad_right, pad_top, pad_bot = 15, 92, 28, 34
        plot_w = w - pad_left - pad_right
        plot_h = h - pad_top - pad_bot

        full_vwap = []
        full_ema9 = []
        cum_pv, cum_v = 0.0, 0
        ema9 = self.chart_candles[0]["close"]
        k_ema = 2.0 / 10.0
        for b in self.chart_candles:
            typ = (b["high"] + b["low"] + b["close"]) / 3.0
            cum_pv += typ * b["volume"]
            cum_v += b["volume"]
            full_vwap.append(cum_pv / cum_v if cum_v > 0 else b["close"])
            ema9 = (b["close"] - ema9) * k_ema + ema9
            full_ema9.append(ema9)

        active_time = self.tm_time.get().strip()
        anchor_full_idx = len(self.chart_candles) - 1
        for idx, b in enumerate(self.chart_candles):
            if b["time"] == active_time:
                anchor_full_idx = idx
                break

        entry_v = self._to_float(self.tm_entry.get())
        sl_v = self._to_float(self.tm_stop.get())
        tp_v = self._to_float(self.tm_target.get())

        show_pink = self.show_pink_pred_var.get()
        pred_bars, pred_summary = ([], "")
        if show_pink:
            pred_bars, pred_summary = self._build_real_predicted_candles(
                self.chart_candles, anchor_full_idx, entry_v, sl_v, tp_v
            )

        total_len = len(self.chart_candles)
        zoom_n = min(max(20, self.visible_bars_count), total_len)
        end_idx = max(zoom_n, total_len - self.pan_offset)
        end_idx = min(total_len, end_idx)
        start_idx = max(0, end_idx - zoom_n)

        vis_candles = self.chart_candles[start_idx:end_idx]
        vis_vwap = full_vwap[start_idx:end_idx]
        vis_ema9 = full_ema9[start_idx:end_idx]

        anchor_vis_idx = anchor_full_idx - start_idx if start_idx <= anchor_full_idx < end_idx else (len(vis_candles) - 1)

        highs = [b["high"] for b in vis_candles] + ([pb["high"] for pb in pred_bars] if show_pink else [])
        lows = [b["low"] for b in vis_candles] + ([pb["low"] for pb in pred_bars] if show_pink else [])
        vols = [b["volume"] for b in vis_candles]

        p_max = max(highs)
        p_min = min(lows)
        candle_span = max(p_max - p_min, p_max * 0.002, 0.05)

        for lvl in (entry_v, sl_v, tp_v):
            if lvl and (p_min - candle_span * 0.25) <= lvl <= (p_max + candle_span * 0.25):
                p_max = max(p_max, lvl)
                p_min = min(p_min, lvl)

        margin = (p_max - p_min) * 0.08 or 0.05
        p_max += margin
        p_min -= margin
        max_vol = max(vols) if vols and max(vols) > 0 else 1

        def price_to_y(p):
            return pad_top + plot_h * (1.0 - (p - p_min) / (p_max - p_min))

        def y_to_price(y_px):
            ratio = 1.0 - ((y_px - pad_top) / max(plot_h, 1))
            return p_min + ratio * (p_max - p_min)

        pred_count = len(pred_bars) if show_pink else 0
        n_vis = len(vis_candles)
        total_slots = n_vis + pred_count
        step_x = plot_w / max(total_slots, 1)
        body_w = max(int(step_x * 0.72), 2)

        self.chart_meta["y_to_price"] = y_to_price
        self.chart_meta["pad_left"] = pad_left
        self.chart_meta["step_x"] = step_x
        self.chart_meta["vis_candles"] = vis_candles

        steps = 7
        for i in range(steps + 1):
            p_val = p_min + (p_max - p_min) * (i / steps)
            y = price_to_y(p_val)
            c.create_line(pad_left, y, w - pad_right, y, fill="#161a25")
            c.create_text(w - pad_right + 8, y, text=f"${p_val:.2f}", fill="#787b86", font=("Consolas", 8), anchor="w")

        chart_style = self.chart_type_var.get()
        line_pts, vwap_pts, ema9_pts = [], [], []
        ha_prev_o = vis_candles[0]["open"]
        ha_prev_c = vis_candles[0]["close"]

        for i, bar in enumerate(vis_candles):
            cx = pad_left + (i + 0.5) * step_x

            if chart_style == "Heikin-Ashi":
                ha_c = (bar["open"] + bar["high"] + bar["low"] + bar["close"]) / 4.0
                ha_o = (ha_prev_o + ha_prev_c) / 2.0 if i > 0 else bar["open"]
                ha_h = max(bar["high"], ha_o, ha_c)
                ha_l = min(bar["low"], ha_o, ha_c)
                ha_prev_o, ha_prev_c = ha_o, ha_c
                o_val, h_val, l_val, c_val = ha_o, ha_h, ha_l, ha_c
            else:
                o_val, h_val, l_val, c_val = bar["open"], bar["high"], bar["low"], bar["close"]

            yo, yh, yl, yc = price_to_y(o_val), price_to_y(h_val), price_to_y(l_val), price_to_y(c_val)
            color = "#089981" if c_val >= o_val else "#f23645"

            if i == anchor_vis_idx:
                c.create_rectangle(cx - step_x / 2, pad_top, cx + step_x / 2, pad_top + plot_h, fill="#1f1429", outline="#ff2e93", dash=(2, 2))

            vh = (bar["volume"] / max_vol) * (plot_h * 0.20)
            vol_col = "#0d3d38" if c_val >= o_val else "#421820"
            c.create_rectangle(cx - body_w / 2, pad_top + plot_h - vh, cx + body_w / 2, pad_top + plot_h, fill=vol_col, outline="")

            if chart_style in ("Svíčky (Candles)", "Heikin-Ashi"):
                c.create_line(cx, yh, cx, yl, fill=color, width=1)
                top_b, bot_b = min(yo, yc), max(yo, yc)
                if bot_b - top_b < 1.5:
                    bot_b = top_b + 1.5
                c.create_rectangle(cx - body_w / 2, top_b, cx + body_w / 2, bot_b, fill=color, outline=color)
            else:
                line_pts.extend([cx, yc])

            vwap_pts.extend([cx, price_to_y(vis_vwap[i])])
            ema9_pts.extend([cx, price_to_y(vis_ema9[i])])

            if i % max(1, n_vis // 8) == 0 or i == n_vis - 1:
                c.create_line(cx, pad_top, cx, pad_top + plot_h, fill="#151922")
                lbl_x = bar["time"] if self.tm_date.get() != "Multi-Day (Vše)" else f"{bar['date'][5:]} {bar['time']}"
                c.create_text(cx, h - 14, text=lbl_x, fill="#787b86", font=("Consolas", 8))

        if chart_style in ("Čárový (Line)", "Plošný (Area)") and len(line_pts) >= 4:
            if chart_style == "Plošný (Area)":
                poly_pts = [line_pts[0], pad_top + plot_h] + line_pts + [line_pts[-2], pad_top + plot_h]
                c.create_polygon(*poly_pts, fill="#16294a", outline="")
            c.create_line(*line_pts, fill="#2962ff", width=2.2, smooth=True)

        if len(vwap_pts) >= 4:
            c.create_line(*vwap_pts, fill="#f5a623", width=1.6, smooth=True)
        if len(ema9_pts) >= 4:
            c.create_line(*ema9_pts, fill="#00bcd4", width=1.2, smooth=True, dash=(4, 2))

        sym_lbl = self.tm_symbol.get().strip().upper()
        tf_lbl = self.tf_var.get()
        c.create_text(
            pad_left + 8, pad_top + 6,
            text=f"{sym_lbl} • {tf_lbl} (Zoom: {n_vis}/{total_len} svíček)  |  {pred_summary}",
            fill="#ffffff", font=("Segoe UI", 9, "bold"), anchor="nw"
        )

        if show_pink and pred_bars:
            zone_x1 = pad_left + n_vis * step_x
            zone_x2 = pad_left + (n_vis + len(pred_bars)) * step_x
            c.create_rectangle(zone_x1, pad_top, zone_x2, pad_top + plot_h, fill="#1a0d21", outline="#ff2e93", width=1, dash=(4, 4))
            c.create_text((zone_x1 + zone_x2) / 2, pad_top + 22, text=f"🩷 PREDIKCE ({tf_lbl})", fill="#ff2e93", font=("Segoe UI", 8, "bold"))

            anchor_bar = vis_candles[-1]
            pink_curve = [pad_left + (n_vis - 0.5) * step_x, price_to_y(anchor_bar["close"])]

            for idx_p, pb in enumerate(pred_bars):
                px = pad_left + (n_vis + idx_p + 0.5) * step_x
                pyo, pyh, pyl, pyc = price_to_y(pb["open"]), price_to_y(pb["high"]), price_to_y(pb["low"]), price_to_y(pb["close"])
                c.create_line(px, pyh, px, pyl, fill="#ff77bc", width=1, dash=(2, 2))
                ptop, pbot = min(pyo, pyc), max(pyo, pyc)
                if pbot - ptop < 2:
                    pbot = ptop + 2
                c.create_rectangle(px - body_w / 2, ptop, px + body_w / 2, pbot, fill="#ff2e93", outline="#ff99cc")
                pink_curve.extend([px, pyc])

            if len(pink_curve) >= 4:
                c.create_line(*pink_curve, fill="#ff2e93", width=2.5, smooth=True, arrow=tk.LAST, arrowshape=(10, 12, 5))

        def draw_level(val, label, col):
            if val is None:
                return
            clamped = max(min(val, p_max), p_min)
            y = price_to_y(clamped)
            is_outside = (val < p_min or val > p_max)
            c.create_line(pad_left, y, w - pad_right, y, fill=col, width=1.5, dash=(2, 4) if is_outside else (5, 3))
            c.create_rectangle(w - pad_right + 2, y - 9, w - 2, y + 9, fill=col, outline="")
            prefix = "▲ " if val > p_max else ("▼ " if val < p_min else "")
            c.create_text(w - pad_right + 5, y, text=f"{prefix}{label} {val:.2f}", fill="#ffffff", font=("Consolas", 8, "bold"), anchor="w")

        draw_level(entry_v, "IN", "#2962ff")
        draw_level(sl_v, "SL", "#f23645")
        draw_level(tp_v, "TP", "#ff2e93" if show_pink else "#089981")

        if hover_x and hover_y and pad_left <= hover_x <= (w - pad_right) and pad_top <= hover_y <= (pad_top + plot_h):
            c.create_line(hover_x, pad_top, hover_x, pad_top + plot_h, fill="#ff77bc", dash=(2, 2))
            c.create_line(pad_left, hover_y, w - pad_right, hover_y, fill="#ff77bc", dash=(2, 2))
            hp = y_to_price(hover_y)
            c.create_rectangle(w - pad_right + 2, hover_y - 9, w - 2, hover_y + 9, fill="#363a45", outline="#ff2e93")
            c.create_text(w - pad_right + 5, hover_y, text=f"${hp:.2f}", fill="#ffffff", font=("Consolas", 8, "bold"), anchor="w")

    def _get_candle_at_x(self, x_px):
        vis = self.chart_meta.get("vis_candles", self.chart_candles)
        if not vis:
            return None
        pad_left = self.chart_meta.get("pad_left", 15)
        step_x = self.chart_meta.get("step_x", 10)
        idx = int((x_px - pad_left) / max(step_x, 1))
        if 0 <= idx < len(vis):
            return vis[idx]
        return None

    def on_chart_hover(self, event):
        bar = self._get_candle_at_x(event.x)
        y_fn = self.chart_meta.get("y_to_price")
        tf = self.tf_var.get()
        if bar and y_fn:
            cur_p = y_fn(event.y)
            chg = ((bar["close"] - bar["open"]) / bar["open"]) * 100.0
            self.chart_info_lbl.config(
                text=f"🕒 [{tf} Svíčka] {bar['date']} {bar['time']} NY  |  O: ${bar['open']:.2f}  H: ${bar['high']:.2f}  L: ${bar['low']:.2f}  C: ${bar['close']:.2f} ({chg:+.2f}%)  |  Vol: {bar['volume']:,}  |  Kurzor: ${cur_p:.2f}",
                fg="#d1d4dc"
            )
            self.redraw_chart(hover_x=event.x, hover_y=event.y)

    def on_chart_left_click(self, event):
        self._drag_start_x = event.x
        self._drag_start_offset = self.pan_offset
        bar = self._get_candle_at_x(event.x)
        y_fn = self.chart_meta.get("y_to_price")
        if bar and y_fn:
            clicked_price = round_tick(y_fn(event.y))
            entry_price = round_tick(bar["close"]) if bar["low"] <= clicked_price <= bar["high"] else clicked_price
            self.tm_entry.delete(0, tk.END)
            self.tm_entry.insert(0, f"{entry_price:.2f}")
            self.tm_time.delete(0, tk.END)
            self.tm_time.insert(0, bar["time"])
            self.redraw_chart()
            self.run_silent_script_prediction()

    def on_chart_drag(self, event):
        if self._drag_start_x is None or not self.chart_candles:
            return
        dx = event.x - self._drag_start_x
        if abs(dx) < 8:
            return
        step_x = max(self.chart_meta.get("step_x", 8), 2)
        bars_moved = int(dx / step_x)
        max_off = max(0, len(self.chart_candles) - 15)
        self.pan_offset = max(0, min(max_off, self._drag_start_offset + bars_moved))
        self.redraw_chart()

    def on_chart_release(self, event):
        self._drag_start_x = None

    def on_chart_right_click(self, event):
        y_fn = self.chart_meta.get("y_to_price")
        if y_fn:
            sl_price = round_tick(y_fn(event.y))
            self.tm_stop.delete(0, tk.END)
            self.tm_stop.insert(0, f"{sl_price:.2f}")
            self.redraw_chart()
            self.run_silent_script_prediction()

    def on_chart_middle_click(self, event):
        y_fn = self.chart_meta.get("y_to_price")
        if y_fn:
            tp_price = round_tick(y_fn(event.y))
            self.tm_target.delete(0, tk.END)
            self.tm_target.insert(0, f"{tp_price:.2f}")
            self.redraw_chart()
            self.run_silent_script_prediction()

    # =========================================================================
    # STAHOVÁNÍ A SLUČOVÁNÍ DAT
    # =========================================================================
    def fetch_yahoo_direct(self, symbol):
        return fetch_yahoo_market_snapshot(symbol)

    def merge_live_with_scanner(self, sym, live_data):
        """Živá data jsou autoritativní pro VŠECHNA pole, která fetch vrací (i None),
        takže se nikdy nemíchají hodnoty z různých dnů. Ze scanneru se zachovají jen
        pole scanner_* a float."""
        existing = self.items_map.get(sym)
        if not existing:
            self.items_map[sym] = live_data
            return

        merged = dict(existing)
        merged.update(live_data)
        if existing.get("scanner_rule") or existing.get("scanner_setups"):
            merged["source"] = "Merged (Scanner + Live Yahoo)"
        self.items_map[sym] = merged

    def autoload_existing_watchlist(self):
        candidates = [
            os.path.join(BASE_DIR, "trader", "watchlist.json"),
            os.path.join(BASE_DIR, "trader-scanner", "watchlist.json"),
            os.path.join(BASE_DIR, "watchlist.json")
        ]
        wl_file = next((p for p in candidates if os.path.exists(p)), None)
        if not wl_file:
            return
        try:
            with open(wl_file, "r", encoding="utf-8", errors="replace") as f:
                raw = json.load(f)
            mtime = datetime.fromtimestamp(os.path.getmtime(wl_file), tz=CET_TZ).strftime("%Y-%m-%d %H:%M:%S CET")
            self.populate_from_json(raw, file_mtime=mtime)
            self.scan_status.config(text=f"✓ Načteno {len(self.items_map)} akcií z {os.path.relpath(wl_file, BASE_DIR)} (ze dne {mtime}).", fg="#089981")
        except Exception as exc:
            self.scan_status.config(text=f"Chyba čtení watchlist.json: {str(exc)}", fg="#f23645")

    def extract_rows_recursive(self, data):
        if isinstance(data, list):
            return [el if isinstance(el, dict) else {"symbol": str(el)} for el in data]
        if isinstance(data, dict):
            for key in ["hits", "watchlist", "candidates", "results", "stocks", "data", "items", "rows"]:
                if key in data and isinstance(data[key], list):
                    return self.extract_rows_recursive(data[key])
            for k, v in data.items():
                if isinstance(v, list) and len(v) > 0:
                    return self.extract_rows_recursive(v)
            out = []
            for k, v in data.items():
                if isinstance(v, dict):
                    v["symbol"] = first_valid(v.get("symbol"), v.get("ticker"), k)
                    out.append(v)
            if out:
                return out
        return []

    def populate_from_json(self, raw_data, file_mtime="Neznámý čas"):
        """Načte watchlist.json ze scan.py (trader-scanner).
        daily: symbol, setups[], price, close_date, rsi, volume
        orb  : symbol, setups[], price, at, bar_start, bar_index, or_high, or_low, pct_over
        Žádná jiná pole (prev_close, gap, pmh, atr, rvol, float, catalyst) scanner nezapisuje –
        všechna čísla pro obchod proto pocházejí z živého dotazu na Yahoo."""
        rows = self.extract_rows_recursive(raw_data)
        scan_mode = raw_data.get("mode") if isinstance(raw_data, dict) else None
        scan_as_of = raw_data.get("as_of") if isinstance(raw_data, dict) else None
        self.items_map.clear()

        for r in rows:
            sym_raw = first_valid(r.get("symbol"), r.get("ticker"), r.get("Symbol"))
            sym = str(sym_raw or "").strip().upper()
            if not sym:
                continue

            setups_raw = r.get("setups")
            if isinstance(setups_raw, str):
                setups_raw = [setups_raw]
            setups = [str(x) for x in (setups_raw or [])]

            price = self._to_float(r.get("price"))
            volume = int(self._to_float(r.get("volume")) or 0)
            rsi = self._to_float(r.get("rsi"))
            close_date = first_valid(r.get("close_date"))
            at_time = first_valid(r.get("at"))
            or_high = self._to_float(r.get("or_high"))
            or_low = self._to_float(r.get("or_low"))
            pct_over = self._to_float(r.get("pct_over"))

            parts = [SETUP_LABELS.get(x, x) for x in setups]
            legacy = first_valid(r.get("catalyst"), r.get("setup"), r.get("rule"), r.get("reason"), r.get("note"))
            if legacy is not None:
                parts.append(str(legacy))
            if rsi is not None:
                parts.append(f"RSI {rsi:.1f}")
            if or_high is not None and or_low is not None:
                parts.append(f"OR {or_low:.2f}–{or_high:.2f}")
            if pct_over is not None:
                parts.append(f"{pct_over:+.2f}% nad OR high")
            if at_time is not None:
                parts.append(f"potvrzeno {at_time} ET")
            if close_date is not None:
                parts.append(f"close {close_date}")
            rule_txt = ", ".join(parts) if parts else "Watchlist scan"

            price_when = first_valid(close_date, at_time, scan_as_of, "čas neznámý")

            self.items_map[sym] = {
                "symbol": sym,
                "fetched_at_ny": "Ze souboru watchlist.json",
                "fetched_at_cet": file_mtime,
                "market_phase": "SCANNER_FILE",
                "market_phase_label": "Načteno z watchlist.json",
                "source": "Scanner (watchlist.json)",
                "scanner_mode": scan_mode,
                "scanner_as_of": scan_as_of,
                "scanner_close_date": close_date,
                "scanner_setups": setups,
                "scanner_price": price,
                "scanner_volume": volume,
                "scanner_rsi": rsi,
                "scanner_or_high": or_high,
                "scanner_or_low": or_low,
                "scanner_pct_over": pct_over,
                "scanner_at": at_time,
                "prev_close": self._to_float(first_valid(r.get("prev_close"), r.get("previousClose"))),
                "prev_close_date": "dle skeneru" if first_valid(r.get("prev_close"), r.get("previousClose")) is not None else None,
                "pm_price": None,
                "pmh": self._to_float(first_valid(r.get("pmh"), r.get("premarket_high"))),
                "pml": self._to_float(first_valid(r.get("pml"), r.get("premarket_low"))),
                "pm_vol": 0,
                "pm_vwap": None,
                "reg_price": None,
                "reg_open": None,
                "reg_high": None,
                "reg_low": None,
                "reg_vol": 0,
                "reg_vwap": None,
                "active_price": price,
                "active_price_type": f"Scanner Price ({price_when})",
                "vwap": self._to_float(r.get("vwap")),
                "vwap_type": "Scanner VWAP" if r.get("vwap") is not None else None,
                "gap_pct": self._to_float(first_valid(r.get("gap_pct"), r.get("gap"))),
                "gap_reference": "Scanner Gap %" if first_valid(r.get("gap_pct"), r.get("gap")) is not None else None,
                "atr": self._to_float(first_valid(r.get("atr"), r.get("atr14"), r.get("atr_14"))),
                "rvol": self._to_float(first_valid(r.get("rvol"), r.get("relative_volume"))),
                "rvol_type": "Scanner RVOL" if first_valid(r.get("rvol"), r.get("relative_volume")) is not None else None,
                "float": self._to_float(first_valid(r.get("float"), r.get("shares_float"), r.get("float_shares"))),
                "scanner_rule": rule_txt,
                "prediction": f"[Scanner] {rule_txt}",
                "warnings": []
            }
        self.apply_table_filter()

    def _to_float(self, val):
        if val is None or val == "":
            return None
        try:
            if isinstance(val, str):
                t = val.replace("%", "").replace("$", "").replace("\u00a0", "").replace(" ", "").strip()
                if "," in t and "." in t:
                    t = t.replace(",", "")            # 1,234.56
                elif "," in t:
                    if re.fullmatch(r"-?\d{1,3}(,\d{3})+", t):
                        t = t.replace(",", "")        # 1,234,567
                    else:
                        t = t.replace(",", ".")       # 1,5 -> 1.5
                return float(t)
            return float(val)
        except Exception:
            return None

    def _insert_or_update_tree_row(self, sym):
        d = self.items_map[sym]
        pred = d.get("prediction", "")
        tag = "bull" if "🟢" in pred else ("bear" if "🔴" in pred else "neutral")

        pm_hl_str = f"${d['pmh']:.2f}/${d['pml']:.2f}" if (d.get("pmh") and d.get("pml")) else "-"
        reg_hl_str = f"${d['reg_high']:.2f}/${d['reg_low']:.2f}" if (d.get("reg_high") and d.get("reg_low")) else "-"

        pm_v = d.get("pm_vol") or 0
        reg_v = d.get("reg_vol") or 0
        vol_str = f"PM:{pm_v/1e3:.0f}k | R:{reg_v/1e3:.0f}k" if (pm_v or reg_v) else "-"
        if vol_str == "-" and d.get("scanner_volume"):
            vol_str = f"Sc:{d['scanner_volume']/1e3:.0f}k"

        act_p = first_valid(d.get("active_price"), d.get("pm_price"), d.get("reg_price"))

        vals = (
            sym,
            f"${d['prev_close']:.2f}" if d.get("prev_close") is not None else "-",
            f"${act_p:.2f}" if act_p is not None else "-",
            f"{d['gap_pct']:+.2f}%" if d.get("gap_pct") is not None else "-",
            pm_hl_str,
            reg_hl_str,
            f"${d['vwap']:.2f}" if d.get("vwap") is not None else "-",
            f"${d['atr']:.2f}" if d.get("atr") is not None else "-",
            f"{d['rvol']:.2f}x" if d.get("rvol") is not None else "-",
            vol_str,
            pred
        )
        if self.tree.exists(sym):
            self.tree.item(sym, values=vals, tags=(tag,))
        else:
            self.tree.insert("", tk.END, iid=sym, values=vals, tags=(tag,))

    def on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        sym = sel[0]
        self.selected_symbol = sym
        self.manual_ticker_entry.delete(0, tk.END)
        self.manual_ticker_entry.insert(0, sym)

        d = self.items_map.get(sym, {})
        if d.get("source") == "Scanner (watchlist.json)" or d.get("atr") is None:
            self.fetch_single_ticker_live()
        else:
            self.update_prediction_banner(sym, update_tm_entries=True)
            self.load_interactive_chart(auto_reset_levels=True)

    def fetch_single_ticker_live(self):
        sym = self.manual_ticker_entry.get().strip().upper()
        if not sym:
            return
        self.selected_symbol = sym
        self.btn_live_pm.config(state=tk.DISABLED, bg="#2a2e39")
        self.pred_summary_lbl.config(text=f"Stahuji a validuji reálná tržní data (PM vs. RTH v NY čase, Wilderův ATR) pro {sym}...", fg="#ff2e93")

        def worker():
            try:
                live = self.fetch_yahoo_direct(sym)
                self.merge_live_with_scanner(sym, live)

                self.root.after(0, self.apply_table_filter)
                self.root.after(0, lambda: self.update_prediction_banner(sym, update_tm_entries=True))
                self.root.after(0, lambda: self.load_interactive_chart(auto_reset_levels=True))
            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda m=err: self.pred_summary_lbl.config(text=f"Chyba Yahoo dat: {m}", fg="#f23645"))
            finally:
                self.root.after(0, lambda: self.btn_live_pm.config(state=tk.NORMAL, bg="#ff2e93"))

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # VÝPOČET POKYNU SE STRUKTUROU TRHU A CAPEM RIZIKA
    # =========================================================================
    def calculate_order_params(self, sym):
        d = self.items_map.get(sym, {})
        cur = first_valid(d.get("active_price"), d.get("reg_price"), d.get("pm_price"))
        atr = d.get("atr")
        vwap = d.get("vwap")

        cap_czk = self._to_float(self.account_size.get()) or 50000.0
        risk_p = self._to_float(self.risk_pct.get()) or 1.0
        fx = self._to_float(self.usd_czk.get()) or 23.50

        max_risk_czk = cap_czk * (risk_p / 100.0)
        max_risk_usd = max_risk_czk / fx
        max_capital_usd = cap_czk / fx

        def no_trade(scenario, reason, invalidation):
            return {
                "valid": False,
                "reason": reason,
                "scenario": scenario,
                "order_type": "ŽÁDNÝ POKYN",
                "direction": "NONE",
                "entry": None,
                "sl": None,
                "tp1": None,
                "tp2": None,
                "shares": 0,
                "actual_risk_usd": 0.0,
                "actual_risk_czk": 0.0,
                "pos_usd": 0.0,
                "pos_czk": 0.0,
                "fx": fx,
                "invalidation_reason": invalidation
            }

        if d.get("stale_session"):
            return no_trade(
                "ZASTARALÁ DATA (NO TRADE)",
                f"Dnešní seance nemá v Yahoo žádné obchody (svátek, nebo ticker dnes ještě neobchodoval). "
                f"Data jsou ze seance {d.get('session_date')} – žádný pokyn.",
                "Zastaralá data"
            )

        if cur is None or cur <= 0 or atr is None or atr <= 0:
            return no_trade(
                "NEDOSTATEK DAT (NO TRADE)",
                "Chybí ověřená cena nebo 14denní ATR – nelze spočítat bezpečný pokyn.",
                "Neúplná tržní data"
            )

        phase = d.get("market_phase", "REGULAR")
        pmh = d.get("pmh")
        pml = d.get("pml")
        reg_h = d.get("reg_high")
        reg_l = d.get("reg_low")
        pred_txt = d.get("prediction", "")

        ref_high = pmh if (phase == "PREMARKET" and pmh) else (reg_h or pmh or cur)
        ref_low = pml if (phase == "PREMARKET" and pml) else (reg_l or pml or cur)
        vwap_val = vwap if vwap is not None else ((ref_high + ref_low) / 2.0)

        atr_buf = max(0.08 * atr, 0.02)
        max_sl_dist = MAX_SL_ATR * atr
        sl_capped = False

        if "GAP & GO" in pred_txt or "TREND UP" in pred_txt or "REVERSAL" in pred_txt:
            direction = "LONG"
            if cur <= ref_high and (ref_high - cur) <= 0.35 * atr:
                order_type = "Buy Stop (Průraz High)"
                entry = round_tick(ref_high + 0.02)
                struct_level = vwap_val - atr_buf
                structure_txt = f"Ztráta VWAP (${vwap_val:.2f}) a selhání průrazu High (${ref_high:.2f})"
            else:
                order_type = "Buy Limit (Pullback k VWAP / podpoře)"
                pullback_lvl = max(vwap_val + 0.02, cur - 0.15 * atr)
                entry = round_tick(min(cur, pullback_lvl))
                struct_level = min(vwap_val, ref_low) - atr_buf
                structure_txt = f"Uzavření 5m svíčky pod VWAP (${vwap_val:.2f}) / Low (${ref_low:.2f})"

            cap_level = entry - max_sl_dist
            sl_capped = struct_level < cap_level
            sl = round_tick(min(max(struct_level, cap_level), entry - 0.05))
            risk_per_share = max(entry - sl, 0.01)
            if sl_capped:
                invalidation = (f"SL omezen stropem {MAX_SL_ATR} ATR (${risk_per_share:.2f}/ks) – "
                                f"strukturální invalidace je dál: {structure_txt}")
            else:
                invalidation = structure_txt

            tp1 = round_tick(entry + max(1.8 * risk_per_share, 0.45 * atr))
            tp2 = round_tick(entry + max(3.0 * risk_per_share, 0.85 * atr))
            scenario_desc = f"LONG ({order_type}) – {pred_txt}"

        elif "GAP FADE" in pred_txt or "WEAKNESS" in pred_txt or "CONTINUATION" in pred_txt:
            if not ALLOW_SHORT_CFD:
                return no_trade(
                    f"VYHNOUT SE LONGU – {pred_txt}",
                    "Bearish scénář: Long se nevyplatí a short je u XTB jen přes CFD "
                    "(pokud obchoduješ CFD, nastav v gen.py ALLOW_SHORT_CFD = True). Žádný pokyn.",
                    f"Návrat a udržení ceny nad VWAP (${vwap_val:.2f}) / High (${ref_high:.2f}) by scénář zrušil"
                )

            direction = "SHORT_CFD"
            retest = vwap_val - 0.01
            if cur < retest:
                order_type = "Sell Limit CFD (retest VWAP / odpor)"
                entry = round_tick(min(retest, cur + 0.15 * atr))
            else:
                order_type = "Sell Stop CFD (průraz pod VWAP)"
                entry = round_tick(vwap_val - 0.02)

            struct_level = max(vwap_val, ref_high) + atr_buf
            cap_level = entry + max_sl_dist
            sl_capped = struct_level > cap_level
            sl = round_tick(max(min(struct_level, cap_level), entry + 0.05))
            risk_per_share = max(sl - entry, 0.01)
            structure_txt = f"Návrat a udržení ceny nad VWAP (${vwap_val:.2f}) / High (${ref_high:.2f})"
            if sl_capped:
                invalidation = (f"SL omezen stropem {MAX_SL_ATR} ATR (${risk_per_share:.2f}/ks) – "
                                f"strukturální invalidace je dál: {structure_txt}")
            else:
                invalidation = structure_txt

            tp1 = round_tick(max(0.01, entry - max(1.8 * risk_per_share, 0.45 * atr)))
            tp2 = round_tick(max(0.01, entry - max(3.0 * risk_per_share, 0.85 * atr)))
            scenario_desc = f"SHORT CFD ({order_type}) – {pred_txt}"

        else:
            direction = "WAIT_BREAKOUT"
            order_type = "WAIT / Buy Stop až při potvrzeném průrazu nad High & VWAP"
            entry = round_tick(max(ref_high, vwap_val) + 0.02)
            floor_dist = max(0.25 * atr, 0.05)
            cap_dist = max(max_sl_dist, floor_dist)
            struct_dist = entry - (vwap_val - atr_buf)
            sl_capped = struct_dist > cap_dist
            sl = round_tick(entry - min(max(struct_dist, floor_dist), cap_dist))
            risk_per_share = max(entry - sl, 0.01)
            structure_txt = f"Pokles pod VWAP (${vwap_val:.2f})"
            if sl_capped:
                invalidation = (f"SL omezen stropem {MAX_SL_ATR} ATR (${risk_per_share:.2f}/ks) – "
                                f"strukturální invalidace je dál: {structure_txt}")
            else:
                invalidation = structure_txt
            tp1 = round_tick(entry + max(1.8 * risk_per_share, 0.45 * atr))
            tp2 = round_tick(entry + max(3.0 * risk_per_share, 0.85 * atr))
            scenario_desc = f"VYČKAT NA PRŮRAZ (Konsolidace) – {pred_txt}"

        tp1_r = abs(tp1 - entry) / risk_per_share
        tp2_r = abs(tp2 - entry) / risk_per_share

        shares_by_risk = int(math.floor(max_risk_usd / risk_per_share))
        shares_by_cap = int(math.floor(max_capital_usd / entry)) if entry > 0 else 0

        shares = max(0, min(shares_by_risk, shares_by_cap))
        actual_risk_usd = shares * risk_per_share
        actual_risk_czk = actual_risk_usd * fx
        pos_usd = shares * entry
        pos_czk = pos_usd * fx

        cap_warning = None
        if shares == 0:
            if shares_by_risk == 0:
                cap_warning = (
                    f"POZOR: Stop Loss na 1 akcii (${risk_per_share:.2f}) je větší než tvůj max risk "
                    f"${max_risk_usd:.2f} ({max_risk_czk:.0f} CZK). Pro dodržení rizika {risk_p}% je potřeba frakční akcie "
                    f"({max_risk_usd/risk_per_share:.2f} ks) nebo vyšší kapitál."
                )
            elif shares_by_cap == 0:
                cap_warning = (
                    f"POZOR: Cena 1 akcie (${entry:.2f}) přesahuje celkový kapitál účtu (${max_capital_usd:.2f})."
                )
        elif shares == shares_by_cap and shares_by_cap < shares_by_risk:
            cap_warning = f"Pozice omezena na {shares} ks podle 100% dostupného kapitálu účtu ({cap_czk:.0f} CZK)."

        return {
            "valid": True,
            "scenario": scenario_desc,
            "order_type": order_type,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "sl_capped": sl_capped,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_r": tp1_r,
            "tp2_r": tp2_r,
            "risk_per_share": risk_per_share,
            "shares": shares,
            "fractional_shares": round(max_risk_usd / risk_per_share, 2),
            "actual_risk_usd": actual_risk_usd,
            "actual_risk_czk": actual_risk_czk,
            "max_risk_czk": max_risk_czk,
            "pos_usd": pos_usd,
            "pos_czk": pos_czk,
            "fx": fx,
            "invalidation_reason": invalidation,
            "cap_warning": cap_warning
        }

    def update_prediction_banner(self, sym, update_tm_entries=True):
        d = self.items_map.get(sym, {})
        calc = self.calculate_order_params(sym)
        broker = self.broker_var.get()

        pc_s = f"${d['prev_close']:.2f} ({d.get('prev_close_date', '')})" if d.get("prev_close") else "N/A"
        act_s = f"${d['active_price']:.2f} ({d.get('active_price_type', '')})" if d.get("active_price") else "N/A"
        pm_s = f"${d['pmh']:.2f}/${d['pml']:.2f} (Vol {d.get('pm_vol', 0):,})" if (d.get("pmh") and d.get("pml")) else "N/A"
        reg_s = f"${d['reg_high']:.2f}/${d['reg_low']:.2f} (Vol {d.get('reg_vol', 0):,})" if (d.get("reg_high") and d.get("reg_low")) else "N/A"
        vwap_s = f"${d['vwap']:.2f}" if d.get("vwap") else "N/A"
        atr_s = f"${d['atr']:.2f}" if d.get("atr") else "N/A"

        if not calc.get("valid"):
            text = (
                f"[{sym}] Prev Close: {pc_s} | Aktuální: {act_s} | Stav: {d.get('market_phase_label', '')}\n"
                f"⚠️ {calc.get('reason')}"
            )
            self.pred_summary_lbl.config(text=text, fg="#f23645")
            return

        warn_line = f"\n⚠️ {calc['cap_warning']}" if calc.get("cap_warning") else ""
        text = (
            f"[{sym} | {d.get('market_phase_label', '')} | Staženo: {d.get('fetched_at_ny', '')}]  "
            f"Prev Close: {pc_s}  |  Aktuální: {act_s}  |  Gap: {fmt_pct(d.get('gap_pct'))}\n"
            f"PM H/L: {pm_s}  |  RTH H/L: {reg_s}  |  VWAP: {vwap_s}  |  Wilder ATR(14): {atr_s}  |  ČNB: {calc['fx']:.2f} CZK\n"
            f"🩷 SCÉNÁŘ: {calc['scenario']}  |  Invalidace: {calc['invalidation_reason']}\n"
            f"POKYN PRO {broker}: [{calc['order_type']}]  Vstup @ ${calc['entry']:.2f}  |  SL: ${calc['sl']:.2f}  |  "
            f"TP1: ${calc['tp1']:.2f}  |  TP2: ${calc['tp2']:.2f}  |  "
            f"KUSŮ: {calc['shares']} ks (Frakčně: {calc['fractional_shares']} ks)  |  "
            f"Hodnota: {calc['pos_czk']:.0f} CZK  |  Skutečný Risk: {calc['actual_risk_czk']:.0f} CZK (Max: {calc['max_risk_czk']:.0f} CZK)"
            f"{warn_line}"
        )
        self.pred_summary_lbl.config(text=text, fg="#ff77bc")

        if update_tm_entries and calc.get("entry") and calc.get("sl") and calc.get("tp1"):
            self.tm_symbol.delete(0, tk.END)
            self.tm_symbol.insert(0, sym)
            self.tm_entry.delete(0, tk.END)
            self.tm_entry.insert(0, f"{calc['entry']:.2f}")
            self.tm_stop.delete(0, tk.END)
            self.tm_stop.insert(0, f"{calc['sl']:.2f}")
            self.tm_target.delete(0, tk.END)
            self.tm_target.insert(0, f"{calc['tp1']:.2f}")

    # =========================================================================
    # SKENOVÁNÍ TRHU A TIME MACHINE PROCES
    # =========================================================================
    def run_stream(self, cmd_args, log_widget):
        process = subprocess.Popen(
            cmd_args, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1
        )
        for line in iter(process.stdout.readline, ''):
            if line:
                cleaned = line.rstrip()
                self.root.after(0, lambda t=cleaned: (log_widget.insert(tk.END, t + "\n"), log_widget.see(tk.END)))
        process.stdout.close()
        process.wait()
        return process.returncode

    def start_scan(self):
        self.btn_run_scan.config(state=tk.DISABLED, bg="#2a2e39")
        self.scan_log.delete("1.0", tk.END)
        self.scan_progress.start(10)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            mode = self.scan_mode_var.get()
            scan_candidates = [
                os.path.join(BASE_DIR, "trader-scanner", "scan.py"),
                os.path.join(BASE_DIR, "scan.py"),
                os.path.join(BASE_DIR, "trader", "scan.py")
            ]
            scan_script = next((p for p in scan_candidates if os.path.exists(p)), None)

            if mode == "custom":
                raw_syms = [s.strip().upper() for s in self.custom_symbols_entry.get().split(",") if s.strip()]
                if not raw_syms:
                    raise ValueError("Zadej alespoň jeden ticker.")
                self.items_map.clear()

                for idx, s in enumerate(raw_syms, 1):
                    self.root.after(0, lambda sym=s, i=idx, tot=len(raw_syms): self.scan_status.config(
                        text=f"Stahuji a validuji Yahoo data ({i}/{tot}): {sym}...", fg="#ff2e93"
                    ))
                    live = self.fetch_yahoo_direct(s)
                    self.items_map[s] = live
                    self.root.after(0, self.apply_table_filter)

                if raw_syms:
                    first = raw_syms[0]
                    self.root.after(0, lambda: self.update_prediction_banner(first, update_tm_entries=True))
                    self.root.after(0, lambda: self.load_interactive_chart(auto_reset_levels=True))
                self.root.after(0, lambda: self.scan_status.config(text="✓ Hotovo! Všechna validovaná data jsou v tabulce.", fg="#089981"))
                return

            if not scan_script:
                raise FileNotFoundError("Soubor scan.py nebyl nalezen.")

            uni_path = os.path.join(BASE_DIR, "trader", "universe.txt")
            if not os.path.exists(uni_path):
                self.root.after(0, lambda: self.scan_status.config(text="Stahuji universe.txt...", fg="#2962ff"))
                self.run_stream([sys.executable, scan_script, "universe"], self.scan_log)

            # scan.py nemá podpříkaz "premarket"; předotevírací sken je "daily".
            sub_cmd = "daily"
            self.root.after(0, lambda: self.scan_status.config(text=f"Spouštím scan.py {sub_cmd}...", fg="#2962ff"))
            self.run_stream([sys.executable, scan_script, sub_cmd], self.scan_log)

            loaded = threading.Event()

            def _load_watchlist():
                try:
                    self.autoload_existing_watchlist()
                finally:
                    loaded.set()

            self.root.after(0, _load_watchlist)
            loaded.wait(10)

            if self.auto_pm_var.get():
                top_symbols = list(self.items_map.keys())[:15]
                for idx, s in enumerate(top_symbols, 1):
                    self.root.after(0, lambda sym=s, i=idx, tot=len(top_symbols): self.scan_status.config(
                        text=f"Doplňuji živá Yahoo data (PM/RTH + Wilder ATR) ({i}/{tot}): {sym}...", fg="#ff2e93"
                    ))
                    try:
                        live = self.fetch_yahoo_direct(s)
                        self.merge_live_with_scanner(s, live)
                        self.root.after(0, self.apply_table_filter)
                    except Exception:
                        pass

            self.root.after(0, lambda: self.scan_status.config(text="✓ Kompletní sken i validace dat dokončena!", fg="#089981"))

        except Exception as exc:
            err = str(exc)
            self.root.after(0, lambda m=err: self.scan_status.config(text=f"Chyba: {m}", fg="#f23645"))
        finally:
            self.root.after(0, self.scan_progress.stop)
            self.root.after(0, lambda: self.btn_run_scan.config(state=tk.NORMAL, bg="#089981"))

    def start_timemachine(self):
        sym = self.tm_symbol.get().strip().upper()
        if not sym:
            return
        self.btn_run_tm.config(state=tk.DISABLED, bg="#2a2e39")
        self.tm_log.delete("1.0", tk.END)
        self.tm_progress.start(10)
        self.redraw_chart()
        threading.Thread(target=self._tm_worker, daemon=True).start()

    def _tm_worker(self):
        try:
            sym = self.tm_symbol.get().strip().upper()
            tm_candidates = [
                os.path.join(BASE_DIR, "trader-timemachine", "timemachine.py"),
                os.path.join(BASE_DIR, "timemachine.py"),
                os.path.join(BASE_DIR, "trader-scanner", "timemachine.py"),
            ]
            tm_script = next((p for p in tm_candidates if os.path.exists(p)), None)
            if not tm_script:
                raise FileNotFoundError("Soubor timemachine.py nebyl nalezen.")

            t_sel = self.tm_time.get().strip()
            target_bar = self.chart_candles[-1] if self.chart_candles else None
            if self.chart_candles:
                for b in self.chart_candles:
                    if b["time"] == t_sel:
                        target_bar = b
                        break

            entry = self.tm_entry.get().strip().replace(",", ".")
            stop = self.tm_stop.get().strip().replace(",", ".")
            target = self.tm_target.get().strip().replace(",", ".")
            dt_date = target_bar["date"] if target_bar else ""
            dt_time = target_bar["time"] if target_bar else t_sel

            cmd = [sys.executable, tm_script, "run", "--symbol", sym]
            if entry:
                cmd.extend(["--entry", entry])
            if stop:
                cmd.extend(["--stop", stop])
            if target:
                cmd.extend(["--target", target])
            side = tm_side(entry, stop) if (entry and stop) else None
            if side:
                cmd.extend(["--side", side])
            if dt_date:
                cmd.extend(["--date", dt_date])
            if dt_time:
                cmd.extend(["--entry-time", dt_time])

            cmd_str = " ".join(cmd)
            self.root.after(0, lambda t=cmd_str: self.tm_log.insert(tk.END, f">> Spouštím reálný 60denní replay: {t}\n\n"))
            code = self.run_stream(cmd, self.tm_log)

            self.run_silent_script_prediction()

            if code == 0:
                def copy_tm():
                    out = self.tm_log.get("1.0", tk.END).strip()
                    self.root.clipboard_clear()
                    self.root.clipboard_append(f"Zhodnoť výsledek 60denního replaye Time Machine pro {sym} ({dt_date} {dt_time} NY):\n\n{out}")
                    self.tm_log.insert(tk.END, "\n[OK] Výstup Time Machine byl zkopírován do schránky pro Claude!\n")
                self.root.after(0, copy_tm)

        except Exception as exc:
            err = str(exc)
            self.root.after(0, lambda m=err: self.tm_log.insert(tk.END, f"\nChyba: {m}\n"))
        finally:
            self.root.after(0, self.tm_progress.stop)
            self.root.after(0, lambda: self.btn_run_tm.config(state=tk.NORMAL, bg="#089981"))

    # =========================================================================
    # VALIDOVANÝ CLAUDE PROMPT SE SANITY KONTROLAMI A ŘAZENÍM TOP SETUPŮ
    # =========================================================================
    def _run_sanity_checks_for_prompt(self, d):
        issues = list(d.get("warnings") or [])
        pc = d.get("prev_close")
        act = d.get("active_price")
        if pc is None or pc <= 0:
            issues.append("Chybí platný Prev Close.")
        if act is None or act <= 0:
            issues.append("Chybí platná aktuální cena.")
        if d.get("pmh") and d.get("pml") and d["pmh"] < d["pml"]:
            issues.append(f"PMH (${d['pmh']:.2f}) < PML (${d['pml']:.2f}).")
        if d.get("reg_high") and d.get("reg_low") and d["reg_high"] < d["reg_low"]:
            issues.append(f"RTH High (${d['reg_high']:.2f}) < RTH Low (${d['reg_low']:.2f}).")
        if d.get("pm_vol", 0) == 0 and d.get("reg_vol", 0) == 0:
            issues.append("Nulový objem v Pre-Marketu i v hlavní seanci.")
        age = data_age_seconds(d)
        if age is not None and age > 15 * 60 and get_us_market_phase()[0] in ("PREMARKET", "REGULAR"):
            issues.append(f"Data jsou stará {int(age // 60)} min – stáhni je znovu.")
        if d.get("source") == "Scanner (watchlist.json)":
            issues.append("Ticker zatím nebyl obohacen živým dotazem z Yahoo (data pouze ze souboru watchlist.json).")
        return issues

    def copy_comprehensive_prompt(self):
        broker = self.broker_var.get()
        cap = self.account_size.get().strip() or "50000"
        risk = self.risk_pct.get().strip() or "1.0"

        now_ny = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S NY")
        now_cet = datetime.now(CET_TZ).strftime("%Y-%m-%d %H:%M:%S CET")
        phase_code, phase_label = get_us_market_phase()

        sym = (self.manual_ticker_entry.get().strip() or self.selected_symbol or "").upper()
        if sym and (sym not in self.items_map
                    or self.items_map[sym].get("source") == "Scanner (watchlist.json)"
                    or (data_age_seconds(self.items_map[sym]) or 0) > 60):
            try:
                live = self.fetch_yahoo_direct(sym)
                self.merge_live_with_scanner(sym, live)
                self.apply_table_filter()
                self.update_prediction_banner(sym, update_tm_entries=False)
            except Exception:
                pass

        if sym and sym in self.items_map:
            d = self.items_map[sym]
            calc = self.calculate_order_params(sym)
            sanity_issues = self._run_sanity_checks_for_prompt(d)
            sanity_str = "\n".join(f"  * [VAROVÁNÍ] {w}" for w in sanity_issues) if sanity_issues else "  * OK – Všechny sanity kontroly prošly (PM a RTH jsou striktně oddělené v NY čase)."

            stale_tag = f" [ZASTARALÉ – seance {d.get('session_date')}]" if d.get("stale_session") else ""

            if d.get("pm_price") is not None:
                pm_block = (
                    f"  - Pre-Market (04:00–09:30 NY){stale_tag}: Last={fmt_usd(d.get('pm_price'))} | "
                    f"PMH={fmt_usd(d.get('pmh'))} | PML={fmt_usd(d.get('pml'))} | "
                    f"PM VWAP={fmt_usd(d.get('pm_vwap'))} | PM Objem={d.get('pm_vol', 0):,} akcií"
                )
            else:
                pm_block = "  - Pre-Market (04:00–09:30 NY): Žádné pre-market obchody v datovém feedu"

            if d.get("reg_price") is not None:
                rth_block = (
                    f"  - Hlavní seance RTH (09:30–16:00 NY){stale_tag}: Open={fmt_usd(d.get('reg_open'))} | "
                    f"Last={fmt_usd(d.get('reg_price'))} | RTH High={fmt_usd(d.get('reg_high'))} | "
                    f"RTH Low={fmt_usd(d.get('reg_low'))} | RTH VWAP={fmt_usd(d.get('reg_vwap'))} | "
                    f"RTH Objem={d.get('reg_vol', 0):,} akcií"
                )
            else:
                rth_block = "  - Hlavní seance RTH (09:30–16:00 NY): Hlavní seance pro tento den ještě neotevřela"

            rvol_line = (
                f"{d['rvol']:.2f}x ({d.get('rvol_type', '')})"
                if d.get("rvol") is not None else
                f"Není k dispozici porovnatelný intradenní průměr ({d.get('rvol_type', 'N/A')})"
            )

            if calc.get("valid"):
                order_section = (
                    f"NÁVRH EXEKUČNÍHO POKYNU PRO {broker} (Kapitál {cap} CZK, Max Risk {risk}% = {calc['max_risk_czk']:.0f} CZK, ČNB kurz {calc['fx']:.3f} CZK/USD):\n"
                    f"  - Směr a typ pokynu : {calc['order_type']}\n"
                    f"  - Vstupní cena      : ${calc['entry']:.2f}\n"
                    f"  - Stop Loss (SL)    : ${calc['sl']:.2f} (Invalidace: {calc['invalidation_reason']})\n"
                    f"  - Take Profit       : TP1=${calc['tp1']:.2f} ({calc['tp1_r']:.1f}R) | TP2=${calc['tp2']:.2f} ({calc['tp2_r']:.1f}R)\n"
                    f"  - Počet celých kusů : {calc['shares']} ks (Frakční ekvivalent: {calc['fractional_shares']} ks)\n"
                    f"  - Hodnota pozice    : ${calc['pos_usd']:.2f} ({calc['pos_czk']:.0f} CZK)\n"
                    f"  - Skutečný risk     : ${calc['actual_risk_usd']:.2f} ({calc['actual_risk_czk']:.0f} CZK <= povolený limit {calc['max_risk_czk']:.0f} CZK)\n"
                )
                if calc.get("cap_warning"):
                    order_section += f"  - Poznámka k limitu : {calc['cap_warning']}\n"
            else:
                order_section = f"NÁVRH POKYNU: Nelze sestavit ({calc.get('reason')})\n"

            target_block = (
                f"DETAILNÍ AUDIT HLAVNÍ VYBRANÉ AKCIE: {sym}\n"
                f"  - Zdroj a čas dat   : {d.get('source')} | Staženo {d.get('fetched_at_ny')} ({d.get('fetched_at_cet')})\n"
                f"  - Sledovaná seance  : {d.get('session_date')} | Aktuální fáze trhu: {d.get('market_phase_label')}\n"
                f"  - Včerejší Close    : {fmt_usd(d.get('prev_close'))} (uzavřená denní svíčka ze dne {d.get('prev_close_date')})\n"
                f"  - Spočtený Gap %    : {fmt_pct(d.get('gap_pct'))} (Metodika: {d.get('gap_reference')})\n"
                f"{pm_block}\n"
                f"{rth_block}\n"
                f"  - Wilderův ATR(14)  : {fmt_usd(d.get('atr'))} (z uzavřených denních svíček) | 20d průměrný RTH objem: {fmt_int(d.get('avg_daily_vol'))} akcií\n"
                f"  - Relativní objem   : {rvol_line}\n"
                f"  - Technický scénář  : {d.get('prediction')}\n"
                f"  - Scanner setup     : {d.get('scanner_rule') or 'N/A'}\n\n"
                f"{order_section}\n"
                f"SANITY CHECK A KONTROLA DATOVÉ INTEGRITY:\n{sanity_str}\n\n"
            )
        else:
            target_block = "Uživatel nevybral konkrétní akcii – vyber 2 nejlepší setupy ze seřazeného seznamu níže.\n\n"

        def sort_key(item):
            has_live = 1 if "Yahoo" in str(item.get("source", "")) else 0
            rv = float(item.get("rvol") or 0.0)
            gp = abs(float(item.get("gap_pct") or 0.0))
            vl = int(item.get("pm_vol") or 0) + int(item.get("reg_vol") or 0)
            return (has_live, rv, gp, vl)

        sorted_items = sorted(self.items_map.values(), key=sort_key, reverse=True)[:20]
        clean_top = []
        for it in sorted_items:
            clean_top.append({
                "symbol": it.get("symbol"),
                "source": it.get("source"),
                "session_date": it.get("session_date"),
                "prev_close": round_tick(it.get("prev_close")),
                "prev_close_date": it.get("prev_close_date"),
                "gap_pct": round(it["gap_pct"], 2) if it.get("gap_pct") is not None else None,
                "pm_price": round_tick(it.get("pm_price")),
                "pmh": round_tick(it.get("pmh")),
                "pml": round_tick(it.get("pml")),
                "pm_vwap": round_tick(it.get("pm_vwap")),
                "pm_vol": it.get("pm_vol"),
                "reg_open": round_tick(it.get("reg_open")),
                "reg_price": round_tick(it.get("reg_price")),
                "reg_high": round_tick(it.get("reg_high")),
                "reg_low": round_tick(it.get("reg_low")),
                "reg_vwap": round_tick(it.get("reg_vwap")),
                "reg_vol": it.get("reg_vol"),
                "atr14_wilder": round_tick(it.get("atr")),
                "rvol": round(it["rvol"], 2) if it.get("rvol") is not None else None,
                "rvol_type": it.get("rvol_type"),
                "scenario": it.get("prediction"),
                "scanner_setups": it.get("scanner_setups"),
                "scanner_rsi": it.get("scanner_rsi"),
                "scanner_or_high": round_tick(it.get("scanner_or_high")),
                "scanner_or_low": round_tick(it.get("scanner_or_low")),
                "warnings": it.get("warnings", [])
            })

        clean_json = json.dumps(clean_top, indent=2, ensure_ascii=False)

        prompt = (
            f"Jsi seniorní intradenní trader a risk manažer. Zde jsou ověřená tržní data z Yahoo Finance API.\n"
            f"Aktuální čas exportu: {now_ny} / {now_cet} | Aktuální fáze trhu v USA: {phase_label}.\n"
            f"Všechna časová pásma jsou převedena do America/New_York a Pre-Market (04:00–09:30 NY) je striktně oddělen od hlavní seance RTH (09:30–16:00 NY).\n\n"
            f"{target_block}"
            f"TVŮJ ÚKOL:\n"
            f"1. Zhodnoť platnost technického scénáře (Gap & Go vs. Gap Fade vs. Konsolidace) na základě výše uvedených oddělených Pre-Market a RTH hladin.\n"
            f"2. Zkontroluj navržený pokyn pro brokera {broker} (Kapitál {cap} CZK, Max Risk {risk} %) – zda umístění Vstupu a Stop Lossu dává smysl vůči PMH, PML, VWAPu a Wilderovu ATR(14).\n"
            f"3. Pokud jsou v sekci SANITY CHECK uvedena varování nebo pokud se nacházíme v bearish Gap Fade scénáři, jasně napiš, proč se vyhnout Longu (případně jak nastavit čekající Buy Stop až nad klíčovou rezistenci).\n\n"
            f"SEŘAZENÝ PŘEHLED TOP 20 AKCIÍ (podle RVOL a Gap %):\n"
            f"{clean_json}\n"
        )

        self.root.clipboard_clear()
        self.root.clipboard_append(prompt)
        messagebox.showinfo("Zkopírováno!", f"Plně validovaný prompt pro {broker} (včetně oddělení PM/RTH a sanity kontrol) je ve schránce!\n\nStiskni Ctrl + V v Claude.")


if __name__ == "__main__":
    enforce_license_check()
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-check":
        ticker_arg = sys.argv[2] if len(sys.argv) >= 3 else "AAPL"
        self_check(ticker_arg)
        sys.exit(0)

    root = tk.Tk()
    app = DayTraderMasterStudio(root)
    root.mainloop()
