"""
RVV Hunter v5.0 - Agent Tools (Инструменты агента)
Все "руки и ноги" агента для взаимодействия с ботом
"""

import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import statistics

logger = logging.getLogger(__name__)


class AgentTools:
    """
    Инструменты агента для:
    - Загрузки данных (свечи, orderbook, история)
    - Анализа (паттерны, статистика)
    - Тестирования стратегий (бэктест)
    - Действий (открытие/закрытие позиций, настройки)
    - Генерации стратегий
    """
    
    def __init__(self, trader=None, exchange=None, db=None, brain=None):
        self.trader = trader
        self.exchange = exchange
        self.db = db
        self.brain = brain
        self.last_backtest_results = None  # Последние результаты бэктеста
        logger.info("[TOOLS] Agent tools initialized")
    
    def set_components(self, trader=None, exchange=None, db=None, brain=None):
        """Установить компоненты (для отложенной инициализации)"""
        logger.info(f"[TOOLS] set_components: trader={trader}, exchange={exchange is not None}")
        if trader:
            self.trader = trader
            logger.info(f"[TOOLS] Trader SET: {self.trader}")
        if exchange:
            self.exchange = exchange
        if db:
            self.db = db
        if brain:
            self.brain = brain
    
    # =========================================================================
    # DATA TOOLS - Загрузка данных
    # =========================================================================
    
    def load_candles(self, symbol: str, timeframe: str = '15m', 
                    limit: int = 100) -> Dict:
        """
        Загрузить свечи для символа
        
        Returns:
            {
                'symbol': str,
                'timeframe': str,
                'candles': List[Dict],  # {time, open, high, low, close, volume}
                'current_price': float,
                'change_pct': float,
                'summary': str
            }
        """
        try:
            if not self.exchange:
                return {'error': 'Exchange not available'}
            
            # Нормализуем символ
            full_symbol = self._normalize_symbol(symbol)
            
            # Загружаем OHLCV
            ohlcv = self.exchange.fetch_ohlcv(full_symbol, timeframe, limit=limit)
            
            if not ohlcv:
                return {'error': f'No data for {symbol}'}
            
            candles = []
            for c in ohlcv:
                candles.append({
                    'time': c[0],
                    'datetime': datetime.fromtimestamp(c[0]/1000).strftime('%Y-%m-%d %H:%M'),
                    'open': c[1],
                    'high': c[2],
                    'low': c[3],
                    'close': c[4],
                    'volume': c[5]
                })
            
            # Статистика
            closes = [c['close'] for c in candles]
            current_price = closes[-1]
            first_price = closes[0]
            change_pct = ((current_price - first_price) / first_price) * 100
            
            high_price = max(c['high'] for c in candles)
            low_price = min(c['low'] for c in candles)
            avg_volume = statistics.mean(c['volume'] for c in candles)
            
            # RSI
            rsi = self._calculate_rsi(closes)
            
            summary = (
                f"📊 {symbol} ({timeframe}, {len(candles)} свечей)\n"
                f"Цена: ${current_price:.6f} ({change_pct:+.2f}%)\n"
                f"Диапазон: ${low_price:.6f} - ${high_price:.6f}\n"
                f"RSI: {rsi:.1f}\n"
                f"Объём (средний): {avg_volume:,.0f}"
            )
            
            return {
                'symbol': symbol,
                'timeframe': timeframe,
                'candles': candles,
                'current_price': current_price,
                'change_pct': change_pct,
                'high': high_price,
                'low': low_price,
                'rsi': rsi,
                'avg_volume': avg_volume,
                'summary': summary
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] load_candles error: {e}")
            return {'error': str(e)}
    
    def get_orderbook(self, symbol: str, depth: int = 10) -> Dict:
        """Получить стакан ордеров"""
        try:
            if not self.exchange:
                return {'error': 'Exchange not available'}
            
            full_symbol = self._normalize_symbol(symbol)
            orderbook = self.exchange.fetch_order_book(full_symbol, limit=depth)
            
            bids = orderbook.get('bids', [])[:depth]
            asks = orderbook.get('asks', [])[:depth]
            
            bid_volume = sum(b[1] for b in bids)
            ask_volume = sum(a[1] for a in asks)
            
            spread = asks[0][0] - bids[0][0] if bids and asks else 0
            spread_pct = (spread / bids[0][0] * 100) if bids else 0
            
            imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume) * 100 if (bid_volume + ask_volume) > 0 else 0
            
            summary = (
                f"📈 Стакан {symbol}\n"
                f"Лучший bid: ${bids[0][0]:.6f}\n"
                f"Лучший ask: ${asks[0][0]:.6f}\n"
                f"Спред: {spread_pct:.3f}%\n"
                f"Дисбаланс: {imbalance:+.1f}% ({'покупатели' if imbalance > 0 else 'продавцы'})"
            )
            
            return {
                'symbol': symbol,
                'bids': bids,
                'asks': asks,
                'spread': spread,
                'spread_pct': spread_pct,
                'bid_volume': bid_volume,
                'ask_volume': ask_volume,
                'imbalance': imbalance,
                'summary': summary
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] get_orderbook error: {e}")
            return {'error': str(e)}
    
    def get_btc_trend(self) -> Dict:
        """Получить текущий тренд Bitcoin"""
        try:
            # Пробуем получить из app.state
            try:
                from app import state
                if state.btc_trend_cache:
                    trend = state.btc_trend_cache
                    summary = (
                        f"₿ Bitcoin Trend\n"
                        f"Направление: {trend.get('trend', 'neutral').upper()}\n"
                        f"Сила: {trend.get('strength', 'weak')}\n"
                        f"RSI: {trend.get('rsi_1h', 50):.1f}\n"
                        f"Изменение 24ч: {trend.get('change_24h', 0):+.2f}%"
                    )
                    trend['summary'] = summary
                    return trend
            except Exception:
                pass
            
            # Fallback: загружаем сами
            candles = self.load_candles('BTC/USDT', '1h', 24)
            if 'error' in candles:
                return candles
            
            return {
                'trend': 'neutral',
                'price': candles['current_price'],
                'change_24h': candles['change_pct'],
                'rsi': candles['rsi'],
                'summary': f"BTC: ${candles['current_price']:.2f} ({candles['change_pct']:+.2f}%)"
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] get_btc_trend error: {e}")
            return {'error': str(e)}
    
    def get_market_overview(self) -> Dict:
        """Обзор рынка: топ растущие/падающие"""
        try:
            if not self.exchange:
                return {'error': 'Exchange not available'}
            
            tickers = self.exchange.fetch_tickers()
            
            usdt_pairs = []
            for symbol, ticker in tickers.items():
                if '/USDT' in symbol and ticker.get('percentage') is not None:
                    usdt_pairs.append({
                        'symbol': symbol.replace('/USDT:USDT', '').replace('/USDT', ''),
                        'price': ticker.get('last', 0),
                        'change': ticker.get('percentage', 0),
                        'volume': ticker.get('quoteVolume', 0)
                    })
            
            # Сортируем
            top_gainers = sorted(usdt_pairs, key=lambda x: x['change'], reverse=True)[:10]
            top_losers = sorted(usdt_pairs, key=lambda x: x['change'])[:10]
            
            summary_parts = ["📊 Обзор рынка\n\n🚀 Топ растущие:"]
            for coin in top_gainers[:5]:
                summary_parts.append(f"  {coin['symbol']}: +{coin['change']:.1f}%")
            
            summary_parts.append("\n📉 Топ падающие:")
            for coin in top_losers[:5]:
                summary_parts.append(f"  {coin['symbol']}: {coin['change']:.1f}%")
            
            return {
                'top_gainers': top_gainers,
                'top_losers': top_losers,
                'total_pairs': len(usdt_pairs),
                'summary': '\n'.join(summary_parts)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] get_market_overview error: {e}")
            return {'error': str(e)}
    
    # =========================================================================
    # ANALYSIS TOOLS - Анализ
    # =========================================================================
    
    def analyze_symbol(self, symbol: str) -> Dict:
        """Полный анализ монеты"""
        try:
            result = {
                'symbol': symbol,
                'timestamp': datetime.now().isoformat()
            }
            
            # Загружаем свечи разных таймфреймов
            candles_15m = self.load_candles(symbol, '15m', 50)
            candles_1h = self.load_candles(symbol, '1h', 24)
            
            if 'error' not in candles_15m:
                result['price'] = candles_15m['current_price']
                result['change_15m'] = candles_15m['change_pct']
                result['rsi_15m'] = candles_15m['rsi']
            
            if 'error' not in candles_1h:
                result['change_1h'] = candles_1h['change_pct']
                result['rsi_1h'] = candles_1h['rsi']
            
            # Стакан
            orderbook = self.get_orderbook(symbol, 5)
            if 'error' not in orderbook:
                result['spread_pct'] = orderbook['spread_pct']
                result['order_imbalance'] = orderbook['imbalance']
            
            # История сделок по монете (если есть)
            if self.db:
                trades = self.db.get_trades_by_symbol(symbol, limit=20) if hasattr(self.db, 'get_trades_by_symbol') else []
                if trades:
                    wins = sum(1 for t in trades if t.get('pnl_usdt', 0) > 0)
                    result['historical_trades'] = len(trades)
                    result['historical_winrate'] = (wins / len(trades)) * 100 if trades else 0
            
            # Формируем саммари
            summary_parts = [f"📊 Анализ {symbol}\n"]
            
            if 'price' in result:
                summary_parts.append(f"Цена: ${result['price']:.6f}")
            if 'rsi_15m' in result:
                rsi = result['rsi_15m']
                rsi_status = "перекуплен" if rsi > 70 else "перепродан" if rsi < 30 else "нейтрально"
                summary_parts.append(f"RSI(15m): {rsi:.1f} ({rsi_status})")
            if 'spread_pct' in result:
                spread_status = "высокий!" if result['spread_pct'] > 0.3 else "норма"
                summary_parts.append(f"Спред: {result['spread_pct']:.3f}% ({spread_status})")
            if 'order_imbalance' in result:
                imb = result['order_imbalance']
                imb_status = "покупатели" if imb > 10 else "продавцы" if imb < -10 else "баланс"
                summary_parts.append(f"Дисбаланс: {imb:+.1f}% ({imb_status})")
            if 'historical_winrate' in result:
                summary_parts.append(f"История: {result['historical_trades']} сделок, WR: {result['historical_winrate']:.0f}%")
            
            # Рекомендация
            recommendation = self._generate_recommendation(result)
            summary_parts.append(f"\n💡 {recommendation}")
            
            result['summary'] = '\n'.join(summary_parts)
            result['recommendation'] = recommendation
            
            return result
            
        except Exception as e:
            logger.error(f"[TOOLS] analyze_symbol error: {e}")
            return {'error': str(e)}
    
    def find_patterns(self, trades_type: str = 'losing', limit: int = 50) -> Dict:
        """Найти паттерны в сделках"""
        try:
            if not self.db:
                return {'error': 'Database not available'}
            
            # Получаем сделки
            trades = self.db.get_trades(limit=limit, only_closed=True)
            
            if not trades:
                return {'patterns': [], 'summary': 'Нет сделок для анализа'}
            
            # Фильтруем по типу
            if trades_type == 'losing':
                filtered = [t for t in trades if t.get('pnl_usdt', 0) < 0]
            elif trades_type == 'winning':
                filtered = [t for t in trades if t.get('pnl_usdt', 0) > 0]
            else:
                filtered = trades
            
            if not filtered:
                return {'patterns': [], 'summary': f'Нет {trades_type} сделок'}
            
            patterns = []
            
            # Анализ RSI при входе
            rsi_values = [t.get('rsi_at_entry', 50) for t in filtered if t.get('rsi_at_entry')]
            if rsi_values:
                avg_rsi = statistics.mean(rsi_values)
                patterns.append({
                    'type': 'rsi_entry',
                    'description': f"Средний RSI при входе: {avg_rsi:.1f}",
                    'value': avg_rsi,
                    'recommendation': "Входить при RSI > 75 для SHORT" if avg_rsi < 70 else "RSI в норме"
                })
            
            # Анализ времени удержания
            hold_times = []
            for t in filtered:
                if t.get('opened_at') and t.get('closed_at'):
                    try:
                        opened = datetime.fromisoformat(t['opened_at'].replace('Z', ''))
                        closed = datetime.fromisoformat(t['closed_at'].replace('Z', ''))
                        hold_times.append((closed - opened).total_seconds() / 60)
                    except Exception:
                        pass
            
            if hold_times:
                avg_hold = statistics.mean(hold_times)
                patterns.append({
                    'type': 'hold_time',
                    'description': f"Среднее время удержания: {avg_hold:.0f} мин",
                    'value': avg_hold,
                    'recommendation': "Закрывать раньше если в минусе" if avg_hold > 60 else "Время удержания в норме"
                })
            
            # Анализ по символам
            symbol_stats = {}
            for t in filtered:
                sym = t.get('symbol', 'Unknown')
                if sym not in symbol_stats:
                    symbol_stats[sym] = {'count': 0, 'total_pnl': 0}
                symbol_stats[sym]['count'] += 1
                symbol_stats[sym]['total_pnl'] += t.get('pnl_usdt', 0)
            
            worst_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['total_pnl'])[:5]
            if worst_symbols and trades_type == 'losing':
                patterns.append({
                    'type': 'worst_symbols',
                    'description': f"Худшие монеты: {', '.join([s[0].replace('/USDT:USDT', '') for s in worst_symbols[:3]])}",
                    'value': worst_symbols,
                    'recommendation': "Рассмотреть добавление в черный список"
                })
            
            # Анализ BTC тренда
            btc_losing = [t for t in filtered if t.get('btc_trend_at_open', {}).get('trend') == 'bearish' and t.get('side') == 'LONG']
            if btc_losing:
                pct = (len(btc_losing) / len(filtered)) * 100
                patterns.append({
                    'type': 'btc_correlation',
                    'description': f"{pct:.0f}% убытков при LONG в медвежьем BTC",
                    'value': pct,
                    'recommendation': "Не открывать LONG при падающем BTC"
                })
            
            # Формируем саммари
            summary_parts = [f"🔍 Паттерны в {len(filtered)} {trades_type} сделках:\n"]
            for i, p in enumerate(patterns, 1):
                summary_parts.append(f"{i}. {p['description']}")
                summary_parts.append(f"   → {p['recommendation']}")
            
            return {
                'patterns': patterns,
                'total_analyzed': len(filtered),
                'summary': '\n'.join(summary_parts)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] find_patterns error: {e}")
            return {'error': str(e)}
    
    def get_trading_statistics(self, period_days: int = 30) -> Dict:
        """Получить статистику торговли"""
        try:
            if not self.db:
                return {'error': 'Database not available'}
            
            trades = self.db.get_trades(limit=500, only_closed=True)
            
            # Фильтруем по периоду
            cutoff = datetime.now() - timedelta(days=period_days)
            recent_trades = []
            for t in trades:
                try:
                    closed_at = datetime.fromisoformat(t.get('closed_at', '').replace('Z', ''))
                    if closed_at > cutoff:
                        recent_trades.append(t)
                except Exception:
                    pass
            
            if not recent_trades:
                return {'summary': f'Нет сделок за последние {period_days} дней'}
            
            total = len(recent_trades)
            wins = sum(1 for t in recent_trades if t.get('pnl_usdt', 0) > 0)
            losses = total - wins
            
            total_pnl = sum(t.get('pnl_usdt', 0) for t in recent_trades)
            win_pnl = sum(t.get('pnl_usdt', 0) for t in recent_trades if t.get('pnl_usdt', 0) > 0)
            loss_pnl = abs(sum(t.get('pnl_usdt', 0) for t in recent_trades if t.get('pnl_usdt', 0) < 0))
            
            win_rate = (wins / total) * 100 if total > 0 else 0
            profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else float('inf')
            avg_win = win_pnl / wins if wins > 0 else 0
            avg_loss = loss_pnl / losses if losses > 0 else 0
            
            # По сторонам
            shorts = [t for t in recent_trades if t.get('side') == 'SHORT']
            longs = [t for t in recent_trades if t.get('side') == 'LONG']
            
            short_wr = (sum(1 for t in shorts if t.get('pnl_usdt', 0) > 0) / len(shorts) * 100) if shorts else 0
            long_wr = (sum(1 for t in longs if t.get('pnl_usdt', 0) > 0) / len(longs) * 100) if longs else 0
            
            summary = (
                f"📊 Статистика за {period_days} дней\n\n"
                f"Всего сделок: {total}\n"
                f"Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
                f"Общий PnL: ${total_pnl:+.2f}\n"
                f"Profit Factor: {profit_factor:.2f}\n"
                f"Средний выигрыш: ${avg_win:.2f}\n"
                f"Средний проигрыш: ${avg_loss:.2f}\n\n"
                f"SHORT: {len(shorts)} сделок, WR: {short_wr:.0f}%\n"
                f"LONG: {len(longs)} сделок, WR: {long_wr:.0f}%"
            )
            
            return {
                'period_days': period_days,
                'total_trades': total,
                'wins': wins,
                'losses': losses,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'profit_factor': profit_factor,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'short_count': len(shorts),
                'short_wr': short_wr,
                'long_count': len(longs),
                'long_wr': long_wr,
                'summary': summary
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] get_trading_statistics error: {e}")
            return {'error': str(e)}
    
    # =========================================================================
    # BACKTEST TOOLS - Тестирование стратегий
    # =========================================================================
    
    def backtest_strategy(self, strategy: Dict, symbol: str = None,
                         period_days: int = 30) -> Dict:
        """
        Бэктест стратегии на исторических данных
        
        strategy: {
            'name': str,
            'entry_rsi_min': float,  # RSI для входа (SHORT: выше этого)
            'entry_rsi_max': float,  # RSI для входа (LONG: ниже этого)
            'side': 'SHORT' | 'LONG' | 'BOTH',
            'stop_loss_pct': float,
            'take_profit_pct': float,
            'min_change_24h': float  # Мин. изменение за 24ч
        }
        """
        try:
            results = {
                'strategy': strategy,
                'period_days': period_days,
                'trades': [],
                'wins': 0,
                'losses': 0,
                'total_pnl': 0
            }
            
            # Получаем исторические данные
            target_symbol = symbol or 'BTC/USDT'
            candles = self.load_candles(target_symbol, '1h', min(period_days * 24, 500))
            
            if 'error' in candles or not candles.get('candles'):
                return {'error': 'Cannot load historical data'}
            
            candle_list = candles['candles']
            
            # Параметры стратегии
            entry_rsi_min = strategy.get('entry_rsi_min', 70)  # Для SHORT
            entry_rsi_max = strategy.get('entry_rsi_max', 30)  # Для LONG
            side = strategy.get('side', 'SHORT')
            sl_pct = strategy.get('stop_loss_pct', 5.0)
            tp_pct = strategy.get('take_profit_pct', 7.0)
            
            # Симуляция
            position = None
            closes = []
            
            for i, candle in enumerate(candle_list):
                closes.append(candle['close'])
                
                if len(closes) < 15:
                    continue
                
                rsi = self._calculate_rsi(closes[-15:])
                price = candle['close']
                
                # Проверяем открытую позицию
                if position:
                    entry = position['entry_price']
                    
                    if position['side'] == 'SHORT':
                        pnl_pct = ((entry - price) / entry) * 100
                    else:
                        pnl_pct = ((price - entry) / entry) * 100
                    
                    # Проверяем SL/TP
                    if pnl_pct <= -sl_pct:
                        # Stop Loss
                        results['trades'].append({
                            'side': position['side'],
                            'entry': entry,
                            'exit': price,
                            'pnl_pct': -sl_pct,
                            'result': 'SL'
                        })
                        results['losses'] += 1
                        results['total_pnl'] -= sl_pct
                        position = None
                    elif pnl_pct >= tp_pct:
                        # Take Profit
                        results['trades'].append({
                            'side': position['side'],
                            'entry': entry,
                            'exit': price,
                            'pnl_pct': tp_pct,
                            'result': 'TP'
                        })
                        results['wins'] += 1
                        results['total_pnl'] += tp_pct
                        position = None
                
                # Проверяем условия входа
                elif position is None:
                    if side in ['SHORT', 'BOTH'] and rsi > entry_rsi_min:
                        position = {'side': 'SHORT', 'entry_price': price, 'entry_rsi': rsi}
                    elif side in ['LONG', 'BOTH'] and rsi < entry_rsi_max:
                        position = {'side': 'LONG', 'entry_price': price, 'entry_rsi': rsi}
            
            # Статистика
            total = results['wins'] + results['losses']
            win_rate = (results['wins'] / total * 100) if total > 0 else 0
            profit_factor = (results['wins'] * tp_pct) / (results['losses'] * sl_pct) if results['losses'] > 0 else float('inf')
            
            results['total_trades'] = total
            results['win_rate'] = win_rate
            results['profit_factor'] = profit_factor
            
            # Оценка стратегии
            if win_rate >= 55 and profit_factor >= 1.3:
                rating = "✅ РЕКОМЕНДУЕТСЯ"
            elif win_rate >= 45:
                rating = "⚠️ СРЕДНЯЯ"
            else:
                rating = "❌ НЕ РЕКОМЕНДУЕТСЯ"
            
            results['rating'] = rating
            results['summary'] = (
                f"🧪 Бэктест: {strategy.get('name', 'Custom')}\n\n"
                f"Период: {period_days} дней\n"
                f"Сделок: {total}\n"
                f"Win Rate: {win_rate:.1f}%\n"
                f"Profit Factor: {profit_factor:.2f}\n"
                f"Общий PnL: {results['total_pnl']:+.1f}%\n\n"
                f"Оценка: {rating}"
            )
            
            return results
            
        except Exception as e:
            logger.error(f"[TOOLS] backtest_strategy error: {e}")
            return {'error': str(e)}
    
    def generate_strategy_suggestions(self) -> List[Dict]:
        """Генерация предложений по стратегиям на основе данных"""
        suggestions = []
        
        try:
            # Анализируем паттерны
            patterns = self.find_patterns('winning', 50)
            stats = self.get_trading_statistics(30)
            
            # Стратегия 1: Консервативный SHORT
            suggestions.append({
                'name': 'Conservative SHORT',
                'description': 'Консервативная стратегия SHORT с высоким RSI',
                'parameters': {
                    'side': 'SHORT',
                    'entry_rsi_min': 75,
                    'stop_loss_pct': 2.0,
                    'take_profit_pct': 3.0,
                    'min_change_24h': 8.0
                },
                'rationale': 'Высокий порог RSI для более надёжных входов'
            })
            
            # Стратегия 2: Агрессивный SHORT
            suggestions.append({
                'name': 'Aggressive SHORT',
                'description': 'Агрессивная стратегия SHORT с большим TP',
                'parameters': {
                    'side': 'SHORT',
                    'entry_rsi_min': 68,
                    'stop_loss_pct': 1.5,
                    'take_profit_pct': 4.0,
                    'min_change_24h': 5.0
                },
                'rationale': 'Больше сделок, но строгий SL'
            })
            
            # Стратегия 3: LONG на перепроданности
            suggestions.append({
                'name': 'Oversold LONG',
                'description': 'LONG на перепроданных монетах',
                'parameters': {
                    'side': 'LONG',
                    'entry_rsi_max': 30,
                    'stop_loss_pct': 2.5,
                    'take_profit_pct': 4.0,
                    'min_change_24h': -8.0,
                    'btc_trend_required': 'bullish'
                },
                'rationale': 'Отскок перепроданных только при бычьем BTC'
            })
            
            # Стратегия 4: Скальпинг
            suggestions.append({
                'name': 'Quick Scalp',
                'description': 'Быстрые сделки с малым профитом',
                'parameters': {
                    'side': 'BOTH',
                    'entry_rsi_min': 72,
                    'entry_rsi_max': 28,
                    'stop_loss_pct': 1.0,
                    'take_profit_pct': 1.5,
                    'max_hold_minutes': 30
                },
                'rationale': 'Много мелких сделок с быстрым выходом'
            })
            
            # Адаптивная стратегия на основе статистики
            if stats and 'short_wr' in stats:
                if stats['short_wr'] > stats.get('long_wr', 0):
                    suggestions.append({
                        'name': 'Adaptive SHORT Only',
                        'description': f'Только SHORT (исторический WR: {stats["short_wr"]:.0f}%)',
                        'parameters': {
                            'side': 'SHORT',
                            'entry_rsi_min': 70,
                            'stop_loss_pct': 2.0,
                            'take_profit_pct': 3.0
                        },
                        'rationale': f'SHORT показывает лучший результат ({stats["short_wr"]:.0f}% vs {stats.get("long_wr", 0):.0f}%)'
                    })
            
            return suggestions
            
        except Exception as e:
            logger.error(f"[TOOLS] generate_strategy_suggestions error: {e}")
            return suggestions
    
    # =========================================================================
    # ACTION TOOLS - Действия
    # =========================================================================
    
    def get_open_positions(self) -> List[Dict]:
        """Получить открытые позиции"""
        try:
            if not self.trader:
                return []
            
            positions = self.trader.get_open_positions()
            return positions if positions else []
        except Exception as e:
            logger.error(f"[TOOLS] get_open_positions error: {e}")
            return []
    
    def close_position(self, trade_id: str, reason: str = 'AGENT') -> Dict:
        """Закрыть позицию"""
        try:
            logger.info(f"[TOOLS] close_position called: trade_id={trade_id}, reason={reason}")
            logger.info(f"[TOOLS] self.trader = {self.trader}")
            
            if not self.trader:
                logger.error("[TOOLS] Trader not available!")
                return {'error': 'Trader not available'}
            
            logger.info(f"[TOOLS] Calling trader.close_position_manual({trade_id}, {reason})")
            result = self.trader.close_position_manual(trade_id, reason)
            logger.info(f"[TOOLS] close_position_manual returned: {result}")
            
            if result:
                return {
                    'success': True,
                    'trade_id': trade_id,
                    'pnl': result.get('pnl', 0),
                    'summary': f"✅ Позиция {trade_id} закрыта: ${result.get('pnl', 0):+.2f}"
                }
            else:
                logger.error(f"[TOOLS] Position {trade_id} not found or already closed")
                return {'error': f'Position {trade_id} not found'}
        except Exception as e:
            logger.error(f"[TOOLS] close_position error: {e}")
            return {'error': str(e)}
    
    def close_all_positions(self, filter_type: str = None, filter_value: float = None) -> Dict:
        """
        Закрыть все позиции по фильтру
        filter_type: 'profit' (в плюсе > value), 'loss' (в минусе < value), 'all'
        """
        try:
            positions = self.get_open_positions()
            if not positions:
                return {'closed': 0, 'summary': 'Нет открытых позиций'}
            
            closed = []
            total_pnl = 0
            
            for pos in positions:
                pnl_pct = pos.get('pnl_percent', 0)
                should_close = False
                
                if filter_type == 'all':
                    should_close = True
                elif filter_type == 'profit' and pnl_pct > (filter_value or 0):
                    should_close = True
                elif filter_type == 'loss' and pnl_pct < (filter_value or 0):
                    should_close = True
                
                if should_close:
                    trade_id = pos.get('id') or pos.get('trade_id')
                    result = self.close_position(trade_id, f'AGENT_{filter_type.upper()}')
                    if result.get('success'):
                        closed.append(trade_id)
                        total_pnl += result.get('pnl', 0)
            
            return {
                'closed': len(closed),
                'closed_ids': closed,
                'total_pnl': total_pnl,
                'summary': f"✅ Закрыто {len(closed)} позиций, общий PnL: ${total_pnl:+.2f}"
            }
        except Exception as e:
            logger.error(f"[TOOLS] close_all_positions error: {e}")
            return {'error': str(e)}
    
    def add_to_blacklist(self, symbol: str, reason: str = 'Agent command') -> Dict:
        """Добавить монету в черный список"""
        try:
            if not self.db:
                return {'error': 'Database not available'}
            
            # Нормализуем символ
            clean_symbol = symbol.upper().replace('/USDT', '').replace(':USDT', '').replace('1000', '')
            
            # Проверяем маппинг для Binance
            if clean_symbol in self.SYMBOL_MAP:
                clean_symbol = self.SYMBOL_MAP[clean_symbol]
            
            full_symbol = f"{clean_symbol}/USDT:USDT"
            
            success = self.db.add_to_blacklist(full_symbol, reason)
            
            if success:
                # Сохраняем в память агента
                if self.brain:
                    self.brain.save_command(
                        f"Черный список: {clean_symbol}",
                        command_type='blacklist',
                        target=clean_symbol,
                        parameters={'reason': reason}
                    )
                
                return {
                    'success': True,
                    'symbol': clean_symbol,
                    'summary': f"⛔ {clean_symbol} добавлен в черный список: {reason}"
                }
            else:
                return {'error': 'Already in blacklist or failed', 'symbol': clean_symbol}
        except Exception as e:
            logger.error(f"[TOOLS] add_to_blacklist error: {e}")
            return {'error': str(e)}
    
    def blacklist_worst_from_backtest(self, backtest_results: Dict = None, max_coins: int = 5) -> Dict:
        """
        Добавить убыточные монеты из результатов бэктеста в blacklist
        
        Args:
            backtest_results: Результаты run_backtest_multi (если None - используем последний)
            max_coins: Максимум монет для добавления
        """
        try:
            # Используем последний бэктест если не передан
            if backtest_results is None:
                if self.last_backtest_results is None:
                    return {'error': 'Нет результатов бэктеста. Сначала запустите бэктест.'}
                backtest_results = self.last_backtest_results
            
            worst = backtest_results.get('worst_symbols', [])
            if not worst:
                return {'error': 'No worst symbols in backtest results'}
            
            added = []
            failed = []
            
            for sym, data in worst[:max_coins]:
                if data.get('pnl', 0) < 0:  # Только убыточные
                    result = self.add_to_blacklist(
                        sym, 
                        f"Backtest: PnL=${data['pnl']:.2f}, WR:{data.get('wins',0)}/{data.get('trades',0)}"
                    )
                    if result.get('success'):
                        added.append(f"{sym} (${data['pnl']:.0f})")
                    else:
                        failed.append(sym)
            
            lines = [f"🚫 **Blacklist из бэктеста:**\n"]
            if added:
                lines.append(f"✅ Добавлено: {', '.join(added)}")
            if failed:
                lines.append(f"⚠️ Уже в списке: {', '.join(failed)}")
            
            if not added and not failed:
                lines.append("ℹ️ Нет убыточных монет для добавления")
            
            return {
                'success': True,
                'added': added,
                'failed': failed,
                'summary': '\n'.join(lines)
            }
        except Exception as e:
            logger.error(f"[TOOLS] blacklist_worst_from_backtest error: {e}")
            return {'error': str(e)}
    
    def whitelist_profitable_from_backtest(self, backtest_results: Dict = None, min_pnl: float = 0) -> Dict:
        """
        Получить список прибыльных монет из бэктеста и установить whitelist
        
        Args:
            backtest_results: Результаты run_backtest_multi (если None - используем последний)
            min_pnl: Минимальный PnL для включения в whitelist
        """
        try:
            # Используем последний бэктест если не передан
            if backtest_results is None:
                if self.last_backtest_results is None:
                    return {'error': 'Нет результатов бэктеста. Сначала запустите бэктест.'}
                backtest_results = self.last_backtest_results
            
            # Получаем все символы из бэктеста
            all_symbols = backtest_results.get('all_symbols', [])
            if not all_symbols:
                # Пробуем собрать из best + worst
                best = backtest_results.get('best_symbols', [])
                worst = backtest_results.get('worst_symbols', [])
                all_symbols = best + worst
            
            if not all_symbols:
                return {'error': 'Нет данных по монетам в бэктесте'}
            
            # Фильтруем прибыльные
            profitable = []
            for item in all_symbols:
                if isinstance(item, tuple):
                    sym, data = item
                    pnl = data.get('pnl', 0)
                else:
                    sym = item.get('symbol', '')
                    pnl = item.get('pnl', 0)
                
                if pnl >= min_pnl:
                    profitable.append({
                        'symbol': sym,
                        'pnl': pnl,
                        'trades': data.get('trades', 0) if isinstance(item, tuple) else item.get('trades', 0),
                        'wins': data.get('wins', 0) if isinstance(item, tuple) else item.get('wins', 0)
                    })
            
            # Сортируем по PnL
            profitable.sort(key=lambda x: x['pnl'], reverse=True)
            
            # Сохраняем whitelist в настройки
            whitelist_symbols = [p['symbol'] for p in profitable]
            if self.trader:
                import json
                self.trader.update_settings({
                    'whitelist_symbols': json.dumps(whitelist_symbols),  # JSON строка
                    'whitelist_enabled': True  # Включаем whitelist!
                })
            
            # Формируем ответ
            lines = [f"✅ **Whitelist прибыльных монет ({len(profitable)} шт):**\n"]
            for i, p in enumerate(profitable[:20], 1):
                emoji = "🟢" if p['pnl'] > 100 else "🟡" if p['pnl'] > 0 else "âšª"
                lines.append(f"{i}. {emoji} {p['symbol']}: ${p['pnl']:.0f} (WR:{p['wins']}/{p['trades']})")
            
            if len(profitable) > 20:
                lines.append(f"... и ещё {len(profitable) - 20} монет")
            
            lines.append(f"\n🎯 Теперь бот торгует только этими {len(profitable)} монетами")
            
            return {
                'success': True,
                'profitable_count': len(profitable),
                'profitable_coins': profitable,
                'whitelist': whitelist_symbols,
                'summary': '\n'.join(lines)
            }
        except Exception as e:
            logger.error(f"[TOOLS] whitelist_profitable_from_backtest error: {e}")
            return {'error': str(e)}
    
    def change_settings(self, settings: Dict) -> Dict:
        """Изменить настройки бота"""
        try:
            if not self.trader:
                return {'error': 'Trader not available'}
            
            # Применяем только разрешённые настройки
            allowed = ['confidence_threshold', 'min_change_filter', 'max_positions',
                      'trailing_enabled', 'trailing_distance_pct', 'spread_check_enabled',
                      'max_spread_pct', 'btc_trend_filter_enabled']
            
            filtered = {k: v for k, v in settings.items() if k in allowed}
            
            if not filtered:
                return {'error': 'No valid settings provided'}
            
            success = self.trader.update_settings(filtered)
            
            if success:
                changes = ', '.join([f"{k}={v}" for k, v in filtered.items()])
                return {
                    'success': True,
                    'changed': filtered,
                    'summary': f"⚙️ Настройки изменены: {changes}"
                }
            else:
                return {'error': 'Failed to update settings'}
        except Exception as e:
            logger.error(f"[TOOLS] change_settings error: {e}")
            return {'error': str(e)}
    
    def pause_scanner(self, paused: bool = True) -> Dict:
        """Поставить сканер на паузу"""
        try:
            if not self.trader:
                return {'error': 'Trader not available'}
            
            self.trader.pause_scanner(paused)
            status = "на паузе" if paused else "активен"
            return {
                'success': True,
                'paused': paused,
                'summary': f"🔄 Сканер {status}"
            }
        except Exception as e:
            logger.error(f"[TOOLS] pause_scanner error: {e}")
            return {'error': str(e)}
    
    # =========================================================================
    # MEMORY TOOLS - Работа с памятью
    # =========================================================================
    
    def remember_command(self, command: str, command_type: str = 'rule',
                        target: str = None) -> Dict:
        """Запомнить команду пользователя"""
        try:
            if not self.brain:
                return {'error': 'Brain not available'}
            
            cmd_id = self.brain.save_command(command, command_type, target)
            
            return {
                'success': True,
                'command_id': cmd_id,
                'summary': f"✅ Запомнил: {command}"
            }
        except Exception as e:
            logger.error(f"[TOOLS] remember_command error: {e}")
            return {'error': str(e)}
    
    def recall_commands(self, query: str = None) -> Dict:
        """Вспомнить команды"""
        try:
            if not self.brain:
                return {'error': 'Brain not available'}
            
            commands = self.brain.get_active_commands()
            
            if query:
                commands = [c for c in commands if query.lower() in c['command'].lower()]
            
            if not commands:
                return {'commands': [], 'summary': 'Нет сохранённых команд'}
            
            summary_parts = ["🔍 Твои команды:"]
            for cmd in commands[:10]:
                summary_parts.append(f"• {cmd['command']}")
            
            return {
                'commands': commands,
                'count': len(commands),
                'summary': '\n'.join(summary_parts)
            }
        except Exception as e:
            logger.error(f"[TOOLS] recall_commands error: {e}")
            return {'error': str(e)}
    
    def save_lesson(self, description: str, lesson: str, symbol: str = None) -> Dict:
        """Сохранить урок"""
        try:
            if not self.brain:
                return {'error': 'Brain not available'}
            
            lesson_id = self.brain.save_lesson(description, lesson, symbol=symbol)
            
            return {
                'success': True,
                'lesson_id': lesson_id,
                'summary': f"📚 Урок сохранён: {lesson}"
            }
        except Exception as e:
            logger.error(f"[TOOLS] save_lesson error: {e}")
            return {'error': str(e)}
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _normalize_symbol(self, symbol: str) -> str:
        """Нормализовать символ для биржи"""
        symbol = symbol.upper()
        if not symbol.endswith('/USDT:USDT'):
            if '/USDT' in symbol:
                symbol = symbol.replace('/USDT', '/USDT:USDT')
            elif ':USDT' not in symbol:
                symbol = f"{symbol}/USDT:USDT"
        return symbol
    
    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """Расчет RSI"""
        if len(closes) < period + 1:
            return 50.0
        
        gains = 0.0
        losses = 0.0
        
        for i in range(1, period + 1):
            delta = closes[-(period - i + 1)] - closes[-(period - i + 2)]
            if delta > 0:
                gains += delta
            else:
                losses += abs(delta)
        
        if losses == 0:
            return 100.0
        
        rs = gains / losses
        return 100 - (100 / (1 + rs))
    
    def _generate_recommendation(self, analysis: Dict) -> str:
        """Генерация рекомендации на основе анализа"""
        rsi = analysis.get('rsi_15m', 50)
        spread = analysis.get('spread_pct', 0)
        imbalance = analysis.get('order_imbalance', 0)
        
        if spread > 0.3:
            return "Высокий спред! Не рекомендуется торговать."
        
        if rsi > 75:
            if imbalance < -10:
                return "SHORT: RSI высокий + продавцы давят"
            return "SHORT возможен: RSI перекуплен"
        elif rsi < 25:
            if imbalance > 10:
                return "LONG: RSI низкий + покупатели входят"
            return "LONG возможен: RSI перепродан"
        else:
            return "Ждать: RSI в нейтральной зоне"
    
    def get_available_tools(self) -> List[Dict]:
        """Список доступных инструментов"""
        return [
            {'name': 'load_candles', 'description': 'Загрузить свечи'},
            {'name': 'get_orderbook', 'description': 'Получить стакан'},
            {'name': 'get_btc_trend', 'description': 'Тренд Bitcoin'},
            {'name': 'get_market_overview', 'description': 'Обзор рынка'},
            {'name': 'analyze_symbol', 'description': 'Анализ монеты'},
            {'name': 'find_patterns', 'description': 'Поиск паттернов'},
            {'name': 'get_trading_statistics', 'description': 'Статистика'},
            {'name': 'backtest_strategy', 'description': 'Бэктест стратегии'},
            {'name': 'run_backtest_multi', 'description': 'Бэктест на нескольких монетах'},
            {'name': 'generate_strategy_suggestions', 'description': 'Предложить стратегии'},
            {'name': 'get_open_positions', 'description': 'Открытые позиции'},
            {'name': 'close_position', 'description': 'Закрыть позицию'},
            {'name': 'close_all_positions', 'description': 'Закрыть все позиции'},
            {'name': 'add_to_blacklist', 'description': 'Добавить в ЧС'},
            {'name': 'blacklist_worst_from_backtest', 'description': 'Добавить убыточные из бэктеста в ЧС'},
            {'name': 'save_optimized_strategy', 'description': 'Сохранить стратегию с названием'},
            {'name': 'optimize_btc_levels', 'description': 'Оптимизация BTC уровней открытия/закрытия'},
            {'name': 'apply_btc_levels', 'description': 'Применить оптимизированные BTC уровни'},
            {'name': 'change_settings', 'description': 'Изменить настройки'},
            {'name': 'pause_scanner', 'description': 'Пауза сканера'},
            {'name': 'remember_command', 'description': 'Запомнить команду'},
            {'name': 'recall_commands', 'description': 'Вспомнить команды'},
            {'name': 'save_lesson', 'description': 'Сохранить урок'},
            {'name': 'update_stop_loss', 'description': 'Изменить Stop Loss'},
            {'name': 'update_take_profit', 'description': 'Изменить Take Profit'}
        ]
    
    def update_stop_loss(self, trade_id: str, new_sl: float) -> Dict:
        """Изменить Stop Loss для позиции"""
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            if hasattr(self.trader, 'update_stop_loss'):
                result = self.trader.update_stop_loss(trade_id, new_sl)
                if result:
                    return {
                        'success': True,
                        'trade_id': trade_id,
                        'new_sl': new_sl,
                        'summary': f"✅ SL для {trade_id} изменён на ${new_sl:.6f}"
                    }
                else:
                    return {'error': 'Failed to update SL', 'summary': f"❌ Не удалось изменить SL для {trade_id}"}
            else:
                return {'error': 'Method not available', 'summary': '❌ Метод update_stop_loss не найден'}
        except Exception as e:
            logger.error(f"[TOOLS] update_stop_loss error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def update_take_profit(self, trade_id: str, new_tp: float) -> Dict:
        """Изменить Take Profit для позиции"""
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            if hasattr(self.trader, 'update_take_profit'):
                result = self.trader.update_take_profit(trade_id, new_tp)
                if result:
                    return {
                        'success': True,
                        'trade_id': trade_id,
                        'new_tp': new_tp,
                        'summary': f"✅ TP для {trade_id} изменён на ${new_tp:.6f}"
                    }
                else:
                    return {'error': 'Failed to update TP', 'summary': f"❌ Не удалось изменить TP для {trade_id}"}
            else:
                return {'error': 'Method not available', 'summary': '❌ Метод update_take_profit не найден'}
        except Exception as e:
            logger.error(f"[TOOLS] update_take_profit error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    # =========================================================================
    # STRATEGY TOOLS - Управление стратегиями
    # =========================================================================
    
    def get_current_strategy(self) -> Dict:
        """Получить текущую стратегию из настроек трейдера"""
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            settings = self.trader.get_settings()
            
            strategy = {
                'name': 'CURRENT',
                'parameters': {
                    # Вход
                    'rsi_overbought': settings.get('rsi_overbought', 70),
                    'rsi_oversold': settings.get('rsi_oversold', 30),
                    'min_atr_percent': settings.get('min_atr_percent', 1.5),
                    'min_volume_usdt': settings.get('min_volume_usdt', 10000000),
                    'min_change_percent': settings.get('min_change_percent', 3.0),
                    'min_change_filter': settings.get('min_change_filter', 5.0),
                    # Риск
                    'stop_loss_pct': settings.get('stop_loss_pct', 5.0),
                    'take_profit_pct': settings.get('take_profit_pct', 7.0),
                    'leverage': settings.get('leverage', 5),
                    'position_size': settings.get('position_size', 500),
                    'max_positions': settings.get('max_positions', 5),
                    # Фильтры
                    'btc_trend_filter_enabled': settings.get('btc_trend_filter_enabled', True),
                    'volume_filter_enabled': settings.get('volume_filter_enabled', True),
                    # Трейлинг
                    'trailing_enabled': settings.get('trailing_enabled', True),
                    'trailing_activation_pct': settings.get('trailing_activation_pct', 2.0),
                    'trailing_distance_pct': settings.get('trailing_distance_pct', 1.0),
                    # Partial TP
                    'partial_tp_enabled': settings.get('partial_tp_enabled', False),
                    'partial_tp_percent': settings.get('partial_tp_percent', 50),
                },
                'mode': settings.get('trade_mode', 'PAPER')
            }
            
            # Формируем читаемый вывод
            lines = ["📊 **Текущая стратегия:**\n"]
            lines.append("**Вход:**")
            lines.append(f"  • RSI SHORT: ≥{strategy['parameters']['rsi_overbought']}")
            lines.append(f"  • RSI LONG: ≤{strategy['parameters']['rsi_oversold']}")
            lines.append(f"  • Min ATR: {strategy['parameters']['min_atr_percent']}%")
            lines.append(f"  • Min Volume: ${strategy['parameters']['min_volume_usdt']/1e6:.0f}M")
            lines.append(f"  • Min Change: {strategy['parameters']['min_change_percent']}%")
            
            lines.append("\n**Риск:**")
            lines.append(f"  • Stop Loss: {strategy['parameters']['stop_loss_pct']}%")
            lines.append(f"  • Take Profit: {strategy['parameters']['take_profit_pct']}%")
            lines.append(f"  • Leverage: {strategy['parameters']['leverage']}x")
            lines.append(f"  • Position Size: ${strategy['parameters']['position_size']}")
            lines.append(f"  • Max Positions: {strategy['parameters']['max_positions']}")
            
            lines.append("\n**Фильтры:**")
            lines.append(f"  • BTC Trend Filter: {'✅' if strategy['parameters']['btc_trend_filter_enabled'] else '❌'}")
            lines.append(f"  • Volume Filter: {'✅' if strategy['parameters']['volume_filter_enabled'] else '❌'}")
            # Recheck at open из config.json
            try:
                import json as _json
                with open('config.json', 'r', encoding='utf-8') as _f:
                    _recheck = _json.load(_f).get('filters', {}).get('recheck_change_at_open', True)
            except Exception:
                _recheck = True
            lines.append(f"  • Recheck Change at Open: {'✅' if _recheck else '❌'}")
            
            lines.append("\n**Трейлинг:**")
            lines.append(f"  • Enabled: {'✅' if strategy['parameters']['trailing_enabled'] else '❌'}")
            lines.append(f"  • Activation: {strategy['parameters']['trailing_activation_pct']}%")
            lines.append(f"  • Distance: {strategy['parameters']['trailing_distance_pct']}%")
            
            lines.append("\n**Partial TP:**")
            lines.append(f"  • Enabled: {'✅' if strategy['parameters']['partial_tp_enabled'] else '❌'}")
            if strategy['parameters']['partial_tp_enabled']:
                lines.append(f"  • Percent: {strategy['parameters']['partial_tp_percent']}%")
            
            lines.append(f"\n**Режим:** {strategy['mode']}")
            
            strategy['summary'] = '\n'.join(lines)
            return strategy
            
        except Exception as e:
            logger.error(f"[TOOLS] get_current_strategy error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}

    
    def set_strategy_param(self, param: str, value: Any) -> Dict:
        """Изменить параметр стратегии"""
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            # Маппинг параметров
            param_map = {
                'rsi_short': 'rsi_overbought',
                'rsi_long': 'rsi_oversold',
                'rsi_overbought': 'rsi_overbought',
                'rsi_oversold': 'rsi_oversold',
                'sl': 'stop_loss_pct',
                'stop_loss': 'stop_loss_pct',
                'stop_loss_pct': 'stop_loss_pct',
                'tp': 'take_profit_pct',
                'take_profit': 'take_profit_pct',
                'take_profit_pct': 'take_profit_pct',
                'leverage': 'leverage',
                'position_size': 'position_size',
                'max_positions': 'max_positions',
                'atr': 'min_atr_percent',
                'min_atr': 'min_atr_percent',
                'min_atr_percent': 'min_atr_percent',
                'trailing': 'trailing_enabled',
                'trailing_enabled': 'trailing_enabled',
                'trailing_activation': 'trailing_activation_pct',
                'trailing_distance': 'trailing_distance_pct',
                'btc_filter': 'btc_trend_filter_enabled',
                'volume_filter': 'volume_filter_enabled',
                'recheck_change': 'recheck_change_at_open',
                'recheck_change_at_open': 'recheck_change_at_open',
                'recheck': 'recheck_change_at_open',
                'volume_filter_enabled': 'volume_filter_enabled',
                'whitelist': 'whitelist_enabled',
                'whitelist_enabled': 'whitelist_enabled',
                'partial_tp': 'partial_tp_enabled',
                'partial_tp_percent': 'partial_tp_percent',
            }
            
            actual_param = param_map.get(param.lower(), param)
            
            # Параметры которые хранятся в config.json/filters (не в settings трейдера)
            CONFIG_FILTER_PARAMS = ['recheck_change_at_open']
            
            # Преобразуем значение
            if actual_param in ['trailing_enabled', 'btc_trend_filter_enabled', 'partial_tp_enabled', 'volume_filter_enabled', 'whitelist_enabled', 'recheck_change_at_open']:
                if isinstance(value, str):
                    value = value.lower() in ['true', '1', 'yes', 'on', 'да']
                else:
                    value = bool(value)
            elif actual_param in ['leverage', 'max_positions', 'partial_tp_percent']:
                value = int(value)
            else:
                value = float(value)
            
            # Если параметр живёт в config.json filters — сохраняем туда
            if actual_param in CONFIG_FILTER_PARAMS:
                try:
                    import json as _json
                    with open('config.json', 'r', encoding='utf-8') as _f:
                        _config = _json.load(_f)
                    old_value = _config.get('filters', {}).get(actual_param, 'N/A')
                    if 'filters' not in _config:
                        _config['filters'] = {}
                    _config['filters'][actual_param] = value
                    with open('config.json', 'w', encoding='utf-8') as _f:
                        _json.dump(_config, _f, indent=2, ensure_ascii=False)
                    return {
                        'success': True,
                        'param': actual_param,
                        'old_value': old_value,
                        'new_value': value,
                        'summary': f"✅ {actual_param}: {old_value} → {value} (сохранено в фильтры)"
                    }
                except Exception as _e:
                    return {'error': str(_e), 'summary': f"❌ Ошибка сохранения фильтра: {_e}"}
            
            # Получаем старое значение
            old_settings = self.trader.get_settings()
            old_value = old_settings.get(actual_param, 'N/A')
            
            # Применяем
            result = self.trader.update_settings({actual_param: value})
            
            if result:
                return {
                    'success': True,
                    'param': actual_param,
                    'old_value': old_value,
                    'new_value': value,
                    'summary': f"✅ {actual_param}: {old_value} → {value}"
                }
            else:
                return {'error': 'Update failed', 'summary': f"❌ Не удалось изменить {param}"}
            
        except Exception as e:
            logger.error(f"[TOOLS] set_strategy_param error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def save_strategy(self, name: str, description: str = None) -> Dict:
        """Сохранить текущую стратегию в память"""
        try:
            if not self.brain:
                return {'error': 'Brain not available', 'summary': '❌ Память не подключена'}
            
            # Получаем текущие настройки
            current = self.get_current_strategy()
            if 'error' in current:
                return current
            
            parameters = current.get('parameters', {})
            
            # Сохраняем в brain
            strategy_id = self.brain.save_strategy_full(
                name=name,
                parameters=parameters,
                description=description or f"Стратегия сохранена {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                is_active=False
            )
            
            return {
                'success': True,
                'strategy_id': strategy_id,
                'name': name,
                'summary': f"💾 Стратегия **{name}** сохранена в память!"
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] save_strategy error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def load_strategy(self, name: str) -> Dict:
        """Загрузить стратегию из памяти и применить"""
        try:
            if not self.brain:
                return {'error': 'Brain not available', 'summary': '❌ Память не подключена'}
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            # Получаем стратегию
            strategy = self.brain.get_strategy_by_name(name)
            if not strategy:
                return {'error': 'Strategy not found', 'summary': f"❌ Стратегия **{name}** не найдена"}
            
            # Применяем параметры
            parameters = strategy.get('parameters', {})
            self.trader.update_settings(parameters)
            
            # Отмечаем как активную
            self.brain.set_active_strategy(name)
            
            lines = [f"✅ Стратегия **{name}** загружена!\n"]
            for k, v in parameters.items():
                lines.append(f"  • {k}: {v}")
            
            return {
                'success': True,
                'name': name,
                'parameters': parameters,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] load_strategy error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def list_strategies(self) -> Dict:
        """Список всех сохранённых стратегий"""
        try:
            if not self.brain:
                return {'error': 'Brain not available', 'summary': '❌ Память не подключена'}
            
            strategies = self.brain.list_all_strategies()
            
            if not strategies:
                return {'strategies': [], 'summary': "📁 Нет сохранённых стратегий"}
            
            lines = [f"📁 **Сохранённые стратегии ({len(strategies)}):**\n"]
            
            for i, s in enumerate(strategies, 1):
                active = " ✅ ACTIVE" if s['is_active'] else ""
                wr = f"WR:{s['win_rate']:.0f}%" if s['win_rate'] else ""
                pf = f"PF:{s['profit_factor']:.2f}" if s['profit_factor'] else ""
                trades = f"({s['total_trades']} trades)" if s['total_trades'] else ""
                
                lines.append(f"{i}. **{s['name']}**{active}")
                if s['description']:
                    lines.append(f"   {s['description'][:50]}")
                if wr or pf:
                    lines.append(f"   {wr} {pf} {trades}")
                lines.append(f"   Создана: {s['created_at'][:10] if s['created_at'] else 'N/A'}")
                lines.append("")
            
            return {
                'strategies': strategies,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] list_strategies error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    # =========================================================================
    # CANDLES & HISTORY - Свечи и история
    # =========================================================================
    
    # Маппинг символов для Binance Futures (некоторые монеты имеют префикс 1000)
    SYMBOL_MAP = {
        'SHIB': '1000SHIB',
        'PEPE': '1000PEPE',
        'FLOKI': '1000FLOKI',
        'LUNC': '1000LUNC',
        'BONK': '1000BONK',
        'SATS': '1000SATS',
        'RATS': '1000RATS',
        'CAT': '1000CAT',
    }
    
    def _normalize_symbol(self, symbol: str) -> tuple:
        """
        Нормализовать символ для Binance
        Возвращает (clean_symbol, full_symbol)
        """
        clean = symbol.upper().replace('/USDT', '').replace(':USDT', '').replace('1000', '')
        
        # Проверяем маппинг
        if clean in self.SYMBOL_MAP:
            mapped = self.SYMBOL_MAP[clean]
            return mapped, f"{mapped}/USDT:USDT"
        
        return clean, f"{clean}/USDT:USDT"
    
    def load_candles_cached(self, symbol: str, timeframe: str = '15m', 
                           days: int = 30) -> Dict:
        """
        Загрузить свечи с кэшированием (до 1 месяца)
        Сначала проверяет кэш, потом дозагружает недостающие
        """
        try:
            if not self.exchange:
                return {'error': 'Exchange not available', 'summary': '❌ Exchange не подключен'}
            if not self.brain:
                return {'error': 'Brain not available', 'summary': '❌ Память не подключена'}
            
            # Нормализуем символ (с учётом маппинга 1000SHIB и т.д.)
            clean_symbol, full_symbol = self._normalize_symbol(symbol)
            
            # Определяем временные рамки
            end_ts = int(datetime.now().timestamp() * 1000)
            start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
            
            # Проверяем кэш
            cached = self.brain.get_candles(clean_symbol, timeframe, start_ts, end_ts)
            cached_count = len(cached)
            
            # Определяем сколько нужно докачать
            tf_minutes = {'1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440}
            minutes = tf_minutes.get(timeframe, 15)
            expected_candles = (days * 24 * 60) // minutes
            
            need_fetch = expected_candles - cached_count
            
            if need_fetch > 100:
                # Нужно догрузить
                logger.info(f"[TOOLS] Fetching {need_fetch} candles for {clean_symbol} {timeframe}")
                
                # Загружаем порциями по 1000
                all_candles = []
                fetch_since = start_ts
                
                while fetch_since < end_ts:
                    try:
                        candles = self.exchange.fetch_ohlcv(
                            full_symbol, timeframe, 
                            since=fetch_since, limit=1000
                        )
                        if not candles:
                            break
                        all_candles.extend(candles)
                        fetch_since = candles[-1][0] + 1
                        
                        # Защита от бесконечного цикла
                        if len(all_candles) > expected_candles * 1.5:
                            break
                    except Exception as e:
                        logger.error(f"[TOOLS] Fetch error: {e}")
                        break
                
                # Сохраняем в кэш
                if all_candles:
                    saved = self.brain.save_candles(clean_symbol, timeframe, all_candles)
                    logger.info(f"[TOOLS] Saved {saved} candles to cache")
                
                # Получаем итоговые данные из кэша
                cached = self.brain.get_candles(clean_symbol, timeframe, start_ts, end_ts)
            
            return {
                'success': True,
                'symbol': clean_symbol,
                'timeframe': timeframe,
                'days': days,
                'candles_count': len(cached),
                'candles': cached,
                'summary': f"📊 {clean_symbol} {timeframe}: загружено {len(cached)} свечей за {days} дней"
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] load_candles_cached error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def get_top_liquid_coins(self, limit: int = 20) -> Dict:
        """Получить топ монет по объёму торгов"""
        try:
            if not self.exchange:
                return {'error': 'Exchange not available', 'summary': '❌ Exchange не подключен'}
            
            tickers = self.exchange.fetch_tickers()
            
            # Фильтруем USDT пары и сортируем по объёму
            usdt_pairs = []
            for symbol, ticker in tickers.items():
                if '/USDT:USDT' in symbol or (symbol.endswith('/USDT') and ':' not in symbol):
                    volume = ticker.get('quoteVolume', 0) or 0
                    if volume > 1000000:  # > $1M
                        clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '').replace(':USDT', '')
                        usdt_pairs.append({
                            'symbol': clean,
                            'volume_24h': volume,
                            'price': ticker.get('last', 0),
                            'change_24h': ticker.get('percentage', 0) or 0
                        })
            
            # Сортируем по объёму
            usdt_pairs.sort(key=lambda x: x['volume_24h'], reverse=True)
            top_coins = usdt_pairs[:limit]
            
            lines = [f"📊 **Топ {limit} по объёму:**\n"]
            for i, coin in enumerate(top_coins, 1):
                vol_m = coin['volume_24h'] / 1e6
                change = coin['change_24h']
                emoji = '🟢' if change > 0 else '🔴' if change < 0 else 'âšª'
                lines.append(f"{i}. {coin['symbol']}: ${vol_m:.0f}M {emoji}{change:+.1f}%")
            
            return {
                'coins': top_coins,
                'count': len(top_coins),
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] get_top_liquid_coins error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def get_trade_history(self, days: int = 30, symbol: str = None) -> Dict:
        """Получить историю сделок"""
        try:
            if not self.db:
                return {'error': 'DB not available', 'summary': '❌ БД не подключена'}
            
            # Получаем из БД
            trades = self.db.get_trades(days=days, symbol=symbol)
            
            if not trades:
                return {'trades': [], 'summary': f"📭 Нет сделок за {days} дней"}
            
            # Статистика
            wins = [t for t in trades if t.get('pnl_usdt', 0) > 0]
            losses = [t for t in trades if t.get('pnl_usdt', 0) <= 0]
            total_pnl = sum(t.get('pnl_usdt', 0) for t in trades)
            
            lines = [f"📈 **История сделок ({len(trades)} за {days} дней):**\n"]
            lines.append(f"Win/Lose: {len(wins)}/{len(losses)} (WR: {len(wins)/len(trades)*100:.1f}%)")
            lines.append(f"Общий PnL: ${total_pnl:+.2f}")
            
            if len(wins) > 0:
                lines.append(f"Средний win: ${sum(t.get('pnl_usdt', 0) for t in wins)/len(wins):.2f}")
            if len(losses) > 0:
                lines.append(f"Средний loss: ${sum(t.get('pnl_usdt', 0) for t in losses)/len(losses):.2f}")
            
            return {
                'trades': trades,
                'stats': {
                    'total': len(trades),
                    'wins': len(wins),
                    'losses': len(losses),
                    'win_rate': len(wins)/len(trades)*100 if trades else 0,
                    'total_pnl': total_pnl
                },
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] get_trade_history error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    # =========================================================================
    # BACKTEST - Расширенный бэктест
    # =========================================================================
    
    def _calc_btc_trend_array(self, btc_candles: List[Dict]) -> Dict[int, Dict]:
        """
        Рассчитать BTC тренд для каждой свечи.
        Используем SMA20 vs SMA50 для определения тренда.
        pct = rolling 24h change (совпадает с change_24h в live).
        
        Returns: {timestamp_ms: {'trend': 'bullish'|'bearish'|'neutral', 'pct': float}}
        """
        trends = {}
        closes = [c['close'] for c in btc_candles]
        
        # 96 свечей по 15м = 24 часа
        lookback_24h = 96
        start_idx = max(50, lookback_24h)
        
        for i in range(start_idx, len(btc_candles)):
            sma20 = sum(closes[i-20:i]) / 20
            sma50 = sum(closes[i-50:i]) / 50
            
            diff_pct = (sma20 - sma50) / sma50 * 100
            
            if diff_pct > 0.3:
                trend = 'bullish'
            elif diff_pct < -0.3:
                trend = 'bearish'
            else:
                trend = 'neutral'
            
            # Сила тренда = change за 24ч (как в live trading)
            change_24h = (closes[i] - closes[i - lookback_24h]) / closes[i - lookback_24h] * 100
            
            ts = btc_candles[i].get('timestamp', btc_candles[i].get('time', 0))
            if isinstance(ts, (int, float)):
                ts = int(ts) - (int(ts) % 900000) if ts > 1e12 else int(ts * 1000) - (int(ts * 1000) % 900000)
            trends[ts] = {'trend': trend, 'pct': abs(change_24h), 'change_24h': change_24h}
        
        return trends
    
    def _get_btc_trend_at(self, btc_trends: Dict[int, any], candle_ts: int) -> Dict:
        """Получить BTC тренд для заданного timestamp.
        Returns: {'trend': str, 'pct': float, 'change_24h': float}
        """
        default = {'trend': 'neutral', 'pct': 0.0, 'change_24h': 0.0}
        if not btc_trends:
            return default
        # Нормализуем timestamp
        if candle_ts > 1e12:
            ts = int(candle_ts) - (int(candle_ts) % 900000)
        else:
            ts = int(candle_ts * 1000) - (int(candle_ts * 1000) % 900000)
        
        # Точное совпадение
        if ts in btc_trends:
            val = btc_trends[ts]
            if isinstance(val, str):
                return {'trend': val, 'pct': 0.0, 'change_24h': 0.0}
            # Обратная совместимость: если нет change_24h
            if 'change_24h' not in val:
                val['change_24h'] = val.get('pct', 0.0) if val.get('trend') == 'bullish' else -val.get('pct', 0.0)
            return val
        
        # Ищем ближайший (в пределах 30 мин)
        for offset in [900000, -900000, 1800000, -1800000]:
            if (ts + offset) in btc_trends:
                val = btc_trends[ts + offset]
                if isinstance(val, str):
                    return {'trend': val, 'pct': 0.0, 'change_24h': 0.0}
                if 'change_24h' not in val:
                    val['change_24h'] = val.get('pct', 0.0) if val.get('trend') == 'bullish' else -val.get('pct', 0.0)
                return val
        
        return default
    
    def run_backtest_multi(self, strategy_name: str = None, symbols: List[str] = None,
                          days: int = 30, use_top: int = None, custom_params: Dict = None,
                          return_details: bool = False) -> Dict:
        """
        Запустить бэктест на нескольких монетах
        
        Args:
            strategy_name: Имя стратегии из памяти (или 'current' для текущей)
            symbols: Список монет для теста
            days: Период в днях
            use_top: Использовать топ N монет по объёму
        """
        try:
            # Получаем стратегию
            if custom_params:
                # Используем переданные параметры (для оптимизации)
                params = custom_params
                strategy_name = 'CUSTOM'
            elif strategy_name and strategy_name.lower() != 'current':
                strategy = self.brain.get_strategy_by_name(strategy_name)
                if not strategy:
                    return {'error': f'Strategy {strategy_name} not found'}
                params = strategy.get('parameters', {})
            else:
                current = self.get_current_strategy()
                params = current.get('parameters', {})
                strategy_name = 'CURRENT'
            
            # Получаем список монет
            if use_top:
                top_coins = self.get_top_liquid_coins(use_top)
                symbols = [c['symbol'] for c in top_coins.get('coins', [])]
            elif not symbols:
                symbols = ['DOGE', 'PEPE', 'SHIB', 'SOL', 'XRP']
            
            # Параметры стратегии
            rsi_short = params.get('rsi_overbought', 70)
            rsi_long = params.get('rsi_oversold', 30)
            sl_pct = params.get('stop_loss_pct', 5.0)
            tp_pct = params.get('take_profit_pct', 7.0)
            position_size = params.get('position_size', 500)
            leverage = params.get('leverage', 5)
            
            # Параметры трейлинга
            trailing_enabled = params.get('trailing_enabled', True)
            trailing_activation = params.get('trailing_activation_pct', 2.0)
            trailing_distance = params.get('trailing_distance_pct', 1.0)
            
            # Параметры реалистичности (v6.0+)
            commission_pct = params.get('commission_pct', 0.08)  # 0.04% open + 0.04% close
            slippage_pct = params.get('slippage_pct', 0.05)     # ~0.05% проскальзывание
            max_positions = params.get('max_positions', 10)    # 10 = реалистичный режим по умолчанию
            # Антизацикливание по монете
            symbol_cooldown_candles = params.get('symbol_cooldown_candles', 2)   # 2 свечи * 15мин = 30мин
            max_symbol_losses_daily = params.get('max_symbol_losses_daily', 2)   # Макс 2 SL за день
            # Минимальное изменение монеты за 24ч (как в live сканере!)
            min_change_pct = params.get('min_change_filter', 0.0)  # 0 = без фильтра
            # Recheck at open — читаем из config.json
            try:
                import json as _json
                with open('config.json', 'r', encoding='utf-8') as _f:
                    recheck_change_at_open = _json.load(_f).get('filters', {}).get('recheck_change_at_open', True)
            except Exception:
                recheck_change_at_open = True
            
            all_trade_details = []  # Для анализа паттернов
            
            results = {
                'strategy': strategy_name,
                'period_days': days,
                'symbols_requested': len(symbols),  # Сколько запросили
                'symbols_tested': len(symbols),
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'total_pnl': 0,
                'gross_profit': 0,
                'gross_loss': 0,
                'by_symbol': {},
                'best_symbols': [],
                'worst_symbols': [],
                'all_symbols': [],  # ВСЕ символы для whitelist
                'symbols_loaded': [],  # Успешно загружены
                'symbols_blacklisted': [],  # Пропущены из-за blacklist
                'symbols_no_data': []  # Пропущены из-за недостатка данных
            }
            
            # ШАГ 1: Загружаем свечи для всех монет
            loaded_candles = {}  # symbol -> candles
            import time as _time
            load_start = _time.time()
            load_timeout = 300  # 5 минут макс на загрузку
            load_errors = 0
            max_load_errors = 10  # После 10 ошибок подряд — стоп (Binance rate limit)
            consecutive_errors = 0
            
            for idx, symbol in enumerate(symbols):
                # Таймаут загрузки
                elapsed = _time.time() - load_start
                if elapsed > load_timeout:
                    logger.warning(f"[BACKTEST] Загрузка прервана по таймауту ({load_timeout}с), загружено {len(loaded_candles)} из {len(symbols)}")
                    break
                
                # Слишком много ошибок подряд — rate limit
                if consecutive_errors >= max_load_errors:
                    logger.warning(f"[BACKTEST] Загрузка прервана: {max_load_errors} ошибок подряд (rate limit?)")
                    break
                
                # Прогресс каждые 50 монет — в UI лог
                if idx > 0 and idx % 50 == 0:
                    logger.info(f"[BACKTEST] Загрузка: {idx}/{len(symbols)} монет ({len(loaded_candles)} OK, {elapsed:.0f}с)")
                    if self.trader and hasattr(self.trader, '_add_log'):
                        self.trader._add_log("filter", f"📊 Бэктест: загружено {len(loaded_candles)}/{idx} монет ({elapsed:.0f}с)...")
                
                try:
                    clean_symbol, full_symbol = self._normalize_symbol(symbol)
                    
                    if self.db and self.db.is_blacklisted(full_symbol):
                        results['symbols_blacklisted'].append(clean_symbol)
                        continue
                    
                    candles_data = self.load_candles_cached(symbol, '15m', days)
                    candles = candles_data.get('candles', [])
                    
                    if len(candles) < 50:
                        results['symbols_no_data'].append(f"{clean_symbol}({len(candles)})")
                        continue
                    
                    results['symbols_loaded'].append(clean_symbol)
                    loaded_candles[clean_symbol] = candles
                    consecutive_errors = 0  # Сброс при успехе
                    
                except Exception as e:
                    load_errors += 1
                    consecutive_errors += 1
                    if consecutive_errors <= 3:
                        logger.error(f"[BACKTEST] Load error for {symbol}: {e}")
                    continue
            
            load_elapsed = _time.time() - load_start
            logger.info(f"[BACKTEST] Загрузка завершена: {len(loaded_candles)} монет за {load_elapsed:.0f}с (ошибок: {load_errors})")
            
            if len(loaded_candles) == 0:
                return {'error': 'No data loaded', 'summary': f'❌ Не удалось загрузить свечи ни для одной монеты (ошибок: {load_errors})'}
            
            results['symbols_tested'] = len(loaded_candles)
            
            # ШАГ 1.5: Загружаем BTC свечи для фильтра тренда
            btc_trends = {}
            btc_modes = {}
            try:
                settings = self.trader.get_settings() if self.trader else {}
                btc_filter_on = settings.get('btc_trend_filter_enabled', True)
                if btc_filter_on:
                    # Кастомные режимы от оптимизатора
                    if custom_params and 'btc_modes' in custom_params:
                        btc_modes = custom_params['btc_modes']
                    else:
                        # ЧИТАЕМ ИЗ SETTINGS (единый источник правды!)
                        # НЕ из self.trader.filters — он может быть рассинхронизирован
                        btc_modes = {
                            'bullish': settings.get('btc_bullish_mode', 'long_only'),
                            'bearish': settings.get('btc_bearish_mode', 'short_only'),
                            'neutral': settings.get('btc_neutral_mode', 'any'),
                            'bullish_min_str': float(settings.get('btc_bullish_min_strength', 0.5)),
                            'bearish_min_str': float(settings.get('btc_bearish_min_strength', 0.5)),
                        }
                    # Загружаем BTC свечи
                    btc_data = self.load_candles_cached('BTC', '15m', days)
                    btc_candles = btc_data.get('candles', [])
                    if len(btc_candles) >= 60:
                        btc_trends = self._calc_btc_trend_array(btc_candles)
                        logger.info(f"[BACKTEST] BTC тренды: {len(btc_trends)} точек, режимы: {btc_modes}")
            except Exception as e:
                logger.warning(f"[BACKTEST] BTC trends load error: {e}")
            
            # ШАГ 1.6: Режимы автозакрытия при ослаблении BTC тренда
            btc_close_modes = {}
            if btc_trends:
                try:
                    # Если переданы кастомные параметры (от оптимизатора)
                    if custom_params and 'btc_close_modes' in custom_params:
                        btc_close_modes = custom_params['btc_close_modes']
                    else:
                        settings = self.trader.get_settings() if self.trader else {}
                        btc_close_modes = {
                            'close_long_on_weak_bull': settings.get('close_long_on_weak_bull', False),
                            'close_long_weak_bull_threshold': float(settings.get('close_long_weak_bull_threshold', 0.5)),
                            'close_short_on_weak_bear': settings.get('close_short_on_weak_bear', False),
                            'close_short_weak_bear_threshold': float(settings.get('close_short_weak_bear_threshold', 0.5)),
                        }
                    if btc_close_modes.get('close_long_on_weak_bull') or btc_close_modes.get('close_short_on_weak_bear'):
                        logger.info(f"[BACKTEST] BTC автозакрытие: {btc_close_modes}")
                except Exception as e:
                    logger.warning(f"[BACKTEST] BTC close modes error: {e}")
            
            # ШАГ 2: Бэктест каждой монеты отдельно (точная симуляция)
            need_details = return_details or max_positions > 0  # Детали нужны для лимита позиций
            bt_start = _time.time()
            for bt_idx, (clean_symbol, candles) in enumerate(loaded_candles.items()):
                try:
                    # Прогресс каждые 100 монет
                    if bt_idx > 0 and bt_idx % 100 == 0:
                        bt_elapsed = _time.time() - bt_start
                        logger.info(f"[BACKTEST] Расчёт: {bt_idx}/{len(loaded_candles)} монет ({bt_elapsed:.0f}с), PnL=${results['total_pnl']:+.2f}")
                        if self.trader and hasattr(self.trader, '_add_log'):
                            self.trader._add_log("filter", f"📊 Бэктест: расчёт {bt_idx}/{len(loaded_candles)}, PnL=${results['total_pnl']:+.2f}")
                    
                    symbol_result = self._backtest_symbol(
                        candles, rsi_short, rsi_long, sl_pct, tp_pct,
                        position_size, leverage,
                        trailing_enabled, trailing_activation, trailing_distance,
                        return_details=need_details,
                        commission_pct=commission_pct,
                        slippage_pct=slippage_pct,
                        symbol_cooldown_candles=symbol_cooldown_candles,
                        max_symbol_losses_daily=max_symbol_losses_daily,
                        btc_trends=btc_trends,
                        btc_modes=btc_modes,
                        btc_close_modes=btc_close_modes,
                        min_change_pct=min_change_pct,
                        recheck_change_at_open=recheck_change_at_open
                    )
                    
                    if need_details and 'trade_details' in symbol_result:
                        for td in symbol_result['trade_details']:
                            td['symbol'] = clean_symbol
                            all_trade_details.append(td)
                    
                    results['by_symbol'][clean_symbol] = symbol_result
                    results['total_trades'] += symbol_result['trades']
                    results['wins'] += symbol_result['wins']
                    results['losses'] += symbol_result['losses']
                    results['total_pnl'] += symbol_result['pnl']
                    results['gross_profit'] += symbol_result.get('gross_profit', 0)
                    results['gross_loss'] += symbol_result.get('gross_loss', 0)
                    results['trailing_wins'] = results.get('trailing_wins', 0) + symbol_result.get('trailing_wins', 0)
                    results['total_commission'] = results.get('total_commission', 0) + symbol_result.get('total_commission', 0)
                    
                except Exception as e:
                    logger.error(f"[TOOLS] Backtest error for {clean_symbol}: {e}")
                    continue
            
            # ШАГ 3: Применяем лимит позиций (если задан)
            if max_positions > 0 and all_trade_details:
                filtered = self._apply_position_limit(all_trade_details, max_positions)
                
                # Пересчитываем статистику из отфильтрованных сделок
                results['total_trades_unlimited'] = results['total_trades']  # Сохраняем оригинал
                results['total_trades'] = filtered['total_trades']
                results['wins'] = filtered['wins']
                results['losses'] = filtered['losses']
                results['total_pnl'] = filtered['total_pnl']
                results['gross_profit'] = filtered['gross_profit']
                results['gross_loss'] = filtered['gross_loss']
                results['trailing_wins'] = filtered['trailing_wins']
                results['total_commission'] = filtered['total_commission']
                results['skipped_signals'] = filtered['skipped_signals']
                results['max_positions_used'] = max_positions
                results['by_symbol'] = filtered['by_symbol']
                # Пересчитываем комиссию: каждая сделка = position_size * leverage * commission_pct / 100
                results['total_commission'] = filtered['total_trades'] * position_size * leverage * commission_pct / 100
                all_trade_details = filtered['accepted_trades']
            
            # Сортируем по прибыльности
            sorted_symbols = sorted(
                results['by_symbol'].items(),
                key=lambda x: x[1]['pnl'],
                reverse=True
            )
            
            results['best_symbols'] = sorted_symbols[:5]
            results['worst_symbols'] = sorted_symbols[-5:] if len(sorted_symbols) > 5 else []
            results['all_symbols'] = sorted_symbols  # ВСЕ символы для whitelist!
            
            # Итоговая статистика
            wr = results['wins'] / results['total_trades'] * 100 if results['total_trades'] > 0 else 0
            
            # ПРАВИЛЬНЫЙ расчёт Profit Factor = Gross Profit / Gross Loss
            gross_profit = results.get('gross_profit', 0)
            gross_loss = results.get('gross_loss', 0)
            pf = gross_profit / gross_loss if gross_loss > 0 else (999 if gross_profit > 0 else 0)
            
            # Формируем вывод
            lines = [f"📈 **РЕЗУЛЬТАТ БЭКТЕСТА**\n"]
            lines.append(f"Стратегия: **{strategy_name}**")
            lines.append(f"Период: {days} дней")
            if btc_modes:
                mode_labels = {'long_only': '↑L', 'short_only': '↓S', 'any': '↕', 'any_incl_neutral': '↕+N', 'none': '⛔'}
                bull_str = btc_modes.get('bullish_min_str', 0)
                bear_str = btc_modes.get('bearish_min_str', 0)
                btc_info = f"₿ Bull={mode_labels.get(btc_modes.get('bullish',''), '?')}≥{bull_str}% Bear={mode_labels.get(btc_modes.get('bearish',''), '?')}≥{bear_str}% Neut={mode_labels.get(btc_modes.get('neutral',''), '?')}"
                lines.append(f"BTC фильтр: {btc_info} ({len(btc_trends)} точек)")
            if btc_close_modes and (btc_close_modes.get('close_long_on_weak_bull') or btc_close_modes.get('close_short_on_weak_bear')):
                close_parts = []
                if btc_close_modes.get('close_long_on_weak_bull'):
                    close_parts.append(f"LONG<+{btc_close_modes['close_long_weak_bull_threshold']}%")
                if btc_close_modes.get('close_short_on_weak_bear'):
                    close_parts.append(f"SHORT>-{btc_close_modes['close_short_weak_bear_threshold']}%")
                lines.append(f"BTC автозакрытие: {', '.join(close_parts)} (только убыточные)")
            lines.append(f"Монет запрошено: {results['symbols_requested']}")
            lines.append(f"Монет протестировано: {len(results['by_symbol'])}")
            
            # Информация о пропущенных
            if results['symbols_blacklisted']:
                lines.append(f"⛔ В blacklist: {len(results['symbols_blacklisted'])} ({', '.join(results['symbols_blacklisted'][:5])}{'...' if len(results['symbols_blacklisted']) > 5 else ''})")
            if results['symbols_no_data']:
                lines.append(f"⚠️ Нет данных: {len(results['symbols_no_data'])} ({', '.join(results['symbols_no_data'][:5])}{'...' if len(results['symbols_no_data']) > 5 else ''})")
            
            lines.append("")
            
            lines.append("**Результаты:**")
            lines.append(f"  • Всего сделок: {results['total_trades']}")
            lines.append(f"  • Win Rate: {wr:.1f}% ({results['wins']}W / {results['losses']}L)")
            lines.append(f"  • Trailing wins: {results.get('trailing_wins', 0)}")
            # Считаем BTC_TREND_WEAK закрытия из деталей
            if all_trade_details:
                btc_closes = sum(1 for t in all_trade_details if t.get('close_reason') == 'BTC_TREND_WEAK')
                if btc_closes > 0:
                    btc_wins = sum(1 for t in all_trade_details if t.get('close_reason') == 'BTC_TREND_WEAK' and t.get('is_win'))
                    lines.append(f"  • BTC Trend Close: {btc_closes} ({btc_wins} win / {btc_closes - btc_wins} loss)")
            lines.append(f"  • Общий PnL: ${results['total_pnl']:+.2f}")
            lines.append(f"  • Gross Profit: ${gross_profit:+.2f}")
            lines.append(f"  • Gross Loss: ${gross_loss:.2f}")
            lines.append(f"  • Profit Factor: {pf:.2f}")
            total_comm = results.get('total_commission', 0)
            if total_comm > 0:
                lines.append(f"  • 💸 Комиссии: ${total_comm:.2f} ({commission_pct}%/сделку)")
                lines.append(f"  • 📉 Slippage: {slippage_pct:.2f}%/вход")
            if min_change_pct > 0:
                lines.append(f"  • 🔍 Min Change 24h: {min_change_pct}% (как в live сканере)")
            if max_positions > 0:
                skipped = results.get('skipped_signals', 0)
                unlimited = results.get('total_trades_unlimited', 0)
                lines.append(f"  • 🔒 Лимит позиций: {max_positions}")
                if unlimited > 0:
                    lines.append(f"  • 📊 Без лимита: {unlimited} сделок → С лимитом: {results['total_trades']}")
                lines.append(f"  • ⏭️ Пропущено сигналов: {skipped}")
                # Средняя длительность сделки
                if all_trade_details:
                    durations = []
                    for t in all_trade_details:
                        ot = t.get('open_time', 0)
                        ct = t.get('close_time', 0)
                        if ot > 0 and ct > ot:
                            durations.append((ct - ot) / 60000)  # В минутах
                    if durations:
                        avg_dur = sum(durations) / len(durations)
                        lines.append(f"  • ⏱️ Средняя длительность сделки: {avg_dur:.0f} мин ({avg_dur/60:.1f}ч)")
            
            if results['best_symbols']:
                lines.append("\n**Лучшие монеты:**")
                for sym, data in results['best_symbols'][:3]:
                    lines.append(f"  🟢 {sym}: ${data['pnl']:+.2f} (WR:{data['wins']}/{data['trades']})")
            
            if results['worst_symbols']:
                lines.append("\n**Худшие монеты:**")
                for sym, data in results['worst_symbols'][:3]:
                    lines.append(f"  🔴 {sym}: ${data['pnl']:+.2f} (WR:{data['wins']}/{data['trades']})")

            
            results['win_rate'] = wr
            results['profit_factor'] = pf
            results['summary'] = '\n'.join(lines)
            
            # Сохраняем результаты в стратегию
            if strategy_name != 'CURRENT' and self.brain:
                self.brain.update_strategy_results(
                    strategy_name,
                    backtest_results=results,
                    win_rate=wr,
                    profit_factor=pf,
                    total_trades=results['total_trades']
                )
            
            # Сохраняем для blacklist_worst_from_backtest
            self.last_backtest_results = results
            
            # UI уведомление о завершении
            if self.trader and hasattr(self.trader, '_add_log'):
                try:
                    total_elapsed = _time.time() - load_start
                except Exception:
                    total_elapsed = 0
                self.trader._add_log("filter", 
                    f"✅ Бэктест завершён: {len(loaded_candles)} монет, "
                    f"{results['total_trades']} сделок, PnL=${results['total_pnl']:+.2f}, "
                    f"WR={wr:.0f}% ({total_elapsed:.0f}с)")
            
            # Добавляем детали сделок если запрошены
            if return_details:
                results['all_trade_details'] = all_trade_details
            
            return results
            
        except Exception as e:
            logger.error(f"[TOOLS] run_backtest_multi error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def _backtest_symbol(self, candles: List[Dict], rsi_short: int, rsi_long: int,
                        sl_pct: float, tp_pct: float, position_size: float = 500,
                        leverage: int = 5, trailing_enabled: bool = True,
                        trailing_activation_pct: float = 2.0, 
                        trailing_distance_pct: float = 1.0,
                        return_details: bool = False,
                        commission_pct: float = 0.08,
                        slippage_pct: float = 0.05,
                        symbol_cooldown_candles: int = 2,
                        max_symbol_losses_daily: int = 2,
                        btc_trends: Dict = None,
                        btc_modes: Dict = None,
                        btc_close_modes: Dict = None,
                        min_change_pct: float = 0.0,
                        recheck_change_at_open: bool = True) -> Dict:
        """
        Бэктест для одной монеты с PnL в долларах и Trailing Stop
        
        ВАЖНО: Trailing гарантирует минимальную прибыль = activation - distance/2
        
        symbol_cooldown_candles: пропуск N свечей после SL (2 свечи * 15мин = 30мин кулдаун)
        max_symbol_losses_daily: макс SL за 1 день (2 = стандарт)
        min_change_pct: мин. изменение за 24ч для входа (как в live сканере)
        """
        trades = 0
        wins = 0
        losses = 0
        gross_profit = 0.0
        gross_loss = 0.0
        trailing_wins = 0
        total_commission = 0.0
        trade_details = []  # Детали сделок для анализа паттернов
        
        # Антизацикливание: кулдаун после SL
        cooldown_until_idx = 0       # Пропускать входы до этого индекса свечи
        daily_sl_count = 0           # Счётчик SL за текущий день
        current_day = ""             # Текущий день (для сброса счётчика)
        
        # Минимальная гарантированная прибыль от trailing
        # Если activation=2%, distance=1.5%, то минимум = 2% - 1.5% = 0.5% profit
        min_trail_profit_pct = max(0.3, trailing_activation_pct - trailing_distance_pct)
        
        # Рассчитываем RSI
        closes = [c['close'] for c in candles]
        rsi_values = self._calculate_rsi(closes, 14)
        
        # Pre-calculate 24h rolling change для каждой свечи (96 свечей = 24ч на 15m TF)
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
                # ═══ АНТИЗАЦИКЛИВАНИЕ ═══
                # Сброс дневного счётчика SL при смене дня
                ts = candles[i].get('timestamp', candles[i].get('time', 0))
                if ts:
                    try:
                        if isinstance(ts, (int, float)):
                            from datetime import datetime
                            day = datetime.utcfromtimestamp(ts/1000 if ts > 1e12 else ts).strftime('%Y-%m-%d')
                        else:
                            day = str(ts)[:10]
                        if day != current_day:
                            current_day = day
                            daily_sl_count = 0
                    except Exception:
                        pass
                
                # Пропуск: кулдаун после SL
                if i < cooldown_until_idx:
                    continue
                # Пропуск: достигнут лимит SL за день
                if daily_sl_count >= max_symbol_losses_daily:
                    continue
                
                # Вход SHORT (RSI перекуплен)
                if rsi >= rsi_short:
                    # Фильтр минимального изменения за 24ч (как в live сканере!)
                    # SHORT → монета должна ВЫРАСТИ на min_change% (ждём откат вниз)
                    if min_change_pct > 0 and change_24h_values[i] < min_change_pct:
                        continue  # Монета недостаточно выросла → пропускаем SHORT
                    # RECHECK: проверяем что на следующей свече change всё ещё >= порога
                    # (симулируем задержку между сканированием и открытием)
                    if recheck_change_at_open and min_change_pct > 0 and i + 1 < len(change_24h_values):
                        if change_24h_values[i + 1] < min_change_pct:
                            continue  # Опоздавший вход — пропускаем
                    
                    # BTC фильтр
                    if btc_trends and btc_modes:
                        candle_ts = candles[i].get('timestamp', candles[i].get('time', 0))
                        btc_info = self._get_btc_trend_at(btc_trends, candle_ts)
                        btc_trend = btc_info['trend']
                        btc_pct = btc_info['pct']
                        # Определяем mode с учётом мин. силы
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
                            continue  # SHORT запрещён
                    
                    # Slippage: SHORT продаём чуть ниже (хуже для нас)
                    entry_price = current_price * (1 - slippage_pct/100)
                    position = {
                        'side': 'SHORT', 
                        'entry': entry_price, 
                        'sl': entry_price * (1 + sl_pct/100), 
                        'tp': entry_price * (1 - tp_pct/100),
                        'trailing_active': False,
                        'best_price': current_price,
                        'min_profit_sl': entry_price * (1 - min_trail_profit_pct/100),
                        'open_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                        'candle_idx': i
                    }
                # Вход LONG (RSI перепродан)
                elif rsi <= rsi_long:
                    # Фильтр минимального изменения за 24ч (как в live сканере!)
                    # LONG → монета должна УПАСТЬ на min_change% (ждём отскок вверх)
                    if min_change_pct > 0 and change_24h_values[i] > -min_change_pct:
                        continue  # Монета недостаточно упала → пропускаем LONG
                    # RECHECK: проверяем что на следующей свече change всё ещё <= -порога
                    if recheck_change_at_open and min_change_pct > 0 and i + 1 < len(change_24h_values):
                        if change_24h_values[i + 1] > -min_change_pct:
                            continue  # Опоздавший вход — пропускаем
                    
                    # BTC фильтр
                    if btc_trends and btc_modes:
                        candle_ts = candles[i].get('timestamp', candles[i].get('time', 0))
                        btc_info = self._get_btc_trend_at(btc_trends, candle_ts)
                        btc_trend = btc_info['trend']
                        btc_pct = btc_info['pct']
                        # Определяем mode с учётом мин. силы
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
                            continue  # LONG запрещён
                    
                    # Slippage: LONG покупаем чуть выше (хуже для нас)
                    entry_price = current_price * (1 + slippage_pct/100)
                    position = {
                        'side': 'LONG', 
                        'entry': entry_price, 
                        'sl': entry_price * (1 - sl_pct/100), 
                        'tp': entry_price * (1 + tp_pct/100),
                        'trailing_active': False,
                        'best_price': current_price,
                        'min_profit_sl': entry_price * (1 + min_trail_profit_pct/100),
                        'open_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                        'candle_idx': i
                    }
            else:
                # === TRAILING STOP ЛОГИКА ===
                if trailing_enabled:
                    if position['side'] == 'SHORT':
                        # Для SHORT: прибыль когда цена НИЖЕ entry
                        current_profit_pct = (position['entry'] - low_price) / position['entry'] * 100
                        
                        # Обновляем лучшую цену (минимум для SHORT)
                        if low_price < position['best_price']:
                            position['best_price'] = low_price
                            # Обновляем гарантированный SL на уровне min_profit от лучшей цены
                            position['min_profit_sl'] = position['best_price'] * (1 + trailing_distance_pct/100)
                        
                        # Активируем трейлинг при достижении прибыли >= activation
                        if current_profit_pct >= trailing_activation_pct and not position['trailing_active']:
                            position['trailing_active'] = True
                            # SL = лучшая цена + distance, но НИЖЕ entry (гарантия профита)
                            new_sl = position['best_price'] * (1 + trailing_distance_pct/100)
                            if new_sl < position['entry']:  # Только если это профит!
                                position['sl'] = new_sl
                        
                        # Если трейлинг активен - подтягиваем SL
                        if position['trailing_active']:
                            new_sl = position['best_price'] * (1 + trailing_distance_pct/100)
                            # SL должен быть НИЖЕ entry И НИЖЕ текущего SL
                            if new_sl < position['entry'] and new_sl < position['sl']:
                                position['sl'] = new_sl
                    
                    else:  # LONG
                        # Для LONG: прибыль когда цена ВЫШЕ entry
                        current_profit_pct = (high_price - position['entry']) / position['entry'] * 100
                        
                        # Обновляем лучшую цену (максимум для LONG)
                        if high_price > position['best_price']:
                            position['best_price'] = high_price
                            position['min_profit_sl'] = position['best_price'] * (1 - trailing_distance_pct/100)
                        
                        # Активируем трейлинг
                        if current_profit_pct >= trailing_activation_pct and not position['trailing_active']:
                            position['trailing_active'] = True
                            new_sl = position['best_price'] * (1 - trailing_distance_pct/100)
                            if new_sl > position['entry']:  # Только если это профит!
                                position['sl'] = new_sl
                        
                        # Если трейлинг активен - подтягиваем SL
                        if position['trailing_active']:
                            new_sl = position['best_price'] * (1 - trailing_distance_pct/100)
                            # SL должен быть ВЫШЕ entry И ВЫШЕ текущего SL
                            if new_sl > position['entry'] and new_sl > position['sl']:
                                position['sl'] = new_sl
                
                # === АВТОЗАКРЫТИЕ ПРИ ОСЛАБЛЕНИИ BTC ТРЕНДА (v6.2) ===
                # ВАЖНО: закрываем ТОЛЬКО убыточные позиции!
                # Прибыльные пусть идут к TP/trailing — не убивать winners!
                if btc_close_modes and btc_trends and position:
                    candle_ts = candles[i].get('timestamp', candles[i].get('time', 0))
                    btc_info = self._get_btc_trend_at(btc_trends, candle_ts)
                    btc_change = btc_info.get('change_24h', 0.0)
                    
                    should_close_trend = False
                    close_reason_trend = ''
                    
                    # Сначала проверяем: позиция в прибыли?
                    if position['side'] == 'SHORT':
                        current_pnl_pct = (position['entry'] - current_price) / position['entry'] * 100
                    else:
                        current_pnl_pct = (current_price - position['entry']) / position['entry'] * 100
                    
                    position_is_losing = current_pnl_pct < 0.1  # Убыток или ~безубыток
                    
                    if position_is_losing:
                        if position['side'] == 'LONG' and btc_close_modes.get('close_long_on_weak_bull'):
                            thr = btc_close_modes.get('close_long_weak_bull_threshold', 0.5)
                            if btc_change < thr:
                                should_close_trend = True
                                close_reason_trend = 'BTC_TREND_WEAK'
                        
                        elif position['side'] == 'SHORT' and btc_close_modes.get('close_short_on_weak_bear'):
                            thr = btc_close_modes.get('close_short_weak_bear_threshold', 0.5)
                            if btc_change > -thr:
                                should_close_trend = True
                                close_reason_trend = 'BTC_TREND_WEAK'
                    
                    if should_close_trend:
                        # Закрываем по текущей цене + slippage
                        if position['side'] == 'SHORT':
                            exit_price = current_price * (1 + slippage_pct/100)
                            actual_pnl_pct = (position['entry'] - exit_price) / position['entry'] * 100
                        else:
                            exit_price = current_price * (1 - slippage_pct/100)
                            actual_pnl_pct = (exit_price - position['entry']) / position['entry'] * 100
                        
                        pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                        comm = position_size * leverage * commission_pct / 100
                        pnl_usd -= comm
                        total_commission += comm
                        
                        if pnl_usd > 0:
                            gross_profit += pnl_usd
                            wins += 1
                        else:
                            gross_loss += abs(pnl_usd)
                            losses += 1
                        trades += 1
                        
                        if return_details:
                            trade_details.append({
                                'open_time': position.get('open_time', 0),
                                'close_time': candles[i].get('timestamp', candles[i].get('time', 0)),
                                'side': position['side'],
                                'pnl': pnl_usd,
                                'is_win': pnl_usd > 0,
                                'close_reason': close_reason_trend
                            })
                        position = None
                        continue  # Позиция закрыта, к следующей свече
                
                # === ПРОВЕРКА ВЫХОДА ===
                if position['side'] == 'SHORT':
                    # SL hit (цена пошла вверх)
                    if high_price >= position['sl']:
                        # Рассчитываем реальный PnL от SL
                        actual_pnl_pct = (position['entry'] - position['sl']) / position['entry'] * 100
                        pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                        # Комиссия: вычитаем из каждой сделки
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
                                'side': 'SHORT',
                                'pnl': pnl_usd,
                                'is_win': pnl_usd > 0,
                                'close_reason': 'TRAILING_STOP' if position['trailing_active'] else 'STOP_LOSS'
                            })
                        # Кулдаун после убыточного SL
                        if pnl_usd <= 0 and not position['trailing_active']:
                            cooldown_until_idx = i + symbol_cooldown_candles
                            daily_sl_count += 1
                        position = None
                    # TP hit
                    elif low_price <= position['tp']:
                        pnl_usd = (tp_pct / 100) * position_size * leverage
                        # Комиссия
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
                                'side': 'SHORT',
                                'pnl': pnl_usd,
                                'is_win': True,
                                'close_reason': 'TAKE_PROFIT'
                            })
                        position = None
                        
                else:  # LONG
                    # SL hit (цена пошла вниз)
                    if low_price <= position['sl']:
                        # Рассчитываем реальный PnL от SL
                        actual_pnl_pct = (position['sl'] - position['entry']) / position['entry'] * 100
                        pnl_usd = (actual_pnl_pct / 100) * position_size * leverage
                        # Комиссия
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
                                'side': 'LONG',
                                'pnl': pnl_usd,
                                'is_win': pnl_usd > 0,
                                'close_reason': 'TRAILING_STOP' if position['trailing_active'] else 'STOP_LOSS'
                            })
                        # Кулдаун после убыточного SL
                        if pnl_usd <= 0 and not position['trailing_active']:
                            cooldown_until_idx = i + symbol_cooldown_candles
                            daily_sl_count += 1
                        position = None
                    # TP hit
                    elif high_price >= position['tp']:
                        pnl_usd = (tp_pct / 100) * position_size * leverage
                        # Комиссия
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
                                'side': 'LONG',
                                'pnl': pnl_usd,
                                'is_win': True,
                                'close_reason': 'TAKE_PROFIT'
                            })
                        position = None
        
        net_pnl = gross_profit - gross_loss
        
        result = {
            'trades': trades,
            'wins': wins,
            'losses': losses,
            'pnl': net_pnl,
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
            'win_rate': wins/trades*100 if trades > 0 else 0,
            'trailing_wins': trailing_wins,
            'total_commission': total_commission
        }
        
        if return_details:
            result['trade_details'] = trade_details
        
        return result
    
    def _apply_position_limit(self, all_trades: List[Dict], max_positions: int) -> Dict:
        """
        Применить лимит одновременных позиций к списку сделок.
        
        Аналогия: автостоянка на max_positions мест.
        Машина заезжает (open_time) → если место есть — паркуется.
        Машина уезжает (close_time) → место освобождается.
        Если мест нет — машина проезжает мимо (skipped).
        """
        empty_result = {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0,
                'gross_profit': 0, 'gross_loss': 0, 'trailing_wins': 0,
                'total_commission': 0, 'skipped_signals': 0, 'by_symbol': {},
                'accepted_trades': []}
        
        if not all_trades:
            return empty_result
        
        # Проверяем наличие close_time
        has_close = sum(1 for t in all_trades if t.get('close_time', 0) > 0)
        has_open = sum(1 for t in all_trades if t.get('open_time', 0) > 0)
        logger.info(f"[POSITION LIMIT] Всего сделок: {len(all_trades)}, "
                     f"с open_time: {has_open}, с close_time: {has_close}, "
                     f"лимит: {max_positions}")
        
        if has_close == 0:
            logger.error("[POSITION LIMIT] ❌ НИ ОДНА сделка не имеет close_time! "
                         "Лимит позиций невозможен. Возвращаем все сделки без фильтра.")
            # Fallback: возвращаем ВСЕ сделки без фильтра
            total_trades = len(all_trades)
            wins = sum(1 for t in all_trades if t.get('is_win', False))
            losses = total_trades - wins
            gross_profit = sum(t['pnl'] for t in all_trades if t.get('pnl', 0) > 0)
            gross_loss = sum(abs(t['pnl']) for t in all_trades if t.get('pnl', 0) <= 0)
            total_pnl = gross_profit - gross_loss
            trailing_wins = sum(1 for t in all_trades if t.get('close_reason') == 'TRAILING_STOP' and t.get('is_win'))
            by_symbol = {}
            for t in all_trades:
                sym = t.get('symbol', 'UNKNOWN')
                if sym not in by_symbol:
                    by_symbol[sym] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0,
                                      'gross_profit': 0.0, 'gross_loss': 0.0,
                                      'trailing_wins': 0, 'total_commission': 0.0, 'win_rate': 0.0}
                by_symbol[sym]['trades'] += 1
                by_symbol[sym]['pnl'] += t.get('pnl', 0)
                if t.get('is_win'):
                    by_symbol[sym]['wins'] += 1
                    by_symbol[sym]['gross_profit'] += t.get('pnl', 0)
                else:
                    by_symbol[sym]['losses'] += 1
                    by_symbol[sym]['gross_loss'] += abs(t.get('pnl', 0))
            for sym in by_symbol:
                s = by_symbol[sym]
                s['win_rate'] = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
            return {
                'total_trades': total_trades, 'wins': wins, 'losses': losses,
                'total_pnl': total_pnl, 'gross_profit': gross_profit,
                'gross_loss': gross_loss, 'trailing_wins': trailing_wins,
                'total_commission': 0.0, 'skipped_signals': 0,
                'by_symbol': by_symbol, 'accepted_trades': all_trades
            }
        
        # Сортируем по времени открытия
        sorted_trades = sorted(all_trades, key=lambda t: t.get('open_time', 0))
        
        accepted = []
        skipped = 0
        skipped_no_time = 0
        # Активные позиции: [(close_time, symbol)]
        active_slots = []
        max_concurrent = 0  # Для статистики
        
        for trade in sorted_trades:
            open_time = trade.get('open_time', 0)
            close_time = trade.get('close_time', 0)
            symbol = trade.get('symbol', '')
            
            # Сделки без timestamps — принимаем (лучше лишнее чем потерять)
            if open_time <= 0 or close_time <= 0:
                accepted.append(trade)
                skipped_no_time += 1
                continue
            
            # Освобождаем слоты: убираем позиции, закрывшиеся ДО или В МОМЕНТ open_time
            active_slots = [(ct, sym) for ct, sym in active_slots if ct > open_time]
            
            # Не открываем 2 позиции на одном символе
            active_syms = {sym for _, sym in active_slots}
            if symbol in active_syms:
                skipped += 1
                continue
            
            # Есть свободный слот?
            if len(active_slots) < max_positions:
                accepted.append(trade)
                active_slots.append((close_time, symbol))
                # Трекаем максимум одновременных позиций
                if len(active_slots) > max_concurrent:
                    max_concurrent = len(active_slots)
            else:
                skipped += 1
        
        logger.info(f"[POSITION LIMIT] ✅ Принято: {len(accepted)}, пропущено: {skipped}, "
                     f"без timestamp: {skipped_no_time}, макс одновременных: {max_concurrent}")
        
        # Пересчитываем статистику из принятых сделок
        total_trades = len(accepted)
        wins = sum(1 for t in accepted if t.get('is_win', False))
        losses = total_trades - wins
        gross_profit = sum(t['pnl'] for t in accepted if t.get('pnl', 0) > 0)
        gross_loss = sum(abs(t['pnl']) for t in accepted if t.get('pnl', 0) <= 0)
        total_pnl = gross_profit - gross_loss
        trailing_wins = sum(1 for t in accepted if t.get('close_reason') == 'TRAILING_STOP' and t.get('is_win'))
        total_commission = 0.0  # Будет пересчитана в run_backtest_multi
        
        # Группируем по символам
        by_symbol = {}
        for t in accepted:
            sym = t.get('symbol', 'UNKNOWN')
            if sym not in by_symbol:
                by_symbol[sym] = {
                    'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0,
                    'gross_profit': 0.0, 'gross_loss': 0.0,
                    'trailing_wins': 0, 'total_commission': 0.0, 'win_rate': 0.0
                }
            by_symbol[sym]['trades'] += 1
            by_symbol[sym]['pnl'] += t.get('pnl', 0)
            if t.get('is_win'):
                by_symbol[sym]['wins'] += 1
                by_symbol[sym]['gross_profit'] += t.get('pnl', 0)
                if t.get('close_reason') == 'TRAILING_STOP':
                    by_symbol[sym]['trailing_wins'] += 1
            else:
                by_symbol[sym]['losses'] += 1
                by_symbol[sym]['gross_loss'] += abs(t.get('pnl', 0))
        
        for sym in by_symbol:
            s = by_symbol[sym]
            s['win_rate'] = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
        
        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'total_pnl': total_pnl,
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
            'trailing_wins': trailing_wins,
            'total_commission': total_commission,
            'skipped_signals': skipped,
            'by_symbol': by_symbol,
            'accepted_trades': accepted
        }
    
    # _backtest_multi_realistic удалён — заменён на _apply_position_limit

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> List[float]:
        """Рассчитать RSI"""
        if len(prices) < period + 1:
            return [50] * len(prices)
        
        rsi_values = [50] * period
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        
        for i in range(period, len(prices)):
            avg_gain = sum(gains[i-period:i]) / period
            avg_loss = sum(losses[i-period:i]) / period
            
            if avg_loss == 0:
                rsi = 100
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            
            rsi_values.append(rsi)
        
        return rsi_values
    
    # =========================================================================
    # AUTO-OPTIMIZE - Автоматическое улучшение
    # =========================================================================
    
    def optimize_strategy(self, days: int = 30, use_backtest: bool = True) -> Dict:
        """
        Автоматически улучшить стратегию на основе анализа
        
        Args:
            days: Период анализа
            use_backtest: Использовать бэктест для оптимизации
        """
        try:
            recommendations = []
            changes_made = []
            
            # Получаем текущие параметры
            current = self.get_current_strategy()
            params = current.get('parameters', {})
            current_sl = params.get('stop_loss_pct', 5.0)
            current_tp = params.get('take_profit_pct', 7.0)
            current_rsi_short = params.get('rsi_overbought', 70)
            current_rsi_long = params.get('rsi_oversold', 30)
            
            # Анализируем реальную историю
            history = self.get_trade_history(days)
            stats = history.get('stats', {})
            trades = history.get('trades', [])
            
            real_wr = stats.get('win_rate', 0)
            real_pnl = stats.get('total_pnl', 0)
            
            lines = ["🚀 **АВТОМАТИЧЕСКАЯ ОПТИМИЗАЦИЯ**\n"]
            lines.append(f"📊 Реальная история: {len(trades)} сделок за {days} дней")
            lines.append(f"   WR: {real_wr:.1f}%, PnL: ${real_pnl:.2f}\n")
            
            # Анализ реальных сделок
            if trades:
                # Анализ SL/TP
                sl_tp_analysis = self._analyze_sl_tp(trades, current_sl, current_tp)
                if sl_tp_analysis.get('recommendation'):
                    recommendations.append(sl_tp_analysis['recommendation'])
                    if sl_tp_analysis.get('new_sl') and sl_tp_analysis['new_sl'] != current_sl:
                        self.set_strategy_param('stop_loss_pct', sl_tp_analysis['new_sl'])
                        changes_made.append(f"SL: {current_sl}% → {sl_tp_analysis['new_sl']}%")
                    if sl_tp_analysis.get('new_tp') and sl_tp_analysis['new_tp'] != current_tp:
                        self.set_strategy_param('take_profit_pct', sl_tp_analysis['new_tp'])
                        changes_made.append(f"TP: {current_tp}% → {sl_tp_analysis['new_tp']}%")
                
                # Проблемные монеты
                coin_analysis = self._analyze_coins(trades)
                if coin_analysis.get('blacklist'):
                    for coin in coin_analysis['blacklist'][:3]:
                        self.add_to_blacklist(coin['symbol'], f"Low WR: {coin['win_rate']:.0f}%")
                        changes_made.append(f"Blacklist: {coin['symbol']}")
                        recommendations.append(f"{coin['symbol']}: WR={coin['win_rate']:.0f}%, убыток ${coin['pnl']:.2f}")
            
            # Бэктест для проверки изменений
            if use_backtest and self.exchange:
                lines.append("📈 Запускаю бэктест на топ-10 монетах...")
                backtest = self.run_backtest_multi('current', days=min(days, 14), use_top=10)
                
                if backtest.get('total_trades', 0) > 0:
                    bt_wr = backtest.get('win_rate', 0)
                    bt_pnl = backtest.get('total_pnl', 0)
                    bt_pf = backtest.get('profit_factor', 0)
                    
                    lines.append(f"\n📈 Бэктест ({backtest.get('total_trades', 0)} сделок):")
                    lines.append(f"   WR: {bt_wr:.1f}%, PnL: ${bt_pnl:.2f}, PF: {bt_pf:.2f}")
                    
                    # Рекомендации на основе бэктеста
                    if bt_pf < 1.0:
                        recommendations.append(f"⚠️ PF={bt_pf:.2f} < 1.0 — стратегия убыточная!")
                        if current_tp <= current_sl:
                            new_tp = current_sl * 1.5
                            self.set_strategy_param('take_profit_pct', new_tp)
                            changes_made.append(f"TP: {current_tp}% → {new_tp}% (TP должен быть > SL)")
                    
                    if bt_wr < 40:
                        recommendations.append(f"⚠️ WR={bt_wr:.1f}% слишком низкий")
                        # Ужесточаем RSI
                        if current_rsi_short < 75:
                            self.set_strategy_param('rsi_overbought', 75)
                            changes_made.append(f"RSI SHORT: {current_rsi_short} → 75")
                        if current_rsi_long > 25:
                            self.set_strategy_param('rsi_oversold', 25)
                            changes_made.append(f"RSI LONG: {current_rsi_long} → 25")
            
            # Итог
            if recommendations:
                lines.append("\n**Найденные проблемы:**")
                for rec in recommendations:
                    lines.append(f"  • {rec}")
            
            if changes_made:
                lines.append("\n**Применённые изменения:**")
                for change in changes_made:
                    lines.append(f"  ✅ {change}")
            elif recommendations:
                lines.append("\n⚠️ Обнаружены проблемы, но автоматические изменения не применены")
            else:
                lines.append("\n✅ Стратегия оптимальна, изменений не требуется")
            
            return {
                'success': True,
                'recommendations': recommendations,
                'changes_made': changes_made,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] optimize_strategy error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def grid_optimize_strategy(self, days: int = 30, top_n: int = 20) -> Dict:
        """
        ADAPTIVE оптимизация (Coarse-to-Fine):
        
        Фаза 1 (Грубая): Широкая сетка с большим шагом → находим "горячие зоны"
        Фаза 2 (Тонкая): В лучших зонах ищем с мелким шагом → точный оптимум
        """
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            lines = ["🧬 **ADAPTIVE ОПТИМИЗАЦИЯ**\n"]
            lines.append(f"Период: {days} дней, Монет: {top_n}")
            
            # Получаем монеты
            symbols = []
            top_coins = self.get_top_liquid_coins(top_n)
            
            if 'error' not in top_coins:
                coins_data = top_coins.get('coins', [])[:top_n]
                symbols = [c['symbol'] for c in coins_data if 'symbol' in c]
            
            if not symbols:
                symbols = [
                    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK', 
                    'DOT', 'MATIC', 'UNI', 'ATOM', 'LTC', 'BCH', 'NEAR', 'APT',
                    'ARB', 'OP', 'SUI', 'SEI', 'INJ', 'TIA', 'PEPE', 'WIF'
                ][:top_n]
                lines.append(f"⚠️ Используем дефолтные монеты")
            
            if not symbols:
                return {'error': 'No symbols', 'summary': '❌ Нет монет для тестирования'}
            
            lines.append(f"Монеты: {len(symbols)} шт\n")
            
            # Сохраняем ТОЛЬКО торговые параметры (не API ключи!)
            current_settings = self.trader.get_settings()
            original_trading_params = {
                'stop_loss_pct': current_settings.get('stop_loss_pct', 5.0),
                'take_profit_pct': current_settings.get('take_profit_pct', 7.0),
                'trailing_activation_pct': current_settings.get('trailing_activation_pct', 3.0),
                'trailing_distance_pct': current_settings.get('trailing_distance_pct', 1.5),
                'trailing_enabled': current_settings.get('trailing_enabled', True),
            }
            
            # ============ ФАЗА 1: ГРУБЫЙ ПРОХОД ============
            lines.append("📊 **ФАЗА 1: Грубый проход**")
            
            # Широкая сетка с большим шагом
            coarse_sl = [2.0, 3.5, 5.0]
            coarse_tp = [4.0, 6.0, 8.0]
            coarse_trail_act = [1.5, 3.0, 4.5]
            coarse_trail_dist = [0.5, 1.25, 2.0]
            
            coarse_total = len(coarse_sl) * len(coarse_tp) * len(coarse_trail_act) * len(coarse_trail_dist)
            lines.append(f"Комбинаций: {coarse_total}")
            
            coarse_results = []
            tested = 0
            
            for sl in coarse_sl:
                for tp in coarse_tp:
                    if tp <= sl:
                        continue
                    for trail_act in coarse_trail_act:
                        for trail_dist in coarse_trail_dist:
                            if trail_dist >= trail_act:
                                continue
                            
                            tested += 1
                            
                            # Передаём параметры напрямую - НЕ меняем настройки trader!
                            test_params = {
                                'stop_loss_pct': sl,
                                'take_profit_pct': tp,
                                'trailing_activation_pct': trail_act,
                                'trailing_distance_pct': trail_dist,
                                'trailing_enabled': True
                            }
                            
                            try:
                                bt_result = self.run_backtest_multi(
                                    symbols=symbols,
                                    days=days,
                                    custom_params=test_params
                                )
                                
                                if 'error' not in bt_result:
                                    pf = bt_result.get('profit_factor', 0)
                                    pnl = bt_result.get('total_pnl', 0)
                                    wr = bt_result.get('win_rate', 0)
                                    trades = bt_result.get('total_trades', 0)
                                    
                                    if trades >= 10:
                                        coarse_results.append({
                                            'sl': sl, 'tp': tp,
                                            'trail_act': trail_act, 'trail_dist': trail_dist,
                                            'pf': pf, 'pnl': pnl, 'wr': wr, 'trades': trades
                                        })
                            except Exception as e:
                                logger.warning(f"[ADAPTIVE] Coarse error: {e}")
                                continue
            
            lines.append(f"Протестировано: {tested}, результатов: {len(coarse_results)}")
            
            if not coarse_results:
                lines.append("\n❌ Нет результатов в грубом проходе")
                return {'error': 'No coarse results', 'summary': '\n'.join(lines)}
            
            # Сортируем и берём топ-3 "горячие зоны"
            coarse_results.sort(key=lambda x: x['pf'], reverse=True)
            hot_zones = coarse_results[:3]
            
            lines.append("\n🔥 **Горячие зоны:**")
            for i, zone in enumerate(hot_zones, 1):
                lines.append(f"  {i}. SL={zone['sl']}% TP={zone['tp']}% Trail={zone['trail_act']}/{zone['trail_dist']}% → PF={zone['pf']:.3f}")
            
            # ============ ФАЗА 2: ТОНКИЙ ПРОХОД ============
            lines.append("\n🎯 **ФАЗА 2: Тонкий проход в горячих зонах**")
            
            fine_results = []
            fine_tested = 0
            
            for zone in hot_zones:
                # Создаём мелкую сетку вокруг каждой зоны (±0.5 с шагом 0.25)
                sl_center, tp_center = zone['sl'], zone['tp']
                trail_act_center, trail_dist_center = zone['trail_act'], zone['trail_dist']
                
                # Мелкая сетка trailing (главный фокус!)
                fine_trail_act = [
                    max(1.0, trail_act_center - 0.5),
                    trail_act_center - 0.25,
                    trail_act_center,
                    trail_act_center + 0.25,
                    min(6.0, trail_act_center + 0.5)
                ]
                fine_trail_dist = [
                    max(0.25, trail_dist_center - 0.25),
                    trail_dist_center,
                    min(2.5, trail_dist_center + 0.25)
                ]
                # SL/TP тоже немного варьируем
                fine_sl = [max(1.5, sl_center - 0.5), sl_center, min(6.0, sl_center + 0.5)]
                fine_tp = [max(3.0, tp_center - 1.0), tp_center, min(10.0, tp_center + 1.0)]
                
                for sl in fine_sl:
                    for tp in fine_tp:
                        if tp <= sl:
                            continue
                        for trail_act in fine_trail_act:
                            for trail_dist in fine_trail_dist:
                                if trail_dist >= trail_act:
                                    continue
                                
                                fine_tested += 1
                                
                                # Передаём параметры напрямую - НЕ меняем настройки trader!
                                test_params = {
                                    'stop_loss_pct': sl,
                                    'take_profit_pct': tp,
                                    'trailing_activation_pct': trail_act,
                                    'trailing_distance_pct': trail_dist,
                                    'trailing_enabled': True
                                }
                                
                                try:
                                    bt_result = self.run_backtest_multi(
                                        symbols=symbols,
                                        days=days,
                                        custom_params=test_params
                                    )
                                    
                                    if 'error' not in bt_result:
                                        pf = bt_result.get('profit_factor', 0)
                                        pnl = bt_result.get('total_pnl', 0)
                                        wr = bt_result.get('win_rate', 0)
                                        trades = bt_result.get('total_trades', 0)
                                        
                                        if trades >= 10:
                                            fine_results.append({
                                                'sl': round(sl, 2), 'tp': round(tp, 2),
                                                'trail_act': round(trail_act, 2), 
                                                'trail_dist': round(trail_dist, 2),
                                                'pf': pf, 'pnl': pnl, 'wr': wr, 'trades': trades
                                            })
                                except Exception as e:
                                    continue
            
            lines.append(f"Тонкий проход: {fine_tested} комбинаций, результатов: {len(fine_results)}")
            
            # ============ ИТОГОВЫЙ РЕЗУЛЬТАТ ============
            all_results = coarse_results + fine_results
            all_results.sort(key=lambda x: x['pf'], reverse=True)
            
            # Убираем дубликаты
            seen = set()
            unique_results = []
            for r in all_results:
                key = (r['sl'], r['tp'], r['trail_act'], r['trail_dist'])
                if key not in seen:
                    seen.add(key)
                    unique_results.append(r)
            
            best_params = unique_results[0] if unique_results else None
            
            if best_params:
                lines.append("\n" + "="*45)
                pf_emoji = "🏆" if best_params['pf'] >= 1.0 else "📊"
                lines.append(f"{pf_emoji} **ЛУЧШАЯ КОМБИНАЦИЯ:**")
                lines.append(f"  • SL: {best_params['sl']}%")
                lines.append(f"  • TP: {best_params['tp']}%")
                lines.append(f"  • Trail Activation: {best_params['trail_act']}%")
                lines.append(f"  • Trail Distance: {best_params['trail_dist']}%")
                lines.append(f"\n📈 **Результаты:**")
                lines.append(f"  • Profit Factor: {best_params['pf']:.3f}")
                lines.append(f"  • PnL: ${best_params['pnl']:.2f}")
                lines.append(f"  • Win Rate: {best_params['wr']:.1f}%")
                lines.append(f"  • Сделок: {best_params['trades']}")
                
                if best_params['pf'] < 1.0:
                    lines.append(f"\n⚠️ PF < 1.0 — стратегия убыточная")
                lines.append("="*45)
                
                # Топ-5
                lines.append("\n📊 **Топ-5 комбинаций:**")
                for i, r in enumerate(unique_results[:5], 1):
                    pf_mark = "✅" if r['pf'] >= 1.0 else "❌"
                    lines.append(f"  {i}. SL={r['sl']}% TP={r['tp']}% Trail={r['trail_act']}/{r['trail_dist']}% → PF={r['pf']:.3f} {pf_mark}")
                
                if best_params['pf'] >= 1.0:
                    lines.append("\n💡 Применить: [TOOL:APPLY_BEST]")
                
                self._last_grid_best = best_params
            else:
                lines.append("\n❌ Нет результатов")
            
            return {
                'success': True,
                'best_params': best_params,
                'all_results': unique_results,
                'tested_total': tested + fine_tested,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] adaptive_optimize error: {e}")
            import traceback
            traceback.print_exc()
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
        
        finally:
            # ГАРАНТИРОВАННО восстанавливаем настройки
            try:
                if original_trading_params:
                    self.trader.update_settings(original_trading_params)
                    logger.info(f"[ADAPTIVE] Settings restored: SL={original_trading_params['stop_loss_pct']}%, TP={original_trading_params['take_profit_pct']}%")
            except NameError:
                pass  # Переменная ещё не была определена
            except Exception as restore_error:
                logger.error(f"[ADAPTIVE] Failed to restore settings: {restore_error}")
    
    def optimize_btc_levels(self, days: int = 30, top_n: int = 50, 
                            fast_mode: bool = True) -> Dict:
        """
        Оптимизация уровней открытия/закрытия по BTC тренду.
        
        Перебирает комбинации 4 параметров:
        - bull_open_min: мин. BTC% для открытия при бычьем
        - bear_open_min: мин. BTC% для открытия при медвежьем
        - bull_close_thr: закрытие LONG если бычий < X%
        - bear_close_thr: закрытие SHORT если медвежий > -X%
        
        Двухфазный подход:
        Фаза 1 (грубая): широкая сетка → находим лучшую зону
        Фаза 2 (тонкая): мелкий шаг вокруг лучшей зоны → точный оптимум
        """
        import time as _time
        
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            start_time = _time.time()
            lines = ["🎯 **ОПТИМИЗАЦИЯ BTC УРОВНЕЙ**\n"]
            lines.append(f"Период: {days} дней, Монет: {top_n}")
            
            # === ЗАГРУЗКА ДАННЫХ (один раз) ===
            settings = self.trader.get_settings() if self.trader else {}
            params = self.get_current_strategy().get('parameters', {})
            
            rsi_short = params.get('rsi_overbought', 70)
            rsi_long = params.get('rsi_oversold', 30)
            sl_pct = params.get('stop_loss_pct', 5.0)
            tp_pct = params.get('take_profit_pct', 7.0)
            position_size = params.get('position_size', 500)
            leverage = params.get('leverage', 5)
            trailing_enabled = params.get('trailing_enabled', True)
            trailing_activation = params.get('trailing_activation_pct', 2.0)
            trailing_distance = params.get('trailing_distance_pct', 1.0)
            commission_pct = params.get('commission_pct', 0.08)
            slippage_pct = params.get('slippage_pct', 0.05)
            max_positions = params.get('max_positions', 10)
            symbol_cooldown_candles = params.get('symbol_cooldown_candles', 2)
            max_symbol_losses_daily = params.get('max_symbol_losses_daily', 2)
            min_change_pct = params.get('min_change_filter', 0.0)
            
            # Текущие режимы BTC (не меняем, оптимизируем только уровни)
            # ЧИТАЕМ ИЗ SETTINGS (единый источник правды!)
            base_btc_modes = {
                'bullish': settings.get('btc_bullish_mode', 'long_only'),
                'bearish': settings.get('btc_bearish_mode', 'short_only'),
                'neutral': settings.get('btc_neutral_mode', 'any'),
            }
            
            lines.append(f"Режимы: Bull={base_btc_modes['bullish']}, Bear={base_btc_modes['bearish']}, Neut={base_btc_modes['neutral']}")
            
            # Загружаем монеты
            symbols = []
            top_coins = self.get_top_liquid_coins(top_n)
            symbols = [c['symbol'] for c in top_coins.get('coins', [])]
            
            if not symbols:
                return {'error': 'No symbols', 'summary': '❌ Не удалось загрузить монеты'}
            
            # Загружаем свечи для всех монет
            loaded_candles = {}
            for symbol in symbols:
                try:
                    clean_symbol, full_symbol = self._normalize_symbol(symbol)
                    if self.db and self.db.is_blacklisted(full_symbol):
                        continue
                    candles_data = self.load_candles_cached(symbol, '15m', days)
                    candles = candles_data.get('candles', [])
                    if len(candles) >= 50:
                        loaded_candles[clean_symbol] = candles
                except Exception:
                    continue
            
            lines.append(f"Загружено монет: {len(loaded_candles)}")
            
            # Загружаем BTC тренды
            btc_data = self.load_candles_cached('BTC', '15m', days)
            btc_candles = btc_data.get('candles', [])
            if len(btc_candles) < 60:
                return {'error': 'Not enough BTC data', 'summary': '❌ Мало BTC данных'}
            
            btc_trends = self._calc_btc_trend_array(btc_candles)
            lines.append(f"BTC точек: {len(btc_trends)}")
            
            # === ФУНКЦИЯ БЭКТЕСТА для одной комбинации параметров ===
            def run_combo(bull_open, bear_open, bull_close, bear_close):
                """Запускает бэктест с заданными BTC уровнями, возвращает PnL и статистику"""
                combo_btc_modes = dict(base_btc_modes)
                combo_btc_modes['bullish_min_str'] = bull_open
                combo_btc_modes['bearish_min_str'] = bear_open
                
                combo_close = {
                    'close_long_on_weak_bull': bull_close > 0,
                    'close_long_weak_bull_threshold': bull_close,
                    'close_short_on_weak_bear': bear_close > 0,
                    'close_short_weak_bear_threshold': bear_close,
                }
                
                total_pnl = 0
                total_trades = 0
                total_wins = 0
                total_losses = 0
                all_details = []
                
                for clean_symbol, candles in loaded_candles.items():
                    try:
                        r = self._backtest_symbol(
                            candles, rsi_short, rsi_long, sl_pct, tp_pct,
                            position_size, leverage,
                            trailing_enabled, trailing_activation, trailing_distance,
                            return_details=(max_positions > 0),
                            commission_pct=commission_pct,
                            slippage_pct=slippage_pct,
                            symbol_cooldown_candles=symbol_cooldown_candles,
                            max_symbol_losses_daily=max_symbol_losses_daily,
                            btc_trends=btc_trends,
                            btc_modes=combo_btc_modes,
                            btc_close_modes=combo_close,
                            min_change_pct=min_change_pct,
                            recheck_change_at_open=recheck_change_at_open
                        )
                        total_pnl += r['pnl']
                        total_trades += r['trades']
                        total_wins += r['wins']
                        total_losses += r['losses']
                        
                        if max_positions > 0 and 'trade_details' in r:
                            for td in r['trade_details']:
                                td['symbol'] = clean_symbol
                                all_details.append(td)
                    except Exception:
                        continue
                
                # Применяем лимит позиций
                if max_positions > 0 and all_details:
                    filtered = self._apply_position_limit(all_details, max_positions)
                    return {
                        'pnl': filtered['total_pnl'],
                        'trades': filtered['total_trades'],
                        'wins': filtered['wins'],
                        'losses': filtered['losses'],
                        'wr': filtered['wins'] / filtered['total_trades'] * 100 if filtered['total_trades'] > 0 else 0
                    }
                
                return {
                    'pnl': total_pnl,
                    'trades': total_trades,
                    'wins': total_wins,
                    'losses': total_losses,
                    'wr': total_wins / total_trades * 100 if total_trades > 0 else 0
                }
            
            # === ФАЗА 1: ГРУБАЯ СЕТКА ===
            if fast_mode:
                open_grid = [0.0, 0.5, 1.0, 2.0, 3.0]
                close_grid = [0.0, 0.3, 0.5, 1.0, 1.5]
            else:
                open_grid = [0.0, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0]
                close_grid = [0.0, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]
            
            total_combos = len(open_grid) ** 2 * len(close_grid) ** 2
            lines.append(f"\n⚙️ **Фаза 1 (грубая):** {total_combos} комбинаций...")
            logger.info(f"[BTC_OPT] Phase 1: {total_combos} combos, {len(loaded_candles)} symbols")
            
            best_pnl = -999999
            best_combo = None
            best_stats = None
            all_results = []
            combo_count = 0
            
            # Базовый результат (текущие настройки)
            current_bull_open = float(settings.get('btc_bullish_min_strength', 0.5))
            current_bear_open = float(settings.get('btc_bearish_min_strength', 0.5))
            current_bull_close = float(settings.get('close_long_weak_bull_threshold', 0.5))
            current_bear_close = float(settings.get('close_short_weak_bear_threshold', 0.5))
            
            baseline = run_combo(current_bull_open, current_bear_open, 0, 0)  # Без автозакрытия
            baseline_with_close = run_combo(current_bull_open, current_bear_open, current_bull_close, current_bear_close)
            
            lines.append(f"\n📊 **Базовые результаты (текущие настройки):**")
            lines.append(f"  Без автозакрытия: PnL=${baseline['pnl']:+.2f}, WR={baseline['wr']:.1f}%, {baseline['trades']} сделок")
            lines.append(f"  С автозакрытием: PnL=${baseline_with_close['pnl']:+.2f}, WR={baseline_with_close['wr']:.1f}%, {baseline_with_close['trades']} сделок")
            lines.append(f"  Откр: бык≥{current_bull_open}%, медв≥{current_bear_open}%")
            lines.append(f"  Закр: LONG<{current_bull_close}%, SHORT>-{current_bear_close}%")
            
            for bo in open_grid:
                for beo in open_grid:
                    for bc in close_grid:
                        for bec in close_grid:
                            combo_count += 1
                            if combo_count % 50 == 0:
                                logger.info(f"[BTC_OPT] Phase 1: {combo_count}/{total_combos}")
                            
                            result = run_combo(bo, beo, bc, bec)
                            all_results.append({
                                'bull_open': bo, 'bear_open': beo,
                                'bull_close': bc, 'bear_close': bec,
                                **result
                            })
                            
                            if result['pnl'] > best_pnl and result['trades'] >= 10:
                                best_pnl = result['pnl']
                                best_combo = (bo, beo, bc, bec)
                                best_stats = result
            
            if not best_combo:
                return {'error': 'No valid combos', 'summary': '❌ Нет валидных комбинаций'}
            
            # === ФАЗА 2: ТОНКАЯ СЕТКА вокруг лучшего ===
            bo_best, beo_best, bc_best, bec_best = best_combo
            
            fine_open = [max(0, bo_best + d) for d in [-0.3, -0.15, 0, 0.15, 0.3]]
            fine_close = [max(0, bc_best + d) for d in [-0.2, -0.1, 0, 0.1, 0.2]]
            fine_open_bear = [max(0, beo_best + d) for d in [-0.3, -0.15, 0, 0.15, 0.3]]
            fine_close_bear = [max(0, bec_best + d) for d in [-0.2, -0.1, 0, 0.1, 0.2]]
            
            fine_combos = len(fine_open) * len(fine_open_bear) * len(fine_close) * len(fine_close_bear)
            lines.append(f"\n⚙️ **Фаза 2 (тонкая):** {fine_combos} комбинаций вокруг лучшей...")
            logger.info(f"[BTC_OPT] Phase 2: {fine_combos} combos around {best_combo}")
            
            for bo in fine_open:
                for beo in fine_open_bear:
                    for bc in fine_close:
                        for bec in fine_close_bear:
                            result = run_combo(bo, beo, bc, bec)
                            if result['pnl'] > best_pnl and result['trades'] >= 10:
                                best_pnl = result['pnl']
                                best_combo = (bo, beo, bc, bec)
                                best_stats = result
            
            elapsed = _time.time() - start_time
            bo_best, beo_best, bc_best, bec_best = best_combo
            
            # === РЕЗУЛЬТАТЫ ===
            improvement = best_pnl - baseline['pnl']
            improvement_pct = (improvement / abs(baseline['pnl']) * 100) if baseline['pnl'] != 0 else 0
            
            lines.append(f"\n{'='*40}")
            lines.append(f"🏆 **ОПТИМАЛЬНЫЕ BTC УРОВНИ:**\n")
            lines.append(f"📈 Открытие:")
            lines.append(f"  Бычий ≥ **{bo_best:.1f}%**  (было {current_bull_open}%)")
            lines.append(f"  Медвежий ≥ **{beo_best:.1f}%**  (было {current_bear_open}%)")
            lines.append(f"📉 Закрытие:")
            lines.append(f"  LONG если бычий < **+{bc_best:.1f}%**  (было {current_bull_close}%)")
            lines.append(f"  SHORT если медв > **-{bec_best:.1f}%**  (было {current_bear_close}%)")
            lines.append(f"\n📊 **Статистика:**")
            lines.append(f"  PnL: **${best_pnl:+.2f}**  (было ${baseline['pnl']:+.2f}, {'📈' if improvement > 0 else '📉'}{improvement:+.2f})")
            lines.append(f"  WR: {best_stats['wr']:.1f}%  (было {baseline['wr']:.1f}%)")
            lines.append(f"  Сделок: {best_stats['trades']}  (было {baseline['trades']})")
            lines.append(f"  ⏱️ Время: {elapsed:.0f} сек ({total_combos + fine_combos} комбинаций)")
            
            # Топ-5 лучших комбинаций
            top5 = sorted(all_results, key=lambda x: x['pnl'], reverse=True)[:5]
            lines.append(f"\n**Топ-5 комбинаций:**")
            for i, t in enumerate(top5, 1):
                lines.append(f"  {i}. Откр:{t['bull_open']}/{t['bear_open']}% Закр:{t['bull_close']}/{t['bear_close']}% → ${t['pnl']:+.2f} WR:{t['wr']:.0f}% ({t['trades']})")
            
            return {
                'success': True,
                'best_combo': {
                    'bull_open_min': bo_best,
                    'bear_open_min': beo_best,
                    'bull_close_threshold': bc_best,
                    'bear_close_threshold': bec_best
                },
                'best_pnl': best_pnl,
                'best_stats': best_stats,
                'baseline_pnl': baseline['pnl'],
                'improvement': improvement,
                'improvement_pct': improvement_pct,
                'elapsed_sec': elapsed,
                'total_combos': total_combos + fine_combos,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[BTC_OPT] Error: {e}", exc_info=True)
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def apply_btc_levels(self, levels: Dict = None) -> Dict:
        """
        Применить оптимизированные BTC уровни к настройкам бота.
        Обновляет ВСЕ 3 места хранения: settings, filters, config.json
        """
        try:
            if not levels and self.last_backtest_results:
                levels = self.last_backtest_results.get('best_combo')
            
            if not levels:
                return {'error': 'No levels to apply', 'summary': '❌ Нет данных для применения. Сначала запусти optimize_btc_levels'}
            
            if not self.trader:
                return {'error': 'No trader', 'summary': '❌ Trader не подключен'}
            
            changes = []
            
            bull_open = levels.get('bull_open_min')
            bear_open = levels.get('bear_open_min')
            bull_close = levels.get('bull_close_threshold')
            bear_close = levels.get('bear_close_threshold')
            
            # ═══ 1. Обновляем settings (dataclass) — единый источник правды ═══
            if bull_open is not None:
                self.trader.settings.btc_bullish_min_strength = float(bull_open)
                changes.append(f"Бычий откр. ≥ {bull_open}%")
            
            if bear_open is not None:
                self.trader.settings.btc_bearish_min_strength = float(bear_open)
                changes.append(f"Медвежий откр. ≥ {bear_open}%")
            
            if bull_close is not None:
                self.trader.settings.close_long_on_weak_bull = float(bull_close) > 0
                self.trader.settings.close_long_weak_bull_threshold = float(bull_close)
                if float(bull_close) > 0:
                    changes.append(f"LONG закр. если бычий < +{bull_close}%")
                else:
                    changes.append(f"LONG автозакрытие: ВЫКЛ")
            
            if bear_close is not None:
                self.trader.settings.close_short_on_weak_bear = float(bear_close) > 0
                self.trader.settings.close_short_weak_bear_threshold = float(bear_close)
                if float(bear_close) > 0:
                    changes.append(f"SHORT закр. если медв > -{bear_close}%")
                else:
                    changes.append(f"SHORT автозакрытие: ВЫКЛ")
            
            # ═══ 2. Обновляем self.trader.filters (in-memory dict) ═══
            # БЕЗ этого бэктест/живая торговля читают старые значения!
            if not hasattr(self.trader, 'filters') or not isinstance(self.trader.filters, dict):
                self.trader.filters = {}
            
            if bull_open is not None:
                self.trader.filters['btc_bullish_min_strength'] = float(bull_open)
            if bear_open is not None:
                self.trader.filters['btc_bearish_min_strength'] = float(bear_open)
            if bull_close is not None:
                self.trader.filters['close_long_on_weak_bull'] = float(bull_close) > 0
                self.trader.filters['close_long_weak_bull_threshold'] = float(bull_close)
            if bear_close is not None:
                self.trader.filters['close_short_on_weak_bear'] = float(bear_close) > 0
                self.trader.filters['close_short_weak_bear_threshold'] = float(bear_close)
            
            # ═══ 3. Сохраняем в config.json ═══
            try:
                import json
                config_path = 'config.json'
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                if 'filters' not in config:
                    config['filters'] = {}
                
                if bull_open is not None:
                    config['filters']['btc_bullish_min_strength'] = float(bull_open)
                if bear_open is not None:
                    config['filters']['btc_bearish_min_strength'] = float(bear_open)
                if bull_close is not None:
                    config['filters']['close_long_on_weak_bull'] = float(bull_close) > 0
                    config['filters']['close_long_weak_bull_threshold'] = float(bull_close)
                if bear_close is not None:
                    config['filters']['close_short_on_weak_bear'] = float(bear_close) > 0
                    config['filters']['close_short_weak_bear_threshold'] = float(bear_close)
                
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"[BTC_OPT] Config save error: {e}")
            
            # ═══ 4. Верификация: перечитываем и проверяем ═══
            verify = self.trader.get_settings()
            lines = ["✅ **BTC уровни применены:**\n"]
            for c in changes:
                lines.append(f"  • {c}")
            lines.append(f"\n🔍 **Верификация (settings):**")
            lines.append(f"  bull_min_str={verify.get('btc_bullish_min_strength')}")
            lines.append(f"  bear_min_str={verify.get('btc_bearish_min_strength')}")
            lines.append(f"  close_long={verify.get('close_long_on_weak_bull')}, thr={verify.get('close_long_weak_bull_threshold')}")
            lines.append(f"  close_short={verify.get('close_short_on_weak_bear')}, thr={verify.get('close_short_weak_bear_threshold')}")
            
            logger.info(f"[BTC_OPT] Applied: {levels} → settings OK, filters OK, config.json OK")
            
            return {'success': True, 'changes': changes, 'summary': '\n'.join(lines)}
            
        except Exception as e:
            logger.error(f"[BTC_OPT] Apply error: {e}", exc_info=True)
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def analyze_late_entries(self, mode: str = 'reset', threshold_pct: float = 0) -> Dict:
        """
        Анализ "опоздавших" входов.
        
        "Свежий" = при открытии change_24h всё ещё >= min_change_filter
        "Опоздавший" = при сканировании прошёл фильтр, но к открытию change_24h
                       упал ниже min_change_filter (монета вернулась)
        
        Порог берётся из настроек min_change_filter (единый источник правды).
        threshold_pct: если >0, использовать вместо min_change_filter
        
        mode: 'reset' — только после последнего сброса PnL
              'all' — все сделки в памяти/БД
        """
        try:
            lines = ["🔍 **АНАЛИЗ ОПОЗДАВШИХ ВХОДОВ**\n"]
            
            # Порог = min_change_filter из настроек (или кастомный)
            settings = self.trader.get_settings() if self.trader else {}
            min_change = threshold_pct if threshold_pct > 0 else settings.get('min_change_filter', 5.0)
            
            lines.append(f"Порог входа: **{min_change}%** (min_change_filter)")
            lines.append(f"🔬 Диагностика: trader={'✅' if self.trader else '❌ None'}")
            
            # Загружаем сделки
            trades = []
            open_trades_count = 0
            if self.trader:
                try:
                    from database import db
                    # Загружаем ВСЕ сделки (и закрытые, и открытые)
                    all_trades = db.get_trades(limit=10000, only_closed=False, days=365)
                    lines.append(f"  db загрузил: {len(all_trades)} сделок")
                    
                    # Считаем открытые отдельно (для диагностики)
                    open_in_db = [t for t in all_trades if t.get('result') is None]
                    open_trades_count = len(open_in_db)
                    
                    if mode == 'reset':
                        pnl_reset_at = db.get_setting('pnl_reset_at', None)
                        if pnl_reset_at:
                            # closed_at — единственный правильный критерий:
                            # именно так считает PnL trader.py и app.py
                            reset_trades = [t for t in all_trades if (t.get('closed_at') or '') >= pnl_reset_at]
                            lines.append(f"Режим: после сброса ({pnl_reset_at})")
                            lines.append(f"  (из {len(all_trades)} всего → {len(reset_trades)} закрыто после сброса)")
                            if not reset_trades:
                                lines.append(f"⚠️ После сброса нет закрытых сделок.")
                                lines.append(f"   Используй [TOOL:ANALYZE_LATE:all] для анализа всех сделок.")
                                return {'summary': '\n'.join(lines)}
                            all_trades = reset_trades
                        else:
                            lines.append(f"Режим: все сделки (сброс не найден)")
                    else:
                        lines.append(f"Режим: все сделки")
                    
                    trades = all_trades
                    
                    # Диагностика: сколько сделок с данными change_24h_at_open
                    with_data = [t for t in all_trades if abs(t.get('change_24h_at_open', 0)) > 0]
                    with_change = [t for t in all_trades if abs(t.get('change_24h', 0)) > 0]
                    lines.append(f"📋 Диагностика: {len(all_trades)} сделок, из них {len(with_data)} с данными at_open, {open_trades_count} ещё открыты")
                    
                except Exception as e:
                    logger.warning(f"[LATE] DB error: {e}, using memory")
                    lines.append(f"  ❌ Ошибка БД: {e}")
                    closed = self.trader.closed_positions if self.trader else []
                    for p in closed:
                        d = p.to_dict() if hasattr(p, 'to_dict') else p
                        trades.append(d)
                    lines.append(f"Режим: из памяти ({len(trades)} сделок)")
            
            if not trades:
                lines.append("❌ Нет сделок для анализа")
                return {'summary': '\n'.join(lines)}
            
            # Классификация
            fresh_trades = []   # При открытии change_24h >= порога
            late_trades = []    # При открытии change_24h < порога (опоздал)
            no_data_trades = [] # Нет данных change_24h_at_open (старые сделки)
            
            for t in trades:
                scan_change = abs(t.get('change_24h', 0))
                open_change = abs(t.get('change_24h_at_open', 0))
                
                # Старая сделка без данных change_24h_at_open
                if open_change == 0 and scan_change > 0:
                    no_data_trades.append(t)
                    continue
                
                t['_scan_change'] = scan_change
                t['_open_change'] = open_change
                t['_entry_lag'] = scan_change - open_change
                
                # Ключевая проверка: прошла бы монета фильтр В МОМЕНТ ОТКРЫТИЯ?
                if open_change >= min_change:
                    fresh_trades.append(t)  # Да — свежий вход
                else:
                    late_trades.append(t)   # Нет — опоздавший
            
            # Статистика
            def calc_stats(trade_list):
                if not trade_list:
                    return {'count': 0, 'wins': 0, 'wr': 0, 'pnl': 0, 'pf': 0}
                wins = sum(1 for t in trade_list if t.get('pnl_usdt', 0) > 0)
                pnl = sum(t.get('pnl_usdt', 0) for t in trade_list)
                gp = sum(t.get('pnl_usdt', 0) for t in trade_list if t.get('pnl_usdt', 0) > 0)
                gl = sum(abs(t.get('pnl_usdt', 0)) for t in trade_list if t.get('pnl_usdt', 0) < 0)
                pf = gp / gl if gl > 0 else 99.0
                wr = (wins / len(trade_list) * 100) if trade_list else 0
                return {'count': len(trade_list), 'wins': wins, 'wr': wr, 'pnl': pnl, 'pf': pf}
            
            fresh_stats = calc_stats(fresh_trades)
            late_stats = calc_stats(late_trades)
            total = len(fresh_trades) + len(late_trades) + len(no_data_trades)
            
            lines.append(f"Всего сделок: {total}\n")
            
            lines.append(f"✅ **Свежие входы** (change при открытии ≥{min_change}%): {fresh_stats['count']} сделок")
            if fresh_stats['count'] > 0:
                lines.append(f"  WR: {fresh_stats['wr']:.1f}% | PnL: ${fresh_stats['pnl']:+.2f} | PF: {fresh_stats['pf']:.2f}")
            
            lines.append(f"\n⏰ **Опоздавшие входы** (change при открытии <{min_change}%): {late_stats['count']} сделок")
            if late_stats['count'] > 0:
                lines.append(f"  WR: {late_stats['wr']:.1f}% | PnL: ${late_stats['pnl']:+.2f} | PF: {late_stats['pf']:.2f}")
                
                # Детали опоздавших
                lines.append(f"\n  📋 Детали опоздавших:")
                sorted_late = sorted(late_trades, key=lambda x: x.get('_entry_lag', 0), reverse=True)
                for t in sorted_late[:10]:
                    sym = t.get('symbol', '?').replace('/USDT:USDT', '')
                    side = t.get('side', '?')
                    pnl = t.get('pnl_usdt', 0)
                    scan_ch = t.get('change_24h', 0)
                    open_ch = t.get('change_24h_at_open', 0)
                    icon = '✅' if pnl > 0 else '❌'
                    lines.append(f"  {icon} {sym} {side}: скан={scan_ch:+.1f}% → откр={open_ch:+.1f}% → ${pnl:+.2f}")
            
            if no_data_trades:
                lines.append(f"\n⚪ Без данных (старые сделки): {len(no_data_trades)}")
            
            # Вывод
            if late_stats['count'] >= 5:
                if late_stats['pf'] < 1.0:
                    lines.append(f"\n⚠️ **ВЫВОД: Опоздавшие входы УБЫТОЧНЫ (PF={late_stats['pf']:.2f})**")
                    lines.append(f"  Рекомендация: добавить перепроверку change_24h перед открытием")
                elif late_stats['pf'] >= 1.5:
                    lines.append(f"\n✅ **ВЫВОД: Опоздавшие входы ПРИБЫЛЬНЫ (PF={late_stats['pf']:.2f})**")
                    lines.append(f"  Текущее поведение работает — перепроверка не нужна")
                else:
                    lines.append(f"\n🟡 **ВЫВОД: Опоздавшие входы НЕЙТРАЛЬНЫ (PF={late_stats['pf']:.2f})**")
                    lines.append(f"  Нужно больше данных для выводов")
            else:
                lines.append(f"\n📊 Мало данных ({late_stats['count']} опоздавших). Нужно минимум 5 для выводов.")
            
            return {
                'fresh': fresh_stats,
                'late': late_stats,
                'no_data': len(no_data_trades),
                'min_change': min_change,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[LATE] Error: {e}", exc_info=True)
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    

    def backtest_change_thresholds(self, days: int = 30, top_n: int = 100) -> Dict:
        """
        Бэктест разных порогов min_change_filter (0..25%) с recheck.
        Находит оптимальный порог входа для текущей стратегии.
        Recheck=True: вход на свече N+1 — без опозданий.
        """
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            lines = ["📊 **БЭКТЕСТ ПОРОГОВ ВХОДА (min_change_filter)**\n"]
            lines.append(f"Период: {days} дней | Монет: {top_n} | Recheck: ✅ (без опозданий)\n")
            
            # Текущие параметры стратегии
            current = self.get_current_strategy()
            params = current.get('parameters', {})
            rsi_short           = params.get('rsi_overbought', 70)
            rsi_long            = params.get('rsi_oversold', 30)
            sl_pct              = params.get('stop_loss_pct', 5.0)
            tp_pct              = params.get('take_profit_pct', 7.0)
            position_size       = params.get('position_size', 25)
            leverage            = params.get('leverage', 5)
            trailing_enabled    = params.get('trailing_enabled', True)
            trailing_activation = params.get('trailing_activation_pct', 1.5)
            trailing_distance   = params.get('trailing_distance_pct', 0.25)
            commission_pct      = params.get('commission_pct', 0.08)
            slippage_pct        = params.get('slippage_pct', 0.05)
            symbol_cooldown_candles = params.get('symbol_cooldown_candles', 2)
            max_symbol_losses_daily = params.get('max_symbol_losses_daily', 2)
            current_threshold   = params.get('min_change_filter', 0)
            
            # Тестируемые пороги
            thresholds = [0, 3, 5, 8, 10, 12, 15, 18, 20, 25]
            
            # Загружаем монеты
            top_coins = self.get_top_liquid_coins(top_n)
            symbols = [c['symbol'] for c in top_coins.get('coins', [])]
            if not symbols:
                return {'summary': '❌ Нет монет для тестирования'}
            
            lines.append(f"⏳ Загружаю свечи для {len(symbols)} монет...")
            if self.trader and hasattr(self.trader, '_add_log'):
                self.trader._add_log("filter", f"📊 BACKTEST_CHANGE: загрузка {len(symbols)} монет...")
            
            # Загружаем свечи (тот же метод что в run_backtest_multi)
            import time as _time
            loaded_candles = {}
            load_start = _time.time()
            load_errors = 0
            consecutive_errors = 0
            
            for idx, symbol in enumerate(symbols):
                if _time.time() - load_start > 300:
                    break
                if consecutive_errors >= 10:
                    break
                try:
                    clean_symbol, _ = self._normalize_symbol(symbol)
                    candles_data = self.load_candles_cached(symbol, '15m', days)
                    candles = candles_data.get('candles', [])
                    if len(candles) >= 50:
                        loaded_candles[clean_symbol] = candles
                        consecutive_errors = 0
                    else:
                        load_errors += 1
                except Exception as e:
                    load_errors += 1
                    consecutive_errors += 1
                
                if idx > 0 and idx % 25 == 0 and self.trader and hasattr(self.trader, '_add_log'):
                    self.trader._add_log("filter", f"📊 BACKTEST_CHANGE: загружено {len(loaded_candles)}/{idx}...")
            
            if not loaded_candles:
                return {'summary': '❌ Не удалось загрузить свечи. Проверьте подключение к Binance.'}
            
            lines.append(f"✅ Загружено {len(loaded_candles)} монет за {_time.time()-load_start:.0f}с\n")
            
            # Запускаем бэктест для каждого порога
            results_by_threshold = []
            
            for threshold in thresholds:
                total_pnl    = 0.0
                total_wins   = 0
                total_losses = 0
                total_trades = 0
                gross_profit = 0.0
                gross_loss   = 0.0
                
                for sym, candles in loaded_candles.items():
                    try:
                        r = self._backtest_symbol(
                            candles, rsi_short, rsi_long, sl_pct, tp_pct,
                            position_size, leverage,
                            trailing_enabled, trailing_activation, trailing_distance,
                            return_details=False,
                            commission_pct=commission_pct,
                            slippage_pct=slippage_pct,
                            symbol_cooldown_candles=symbol_cooldown_candles,
                            max_symbol_losses_daily=max_symbol_losses_daily,
                            btc_trends=None,
                            btc_modes=None,
                            btc_close_modes=None,
                            min_change_pct=float(threshold),
                            recheck_change_at_open=True
                        )
                        total_pnl    += r.get('pnl', 0)
                        total_wins   += r.get('wins', 0)
                        total_losses += r.get('losses', 0)
                        total_trades += r.get('trades', 0)
                        gross_profit += r.get('gross_profit', 0)
                        gross_loss   += abs(r.get('gross_loss', 0))
                    except Exception:
                        pass
                
                wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
                pf = (gross_profit / gross_loss) if gross_loss > 0 else (9.99 if gross_profit > 0 else 0)
                
                results_by_threshold.append({
                    'threshold': threshold,
                    'trades': total_trades,
                    'wins': total_wins,
                    'losses': total_losses,
                    'wr': wr,
                    'pnl': total_pnl,
                    'pf': pf,
                })
                
                if self.trader and hasattr(self.trader, '_add_log'):
                    self.trader._add_log("filter", f"📊 Порог {threshold}%: {total_trades} сд, PnL=${total_pnl:+.2f}, PF={pf:.2f}")
            
            # Сортируем по PF (качество) с фильтром минимум 10 сделок
            valid = [r for r in results_by_threshold if r['trades'] >= 10]
            sorted_by_pf  = sorted(valid, key=lambda x: x['pf'], reverse=True)
            sorted_by_pnl = sorted(valid, key=lambda x: x['pnl'], reverse=True)
            best = sorted_by_pf[0] if sorted_by_pf else None
            
            # Таблица
            lines.append(f"{'Порог':<8} {'Сделок':<8} {'WR':>5} {'PnL':>10} {'PF':>6}  Рейтинг")
            lines.append("─" * 54)
            
            medals = {0: '🥇', 1: '🥈', 2: '🥉'}
            pf_rank = {r['threshold']: i for i, r in enumerate(sorted_by_pf)}
            
            for r in results_by_threshold:
                medal = medals.get(pf_rank.get(r['threshold'], 99), '')
                if best and r['threshold'] == best['threshold']:
                    medal = '🏆 ЛУЧШИЙ'
                thr_str = f"{r['threshold']}%" if r['threshold'] > 0 else "Нет"
                pf_str  = f"{r['pf']:.2f}" if r['pf'] > 0 else "—"
                cur_mark = " ◄" if r['threshold'] == current_threshold else ""
                lines.append(f"{thr_str:<8} {r['trades']:<8} {r['wr']:>4.0f}% {r['pnl']:>+9.2f} {pf_str:>6}  {medal}{cur_mark}")
            
            lines.append("─" * 54)
            
            if best:
                lines.append(f"\n🏆 Оптимальный порог: **{best['threshold']}%**")
                lines.append(f"   Сделок: {best['trades']} | WR: {best['wr']:.0f}% | PnL: ${best['pnl']:+.2f} | PF: {best['pf']:.2f}")
                if best['threshold'] != current_threshold:
                    lines.append(f"\n💡 Текущий порог: **{current_threshold}%** — рекомендую изменить:")
                    lines.append(f"   [TOOL:SET_PARAM:min_change_filter:{best['threshold']}]")
                else:
                    lines.append(f"\n✅ Текущий порог {current_threshold}% уже оптимальный!")
            
            lines.append(f"\n⚠️ Recheck=ON: все входы без задержки (N+1 свеча).")
            
            return {
                'results': results_by_threshold,
                'best': best,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[BACKTEST_CHANGE] Error: {e}", exc_info=True)
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}

    def walk_forward_analysis(self, days: int = 30, top_n: int = 100) -> Dict:
        """
        Walk-Forward Analysis (WFA) — скользящая валидация стратегии.
        
        Делит период на окна: 70% тренировка (оптимизация), 30% тест (проверка).
        Окна скользят вперёд, каждый раз оптимизируя на свежих данных
        и тестируя на ещё не виденных.
        
        Это самый надёжный способ проверить, что стратегия не переоптимизирована.
        """
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            lines = ["📊 **WALK-FORWARD ANALYSIS**\n"]
            
            # Получаем монеты
            symbols = []
            top_coins = self.get_top_liquid_coins(top_n)
            if 'error' not in top_coins:
                coins_data = top_coins.get('coins', [])[:top_n]
                symbols = [c['symbol'] for c in coins_data if 'symbol' in c]
            
            if not symbols:
                symbols = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK',
                           'DOT', 'UNI', 'ATOM', 'LTC', 'BCH', 'NEAR', 'APT', 'ARB',
                           'OP', 'SUI', 'SEI', 'INJ', 'TIA', 'PEPE', 'WIF', 'BONK'][:top_n]
            
            lines.append(f"Период: {days} дней | Монет: {len(symbols)}")
            
            # ====== ШАГ 1: Загружаем ВСЕ свечи за полный период ======
            lines.append("⏳ Загрузка данных...")
            all_candles = {}
            for sym in symbols:
                try:
                    clean_sym, full_sym = self._normalize_symbol(sym)
                    if self.db and self.db.is_blacklisted(full_sym):
                        continue
                    data = self.load_candles_cached(sym, '15m', days)
                    candles = data.get('candles', [])
                    if len(candles) >= 200:  # Минимум ~2 дня
                        all_candles[clean_sym] = candles
                except Exception:
                    continue
            
            if not all_candles:
                return {'error': 'No data', 'summary': '❌ Нет данных для анализа'}
            
            lines.append(f"Загружено: {len(all_candles)} монет\n")
            
            # ====== ШАГ 2: Определяем окна WFA ======
            # Берём минимальную длину свечей
            min_candles = min(len(c) for c in all_candles.values())
            
            # Размеры окон: train = 70%, test = 30%
            # Количество окон зависит от периода
            num_windows = max(3, min(5, days // 7))  # 3-5 окон
            test_size = min_candles // (num_windows + 2)  # Размер тестового окна
            train_size = test_size * 2  # Тренировочное = 2x тестовое
            step = test_size  # Шаг скольжения = размер теста
            
            lines.append(f"Окна: {num_windows} | Трейн: ~{train_size//96:.0f}д | Тест: ~{test_size//96:.0f}д | Шаг: ~{step//96:.0f}д")
            
            # ====== ШАГ 3: Сетка параметров (компактная) ======
            param_grid = []
            sl_values = [0.25, 1.0, 2.5, 4.0]
            tp_values = [4.0, 7.0, 10.0]
            trail_act_values = [0.5, 1.5, 3.0]
            trail_dist_values = [0.25, 0.75]
            
            for sl in sl_values:
                for tp in tp_values:
                    if tp <= sl * 1.5:
                        continue
                    for ta in trail_act_values:
                        for td in trail_dist_values:
                            if td >= ta:
                                continue
                            param_grid.append({
                                'stop_loss_pct': sl, 'take_profit_pct': tp,
                                'trailing_activation_pct': ta, 'trailing_distance_pct': td,
                                'trailing_enabled': True
                            })
            
            lines.append(f"Сетка: {len(param_grid)} комбинаций\n")
            
            # ====== ШАГ 4: Скользящие окна ======
            window_results = []
            settings = self.trader.get_settings()
            rsi_short = settings.get('rsi_overbought', 70)
            rsi_long = settings.get('rsi_oversold', 30)
            position_size = settings.get('position_size', 50)
            leverage = settings.get('leverage', 5)
            min_change_pct = settings.get('min_change_filter', 0.0)
            
            for w in range(num_windows):
                train_start = w * step
                train_end = train_start + train_size
                test_start = train_end
                test_end = test_start + test_size
                
                if test_end > min_candles:
                    break
                
                lines.append(f"--- Окно {w+1}/{num_windows} ---")
                
                # ====== TRAIN: Оптимизация на тренировочных данных ======
                best_pf = 0
                best_wr = 0
                best_pnl = -999999
                best_params = None
                
                for params in param_grid:
                    total_pnl = 0
                    total_trades = 0
                    total_wins = 0
                    gross_profit = 0
                    gross_loss = 0
                    
                    for sym, candles in all_candles.items():
                        train_candles = candles[train_start:train_end]
                        if len(train_candles) < 100:
                            continue
                        try:
                            r = self._backtest_symbol(
                                train_candles, rsi_short, rsi_long,
                                params['stop_loss_pct'], params['take_profit_pct'],
                                position_size, leverage,
                                params['trailing_enabled'],
                                params['trailing_activation_pct'],
                                params['trailing_distance_pct'],
                                return_details=False,
                                min_change_pct=min_change_pct,
                                recheck_change_at_open=recheck_change_at_open
                            )
                            total_pnl += r.get('pnl', 0)
                            total_trades += r.get('trades', 0)
                            total_wins += r.get('wins', 0)
                            gross_profit += r.get('gross_profit', 0)
                            gross_loss += r.get('gross_loss', 0)
                        except Exception:
                            continue
                    
                    if total_trades < 10:
                        continue
                    
                    pf = gross_profit / abs(gross_loss) if gross_loss != 0 else 0
                    wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
                    
                    # Выбираем лучший по PF * sqrt(trades) (Sharpe-like)
                    score = pf * (total_trades ** 0.5) if pf > 0 else 0
                    best_score = best_pf * 1  # Сравниваем
                    
                    if pf > best_pf and total_pnl > 0:
                        best_pf = pf
                        best_wr = wr
                        best_pnl = total_pnl
                        best_params = params.copy()
                        best_params['_train_trades'] = total_trades
                        best_params['_train_pf'] = pf
                        best_params['_train_wr'] = wr
                        best_params['_train_pnl'] = total_pnl
                
                if not best_params:
                    lines.append(f"  ⚠️ Нет прибыльных комбинаций на тренировке")
                    window_results.append({
                        'window': w + 1, 'train_ok': False, 'oos_ok': False,
                        'train_pf': 0, 'oos_pf': 0
                    })
                    continue
                
                lines.append(f"  🏋️ Трейн: SL={best_params['stop_loss_pct']}% TP={best_params['take_profit_pct']}% "
                           f"Trail={best_params['trailing_activation_pct']}/{best_params['trailing_distance_pct']}%")
                lines.append(f"  📊 Трейн: PF={best_pf:.2f} WR={best_wr:.1f}% PnL=${best_pnl:.0f} ({best_params['_train_trades']} сделок)")
                
                # ====== TEST: Проверка на невиденных данных ======
                oos_pnl = 0
                oos_trades = 0
                oos_wins = 0
                oos_gp = 0
                oos_gl = 0
                
                for sym, candles in all_candles.items():
                    test_candles = candles[test_start:test_end]
                    if len(test_candles) < 50:
                        continue
                    try:
                        r = self._backtest_symbol(
                            test_candles, rsi_short, rsi_long,
                            best_params['stop_loss_pct'], best_params['take_profit_pct'],
                            position_size, leverage,
                            best_params['trailing_enabled'],
                            best_params['trailing_activation_pct'],
                            best_params['trailing_distance_pct'],
                            return_details=False,
                            min_change_pct=min_change_pct,
                            recheck_change_at_open=recheck_change_at_open
                        )
                        oos_pnl += r.get('pnl', 0)
                        oos_trades += r.get('trades', 0)
                        oos_wins += r.get('wins', 0)
                        oos_gp += r.get('gross_profit', 0)
                        oos_gl += r.get('gross_loss', 0)
                    except Exception:
                        continue
                
                oos_pf = oos_gp / abs(oos_gl) if oos_gl != 0 else 0
                oos_wr = (oos_wins / oos_trades * 100) if oos_trades > 0 else 0
                oos_ok = oos_pf >= 1.0 and oos_pnl > 0
                
                emoji = '✅' if oos_ok else '❌'
                lines.append(f"  {emoji} Тест:  PF={oos_pf:.2f} WR={oos_wr:.1f}% PnL=${oos_pnl:.0f} ({oos_trades} сделок)")
                
                # Сравнение деградации
                if best_pf > 0:
                    degradation = ((best_pf - oos_pf) / best_pf * 100)
                    deg_emoji = '🟢' if degradation < 30 else '🟡' if degradation < 60 else '🔴'
                    lines.append(f"  {deg_emoji} Деградация PF: {degradation:+.0f}%")
                
                window_results.append({
                    'window': w + 1,
                    'train_ok': True,
                    'oos_ok': oos_ok,
                    'params': best_params,
                    'train_pf': best_pf,
                    'train_wr': best_wr,
                    'train_pnl': best_pnl,
                    'oos_pf': oos_pf,
                    'oos_wr': oos_wr,
                    'oos_pnl': oos_pnl,
                    'oos_trades': oos_trades,
                    'degradation': degradation if best_pf > 0 else 100
                })
                lines.append("")
            
            # ====== ШАГ 5: Итоговый отчёт ======
            lines.append("═══════════════════════════════")
            lines.append("📋 **ИТОГИ WFA**\n")
            
            oos_windows = [w for w in window_results if w.get('train_ok')]
            passed_windows = [w for w in oos_windows if w.get('oos_ok')]
            
            if oos_windows:
                pass_rate = len(passed_windows) / len(oos_windows) * 100
                avg_oos_pf = statistics.mean([w['oos_pf'] for w in oos_windows])
                avg_degradation = statistics.mean([w.get('degradation', 100) for w in oos_windows])
                total_oos_pnl = sum(w.get('oos_pnl', 0) for w in oos_windows)
                
                lines.append(f"Окон прошло тест: {len(passed_windows)}/{len(oos_windows)} ({pass_rate:.0f}%)")
                lines.append(f"Средний OOS PF: {avg_oos_pf:.2f}")
                lines.append(f"Средняя деградация: {avg_degradation:.0f}%")
                lines.append(f"Суммарный OOS PnL: ${total_oos_pnl:.0f}")
                
                # Вердикт
                lines.append("")
                if pass_rate >= 80 and avg_oos_pf >= 1.5 and avg_degradation < 40:
                    lines.append("🏆 СТРАТЕГИЯ РОБАСТНАЯ! Отличная OOS устойчивость.")
                    verdict = 'ROBUST'
                elif pass_rate >= 60 and avg_oos_pf >= 1.0:
                    lines.append("✅ Стратегия СТАБИЛЬНАЯ. Приемлемая OOS производительность.")
                    verdict = 'STABLE'
                elif pass_rate >= 40:
                    lines.append("⚠️ Стратегия НЕСТАБИЛЬНАЯ. Высокая деградация на OOS данных.")
                    verdict = 'UNSTABLE'
                else:
                    lines.append("❌ Стратегия ПЕРЕОПТИМИЗИРОВАНА. OOS результаты значительно хуже.")
                    verdict = 'OVERFITTED'
                
                # Рекомендации по параметрам (консенсус окон)
                if passed_windows:
                    avg_sl = statistics.mean([w['params']['stop_loss_pct'] for w in passed_windows])
                    avg_tp = statistics.mean([w['params']['take_profit_pct'] for w in passed_windows])
                    avg_ta = statistics.mean([w['params']['trailing_activation_pct'] for w in passed_windows])
                    avg_td = statistics.mean([w['params']['trailing_distance_pct'] for w in passed_windows])
                    
                    lines.append(f"\n📌 Консенсус прибыльных окон:")
                    lines.append(f"  SL={avg_sl:.2f}% TP={avg_tp:.1f}% Trail={avg_ta:.1f}/{avg_td:.2f}%")
                    
                    # Сохраняем для APPLY_BEST
                    self._last_grid_best = {
                        'sl': round(avg_sl, 2), 'tp': round(avg_tp, 1),
                        'trail_act': round(avg_ta, 2), 'trail_dist': round(avg_td, 2),
                        'pf': avg_oos_pf, 'wr': statistics.mean([w['oos_wr'] for w in passed_windows]),
                        'pnl': total_oos_pnl, 'trades': sum(w.get('oos_trades', 0) for w in passed_windows),
                        'source': 'WFA'
                    }
                    lines.append(f"\n💡 Применить: [TOOL:APPLY_BEST]")
            else:
                lines.append("❌ Нет результатов — недостаточно данных")
                verdict = 'NO_DATA'
            
            return {
                'success': True,
                'verdict': verdict if oos_windows else 'NO_DATA',
                'windows': window_results,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] WFA error: {e}", exc_info=True)
            return {'error': str(e), 'summary': f"❌ Ошибка WFA: {e}"}
    
    def backtest_patterns(self, days: int = 60, top_n: int = 200) -> Dict:
        """
        Бэктест для поиска паттернов (время, день недели, BTC тренд)
        
        Анализирует:
        - По часам (0-23 UTC)
        - По дням недели (пн-вс)
        - По тренду BTC (bullish/bearish/neutral) + направлению (SHORT/LONG)
        
        Считает Profit Factor для каждого сегмента
        """
        try:
            if not self.trader:
                return {'error': 'Trader not available', 'summary': '❌ Trader не подключен'}
            
            lines = ["🔬 **БЭКТЕСТ ПАТТЕРНОВ**\n"]
            
            # Получаем текущие параметры стратегии
            settings = self.trader.get_settings()
            sl = settings.get('stop_loss_pct', 4.5)
            tp = settings.get('take_profit_pct', 7.0)
            trail_act = settings.get('trailing_activation_pct', 1.0)
            trail_dist = settings.get('trailing_distance_pct', 0.25)
            
            # Хеш стратегии для привязки паттернов
            strategy_hash = f"SL{sl}_TP{tp}_TA{trail_act}_TD{trail_dist}"
            
            lines.append(f"Стратегия: SL={sl}% TP={tp}% Trail={trail_act}/{trail_dist}%")
            lines.append(f"Период: {days} дней, Монет: до {top_n}\n")
            
            # Получаем монеты
            symbols = []
            top_coins = self.get_top_liquid_coins(top_n)
            if 'error' not in top_coins:
                coins_data = top_coins.get('coins', [])[:top_n]
                symbols = [c['symbol'] for c in coins_data if 'symbol' in c]
            
            if not symbols:
                symbols = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK',
                          'DOT', 'MATIC', 'UNI', 'ATOM', 'LTC', 'BCH', 'NEAR', 'APT'][:top_n]
            
            lines.append(f"Загружаем данные для {len(symbols)} монет...")
            
            # Параметры для бэктеста
            test_params = {
                'stop_loss_pct': sl,
                'take_profit_pct': tp,
                'trailing_activation_pct': trail_act,
                'trailing_distance_pct': trail_dist,
                'trailing_enabled': True
            }
            
            # Запускаем бэктест с детализацией
            bt_result = self.run_backtest_multi(
                symbols=symbols,
                days=days,
                custom_params=test_params,
                return_details=True  # Получаем детали каждой сделки
            )
            
            if 'error' in bt_result:
                return {'error': bt_result['error'], 'summary': f"❌ Ошибка бэктеста: {bt_result['error']}"}
            
            total_trades = bt_result.get('total_trades', 0)
            if total_trades < 100:
                lines.append(f"\n⚠️ Мало сделок ({total_trades}), паттерны могут быть ненадёжными")
            
            lines.append(f"Всего сделок: {total_trades}")
            lines.append(f"Общий PF: {bt_result.get('profit_factor', 0):.2f}")
            
            # Получаем РЕАЛЬНЫЕ детали сделок с timestamp
            raw_trades = bt_result.get('all_trade_details', [])
            
            if not raw_trades:
                return {'error': 'No trade details', 'summary': '❌ Нет детальных данных о сделках'}
            
            # Конвертируем timestamp в hour и day_of_week
            all_trades = []
            for t in raw_trades:
                open_time = t.get('open_time', 0)
                if open_time > 0:
                    # open_time в миллисекундах
                    dt = datetime.fromtimestamp(open_time / 1000)
                    all_trades.append({
                        'symbol': t.get('symbol', ''),
                        'hour': dt.hour,
                        'day_of_week': dt.weekday(),
                        'is_win': t.get('is_win', False),
                        'pnl': t.get('pnl', 0),
                        'side': t.get('side', 'SHORT'),
                        'close_reason': t.get('close_reason', '')
                    })
            
            if not all_trades:
                return {'error': 'No valid trades', 'summary': '❌ Нет сделок с валидным timestamp'}
            
            # ========== АНАЛИЗ ПО ЧАСАМ ==========
            lines.append("\n" + "="*45)
            lines.append("🕐 **ПО ЧАСАМ (UTC):**")
            
            hour_patterns = {}
            for hour_start in [0, 4, 8, 12, 16, 20]:
                hour_end = hour_start + 4
                hour_trades = [t for t in all_trades if hour_start <= t['hour'] < hour_end]
                
                if len(hour_trades) >= 10:
                    gross_profit = sum(t['pnl'] for t in hour_trades if t['pnl'] > 0)
                    gross_loss = abs(sum(t['pnl'] for t in hour_trades if t['pnl'] < 0))
                    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
                    wr = len([t for t in hour_trades if t['is_win']]) / len(hour_trades) * 100
                    
                    hour_patterns[f"{hour_start:02d}-{hour_end:02d}"] = {
                        'pf': pf, 'wr': wr, 'trades': len(hour_trades),
                        'gross_profit': gross_profit, 'gross_loss': gross_loss
                    }
                    
                    pf_emoji = "✅" if pf >= 1.2 else "⚠️" if pf >= 1.0 else "❌"
                    lines.append(f"  {hour_start:02d}-{hour_end:02d} UTC: PF={pf:.2f} {pf_emoji}, WR={wr:.0f}%, {len(hour_trades)} сделок")
            
            # ========== АНАЛИЗ ПО ДНЯМ НЕДЕЛИ ==========
            lines.append("\n📅 **ПО ДНЯМ НЕДЕЛИ:**")
            
            day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            day_patterns = {}
            
            for day in range(7):
                day_trades = [t for t in all_trades if t['day_of_week'] == day]
                
                if len(day_trades) >= 10:
                    gross_profit = sum(t['pnl'] for t in day_trades if t['pnl'] > 0)
                    gross_loss = abs(sum(t['pnl'] for t in day_trades if t['pnl'] < 0))
                    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
                    wr = len([t for t in day_trades if t['is_win']]) / len(day_trades) * 100
                    
                    day_patterns[day_names[day]] = {
                        'pf': pf, 'wr': wr, 'trades': len(day_trades),
                        'gross_profit': gross_profit, 'gross_loss': gross_loss
                    }
                    
                    pf_emoji = "✅" if pf >= 1.2 else "⚠️" if pf >= 1.0 else "❌"
                    weekend = " (выходной)" if day >= 5 else ""
                    lines.append(f"  {day_names[day]}{weekend}: PF={pf:.2f} {pf_emoji}, WR={wr:.0f}%, {len(day_trades)} сделок")
            
            # Будни vs Выходные
            weekday_trades = [t for t in all_trades if t['day_of_week'] < 5]
            weekend_trades = [t for t in all_trades if t['day_of_week'] >= 5]
            
            if len(weekday_trades) >= 20 and len(weekend_trades) >= 20:
                wd_profit = sum(t['pnl'] for t in weekday_trades if t['pnl'] > 0)
                wd_loss = abs(sum(t['pnl'] for t in weekday_trades if t['pnl'] < 0))
                wd_pf = wd_profit / wd_loss if wd_loss > 0 else 99.0
                
                we_profit = sum(t['pnl'] for t in weekend_trades if t['pnl'] > 0)
                we_loss = abs(sum(t['pnl'] for t in weekend_trades if t['pnl'] < 0))
                we_pf = we_profit / we_loss if we_loss > 0 else 99.0
                
                lines.append(f"\n  📊 Будни: PF={wd_pf:.2f}, Выходные: PF={we_pf:.2f}")
                
                if we_pf < 1.0 and wd_pf >= 1.0:
                    lines.append(f"  ⚠️ РЕКОМЕНДАЦИЯ: Не торговать в выходные!")
            
            # ========== АНАЛИЗ ПО НАПРАВЛЕНИЮ ==========
            lines.append("\n📈 **ПО НАПРАВЛЕНИЮ (SHORT/LONG):**")
            
            short_trades = [t for t in all_trades if t['side'] == 'SHORT']
            long_trades = [t for t in all_trades if t['side'] == 'LONG']
            
            direction_patterns = {}
            
            for name, trades in [('SHORT', short_trades), ('LONG', long_trades)]:
                if len(trades) >= 20:
                    gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
                    gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
                    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
                    wr = len([t for t in trades if t['is_win']]) / len(trades) * 100
                    
                    direction_patterns[name] = {
                        'pf': pf, 'wr': wr, 'trades': len(trades),
                        'gross_profit': gross_profit, 'gross_loss': gross_loss
                    }
                    
                    pf_emoji = "✅" if pf >= 1.2 else "⚠️" if pf >= 1.0 else "❌"
                    emoji = "🔴" if name == "SHORT" else "🟢"
                    lines.append(f"  {emoji} {name}: PF={pf:.2f} {pf_emoji}, WR={wr:.0f}%, {len(trades)} сделок")
            
            # ========== СОХРАНЕНИЕ В БД ==========
            try:
                if self.brain:
                    with self.brain.get_connection() as conn:
                        cur = conn.cursor()
                        
                        # Сохраняем паттерны по часам
                        for time_range, data in hour_patterns.items():
                            cur.execute('''
                                INSERT OR REPLACE INTO trading_patterns 
                                (strategy_hash, factor_type, factor_value, trades_count, wins, losses,
                                 gross_profit, gross_loss, profit_factor, win_rate, avg_pnl, updated_at)
                                VALUES (?, 'hour', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            ''', (strategy_hash, time_range, data['trades'],
                                  int(data['trades'] * data['wr'] / 100), 
                                  int(data['trades'] * (1 - data['wr'] / 100)),
                                  data['gross_profit'], data['gross_loss'], data['pf'], data['wr'],
                                  (data['gross_profit'] - data['gross_loss']) / data['trades'] if data['trades'] > 0 else 0))
                        
                        # Сохраняем паттерны по дням
                        for day_name, data in day_patterns.items():
                            cur.execute('''
                                INSERT OR REPLACE INTO trading_patterns 
                                (strategy_hash, factor_type, factor_value, trades_count, wins, losses,
                                 gross_profit, gross_loss, profit_factor, win_rate, avg_pnl, updated_at)
                                VALUES (?, 'day', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            ''', (strategy_hash, day_name, data['trades'],
                                  int(data['trades'] * data['wr'] / 100),
                                  int(data['trades'] * (1 - data['wr'] / 100)),
                                  data['gross_profit'], data['gross_loss'], data['pf'], data['wr'],
                                  (data['gross_profit'] - data['gross_loss']) / data['trades'] if data['trades'] > 0 else 0))
                        
                        # Сохраняем паттерны по направлению
                        for direction, data in direction_patterns.items():
                            cur.execute('''
                                INSERT OR REPLACE INTO trading_patterns 
                                (strategy_hash, factor_type, factor_value, trades_count, wins, losses,
                                 gross_profit, gross_loss, profit_factor, win_rate, avg_pnl, updated_at)
                                VALUES (?, 'direction', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            ''', (strategy_hash, direction, data['trades'],
                                  int(data['trades'] * data['wr'] / 100),
                                  int(data['trades'] * (1 - data['wr'] / 100)),
                                  data['gross_profit'], data['gross_loss'], data['pf'], data['wr'],
                                  (data['gross_profit'] - data['gross_loss']) / data['trades'] if data['trades'] > 0 else 0))
                        
                        conn.commit()
                        lines.append(f"\n✅ Паттерны сохранены в базу (hash: {strategy_hash})")
                
            except Exception as db_error:
                lines.append(f"\n⚠️ Не удалось сохранить в БД: {db_error}")
            
            # ========== РЕКОМЕНДАЦИИ ==========
            lines.append("\n" + "="*45)
            lines.append("🎯 **РЕКОМЕНДАЦИИ:**")
            
            recommendations = []
            
            # Найти лучшие и худшие часы
            if hour_patterns:
                best_hour = max(hour_patterns.items(), key=lambda x: x[1]['pf'])
                worst_hour = min(hour_patterns.items(), key=lambda x: x[1]['pf'])
                
                if best_hour[1]['pf'] >= 1.3:
                    recommendations.append(f"✅ Лучшее время: {best_hour[0]} UTC (PF={best_hour[1]['pf']:.2f})")
                if worst_hour[1]['pf'] < 1.0:
                    recommendations.append(f"❌ Избегать: {worst_hour[0]} UTC (PF={worst_hour[1]['pf']:.2f})")
            
            # Выходные
            if 'Сб' in day_patterns and 'Вс' in day_patterns:
                weekend_pf = (day_patterns['Сб']['pf'] + day_patterns['Вс']['pf']) / 2
                if weekend_pf < 1.0:
                    recommendations.append(f"❌ Не торговать в выходные (PF={weekend_pf:.2f})")
            
            # Направление
            if direction_patterns:
                for direction, data in direction_patterns.items():
                    if data['pf'] < 0.9:
                        recommendations.append(f"❌ Осторожно с {direction} (PF={data['pf']:.2f})")
                    elif data['pf'] >= 1.5:
                        recommendations.append(f"✅ {direction} работает хорошо (PF={data['pf']:.2f})")
            
            if recommendations:
                for rec in recommendations:
                    lines.append(f"  {rec}")
            else:
                lines.append("  ℹ️ Нет явных паттернов, нужно больше данных")
            
            lines.append("="*45)
            
            return {
                'success': True,
                'strategy_hash': strategy_hash,
                'total_trades': total_trades,
                'hour_patterns': hour_patterns,
                'day_patterns': day_patterns,
                'direction_patterns': direction_patterns,
                'recommendations': recommendations,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] backtest_patterns error: {e}")
            import traceback
            traceback.print_exc()
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def analyze_real_patterns(self, days: int = 30) -> Dict:
        """
        Анализ паттернов на РЕАЛЬНЫХ сделках из базы данных.
        
        Использует: hour_opened, day_of_week, side, pnl_usdt
        Считает: Profit Factor для каждого сегмента
        """
        try:
            if not self.db:
                return {'error': 'Database not available', 'summary': '❌ База данных недоступна'}
            
            lines = ["📊 **АНАЛИЗ РЕАЛЬНЫХ СДЕЛОК**\n"]
            
            # Получаем закрытые сделки
            trades = self.db.get_trades(limit=None, only_closed=True, days=days)
            
            if not trades:
                return {'error': 'No trades', 'summary': f'❌ Нет закрытых сделок за {days} дней'}
            
            lines.append(f"Период: {days} дней")
            lines.append(f"Всего сделок: {len(trades)}")
            
            # Общая статистика
            total_profit = sum(t['pnl_usdt'] for t in trades if t.get('pnl_usdt', 0) > 0)
            total_loss = abs(sum(t['pnl_usdt'] for t in trades if t.get('pnl_usdt', 0) < 0))
            overall_pf = total_profit / total_loss if total_loss > 0 else 99.0
            wins = len([t for t in trades if t.get('pnl_usdt', 0) > 0])
            losses = len(trades) - wins
            overall_wr = wins / len(trades) * 100 if trades else 0
            
            lines.append(f"Общий PF: {overall_pf:.2f}, WR: {overall_wr:.1f}% ({wins}W/{losses}L)")
            
            if len(trades) < 30:
                lines.append(f"\n⚠️ Мало сделок ({len(trades)}), паттерны ненадёжны!")
            
            # ========== АНАЛИЗ ПО ЧАСАМ ==========
            lines.append("\n" + "="*45)
            lines.append("🕐 **ПО ЧАСАМ (UTC):**")
            
            hour_patterns = {}
            for hour_start in [0, 4, 8, 12, 16, 20]:
                hour_end = hour_start + 4
                hour_trades = [t for t in trades 
                              if t.get('hour_opened') is not None 
                              and hour_start <= t['hour_opened'] < hour_end]
                
                if len(hour_trades) >= 5:  # Минимум 5 сделок для статистики
                    gross_profit = sum(t['pnl_usdt'] for t in hour_trades if t.get('pnl_usdt', 0) > 0)
                    gross_loss = abs(sum(t['pnl_usdt'] for t in hour_trades if t.get('pnl_usdt', 0) < 0))
                    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
                    wr = len([t for t in hour_trades if t.get('pnl_usdt', 0) > 0]) / len(hour_trades) * 100
                    avg_pnl = sum(t.get('pnl_usdt', 0) for t in hour_trades) / len(hour_trades)
                    
                    hour_patterns[f"{hour_start:02d}-{hour_end:02d}"] = {
                        'pf': pf, 'wr': wr, 'trades': len(hour_trades),
                        'gross_profit': gross_profit, 'gross_loss': gross_loss,
                        'avg_pnl': avg_pnl
                    }
                    
                    pf_emoji = "✅" if pf >= 1.2 else "⚠️" if pf >= 1.0 else "❌"
                    lines.append(f"  {hour_start:02d}-{hour_end:02d} UTC: PF={pf:.2f} {pf_emoji}, WR={wr:.0f}%, {len(hour_trades)} сделок, avg=${avg_pnl:.2f}")
                else:
                    lines.append(f"  {hour_start:02d}-{hour_end:02d} UTC: мало данных ({len(hour_trades)} сделок)")
            
            # ========== АНАЛИЗ ПО ДНЯМ НЕДЕЛИ ==========
            lines.append("\n📅 **ПО ДНЯМ НЕДЕЛИ:**")
            
            day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            day_patterns = {}
            
            for day in range(7):
                day_trades = [t for t in trades 
                             if t.get('day_of_week') is not None 
                             and t['day_of_week'] == day]
                
                if len(day_trades) >= 3:  # Минимум 3 сделки
                    gross_profit = sum(t['pnl_usdt'] for t in day_trades if t.get('pnl_usdt', 0) > 0)
                    gross_loss = abs(sum(t['pnl_usdt'] for t in day_trades if t.get('pnl_usdt', 0) < 0))
                    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
                    wr = len([t for t in day_trades if t.get('pnl_usdt', 0) > 0]) / len(day_trades) * 100
                    avg_pnl = sum(t.get('pnl_usdt', 0) for t in day_trades) / len(day_trades)
                    
                    day_patterns[day_names[day]] = {
                        'pf': pf, 'wr': wr, 'trades': len(day_trades),
                        'gross_profit': gross_profit, 'gross_loss': gross_loss,
                        'avg_pnl': avg_pnl
                    }
                    
                    pf_emoji = "✅" if pf >= 1.2 else "⚠️" if pf >= 1.0 else "❌"
                    weekend = " (выходной)" if day >= 5 else ""
                    lines.append(f"  {day_names[day]}{weekend}: PF={pf:.2f} {pf_emoji}, WR={wr:.0f}%, {len(day_trades)} сделок, avg=${avg_pnl:.2f}")
                else:
                    weekend = " (выходной)" if day >= 5 else ""
                    lines.append(f"  {day_names[day]}{weekend}: мало данных ({len(day_trades)} сделок)")
            
            # Будни vs Выходные
            weekday_trades = [t for t in trades if t.get('day_of_week') is not None and t['day_of_week'] < 5]
            weekend_trades = [t for t in trades if t.get('day_of_week') is not None and t['day_of_week'] >= 5]
            
            if len(weekday_trades) >= 10 and len(weekend_trades) >= 5:
                wd_profit = sum(t['pnl_usdt'] for t in weekday_trades if t.get('pnl_usdt', 0) > 0)
                wd_loss = abs(sum(t['pnl_usdt'] for t in weekday_trades if t.get('pnl_usdt', 0) < 0))
                wd_pf = wd_profit / wd_loss if wd_loss > 0 else 99.0
                
                we_profit = sum(t['pnl_usdt'] for t in weekend_trades if t.get('pnl_usdt', 0) > 0)
                we_loss = abs(sum(t['pnl_usdt'] for t in weekend_trades if t.get('pnl_usdt', 0) < 0))
                we_pf = we_profit / we_loss if we_loss > 0 else 99.0
                
                lines.append(f"\n  📊 Будни ({len(weekday_trades)}): PF={wd_pf:.2f}")
                lines.append(f"  📊 Выходные ({len(weekend_trades)}): PF={we_pf:.2f}")
                
                if we_pf < 0.9 and wd_pf >= 1.0:
                    lines.append(f"  ⚠️ ВНИМАНИЕ: Выходные убыточны!")
            
            # ========== АНАЛИЗ ПО НАПРАВЛЕНИЮ ==========
            lines.append("\n📈 **ПО НАПРАВЛЕНИЮ:**")
            
            direction_patterns = {}
            
            for side in ['SHORT', 'LONG']:
                side_trades = [t for t in trades if t.get('side', '').upper() == side]
                
                if len(side_trades) >= 5:
                    gross_profit = sum(t['pnl_usdt'] for t in side_trades if t.get('pnl_usdt', 0) > 0)
                    gross_loss = abs(sum(t['pnl_usdt'] for t in side_trades if t.get('pnl_usdt', 0) < 0))
                    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
                    wr = len([t for t in side_trades if t.get('pnl_usdt', 0) > 0]) / len(side_trades) * 100
                    avg_pnl = sum(t.get('pnl_usdt', 0) for t in side_trades) / len(side_trades)
                    
                    direction_patterns[side] = {
                        'pf': pf, 'wr': wr, 'trades': len(side_trades),
                        'gross_profit': gross_profit, 'gross_loss': gross_loss,
                        'avg_pnl': avg_pnl
                    }
                    
                    pf_emoji = "✅" if pf >= 1.2 else "⚠️" if pf >= 1.0 else "❌"
                    emoji = "🔴" if side == "SHORT" else "🟢"
                    lines.append(f"  {emoji} {side}: PF={pf:.2f} {pf_emoji}, WR={wr:.0f}%, {len(side_trades)} сделок, avg=${avg_pnl:.2f}")
                else:
                    emoji = "🔴" if side == "SHORT" else "🟢"
                    lines.append(f"  {emoji} {side}: мало данных ({len(side_trades)} сделок)")
            
            # ========== АНАЛИЗ ПО ПРИЧИНЕ ЗАКРЫТИЯ ==========
            lines.append("\n🎯 **ПО ПРИЧИНЕ ЗАКРЫТИЯ:**")
            
            close_reasons = {}
            for t in trades:
                reason = t.get('close_reason', 'UNKNOWN')
                if reason not in close_reasons:
                    close_reasons[reason] = {'trades': [], 'count': 0}
                close_reasons[reason]['trades'].append(t)
                close_reasons[reason]['count'] += 1
            
            for reason, data in sorted(close_reasons.items(), key=lambda x: x[1]['count'], reverse=True):
                reason_trades = data['trades']
                gross_profit = sum(t['pnl_usdt'] for t in reason_trades if t.get('pnl_usdt', 0) > 0)
                gross_loss = abs(sum(t['pnl_usdt'] for t in reason_trades if t.get('pnl_usdt', 0) < 0))
                pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
                avg_pnl = sum(t.get('pnl_usdt', 0) for t in reason_trades) / len(reason_trades)
                
                pf_emoji = "✅" if pf >= 1.2 else "⚠️" if pf >= 1.0 else "❌"
                lines.append(f"  {reason}: {len(reason_trades)} сделок, PF={pf:.2f} {pf_emoji}, avg=${avg_pnl:.2f}")
            
            # ========== РЕКОМЕНДАЦИИ ==========
            lines.append("\n" + "="*45)
            lines.append("🎯 **РЕКОМЕНДАЦИИ:**")
            
            recommendations = []
            
            # Лучшие и худшие часы
            if hour_patterns:
                sorted_hours = sorted(hour_patterns.items(), key=lambda x: x[1]['pf'], reverse=True)
                best_hour = sorted_hours[0] if sorted_hours else None
                worst_hour = sorted_hours[-1] if sorted_hours else None
                
                if best_hour and best_hour[1]['pf'] >= 1.3:
                    recommendations.append(f"✅ Лучшее время: {best_hour[0]} UTC (PF={best_hour[1]['pf']:.2f})")
                if worst_hour and worst_hour[1]['pf'] < 1.0:
                    recommendations.append(f"❌ Худшее время: {worst_hour[0]} UTC (PF={worst_hour[1]['pf']:.2f})")
            
            # Выходные
            if 'Сб' in day_patterns or 'Вс' in day_patterns:
                weekend_pfs = [day_patterns[d]['pf'] for d in ['Сб', 'Вс'] if d in day_patterns]
                if weekend_pfs:
                    avg_weekend_pf = sum(weekend_pfs) / len(weekend_pfs)
                    if avg_weekend_pf < 0.9:
                        recommendations.append(f"❌ Выходные убыточны (PF={avg_weekend_pf:.2f})")
            
            # Направление
            if direction_patterns:
                for direction, data in direction_patterns.items():
                    if data['pf'] < 0.8:
                        recommendations.append(f"❌ {direction} убыточен (PF={data['pf']:.2f})")
                    elif data['pf'] >= 1.5:
                        recommendations.append(f"✅ {direction} прибылен (PF={data['pf']:.2f})")
            
            if recommendations:
                for rec in recommendations:
                    lines.append(f"  {rec}")
            else:
                lines.append("  ℹ️ Нет явных паттернов (нужно больше данных)")
            
            lines.append("="*45)
            
            return {
                'success': True,
                'total_trades': len(trades),
                'overall_pf': overall_pf,
                'overall_wr': overall_wr,
                'hour_patterns': hour_patterns,
                'day_patterns': day_patterns,
                'direction_patterns': direction_patterns,
                'recommendations': recommendations,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            logger.error(f"[TOOLS] analyze_real_patterns error: {e}")
            import traceback
            traceback.print_exc()
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def apply_best_grid_params(self) -> Dict:
        """Применить лучшие параметры из Grid Search"""
        try:
            if not hasattr(self, '_last_grid_best') or not self._last_grid_best:
                return {'error': 'No grid results', 'summary': '❌ Сначала запусти [TOOL:GRID_OPTIMIZE:30]'}
            
            params = self._last_grid_best
            
            self.trader.update_settings({
                'stop_loss_pct': params['sl'],
                'take_profit_pct': params['tp'],
                'trailing_activation_pct': params['trail_act'],
                'trailing_distance_pct': params['trail_dist']
            })
            
            return {
                'success': True,
                'summary': f"""✅ **Лучшие параметры применены!**
                
• SL: {params['sl']}%
• TP: {params['tp']}%
• Trail Activation: {params['trail_act']}%
• Trail Distance: {params['trail_dist']}%

Profit Factor был: {params['pf']:.2f}"""
            }
            
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}


    def save_optimized_strategy(self, name: str, description: str = "") -> Dict:
        """
        Сохранить текущую стратегию с новым названием
        
        Args:
            name: Название стратегии (например "Aggressive_v2", "Conservative_Jan2026")
            description: Описание стратегии
        """
        try:
            if not self.brain:
                return {'error': 'Brain not available'}
            if not self.trader:
                return {'error': 'Trader not available'}
            
            # Получаем текущие параметры
            current = self.get_current_strategy()
            params = current.get('parameters', {})
            
            # Сохраняем как новую стратегию
            result = self.brain.save_strategy(
                name=name,
                description=description or f"Оптимизированная стратегия от {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                parameters=params
            )
            
            if result:
                lines = [f"✅ **Стратегия сохранена: {name}**\n"]
                lines.append("**Параметры:**")
                lines.append(f"  • RSI SHORT: â‰¥{params.get('rsi_overbought', 70)}")
                lines.append(f"  • RSI LONG: â‰¤{params.get('rsi_oversold', 30)}")
                lines.append(f"  • Stop Loss: {params.get('stop_loss_pct', 5.0)}%")
                lines.append(f"  • Take Profit: {params.get('take_profit_pct', 7.0)}%")
                lines.append(f"  • Trailing: {'✅' if params.get('trailing_enabled', True) else 'âŒ'}")
                lines.append(f"\n💡 Чтобы загрузить: `загрузи стратегию {name}`")
                
                return {
                    'success': True,
                    'name': name,
                    'parameters': params,
                    'summary': '\n'.join(lines)
                }
            else:
                return {'error': 'Failed to save strategy', 'summary': f"❌ Не удалось сохранить стратегию {name}"}
                
        except Exception as e:
            logger.error(f"[TOOLS] save_optimized_strategy error: {e}")
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def _analyze_rsi_entries(self, trades: List[Dict]) -> Dict:
        """Анализ входов по RSI"""
        # Группируем по RSI диапазонам (упрощённо)
        # В реальности нужно хранить RSI при входе в БД
        return {}  # TODO: implement with actual RSI data
    
    def _analyze_sl_tp(self, trades: List[Dict], current_sl: float = 2.0, current_tp: float = 3.0) -> Dict:
        """Анализ эффективности SL/TP"""
        sl_hits = [t for t in trades if 'STOP' in str(t.get('close_reason', '')).upper()]
        tp_hits = [t for t in trades if 'TP' in str(t.get('close_reason', '')).upper() or 'TAKE' in str(t.get('close_reason', '')).upper()]
        
        result = {}
        
        # SL срабатывает слишком часто
        if len(sl_hits) > len(tp_hits) * 2:
            new_sl = round(current_sl + 0.5, 1)  # Увеличиваем на 0.5%
            if new_sl <= 4.0 and new_sl != current_sl:  # Не больше 4%
                result['recommendation'] = f'SL срабатывает слишком часто ({len(sl_hits)} vs {len(tp_hits)} TP). Увеличиваем.'
                result['new_sl'] = new_sl
        
        # TP слишком близко (меньше или равен SL)
        if current_tp <= current_sl:
            new_tp = round(current_sl * 1.5, 1)
            result['recommendation'] = f'TP ({current_tp}%) ≤ SL ({current_sl}%) — математически убыточно!'
            result['new_tp'] = new_tp
        
        return result
    
    def _analyze_coins(self, trades: List[Dict]) -> Dict:
        """Анализ монет"""
        coin_stats = {}
        
        for t in trades:
            symbol = t.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
            if symbol not in coin_stats:
                coin_stats[symbol] = {'trades': 0, 'wins': 0, 'pnl': 0}
            
            coin_stats[symbol]['trades'] += 1
            if t.get('pnl_usdt', 0) > 0:
                coin_stats[symbol]['wins'] += 1
            coin_stats[symbol]['pnl'] += t.get('pnl_usdt', 0)
        
        # Находим проблемные монеты
        blacklist = []
        for symbol, stats in coin_stats.items():
            if stats['trades'] >= 5:  # Минимум 5 сделок
                wr = stats['wins'] / stats['trades'] * 100
                if wr < 30:  # WR < 30%
                    blacklist.append({
                        'symbol': symbol,
                        'win_rate': wr,
                        'trades': stats['trades'],
                        'pnl': stats['pnl']
                    })
        
        return {'blacklist': sorted(blacklist, key=lambda x: x['win_rate'])}
    
    # =========================================================================
    # LIVE TRADING TOOLS - Инструменты реальной торговли v6.0
    # =========================================================================
    
    def live_status(self) -> Dict:
        """
        [TOOL:LIVE_STATUS] - Статус реальной торговли
        
        Показывает:
        - Подключение к Binance
        - Баланс USDT
        - Открытые LIVE позиции
        - Дневной/недельный PnL
        - Лимиты безопасности
        """
        try:
            from binance_live import live_trader
            
            stats = live_trader.get_stats()
            balance = live_trader.get_balance()
            positions = live_trader.get_positions()
            
            lines = ["=" * 45]
            lines.append("⚡ LIVE TRADING STATUS")
            lines.append("=" * 45)
            
            # Подключение
            if stats.get('connected'):
                mode = "TESTNET" if stats.get('testnet') else "MAINNET"
                lines.append(f"✅ Подключено к Binance {mode}")
            else:
                lines.append("❌ Нет подключения к Binance")
            
            # Статус
            lines.append(f"🔘 Live Trading: {'ВКЛЮЧЕН ⚡' if stats.get('enabled') else 'ВЫКЛЮЧЕН'}")
            
            # Баланс
            lines.append(f"\n💰 БАЛАНС:")
            lines.append(f"  • Свободно: ${balance.get('free', 0):.2f}")
            lines.append(f"  • Всего: ${balance.get('total', 0):.2f}")
            lines.append(f"  • В позициях: ${balance.get('used', 0):.2f}")
            
            # PnL
            lines.append(f"\n📊 PnL:")
            lines.append(f"  • Сегодня: ${stats.get('daily_pnl', 0):+.2f}")
            lines.append(f"  • Неделя: ${stats.get('weekly_pnl', 0):+.2f}")
            lines.append(f"  • Всего: ${stats.get('total_pnl', 0):+.2f}")
            
            # Статистика
            total = stats.get('total_trades', 0)
            wins = stats.get('winning_trades', 0)
            wr = (wins / total * 100) if total > 0 else 0
            lines.append(f"\n📈 Статистика:")
            lines.append(f"  • Сделок: {total}")
            lines.append(f"  • Win Rate: {wr:.0f}%")
            lines.append(f"  • LONG: {stats.get('long_trades', 0)} (${stats.get('long_pnl', 0):+.2f})")
            lines.append(f"  • SHORT: {stats.get('short_trades', 0)} (${stats.get('short_pnl', 0):+.2f})")
            
            # Позиции
            lines.append(f"\n🔴 LIVE Позиции: {len(positions)}")
            for p in positions:
                emoji = "🟢" if p['side'] == 'LONG' else "🔴"
                lines.append(f"  {emoji} {p['symbol'].replace('/USDT:USDT', '')} {p['side']}")
                lines.append(f"     Entry: ${p['entry_price']:.6f} | PnL: ${p['pnl_usdt']:+.2f}")
            
            # Лимиты
            config = stats.get('config', {})
            lines.append(f"\n🛡️ Лимиты:")
            lines.append(f"  • Max позиция: ${config.get('MAX_POSITION_SIZE_USD', 100)}")
            lines.append(f"  • Max позиций: {config.get('MAX_POSITIONS', 3)}")
            lines.append(f"  • Max дневной убыток: ${config.get('MAX_DAILY_LOSS_USD', 50)}")
            
            lines.append("=" * 45)
            
            return {
                'success': True,
                'stats': stats,
                'balance': balance,
                'positions': positions,
                'summary': '\n'.join(lines)
            }
            
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def live_enable(self, enable: bool = True) -> Dict:
        """
        [TOOL:LIVE_ENABLE:true/false] - Включить/выключить реальную торговлю
        
        Args:
            enable: True для включения, False для выключения
        """
        try:
            from binance_live import live_trader
            
            # Проверяем подключение
            if enable and not live_trader.connected:
                return {
                    'error': 'Not connected',
                    'summary': '❌ Сначала настройте API ключи Binance!'
                }
            
            live_trader.enable(enable)
            
            status = "ВКЛЮЧЕНА ⚡" if enable else "ВЫКЛЮЧЕНА"
            emoji = "✅" if enable else "🔴"
            
            return {
                'success': True,
                'enabled': enable,
                'summary': f"{emoji} Реальная торговля {status}"
            }
            
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def live_open(self, symbol: str, side: str, size_usdt: float = None,
                  stop_loss_pct: float = None, take_profit_pct: float = None) -> Dict:
        """
        [TOOL:LIVE_OPEN:SYMBOL:SIDE:SIZE] - Открыть реальную позицию
        
        Args:
            symbol: Торговая пара (BTC, ETH, SOL и т.д.)
            side: LONG или SHORT
            size_usdt: Размер в USDT (по умолчанию из настроек)
            stop_loss_pct: SL в % (по умолчанию из настроек)
            take_profit_pct: TP в % (по умолчанию из настроек)
        """
        try:
            from binance_live import live_trader
            
            if not live_trader.enabled:
                return {
                    'error': 'Live trading disabled',
                    'summary': '❌ Реальная торговля выключена!\nИспользуй [TOOL:LIVE_ENABLE:true]'
                }
            
            # Получаем настройки
            if self.trader:
                settings = self.trader.get_settings()
                size_usdt = size_usdt or settings.get('position_size_live', 10)
                stop_loss_pct = stop_loss_pct or settings.get('stop_loss_pct', 3.5)
                take_profit_pct = take_profit_pct or settings.get('take_profit_pct', 7)
                leverage = settings.get('leverage', 5)
            else:
                size_usdt = size_usdt or 10
                stop_loss_pct = stop_loss_pct or 3.5
                take_profit_pct = take_profit_pct or 7
                leverage = 5
            
            # Нормализуем символ
            full_symbol = self._normalize_symbol(symbol)
            side = side.upper()
            
            # Получаем текущую цену
            ticker = self.exchange.fetch_ticker(full_symbol)
            current_price = float(ticker.get('last', 0))
            
            if current_price <= 0:
                return {'error': 'Invalid price', 'summary': f'❌ Не удалось получить цену {symbol}'}
            
            # Рассчитываем SL/TP
            if side == "LONG":
                stop_loss = current_price * (1 - stop_loss_pct / 100)
                take_profit = current_price * (1 + take_profit_pct / 100)
            else:  # SHORT
                stop_loss = current_price * (1 + stop_loss_pct / 100)
                take_profit = current_price * (1 - take_profit_pct / 100)
            
            # Открываем позицию
            success, message, position = live_trader.open_position(
                symbol=full_symbol,
                side=side,
                size_usdt=size_usdt,
                stop_loss=stop_loss,
                take_profit=take_profit,
                leverage=leverage,
                reason="Agent command"
            )
            
            if success:
                emoji = "🟢" if side == "LONG" else "🔴"
                return {
                    'success': True,
                    'position': position.__dict__ if hasattr(position, '__dict__') else position,
                    'summary': f"""{emoji} **LIVE {side} ОТКРЫТ**

📍 {symbol.replace('/USDT:USDT', '')}
💰 Размер: ${size_usdt:.2f}
📈 Entry: ${current_price:.6f}
🛑 SL: ${stop_loss:.6f} (-{stop_loss_pct}%)
🎯 TP: ${take_profit:.6f} (+{take_profit_pct}%)
⚡ Плечо: x{leverage}

⚠️ РЕАЛЬНЫЕ ДЕНЬГИ!"""
                }
            else:
                return {
                    'error': message,
                    'summary': f'❌ Не удалось открыть позицию: {message}'
                }
                
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def live_close(self, position_id: str = None, symbol: str = None, reason: str = "Agent") -> Dict:
        """
        [TOOL:LIVE_CLOSE:ID] или [TOOL:LIVE_CLOSE_ALL] - Закрыть LIVE позицию
        
        Args:
            position_id: ID позиции (или 'ALL' для закрытия всех)
            symbol: Символ для закрытия (если не указан ID)
            reason: Причина закрытия
        """
        try:
            from binance_live import live_trader
            
            # Закрыть все
            if position_id and position_id.upper() == 'ALL':
                closed, total_pnl = live_trader.close_all_positions(reason)
                emoji = "✅" if total_pnl >= 0 else "❌"
                return {
                    'success': True,
                    'closed': closed,
                    'total_pnl': total_pnl,
                    'summary': f"""{emoji} **ЗАКРЫТО {closed} LIVE ПОЗИЦИЙ**

💰 Общий PnL: ${total_pnl:+.2f}
📝 Причина: {reason}"""
                }
            
            # Найти позицию по символу
            if not position_id and symbol:
                full_symbol = self._normalize_symbol(symbol)
                for pid, pos in live_trader.positions.items():
                    if pos.symbol == full_symbol:
                        position_id = pid
                        break
            
            if not position_id:
                return {'error': 'Position not found', 'summary': '❌ Позиция не найдена'}
            
            # Закрываем позицию
            success, message, pnl = live_trader.close_position(position_id, reason)
            
            if success:
                emoji = "✅" if pnl >= 0 else "❌"
                return {
                    'success': True,
                    'pnl': pnl,
                    'summary': f"""{emoji} **LIVE ПОЗИЦИЯ ЗАКРЫТА**

💰 PnL: ${pnl:+.2f}
📝 {message}"""
                }
            else:
                return {'error': message, 'summary': f'❌ {message}'}
                
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def live_balance(self) -> Dict:
        """
        [TOOL:LIVE_BALANCE] - Баланс на Binance
        """
        try:
            from binance_live import live_trader
            
            if not live_trader.connected:
                return {
                    'error': 'Not connected',
                    'summary': '❌ Нет подключения к Binance'
                }
            
            balance = live_trader.get_balance()
            
            return {
                'success': True,
                'balance': balance,
                'summary': f"""💰 **БАЛАНС BINANCE**

• Свободно: ${balance.get('free', 0):.2f} USDT
• Всего: ${balance.get('total', 0):.2f} USDT
• В позициях: ${balance.get('used', 0):.2f} USDT"""
            }
            
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def live_test_connection(self) -> Dict:
        """
        [TOOL:LIVE_TEST] - Тест подключения к Binance
        """
        try:
            from binance_live import live_trader
            
            success, message, info = live_trader.test_connection()
            
            if success:
                return {
                    'success': True,
                    'info': info,
                    'summary': f"""✅ **ПОДКЛЮЧЕНИЕ К BINANCE**

{message}

📊 Информация:
• Свободно: ${info.get('usdt_free', 0):.2f}
• Открытых позиций: {info.get('open_positions', 0)}
• Можно торговать: {'✅' if info.get('can_trade') else '❌'}
• Мин. ордер: ${info.get('min_order', 5):.2f}"""
                }
            else:
                return {
                    'error': message,
                    'summary': f'❌ {message}'
                }
                
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def live_update_sl(self, position_id: str, new_sl_pct: float) -> Dict:
        """
        [TOOL:LIVE_SL:ID:PCT] - Обновить Stop Loss LIVE позиции
        
        Args:
            position_id: ID позиции
            new_sl_pct: Новый SL в % от текущей цены
        """
        try:
            from binance_live import live_trader
            
            if position_id not in live_trader.positions:
                return {'error': 'Position not found', 'summary': '❌ Позиция не найдена'}
            
            position = live_trader.positions[position_id]
            
            # Рассчитываем новый SL
            if position.side == "LONG":
                new_sl = position.current_price * (1 - new_sl_pct / 100)
            else:
                new_sl = position.current_price * (1 + new_sl_pct / 100)
            
            success, message = live_trader.update_stop_loss(position_id, new_sl)
            
            if success:
                return {
                    'success': True,
                    'summary': f"""✅ **SL ОБНОВЛЕН**

📍 {position.symbol.replace('/USDT:USDT', '')}
🛑 Новый SL: ${new_sl:.6f}
📊 Расстояние: {new_sl_pct}%"""
                }
            else:
                return {'error': message, 'summary': f'❌ {message}'}
                
        except Exception as e:
            return {'error': str(e), 'summary': f"❌ Ошибка: {e}"}
    
    def get_all_tools_help(self) -> str:
        """
        Возвращает справку по всем инструментам агента
        """
        help_text = """
═══════════════════════════════════════════════════════════════
📚 ПОЛНЫЙ СПРАВОЧНИК ИНСТРУМЕНТОВ АГЕНТА v6.0
═══════════════════════════════════════════════════════════════

🔴 PAPER TRADING (виртуальная торговля)
───────────────────────────────────────────────────────────────
[TOOL:POSITIONS]           - Показать открытые позиции
[TOOL:CLOSE:RVV-0012]      - Закрыть позицию по ID
[TOOL:CLOSE_ALL]           - Закрыть все позиции
[TOOL:PAUSE:true/false]    - Пауза сканера

⚙️ СТРАТЕГИЯ
───────────────────────────────────────────────────────────────
[TOOL:STRATEGY]            - Текущие параметры стратегии
[TOOL:STRATEGY:SET:sl:3.5] - Изменить Stop Loss %
[TOOL:STRATEGY:SET:tp:7.0] - Изменить Take Profit %
[TOOL:STRATEGY:SET:trailing_activation:1.0]
[TOOL:STRATEGY:SET:trailing_distance:0.25]
[TOOL:SET_PARAM:btc_filter:false] - Выключить BTC фильтр
[TOOL:SET_PARAM:btc_filter:true]  - Включить BTC фильтр

📈 ОПТИМИЗАЦИЯ
───────────────────────────────────────────────────────────────
[TOOL:GRID_OPTIMIZE:30:100]  - Grid Search 30 дней, топ 100
[TOOL:APPLY_BEST]            - Применить лучшие параметры
[TOOL:WFA:30:100]            - Walk-Forward Analysis

🧪 БЭКТЕСТ
───────────────────────────────────────────────────────────────
[TOOL:BACKTEST:current:30:500]   - Бэктест текущей стратегии
[TOOL:BACKTEST_PATTERNS:60:200]  - Бэктест с анализом паттернов
[TOOL:ANALYZE_PATTERNS:30]       - Анализ реальных сделок

📊 АНАЛИТИКА
───────────────────────────────────────────────────────────────
[TOOL:MARKET]              - Обзор рынка и топ пампов
[TOOL:STATS:30]            - Статистика за N дней
[TOOL:TOP:50]              - Топ N монет по объёму

📉 ПАМЯТЬ И BLACKLIST
───────────────────────────────────────────────────────────────
[TOOL:BLACKLIST:SYMBOL:REASON]  - Добавить в чёрный список
[TOOL:BLACKLIST_WORST]          - Добавить худшие из бэктеста
[TOOL:WHITELIST_PROFITABLE]     - Только прибыльные монеты
[TOOL:MEMORY]                   - Показать память агента

⚡ LIVE TRADING (реальная торговля) ⚠️ ОСТОРОЖНО!
───────────────────────────────────────────────────────────────
[TOOL:LIVE_STATUS]              - Статус реальной торговли
[TOOL:LIVE_TEST]                - Тест подключения Binance
[TOOL:LIVE_BALANCE]             - Баланс на Binance
[TOOL:LIVE_ENABLE:true]         - ВКЛЮЧИТЬ реальную торговлю
[TOOL:LIVE_ENABLE:false]        - Выключить реальную торговлю
[TOOL:LIVE_OPEN:BTC:LONG:20]    - Открыть LIVE позицию
[TOOL:LIVE_OPEN:ETH:SHORT:15]   - Открыть SHORT на $15
[TOOL:LIVE_CLOSE:LIVE-123]      - Закрыть LIVE позицию
[TOOL:LIVE_CLOSE_ALL]           - Закрыть ВСЕ LIVE позиции
[TOOL:LIVE_SL:LIVE-123:2.0]     - Обновить SL позиции

═══════════════════════════════════════════════════════════════
⚠️ ВАЖНО: LIVE команды работают с РЕАЛЬНЫМИ деньгами!
   Сначала протестируй стратегию в PAPER режиме!
═══════════════════════════════════════════════════════════════
"""
        return help_text
