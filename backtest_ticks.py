#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — Tick-Level Backtest (micro-candle)
Бэктест на 1-секундных микросвечах из tick_collector.
Точность: ~1 секунда (vs 1 мин в backtest_1m, vs 15 мин в full_backtest).

Требует: data/ticks.db (запустить tick_collector.py минимум на неделю)

Использование:
  python backtest_ticks.py                    # config.json, все данные
  python backtest_ticks.py --days 7           # последние 7 дней
  python backtest_ticks.py --sl 0.8 --tp 4.0  # override
  python backtest_ticks.py --grid             # grid search
  python backtest_ticks.py --symbols BTC ETH  # конкретные символы
  python backtest_ticks.py --stats            # статистика БД
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from itertools import product

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
DB_PATH = os.path.join(BASE_DIR, 'data', 'ticks.db')
RESULTS_PATH = os.path.join(BASE_DIR, 'data', 'backtest_ticks_results.json')


def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── Indicators (same as backtest_1m) ───────────────────────────────────────

def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
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
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))
    return rsi_values


def calculate_bollinger(closes, period=20):
    if len(closes) < period:
        return 50.0
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = middle + 2.0 * std
    lower = middle - 2.0 * std
    if (upper - lower) > 0:
        return (closes[-1] - lower) / (upper - lower) * 100
    return 50.0


def calculate_macd(closes):
    if len(closes) < 35:
        return 0, 0, 0
    def ema_s(data, p):
        r = [data[0]]
        k = 2 / (p + 1)
        for i in range(1, len(data)):
            r.append(data[i] * k + r[-1] * (1 - k))
        return r
    e12 = ema_s(closes, 12)
    e26 = ema_s(closes, 26)
    ml = [e12[i] - e26[i] for i in range(len(closes))]
    sl = ema_s(ml, 9)
    return ml[-1], sl[-1], ml[-1] - sl[-1]


def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02 if closes else 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs[-period:]) / period


def calculate_ema(data, period):
    if not data or len(data) < period:
        return data[-1] if data else 0
    ema = data[0]
    k = 2 / (period + 1)
    for v in data[1:]:
        ema = v * k + ema * (1 - k)
    return ema


# ─── Database ───────────────────────────────────────────────────────────────

def db_stats(conn: sqlite3.Connection):
    """Показать статистику БД."""
    cursor = conn.execute("SELECT COUNT(*) FROM micro_candles")
    total = cursor.fetchone()[0]

    cursor = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM micro_candles")
    row = cursor.fetchone()
    min_ts, max_ts = row

    cursor = conn.execute("SELECT symbol, COUNT(*) FROM micro_candles GROUP BY symbol ORDER BY COUNT(*) DESC")
    by_symbol = cursor.fetchall()

    print(f"\n  БД: {DB_PATH}")
    print(f"  Записей: {total:,}")
    if min_ts and max_ts:
        start = datetime.utcfromtimestamp(min_ts / 1000)
        end = datetime.utcfromtimestamp(max_ts / 1000)
        hours = (max_ts - min_ts) / 3600000
        print(f"  Период: {start} — {end} ({hours:.1f}ч)")
    print(f"  Символов: {len(by_symbol)}")
    if by_symbol:
        print(f"  Топ-10:")
        for sym, cnt in by_symbol[:10]:
            print(f"    {sym:>8}: {cnt:>10,} записей")

    db_size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"  Размер БД: {db_size_mb:.1f} MB")


def load_micro_candles(conn: sqlite3.Connection, symbol: str,
                       since_ms: int = 0) -> List[Dict]:
    """Загрузить микросвечи для символа."""
    cursor = conn.execute(
        "SELECT timestamp, open, high, low, close, volume, trade_count "
        "FROM micro_candles WHERE symbol = ? AND timestamp >= ? "
        "ORDER BY timestamp",
        (symbol, since_ms)
    )
    return [
        {'timestamp': r[0], 'open': r[1], 'high': r[2], 'low': r[3],
         'close': r[4], 'volume': r[5], 'trade_count': r[6]}
        for r in cursor.fetchall()
    ]


def get_available_symbols(conn: sqlite3.Connection, since_ms: int = 0) -> List[str]:
    """Символы с данными."""
    cursor = conn.execute(
        "SELECT symbol, COUNT(*) as cnt FROM micro_candles "
        "WHERE timestamp >= ? GROUP BY symbol HAVING cnt >= 1000 "
        "ORDER BY cnt DESC",
        (since_ms,)
    )
    return [r[0] for r in cursor.fetchall()]


def aggregate_to_5m(micro: List[Dict]) -> List[Dict]:
    """Агрегировать микросвечи в 5m."""
    candles_5m = []
    i = 0
    while i < len(micro):
        ts = micro[i]['timestamp']
        boundary = ts - (ts % 300000)
        group = []
        while i < len(micro) and micro[i]['timestamp'] < boundary + 300000:
            group.append(micro[i])
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


# ─── Scoring (5m based, same as backtest_1m) ───────────────────────────────

def compute_signals_5m(candles_5m: List[Dict], entry_filters: Dict) -> List[Dict]:
    """Scoring на 5m свечах."""
    if len(candles_5m) < 50:
        return []

    closes = [c['close'] for c in candles_5m]
    highs = [c['high'] for c in candles_5m]
    lows = [c['low'] for c in candles_5m]
    volumes = [c['volume'] for c in candles_5m]
    rsi_values = calculate_rsi(closes, 14)

    signals = []
    lookback_24h = 288

    for i in range(50, len(candles_5m)):
        rsi = rsi_values[i] if i < len(rsi_values) else 50
        bb_pct = calculate_bollinger(closes[:i + 1], 20)
        _, _, macd_hist = calculate_macd(closes[:i + 1])
        atr = calculate_atr(highs[:i + 1], lows[:i + 1], closes[:i + 1], 14)
        atr_pct = (atr / closes[i] * 100) if closes[i] > 0 else 2.0

        change_24h = 0.0
        if i >= lookback_24h:
            old = closes[i - lookback_24h]
            if old > 0:
                change_24h = (closes[i] - old) / old * 100

        vol_ratio = 1.0
        if i >= 10:
            avg_vol = sum(volumes[max(0, i - 20):i]) / max(min(20, i), 1)
            if avg_vol > 0:
                vol_ratio = volumes[i] / avg_vol

        # Entry filters
        is_parabolic = False
        if entry_filters.get('parabolic_enabled', True) and i >= 16:
            mult = entry_filters.get('parabolic_multiplier', 3.0)
            ranges = [highs[j] - lows[j] for j in range(max(0, i - 15), i + 1)]
            cr = ranges[-1]
            ar = sum(ranges[:-1]) / max(len(ranges) - 1, 1)
            if ar > 0 and cr / ar > mult:
                is_parabolic = True

        rvol_ok = True
        if entry_filters.get('rvol_enabled', True):
            if vol_ratio < entry_filters.get('min_rvol', 1.2):
                rvol_ok = False

        trend_1h = None
        if entry_filters.get('multi_tf_enabled', True) and i >= 60:
            ep = entry_filters.get('multi_tf_ema_period', 20)
            hc = closes[max(0, i - 12 * ep):i + 1:12]
            if len(hc) >= ep:
                ev = calculate_ema(hc, ep)
                ed = (hc[-1] - ev) / ev * 100 if ev > 0 else 0
                if ed > 0.3:
                    trend_1h = "UP"
                elif ed < -0.3:
                    trend_1h = "DOWN"

        # Scoring
        score = 0
        if rsi >= 75: score += 3
        elif rsi >= 70: score += 2
        elif rsi <= 25: score -= 3
        elif rsi <= 30: score -= 2

        if bb_pct >= 95: score += 2
        elif bb_pct >= 80: score += 1
        elif bb_pct <= 5: score -= 2
        elif bb_pct <= 20: score -= 1

        if macd_hist < 0 and score > 0: score += 1
        elif macd_hist > 0 and score < 0: score -= 1

        if change_24h >= 8: score += 1
        elif change_24h <= -8: score -= 1

        action = "WAIT"
        if score >= 3:
            action = "SHORT"
        elif score <= -3:
            action = "LONG"

        if action != "WAIT":
            if is_parabolic:
                action = "WAIT"
            elif not rvol_ok:
                action = "WAIT"
            elif trend_1h:
                if action == "LONG" and trend_1h == "DOWN":
                    action = "WAIT"
                elif action == "SHORT" and trend_1h == "UP":
                    action = "WAIT"

        if action != "WAIT":
            signals.append({
                'ts': candles_5m[i]['timestamp'],
                'action': action,
                'score': score,
                'atr_pct': atr_pct,
                'price': closes[i],
            })

    return signals


# ─── Trade Simulation (tick-level) ─────────────────────────────────────────

def simulate_trades_ticks(micro: List[Dict], signals: List[Dict],
                          sl_pct: float, tp_pct: float,
                          trail_act: float, trail_dist: float,
                          position_size: float, leverage: int,
                          atr_adaptive: bool = False,
                          atr_sl_mult: float = 1.5,
                          atr_trail_act_mult: float = 3.0,
                          atr_trail_dist_mult: float = 0.7,
                          commission_pct: float = 0.08,
                          slippage_pct: float = 0.05,
                          ) -> List[Dict]:
    """Симулировать сделки на микросвечах (1 секунда)."""

    # Build timestamp → index mapping
    ts_idx = {}
    for i, c in enumerate(micro):
        ts_idx[c['timestamp']] = i

    trades = []

    for sig in sorted(signals, key=lambda s: s['ts']):
        entry_idx = None
        # Find micro-candle at/after signal time
        for offset in range(0, 300 * 1000, 1000):  # 5 min range, 1s step
            entry_idx = ts_idx.get(sig['ts'] + offset)
            if entry_idx is not None:
                break
        if entry_idx is None:
            continue

        action = sig['action']
        entry_price = micro[entry_idx]['close']
        if action == 'SHORT':
            entry_price *= (1 - slippage_pct / 100)
        else:
            entry_price *= (1 + slippage_pct / 100)

        # ATR adaptive
        actual_sl = sl_pct
        actual_trail_act = trail_act
        actual_trail_dist = trail_dist
        if atr_adaptive:
            ap = sig.get('atr_pct', 2.0)
            actual_sl = max(sl_pct, ap * atr_sl_mult)
            actual_trail_act = max(trail_act, ap * atr_trail_act_mult)
            actual_trail_dist = max(trail_dist, ap * atr_trail_dist_mult)

        if action == 'SHORT':
            sl_price = entry_price * (1 + actual_sl / 100)
            tp_price = entry_price * (1 - tp_pct / 100)
        else:
            sl_price = entry_price * (1 - actual_sl / 100)
            tp_price = entry_price * (1 + tp_pct / 100)

        trailing_active = False
        best_price = entry_price
        close_reason = None
        close_price = None
        close_ts = 0
        max_pnl_pct = 0.0

        # Walk micro-candles
        for j in range(entry_idx + 1, len(micro)):
            c = micro[j]
            high, low = c['high'], c['low']

            if action == 'SHORT':
                profit_pct = (entry_price - low) / entry_price * 100
                max_pnl_pct = max(max_pnl_pct, profit_pct)
                if low < best_price:
                    best_price = low
                if profit_pct >= actual_trail_act and not trailing_active:
                    trailing_active = True
                    ns = best_price * (1 + actual_trail_dist / 100)
                    if ns < entry_price:
                        sl_price = ns
                if trailing_active:
                    ns = best_price * (1 + actual_trail_dist / 100)
                    if ns < entry_price and ns < sl_price:
                        sl_price = ns
                if high >= sl_price:
                    close_reason = 'TRAILING_STOP' if trailing_active else 'STOP_LOSS'
                    close_price = sl_price
                    close_ts = c['timestamp']
                    break
                if low <= tp_price:
                    close_reason = 'TAKE_PROFIT'
                    close_price = tp_price
                    close_ts = c['timestamp']
                    break
            else:  # LONG
                profit_pct = (high - entry_price) / entry_price * 100
                max_pnl_pct = max(max_pnl_pct, profit_pct)
                if high > best_price:
                    best_price = high
                if profit_pct >= actual_trail_act and not trailing_active:
                    trailing_active = True
                    ns = best_price * (1 - actual_trail_dist / 100)
                    if ns > entry_price:
                        sl_price = ns
                if trailing_active:
                    ns = best_price * (1 - actual_trail_dist / 100)
                    if ns > entry_price and ns > sl_price:
                        sl_price = ns
                if low <= sl_price:
                    close_reason = 'TRAILING_STOP' if trailing_active else 'STOP_LOSS'
                    close_price = sl_price
                    close_ts = c['timestamp']
                    break
                if high >= tp_price:
                    close_reason = 'TAKE_PROFIT'
                    close_price = tp_price
                    close_ts = c['timestamp']
                    break

        if close_reason is None:
            close_price = micro[-1]['close']
            close_ts = micro[-1]['timestamp']
            close_reason = 'END_OF_DATA'

        if close_reason == 'TAKE_PROFIT':
            pnl_pct = tp_pct
        else:
            if action == 'SHORT':
                pnl_pct = (entry_price - close_price) / entry_price * 100
            else:
                pnl_pct = (close_price - entry_price) / entry_price * 100

        pnl_usd = (pnl_pct / 100) * position_size * leverage
        pnl_usd -= position_size * leverage * commission_pct / 100

        trades.append({
            'side': action, 'entry_price': entry_price,
            'close_price': close_price,
            'entry_ts': micro[entry_idx]['timestamp'],
            'close_ts': close_ts,
            'pnl_pct': pnl_pct, 'pnl_usd': pnl_usd,
            'is_win': pnl_usd > 0,
            'close_reason': close_reason,
            'trailing_active': trailing_active,
            'max_pnl_pct': max_pnl_pct,
            'sl_pct_used': actual_sl,
            'duration_sec': (close_ts - micro[entry_idx]['timestamp']) / 1000,
        })

    return trades


# ─── Multi-symbol run ──────────────────────────────────────────────────────

def run_backtest(conn: sqlite3.Connection, cfg: Dict,
                 symbols: List[str] = None, days: int = None,
                 sl_override: float = None, tp_override: float = None,
                 ) -> Dict:
    """Run backtest on all symbols."""
    trading = cfg.get('trading', {})
    entry_filters = cfg.get('entry_filters', {})

    sl = sl_override or trading.get('stop_loss_pct', 0.8)
    tp = tp_override or trading.get('take_profit_pct', 7.75)
    trail_act = trading.get('trailing_activation_pct', 2.0)
    trail_dist = trading.get('trailing_distance_pct', 0.4)
    position_size = trading.get('position_size', 10)
    leverage = trading.get('leverage', 2)
    max_positions = trading.get('max_positions', 10)
    atr_adaptive = trading.get('atr_adaptive_sl', False)

    since_ms = 0
    if days:
        since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

    if not symbols:
        symbols = get_available_symbols(conn, since_ms)

    all_trades = []

    for sym in symbols:
        micro = load_micro_candles(conn, sym, since_ms)
        if len(micro) < 5000:  # min ~1.5h of data
            continue

        # Aggregate to 5m for scoring
        candles_5m = aggregate_to_5m(micro)
        if len(candles_5m) < 50:
            continue

        signals = compute_signals_5m(candles_5m, entry_filters)
        if not signals:
            continue

        trades = simulate_trades_ticks(
            micro, signals,
            sl_pct=sl, tp_pct=tp,
            trail_act=trail_act, trail_dist=trail_dist,
            position_size=position_size, leverage=leverage,
            atr_adaptive=atr_adaptive,
            atr_sl_mult=trading.get('atr_sl_multiplier', 1.5),
            atr_trail_act_mult=trading.get('atr_trail_activation_multiplier', 3.0),
            atr_trail_dist_mult=trading.get('atr_trail_distance_multiplier', 0.7),
        )

        for t in trades:
            t['symbol'] = sym
            all_trades.append(t)

    # Global position limit
    if max_positions > 0 and all_trades:
        all_trades.sort(key=lambda t: t['entry_ts'])
        accepted = []
        active_slots = []
        for trade in all_trades:
            ot, ct = trade['entry_ts'], trade['close_ts']
            sym = trade['symbol']
            active_slots = [(c, s) for c, s in active_slots if c > ot]
            if sym in {s for _, s in active_slots}:
                continue
            if len(active_slots) < max_positions:
                accepted.append(trade)
                active_slots.append((ct, sym))
        all_trades = accepted

    # Aggregate
    wins = sum(1 for t in all_trades if t['is_win'])
    gp = sum(t['pnl_usd'] for t in all_trades if t['pnl_usd'] > 0)
    gl = sum(abs(t['pnl_usd']) for t in all_trades if t['pnl_usd'] <= 0)

    return {
        'trades': len(all_trades), 'wins': wins, 'losses': len(all_trades) - wins,
        'win_rate': wins / len(all_trades) * 100 if all_trades else 0,
        'pnl': gp - gl, 'gross_profit': gp, 'gross_loss': gl,
        'trailing_wins': sum(1 for t in all_trades if t.get('close_reason') == 'TRAILING_STOP' and t['is_win']),
        'avg_duration_sec': sum(t.get('duration_sec', 0) for t in all_trades) / len(all_trades) if all_trades else 0,
        'params': {'sl': sl, 'tp': tp, 'trail_act': trail_act, 'trail_dist': trail_dist},
        'by_reason': {
            r: {'count': sum(1 for t in all_trades if t['close_reason'] == r),
                'pnl': sum(t['pnl_usd'] for t in all_trades if t['close_reason'] == r)}
            for r in set(t['close_reason'] for t in all_trades)
        } if all_trades else {},
        'by_side': {
            s: {'count': sum(1 for t in all_trades if t['side'] == s),
                'wins': sum(1 for t in all_trades if t['side'] == s and t['is_win']),
                'pnl': sum(t['pnl_usd'] for t in all_trades if t['side'] == s)}
            for s in ['LONG', 'SHORT']
        },
        'symbols_used': len(set(t['symbol'] for t in all_trades)),
        'trade_details': all_trades,
    }


def print_result(r: Dict, label: str = ""):
    if label:
        print(f"\n  === {label} ===")
    if r['trades'] == 0:
        print("  0 сделок (нет данных или нет сигналов)")
        return

    print(f"  Сделок: {r['trades']} | Побед: {r['wins']} | Поражений: {r['losses']}")
    print(f"  Win Rate: {r['win_rate']:.1f}%")
    print(f"  PnL: {r['pnl']:+.2f}$ (profit {r['gross_profit']:.2f}$ / loss {r['gross_loss']:.2f}$)")
    print(f"  Trailing wins: {r['trailing_wins']}")
    if r.get('avg_duration_sec'):
        dur_min = r['avg_duration_sec'] / 60
        print(f"  Avg duration: {dur_min:.0f} мин")
    if r.get('symbols_used'):
        print(f"  Символов: {r['symbols_used']}")
    p = r.get('params', {})
    if p:
        print(f"  Params: SL={p['sl']}% TP={p['tp']}% Trail={p['trail_act']}/{p['trail_dist']}%")

    if r.get('by_reason'):
        print("  По причинам:")
        for reason, d in sorted(r['by_reason'].items(), key=lambda x: x[1]['pnl'], reverse=True):
            print(f"    {reason:>15}: {d['count']:>4} | PnL {d['pnl']:+.2f}$")
    if r.get('by_side'):
        print("  По сторонам:")
        for side, d in r['by_side'].items():
            wr = d['wins'] / d['count'] * 100 if d['count'] > 0 else 0
            print(f"    {side:>6}: {d['count']:>4} | WR {wr:.1f}% | PnL {d['pnl']:+.2f}$")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='RVV Hunter Tick-Level Backtest')
    parser.add_argument('--days', type=int, help='Последние N дней')
    parser.add_argument('--symbols', nargs='+', help='Конкретные символы')
    parser.add_argument('--sl', type=float, help='Override SL%%')
    parser.add_argument('--tp', type=float, help='Override TP%%')
    parser.add_argument('--grid', action='store_true', help='Grid search SL/TP')
    parser.add_argument('--stats', action='store_true', help='Только статистика БД')
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"ОШИБКА: {DB_PATH} не найден!")
        print("Запустите tick_collector.py для сбора данных.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    if args.stats:
        db_stats(conn)
        conn.close()
        return

    cfg = load_config()

    print("=" * 70)
    print("  RVV Hunter — Tick-Level Backtest (micro-candles)")
    print("=" * 70)

    # DB info
    db_stats(conn)

    symbols = [s.upper() for s in args.symbols] if args.symbols else None

    print(f"\n[1] Запуск бэктеста...")
    t0 = time.time()
    result = run_backtest(conn, cfg, symbols=symbols, days=args.days,
                          sl_override=args.sl, tp_override=args.tp)
    elapsed = time.time() - t0
    print(f"  Завершён за {elapsed:.1f}с")

    print_result(result, "РЕЗУЛЬТАТ")

    # Grid search
    if args.grid:
        print(f"\n[2] Grid Search SL/TP...")
        sl_values = [0.5, 0.8, 1.0, 1.5, 2.0]
        tp_values = [2.0, 3.0, 4.0, 5.0, 6.0, 7.75]
        grid_results = []

        for sl, tp in product(sl_values, tp_values):
            if tp <= sl:
                continue
            r = run_backtest(conn, cfg, symbols=symbols, days=args.days,
                             sl_override=sl, tp_override=tp)
            grid_results.append({
                'sl': sl, 'tp': tp, 'trades': r['trades'],
                'win_rate': r['win_rate'], 'pnl': r['pnl'],
            })

        grid_results.sort(key=lambda x: x['pnl'], reverse=True)
        print(f"\n  Топ-10:")
        print(f"  {'#':>3}  {'SL':>5}  {'TP':>5}  {'Trades':>6}  {'WR%':>6}  {'PnL':>10}")
        print("  " + "-" * 45)
        for i, r in enumerate(grid_results[:10]):
            print(f"  {i + 1:>3}  {r['sl']:>5.1f}  {r['tp']:>5.1f}  {r['trades']:>6}  "
                  f"{r['win_rate']:>5.1f}%  {r['pnl']:>+9.2f}$")

        result['grid_search'] = grid_results[:10]

    # Save
    save = {k: v for k, v in result.items() if k != 'trade_details'}
    save['timestamp'] = datetime.utcnow().isoformat()
    with open(RESULTS_PATH, 'w') as f:
        json.dump(save, f, indent=2, default=str)
    print(f"\n  Результаты: {RESULTS_PATH}")

    conn.close()
    print("\n" + "=" * 70)


if __name__ == '__main__':
    main()
