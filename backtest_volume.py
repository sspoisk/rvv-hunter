#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — Volume Confirmation Backtest
Тестирует: помогает ли объёмный фильтр отсечь плохие сделки?

Логика mean reversion + объём:
  SHORT (после пампа): объём должен ПАДАТЬ (exhaustion) или быть spike (climax)
  LONG (после дампа): объём должен ПАДАТЬ (selling exhaustion) или быть spike (capitulation)

Варианты фильтра:
  1. NO_FILTER — текущее поведение (baseline)
  2. DECLINING_VOLUME — вход только если объём падает (последние 5 < предыдущие 5)
  3. VOLUME_SPIKE — вход только если есть spike (max > avg × threshold)
  4. DECLINING_OR_SPIKE — объём падает ИЛИ есть spike
  5. LOW_VOLUME_RATIO — volume_trend < порога (объём не растёт агрессивно)
"""

import json
import os
import sys
import time
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'data', 'candle_cache')
RESULTS_PATH = os.path.join(BASE_DIR, 'data', 'backtest_volume_results.json')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

# ─── Загрузка конфига ────────────────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

CFG = load_config()
TRADING = CFG.get('trading', {})
FILTERS = CFG.get('filters', {})

PARAMS = {
    'sl_pct': TRADING.get('stop_loss_pct', 1.25),
    'tp_pct': TRADING.get('take_profit_pct', 7.75),
    'trailing_activation_pct': TRADING.get('trailing_activation_pct', 0.5),
    'trailing_distance_pct': TRADING.get('trailing_distance_pct', 0.05),
    'position_size': TRADING.get('position_size', 10),
    'leverage': TRADING.get('leverage', 2),
    'max_positions': TRADING.get('max_positions', 10),
    'commission_pct': 0.08,
    'slippage_pct': 0.05,
    'symbol_cooldown_candles': 2,
    'max_symbol_losses_daily': 2,
    'min_change_pct': TRADING.get('min_change_filter', 0),
    'btc_modes': {
        'bullish': FILTERS.get('btc_bullish_mode', 'short_only'),
        'bearish': FILTERS.get('btc_bearish_mode', 'any'),
        'neutral': FILTERS.get('btc_neutral_mode', 'any'),
        'bullish_min_str': float(FILTERS.get('btc_bullish_min_strength', 0.3)),
        'bearish_min_str': float(FILTERS.get('btc_bearish_min_strength', 0.3)),
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# ИНДИКАТОРЫ
# ═══════════════════════════════════════════════════════════════════════════════

def _calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
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


def _calc_volume_metrics(volumes: List[float], idx: int, lookback: int = 20) -> Dict:
    """Рассчитать метрики объёма для свечи idx"""
    if idx < lookback or len(volumes) < lookback:
        return {'avg': 0, 'trend': 0, 'spike': False, 'current': 0, 'declining': False}

    recent = volumes[idx - lookback:idx]
    avg_vol = sum(recent) / lookback
    current_vol = volumes[idx]

    # Тренд: последние 5 vs предыдущие 5
    if len(recent) >= 10:
        last5 = sum(recent[-5:]) / 5
        prev5 = sum(recent[-10:-5]) / 5
        trend_pct = (last5 - prev5) / prev5 * 100 if prev5 > 0 else 0
        declining = last5 < prev5
    else:
        trend_pct = 0
        declining = False

    # Spike: текущий объём > avg × threshold
    spike = current_vol > avg_vol * 2.5 if avg_vol > 0 else False

    return {
        'avg': avg_vol,
        'trend': trend_pct,
        'spike': spike,
        'current': current_vol,
        'declining': declining,
        'ratio': current_vol / avg_vol if avg_vol > 0 else 1.0
    }


def _calc_btc_trend_array(btc_candles: List[Dict]) -> Dict[int, Dict]:
    trends = {}
    closes = [c['close'] for c in btc_candles]
    lookback_24h = 96
    start_idx = max(50, lookback_24h)
    for i in range(start_idx, len(btc_candles)):
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
        ts = btc_candles[i].get('timestamp', btc_candles[i].get('time', 0))
        if isinstance(ts, (int, float)):
            ts = int(ts) - (int(ts) % 900000) if ts > 1e12 else int(ts * 1000) - (int(ts * 1000) % 900000)
        trends[ts] = {'trend': trend, 'pct': abs(change_24h), 'change_24h': change_24h}
    return trends


def _get_btc_trend_at(btc_trends: Dict, candle_ts: int) -> Dict:
    default = {'trend': 'neutral', 'pct': 0.0, 'change_24h': 0.0}
    if not btc_trends:
        return default
    if candle_ts > 1e12:
        ts = int(candle_ts) - (int(candle_ts) % 900000)
    else:
        ts = int(candle_ts * 1000) - (int(candle_ts * 1000) % 900000)
    if ts in btc_trends:
        return btc_trends[ts]
    for offset in [900000, -900000, 1800000, -1800000]:
        if (ts + offset) in btc_trends:
            return btc_trends[ts + offset]
    return default


# ═══════════════════════════════════════════════════════════════════════════════
# БЭКТЕСТ С ОБЪЁМНЫМ ФИЛЬТРОМ
# ═══════════════════════════════════════════════════════════════════════════════

def _backtest_symbol_volume(candles: List[Dict], volume_filter: str = 'none',
                            spike_threshold: float = 2.5,
                            max_trend_pct: float = 50.0,
                            btc_trends: Dict = None, btc_modes: Dict = None) -> Dict:
    """
    Бэктест одной монеты с объёмным фильтром.

    volume_filter:
      'none' — без фильтра (baseline)
      'declining' — вход только если объём падает
      'spike' — вход только если spike
      'declining_or_spike' — объём падает ИЛИ spike
      'no_rising' — блокировать если объём растёт > max_trend_pct%
    """
    sl_pct = PARAMS['sl_pct']
    tp_pct = PARAMS['tp_pct']
    trailing_activation_pct = PARAMS['trailing_activation_pct']
    trailing_distance_pct = PARAMS['trailing_distance_pct']
    position_size = PARAMS['position_size']
    leverage = PARAMS['leverage']
    commission_pct = PARAMS['commission_pct']
    slippage_pct = PARAMS['slippage_pct']
    symbol_cooldown_candles = PARAMS['symbol_cooldown_candles']
    max_symbol_losses_daily = PARAMS['max_symbol_losses_daily']
    min_change_pct = PARAMS['min_change_pct']

    min_trail_profit_pct = max(0.3, trailing_activation_pct - trailing_distance_pct)

    closes = [c['close'] for c in candles]
    volumes = [c.get('volume', 0) for c in candles]
    rsi_values = _calculate_rsi(closes, 14)

    lookback_24h = 96
    change_24h_values = [0.0] * len(candles)
    if min_change_pct > 0:
        for idx in range(lookback_24h, len(candles)):
            old_close = closes[idx - lookback_24h]
            if old_close > 0:
                change_24h_values[idx] = (closes[idx] - old_close) / old_close * 100

    trade_details = []
    cooldown_until_idx = 0
    daily_sl_count = 0
    current_day = ""
    position = None
    volume_blocked = 0  # Сколько сигналов заблокировал фильтр

    for i in range(20, len(candles)):
        rsi = rsi_values[i] if i < len(rsi_values) else 50
        current_price = candles[i]['close']
        high_price = candles[i]['high']
        low_price = candles[i]['low']

        if position is None:
            ts = candles[i].get('timestamp', candles[i].get('time', 0))
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        day = datetime.utcfromtimestamp(ts / 1000 if ts > 1e12 else ts).strftime('%Y-%m-%d')
                    else:
                        day = str(ts)[:10]
                    if day != current_day:
                        current_day = day
                        daily_sl_count = 0
                except Exception:
                    pass

            if i < cooldown_until_idx:
                continue
            if daily_sl_count >= max_symbol_losses_daily:
                continue

            # Определяем сигнал
            signal = None
            if rsi >= 70:
                if min_change_pct > 0 and change_24h_values[i] < min_change_pct:
                    pass
                else:
                    signal = 'SHORT'
            elif rsi <= 30:
                if min_change_pct > 0 and change_24h_values[i] > -min_change_pct:
                    pass
                else:
                    signal = 'LONG'

            if signal is None:
                continue

            # BTC фильтр
            if btc_trends and btc_modes:
                candle_ts = candles[i].get('timestamp', candles[i].get('time', 0))
                btc_info = _get_btc_trend_at(btc_trends, candle_ts)
                btc_trend = btc_info['trend']
                btc_pct = btc_info['pct']
                if btc_trend == 'bullish':
                    min_str = btc_modes.get('bullish_min_str', 0)
                    mode = btc_modes.get('neutral', 'any') if (min_str > 0 and btc_pct < min_str) else btc_modes.get('bullish', 'any')
                elif btc_trend == 'bearish':
                    min_str = btc_modes.get('bearish_min_str', 0)
                    mode = btc_modes.get('neutral', 'any') if (min_str > 0 and btc_pct < min_str) else btc_modes.get('bearish', 'any')
                else:
                    mode = btc_modes.get('neutral', 'any')
                if signal == 'SHORT' and mode in ('none', 'long_only'):
                    continue
                if signal == 'LONG' and mode in ('none', 'short_only'):
                    continue

            # ═══ ОБЪЁМНЫЙ ФИЛЬТР ═══
            if volume_filter != 'none':
                vol_metrics = _calc_volume_metrics(volumes, i)
                passed = False

                if volume_filter == 'declining':
                    passed = vol_metrics['declining']
                elif volume_filter == 'spike':
                    passed = vol_metrics['spike']
                elif volume_filter == 'declining_or_spike':
                    passed = vol_metrics['declining'] or vol_metrics['spike']
                elif volume_filter == 'no_rising':
                    passed = vol_metrics['trend'] < max_trend_pct

                if not passed:
                    volume_blocked += 1
                    continue

            # Открываем позицию
            if signal == 'SHORT':
                entry_price = current_price * (1 - slippage_pct / 100)
                position = {
                    'side': 'SHORT', 'entry': entry_price,
                    'sl': entry_price * (1 + sl_pct / 100),
                    'tp': entry_price * (1 - tp_pct / 100),
                    'trailing_active': False, 'best_price': current_price,
                    'min_profit_sl': entry_price * (1 - min_trail_profit_pct / 100),
                    'open_time': ts, 'candle_idx': i
                }
            else:
                entry_price = current_price * (1 + slippage_pct / 100)
                position = {
                    'side': 'LONG', 'entry': entry_price,
                    'sl': entry_price * (1 - sl_pct / 100),
                    'tp': entry_price * (1 + tp_pct / 100),
                    'trailing_active': False, 'best_price': current_price,
                    'min_profit_sl': entry_price * (1 + min_trail_profit_pct / 100),
                    'open_time': ts, 'candle_idx': i
                }
        else:
            # === TRAILING STOP ===
            if position['side'] == 'SHORT':
                current_profit_pct = (position['entry'] - low_price) / position['entry'] * 100
                if low_price < position['best_price']:
                    position['best_price'] = low_price
                if current_profit_pct >= trailing_activation_pct and not position['trailing_active']:
                    position['trailing_active'] = True
                    new_sl = position['best_price'] * (1 + trailing_distance_pct / 100)
                    if new_sl < position['entry']:
                        position['sl'] = new_sl
                if position['trailing_active']:
                    new_sl = position['best_price'] * (1 + trailing_distance_pct / 100)
                    if new_sl < position['entry'] and new_sl < position['sl']:
                        position['sl'] = new_sl
            else:
                current_profit_pct = (high_price - position['entry']) / position['entry'] * 100
                if high_price > position['best_price']:
                    position['best_price'] = high_price
                if current_profit_pct >= trailing_activation_pct and not position['trailing_active']:
                    position['trailing_active'] = True
                    new_sl = position['best_price'] * (1 - trailing_distance_pct / 100)
                    if new_sl > position['entry']:
                        position['sl'] = new_sl
                if position['trailing_active']:
                    new_sl = position['best_price'] * (1 - trailing_distance_pct / 100)
                    if new_sl > position['entry'] and new_sl > position['sl']:
                        position['sl'] = new_sl

            # === ПРОВЕРКА ВЫХОДА ===
            closed = False
            if position['side'] == 'SHORT':
                if high_price >= position['sl']:
                    actual_pnl_pct = (position['entry'] - position['sl']) / position['entry'] * 100
                    pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    reason = 'TRAILING_STOP' if position['trailing_active'] else 'STOP_LOSS'
                    closed = True
                elif low_price <= position['tp']:
                    pnl_usd = (tp_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    reason = 'TAKE_PROFIT'
                    closed = True
            else:
                if low_price <= position['sl']:
                    actual_pnl_pct = (position['sl'] - position['entry']) / position['entry'] * 100
                    pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    reason = 'TRAILING_STOP' if position['trailing_active'] else 'STOP_LOSS'
                    closed = True
                elif high_price >= position['tp']:
                    pnl_usd = (tp_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    reason = 'TAKE_PROFIT'
                    closed = True

            if closed:
                is_win = pnl_usd > 0
                trade_details.append({
                    'open_time': position.get('open_time', 0),
                    'close_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                    'side': position['side'], 'pnl': pnl_usd, 'is_win': is_win,
                    'close_reason': reason, 'trailing': position['trailing_active']
                })
                if not is_win and not position['trailing_active']:
                    cooldown_until_idx = i + symbol_cooldown_candles
                    daily_sl_count += 1
                position = None

    wins = sum(1 for t in trade_details if t['is_win'])
    losses_count = len(trade_details) - wins
    gp = sum(t['pnl'] for t in trade_details if t['pnl'] > 0)
    gl = sum(abs(t['pnl']) for t in trade_details if t['pnl'] <= 0)

    return {
        'trades': len(trade_details),
        'wins': wins,
        'losses': losses_count,
        'pnl': gp - gl,
        'gross_profit': gp,
        'gross_loss': gl,
        'win_rate': wins / len(trade_details) * 100 if trade_details else 0,
        'trailing_wins': sum(1 for t in trade_details if t.get('trailing') and t['is_win']),
        'volume_blocked': volume_blocked,
        'trade_details': trade_details
    }


def _apply_position_limit(all_trades: List[Dict], max_positions: int) -> Dict:
    """Лимит одновременных позиций"""
    if not all_trades:
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0,
                'gross_profit': 0, 'gross_loss': 0, 'skipped_signals': 0, 'trailing_wins': 0}

    sorted_trades = sorted(all_trades, key=lambda t: t.get('open_time', 0))
    accepted = []
    skipped = 0
    active_slots = []

    for trade in sorted_trades:
        open_time = trade.get('open_time', 0)
        close_time = trade.get('close_time', 0)
        symbol = trade.get('symbol', '')

        if open_time <= 0 or close_time <= 0:
            accepted.append(trade)
            continue

        active_slots = [(ct, sym) for ct, sym in active_slots if ct > open_time]
        active_syms = {sym for _, sym in active_slots}
        if symbol in active_syms:
            skipped += 1
            continue

        if len(active_slots) < max_positions:
            accepted.append(trade)
            active_slots.append((close_time, symbol))
        else:
            skipped += 1

    w = sum(1 for t in accepted if t.get('is_win', False))
    gp = sum(t['pnl'] for t in accepted if t.get('pnl', 0) > 0)
    gl = sum(abs(t['pnl']) for t in accepted if t.get('pnl', 0) <= 0)
    tw = sum(1 for t in accepted if t.get('close_reason') == 'TRAILING_STOP' and t.get('is_win'))
    return {
        'total_trades': len(accepted), 'wins': w, 'losses': len(accepted) - w,
        'total_pnl': gp - gl, 'gross_profit': gp, 'gross_loss': gl,
        'trailing_wins': tw, 'skipped_signals': skipped
    }


def run_volume_backtest(candles_by_symbol, btc_trends, btc_modes,
                        volume_filter='none', spike_threshold=2.5,
                        max_trend_pct=50.0) -> Dict:
    """Прогнать бэктест на всех монетах с объёмным фильтром."""
    all_trades = []
    total_blocked = 0

    for sym, candles in candles_by_symbol.items():
        res = _backtest_symbol_volume(
            candles, volume_filter=volume_filter,
            spike_threshold=spike_threshold, max_trend_pct=max_trend_pct,
            btc_trends=btc_trends, btc_modes=btc_modes
        )
        total_blocked += res['volume_blocked']
        for td in res['trade_details']:
            td['symbol'] = sym
            all_trades.append(td)

    # Применяем лимит позиций
    max_positions = PARAMS['max_positions']
    if max_positions > 0 and all_trades:
        limited = _apply_position_limit(all_trades, max_positions)
        result = {
            'trades': limited['total_trades'],
            'wins': limited['wins'],
            'losses': limited['losses'],
            'pnl': limited['total_pnl'],
            'gross_profit': limited['gross_profit'],
            'gross_loss': limited['gross_loss'],
            'win_rate': limited['wins'] / limited['total_trades'] * 100 if limited['total_trades'] > 0 else 0,
            'trailing_wins': limited.get('trailing_wins', 0),
            'skipped': limited.get('skipped_signals', 0),
            'volume_blocked': total_blocked
        }
    else:
        w = sum(1 for t in all_trades if t['is_win'])
        gp = sum(t['pnl'] for t in all_trades if t['pnl'] > 0)
        gl = sum(abs(t['pnl']) for t in all_trades if t['pnl'] <= 0)
        result = {
            'trades': len(all_trades), 'wins': w, 'losses': len(all_trades) - w,
            'pnl': gp - gl, 'gross_profit': gp, 'gross_loss': gl,
            'win_rate': w / len(all_trades) * 100 if all_trades else 0,
            'trailing_wins': 0, 'skipped': 0, 'volume_blocked': total_blocked
        }

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА ДАННЫХ ИЗ КЭША
# ═══════════════════════════════════════════════════════════════════════════════

def load_cached_data():
    """Загрузить данные из кэша — сканируем все файлы в candle_cache"""
    if not os.path.isdir(CACHE_DIR):
        print("❌ Кэш не найден. Сначала запустите full_backtest.py для загрузки данных.")
        sys.exit(1)

    candles_by_symbol = {}
    btc_candles = []

    for fname in os.listdir(CACHE_DIR):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        # Формат: BTC_USDT:USDT.json → символ BTC
        sym = fname.split('_USDT')[0]
        fpath = os.path.join(CACHE_DIR, fname)
        with open(fpath, 'r') as f:
            data = json.load(f)
        if sym == 'BTC':
            btc_candles = data
        else:
            candles_by_symbol[sym] = data

    print(f"  Загружено: {len(candles_by_symbol)} монет, BTC: {len(btc_candles)} свечей")
    return candles_by_symbol, btc_candles


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  RVV HUNTER — VOLUME CONFIRMATION BACKTEST")
    print("=" * 70)
    print(f"\nПараметры: SL={PARAMS['sl_pct']}%, TP={PARAMS['tp_pct']}%, "
          f"Trail={PARAMS['trailing_activation_pct']}/{PARAMS['trailing_distance_pct']}%, "
          f"Size={PARAMS['position_size']}$×{PARAMS['leverage']}x, "
          f"MaxPos={PARAMS['max_positions']}")

    print("\n📦 Фаза 1: Загрузка данных из кэша...")
    candles_by_symbol, btc_candles = load_cached_data()

    print("  Расчёт BTC тренда...")
    btc_trends = _calc_btc_trend_array(btc_candles) if btc_candles else {}
    btc_modes = PARAMS['btc_modes']

    # ═══════════════════════════════════════════════════════════════════════
    # ТЕСТЫ
    # ═══════════════════════════════════════════════════════════════════════

    tests = [
        # (name, volume_filter, spike_threshold, max_trend_pct)
        ("BASELINE (без фильтра)", "none", 2.5, 50),
        ("DECLINING — только падающий объём", "declining", 2.5, 50),
        ("SPIKE — только при spike (>2.5x)", "spike", 2.5, 50),
        ("SPIKE (>2.0x)", "spike", 2.0, 50),
        ("SPIKE (>3.0x)", "spike", 3.0, 50),
        ("DECLINING OR SPIKE (2.5x)", "declining_or_spike", 2.5, 50),
        ("DECLINING OR SPIKE (2.0x)", "declining_or_spike", 2.0, 50),
        ("NO RISING (trend < 0%)", "no_rising", 2.5, 0),
        ("NO RISING (trend < 10%)", "no_rising", 2.5, 10),
        ("NO RISING (trend < 20%)", "no_rising", 2.5, 20),
        ("NO RISING (trend < 30%)", "no_rising", 2.5, 30),
        ("NO RISING (trend < 50%)", "no_rising", 2.5, 50),
    ]

    results = []
    print(f"\n{'='*90}")
    print(f"{'Фильтр':<38} {'Сделок':>6} {'Win%':>6} {'PnL':>10} {'Blocked':>8} {'Profit':>10} {'Loss':>10}")
    print(f"{'='*90}")

    for name, vf, spike_th, max_trend in tests:
        t0 = time.time()
        res = run_volume_backtest(candles_by_symbol, btc_trends, btc_modes,
                                  volume_filter=vf, spike_threshold=spike_th,
                                  max_trend_pct=max_trend)
        dt = time.time() - t0

        marker = ""
        results.append({
            'name': name, 'filter': vf, 'spike_threshold': spike_th,
            'max_trend_pct': max_trend, **res
        })

        print(f"  {name:<36} {res['trades']:>6} {res['win_rate']:>5.1f}% "
              f"{res['pnl']:>+9.2f}$ {res['volume_blocked']:>7} "
              f"{res['gross_profit']:>+9.2f}$ {res['gross_loss']:>9.2f}$ "
              f"({dt:.1f}s)")

    # Найти лучший
    baseline = results[0]
    best = max(results, key=lambda r: r['pnl'])

    print(f"\n{'='*70}")
    print(f"  BASELINE:  {baseline['trades']} сделок | WR {baseline['win_rate']:.1f}% | PnL {baseline['pnl']:+.2f}$")
    print(f"  ЛУЧШИЙ:    {best['name']}")
    print(f"             {best['trades']} сделок | WR {best['win_rate']:.1f}% | PnL {best['pnl']:+.2f}$")
    diff = best['pnl'] - baseline['pnl']
    diff_pct = diff / abs(baseline['pnl']) * 100 if baseline['pnl'] != 0 else 0
    print(f"  РАЗНИЦА:   {diff:+.2f}$ ({diff_pct:+.1f}%)")
    print(f"  Blocked:   {best['volume_blocked']} сигналов отфильтровано")

    # Анализ: какие сделки фильтр убирает — больше лоссов или винов?
    print(f"\n{'='*70}")
    print("  АНАЛИЗ: ЧТО ФИЛЬТР УБИРАЕТ?")
    print(f"{'='*70}")
    if baseline['trades'] > 0 and best['filter'] != 'none':
        removed_trades = baseline['trades'] - best['trades']
        removed_wins = baseline['wins'] - best['wins']
        removed_losses = baseline['losses'] - best['losses']
        removed_profit = baseline['gross_profit'] - best['gross_profit']
        removed_loss = baseline['gross_loss'] - best['gross_loss']
        print(f"  Убрано сделок: {removed_trades} (wins: {removed_wins}, losses: {removed_losses})")
        print(f"  Убрано прибыли: {removed_profit:+.2f}$, убрано убытков: {removed_loss:.2f}$")
        if removed_trades > 0:
            removed_wr = removed_wins / removed_trades * 100
            print(f"  WR убранных сделок: {removed_wr:.1f}% (vs baseline {baseline['win_rate']:.1f}%)")
            if removed_wr < baseline['win_rate']:
                print(f"  ✅ Фильтр убирает ПЛОХИЕ сделки (WR убранных ниже)")
            else:
                print(f"  ⚠️ Фильтр убирает ХОРОШИЕ сделки (WR убранных выше)")

    # Сохранение
    with open(RESULTS_PATH, 'w') as f:
        json.dump({
            'results': results,
            'baseline': results[0],
            'best': best,
            'params': PARAMS,
            'timestamp': datetime.utcnow().isoformat()
        }, f, indent=2)
    print(f"\n💾 Результаты сохранены: {RESULTS_PATH}")


if __name__ == '__main__':
    main()
