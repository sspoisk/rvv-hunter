#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — Бэктест: ATR multiplier для SL
Сравнивает:
  - Фиксированный SL (без ATR)
  - ATR × 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5
  - ATR с разными floor (min SL)
"""

import json
import os
import sys
import numpy as np
from datetime import datetime
from typing import List, Dict
from itertools import product

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

CACHE_DIR = os.path.join(BASE_DIR, 'data', 'candle_cache')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

with open(CONFIG_PATH, 'r') as f:
    CFG = json.load(f)

TRADING = CFG.get('trading', {})
FILTERS = CFG.get('filters', {})

# Текущие оптимизированные параметры (из бэктеста)
BASE_PARAMS = {
    'sl_pct': TRADING.get('stop_loss_pct', 1.25),
    'tp_pct': TRADING.get('take_profit_pct', 7.75),
    'trailing_activation_pct': TRADING.get('trailing_activation_pct', 0.5),
    'trailing_distance_pct': TRADING.get('trailing_distance_pct', 0.05),
    'position_size': 25,
    'leverage': 5,
    'max_positions': 10,
    'rsi_overbought': 70,
    'rsi_oversold': 30,
    'commission_pct': 0.08,
    'slippage_pct': 0.05,
    'symbol_cooldown_candles': 2,
    'max_symbol_losses_daily': 2,
    'btc_modes': {
        'bullish': FILTERS.get('btc_bullish_mode', 'short_only'),
        'bearish': FILTERS.get('btc_bearish_mode', 'any'),
        'neutral': FILTERS.get('btc_neutral_mode', 'any'),
        'bullish_min_str': float(FILTERS.get('btc_bullish_min_strength', 0.3)),
        'bearish_min_str': float(FILTERS.get('btc_bearish_min_strength', 0.3)),
    },
}


# ─── Копии функций из full_backtest.py ───

def _calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return [50.0] * len(prices)
    rsi_values = [50.0] * period
    gains, losses = [], []
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
            rsi = 100 - (100 / (1 + avg_gain / avg_loss))
        rsi_values.append(rsi)
    return rsi_values


def _calculate_atr(candles, period=14):
    """ATR для каждой свечи — True Range, period-SMA"""
    atr_values = [0.0] * len(candles)
    if len(candles) < period + 1:
        return atr_values
    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]['high']
        low = candles[i]['low']
        prev_close = candles[i - 1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    for i in range(period, len(candles)):
        atr = sum(true_ranges[i - period:i]) / period
        atr_pct = (atr / candles[i]['close']) * 100 if candles[i]['close'] > 0 else 0
        atr_values[i] = atr_pct
    return atr_values


def _calc_btc_trend_array(btc_candles):
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


def _get_btc_trend_at(btc_trends, candle_ts):
    default = {'trend': 'neutral', 'pct': 0.0, 'change_24h': 0.0}
    if not btc_trends:
        return default
    ts = int(candle_ts) - (int(candle_ts) % 900000) if candle_ts > 1e12 else int(candle_ts * 1000) - (int(candle_ts * 1000) % 900000)
    if ts in btc_trends:
        return btc_trends[ts]
    for offset in [900000, -900000, 1800000, -1800000]:
        if (ts + offset) in btc_trends:
            return btc_trends[ts + offset]
    return default


def _backtest_symbol_atr(candles, sl_pct_floor, tp_pct, atr_multiplier,
                         trailing_activation_pct, trailing_distance_pct,
                         rsi_short=70, rsi_long=30,
                         position_size=25, leverage=5,
                         commission_pct=0.08, slippage_pct=0.05,
                         symbol_cooldown_candles=2, max_symbol_losses_daily=2,
                         btc_trends=None, btc_modes=None):
    """
    Бэктест с ATR-адаптивным SL.
    atr_multiplier=0 → фиксированный SL (sl_pct_floor)
    atr_multiplier>0 → SL = max(sl_pct_floor, ATR × atr_multiplier)
    """
    trades = 0
    wins = 0
    losses_count = 0
    gross_profit = 0.0
    gross_loss = 0.0
    trailing_wins = 0
    total_commission = 0.0
    sl_values_used = []  # для статистики

    trailing_enabled = True
    min_trail_profit_pct = max(0.3, trailing_activation_pct - trailing_distance_pct)

    closes = [c['close'] for c in candles]
    rsi_values = _calculate_rsi(closes, 14)
    atr_values = _calculate_atr(candles, 14)

    cooldown_until_idx = 0
    daily_sl_count = 0
    current_day = ""
    position = None

    for i in range(20, len(candles)):
        rsi = rsi_values[i] if i < len(rsi_values) else 50
        current_price = candles[i]['close']
        high_price = candles[i]['high']
        low_price = candles[i]['low']

        if position is None:
            ts = candles[i].get('timestamp', candles[i].get('time', 0))
            if ts:
                try:
                    day = datetime.utcfromtimestamp(ts / 1000 if ts > 1e12 else ts).strftime('%Y-%m-%d')
                    if day != current_day:
                        current_day = day
                        daily_sl_count = 0
                except Exception:
                    pass

            if i < cooldown_until_idx:
                continue
            if daily_sl_count >= max_symbol_losses_daily:
                continue

            # Вычислить ATR-based SL для текущей свечи
            atr_pct = atr_values[i]
            if atr_multiplier > 0 and atr_pct > 0:
                sl_pct = max(sl_pct_floor, atr_pct * atr_multiplier)
            else:
                sl_pct = sl_pct_floor

            # SHORT
            if rsi >= rsi_short:
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
                    if mode == 'none' or mode == 'long_only':
                        continue

                entry_price = current_price * (1 - slippage_pct / 100)
                sl_values_used.append(sl_pct)
                position = {
                    'side': 'SHORT', 'entry': entry_price,
                    'sl': entry_price * (1 + sl_pct / 100),
                    'tp': entry_price * (1 - tp_pct / 100),
                    'sl_pct': sl_pct,
                    'trailing_active': False, 'best_price': current_price,
                    'min_profit_sl': entry_price * (1 - min_trail_profit_pct / 100),
                }

            # LONG
            elif rsi <= rsi_long:
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
                    if mode == 'none' or mode == 'short_only':
                        continue

                entry_price = current_price * (1 + slippage_pct / 100)
                sl_values_used.append(sl_pct)
                position = {
                    'side': 'LONG', 'entry': entry_price,
                    'sl': entry_price * (1 - sl_pct / 100),
                    'tp': entry_price * (1 + tp_pct / 100),
                    'sl_pct': sl_pct,
                    'trailing_active': False, 'best_price': current_price,
                    'min_profit_sl': entry_price * (1 + min_trail_profit_pct / 100),
                }

        else:
            # TRAILING STOP
            if trailing_enabled:
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

            # ПРОВЕРКА ВЫХОДА
            if position['side'] == 'SHORT':
                if high_price >= position['sl']:
                    actual_pnl_pct = (position['entry'] - position['sl']) / position['entry'] * 100
                    pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    total_commission += comm
                    if pnl_usd > 0:
                        gross_profit += pnl_usd
                        wins += 1
                        if position['trailing_active']:
                            trailing_wins += 1
                    else:
                        gross_loss += abs(pnl_usd)
                        losses_count += 1
                    trades += 1
                    if pnl_usd <= 0 and not position['trailing_active']:
                        cooldown_until_idx = i + symbol_cooldown_candles
                        daily_sl_count += 1
                    position = None
                elif low_price <= position['tp']:
                    pnl_usd = (tp_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    total_commission += comm
                    gross_profit += pnl_usd
                    wins += 1
                    trades += 1
                    position = None
            else:  # LONG
                if low_price <= position['sl']:
                    actual_pnl_pct = (position['sl'] - position['entry']) / position['entry'] * 100
                    pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    total_commission += comm
                    if pnl_usd > 0:
                        gross_profit += pnl_usd
                        wins += 1
                        if position['trailing_active']:
                            trailing_wins += 1
                    else:
                        gross_loss += abs(pnl_usd)
                        losses_count += 1
                    trades += 1
                    if pnl_usd <= 0 and not position['trailing_active']:
                        cooldown_until_idx = i + symbol_cooldown_candles
                        daily_sl_count += 1
                    position = None
                elif high_price >= position['tp']:
                    pnl_usd = (tp_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    total_commission += comm
                    gross_profit += pnl_usd
                    wins += 1
                    trades += 1
                    position = None

    net_pnl = gross_profit - gross_loss
    win_rate = (wins / trades * 100) if trades > 0 else 0
    avg_sl = np.mean(sl_values_used) if sl_values_used else sl_pct_floor

    return {
        'trades': trades, 'wins': wins, 'losses': losses_count,
        'net_pnl': net_pnl, 'gross_profit': gross_profit, 'gross_loss': gross_loss,
        'win_rate': win_rate, 'trailing_wins': trailing_wins,
        'commission': total_commission, 'avg_sl_used': avg_sl,
    }


def load_cached_candles():
    """Загрузить кэшированные свечи"""
    if not os.path.exists(CACHE_DIR):
        print(f"  ОШИБКА: кэш не найден: {CACHE_DIR}")
        print("  Сначала запусти full_backtest.py для загрузки данных")
        sys.exit(1)

    files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.json')]
    print(f"  Найдено {len(files)} файлов в кэше")

    all_candles = {}
    btc_candles = None

    for fname in files:
        symbol = fname.replace('.json', '').replace('_', '/')
        fpath = os.path.join(CACHE_DIR, fname)
        try:
            with open(fpath, 'r') as f:
                data = json.load(f)
            if len(data) < 100:
                continue
            if 'BTC' in symbol:
                btc_candles = data
            else:
                all_candles[symbol] = data
        except Exception:
            continue

    print(f"  Загружено {len(all_candles)} монет, BTC: {'да' if btc_candles else 'нет'}")
    return all_candles, btc_candles


def run_variant(all_candles, btc_trends, label, sl_floor, atr_mult):
    """Прогнать один вариант по всем монетам"""
    total = {'trades': 0, 'wins': 0, 'losses': 0, 'net_pnl': 0, 'gross_profit': 0,
             'gross_loss': 0, 'trailing_wins': 0, 'commission': 0, 'sl_values': []}

    for symbol, candles in all_candles.items():
        r = _backtest_symbol_atr(
            candles,
            sl_pct_floor=sl_floor,
            tp_pct=BASE_PARAMS['tp_pct'],
            atr_multiplier=atr_mult,
            trailing_activation_pct=BASE_PARAMS['trailing_activation_pct'],
            trailing_distance_pct=BASE_PARAMS['trailing_distance_pct'],
            rsi_short=BASE_PARAMS['rsi_overbought'],
            rsi_long=BASE_PARAMS['rsi_oversold'],
            position_size=BASE_PARAMS['position_size'],
            leverage=BASE_PARAMS['leverage'],
            commission_pct=BASE_PARAMS['commission_pct'],
            slippage_pct=BASE_PARAMS['slippage_pct'],
            symbol_cooldown_candles=BASE_PARAMS['symbol_cooldown_candles'],
            max_symbol_losses_daily=BASE_PARAMS['max_symbol_losses_daily'],
            btc_trends=btc_trends,
            btc_modes=BASE_PARAMS['btc_modes'],
        )
        total['trades'] += r['trades']
        total['wins'] += r['wins']
        total['losses'] += r['losses']
        total['net_pnl'] += r['net_pnl']
        total['gross_profit'] += r['gross_profit']
        total['gross_loss'] += r['gross_loss']
        total['trailing_wins'] += r['trailing_wins']
        total['commission'] += r['commission']
        total['sl_values'].append(r['avg_sl_used'])

    win_rate = (total['wins'] / total['trades'] * 100) if total['trades'] > 0 else 0
    avg_sl = np.mean(total['sl_values']) if total['sl_values'] else sl_floor
    profit_factor = (total['gross_profit'] / total['gross_loss']) if total['gross_loss'] > 0 else 999

    return {
        'label': label, 'sl_floor': sl_floor, 'atr_mult': atr_mult,
        'trades': total['trades'], 'wins': total['wins'], 'losses': total['losses'],
        'net_pnl': total['net_pnl'], 'win_rate': win_rate,
        'avg_sl': avg_sl, 'profit_factor': profit_factor,
        'gross_profit': total['gross_profit'], 'gross_loss': total['gross_loss'],
        'trailing_wins': total['trailing_wins'], 'commission': total['commission'],
    }


def main():
    print("=" * 70)
    print("  RVV Hunter — Бэктест ATR Multiplier для SL")
    print("=" * 70)

    # Загрузка
    print("\n[1] Загрузка кэша...")
    all_candles, btc_candles = load_cached_candles()

    btc_trends = {}
    if btc_candles:
        btc_trends = _calc_btc_trend_array(btc_candles)
        print(f"  BTC тренды: {len(btc_trends)} точек")

    # Варианты для теста
    variants = [
        # (label, sl_floor, atr_multiplier)
        # Группа 1: Фиксированный SL (без ATR)
        ("FIXED SL=1.25%",         1.25, 0),
        ("FIXED SL=2.0%",          2.0,  0),
        ("FIXED SL=3.0%",          3.0,  0),
        ("FIXED SL=4.0%",          4.0,  0),

        # Группа 2: ATR × multiplier, floor=1.25% (текущая логика бота)
        ("ATR×0.5, floor=1.25%",   1.25, 0.5),
        ("ATR×0.75, floor=1.25%",  1.25, 0.75),
        ("ATR×1.0, floor=1.25%",   1.25, 1.0),
        ("ATR×1.25, floor=1.25%",  1.25, 1.25),
        ("ATR×1.5, floor=1.25%",   1.25, 1.5),   # ← ТЕКУЩАЯ НАСТРОЙКА БОТА
        ("ATR×2.0, floor=1.25%",   1.25, 2.0),

        # Группа 3: ATR × multiplier, floor=0 (чистый ATR)
        ("ATR×0.5, no floor",      0.1,  0.5),
        ("ATR×0.75, no floor",     0.1,  0.75),
        ("ATR×1.0, no floor",      0.1,  1.0),
        ("ATR×1.5, no floor",      0.1,  1.5),

        # Группа 4: ATR × multiplier, floor=0.5% (тесный floor)
        ("ATR×0.75, floor=0.5%",   0.5,  0.75),
        ("ATR×1.0, floor=0.5%",    0.5,  1.0),
        ("ATR×1.25, floor=0.5%",   0.5,  1.25),
    ]

    print(f"\n[2] Тестируем {len(variants)} вариантов...")
    results = []

    for idx, (label, sl_floor, atr_mult) in enumerate(variants, 1):
        r = run_variant(all_candles, btc_trends, label, sl_floor, atr_mult)
        results.append(r)
        marker = " ← ТЕКУЩИЙ" if label == "ATR×1.5, floor=1.25%" else ""
        marker = " ← БЭКТЕСТ" if label == "FIXED SL=1.25%" else marker
        pnl_sign = "+" if r['net_pnl'] >= 0 else ""
        print(f"  [{idx:2d}/{len(variants)}] {label:30s} | PnL: {pnl_sign}{r['net_pnl']:8.1f}$ | "
              f"WR: {r['win_rate']:5.1f}% | Trades: {r['trades']:4d} | "
              f"Avg SL: {r['avg_sl']:.2f}% | PF: {r['profit_factor']:.2f}{marker}")

    # Сортировка по PnL
    results.sort(key=lambda x: x['net_pnl'], reverse=True)

    print("\n" + "=" * 70)
    print("  ИТОГИ — ТОП-10 по PnL")
    print("=" * 70)
    print(f"  {'#':>2s}  {'Вариант':30s} {'PnL':>10s} {'WR':>7s} {'Trades':>7s} {'Avg SL':>8s} {'PF':>6s}")
    print("  " + "-" * 68)

    for idx, r in enumerate(results[:10], 1):
        marker = ""
        if r['label'] == "ATR×1.5, floor=1.25%":
            marker = " ← ТЕКУЩИЙ"
        elif r['label'] == "FIXED SL=1.25%":
            marker = " ← БЭКТЕСТ"
        pnl_sign = "+" if r['net_pnl'] >= 0 else ""
        print(f"  {idx:2d}. {r['label']:30s} {pnl_sign}{r['net_pnl']:9.1f}$ {r['win_rate']:6.1f}% "
              f"{r['trades']:7d} {r['avg_sl']:7.2f}% {r['profit_factor']:6.2f}{marker}")

    # Найти позицию текущего и бэктеста
    for idx, r in enumerate(results, 1):
        if r['label'] == "ATR×1.5, floor=1.25%":
            current_rank = idx
            current_pnl = r['net_pnl']
        if r['label'] == "FIXED SL=1.25%":
            backtest_rank = idx
            backtest_pnl = r['net_pnl']

    best = results[0]
    print(f"\n  ЛУЧШИЙ:   {best['label']} → {'+' if best['net_pnl'] >= 0 else ''}{best['net_pnl']:.1f}$")
    print(f"  ТЕКУЩИЙ:  ATR×1.5, floor=1.25% → {'+' if current_pnl >= 0 else ''}{current_pnl:.1f}$ (#{current_rank})")
    print(f"  БЭКТЕСТ:  FIXED SL=1.25% → {'+' if backtest_pnl >= 0 else ''}{backtest_pnl:.1f}$ (#{backtest_rank})")
    diff = best['net_pnl'] - current_pnl
    print(f"\n  Потенциал улучшения: +{diff:.1f}$ ({diff/abs(current_pnl)*100:.0f}%)" if diff > 0 else "")

    # Сохраняем
    results_path = os.path.join(BASE_DIR, 'data', 'backtest_atr_results.json')
    with open(results_path, 'w') as f:
        json.dump({
            'timestamp': datetime.utcnow().isoformat(),
            'base_params': {k: v for k, v in BASE_PARAMS.items() if k != 'btc_modes'},
            'results': results,
        }, f, indent=2)
    print(f"\n  Результаты: {results_path}")


if __name__ == '__main__':
    main()
