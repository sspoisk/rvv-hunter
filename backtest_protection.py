#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — Бэктест: защита от разворота BTC
Тестирует:
  1. close_long_weak_bull_threshold (0.5, 1.0, 1.5, 2.0, 3.0, ВЫКЛ)
  2. max_position_age_hours (4, 8, 12, 24, 48, ВЫКЛ)
  3. Комбинации лучших
"""

import json
import os
import sys
import numpy as np
from datetime import datetime
from typing import List, Dict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'data', 'candle_cache')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

with open(CONFIG_PATH, 'r') as f:
    CFG = json.load(f)

TRADING = CFG.get('trading', {})
FILTERS = CFG.get('filters', {})

BASE_PARAMS = {
    'sl_pct': TRADING.get('stop_loss_pct', 1.25),
    'tp_pct': TRADING.get('take_profit_pct', 7.75),
    'trailing_activation_pct': TRADING.get('trailing_activation_pct', 0.5),
    'trailing_distance_pct': TRADING.get('trailing_distance_pct', 0.05),
    'atr_multiplier': 2.0,
    'position_size': 25,
    'leverage': 5,
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


def _backtest_symbol_protection(candles, btc_trends, btc_modes,
                                 sl_pct_floor, tp_pct, atr_multiplier,
                                 trailing_activation_pct, trailing_distance_pct,
                                 weak_bull_threshold, weak_bear_threshold,
                                 max_age_candles,
                                 rsi_short=70, rsi_long=30,
                                 position_size=25, leverage=5,
                                 commission_pct=0.08, slippage_pct=0.05,
                                 symbol_cooldown_candles=2, max_symbol_losses_daily=2):
    """
    Бэктест с:
    - ATR-адаптивным SL
    - Автозакрытие при ослаблении BTC тренда (weak_bull/bear_threshold)
    - Автозакрытие по возрасту позиции (max_age_candles, 0=выкл)
    """
    trades = 0
    wins = 0
    losses_count = 0
    gross_profit = 0.0
    gross_loss = 0.0
    trailing_wins = 0
    total_commission = 0.0
    trend_closes = 0
    age_closes = 0

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

            atr_pct = atr_values[i]
            if atr_multiplier > 0 and atr_pct > 0:
                sl_pct = max(sl_pct_floor, atr_pct * atr_multiplier)
            else:
                sl_pct = sl_pct_floor

            # BTC фильтр для входа
            candle_ts = candles[i].get('timestamp', candles[i].get('time', 0))
            btc_info = _get_btc_trend_at(btc_trends, candle_ts) if btc_trends else None

            def check_btc_entry(side):
                if not btc_info or not btc_modes:
                    return True
                bt = btc_info['trend']
                bp = btc_info['pct']
                if bt == 'bullish':
                    ms = btc_modes.get('bullish_min_str', 0)
                    mode = btc_modes.get('neutral', 'any') if (ms > 0 and bp < ms) else btc_modes.get('bullish', 'any')
                elif bt == 'bearish':
                    ms = btc_modes.get('bearish_min_str', 0)
                    mode = btc_modes.get('neutral', 'any') if (ms > 0 and bp < ms) else btc_modes.get('bearish', 'any')
                else:
                    mode = btc_modes.get('neutral', 'any')
                if side == 'SHORT' and mode in ('none', 'long_only'):
                    return False
                if side == 'LONG' and mode in ('none', 'short_only'):
                    return False
                return True

            if rsi >= rsi_short and check_btc_entry('SHORT'):
                entry_price = current_price * (1 - slippage_pct / 100)
                position = {
                    'side': 'SHORT', 'entry': entry_price,
                    'sl': entry_price * (1 + sl_pct / 100),
                    'tp': entry_price * (1 - tp_pct / 100),
                    'trailing_active': False, 'best_price': current_price,
                    'min_profit_sl': entry_price * (1 - min_trail_profit_pct / 100),
                    'open_idx': i,
                }
            elif rsi <= rsi_long and check_btc_entry('LONG'):
                entry_price = current_price * (1 + slippage_pct / 100)
                position = {
                    'side': 'LONG', 'entry': entry_price,
                    'sl': entry_price * (1 - sl_pct / 100),
                    'tp': entry_price * (1 + tp_pct / 100),
                    'trailing_active': False, 'best_price': current_price,
                    'min_profit_sl': entry_price * (1 + min_trail_profit_pct / 100),
                    'open_idx': i,
                }
        else:
            # === ЗАЩИТА 1: Автозакрытие при ослаблении BTC тренда ===
            if btc_trends and (weak_bull_threshold > 0 or weak_bear_threshold > 0):
                candle_ts = candles[i].get('timestamp', candles[i].get('time', 0))
                btc_info = _get_btc_trend_at(btc_trends, candle_ts)
                btc_change = btc_info.get('change_24h', 0)

                close_by_trend = False
                if position['side'] == 'LONG' and weak_bull_threshold > 0:
                    # Закрыть LONG если BTC 24h < +threshold (только убыточные, как в боте)
                    pnl_pct = (current_price - position['entry']) / position['entry'] * 100
                    if btc_change < weak_bull_threshold and pnl_pct < 0:
                        close_by_trend = True
                elif position['side'] == 'SHORT' and weak_bear_threshold > 0:
                    pnl_pct = (position['entry'] - current_price) / position['entry'] * 100
                    if btc_change > -weak_bear_threshold and pnl_pct < 0:
                        close_by_trend = True

                if close_by_trend:
                    if position['side'] == 'SHORT':
                        actual_pnl_pct = (position['entry'] - current_price) / position['entry'] * 100
                    else:
                        actual_pnl_pct = (current_price - position['entry']) / position['entry'] * 100
                    pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    total_commission += comm
                    if pnl_usd > 0:
                        gross_profit += pnl_usd
                        wins += 1
                    else:
                        gross_loss += abs(pnl_usd)
                        losses_count += 1
                    trades += 1
                    trend_closes += 1
                    cooldown_until_idx = i + symbol_cooldown_candles
                    position = None
                    continue

            # === ЗАЩИТА 2: Автозакрытие по возрасту ===
            if max_age_candles > 0 and (i - position['open_idx']) >= max_age_candles:
                if position['side'] == 'SHORT':
                    actual_pnl_pct = (position['entry'] - current_price) / position['entry'] * 100
                else:
                    actual_pnl_pct = (current_price - position['entry']) / position['entry'] * 100
                pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                comm = position_size * leverage * commission_pct / 100
                pnl_usd -= comm
                total_commission += comm
                if pnl_usd > 0:
                    gross_profit += pnl_usd
                    wins += 1
                else:
                    gross_loss += abs(pnl_usd)
                    losses_count += 1
                trades += 1
                age_closes += 1
                position = None
                continue

            # === TRAILING STOP ===
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

            # === ПРОВЕРКА SL/TP ===
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
            else:
                if low_price <= position['sl']:
                    actual_pnl_pct = (position['sl'] - position['entry']) / position['entry'] * 100
                    pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                    comm = position_size * leverage * commission_pct / 100
                    pnl_usd -= comm
                    total_commission += comm
                    if pnl_usd > 0:
                        gross_profit += pnl_usd
                        wins += 1
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
    return {
        'trades': trades, 'wins': wins, 'losses': losses_count,
        'net_pnl': net_pnl, 'win_rate': win_rate,
        'gross_profit': gross_profit, 'gross_loss': gross_loss,
        'trailing_wins': trailing_wins, 'commission': total_commission,
        'trend_closes': trend_closes, 'age_closes': age_closes,
    }


def load_cached_candles():
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


def run_test(all_candles, btc_trends, weak_bull_thr, weak_bear_thr, max_age_candles):
    total = {'trades': 0, 'wins': 0, 'losses': 0, 'net_pnl': 0,
             'gross_profit': 0, 'gross_loss': 0, 'trend_closes': 0, 'age_closes': 0}
    p = BASE_PARAMS
    for symbol, candles in all_candles.items():
        r = _backtest_symbol_protection(
            candles, btc_trends, p['btc_modes'],
            sl_pct_floor=p['sl_pct'], tp_pct=p['tp_pct'], atr_multiplier=p['atr_multiplier'],
            trailing_activation_pct=p['trailing_activation_pct'],
            trailing_distance_pct=p['trailing_distance_pct'],
            weak_bull_threshold=weak_bull_thr, weak_bear_threshold=weak_bear_thr,
            max_age_candles=max_age_candles,
            rsi_short=p['rsi_overbought'], rsi_long=p['rsi_oversold'],
            position_size=p['position_size'], leverage=p['leverage'],
            commission_pct=p['commission_pct'], slippage_pct=p['slippage_pct'],
            symbol_cooldown_candles=p['symbol_cooldown_candles'],
            max_symbol_losses_daily=p['max_symbol_losses_daily'],
        )
        for k in total:
            total[k] += r.get(k, 0)

    wr = (total['wins'] / total['trades'] * 100) if total['trades'] > 0 else 0
    pf = (total['gross_profit'] / total['gross_loss']) if total['gross_loss'] > 0 else 999
    return {**total, 'win_rate': wr, 'profit_factor': pf}


def main():
    print("=" * 75)
    print("  RVV Hunter — Бэктест: Защита от разворота BTC")
    print("=" * 75)

    print("\n[1] Загрузка кэша...")
    all_candles, btc_candles = load_cached_candles()
    btc_trends = _calc_btc_trend_array(btc_candles) if btc_candles else {}
    print(f"  BTC тренды: {len(btc_trends)} точек")

    # ═══════════════════════════════════════════════════════════════
    # ТЕСТ 1: weak_bull/bear_threshold
    # ═══════════════════════════════════════════════════════════════
    print(f"\n[2] Тест weak_bull/bear_threshold...")
    print(f"  {'Threshold':>12s} | {'PnL':>10s} | {'WR':>7s} | {'Trades':>7s} | {'PF':>6s} | {'TrendClose':>10s}")
    print("  " + "-" * 70)

    thresholds = [0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
    thr_results = []
    for thr in thresholds:
        r = run_test(all_candles, btc_trends, thr, thr, 0)
        thr_results.append((thr, r))
        marker = " ← ТЕКУЩИЙ" if thr == 0.5 else ""
        marker = " ← ВЫКЛ" if thr == 0 else marker
        pnl_s = f"+{r['net_pnl']:.1f}" if r['net_pnl'] >= 0 else f"{r['net_pnl']:.1f}"
        print(f"  {thr:>10.2f}% | {pnl_s:>10s}$ | {r['win_rate']:6.1f}% | {r['trades']:>7d} | "
              f"{r['profit_factor']:5.2f} | {r['trend_closes']:>10d}{marker}")

    # ═══════════════════════════════════════════════════════════════
    # ТЕСТ 2: max_position_age
    # ═══════════════════════════════════════════════════════════════
    print(f"\n[3] Тест max_position_age...")
    print(f"  {'MaxAge':>12s} | {'PnL':>10s} | {'WR':>7s} | {'Trades':>7s} | {'PF':>6s} | {'AgeClose':>10s}")
    print("  " + "-" * 70)

    # 15-мин свечи: 4 часа=16, 8=32, 12=48, 24=96, 48=192
    ages = [(0, "ВЫКЛ"), (16, "4h"), (32, "8h"), (48, "12h"), (96, "24h"), (192, "48h")]
    age_results = []
    for age_candles, label in ages:
        r = run_test(all_candles, btc_trends, 0.5, 0.5, age_candles)  # текущий threshold
        age_results.append((age_candles, label, r))
        marker = " ← ТЕКУЩИЙ" if age_candles == 0 else ""
        pnl_s = f"+{r['net_pnl']:.1f}" if r['net_pnl'] >= 0 else f"{r['net_pnl']:.1f}"
        print(f"  {label:>12s} | {pnl_s:>10s}$ | {r['win_rate']:6.1f}% | {r['trades']:>7d} | "
              f"{r['profit_factor']:5.2f} | {r['age_closes']:>10d}{marker}")

    # ═══════════════════════════════════════════════════════════════
    # ТЕСТ 3: Лучшие комбинации
    # ═══════════════════════════════════════════════════════════════
    # Находим лучший threshold и лучший age
    best_thr = max(thr_results, key=lambda x: x[1]['net_pnl'])
    best_age = max(age_results, key=lambda x: x[2]['net_pnl'])

    print(f"\n[4] Комбинации лучших вариантов...")
    print(f"  Лучший threshold: {best_thr[0]}%")
    print(f"  Лучший max_age:   {best_age[1]}")

    combos = [
        (0.5, 0, "Текущий (thr=0.5%, без age)"),
        (best_thr[0], 0, f"Лучший thr={best_thr[0]}%, без age"),
        (0.5, best_age[0], f"thr=0.5% + age={best_age[1]}"),
        (best_thr[0], best_age[0], f"thr={best_thr[0]}% + age={best_age[1]}"),
        (0, 0, "Без защиты (baseline)"),
    ]
    # Добавим ещё пару вариантов
    for extra_thr in [0.25, 1.0, 1.5]:
        if extra_thr != best_thr[0]:
            combos.append((extra_thr, best_age[0], f"thr={extra_thr}% + age={best_age[1]}"))

    print(f"\n  {'Комбинация':>40s} | {'PnL':>10s} | {'WR':>7s} | {'Trades':>7s} | {'PF':>6s} | {'Trend':>6s} | {'Age':>5s}")
    print("  " + "-" * 90)

    combo_results = []
    for thr, age, label in combos:
        r = run_test(all_candles, btc_trends, thr, thr, age)
        combo_results.append((label, r))
        pnl_s = f"+{r['net_pnl']:.1f}" if r['net_pnl'] >= 0 else f"{r['net_pnl']:.1f}"
        print(f"  {label:>40s} | {pnl_s:>10s}$ | {r['win_rate']:6.1f}% | {r['trades']:>7d} | "
              f"{r['profit_factor']:5.2f} | {r['trend_closes']:>6d} | {r['age_closes']:>5d}")

    # Итог
    combo_results.sort(key=lambda x: x[1]['net_pnl'], reverse=True)
    print(f"\n{'=' * 75}")
    print(f"  ЛУЧШАЯ КОМБИНАЦИЯ: {combo_results[0][0]}")
    best_r = combo_results[0][1]
    print(f"  PnL: +{best_r['net_pnl']:.1f}$  |  WR: {best_r['win_rate']:.1f}%  |  PF: {best_r['profit_factor']:.2f}")

    # Текущий для сравнения
    for label, r in combo_results:
        if "Текущий" in label:
            print(f"\n  ТЕКУЩИЙ: {label}")
            print(f"  PnL: +{r['net_pnl']:.1f}$  |  WR: {r['win_rate']:.1f}%  |  PF: {r['profit_factor']:.2f}")
            diff = combo_results[0][1]['net_pnl'] - r['net_pnl']
            if diff > 0:
                print(f"\n  Потенциал улучшения: +{diff:.1f}$")
            break

    # Сохранение
    results_path = os.path.join(BASE_DIR, 'data', 'backtest_protection_results.json')
    with open(results_path, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'threshold_results': [(t, r) for t, r in thr_results],
            'age_results': [(a, l, r) for a, l, r in age_results],
            'combo_results': combo_results,
        }, f, indent=2, default=str)
    print(f"\n  Результаты: {results_path}")


if __name__ == '__main__':
    main()
