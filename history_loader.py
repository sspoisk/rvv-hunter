# -*- coding: utf-8 -*-
"""
RVV Hunter v6.0 - History Loader
Загрузка исторических данных о пампах с Binance
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
import ccxt
from database import db, get_gmt2_time

logger = logging.getLogger(__name__)


class HistoryLoader:
    """Загрузчик исторических данных с Binance"""
    
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True
            }
        })
        self.is_loading = False
        self.progress = 0
        self.status = "idle"
        logger.info("[HISTORY] Loader initialized")
    
    def get_top_pairs(self, limit: int = 50) -> List[str]:
        """Получить топ пар по объёму"""
        try:
            tickers = self.exchange.fetch_tickers()
            pairs = []
            
            for symbol, ticker in tickers.items():
                if symbol.endswith('/USDT:USDT'):
                    vol = ticker.get('quoteVolume', 0) or 0
                    if vol > 0:
                        pairs.append({'symbol': symbol, 'volume': vol})
            
            pairs.sort(key=lambda x: x['volume'], reverse=True)
            return [p['symbol'] for p in pairs[:limit]]
            
        except Exception as e:
            logger.error(f"[HISTORY] Error getting pairs: {e}")
            return []
    
    def load_history(self, days: int = 30, min_change: float = 10.0, 
                     progress_callback: Callable = None) -> Dict:
        """
        Загружает историю пампов за указанный период
        
        Args:
            days: количество дней
            min_change: минимальное изменение % для записи
            progress_callback: функция для обновления прогресса
            
        Returns:
            Статистика загрузки
        """
        if self.is_loading:
            return {'error': 'Already loading'}
        
        self.is_loading = True
        self.progress = 0
        self.status = "Получение списка пар..."
        
        stats = {
            'pairs_processed': 0,
            'pumps_found': 0,
            'errors': 0,
            'started_at': get_gmt2_time().isoformat(),
        }
        
        try:
            pairs = self.get_top_pairs(50)
            
            if not pairs:
                self.status = "Ошибка: не удалось получить пары"
                return {'error': 'No pairs found'}
            
            total_pairs = len(pairs)
            logger.info(f"[HISTORY] Loading {days} days for {total_pairs} pairs")
            
            for i, symbol in enumerate(pairs):
                self.progress = int((i / total_pairs) * 100)
                self.status = f"Обработка {symbol.replace('/USDT:USDT', '')}... ({i+1}/{total_pairs})"
                
                if progress_callback:
                    progress_callback(self.progress, self.status)
                
                try:
                    pumps = self._analyze_pair_history(symbol, days, min_change)
                    
                    for pump in pumps:
                        db.save_market_pump(pump)
                        stats['pumps_found'] += 1
                    
                    stats['pairs_processed'] += 1
                    
                except Exception as e:
                    logger.error(f"[HISTORY] Error processing {symbol}: {e}")
                    stats['errors'] += 1
                
                time.sleep(0.2)
            
            self.progress = 100
            self.status = f"Готово! Найдено {stats['pumps_found']} пампов"
            stats['finished_at'] = get_gmt2_time().isoformat()
            
            logger.info(f"[HISTORY] Completed: {stats['pumps_found']} pumps from {stats['pairs_processed']} pairs")
            
        except Exception as e:
            logger.error(f"[HISTORY] Fatal error: {e}")
            stats['error'] = str(e)
            self.status = f"Ошибка: {str(e)[:50]}"
            
        finally:
            self.is_loading = False
        
        return stats
    
    def _analyze_pair_history(self, symbol: str, days: int, min_change: float) -> List[Dict]:
        """Анализирует историю одной пары"""
        pumps = []
        limit = min(days * 24, 1000)
        
        try:
            klines = self.exchange.fetch_ohlcv(symbol, '1h', limit=limit)
        except Exception as e:
            logger.error(f"[HISTORY] OHLCV error {symbol}: {e}")
            return []
        
        if not klines or len(klines) < 48:
            return []
        
        for i in range(24, len(klines)):
            try:
                current = klines[i]
                timestamp = datetime.utcfromtimestamp(current[0] / 1000)
                current_close = current[4]
                current_volume = current[5]
                
                price_24h_ago = klines[i - 24][4]
                
                if price_24h_ago <= 0:
                    continue
                
                change_24h = (current_close - price_24h_ago) / price_24h_ago * 100
                
                if abs(change_24h) >= min_change:
                    aftermath = self._analyze_aftermath(klines, i)
                    
                    pumps.append({
                        'symbol': symbol,
                        'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                        'change_24h': change_24h,
                        'price': current_close,
                        'volume': current_volume,
                        'reversal_1h': aftermath.get('reversal_1h'),
                        'reversal_4h': aftermath.get('reversal_4h'),
                        'reversal_24h': aftermath.get('reversal_24h'),
                        'max_drawdown': aftermath.get('max_drawdown'),
                        'max_profit': aftermath.get('max_profit'),
                    })
                    
            except Exception as e:
                continue
        
        return pumps
    
    def _analyze_aftermath(self, klines: List, pump_index: int) -> Dict:
        """Анализирует что произошло ПОСЛЕ пампа"""
        result = {
            'reversal_1h': None,
            'reversal_4h': None,
            'reversal_24h': None,
            'max_drawdown': None,
            'max_profit': None,
        }
        
        if pump_index >= len(klines) - 1:
            return result
        
        pump_price = klines[pump_index][4]
        
        if pump_price <= 0:
            return result
        
        max_high = pump_price
        min_low = pump_price
        
        for j in range(1, min(25, len(klines) - pump_index)):
            idx = pump_index + j
            if idx >= len(klines):
                break
            
            candle = klines[idx]
            high = candle[2]
            low = candle[3]
            close = candle[4]
            
            max_high = max(max_high, high)
            min_low = min(min_low, low)
            
            change = (close - pump_price) / pump_price * 100
            
            if j == 1:
                result['reversal_1h'] = change
            elif j == 4:
                result['reversal_4h'] = change
            elif j == 24:
                result['reversal_24h'] = change
        
        result['max_drawdown'] = (max_high - pump_price) / pump_price * 100
        result['max_profit'] = (pump_price - min_low) / pump_price * 100
        
        return result
    
    def get_status(self) -> Dict:
        """Получить текущий статус загрузки"""
        return {
            'is_loading': self.is_loading,
            'progress': self.progress,
            'status': self.status,
            'history_count': db.get_market_history_count()
        }


# Глобальный экземпляр
history_loader = HistoryLoader()
