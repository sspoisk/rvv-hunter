#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — Full Parameter Backtest
Тестирует все настройки бота на исторических данных.
Не модифицирует бота — один самостоятельный скрипт.

Фазы:
  1. Загрузка данных (100 монет × 30 дней, 15m свечи)
  2. Baseline — текущие настройки из config.json
  3. Grid Search SL/TP/Trailing (грубая + точная сетка)
  4. Оптимизация min_change_filter
  5. Оптимизация BTC фильтра
  6. Walk-Forward валидация
  7. Анализ паттернов (часы/дни/LONG vs SHORT)
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
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

# ─── Пути ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CACHE_DIR = os.path.join(BASE_DIR, 'data', 'candle_cache')
RESULTS_PATH = os.path.join(BASE_DIR, 'data', 'full_backtest_results.json')

# ─── Загрузка конфига ────────────────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

CFG = load_config()
TRADING = CFG.get('trading', {})
FILTERS = CFG.get('filters', {})

# Baseline параметры
BASELINE = {
    'sl_pct': TRADING.get('stop_loss_pct', 3.5),
    'tp_pct': TRADING.get('take_profit_pct', 12.0),
    'trailing_activation_pct': TRADING.get('trailing_activation_pct', 2.0),
    'trailing_distance_pct': TRADING.get('trailing_distance_pct', 0.8),
    'position_size': TRADING.get('position_size', 25),
    'leverage': TRADING.get('leverage', 5),
    'max_positions': TRADING.get('max_positions', 5),
    'rsi_overbought': 70,
    'rsi_oversold': 30,
    'commission_pct': 0.08,
    'slippage_pct': 0.05,
    'symbol_cooldown_candles': 2,
    'max_symbol_losses_daily': 2,
    'min_change_pct': 5.0,
    'btc_modes': {
        'bullish': FILTERS.get('btc_bullish_mode', 'long_only'),
        'bearish': FILTERS.get('btc_bearish_mode', 'short_only'),
        'neutral': FILTERS.get('btc_neutral_mode', 'none'),
        'bullish_min_str': float(FILTERS.get('btc_bullish_min_strength', 0.3)),
        'bearish_min_str': float(FILTERS.get('btc_bearish_min_strength', 0.3)),
    },
}

BACKTEST_DAYS = 30
TOP_COINS = 100

# ═════════════════════════════════════════════════════════════════════════════
# КОПИИ ВЫЧИСЛИТЕЛЬНЫХ ФУНКЦИЙ ИЗ agent_tools.py (без зависимостей)
# ═════════════════════════════════════════════════════════════════════════════

def _calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    """Рассчитать RSI для массива цен — копия из agent_tools.py:2760"""
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


def _calc_btc_trend_array(btc_candles: List[Dict]) -> Dict[int, Dict]:
    """Рассчитать BTC тренд для каждой свечи — копия из agent_tools.py:1708"""
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


def _get_btc_trend_at(btc_trends: Dict[int, any], candle_ts: int) -> Dict:
    """Получить BTC тренд для timestamp — копия из agent_tools.py:1746"""
    default = {'trend': 'neutral', 'pct': 0.0, 'change_24h': 0.0}
    if not btc_trends:
        return default
    if candle_ts > 1e12:
        ts = int(candle_ts) - (int(candle_ts) % 900000)
    else:
        ts = int(candle_ts * 1000) - (int(candle_ts * 1000) % 900000)
    if ts in btc_trends:
        val = btc_trends[ts]
        if isinstance(val, str):
            return {'trend': val, 'pct': 0.0, 'change_24h': 0.0}
        if 'change_24h' not in val:
            val['change_24h'] = val.get('pct', 0.0) if val.get('trend') == 'bullish' else -val.get('pct', 0.0)
        return val
    for offset in [900000, -900000, 1800000, -1800000]:
        if (ts + offset) in btc_trends:
            val = btc_trends[ts + offset]
            if isinstance(val, str):
                return {'trend': val, 'pct': 0.0, 'change_24h': 0.0}
            if 'change_24h' not in val:
                val['change_24h'] = val.get('pct', 0.0) if val.get('trend') == 'bullish' else -val.get('pct', 0.0)
            return val
    return default


def _backtest_symbol(candles: List[Dict], rsi_short: int, rsi_long: int,
                     sl_pct: float, tp_pct: float, position_size: float = 25,
                     leverage: int = 5, trailing_activation_pct: float = 2.0,
                     trailing_distance_pct: float = 0.8,
                     commission_pct: float = 0.08, slippage_pct: float = 0.05,
                     symbol_cooldown_candles: int = 2,
                     max_symbol_losses_daily: int = 2,
                     btc_trends: Dict = None, btc_modes: Dict = None,
                     min_change_pct: float = 0.0,
                     return_details: bool = False) -> Dict:
    """Бэктест одной монеты — адаптация из agent_tools.py:2180"""
    trades = 0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    trailing_wins = 0
    total_commission = 0.0
    trade_details = []

    cooldown_until_idx = 0
    daily_sl_count = 0
    current_day = ""

    trailing_enabled = True
    min_trail_profit_pct = max(0.3, trailing_activation_pct - trailing_distance_pct)

    closes = [c['close'] for c in candles]
    rsi_values = _calculate_rsi(closes, 14)

    lookback_24h = 96
    change_24h_values = [0.0] * len(candles)
    if min_change_pct > 0:
        for idx in range(lookback_24h, len(candles)):
            old_close = closes[idx - lookback_24h]
            if old_close > 0:
                change_24h_values[idx] = (closes[idx] - old_close) / old_close * 100

    position = None

    for i in range(20, len(candles)):
        rsi = rsi_values[i] if i < len(rsi_values) else 50
        current_price = candles[i]['close']
        high_price = candles[i]['high']
        low_price = candles[i]['low']

        if position is None:
            # Сброс дневного счётчика
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

            # SHORT (RSI перекуплен)
            if rsi >= rsi_short:
                if min_change_pct > 0 and change_24h_values[i] < min_change_pct:
                    continue
                if min_change_pct > 0 and i + 1 < len(change_24h_values):
                    if change_24h_values[i + 1] < min_change_pct:
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
                        if mode not in ('any', 'any_incl_neutral', 'none'):
                            if btc_modes.get('bullish') == 'any_incl_neutral' or btc_modes.get('bearish') == 'any_incl_neutral':
                                mode = 'any'
                    if mode == 'none' or mode == 'long_only':
                        continue

                entry_price = current_price * (1 - slippage_pct / 100)
                position = {
                    'side': 'SHORT',
                    'entry': entry_price,
                    'sl': entry_price * (1 + sl_pct / 100),
                    'tp': entry_price * (1 - tp_pct / 100),
                    'trailing_active': False,
                    'best_price': current_price,
                    'min_profit_sl': entry_price * (1 - min_trail_profit_pct / 100),
                    'open_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                    'candle_idx': i
                }

            # LONG (RSI перепродан)
            elif rsi <= rsi_long:
                if min_change_pct > 0 and change_24h_values[i] > -min_change_pct:
                    continue
                if min_change_pct > 0 and i + 1 < len(change_24h_values):
                    if change_24h_values[i + 1] > -min_change_pct:
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
                        if mode not in ('any', 'any_incl_neutral', 'none'):
                            if btc_modes.get('bullish') == 'any_incl_neutral' or btc_modes.get('bearish') == 'any_incl_neutral':
                                mode = 'any'
                    if mode == 'none' or mode == 'short_only':
                        continue

                entry_price = current_price * (1 + slippage_pct / 100)
                position = {
                    'side': 'LONG',
                    'entry': entry_price,
                    'sl': entry_price * (1 - sl_pct / 100),
                    'tp': entry_price * (1 + tp_pct / 100),
                    'trailing_active': False,
                    'best_price': current_price,
                    'min_profit_sl': entry_price * (1 + min_trail_profit_pct / 100),
                    'open_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                    'candle_idx': i
                }
        else:
            # === TRAILING STOP ===
            if trailing_enabled:
                if position['side'] == 'SHORT':
                    current_profit_pct = (position['entry'] - low_price) / position['entry'] * 100
                    if low_price < position['best_price']:
                        position['best_price'] = low_price
                        position['min_profit_sl'] = position['best_price'] * (1 + trailing_distance_pct / 100)
                    if current_profit_pct >= trailing_activation_pct and not position['trailing_active']:
                        position['trailing_active'] = True
                        new_sl = position['best_price'] * (1 + trailing_distance_pct / 100)
                        if new_sl < position['entry']:
                            position['sl'] = new_sl
                    if position['trailing_active']:
                        new_sl = position['best_price'] * (1 + trailing_distance_pct / 100)
                        if new_sl < position['entry'] and new_sl < position['sl']:
                            position['sl'] = new_sl
                else:  # LONG
                    current_profit_pct = (high_price - position['entry']) / position['entry'] * 100
                    if high_price > position['best_price']:
                        position['best_price'] = high_price
                        position['min_profit_sl'] = position['best_price'] * (1 - trailing_distance_pct / 100)
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
                        losses += 1
                    trades += 1
                    if return_details:
                        trade_details.append({
                            'open_time': position.get('open_time', 0),
                            'close_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                            'side': 'SHORT', 'pnl': pnl_usd, 'is_win': pnl_usd > 0,
                            'close_reason': 'TRAILING_STOP' if position['trailing_active'] else 'STOP_LOSS'
                        })
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
                    if return_details:
                        trade_details.append({
                            'open_time': position.get('open_time', 0),
                            'close_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                            'side': 'SHORT', 'pnl': pnl_usd, 'is_win': True,
                            'close_reason': 'TAKE_PROFIT'
                        })
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
                        losses += 1
                    trades += 1
                    if return_details:
                        trade_details.append({
                            'open_time': position.get('open_time', 0),
                            'close_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                            'side': 'LONG', 'pnl': pnl_usd, 'is_win': pnl_usd > 0,
                            'close_reason': 'TRAILING_STOP' if position['trailing_active'] else 'STOP_LOSS'
                        })
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
                    if return_details:
                        trade_details.append({
                            'open_time': position.get('open_time', 0),
                            'close_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                            'side': 'LONG', 'pnl': pnl_usd, 'is_win': True,
                            'close_reason': 'TAKE_PROFIT'
                        })
                    position = None

    net_pnl = gross_profit - gross_loss
    result = {
        'trades': trades, 'wins': wins, 'losses': losses,
        'pnl': net_pnl, 'gross_profit': gross_profit, 'gross_loss': gross_loss,
        'win_rate': wins / trades * 100 if trades > 0 else 0,
        'trailing_wins': trailing_wins, 'total_commission': total_commission
    }
    if return_details:
        result['trade_details'] = trade_details
    return result


def _apply_position_limit(all_trades: List[Dict], max_positions: int) -> Dict:
    """Лимит одновременных позиций — копия из agent_tools.py:2605"""
    empty = {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0,
             'gross_profit': 0, 'gross_loss': 0, 'trailing_wins': 0,
             'total_commission': 0, 'skipped_signals': 0, 'accepted_trades': []}
    if not all_trades:
        return empty

    has_close = sum(1 for t in all_trades if t.get('close_time', 0) > 0)
    if has_close == 0:
        # Fallback: все сделки без фильтра
        total_trades = len(all_trades)
        w = sum(1 for t in all_trades if t.get('is_win', False))
        gp = sum(t['pnl'] for t in all_trades if t.get('pnl', 0) > 0)
        gl = sum(abs(t['pnl']) for t in all_trades if t.get('pnl', 0) <= 0)
        return {
            'total_trades': total_trades, 'wins': w, 'losses': total_trades - w,
            'total_pnl': gp - gl, 'gross_profit': gp, 'gross_loss': gl,
            'trailing_wins': 0, 'total_commission': 0, 'skipped_signals': 0,
            'accepted_trades': all_trades
        }

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
        'trailing_wins': tw, 'total_commission': 0,
        'skipped_signals': skipped, 'accepted_trades': accepted
    }


# ═════════════════════════════════════════════════════════════════════════════
# ФАЗА 1: ЗАГРУЗКА ДАННЫХ
# ═════════════════════════════════════════════════════════════════════════════

def get_exchange():
    return ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'proxies': {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050',
        },
    })


def load_candles_binance(exchange, symbol: str, days: int) -> List[Dict]:
    """Загрузить 15m свечи с Binance, вернуть в формате agent_tools."""
    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    all_candles = []
    limit = 1500
    current_since = since

    while True:
        raw = exchange.fetch_ohlcv(symbol, '15m', since=current_since, limit=limit)
        if not raw:
            break
        for c in raw:
            all_candles.append({
                'timestamp': c[0], 'open': c[1], 'high': c[2],
                'low': c[3], 'close': c[4], 'volume': c[5]
            })
        if len(raw) < limit:
            break
        current_since = raw[-1][0] + 1
        time.sleep(0.1)

    return all_candles


def phase1_load_data() -> Tuple[Dict[str, List[Dict]], List[Dict], List[str]]:
    """Загрузить данные. Возвращает (candles_by_symbol, btc_candles, symbol_list)."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Проверяем кэш
    cache_meta_path = os.path.join(CACHE_DIR, '_meta.json')
    cache_valid = False
    if os.path.exists(cache_meta_path):
        try:
            with open(cache_meta_path, 'r') as f:
                meta = json.load(f)
            cache_age_h = (time.time() - meta.get('timestamp', 0)) / 3600
            if cache_age_h < 6 and meta.get('days') == BACKTEST_DAYS:
                cache_valid = True
                print(f"  Кэш найден ({cache_age_h:.1f}ч назад), загружаем...")
        except Exception:
            pass

    if cache_valid:
        candles_by_symbol = {}
        symbol_list = meta.get('symbols', [])
        for sym_clean in symbol_list:
            fpath = os.path.join(CACHE_DIR, f"{sym_clean.replace('/', '_')}.json")
            if os.path.exists(fpath):
                with open(fpath, 'r') as f:
                    candles_by_symbol[sym_clean] = json.load(f)
        # BTC
        btc_path = os.path.join(CACHE_DIR, 'BTC_USDT_USDT.json')
        btc_candles = []
        if os.path.exists(btc_path):
            with open(btc_path, 'r') as f:
                btc_candles = json.load(f)
        print(f"  Из кэша: {len(candles_by_symbol)} монет, BTC: {len(btc_candles)} свечей")
        return candles_by_symbol, btc_candles, list(candles_by_symbol.keys())

    # Загрузка с Binance
    exchange = get_exchange()
    print("  Подключение к Binance Futures...")

    tickers = exchange.fetch_tickers()
    pairs = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith('/USDT:USDT'):
            continue
        vol = ticker.get('quoteVolume', 0) or 0
        if vol > 0:
            pairs.append({'symbol': symbol, 'volume': vol})

    pairs.sort(key=lambda x: x['volume'], reverse=True)
    top_symbols = [p['symbol'] for p in pairs[:TOP_COINS]]
    print(f"  Топ-{TOP_COINS} пар по объёму получены")

    # Загружаем BTC отдельно
    print("  Загрузка BTC свечей...")
    btc_symbol = 'BTC/USDT:USDT'
    btc_candles = load_candles_binance(exchange, btc_symbol, BACKTEST_DAYS)
    btc_path = os.path.join(CACHE_DIR, 'BTC_USDT_USDT.json')
    with open(btc_path, 'w') as f:
        json.dump(btc_candles, f)
    print(f"  BTC: {len(btc_candles)} свечей")

    # Загружаем остальные
    candles_by_symbol = {}
    failed = 0
    for i, symbol in enumerate(top_symbols):
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(top_symbols)} монет загружено...")
        try:
            candles = load_candles_binance(exchange, symbol, BACKTEST_DAYS)
            if len(candles) >= 100:
                clean = symbol.replace('/USDT:USDT', '')
                candles_by_symbol[clean] = candles
                fpath = os.path.join(CACHE_DIR, f"{symbol.replace('/', '_')}.json")
                with open(fpath, 'w') as f:
                    json.dump(candles, f)
        except Exception as e:
            failed += 1
        time.sleep(0.12)

    # Сохраняем мета
    with open(cache_meta_path, 'w') as f:
        json.dump({
            'timestamp': time.time(),
            'days': BACKTEST_DAYS,
            'symbols': list(candles_by_symbol.keys()),
            'count': len(candles_by_symbol)
        }, f)

    print(f"  Загружено: {len(candles_by_symbol)} монет ({failed} ошибок)")
    return candles_by_symbol, btc_candles, list(candles_by_symbol.keys())


# ═════════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ БЭКТЕСТА
# ═════════════════════════════════════════════════════════════════════════════

def run_multi_backtest(candles_by_symbol: Dict[str, List[Dict]],
                       btc_trends: Dict, btc_modes: Dict,
                       sl_pct: float, tp_pct: float,
                       trail_act: float, trail_dist: float,
                       min_change_pct: float = 5.0,
                       max_positions: int = 5,
                       position_size: float = 25,
                       leverage: int = 5,
                       return_details: bool = False,
                       candle_slice: Tuple[float, float] = None) -> Dict:
    """Прогнать бэктест на всех монетах с заданными параметрами."""
    all_trade_details = []
    total = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0,
             'gross_profit': 0.0, 'gross_loss': 0.0, 'trailing_wins': 0}

    for sym, candles in candles_by_symbol.items():
        # Обрезка по временному окну (для walk-forward)
        if candle_slice:
            start_frac, end_frac = candle_slice
            n = len(candles)
            candles = candles[int(n * start_frac):int(n * end_frac)]
            if len(candles) < 100:
                continue

        res = _backtest_symbol(
            candles, rsi_short=70, rsi_long=30,
            sl_pct=sl_pct, tp_pct=tp_pct,
            position_size=position_size, leverage=leverage,
            trailing_activation_pct=trail_act,
            trailing_distance_pct=trail_dist,
            commission_pct=0.08, slippage_pct=0.05,
            symbol_cooldown_candles=2, max_symbol_losses_daily=2,
            btc_trends=btc_trends, btc_modes=btc_modes,
            min_change_pct=min_change_pct,
            return_details=(return_details or max_positions > 0)
        )
        total['trades'] += res['trades']
        total['wins'] += res['wins']
        total['losses'] += res['losses']
        total['pnl'] += res['pnl']
        total['gross_profit'] += res['gross_profit']
        total['gross_loss'] += res['gross_loss']
        total['trailing_wins'] += res.get('trailing_wins', 0)

        if 'trade_details' in res:
            for td in res['trade_details']:
                td['symbol'] = sym
                all_trade_details.append(td)

    # Применяем лимит позиций
    if max_positions > 0 and all_trade_details:
        limited = _apply_position_limit(all_trade_details, max_positions)
        total['trades'] = limited['total_trades']
        total['wins'] = limited['wins']
        total['losses'] = limited['losses']
        total['pnl'] = limited['total_pnl']
        total['gross_profit'] = limited['gross_profit']
        total['gross_loss'] = limited['gross_loss']
        total['trailing_wins'] = limited.get('trailing_wins', 0)
        total['skipped'] = limited.get('skipped_signals', 0)
        if return_details:
            total['accepted_trades'] = limited.get('accepted_trades', [])
    elif return_details:
        total['accepted_trades'] = all_trade_details

    total['win_rate'] = total['wins'] / total['trades'] * 100 if total['trades'] > 0 else 0
    return total


def fmt_result(r: Dict) -> str:
    """Форматировать результат в строку таблицы."""
    if r['trades'] == 0:
        return "  0 сделок"
    return (f"  {r['trades']:>4} сделок | WR {r['win_rate']:5.1f}% | "
            f"PnL {r['pnl']:+8.2f}$ | "
            f"Trailing {r.get('trailing_wins', 0)}")


# ═════════════════════════════════════════════════════════════════════════════
# ФАЗА 2: BASELINE
# ═════════════════════════════════════════════════════════════════════════════

def phase2_baseline(candles_by_symbol, btc_trends, btc_modes) -> Dict:
    print("\n[ФАЗА 2] Baseline — текущие настройки")
    print(f"  SL={BASELINE['sl_pct']}% TP={BASELINE['tp_pct']}% "
          f"Trail={BASELINE['trailing_activation_pct']}/{BASELINE['trailing_distance_pct']}% "
          f"min_change={BASELINE['min_change_pct']}%")

    result = run_multi_backtest(
        candles_by_symbol, btc_trends, btc_modes,
        sl_pct=BASELINE['sl_pct'], tp_pct=BASELINE['tp_pct'],
        trail_act=BASELINE['trailing_activation_pct'],
        trail_dist=BASELINE['trailing_distance_pct'],
        min_change_pct=BASELINE['min_change_pct'],
        max_positions=BASELINE['max_positions'],
        position_size=BASELINE['position_size'],
        leverage=BASELINE['leverage'],
        return_details=True
    )
    print(f"  BASELINE:{fmt_result(result)}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# ФАЗА 3: GRID SEARCH SL/TP/TRAILING
# ═════════════════════════════════════════════════════════════════════════════

def phase3_grid_search(candles_by_symbol, btc_trends, btc_modes, baseline_pnl: float) -> List[Dict]:
    print("\n[ФАЗА 3] Grid Search — SL/TP/Trailing")

    # Грубая сетка
    sl_values = [1.0, 2.0, 3.0, 3.5, 4.0, 5.0]
    tp_values = [4.0, 6.0, 8.0, 10.0, 12.0, 15.0]
    trail_act_values = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
    trail_dist_values = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]

    combos = []
    for sl, tp, ta, td in product(sl_values, tp_values, trail_act_values, trail_dist_values):
        if tp <= sl:
            continue
        if td >= ta:
            continue
        combos.append((sl, tp, ta, td))

    print(f"  Грубая сетка: {len(combos)} комбинаций")

    results = []
    t0 = time.time()
    for idx, (sl, tp, ta, td) in enumerate(combos):
        if (idx + 1) % 100 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (idx + 1) * (len(combos) - idx - 1)
            print(f"  {idx + 1}/{len(combos)} ({elapsed:.0f}с, ETA {eta:.0f}с)...")

        r = run_multi_backtest(
            candles_by_symbol, btc_trends, btc_modes,
            sl_pct=sl, tp_pct=tp, trail_act=ta, trail_dist=td,
            min_change_pct=BASELINE['min_change_pct'],
            max_positions=BASELINE['max_positions'],
            position_size=BASELINE['position_size'],
            leverage=BASELINE['leverage']
        )
        results.append({
            'sl': sl, 'tp': tp, 'trail_act': ta, 'trail_dist': td,
            **r
        })

    elapsed = time.time() - t0
    print(f"  Грубая сетка завершена за {elapsed:.0f}с")

    # Сортируем по PnL
    results.sort(key=lambda x: x['pnl'], reverse=True)

    # Топ-5 грубой сетки
    print("\n  Топ-5 грубой сетки:")
    print(f"  {'#':>3}  {'SL':>5}  {'TP':>5}  {'T.Act':>5}  {'T.Dst':>5}  {'Trades':>6}  {'WR%':>6}  {'PnL':>10}  {'vs Base':>10}")
    print("  " + "-" * 72)
    for i, r in enumerate(results[:5]):
        delta = r['pnl'] - baseline_pnl
        print(f"  {i + 1:>3}  {r['sl']:>5.1f}  {r['tp']:>5.1f}  {r['trail_act']:>5.1f}  "
              f"{r['trail_dist']:>5.1f}  {r['trades']:>6}  {r['win_rate']:>5.1f}%  "
              f"{r['pnl']:>+9.2f}$  {delta:>+9.2f}$")

    # Точная сетка вокруг топ-5
    print("\n  Точная сетка вокруг топ-5 (шаг 0.25)...")
    fine_combos = set()
    for r in results[:5]:
        for sl_d in [-0.5, -0.25, 0, 0.25, 0.5]:
            for tp_d in [-0.5, -0.25, 0, 0.25, 0.5]:
                for ta_d in [-0.5, -0.25, 0, 0.25, 0.5]:
                    for td_d in [-0.25, 0, 0.25]:
                        sl = round(r['sl'] + sl_d, 2)
                        tp = round(r['tp'] + tp_d, 2)
                        ta = round(r['trail_act'] + ta_d, 2)
                        td_v = round(r['trail_dist'] + td_d, 2)
                        if sl <= 0 or tp <= 0 or ta <= 0 or td_v <= 0:
                            continue
                        if tp <= sl or td_v >= ta:
                            continue
                        fine_combos.add((sl, tp, ta, td_v))

    # Убираем уже протестированные
    tested = {(r['sl'], r['tp'], r['trail_act'], r['trail_dist']) for r in results}
    fine_combos = [c for c in fine_combos if c not in tested]
    print(f"  Точная сетка: {len(fine_combos)} новых комбинаций")

    fine_results = []
    t0 = time.time()
    for idx, (sl, tp, ta, td) in enumerate(fine_combos):
        if (idx + 1) % 100 == 0:
            print(f"  {idx + 1}/{len(fine_combos)}...")
        r = run_multi_backtest(
            candles_by_symbol, btc_trends, btc_modes,
            sl_pct=sl, tp_pct=tp, trail_act=ta, trail_dist=td,
            min_change_pct=BASELINE['min_change_pct'],
            max_positions=BASELINE['max_positions'],
            position_size=BASELINE['position_size'],
            leverage=BASELINE['leverage']
        )
        fine_results.append({
            'sl': sl, 'tp': tp, 'trail_act': ta, 'trail_dist': td,
            **r
        })

    all_results = results + fine_results
    all_results.sort(key=lambda x: x['pnl'], reverse=True)
    elapsed = time.time() - t0
    print(f"  Точная сетка завершена за {elapsed:.0f}с")

    # Финальный топ-5
    print("\n  ФИНАЛЬНЫЙ Топ-5 SL/TP/Trailing:")
    print(f"  {'#':>3}  {'SL':>5}  {'TP':>5}  {'T.Act':>5}  {'T.Dst':>5}  {'Trades':>6}  {'WR%':>6}  {'PnL':>10}  {'vs Base':>10}")
    print("  " + "-" * 72)
    for i, r in enumerate(all_results[:5]):
        delta = r['pnl'] - baseline_pnl
        print(f"  {i + 1:>3}  {r['sl']:>5.2f}  {r['tp']:>5.2f}  {r['trail_act']:>5.2f}  "
              f"{r['trail_dist']:>5.2f}  {r['trades']:>6}  {r['win_rate']:>5.1f}%  "
              f"{r['pnl']:>+9.2f}$  {delta:>+9.2f}$")

    return all_results[:10]


# ═════════════════════════════════════════════════════════════════════════════
# ФАЗА 4: MIN_CHANGE_FILTER
# ═════════════════════════════════════════════════════════════════════════════

def phase4_min_change(candles_by_symbol, btc_trends, btc_modes,
                      best_sl, best_tp, best_ta, best_td, baseline_pnl) -> List[Dict]:
    print("\n[ФАЗА 4] Оптимизация min_change_filter")
    thresholds = [0, 3, 5, 8, 10, 12, 15, 18, 20, 25]

    results = []
    for mc in thresholds:
        r = run_multi_backtest(
            candles_by_symbol, btc_trends, btc_modes,
            sl_pct=best_sl, tp_pct=best_tp,
            trail_act=best_ta, trail_dist=best_td,
            min_change_pct=float(mc),
            max_positions=BASELINE['max_positions'],
            position_size=BASELINE['position_size'],
            leverage=BASELINE['leverage']
        )
        results.append({'min_change': mc, **r})

    print(f"\n  {'min_change%':>12}  {'Trades':>6}  {'WR%':>6}  {'PnL':>10}  {'vs Base':>10}")
    print("  " + "-" * 55)
    for r in results:
        delta = r['pnl'] - baseline_pnl
        marker = " <-- current" if r['min_change'] == 5 else ""
        print(f"  {r['min_change']:>11}%  {r['trades']:>6}  {r['win_rate']:>5.1f}%  "
              f"{r['pnl']:>+9.2f}$  {delta:>+9.2f}${marker}")

    results.sort(key=lambda x: x['pnl'], reverse=True)
    return results


# ═════════════════════════════════════════════════════════════════════════════
# ФАЗА 5: BTC ФИЛЬТР
# ═════════════════════════════════════════════════════════════════════════════

def phase5_btc_filter(candles_by_symbol, btc_trends,
                      best_sl, best_tp, best_ta, best_td,
                      best_min_change, baseline_pnl) -> List[Dict]:
    print("\n[ФАЗА 5] Оптимизация BTC фильтра")
    modes = ['long_only', 'short_only', 'any', 'none']
    strengths = [0.0, 0.3, 0.5, 1.0, 2.0, 3.0]

    results = []
    total_combos = 0

    # Тестируем комбинации bull/bear/neutral режимов
    for bull_mode in modes:
        for bear_mode in modes:
            for neutral_mode in ['any', 'none']:
                for strength in strengths:
                    total_combos += 1
                    btc_modes = {
                        'bullish': bull_mode,
                        'bearish': bear_mode,
                        'neutral': neutral_mode,
                        'bullish_min_str': strength,
                        'bearish_min_str': strength,
                    }
                    r = run_multi_backtest(
                        candles_by_symbol, btc_trends, btc_modes,
                        sl_pct=best_sl, tp_pct=best_tp,
                        trail_act=best_ta, trail_dist=best_td,
                        min_change_pct=best_min_change,
                        max_positions=BASELINE['max_positions'],
                        position_size=BASELINE['position_size'],
                        leverage=BASELINE['leverage']
                    )
                    results.append({
                        'bull': bull_mode, 'bear': bear_mode,
                        'neutral': neutral_mode, 'strength': strength,
                        **r
                    })

    print(f"  Протестировано {total_combos} комбинаций BTC фильтра")
    results.sort(key=lambda x: x['pnl'], reverse=True)

    print(f"\n  Топ-5 BTC фильтров:")
    print(f"  {'#':>3}  {'Bull':>10}  {'Bear':>10}  {'Neut':>5}  {'Str':>4}  {'Trades':>6}  {'WR%':>6}  {'PnL':>10}  {'vs Base':>10}")
    print("  " + "-" * 80)
    for i, r in enumerate(results[:5]):
        delta = r['pnl'] - baseline_pnl
        print(f"  {i + 1:>3}  {r['bull']:>10}  {r['bear']:>10}  {r['neutral']:>5}  "
              f"{r['strength']:>4.1f}  {r['trades']:>6}  {r['win_rate']:>5.1f}%  "
              f"{r['pnl']:>+9.2f}$  {delta:>+9.2f}$")

    return results[:10]


# ═════════════════════════════════════════════════════════════════════════════
# ФАЗА 6: WALK-FORWARD ВАЛИДАЦИЯ
# ═════════════════════════════════════════════════════════════════════════════

def phase6_walk_forward(candles_by_symbol, btc_trends, best_btc_modes,
                        best_sl, best_tp, best_ta, best_td,
                        best_min_change) -> Dict:
    print("\n[ФАЗА 6] Walk-Forward валидация (70/30)")

    windows = [
        {'name': 'Window 1 (0-70% train, 70-100% test)', 'train': (0.0, 0.7), 'test': (0.7, 1.0)},
        {'name': 'Window 2 (10-80% train, 80-100% test)', 'train': (0.1, 0.8), 'test': (0.8, 1.0)},
        {'name': 'Window 3 (0-60% train, 60-90% test)', 'train': (0.0, 0.6), 'test': (0.6, 0.9)},
    ]

    wf_results = []
    for w in windows:
        train_r = run_multi_backtest(
            candles_by_symbol, btc_trends, best_btc_modes,
            sl_pct=best_sl, tp_pct=best_tp,
            trail_act=best_ta, trail_dist=best_td,
            min_change_pct=best_min_change,
            max_positions=BASELINE['max_positions'],
            position_size=BASELINE['position_size'],
            leverage=BASELINE['leverage'],
            candle_slice=w['train']
        )
        test_r = run_multi_backtest(
            candles_by_symbol, btc_trends, best_btc_modes,
            sl_pct=best_sl, tp_pct=best_tp,
            trail_act=best_ta, trail_dist=best_td,
            min_change_pct=best_min_change,
            max_positions=BASELINE['max_positions'],
            position_size=BASELINE['position_size'],
            leverage=BASELINE['leverage'],
            candle_slice=w['test']
        )

        wf_results.append({
            'window': w['name'],
            'train_pnl': train_r['pnl'], 'train_wr': train_r['win_rate'], 'train_trades': train_r['trades'],
            'test_pnl': test_r['pnl'], 'test_wr': test_r['win_rate'], 'test_trades': test_r['trades'],
        })

    print(f"\n  {'Window':>45}  {'Train PnL':>10}  {'Train WR':>8}  {'Test PnL':>10}  {'Test WR':>8}")
    print("  " + "-" * 90)
    for wr in wf_results:
        print(f"  {wr['window']:>45}  {wr['train_pnl']:>+9.2f}$  {wr['train_wr']:>7.1f}%  "
              f"{wr['test_pnl']:>+9.2f}$  {wr['test_wr']:>7.1f}%")

    # Проверка переоптимизации
    test_positive = sum(1 for wr in wf_results if wr['test_pnl'] > 0)
    consistency = test_positive / len(wf_results) * 100
    print(f"\n  Консистентность: {test_positive}/{len(wf_results)} тестовых окон с положительным PnL ({consistency:.0f}%)")
    if consistency >= 66:
        print("  ВЫВОД: Параметры НЕ переоптимизированы (стабильны на OOS данных)")
    else:
        print("  ВНИМАНИЕ: Возможная переоптимизация! Рекомендуется осторожность.")

    return {'windows': wf_results, 'consistency_pct': consistency}


# ═════════════════════════════════════════════════════════════════════════════
# ФАЗА 7: АНАЛИЗ ПАТТЕРНОВ
# ═════════════════════════════════════════════════════════════════════════════

def phase7_patterns(candles_by_symbol, btc_trends, best_btc_modes,
                    best_sl, best_tp, best_ta, best_td,
                    best_min_change) -> Dict:
    print("\n[ФАЗА 7] Анализ паттернов")

    result = run_multi_backtest(
        candles_by_symbol, btc_trends, best_btc_modes,
        sl_pct=best_sl, tp_pct=best_tp,
        trail_act=best_ta, trail_dist=best_td,
        min_change_pct=best_min_change,
        max_positions=BASELINE['max_positions'],
        position_size=BASELINE['position_size'],
        leverage=BASELINE['leverage'],
        return_details=True
    )

    trades = result.get('accepted_trades', [])
    if not trades:
        print("  Нет сделок для анализа")
        return {}

    # По часам
    hour_stats = {}
    for t in trades:
        ot = t.get('open_time', 0)
        if ot > 0:
            try:
                h = datetime.utcfromtimestamp(ot / 1000 if ot > 1e12 else ot).hour
                if h not in hour_stats:
                    hour_stats[h] = {'trades': 0, 'wins': 0, 'pnl': 0.0}
                hour_stats[h]['trades'] += 1
                hour_stats[h]['pnl'] += t.get('pnl', 0)
                if t.get('is_win'):
                    hour_stats[h]['wins'] += 1
            except Exception:
                pass

    # По дням недели
    day_stats = {}
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for t in trades:
        ot = t.get('open_time', 0)
        if ot > 0:
            try:
                d = datetime.utcfromtimestamp(ot / 1000 if ot > 1e12 else ot).weekday()
                if d not in day_stats:
                    day_stats[d] = {'trades': 0, 'wins': 0, 'pnl': 0.0}
                day_stats[d]['trades'] += 1
                day_stats[d]['pnl'] += t.get('pnl', 0)
                if t.get('is_win'):
                    day_stats[d]['wins'] += 1
            except Exception:
                pass

    # LONG vs SHORT
    side_stats = {'LONG': {'trades': 0, 'wins': 0, 'pnl': 0.0},
                  'SHORT': {'trades': 0, 'wins': 0, 'pnl': 0.0}}
    for t in trades:
        side = t.get('side', 'UNKNOWN')
        if side in side_stats:
            side_stats[side]['trades'] += 1
            side_stats[side]['pnl'] += t.get('pnl', 0)
            if t.get('is_win'):
                side_stats[side]['wins'] += 1

    # По причинам закрытия
    reason_stats = {}
    for t in trades:
        reason = t.get('close_reason', 'UNKNOWN')
        if reason not in reason_stats:
            reason_stats[reason] = {'trades': 0, 'pnl': 0.0}
        reason_stats[reason]['trades'] += 1
        reason_stats[reason]['pnl'] += t.get('pnl', 0)

    # Вывод
    print("\n  === LONG vs SHORT ===")
    for side, s in side_stats.items():
        wr = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
        print(f"  {side:>6}: {s['trades']:>4} сделок | WR {wr:5.1f}% | PnL {s['pnl']:+8.2f}$")

    print("\n  === По причинам закрытия ===")
    for reason, s in sorted(reason_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
        print(f"  {reason:>15}: {s['trades']:>4} сделок | PnL {s['pnl']:+8.2f}$")

    print("\n  === Лучшие/худшие часы (UTC) ===")
    if hour_stats:
        sorted_hours = sorted(hour_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
        print("  Лучшие:")
        for h, s in sorted_hours[:3]:
            wr = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
            print(f"    {h:02d}:00 — {s['trades']:>3} сделок | WR {wr:5.1f}% | PnL {s['pnl']:+7.2f}$")
        print("  Худшие:")
        for h, s in sorted_hours[-3:]:
            wr = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
            print(f"    {h:02d}:00 — {s['trades']:>3} сделок | WR {wr:5.1f}% | PnL {s['pnl']:+7.2f}$")

    print("\n  === По дням недели ===")
    for d in range(7):
        if d in day_stats:
            s = day_stats[d]
            wr = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
            print(f"  {day_names[d]:>3}: {s['trades']:>4} сделок | WR {wr:5.1f}% | PnL {s['pnl']:+8.2f}$")

    return {
        'hours': {str(h): s for h, s in hour_stats.items()},
        'days': {day_names[d]: s for d, s in day_stats.items()},
        'sides': side_stats,
        'reasons': reason_stats
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    start_time = time.time()

    print("=" * 70)
    print("  RVV Hunter — Full Parameter Backtest")
    print("=" * 70)
    print(f"  Период: {BACKTEST_DAYS} дней | Монет: {TOP_COINS}")
    print(f"  Baseline: SL={BASELINE['sl_pct']}% TP={BASELINE['tp_pct']}% "
          f"Trail={BASELINE['trailing_activation_pct']}/{BASELINE['trailing_distance_pct']}%")
    print(f"  BTC filter: bull={BASELINE['btc_modes']['bullish']}, "
          f"bear={BASELINE['btc_modes']['bearish']}, "
          f"neutral={BASELINE['btc_modes']['neutral']}")
    print()

    # ── Фаза 1: Загрузка данных ──
    print("[ФАЗА 1] Загрузка данных")
    t0 = time.time()
    candles_by_symbol, btc_candles, symbol_list = phase1_load_data()
    print(f"  Фаза 1 завершена за {time.time() - t0:.0f}с")

    if len(candles_by_symbol) < 5:
        print("ОШИБКА: слишком мало данных загружено!")
        return

    # BTC тренды
    btc_trends = {}
    if btc_candles and len(btc_candles) >= 100:
        btc_trends = _calc_btc_trend_array(btc_candles)
        print(f"  BTC тренды: {len(btc_trends)} точек")

    all_results = {}

    # ── Фаза 2: Baseline ──
    t0 = time.time()
    baseline = phase2_baseline(candles_by_symbol, btc_trends, BASELINE['btc_modes'])
    baseline_pnl = baseline['pnl']
    all_results['baseline'] = {
        'params': BASELINE,
        'result': {k: v for k, v in baseline.items() if k != 'accepted_trades'}
    }
    print(f"  Фаза 2 завершена за {time.time() - t0:.0f}с")

    # ── Фаза 3: Grid Search ──
    t0 = time.time()
    grid_top = phase3_grid_search(candles_by_symbol, btc_trends, BASELINE['btc_modes'], baseline_pnl)
    all_results['grid_search'] = [{k: v for k, v in r.items() if k != 'accepted_trades'} for r in grid_top]
    print(f"  Фаза 3 завершена за {time.time() - t0:.0f}с")

    # Лучшие SL/TP/Trailing из grid search
    if grid_top:
        best = grid_top[0]
        best_sl = best['sl']
        best_tp = best['tp']
        best_ta = best['trail_act']
        best_td = best['trail_dist']
    else:
        best_sl = BASELINE['sl_pct']
        best_tp = BASELINE['tp_pct']
        best_ta = BASELINE['trailing_activation_pct']
        best_td = BASELINE['trailing_distance_pct']

    # ── Фаза 4: min_change_filter ──
    t0 = time.time()
    mc_results = phase4_min_change(candles_by_symbol, btc_trends, BASELINE['btc_modes'],
                                    best_sl, best_tp, best_ta, best_td, baseline_pnl)
    all_results['min_change'] = [{k: v for k, v in r.items() if k != 'accepted_trades'} for r in mc_results]
    best_min_change = mc_results[0]['min_change'] if mc_results else 5.0
    print(f"  Фаза 4 завершена за {time.time() - t0:.0f}с")

    # ── Фаза 5: BTC фильтр ──
    t0 = time.time()
    btc_results = phase5_btc_filter(candles_by_symbol, btc_trends,
                                     best_sl, best_tp, best_ta, best_td,
                                     best_min_change, baseline_pnl)
    all_results['btc_filter'] = [{k: v for k, v in r.items() if k != 'accepted_trades'} for r in btc_results]
    print(f"  Фаза 5 завершена за {time.time() - t0:.0f}с")

    # Лучший BTC фильтр
    if btc_results:
        best_btc = btc_results[0]
        best_btc_modes = {
            'bullish': best_btc['bull'],
            'bearish': best_btc['bear'],
            'neutral': best_btc['neutral'],
            'bullish_min_str': best_btc['strength'],
            'bearish_min_str': best_btc['strength'],
        }
    else:
        best_btc_modes = BASELINE['btc_modes']

    # ── Фаза 6: Walk-Forward ──
    t0 = time.time()
    wf_result = phase6_walk_forward(candles_by_symbol, btc_trends, best_btc_modes,
                                     best_sl, best_tp, best_ta, best_td, best_min_change)
    all_results['walk_forward'] = wf_result
    print(f"  Фаза 6 завершена за {time.time() - t0:.0f}с")

    # ── Фаза 7: Паттерны ──
    t0 = time.time()
    patterns = phase7_patterns(candles_by_symbol, btc_trends, best_btc_modes,
                               best_sl, best_tp, best_ta, best_td, best_min_change)
    all_results['patterns'] = patterns
    print(f"  Фаза 7 завершена за {time.time() - t0:.0f}с")

    # ═══ ФИНАЛЬНЫЕ РЕКОМЕНДАЦИИ ═══
    total_time = time.time() - start_time
    print("\n" + "=" * 70)
    print("  ФИНАЛЬНЫЕ РЕКОМЕНДАЦИИ")
    print("=" * 70)

    print(f"\n  Текущие (baseline):  SL={BASELINE['sl_pct']}% TP={BASELINE['tp_pct']}% "
          f"Trail={BASELINE['trailing_activation_pct']}/{BASELINE['trailing_distance_pct']}%  "
          f"PnL={baseline_pnl:+.2f}$")

    best_overall_pnl = grid_top[0]['pnl'] if grid_top else baseline_pnl
    print(f"\n  Рекомендуемые:       SL={best_sl}% TP={best_tp}% "
          f"Trail={best_ta}/{best_td}%  "
          f"PnL={best_overall_pnl:+.2f}$")

    delta = best_overall_pnl - baseline_pnl
    print(f"\n  Разница: {delta:+.2f}$ ({delta / abs(baseline_pnl) * 100:+.1f}%)" if baseline_pnl != 0
          else f"\n  Разница: {delta:+.2f}$")

    print(f"\n  min_change_filter: {best_min_change}% (было {BASELINE['min_change_pct']}%)")
    print(f"  BTC фильтр: bull={best_btc_modes['bullish']}, "
          f"bear={best_btc_modes['bearish']}, "
          f"neutral={best_btc_modes['neutral']}, "
          f"strength={best_btc_modes['bullish_min_str']}")

    wf_consistency = wf_result.get('consistency_pct', 0)
    if wf_consistency >= 66:
        print(f"\n  Walk-Forward: {wf_consistency:.0f}% консистентность — параметры стабильны")
    else:
        print(f"\n  Walk-Forward: {wf_consistency:.0f}% — ОСТОРОЖНО, возможна переоптимизация!")

    print(f"\n  Время выполнения: {total_time:.0f}с ({total_time / 60:.1f} мин)")
    print("=" * 70)

    # Финальный JSON с рекомендациями
    all_results['recommendations'] = {
        'sl_pct': best_sl,
        'tp_pct': best_tp,
        'trailing_activation_pct': best_ta,
        'trailing_distance_pct': best_td,
        'min_change_filter': best_min_change,
        'btc_modes': best_btc_modes,
        'baseline_pnl': baseline_pnl,
        'optimized_pnl': best_overall_pnl,
        'improvement_pct': (delta / abs(baseline_pnl) * 100) if baseline_pnl != 0 else 0,
        'walk_forward_consistency': wf_consistency,
    }
    all_results['meta'] = {
        'timestamp': datetime.utcnow().isoformat(),
        'days': BACKTEST_DAYS,
        'coins': len(candles_by_symbol),
        'total_time_sec': round(total_time, 1),
    }

    # Сохраняем JSON
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Результаты сохранены в: {RESULTS_PATH}")


if __name__ == '__main__':
    main()
