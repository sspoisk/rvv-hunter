#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — Бэктест: max_positions × количество монет
Использует кэш данных из full_backtest.py
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from full_backtest import (
    _calculate_rsi, _calc_btc_trend_array, _get_btc_trend_at,
    _backtest_symbol, _apply_position_limit, run_multi_backtest,
    CACHE_DIR, RESULTS_PATH
)

# Оптимальные параметры из предыдущего бэктеста
BEST = {
    'sl_pct': 1.25,
    'tp_pct': 7.75,
    'trail_act': 0.5,
    'trail_dist': 0.05,
    'min_change_pct': 0.0,
    'btc_modes': {
        'bullish': 'short_only',
        'bearish': 'any',
        'neutral': 'any',
        'bullish_min_str': 0.3,
        'bearish_min_str': 0.3,
    },
    'position_size': 25,
    'leverage': 5,
}

MAX_POS_VALUES = [1, 2, 3, 5, 7, 10, 15, 20]
TOP_COINS_VALUES = [20, 30, 50, 75, 100]


def load_cached_data():
    """Загрузить данные из кэша full_backtest."""
    meta_path = os.path.join(CACHE_DIR, '_meta.json')
    if not os.path.exists(meta_path):
        print("ОШИБКА: кэш не найден! Сначала запустите full_backtest.py")
        sys.exit(1)

    with open(meta_path, 'r') as f:
        meta = json.load(f)

    symbols = meta.get('symbols', [])
    print(f"  Кэш: {len(symbols)} монет")

    candles_by_symbol = {}
    for sym in symbols:
        # Пробуем разные форматы имён файлов
        candidates = [
            os.path.join(CACHE_DIR, f"{sym}_USDT:USDT.json"),
            os.path.join(CACHE_DIR, f"{sym}.json"),
        ]
        for fpath in candidates:
            if os.path.exists(fpath):
                with open(fpath, 'r') as f:
                    candles = json.load(f)
                if len(candles) >= 100:
                    candles_by_symbol[sym] = candles
                break

    # BTC
    btc_path = os.path.join(CACHE_DIR, 'BTC_USDT_USDT.json')
    btc_candles = []
    if os.path.exists(btc_path):
        with open(btc_path, 'r') as f:
            btc_candles = json.load(f)

    print(f"  Загружено: {len(candles_by_symbol)} монет, BTC: {len(btc_candles)} свечей")
    return candles_by_symbol, btc_candles, symbols


def sort_symbols_by_volume(candles_by_symbol: Dict, symbols: List[str]) -> List[str]:
    """Сортировать символы по среднему объёму (убывание)."""
    vol_map = {}
    for sym, candles in candles_by_symbol.items():
        avg_vol = sum(c.get('volume', 0) for c in candles[-96:]) / max(1, min(96, len(candles)))
        vol_map[sym] = avg_vol
    return sorted(candles_by_symbol.keys(), key=lambda s: vol_map.get(s, 0), reverse=True)


def main():
    t_start = time.time()

    print("=" * 70)
    print("  RVV Hunter — Бэктест: max_positions × кол-во монет")
    print("=" * 70)
    print(f"  Параметры: SL={BEST['sl_pct']}% TP={BEST['tp_pct']}% "
          f"Trail={BEST['trail_act']}/{BEST['trail_dist']}%")
    print(f"  max_positions: {MAX_POS_VALUES}")
    print(f"  top_coins: {TOP_COINS_VALUES}")
    print()

    # Загрузка
    print("[1] Загрузка из кэша")
    candles_by_symbol, btc_candles, all_symbols = load_cached_data()

    btc_trends = {}
    if btc_candles and len(btc_candles) >= 100:
        btc_trends = _calc_btc_trend_array(btc_candles)

    # Сортируем по объёму
    sorted_symbols = sort_symbols_by_volume(candles_by_symbol, all_symbols)
    print(f"  Символы отсортированы по объёму")

    # Тест
    print(f"\n[2] Grid: {len(MAX_POS_VALUES)} × {len(TOP_COINS_VALUES)} = "
          f"{len(MAX_POS_VALUES) * len(TOP_COINS_VALUES)} комбинаций\n")

    results = []
    t0 = time.time()
    total = len(MAX_POS_VALUES) * len(TOP_COINS_VALUES)
    done = 0

    for top_n in TOP_COINS_VALUES:
        # Берём топ-N монет по объёму
        subset_symbols = sorted_symbols[:top_n]
        subset = {s: candles_by_symbol[s] for s in subset_symbols if s in candles_by_symbol}

        for max_pos in MAX_POS_VALUES:
            done += 1
            r = run_multi_backtest(
                subset, btc_trends, BEST['btc_modes'],
                sl_pct=BEST['sl_pct'], tp_pct=BEST['tp_pct'],
                trail_act=BEST['trail_act'], trail_dist=BEST['trail_dist'],
                min_change_pct=BEST['min_change_pct'],
                max_positions=max_pos,
                position_size=BEST['position_size'],
                leverage=BEST['leverage'],
            )
            results.append({
                'top_coins': top_n,
                'max_positions': max_pos,
                'actual_coins': len(subset),
                **r
            })

            if done % 10 == 0:
                elapsed = time.time() - t0
                print(f"  {done}/{total} ({elapsed:.0f}с)...")

    elapsed = time.time() - t0
    print(f"\n  Завершено за {elapsed:.0f}с")

    # Сортируем по PnL
    results.sort(key=lambda x: x['pnl'], reverse=True)

    # Таблица результатов
    print(f"\n{'=' * 80}")
    print("  РЕЗУЛЬТАТЫ: max_positions × количество монет")
    print(f"{'=' * 80}")

    # Матрица PnL
    print(f"\n  === PnL матрица ($) ===")
    header = f"  {'MaxPos':>8}"
    for tc in TOP_COINS_VALUES:
        header += f"  {'Top'+str(tc):>10}"
    print(header)
    print("  " + "-" * (10 + 12 * len(TOP_COINS_VALUES)))

    for mp in MAX_POS_VALUES:
        row = f"  {mp:>8}"
        for tc in TOP_COINS_VALUES:
            match = next((r for r in results if r['max_positions'] == mp and r['top_coins'] == tc), None)
            if match:
                row += f"  {match['pnl']:>+9.2f}$"
            else:
                row += f"  {'—':>10}"
        print(row)

    # Матрица Win Rate
    print(f"\n  === Win Rate матрица (%) ===")
    header = f"  {'MaxPos':>8}"
    for tc in TOP_COINS_VALUES:
        header += f"  {'Top'+str(tc):>10}"
    print(header)
    print("  " + "-" * (10 + 12 * len(TOP_COINS_VALUES)))

    for mp in MAX_POS_VALUES:
        row = f"  {mp:>8}"
        for tc in TOP_COINS_VALUES:
            match = next((r for r in results if r['max_positions'] == mp and r['top_coins'] == tc), None)
            if match and match['trades'] > 0:
                row += f"  {match['win_rate']:>9.1f}%"
            else:
                row += f"  {'—':>10}"
        print(row)

    # Матрица Trades
    print(f"\n  === Количество сделок ===")
    header = f"  {'MaxPos':>8}"
    for tc in TOP_COINS_VALUES:
        header += f"  {'Top'+str(tc):>10}"
    print(header)
    print("  " + "-" * (10 + 12 * len(TOP_COINS_VALUES)))

    for mp in MAX_POS_VALUES:
        row = f"  {mp:>8}"
        for tc in TOP_COINS_VALUES:
            match = next((r for r in results if r['max_positions'] == mp and r['top_coins'] == tc), None)
            if match:
                row += f"  {match['trades']:>10}"
            else:
                row += f"  {'—':>10}"
        print(row)

    # Топ-10
    print(f"\n  === Топ-10 комбинаций по PnL ===")
    print(f"  {'#':>3}  {'Coins':>5}  {'MaxPos':>6}  {'Trades':>6}  {'WR%':>6}  {'PnL':>10}  {'Skipped':>8}")
    print("  " + "-" * 55)
    for i, r in enumerate(results[:10]):
        print(f"  {i+1:>3}  {r['top_coins']:>5}  {r['max_positions']:>6}  "
              f"{r['trades']:>6}  {r['win_rate']:>5.1f}%  {r['pnl']:>+9.2f}$  "
              f"{r.get('skipped', 0):>8}")

    # Худшие 5
    print(f"\n  === Худшие 5 комбинаций ===")
    for i, r in enumerate(results[-5:]):
        print(f"  {len(results)-4+i:>3}  {r['top_coins']:>5}  {r['max_positions']:>6}  "
              f"{r['trades']:>6}  {r['win_rate']:>5.1f}%  {r['pnl']:>+9.2f}$  "
              f"{r.get('skipped', 0):>8}")

    # Рекомендация
    best = results[0]
    print(f"\n{'=' * 80}")
    print(f"  РЕКОМЕНДАЦИЯ: top_coins={best['top_coins']}, max_positions={best['max_positions']}")
    print(f"  PnL: {best['pnl']:+.2f}$ | {best['trades']} сделок | WR: {best['win_rate']:.1f}%")
    print(f"  Время: {time.time() - t_start:.0f}с")
    print(f"{'=' * 80}")

    # Сохраняем результаты
    out_path = os.path.join(BASE_DIR, 'data', 'backtest_positions_results.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'results': results,
            'best': {
                'top_coins': best['top_coins'],
                'max_positions': best['max_positions'],
                'pnl': best['pnl'],
                'trades': best['trades'],
                'win_rate': best['win_rate'],
            },
            'params': BEST,
            'meta': {
                'timestamp': datetime.utcnow().isoformat(),
                'max_pos_tested': MAX_POS_VALUES,
                'top_coins_tested': TOP_COINS_VALUES,
            }
        }, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Результаты: {out_path}")


if __name__ == '__main__':
    main()
