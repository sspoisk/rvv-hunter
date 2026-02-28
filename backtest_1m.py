#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — 1-Minute Candle Backtest
Бэктест на 1m свечах: scoring на агрегированных 5m, SL/TP/trailing на 1m.
В 5× точнее чем 5m/15m бэктест (окно ошибки 1 мин vs 5-15 мин).

Использование:
  python backtest_1m.py                           # config.json, 7 дней, топ-20
  python backtest_1m.py --days 3 --pairs 10       # 3 дня, топ-10
  python backtest_1m.py --sl 0.8 --tp 4.0         # override SL/TP
  python backtest_1m.py --grid                     # grid search SL/TP
  python backtest_1m.py --symbols BTC ETH SOL      # конкретные пары
"""

import ccxt
import time
import json
import os
import sys
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from itertools import product

# ─── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CACHE_DIR = os.path.join(BASE_DIR, 'data', 'candle_cache_1m')
RESULTS_PATH = os.path.join(BASE_DIR, 'data', 'backtest_1m_results.json')


# ─── Config ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── Indicators ─────────────────────────────────────────────────────────────

def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    """RSI array."""
    if len(prices) < period + 1:
        return [50.0] * len(prices)
    rsi_values = [50.0] * period
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    for i in range(period, len(prices)):
        avg_gain = sum(gains[i - period:i]) / period
        avg_loss = sum(losses[i - period:i]) / period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        rsi_values.append(rsi)
    return rsi_values


def calculate_bollinger(closes: List[float], period: int = 20, std_mult: float = 2.0):
    """Bollinger %B."""
    if len(closes) < period:
        return 50.0
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    price = closes[-1]
    if (upper - lower) > 0:
        return (price - lower) / (upper - lower) * 100
    return 50.0


def calculate_macd(closes: List[float]):
    """MACD histogram."""
    if len(closes) < 35:
        return 0, 0, 0
    def ema_series(data, period):
        result = [data[0]]
        k = 2 / (period + 1)
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_line = [ema12[i] - ema26[i] for i in range(len(closes))]
    signal_line = ema_series(macd_line, 9)
    histogram = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], histogram


def calculate_atr(highs, lows, closes, period=14):
    """ATR."""
    if len(closes) < period + 1:
        return closes[-1] * 0.02 if closes else 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def calculate_ema(data: List[float], period: int) -> float:
    """Single EMA value."""
    if not data or len(data) < period:
        return data[-1] if data else 0
    ema = data[0]
    k = 2 / (period + 1)
    for v in data[1:]:
        ema = v * k + ema * (1 - k)
    return ema


# ─── BTC Trend ──────────────────────────────────────────────────────────────

def calc_btc_trend_array(btc_5m: List[Dict]) -> Dict[int, Dict]:
    """BTC trend at each 5m candle. Keys = timestamp (ms) aligned to 5m."""
    trends = {}
    closes = [c['close'] for c in btc_5m]
    lookback_24h = 288  # 24h / 5m = 288
    start_idx = max(50, lookback_24h)
    for i in range(start_idx, len(btc_5m)):
        sma20 = sum(closes[i - 20:i]) / 20
        sma50 = sum(closes[i - 50:i]) / 50
        diff_pct = (sma20 - sma50) / sma50 * 100
        if diff_pct > 0.3:
            trend = 'bullish'
        elif diff_pct < -0.3:
            trend = 'bearish'
        else:
            trend = 'neutral'
        change_24h = (closes[i] - closes[i - lookback_24h]) / closes[i - lookback_24h] * 100
        ts = btc_5m[i]['timestamp']
        # Align to 5m
        ts_aligned = int(ts) - (int(ts) % 300000)
        trends[ts_aligned] = {'trend': trend, 'pct': abs(change_24h), 'change_24h': change_24h}
    return trends


def get_btc_trend_at(btc_trends: Dict, ts: int) -> Dict:
    """Get BTC trend for a timestamp."""
    default = {'trend': 'neutral', 'pct': 0.0, 'change_24h': 0.0}
    if not btc_trends:
        return default
    ts_aligned = int(ts) - (int(ts) % 300000)
    if ts_aligned in btc_trends:
        return btc_trends[ts_aligned]
    for offset in [300000, -300000, 600000, -600000]:
        if (ts_aligned + offset) in btc_trends:
            return btc_trends[ts_aligned + offset]
    return default


# ─── Data Loading ───────────────────────────────────────────────────────────

def get_exchange():
    """OKX ccxt exchange."""
    return ccxt.okx({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })


def fetch_1m_candles(exchange, symbol: str, days: int) -> List[Dict]:
    """Загрузить 1m свечи с OKX. Max 100 per request."""
    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    all_candles = []
    current_since = since
    request_count = 0

    while True:
        try:
            raw = exchange.fetch_ohlcv(symbol, '1m', since=current_since, limit=100)
        except Exception as e:
            print(f"    Ошибка {symbol}: {e}")
            break

        if not raw:
            break

        for c in raw:
            all_candles.append({
                'timestamp': c[0], 'open': c[1], 'high': c[2],
                'low': c[3], 'close': c[4], 'volume': c[5]
            })

        request_count += 1
        if len(raw) < 100:
            break
        current_since = raw[-1][0] + 1

        # Rate limit: OKX allows ~20 req/sec, be conservative
        if request_count % 10 == 0:
            time.sleep(0.5)
        else:
            time.sleep(0.1)

    return all_candles


def aggregate_1m_to_5m(candles_1m: List[Dict]) -> List[Dict]:
    """Агрегировать 1m свечи в 5m для расчёта индикаторов."""
    candles_5m = []
    # Group by 5-minute windows
    i = 0
    while i < len(candles_1m):
        ts = candles_1m[i]['timestamp']
        # Align to 5m boundary
        boundary = ts - (ts % 300000)
        group = []
        while i < len(candles_1m) and candles_1m[i]['timestamp'] < boundary + 300000:
            group.append(candles_1m[i])
            i += 1

        if group:
            candles_5m.append({
                'timestamp': boundary,
                'open': group[0]['open'],
                'high': max(c['high'] for c in group),
                'low': min(c['low'] for c in group),
                'close': group[-1]['close'],
                'volume': sum(c['volume'] for c in group),
            })
    return candles_5m


def load_data(exchange, symbols: List[str], days: int) -> Tuple[Dict[str, List[Dict]], Dict[str, List[Dict]]]:
    """Загрузить 1m свечи для всех символов + агрегировать в 5m.
    Кэширует в data/candle_cache_1m/.
    Returns: (candles_1m_by_symbol, candles_5m_by_symbol)
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    candles_1m = {}
    candles_5m = {}

    for idx, sym in enumerate(symbols):
        sym_clean = sym.replace('/USDT:USDT', '')
        cache_path = os.path.join(CACHE_DIR, f"{sym_clean}_1m_{days}d.json")

        # Check cache (valid for 4 hours)
        if os.path.exists(cache_path):
            try:
                age_h = (time.time() - os.path.getmtime(cache_path)) / 3600
                if age_h < 4:
                    with open(cache_path, 'r') as f:
                        data = json.load(f)
                    if len(data) >= 100:
                        candles_1m[sym_clean] = data
                        candles_5m[sym_clean] = aggregate_1m_to_5m(data)
                        continue
            except Exception:
                pass

        print(f"  [{idx + 1}/{len(symbols)}] Загрузка {sym_clean} 1m ({days}д)...", end='', flush=True)
        raw = fetch_1m_candles(exchange, sym, days)
        if len(raw) >= 100:
            candles_1m[sym_clean] = raw
            candles_5m[sym_clean] = aggregate_1m_to_5m(raw)
            # Cache
            with open(cache_path, 'w') as f:
                json.dump(raw, f)
            print(f" {len(raw)} свечей")
        else:
            print(f" пропуск ({len(raw)} свечей)")

        time.sleep(0.1)

    return candles_1m, candles_5m


def fetch_top_pairs(exchange, count: int) -> List[str]:
    """Получить топ пар по объёму."""
    tickers = exchange.fetch_tickers()
    pairs = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith('/USDT:USDT'):
            continue
        vol = ticker.get('quoteVolume') or 0
        if vol == 0 and ticker.get('baseVolume') and ticker.get('last'):
            vol = float(ticker['baseVolume']) * float(ticker['last'])
        if vol > 0:
            pairs.append({'symbol': symbol, 'volume': vol})

    pairs.sort(key=lambda x: x['volume'], reverse=True)
    return [p['symbol'] for p in pairs[:count]]


# ─── Scoring (5m based) ────────────────────────────────────────────────────

def compute_signals_5m(candles_5m: List[Dict], entry_filters: Dict, score_threshold: int = 3) -> List[Dict]:
    """Рассчитать scoring на каждой 5m свече. Возвращает список сигналов.
    Каждый сигнал: {ts, action, score, confidence, atr_pct, ...}
    """
    if len(candles_5m) < 50:
        return []

    closes = [c['close'] for c in candles_5m]
    highs = [c['high'] for c in candles_5m]
    lows = [c['low'] for c in candles_5m]
    volumes = [c['volume'] for c in candles_5m]
    rsi_values = calculate_rsi(closes, 14)

    signals = []
    lookback_24h = 288  # 5m

    for i in range(50, len(candles_5m)):
        rsi = rsi_values[i] if i < len(rsi_values) else 50

        # Bollinger %B (last 20 closes up to i)
        bb_pct = calculate_bollinger(closes[:i + 1], 20)

        # MACD
        macd_val, signal_val, macd_hist = calculate_macd(closes[:i + 1])

        # ATR
        atr = calculate_atr(highs[:i + 1], lows[:i + 1], closes[:i + 1], 14)
        atr_pct = (atr / closes[i] * 100) if closes[i] > 0 else 2.0

        # 24h change
        change_24h = 0.0
        if i >= lookback_24h:
            old = closes[i - lookback_24h]
            if old > 0:
                change_24h = (closes[i] - old) / old * 100

        # Volume ratio
        vol_ratio = 1.0
        if i >= 10 and volumes:
            avg_vol = sum(volumes[max(0, i - 20):i]) / max(min(20, i), 1)
            if avg_vol > 0:
                vol_ratio = volumes[i] / avg_vol

        # ── Entry Filters ──
        is_parabolic = False
        parabolic_ratio = 0.0
        if entry_filters.get('parabolic_enabled', True) and i >= 16:
            parabolic_mult = entry_filters.get('parabolic_multiplier', 3.0)
            ranges = [highs[j] - lows[j] for j in range(max(0, i - 15), i + 1)]
            current_range = ranges[-1]
            avg_range = sum(ranges[:-1]) / max(len(ranges) - 1, 1)
            if avg_range > 0:
                parabolic_ratio = current_range / avg_range
                if parabolic_ratio > parabolic_mult:
                    is_parabolic = True

        rvol_ok = True
        if entry_filters.get('rvol_enabled', True):
            min_rvol = entry_filters.get('min_rvol', 1.2)
            if vol_ratio < min_rvol:
                rvol_ok = False

        # Multi-TF: skip for now (we only have 5m, no separate 1h data in this mode)
        # We approximate 1h trend from 5m: EMA(20) on closes resampled to 1h
        trend_1h = None
        if entry_filters.get('multi_tf_enabled', True) and i >= 60:
            # Use every 12th 5m candle as ~1h proxy
            ema_period = entry_filters.get('multi_tf_ema_period', 20)
            hourly_closes = closes[max(0, i - 12 * ema_period):i + 1:12]
            if len(hourly_closes) >= ema_period:
                ema_val = calculate_ema(hourly_closes, ema_period)
                price_1h = hourly_closes[-1]
                ema_diff = (price_1h - ema_val) / ema_val * 100 if ema_val > 0 else 0
                if ema_diff > 0.3:
                    trend_1h = "UP"
                elif ema_diff < -0.3:
                    trend_1h = "DOWN"

        # ── Scoring ──
        score = 0
        # RSI (weight 3)
        if rsi >= 75:
            score += 3
        elif rsi >= 70:
            score += 2
        elif rsi <= 25:
            score -= 3
        elif rsi <= 30:
            score -= 2

        # BB %B (weight 2)
        if bb_pct >= 95:
            score += 2
        elif bb_pct >= 80:
            score += 1
        elif bb_pct <= 5:
            score -= 2
        elif bb_pct <= 20:
            score -= 1

        # MACD (weight 1)
        if macd_hist < 0 and score > 0:
            score += 1
        elif macd_hist > 0 and score < 0:
            score -= 1

        # 24h change (weight 1)
        if change_24h >= 8:
            score += 1
        elif change_24h <= -8:
            score -= 1

        # Decision
        action = "WAIT"
        confidence = 50.0
        if score >= score_threshold:
            action = "SHORT"
            confidence = min(55 + score * 5, 95)
        elif score <= -score_threshold:
            action = "LONG"
            confidence = min(55 + abs(score) * 5, 95)

        # Apply entry filters
        if action != "WAIT":
            if is_parabolic:
                action = "WAIT"
            elif not rvol_ok:
                action = "WAIT"
            elif trend_1h is not None:
                if action == "LONG" and trend_1h == "DOWN":
                    action = "WAIT"
                elif action == "SHORT" and trend_1h == "UP":
                    action = "WAIT"

        if action != "WAIT":
            signals.append({
                'ts': candles_5m[i]['timestamp'],
                'action': action,
                'score': score,
                'confidence': confidence,
                'atr_pct': atr_pct,
                'rsi': rsi,
                'bb_pct': bb_pct,
                'change_24h': change_24h,
                'price': closes[i],
            })

    return signals


# ─── Alternative Signal Strategies ────────────────────────────────────────

def compute_signals_rsi_only(candles_5m: List[Dict], entry_filters: Dict) -> List[Dict]:
    """Стратегия A: Только RSI. RSI>75→SHORT, RSI<25→LONG."""
    if len(candles_5m) < 50:
        return []
    closes = [c['close'] for c in candles_5m]
    highs = [c['high'] for c in candles_5m]
    lows = [c['low'] for c in candles_5m]
    rsi_values = calculate_rsi(closes, 14)
    atr_values = []
    for i in range(len(candles_5m)):
        atr_values.append(calculate_atr(highs[:i+1], lows[:i+1], closes[:i+1], 14))

    signals = []
    for i in range(50, len(candles_5m)):
        rsi = rsi_values[i] if i < len(rsi_values) else 50
        atr_pct = (atr_values[i] / closes[i] * 100) if closes[i] > 0 else 2.0
        action = "WAIT"
        if rsi >= 75:
            action = "SHORT"
        elif rsi <= 25:
            action = "LONG"
        if action != "WAIT":
            signals.append({
                'ts': candles_5m[i]['timestamp'], 'action': action,
                'score': 0, 'confidence': 70, 'atr_pct': atr_pct,
                'rsi': rsi, 'bb_pct': 50, 'change_24h': 0, 'price': closes[i],
            })
    return signals


def compute_signals_bb_only(candles_5m: List[Dict], entry_filters: Dict) -> List[Dict]:
    """Стратегия B: Только Bollinger Bands. BB%B>95→SHORT, BB%B<5→LONG."""
    if len(candles_5m) < 50:
        return []
    closes = [c['close'] for c in candles_5m]
    highs = [c['high'] for c in candles_5m]
    lows = [c['low'] for c in candles_5m]

    signals = []
    for i in range(50, len(candles_5m)):
        bb_pct = calculate_bollinger(closes[:i+1], 20)
        atr = calculate_atr(highs[:i+1], lows[:i+1], closes[:i+1], 14)
        atr_pct = (atr / closes[i] * 100) if closes[i] > 0 else 2.0
        action = "WAIT"
        if bb_pct >= 95:
            action = "SHORT"
        elif bb_pct <= 5:
            action = "LONG"
        if action != "WAIT":
            signals.append({
                'ts': candles_5m[i]['timestamp'], 'action': action,
                'score': 0, 'confidence': 70, 'atr_pct': atr_pct,
                'rsi': 50, 'bb_pct': bb_pct, 'change_24h': 0, 'price': closes[i],
            })
    return signals


def compute_signals_momentum(candles_5m: List[Dict], entry_filters: Dict) -> List[Dict]:
    """Стратегия C: Momentum (trend-following). RSI>60 + MACD>0 → LONG, RSI<40 + MACD<0 → SHORT."""
    if len(candles_5m) < 50:
        return []
    closes = [c['close'] for c in candles_5m]
    highs = [c['high'] for c in candles_5m]
    lows = [c['low'] for c in candles_5m]
    rsi_values = calculate_rsi(closes, 14)

    signals = []
    for i in range(50, len(candles_5m)):
        rsi = rsi_values[i] if i < len(rsi_values) else 50
        _, _, macd_hist = calculate_macd(closes[:i+1])
        atr = calculate_atr(highs[:i+1], lows[:i+1], closes[:i+1], 14)
        atr_pct = (atr / closes[i] * 100) if closes[i] > 0 else 2.0

        # EMA trend: price above EMA(20) = uptrend
        ema20 = calculate_ema(closes[max(0, i-30):i+1], 20)
        above_ema = closes[i] > ema20 if ema20 else False

        action = "WAIT"
        if rsi > 60 and macd_hist > 0 and above_ema:
            action = "LONG"
        elif rsi < 40 and macd_hist < 0 and not above_ema:
            action = "SHORT"
        if action != "WAIT":
            signals.append({
                'ts': candles_5m[i]['timestamp'], 'action': action,
                'score': 0, 'confidence': 70, 'atr_pct': atr_pct,
                'rsi': rsi, 'bb_pct': 50, 'change_24h': 0, 'price': closes[i],
            })
    return signals


def compute_signals_volume_spike(candles_5m: List[Dict], entry_filters: Dict) -> List[Dict]:
    """Стратегия D: Volume spike + direction. RVOL>2x + направление по последним 3 свечам."""
    if len(candles_5m) < 50:
        return []
    closes = [c['close'] for c in candles_5m]
    highs = [c['high'] for c in candles_5m]
    lows = [c['low'] for c in candles_5m]
    volumes = [c['volume'] for c in candles_5m]

    signals = []
    for i in range(50, len(candles_5m)):
        atr = calculate_atr(highs[:i+1], lows[:i+1], closes[:i+1], 14)
        atr_pct = (atr / closes[i] * 100) if closes[i] > 0 else 2.0

        # Volume ratio
        avg_vol = sum(volumes[max(0, i-20):i]) / max(min(20, i), 1)
        if avg_vol <= 0:
            continue
        vol_ratio = volumes[i] / avg_vol
        if vol_ratio < 2.0:
            continue

        # Direction: last 3 candles
        if i < 3:
            continue
        direction = closes[i] - closes[i-3]
        pct_move = abs(direction) / closes[i-3] * 100 if closes[i-3] > 0 else 0
        if pct_move < 0.5:  # need at least 0.5% move
            continue

        action = "WAIT"
        if direction > 0:
            # Price went up with volume → mean reversion SHORT
            action = "SHORT"
        else:
            # Price went down with volume → mean reversion LONG
            action = "LONG"

        if action != "WAIT":
            signals.append({
                'ts': candles_5m[i]['timestamp'], 'action': action,
                'score': 0, 'confidence': 70, 'atr_pct': atr_pct,
                'rsi': 50, 'bb_pct': 50, 'change_24h': 0, 'price': closes[i],
            })
    return signals


def compute_signals_volume_momentum(candles_5m: List[Dict], entry_filters: Dict) -> List[Dict]:
    """Стратегия E: Volume spike + momentum (trend-following). RVOL>2x → продолжение движения."""
    if len(candles_5m) < 50:
        return []
    closes = [c['close'] for c in candles_5m]
    highs = [c['high'] for c in candles_5m]
    lows = [c['low'] for c in candles_5m]
    volumes = [c['volume'] for c in candles_5m]

    signals = []
    for i in range(50, len(candles_5m)):
        atr = calculate_atr(highs[:i+1], lows[:i+1], closes[:i+1], 14)
        atr_pct = (atr / closes[i] * 100) if closes[i] > 0 else 2.0

        avg_vol = sum(volumes[max(0, i-20):i]) / max(min(20, i), 1)
        if avg_vol <= 0:
            continue
        vol_ratio = volumes[i] / avg_vol
        if vol_ratio < 2.0:
            continue

        if i < 3:
            continue
        direction = closes[i] - closes[i-3]
        pct_move = abs(direction) / closes[i-3] * 100 if closes[i-3] > 0 else 0
        if pct_move < 0.5:
            continue

        action = "WAIT"
        if direction > 0:
            action = "LONG"  # momentum: follow the move
        else:
            action = "SHORT"

        if action != "WAIT":
            signals.append({
                'ts': candles_5m[i]['timestamp'], 'action': action,
                'score': 0, 'confidence': 70, 'atr_pct': atr_pct,
                'rsi': 50, 'bb_pct': 50, 'change_24h': 0, 'price': closes[i],
            })
    return signals


STRATEGY_MAP = {
    'scoring': None,  # default, uses compute_signals_5m
    'rsi_only': compute_signals_rsi_only,
    'bb_only': compute_signals_bb_only,
    'momentum': compute_signals_momentum,
    'vol_reversal': compute_signals_volume_spike,
    'vol_momentum': compute_signals_volume_momentum,
}

STRATEGY_NAMES = {
    'scoring': 'Scoring (RSI+BB+MACD, score≥3)',
    'rsi_only': 'A: RSI only (>75/< 25)',
    'bb_only': 'B: BB only (%B>95/<5)',
    'momentum': 'C: Momentum (RSI+MACD+EMA trend-follow)',
    'vol_reversal': 'D: Volume spike + reversal',
    'vol_momentum': 'E: Volume spike + momentum',
}


# ─── Trade Simulation (1m resolution) ──────────────────────────────────────

def simulate_trades(candles_1m: List[Dict], signals: List[Dict],
                    sl_pct: float, tp_pct: float,
                    trailing_activation_pct: float, trailing_distance_pct: float,
                    position_size: float, leverage: int,
                    atr_adaptive_sl: bool = False,
                    atr_sl_multiplier: float = 1.5,
                    atr_trail_act_mult: float = 3.0,
                    atr_trail_dist_mult: float = 0.7,
                    commission_pct: float = 0.08, slippage_pct: float = 0.05,
                    max_positions: int = 10,
                    btc_trends: Dict = None, btc_modes: Dict = None,
                    side_filter: str = 'any',  # 'any', 'long_only', 'short_only'
                    breakeven_pct: float = 0.0,  # move SL to entry after +X%
                    time_stop_minutes: int = 0,  # close after N minutes if flat
                    ) -> Dict:
    """Симулировать сделки: вход по сигналам (5m scoring), выход проверяется на каждой 1m свече."""

    # Build 1m timestamp index for fast lookup
    ts_to_idx = {}
    for i, c in enumerate(candles_1m):
        ts_to_idx[c['timestamp']] = i

    trades = []
    positions = []  # active positions

    # Sort signals by time
    sorted_signals = sorted(signals, key=lambda s: s['ts'])

    # Apply side filter
    if side_filter == 'short_only':
        sorted_signals = [s for s in sorted_signals if s['action'] == 'SHORT']
    elif side_filter == 'long_only':
        sorted_signals = [s for s in sorted_signals if s['action'] == 'LONG']

    min_trail_profit_pct = max(0.3, trailing_activation_pct - trailing_distance_pct)

    for sig in sorted_signals:
        sig_ts = sig['ts']
        action = sig['action']

        # Find the 1m candle at or after signal time
        entry_idx = ts_to_idx.get(sig_ts)
        if entry_idx is None:
            # Find nearest 1m candle after signal
            for offset in range(0, 5 * 60000, 60000):  # within 5 minutes
                entry_idx = ts_to_idx.get(sig_ts + offset)
                if entry_idx is not None:
                    break
        if entry_idx is None:
            continue

        # Check max positions
        active = [p for p in positions if p.get('open', True)]
        if len(active) >= max_positions:
            continue

        # Check if same symbol already has position
        # (signals don't have symbol info in multi-mode, skip this check)

        # BTC filter
        if btc_trends and btc_modes:
            btc_info = get_btc_trend_at(btc_trends, sig_ts)
            btc_trend = btc_info['trend']
            btc_pct_val = btc_info['pct']
            if btc_trend == 'bullish':
                min_str = btc_modes.get('bullish_min_str', 0)
                mode = btc_modes.get('neutral', 'any') if (min_str > 0 and btc_pct_val < min_str) else btc_modes.get('bullish', 'any')
            elif btc_trend == 'bearish':
                min_str = btc_modes.get('bearish_min_str', 0)
                mode = btc_modes.get('neutral', 'any') if (min_str > 0 and btc_pct_val < min_str) else btc_modes.get('bearish', 'any')
            else:
                mode = btc_modes.get('neutral', 'any')
            if mode == 'none':
                continue
            if mode == 'long_only' and action == 'SHORT':
                continue
            if mode == 'short_only' and action == 'LONG':
                continue

        # ATR-adaptive SL/TP
        actual_sl = sl_pct
        actual_trail_act = trailing_activation_pct
        actual_trail_dist = trailing_distance_pct

        if atr_adaptive_sl:
            atr_pct = sig.get('atr_pct', 2.0)
            adaptive_sl = atr_pct * atr_sl_multiplier
            actual_sl = max(sl_pct, adaptive_sl)  # floor = config SL
            adaptive_trail_act = atr_pct * atr_trail_act_mult
            actual_trail_act = max(trailing_activation_pct, adaptive_trail_act)
            adaptive_trail_dist = atr_pct * atr_trail_dist_mult
            actual_trail_dist = max(trailing_distance_pct, adaptive_trail_dist)

        entry_price = candles_1m[entry_idx]['close']
        if action == 'SHORT':
            entry_price *= (1 - slippage_pct / 100)
        else:
            entry_price *= (1 + slippage_pct / 100)

        actual_min_trail_profit = max(0.3, actual_trail_act - actual_trail_dist)

        pos = {
            'side': action,
            'entry': entry_price,
            'entry_idx': entry_idx,
            'entry_ts': candles_1m[entry_idx]['timestamp'],
            'trailing_active': False,
            'best_price': entry_price,
            'sl_pct_used': actual_sl,
            'trail_act_used': actual_trail_act,
            'trail_dist_used': actual_trail_dist,
            'open': True,
        }

        if action == 'SHORT':
            pos['sl'] = entry_price * (1 + actual_sl / 100)
            pos['tp'] = entry_price * (1 - tp_pct / 100)
            pos['min_profit_sl'] = entry_price * (1 - actual_min_trail_profit / 100)
        else:
            pos['sl'] = entry_price * (1 - actual_sl / 100)
            pos['tp'] = entry_price * (1 + tp_pct / 100)
            pos['min_profit_sl'] = entry_price * (1 + actual_min_trail_profit / 100)

        positions.append(pos)

    # Now simulate all positions on 1m candles
    for pos in positions:
        entry_idx = pos['entry_idx']
        side = pos['side']
        entry_price = pos['entry']
        sl = pos['sl']
        tp = pos['tp']
        trailing_active = False
        breakeven_moved = False
        best_price = entry_price
        actual_trail_act = pos['trail_act_used']
        actual_trail_dist = pos['trail_dist_used']

        close_reason = None
        close_price = None
        close_ts = 0
        max_pnl_pct = 0.0

        # Walk 1m candles from entry
        for j in range(entry_idx + 1, len(candles_1m)):
            c = candles_1m[j]
            high = c['high']
            low = c['low']
            elapsed_min = (c['timestamp'] - pos['entry_ts']) / 60000

            # Time stop: close if position is flat after N minutes
            if time_stop_minutes > 0 and elapsed_min >= time_stop_minutes:
                mid = (high + low) / 2
                if side == 'SHORT':
                    flat_pnl = (entry_price - mid) / entry_price * 100
                else:
                    flat_pnl = (mid - entry_price) / entry_price * 100
                if abs(flat_pnl) < 0.3:  # within ±0.3% = flat
                    close_reason = 'TIME_STOP'
                    close_price = mid
                    close_ts = c['timestamp']
                    break

            # Track best price & trailing
            if side == 'SHORT':
                current_profit_pct = (entry_price - low) / entry_price * 100
                max_pnl_pct = max(max_pnl_pct, current_profit_pct)

                if low < best_price:
                    best_price = low

                # Break-even stop: move SL to entry after +X%
                if breakeven_pct > 0 and not breakeven_moved and not trailing_active:
                    if current_profit_pct >= breakeven_pct:
                        sl = entry_price  # move SL to breakeven
                        breakeven_moved = True

                if current_profit_pct >= actual_trail_act and not trailing_active:
                    trailing_active = True
                    new_sl = best_price * (1 + actual_trail_dist / 100)
                    if new_sl < entry_price:
                        sl = new_sl

                if trailing_active:
                    new_sl = best_price * (1 + actual_trail_dist / 100)
                    if new_sl < entry_price and new_sl < sl:
                        sl = new_sl

                # Check exit
                if high >= sl:
                    actual_pnl_pct = (entry_price - sl) / entry_price * 100
                    if trailing_active:
                        close_reason = 'TRAILING_STOP'
                    elif breakeven_moved:
                        close_reason = 'BREAKEVEN'
                    else:
                        close_reason = 'STOP_LOSS'
                    close_price = sl
                    close_ts = c['timestamp']
                    break
                if low <= tp:
                    close_reason = 'TAKE_PROFIT'
                    close_price = tp
                    close_ts = c['timestamp']
                    break
            else:  # LONG
                current_profit_pct = (high - entry_price) / entry_price * 100
                max_pnl_pct = max(max_pnl_pct, current_profit_pct)

                if high > best_price:
                    best_price = high

                # Break-even stop
                if breakeven_pct > 0 and not breakeven_moved and not trailing_active:
                    if current_profit_pct >= breakeven_pct:
                        sl = entry_price
                        breakeven_moved = True

                if current_profit_pct >= actual_trail_act and not trailing_active:
                    trailing_active = True
                    new_sl = best_price * (1 - actual_trail_dist / 100)
                    if new_sl > entry_price:
                        sl = new_sl

                if trailing_active:
                    new_sl = best_price * (1 - actual_trail_dist / 100)
                    if new_sl > entry_price and new_sl > sl:
                        sl = new_sl

                # Check exit
                if low <= sl:
                    actual_pnl_pct = (sl - entry_price) / entry_price * 100
                    if trailing_active:
                        close_reason = 'TRAILING_STOP'
                    elif breakeven_moved:
                        close_reason = 'BREAKEVEN'
                    else:
                        close_reason = 'STOP_LOSS'
                    close_price = sl
                    close_ts = c['timestamp']
                    break
                if high >= tp:
                    close_reason = 'TAKE_PROFIT'
                    close_price = tp
                    close_ts = c['timestamp']
                    break

        if close_reason is None:
            # Position still open at end of data
            close_price = candles_1m[-1]['close']
            close_ts = candles_1m[-1]['timestamp']
            if side == 'SHORT':
                actual_pnl_pct = (entry_price - close_price) / entry_price * 100
            else:
                actual_pnl_pct = (close_price - entry_price) / entry_price * 100
            close_reason = 'END_OF_DATA'
        else:
            if close_reason == 'TAKE_PROFIT':
                actual_pnl_pct = tp_pct if side == 'SHORT' else tp_pct
            else:
                if side == 'SHORT':
                    actual_pnl_pct = (entry_price - close_price) / entry_price * 100
                else:
                    actual_pnl_pct = (close_price - entry_price) / entry_price * 100

        pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
        comm = position_size * leverage * commission_pct / 100
        pnl_usd -= comm

        trades.append({
            'side': side,
            'entry_price': entry_price,
            'close_price': close_price,
            'entry_ts': pos['entry_ts'],
            'close_ts': close_ts,
            'pnl_pct': actual_pnl_pct,
            'pnl_usd': pnl_usd,
            'is_win': pnl_usd > 0,
            'close_reason': close_reason,
            'trailing_active': trailing_active,
            'max_pnl_pct': max_pnl_pct,
            'sl_pct_used': pos['sl_pct_used'],
            'duration_min': (close_ts - pos['entry_ts']) / 60000 if close_ts > 0 else 0,
        })

        pos['open'] = False

    # Aggregate results
    wins = sum(1 for t in trades if t['is_win'])
    losses_count = len(trades) - wins
    gross_profit = sum(t['pnl_usd'] for t in trades if t['pnl_usd'] > 0)
    gross_loss = sum(abs(t['pnl_usd']) for t in trades if t['pnl_usd'] <= 0)
    trailing_wins = sum(1 for t in trades if t['close_reason'] == 'TRAILING_STOP' and t['is_win'])

    return {
        'trades': len(trades),
        'wins': wins,
        'losses': losses_count,
        'win_rate': wins / len(trades) * 100 if trades else 0,
        'pnl': gross_profit - gross_loss,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'trailing_wins': trailing_wins,
        'avg_pnl_pct': sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0,
        'avg_duration_min': sum(t['duration_min'] for t in trades) / len(trades) if trades else 0,
        'max_pnl_pct': max((t['max_pnl_pct'] for t in trades), default=0),
        'trade_details': trades,
        'by_reason': {
            reason: {
                'count': sum(1 for t in trades if t['close_reason'] == reason),
                'pnl': sum(t['pnl_usd'] for t in trades if t['close_reason'] == reason),
            }
            for reason in set(t['close_reason'] for t in trades)
        },
        'by_side': {
            side: {
                'count': sum(1 for t in trades if t['side'] == side),
                'wins': sum(1 for t in trades if t['side'] == side and t['is_win']),
                'pnl': sum(t['pnl_usd'] for t in trades if t['side'] == side),
            }
            for side in ['LONG', 'SHORT']
        },
    }


# ─── Multi-symbol backtest ─────────────────────────────────────────────────

def run_backtest(candles_1m_all: Dict[str, List[Dict]],
                 candles_5m_all: Dict[str, List[Dict]],
                 cfg: Dict,
                 sl_override: float = None, tp_override: float = None,
                 trail_act_override: float = None, trail_dist_override: float = None,
                 btc_trends: Dict = None, btc_modes: Dict = None,
                 score_threshold: int = 3,
                 strategy: str = 'scoring',
                 side_filter: str = 'any',
                 breakeven_pct: float = 0.0,
                 time_stop_minutes: int = 0,
                 ) -> Dict:
    """Прогнать бэктест на всех символах."""
    trading = cfg.get('trading', {})
    entry_filters = cfg.get('entry_filters', {})

    sl_pct = sl_override or trading.get('stop_loss_pct', 0.8)
    tp_pct = tp_override or trading.get('take_profit_pct', 7.75)
    trail_act = trail_act_override or trading.get('trailing_activation_pct', 2.0)
    trail_dist = trail_dist_override or trading.get('trailing_distance_pct', 0.4)
    position_size = trading.get('position_size', 10)
    leverage = trading.get('leverage', 2)
    max_positions = trading.get('max_positions', 10)
    atr_adaptive = trading.get('atr_adaptive_sl', False)
    atr_sl_mult = trading.get('atr_sl_multiplier', 1.5)
    atr_trail_act_mult = trading.get('atr_trail_activation_multiplier', 3.0)
    atr_trail_dist_mult = trading.get('atr_trail_distance_multiplier', 0.7)

    all_trades = []

    for sym in candles_1m_all:
        if sym not in candles_5m_all:
            continue

        c1m = candles_1m_all[sym]
        c5m = candles_5m_all[sym]

        # Compute signals on 5m
        if strategy == 'scoring' or strategy not in STRATEGY_MAP:
            signals = compute_signals_5m(c5m, entry_filters, score_threshold=score_threshold)
        else:
            signals = STRATEGY_MAP[strategy](c5m, entry_filters)
        if not signals:
            continue

        # Simulate on 1m
        result = simulate_trades(
            c1m, signals,
            sl_pct=sl_pct, tp_pct=tp_pct,
            trailing_activation_pct=trail_act,
            trailing_distance_pct=trail_dist,
            position_size=position_size, leverage=leverage,
            atr_adaptive_sl=atr_adaptive,
            atr_sl_multiplier=atr_sl_mult,
            atr_trail_act_mult=atr_trail_act_mult,
            atr_trail_dist_mult=atr_trail_dist_mult,
            max_positions=999,  # per-symbol no limit, global limit below
            btc_trends=btc_trends, btc_modes=btc_modes,
            side_filter=side_filter,
            breakeven_pct=breakeven_pct,
            time_stop_minutes=time_stop_minutes,
        )

        for td in result.get('trade_details', []):
            td['symbol'] = sym
            all_trades.append(td)

    # Apply global position limit
    if max_positions > 0 and all_trades:
        all_trades.sort(key=lambda t: t.get('entry_ts', 0))
        accepted = []
        active_slots = []

        for trade in all_trades:
            open_time = trade.get('entry_ts', 0)
            close_time = trade.get('close_ts', 0)
            sym = trade.get('symbol', '')

            active_slots = [(ct, s) for ct, s in active_slots if ct > open_time]
            active_syms = {s for _, s in active_slots}

            if sym in active_syms:
                continue

            if len(active_slots) < max_positions:
                accepted.append(trade)
                active_slots.append((close_time, sym))

        all_trades = accepted

    # Aggregate
    wins = sum(1 for t in all_trades if t['is_win'])
    gross_profit = sum(t['pnl_usd'] for t in all_trades if t['pnl_usd'] > 0)
    gross_loss = sum(abs(t['pnl_usd']) for t in all_trades if t['pnl_usd'] <= 0)
    trailing_wins = sum(1 for t in all_trades if t.get('close_reason') == 'TRAILING_STOP' and t['is_win'])

    return {
        'trades': len(all_trades),
        'wins': wins,
        'losses': len(all_trades) - wins,
        'win_rate': wins / len(all_trades) * 100 if all_trades else 0,
        'pnl': gross_profit - gross_loss,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'trailing_wins': trailing_wins,
        'avg_duration_min': sum(t.get('duration_min', 0) for t in all_trades) / len(all_trades) if all_trades else 0,
        'by_reason': {
            reason: {
                'count': sum(1 for t in all_trades if t['close_reason'] == reason),
                'pnl': sum(t['pnl_usd'] for t in all_trades if t['close_reason'] == reason),
            }
            for reason in set(t['close_reason'] for t in all_trades)
        },
        'by_side': {
            side: {
                'count': sum(1 for t in all_trades if t['side'] == side),
                'wins': sum(1 for t in all_trades if t['side'] == side and t['is_win']),
                'pnl': sum(t['pnl_usd'] for t in all_trades if t['side'] == side),
            }
            for side in ['LONG', 'SHORT']
        },
        'params': {
            'sl': sl_pct, 'tp': tp_pct,
            'trail_act': trail_act, 'trail_dist': trail_dist,
            'score_threshold': score_threshold,
            'strategy': strategy, 'side_filter': side_filter,
            'breakeven_pct': breakeven_pct, 'time_stop_min': time_stop_minutes,
        },
        'trade_details': all_trades,
    }


# ─── Grid Search ────────────────────────────────────────────────────────────

def grid_search(candles_1m_all, candles_5m_all, cfg, btc_trends, btc_modes):
    """Grid search по SL/TP/Score threshold (trailing фиксирован из config)."""
    sl_values = [0.5, 0.8, 1.0, 1.5, 2.0]
    tp_values = [3.0, 4.0, 5.0, 7.75]
    score_thresholds = [3, 4, 5]

    combos = []
    for sl, tp, st in product(sl_values, tp_values, score_thresholds):
        if tp <= sl:
            continue
        combos.append((sl, tp, st))

    total = len(combos)
    print(f"\n[GRID] {total} комбинаций SL×TP×ScoreThreshold")

    results = []
    for idx, (sl, tp, st) in enumerate(combos):
        r = run_backtest(candles_1m_all, candles_5m_all, cfg,
                         sl_override=sl, tp_override=tp,
                         btc_trends=btc_trends, btc_modes=btc_modes,
                         score_threshold=st)
        results.append({
            'sl': sl, 'tp': tp, 'trail_act': r['params']['trail_act'],
            'trail_dist': r['params']['trail_dist'],
            'score_thr': st,
            'trades': r['trades'], 'wins': r['wins'],
            'win_rate': r['win_rate'], 'pnl': r['pnl'],
            'trailing_wins': r['trailing_wins'],
        })
        if (idx + 1) % 10 == 0:
            print(f"  {idx + 1}/{total}...", flush=True)

    results.sort(key=lambda x: x['pnl'], reverse=True)
    return results


# ─── Display ────────────────────────────────────────────────────────────────

def print_result(r: Dict, label: str = ""):
    """Красивый вывод результата."""
    if label:
        print(f"\n  === {label} ===")
    if r['trades'] == 0:
        print("  0 сделок")
        return

    print(f"  Сделок: {r['trades']} | Побед: {r['wins']} | Поражений: {r['losses']}")
    print(f"  Win Rate: {r['win_rate']:.1f}%")
    print(f"  PnL: {r['pnl']:+.2f}$ (profit {r['gross_profit']:.2f}$ / loss {r['gross_loss']:.2f}$)")
    print(f"  Trailing wins: {r['trailing_wins']}")

    if 'avg_duration_min' in r:
        print(f"  Avg duration: {r['avg_duration_min']:.0f} мин")

    if 'params' in r:
        p = r['params']
        print(f"  Params: SL={p['sl']}% TP={p['tp']}% Trail={p['trail_act']}/{p['trail_dist']}%")

    if 'by_reason' in r:
        print("  По причинам:")
        for reason, data in sorted(r['by_reason'].items(), key=lambda x: x[1]['pnl'], reverse=True):
            print(f"    {reason:>15}: {data['count']:>4} сделок | PnL {data['pnl']:+.2f}$")

    if 'by_side' in r:
        print("  По сторонам:")
        for side, data in r['by_side'].items():
            wr = data['wins'] / data['count'] * 100 if data['count'] > 0 else 0
            print(f"    {side:>6}: {data['count']:>4} сделок | WR {wr:.1f}% | PnL {data['pnl']:+.2f}$")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='RVV Hunter 1m Backtest')
    parser.add_argument('--days', type=int, default=7, help='Период в днях (default: 7)')
    parser.add_argument('--pairs', type=int, default=20, help='Кол-во пар (default: 20)')
    parser.add_argument('--symbols', nargs='+', help='Конкретные символы')
    parser.add_argument('--sl', type=float, help='Override SL%%')
    parser.add_argument('--tp', type=float, help='Override TP%%')
    parser.add_argument('--trail-act', type=float, help='Override trailing activation%%')
    parser.add_argument('--trail-dist', type=float, help='Override trailing distance%%')
    parser.add_argument('--grid', action='store_true', help='Grid search SL/TP')
    parser.add_argument('--compare', action='store_true', help='Сравнить все стратегии входа')
    parser.add_argument('--strategy', type=str, default='scoring', choices=list(STRATEGY_MAP.keys()),
                        help='Стратегия входа')
    parser.add_argument('--side', type=str, default='any', choices=['any', 'short_only', 'long_only'],
                        help='Фильтр стороны')
    parser.add_argument('--breakeven', type=float, default=0.0, help='Break-even stop после +X%%')
    parser.add_argument('--time-stop', type=int, default=0, help='Time stop после N минут')
    parser.add_argument('--no-cache', action='store_true', help='Игнорировать кэш')
    args = parser.parse_args()

    cfg = load_config()
    trading = cfg.get('trading', {})
    filters_cfg = cfg.get('filters', {})

    print("=" * 70)
    print("  RVV Hunter — 1-Minute Candle Backtest")
    print("=" * 70)
    print(f"  Период: {args.days} дней")
    print(f"  SL={args.sl or trading.get('stop_loss_pct', 0.8)}% "
          f"TP={args.tp or trading.get('take_profit_pct', 7.75)}% "
          f"Trail={trading.get('trailing_activation_pct', 2.0)}/{trading.get('trailing_distance_pct', 0.4)}%")
    print(f"  ATR-adaptive: {trading.get('atr_adaptive_sl', False)}")
    print()

    # Exchange
    exchange = get_exchange()

    # Get symbols
    if args.symbols:
        symbols = [f"{s.upper()}/USDT:USDT" for s in args.symbols]
    else:
        print("[1] Получение топ пар...")
        symbols = fetch_top_pairs(exchange, args.pairs)
        print(f"  {len(symbols)} пар получено")

    # Ensure BTC is included
    btc_sym = 'BTC/USDT:USDT'
    if btc_sym not in symbols:
        symbols.insert(0, btc_sym)

    # Clear cache if requested
    if args.no_cache and os.path.exists(CACHE_DIR):
        import shutil
        shutil.rmtree(CACHE_DIR)
        print("  Кэш очищен")

    # Load data
    print(f"\n[2] Загрузка 1m свечей ({len(symbols)} пар × {args.days}д)...")
    t0 = time.time()
    candles_1m_all, candles_5m_all = load_data(exchange, symbols, args.days)
    elapsed = time.time() - t0
    total_candles = sum(len(v) for v in candles_1m_all.values())
    print(f"  Загружено: {len(candles_1m_all)} пар, {total_candles:,} свечей за {elapsed:.0f}с")

    # BTC trends
    btc_5m = candles_5m_all.get('BTC', [])
    btc_trends = calc_btc_trend_array(btc_5m) if len(btc_5m) >= 300 else {}
    btc_modes = {
        'bullish': filters_cfg.get('btc_bullish_mode', 'short_only'),
        'bearish': filters_cfg.get('btc_bearish_mode', 'any'),
        'neutral': filters_cfg.get('btc_neutral_mode', 'any'),
        'bullish_min_str': float(filters_cfg.get('btc_bullish_min_strength', 0.5)),
        'bearish_min_str': float(filters_cfg.get('btc_bearish_min_strength', 0.5)),
    }
    if btc_trends:
        print(f"  BTC тренды: {len(btc_trends)} точек")

    # Run backtest
    print(f"\n[3] Бэктест (strategy={args.strategy}, side={args.side})...")
    t0 = time.time()
    result = run_backtest(
        candles_1m_all, candles_5m_all, cfg,
        sl_override=args.sl, tp_override=args.tp,
        trail_act_override=args.trail_act, trail_dist_override=args.trail_dist,
        btc_trends=btc_trends, btc_modes=btc_modes,
        strategy=args.strategy, side_filter=args.side,
        breakeven_pct=args.breakeven, time_stop_minutes=args.time_stop,
    )
    elapsed = time.time() - t0
    print(f"  Завершён за {elapsed:.1f}с")

    print_result(result, "BASELINE (текущие настройки)")

    # Compare all strategies
    if args.compare:
        print(f"\n[4] Сравнение стратегий...")
        t0 = time.time()
        strategies_results = []

        # Each strategy × side × breakeven combos
        sides = ['any', 'short_only']
        be_values = [0.0, 0.5, 1.0]
        ts_values = [0, 240]  # 0 = off, 240 min = 4h

        combos = []
        for strat in STRATEGY_MAP:
            for side in sides:
                for be in be_values:
                    for ts in ts_values:
                        combos.append((strat, side, be, ts))

        total = len(combos)
        print(f"  {total} комбинаций (стратегия × сторона × BE × TimeStop)")

        for idx, (strat, side, be, ts) in enumerate(combos):
            r = run_backtest(
                candles_1m_all, candles_5m_all, cfg,
                sl_override=args.sl, tp_override=args.tp,
                btc_trends=btc_trends, btc_modes=btc_modes,
                strategy=strat, side_filter=side,
                breakeven_pct=be, time_stop_minutes=ts,
            )
            strategies_results.append({
                'strategy': strat, 'side': side,
                'be': be, 'ts': ts,
                'trades': r['trades'], 'wins': r['wins'],
                'win_rate': r['win_rate'], 'pnl': r['pnl'],
                'trailing_wins': r['trailing_wins'],
                'by_reason': r.get('by_reason', {}),
            })
            if (idx + 1) % 12 == 0:
                print(f"  {idx + 1}/{total}...", flush=True)

        strategies_results.sort(key=lambda x: x['pnl'], reverse=True)
        elapsed = time.time() - t0
        print(f"  Сравнение за {elapsed:.0f}с")

        print(f"\n  Топ-20 комбинаций:")
        print(f"  {'#':>3}  {'Стратегия':<18}  {'Сторона':<12}  {'BE%':>4}  {'TS':>4}  "
              f"{'Trades':>6}  {'WR%':>6}  {'PnL':>10}  {'Trail':>5}")
        print("  " + "-" * 95)
        for i, r in enumerate(strategies_results[:20]):
            sname = r['strategy'][:16]
            print(f"  {i + 1:>3}  {sname:<18}  {r['side']:<12}  {r['be']:>4.1f}  {r['ts']:>4}  "
                  f"{r['trades']:>6}  {r['win_rate']:>5.1f}%  {r['pnl']:>+9.2f}$  "
                  f"{r['trailing_wins']:>5}")

        # Show worst too
        print(f"\n  Худшие 5:")
        for i, r in enumerate(strategies_results[-5:]):
            sname = r['strategy'][:16]
            print(f"  {len(strategies_results) - 4 + i:>3}  {sname:<18}  {r['side']:<12}  "
                  f"{r['be']:>4.1f}  {r['ts']:>4}  {r['trades']:>6}  {r['win_rate']:>5.1f}%  "
                  f"{r['pnl']:>+9.2f}$  {r['trailing_wins']:>5}")

        result['compare'] = strategies_results[:20]

    # Grid search
    if args.grid:
        print(f"\n[4] Grid Search SL/TP...")
        t0 = time.time()
        grid_results = grid_search(candles_1m_all, candles_5m_all, cfg, btc_trends, btc_modes)
        elapsed = time.time() - t0
        print(f"  Grid search за {elapsed:.0f}с")

        print(f"\n  Топ-20 комбинаций:")
        print(f"  {'#':>3}  {'SL':>5}  {'TP':>5}  {'TrA':>5}  {'TrD':>5}  {'Scr':>3}  {'Trades':>6}  {'WR%':>6}  {'PnL':>10}  {'Trail':>6}")
        print("  " + "-" * 75)
        for i, r in enumerate(grid_results[:20]):
            print(f"  {i + 1:>3}  {r['sl']:>5.1f}  {r['tp']:>5.1f}  {r.get('trail_act', 0):>5.1f}  "
                  f"{r.get('trail_dist', 0):>5.1f}  {r.get('score_thr', 3):>3}  {r['trades']:>6}  "
                  f"{r['win_rate']:>5.1f}%  {r['pnl']:>+9.2f}$  {r['trailing_wins']:>5}")

        result['grid_search'] = grid_results[:20]

    # Save results
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    save_data = {k: v for k, v in result.items() if k != 'trade_details'}
    save_data['timestamp'] = datetime.utcnow().isoformat()
    save_data['days'] = args.days
    save_data['pairs'] = len(candles_1m_all)

    with open(RESULTS_PATH, 'w') as f:
        json.dump(save_data, f, indent=2, default=str)
    print(f"\n  Результаты сохранены: {RESULTS_PATH}")

    print("\n" + "=" * 70)


if __name__ == '__main__':
    main()
