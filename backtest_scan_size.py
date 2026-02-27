#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RVV Hunter - Backtest: влияние размера сканирования на доходность
Тестирует max_to_analyze = [5, 10, 15, 25, 50, 100]
"""

import ccxt
import time
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

# ─── Параметры бота (из config.json и trader.py) ───────────────────────────
MIN_CHANGE_FILTER = 5.0       # Минимальное изменение 24ч для отбора
CONFIDENCE_THRESHOLD = 75     # Порог уверенности для входа (%)
STOP_LOSS_PCT = 3.5           # % стоп-лосс от цены входа
TAKE_PROFIT_PCT = 12.0        # % тейк-профит
TRAILING_ACTIVATION_PCT = 2.0 # % активации трейлинга
TRAILING_DISTANCE_PCT = 0.8   # % дистанция трейлинга
POSITION_SIZE = 25            # USD на позицию
LEVERAGE = 5                  # Плечо
MAX_POSITIONS = 5             # Максимум одновременных позиций
TOP_PAIRS_TOTAL = 300         # Сколько пар берём с биржи

BACKTEST_DAYS = 14            # Период бэктеста
SCAN_INTERVAL_H = 1           # Интервал сканирования (часы)
MIN_CANDLES = 50              # Минимум свечей для анализа
MIN_VOLATILITY = 0.001        # Минимальная волатильность (1%)

SCAN_SIZES = [5, 10, 15, 25, 50, 100]

# ─── Технические индикаторы ────────────────────────────────────────────────

def calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period+1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_ema(closes: np.ndarray, period: int) -> float:
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_bollinger(closes: np.ndarray, period: int = 20) -> Tuple[float, float, float]:
    if len(closes) < period:
        mid = closes[-1]
        return mid, mid * 1.02, mid * 0.98
    recent = closes[-period:]
    mid = np.mean(recent)
    std = np.std(recent)
    return mid, mid + 2 * std, mid - 2 * std

def calc_macd(closes: np.ndarray) -> float:
    if len(closes) < 26:
        return 0.0
    ema12 = calc_ema(closes[-26:], 12)
    ema26 = calc_ema(closes[-26:], 26)
    return ema12 - ema26

def calc_volatility(closes: np.ndarray, period: int = 50) -> float:
    if len(closes) < 2:
        return 0.0
    recent = closes[-min(period, len(closes)):]
    if recent[-1] == 0:
        return 0.0
    return (recent.max() - recent.min()) / recent[-1]

def analyze_signal(closes: np.ndarray, change_24h: float) -> Tuple[str, float]:
    """
    Имитация AI-анализа на основе технических индикаторов.
    Возвращает (action, confidence).
    """
    rsi = calc_rsi(closes)
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    macd = calc_macd(closes)
    bb_mid, bb_upper, bb_lower = calc_bollinger(closes)
    price = closes[-1]

    bull_score = 0
    bear_score = 0

    # LONG сигналы (монета упала — ищем разворот вверх)
    if rsi < 30: bull_score += 3
    elif rsi < 40: bull_score += 2
    elif rsi < 50: bull_score += 1

    if ema9 > ema21: bull_score += 2
    if macd > 0: bull_score += 1
    if price < bb_lower: bull_score += 3
    elif price < bb_mid: bull_score += 1
    if change_24h < -10: bull_score += 2
    elif change_24h < -5: bull_score += 1

    # SHORT сигналы (монета выросла — ищем разворот вниз)
    if rsi > 70: bear_score += 3
    elif rsi > 60: bear_score += 2
    elif rsi > 50: bear_score += 1

    if ema9 < ema21: bear_score += 2
    if macd < 0: bear_score += 1
    if price > bb_upper: bear_score += 3
    elif price > bb_mid: bear_score += 1
    if change_24h > 10: bear_score += 2
    elif change_24h > 5: bear_score += 1

    max_score = 12
    if bull_score > bear_score and bull_score >= 5:
        confidence = 60 + (bull_score / max_score) * 40
        return 'LONG', min(confidence, 98)
    elif bear_score > bull_score and bear_score >= 5:
        confidence = 60 + (bear_score / max_score) * 40
        return 'SHORT', min(confidence, 98)
    else:
        confidence = max(bull_score, bear_score) / max_score * 60
        return 'WAIT', confidence

# ─── Симуляция торговли ────────────────────────────────────────────────────

def simulate_trade(
    candles_after: List, action: str, entry_price: float
) -> Dict:
    """Симулирует одну сделку по последующим свечам."""
    sl_pct = STOP_LOSS_PCT / 100
    tp_pct = TAKE_PROFIT_PCT / 100
    trail_act = TRAILING_ACTIVATION_PCT / 100
    trail_dist = TRAILING_DISTANCE_PCT / 100

    if action == 'LONG':
        sl = entry_price * (1 - sl_pct)
        tp = entry_price * (1 + tp_pct)
    else:
        sl = entry_price * (1 + sl_pct)
        tp = entry_price * (1 - tp_pct)

    trailing_active = False
    trailing_stop = None
    close_reason = None
    exit_price = entry_price
    duration_candles = 0

    for candle in candles_after[:200]:  # максимум 200 свечей (50 часов на 15m)
        high = candle[2]
        low = candle[3]
        close = candle[4]
        duration_candles += 1

        if action == 'LONG':
            # Активация трейлинга
            if not trailing_active and high >= entry_price * (1 + trail_act):
                trailing_active = True
                trailing_stop = high * (1 - trail_dist)

            if trailing_active:
                if high * (1 - trail_dist) > (trailing_stop or 0):
                    trailing_stop = high * (1 - trail_dist)
                if low <= trailing_stop:
                    exit_price = trailing_stop
                    close_reason = 'TRAILING'
                    break

            if low <= sl:
                exit_price = sl
                close_reason = 'STOP_LOSS'
                break
            if high >= tp:
                exit_price = tp
                close_reason = 'TAKE_PROFIT'
                break
        else:  # SHORT
            if not trailing_active and low <= entry_price * (1 - trail_act):
                trailing_active = True
                trailing_stop = low * (1 + trail_dist)

            if trailing_active:
                if low * (1 + trail_dist) < (trailing_stop or float('inf')):
                    trailing_stop = low * (1 + trail_dist)
                if high >= trailing_stop:
                    exit_price = trailing_stop
                    close_reason = 'TRAILING'
                    break

            if high >= sl:
                exit_price = sl
                close_reason = 'STOP_LOSS'
                break
            if low <= tp:
                exit_price = tp
                close_reason = 'TAKE_PROFIT'
                break

    if close_reason is None:
        exit_price = candles_after[-1][4] if candles_after else entry_price
        close_reason = 'TIMEOUT'

    if action == 'LONG':
        pnl_pct = (exit_price - entry_price) / entry_price * 100 * LEVERAGE
    else:
        pnl_pct = (entry_price - exit_price) / entry_price * 100 * LEVERAGE

    pnl_usdt = POSITION_SIZE * pnl_pct / 100

    return {
        'pnl_pct': pnl_pct,
        'pnl_usdt': pnl_usdt,
        'close_reason': close_reason,
        'duration_candles': duration_candles,
        'win': pnl_usdt > 0
    }

# ─── Основная логика бэктеста ──────────────────────────────────────────────

def run_backtest():
    print("=" * 65)
    print("  RVV Hunter Backtest — влияние max_to_analyze на доходность")
    print("=" * 65)
    print(f"  Период: {BACKTEST_DAYS} дней | Плечо: {LEVERAGE}x | Размер: ${POSITION_SIZE}")
    print(f"  SL: {STOP_LOSS_PCT}% | TP: {TAKE_PROFIT_PCT}% | min_change: {MIN_CHANGE_FILTER}%")
    print(f"  Порог уверенности: {CONFIDENCE_THRESHOLD}%")
    print()

    # ── Подключение к бирже ──
    print("[1/3] Подключение к Binance Futures через TOR...")
    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'proxies': {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050',
        },
    })

    # ── Получение топ пар ──
    print("[2/3] Загрузка списка пар...")
    try:
        tickers = exchange.fetch_tickers()
    except Exception as e:
        print(f"  Ошибка: {e}")
        return

    pairs = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith('/USDT:USDT'):
            continue
        vol = ticker.get('quoteVolume', 0) or 0
        if vol > 0:
            pairs.append({'symbol': symbol, 'volume': vol})

    pairs.sort(key=lambda x: x['volume'], reverse=True)
    top_pairs = [p['symbol'] for p in pairs[:TOP_PAIRS_TOTAL]]
    print(f"  Получено {len(top_pairs)} пар (отсортированы по объёму)")

    # ── Загрузка OHLCV ──
    print(f"[3/3] Загрузка свечей за {BACKTEST_DAYS} дней (15m)...")
    since = int((datetime.utcnow() - timedelta(days=BACKTEST_DAYS)).timestamp() * 1000)
    # 15m свечей за 14 дней = ~1344 на пару
    limit = min(BACKTEST_DAYS * 24 * 4 + 50, 1500)

    ohlcv_cache: Dict[str, List] = {}
    failed = 0
    for i, symbol in enumerate(top_pairs):
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(top_pairs)} пар загружено...")
        try:
            candles = exchange.fetch_ohlcv(symbol, '15m', since=since, limit=limit)
            if candles and len(candles) >= MIN_CANDLES:
                ohlcv_cache[symbol] = candles
        except Exception as e:
            failed += 1
        time.sleep(0.12)

    print(f"  Загружено {len(ohlcv_cache)} пар ({failed} ошибок)")
    available = list(ohlcv_cache.keys())  # уже отсортированы по объёму

    # ── Бэктест ──
    print()
    print("Запуск симуляции...\n")

    results: Dict[int, Dict] = {}

    for scan_size in SCAN_SIZES:
        # Берём только те пары которые реально есть
        pairs_to_scan = [s for s in available[:scan_size] if s in ohlcv_cache]
        if not pairs_to_scan:
            continue

        trades_all = []
        open_positions = 0
        scan_count = 0

        # Шагаем по времени с интервалом SCAN_INTERVAL_H часов
        start_ts = since
        end_ts = int(datetime.utcnow().timestamp() * 1000) - 200 * 15 * 60 * 1000  # минус 50ч для выхода

        ts = start_ts
        step_ms = SCAN_INTERVAL_H * 60 * 60 * 1000

        while ts < end_ts:
            scan_count += 1
            open_positions = 0  # упрощение: не держим состояние между сканами

            # Определяем индекс свечи для текущего ts
            candidates = []
            for symbol in pairs_to_scan:
                candles = ohlcv_cache[symbol]
                # Находим индекс свечи для текущего ts
                idx = None
                for ci in range(len(candles)):
                    if candles[ci][0] >= ts:
                        idx = ci
                        break
                if idx is None or idx < 96:  # нужно минимум 24ч истории (96 * 15m)
                    continue

                # Считаем 24h change
                price_now = candles[idx][4]
                price_24h_ago = candles[idx - 96][4]
                if price_24h_ago <= 0:
                    continue
                change_24h = (price_now - price_24h_ago) / price_24h_ago * 100

                # Фильтр по изменению
                if abs(change_24h) < MIN_CHANGE_FILTER:
                    continue

                # Фильтр волатильности
                closes = np.array([c[4] for c in candles[max(0, idx-50):idx+1]])
                vol = calc_volatility(closes)
                if vol < MIN_VOLATILITY:
                    continue

                candidates.append({
                    'symbol': symbol,
                    'idx': idx,
                    'change_24h': change_24h,
                    'closes': closes,
                    'candles': candles,
                })

            # Анализируем кандидатов
            for cand in candidates:
                if open_positions >= MAX_POSITIONS:
                    break

                action, confidence = analyze_signal(cand['closes'], cand['change_24h'])

                if action == 'WAIT' or confidence < CONFIDENCE_THRESHOLD:
                    continue

                # Открываем сделку
                entry_price = cand['candles'][cand['idx']][4]
                future_candles = cand['candles'][cand['idx']+1:]

                if len(future_candles) < 10:
                    continue

                trade = simulate_trade(future_candles, action, entry_price)
                trade['symbol'] = cand['symbol']
                trade['action'] = action
                trade['confidence'] = confidence
                trade['change_24h'] = cand['change_24h']
                trades_all.append(trade)
                open_positions += 1

            ts += step_ms

        # Считаем результаты
        if not trades_all:
            results[scan_size] = {
                'trades': 0, 'wins': 0, 'losses': 0,
                'win_rate': 0, 'total_pnl': 0, 'avg_pnl': 0,
                'scan_count': scan_count,
            }
            continue

        wins = sum(1 for t in trades_all if t['win'])
        losses = len(trades_all) - wins
        total_pnl = sum(t['pnl_usdt'] for t in trades_all)
        avg_pnl = total_pnl / len(trades_all)
        win_rate = wins / len(trades_all) * 100

        # Разбивка по причинам закрытия
        reasons = {}
        for t in trades_all:
            r = t['close_reason']
            reasons[r] = reasons.get(r, 0) + 1

        # Топ монеты
        sym_pnl: Dict[str, float] = {}
        for t in trades_all:
            sym_pnl[t['symbol']] = sym_pnl.get(t['symbol'], 0) + t['pnl_usdt']
        top_sym = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:3]

        results[scan_size] = {
            'trades': len(trades_all),
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'scan_count': scan_count,
            'reasons': reasons,
            'top_symbols': top_sym,
        }

    # ── Вывод результатов ──
    print("=" * 65)
    print(f"  {'scan':>5}  {'сделок':>7}  {'winrate':>8}  {'total_pnl':>10}  {'avg_pnl':>8}")
    print("-" * 65)

    baseline_pnl = None
    for scan_size in SCAN_SIZES:
        if scan_size not in results:
            continue
        r = results[scan_size]
        if r['trades'] == 0:
            print(f"  {scan_size:>5}  {'0':>7}  {'—':>8}  {'—':>10}  {'—':>8}")
            continue

        if baseline_pnl is None:
            baseline_pnl = r['total_pnl']
            delta_str = "(база)"
        else:
            delta = r['total_pnl'] - baseline_pnl
            delta_str = f"{delta:+.2f}$"

        marker = " ◀ текущий" if scan_size == 15 else ""
        print(f"  {scan_size:>5}  {r['trades']:>7}  {r['win_rate']:>7.1f}%  {r['total_pnl']:>+9.2f}$  {r['avg_pnl']:>+7.2f}$  {delta_str}{marker}")

    print("=" * 65)
    print()
    print("Детали по каждому размеру сканирования:")
    print()
    for scan_size in SCAN_SIZES:
        if scan_size not in results:
            continue
        r = results[scan_size]
        marker = " ◀ текущий" if scan_size == 15 else ""
        print(f"  max_to_analyze = {scan_size}{marker}")
        if r['trades'] == 0:
            print("    Нет сделок")
            continue
        print(f"    Сделок: {r['trades']} (побед: {r['wins']}, потерь: {r['losses']})")
        print(f"    Win rate: {r['win_rate']:.1f}%")
        print(f"    PnL суммарный: {r['total_pnl']:+.2f}$")
        print(f"    PnL средний: {r['avg_pnl']:+.2f}$ на сделку")
        if 'reasons' in r:
            reasons_str = ', '.join(f"{k}:{v}" for k, v in r['reasons'].items())
            print(f"    Закрытия: {reasons_str}")
        if 'top_symbols' in r and r['top_symbols']:
            top_str = ', '.join(f"{s.replace('/USDT:USDT','')}({p:+.1f}$)" for s, p in r['top_symbols'])
            print(f"    Топ монеты: {top_str}")
        print()

    # Вывод рекомендации
    print("-" * 65)
    best_scan = max(
        (s for s in SCAN_SIZES if s in results and results[s]['trades'] > 0),
        key=lambda s: results[s]['total_pnl'],
        default=15
    )
    best = results.get(best_scan, {})
    current = results.get(15, {})
    if best_scan != 15 and current.get('trades', 0) > 0:
        improvement = best.get('total_pnl', 0) - current.get('total_pnl', 0)
        print(f"  ВЫВОД: оптимальный max_to_analyze = {best_scan}")
        print(f"  Потенциальный прирост vs текущего (15): {improvement:+.2f}$")
    else:
        print(f"  ВЫВОД: текущий размер (15) оптимален или близок к нему")
    print("=" * 65)


if __name__ == '__main__':
    run_backtest()
