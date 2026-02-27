# -*- coding: utf-8 -*-
"""
RVV Hunter v6.0 - Binance Live Trading Module
Реальная торговля через Binance Futures API
SHORT и LONG позиции с жесткими лимитами безопасности
"""

import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    logger.warning("ccxt не установлен. Установите: pip install ccxt")
    CCXT_AVAILABLE = False


# ============================================================================
# КОНСТАНТЫ БЕЗОПАСНОСТИ - НЕ ИЗМЕНЯТЬ!
# ============================================================================

MAX_POSITION_SIZE_USD = 100.0     # Максимальный размер позиции в USD (реалистично для торговли)
MAX_POSITIONS = 5                  # Максимум одновременных позиций
MAX_DAILY_LOSS_USD = 50.0         # Максимальный дневной убыток
MAX_WEEKLY_LOSS_USD = 200.0       # Максимальный недельный убыток
DEFAULT_LEVERAGE = 5               # Плечо по умолчанию
MIN_ORDER_SIZE_USD = 10.0         # Минимальный размер ордера (Binance USDM требует $10)


@dataclass
class LivePosition:
    """Реальная позиция на бирже"""
    id: str
    symbol: str
    side: str  # Всегда 'SHORT'
    entry_price: float
    current_price: float
    size: float  # Размер в базовой валюте
    size_usdt: float
    leverage: int
    stop_loss: float
    take_profit: float
    pnl_usdt: float = 0.0
    pnl_percent: float = 0.0
    opened_at: str = ""
    order_ids: Dict = None  # SL/TP order IDs
    
    def __post_init__(self):
        if self.order_ids is None:
            self.order_ids = {}


class BinanceLiveTrader:
    """
    Менеджер реальной торговли на Binance Futures
    
    ВАЖНО: Этот модуль работает с реальными деньгами!
    Все операции проходят через несколько уровней защиты.
    """
    
    def __init__(self, api_key: str = None, api_secret: str = None, testnet: bool = False):
        """
        Инициализация
        
        Args:
            api_key: Binance API ключ
            api_secret: Binance API секрет
            testnet: Использовать тестовую сеть
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.exchange = None
        self.connected = False
        
        self.positions: Dict[str, LivePosition] = {}
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.daily_date = ""
        self.weekly_start = ""
        
        self.lock = threading.Lock()
        self.enabled = False
        
        self.stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'last_trade_time': None
        }
        
        if api_key and api_secret:
            self._init_exchange()
    
    def _init_exchange(self):
        """Инициализация подключения к бирже"""
        if not CCXT_AVAILABLE:
            logger.error("[LIVE] ccxt не установлен")
            return False
        
        try:
            options = {
                'defaultType': 'future',
                'adjustForTimeDifference': True,
                'recvWindow': 60000,
            }
            
            import os
            use_tor = os.getenv('USE_TOR', '1') != '0'
            tor_proxies = {
                'http': 'socks5h://127.0.0.1:9050',
                'https': 'socks5h://127.0.0.1:9050',
            } if use_tor else {}

            if self.testnet:
                cfg = {
                    'apiKey': self.api_key,
                    'secret': self.api_secret,
                    'enableRateLimit': True,
                    'options': options,
                    'urls': {
                        'api': {
                            'public': 'https://testnet.binancefuture.com/fapi/v1',
                            'private': 'https://testnet.binancefuture.com/fapi/v1',
                        }
                    }
                }
                if tor_proxies:
                    cfg['proxies'] = tor_proxies
                self.exchange = ccxt.binance(cfg)
                logger.info("[LIVE] Инициализация Binance TESTNET" + (" + Tor" if use_tor else ""))
            else:
                cfg = {
                    'apiKey': self.api_key,
                    'secret': self.api_secret,
                    'enableRateLimit': True,
                    'options': options
                }
                if tor_proxies:
                    cfg['proxies'] = tor_proxies
                self.exchange = ccxt.binance(cfg)
                logger.info("[LIVE] Инициализация Binance MAINNET" + (" + Tor" if use_tor else ""))
            
            self.connected = True
            return True
            
        except Exception as e:
            logger.error(f"[LIVE] Ошибка инициализации: {e}")
            self.connected = False
            return False
    
    def configure(self, api_key: str, api_secret: str, testnet: bool = False) -> bool:
        """Настройка API ключей"""
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        return self._init_exchange()
    
    def test_connection(self) -> Tuple[bool, str, Dict]:
        """
        Тестирование подключения к Binance
        
        Returns:
            (success, message, info)
        """
        if not self.exchange:
            return False, "Exchange не инициализирован", {}
        
        try:
            # Проверяем баланс
            balance = self.exchange.fetch_balance()
            
            # Получаем USDT баланс
            usdt_balance = balance.get('USDT', {})
            free = usdt_balance.get('free', 0)
            total = usdt_balance.get('total', 0)
            
            # Проверяем позиции
            positions = self.exchange.fetch_positions()
            open_positions = [p for p in positions if float(p.get('contracts', 0)) > 0]
            
            info = {
                'usdt_free': free,
                'usdt_total': total,
                'open_positions': len(open_positions),
                'testnet': self.testnet
            }
            
            mode = "TESTNET" if self.testnet else "MAINNET"
            msg = f"✅ Подключено к Binance {mode}. Баланс: ${total:.2f} USDT"
            
            logger.info(f"[LIVE] {msg}")
            return True, msg, info
            
        except ccxt.AuthenticationError as e:
            logger.error(f"[LIVE] Ошибка аутентификации: {e}")
            return False, f"Ошибка аутентификации: неверные API ключи", {}
            
        except ccxt.NetworkError as e:
            logger.error(f"[LIVE] Сетевая ошибка: {e}")
            return False, f"Сетевая ошибка: {str(e)[:50]}", {}
            
        except Exception as e:
            logger.error(f"[LIVE] Ошибка подключения: {e}")
            return False, f"Ошибка: {str(e)[:50]}", {}
    
    def get_balance(self) -> Dict:
        """Получить баланс"""
        if not self.exchange or not self.connected:
            return {'free': 0, 'total': 0, 'used': 0}
        
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get('USDT', {})
            return {
                'free': float(usdt.get('free', 0)),
                'total': float(usdt.get('total', 0)),
                'used': float(usdt.get('used', 0))
            }
        except Exception as e:
            logger.error(f"[LIVE] Ошибка получения баланса: {e}")
            return {'free': 0, 'total': 0, 'used': 0}
    
    def _check_safety_limits(self, size_usdt: float) -> Tuple[bool, str]:
        """
        Проверка лимитов безопасности
        
        Returns:
            (can_trade, reason)
        """
        # 1. Проверка размера позиции
        if size_usdt > MAX_POSITION_SIZE_USD:
            return False, f"Размер позиции ${size_usdt:.2f} > лимит ${MAX_POSITION_SIZE_USD}"
        
        if size_usdt < MIN_ORDER_SIZE_USD:
            return False, f"Размер позиции ${size_usdt:.2f} < минимум ${MIN_ORDER_SIZE_USD}"
        
        # 2. Проверка количества позиций
        if len(self.positions) >= MAX_POSITIONS:
            return False, f"Достигнут лимит позиций: {MAX_POSITIONS}"
        
        # 3. Проверка дневного убытка
        today = datetime.utcnow().date().isoformat()
        if self.daily_date != today:
            self.daily_date = today
            self.daily_pnl = 0.0
        
        if self.daily_pnl <= -MAX_DAILY_LOSS_USD:
            return False, f"Достигнут дневной лимит убытков: ${MAX_DAILY_LOSS_USD}"
        
        # 4. Проверка недельного убытка
        week_start = (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())).date().isoformat()
        if self.weekly_start != week_start:
            self.weekly_start = week_start
            self.weekly_pnl = 0.0
        
        if self.weekly_pnl <= -MAX_WEEKLY_LOSS_USD:
            return False, f"Достигнут недельный лимит убытков: ${MAX_WEEKLY_LOSS_USD}"
        
        return True, "OK"
    
    def _set_leverage(self, symbol: str, leverage: int = DEFAULT_LEVERAGE) -> bool:
        """Установить плечо для символа"""
        try:
            # Конвертируем символ для Binance
            binance_symbol = symbol.replace('/USDT:USDT', 'USDT').replace('/USDT', 'USDT').replace('/', '')
            
            self.exchange.set_leverage(leverage, binance_symbol)
            logger.info(f"[LIVE] Установлено плечо {leverage}x для {binance_symbol}")
            return True
            
        except Exception as e:
            logger.error(f"[LIVE] Ошибка установки плеча: {e}")
            return False
    
    def open_short(self, symbol: str, size_usdt: float, entry_price: float,
                   stop_loss: float, take_profit: float,
                   confirm_callback=None) -> Tuple[bool, str, Optional[LivePosition]]:
        """
        Открыть SHORT позицию
        
        Args:
            symbol: Торговая пара (например, "BTC/USDT:USDT")
            size_usdt: Размер позиции в USDT
            entry_price: Ожидаемая цена входа (для расчета количества)
            stop_loss: Цена Stop Loss
            take_profit: Цена Take Profit
            confirm_callback: Функция для подтверждения (опционально)
            
        Returns:
            (success, message, position)
        """
        if not self.enabled:
            return False, "Live trading отключен", None
        
        if not self.exchange or not self.connected:
            return False, "Не подключено к бирже", None
        
        with self.lock:
            # 1. Проверка лимитов
            can_trade, reason = self._check_safety_limits(size_usdt)
            if not can_trade:
                logger.warning(f"[LIVE] Отклонено: {reason}")
                return False, reason, None
            
            # 2. Валидация уровней для SHORT
            if stop_loss <= entry_price:
                return False, f"SL ({stop_loss}) должен быть > entry ({entry_price}) для SHORT", None
            
            if take_profit >= entry_price:
                return False, f"TP ({take_profit}) должен быть < entry ({entry_price}) для SHORT", None
            
            # 3. Подтверждение (если требуется)
            if confirm_callback:
                confirmed = confirm_callback(
                    symbol=symbol,
                    size_usdt=size_usdt,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )
                if not confirmed:
                    return False, "Отменено пользователем", None
            
            try:
                # 4. Конвертируем символ
                binance_symbol = symbol.replace('/USDT:USDT', 'USDT').replace('/USDT', 'USDT').replace('/', '')
                
                # 5. Получаем текущую цену
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # 6. Рассчитываем количество
                amount = size_usdt / current_price
                
                # 7. Получаем информацию о минимальном лоте
                market = self.exchange.market(symbol)
                min_amount = market.get('limits', {}).get('amount', {}).get('min', 0)
                amount_precision = market.get('precision', {}).get('amount', 8)
                
                # Округляем количество
                amount = round(amount, amount_precision)
                
                if amount < min_amount:
                    return False, f"Количество {amount} меньше минимума {min_amount}", None
                
                # 8. Устанавливаем плечо
                self._set_leverage(symbol, DEFAULT_LEVERAGE)
                
                # 9. Открываем SHORT (market order)
                logger.info(f"[LIVE] Открытие SHORT: {symbol} amount={amount} price~{current_price}")
                
                order = self.exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side='sell',  # SHORT = sell
                    amount=amount,
                    params={'reduceOnly': False}
                )
                
                actual_price = float(order.get('average', current_price))
                order_id = order.get('id', '')
                
                logger.info(f"[LIVE] Ордер исполнен: {order_id} @ ${actual_price}")
                
                # 10. Создаем позицию
                position_id = f"LIVE-{int(time.time())}"
                position = LivePosition(
                    id=position_id,
                    symbol=symbol,
                    side='SHORT',
                    entry_price=actual_price,
                    current_price=actual_price,
                    size=amount,
                    size_usdt=amount * actual_price,
                    leverage=DEFAULT_LEVERAGE,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    opened_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    order_ids={'entry': order_id}
                )
                
                # 11. Устанавливаем SL/TP ордера
                self._set_stop_orders(position)
                
                self.positions[position_id] = position
                self.stats['total_trades'] += 1
                self.stats['last_trade_time'] = datetime.utcnow().isoformat()
                
                logger.info(f"[LIVE] ✅ SHORT открыт: {symbol} @ ${actual_price} (SL: ${stop_loss}, TP: ${take_profit})")
                
                return True, f"SHORT открыт @ ${actual_price}", position
                
            except ccxt.InsufficientFunds as e:
                logger.error(f"[LIVE] Недостаточно средств: {e}")
                return False, "Недостаточно средств", None
                
            except ccxt.InvalidOrder as e:
                logger.error(f"[LIVE] Неверный ордер: {e}")
                return False, f"Ошибка ордера: {str(e)[:50]}", None
                
            except Exception as e:
                logger.error(f"[LIVE] Ошибка открытия позиции: {e}")
                return False, f"Ошибка: {str(e)[:50]}", None
    
    def open_long(self, symbol: str, size_usdt: float, entry_price: float,
                  stop_loss: float, take_profit: float,
                  confirm_callback=None) -> Tuple[bool, str, Optional[LivePosition]]:
        """
        Открыть LONG позицию
        
        Args:
            symbol: Торговая пара (например, "BTC/USDT:USDT")
            size_usdt: Размер позиции в USDT
            entry_price: Ожидаемая цена входа
            stop_loss: Цена Stop Loss (ниже entry для LONG)
            take_profit: Цена Take Profit (выше entry для LONG)
            confirm_callback: Функция для подтверждения (опционально)
            
        Returns:
            (success, message, position)
        """
        if not self.enabled:
            return False, "Live trading отключен", None
        
        if not self.exchange or not self.connected:
            return False, "Не подключено к бирже", None
        
        with self.lock:
            # 1. Проверка лимитов
            can_trade, reason = self._check_safety_limits(size_usdt)
            if not can_trade:
                logger.warning(f"[LIVE] Отклонено: {reason}")
                return False, reason, None
            
            # 2. Валидация уровней для LONG
            if stop_loss >= entry_price:
                return False, f"SL ({stop_loss}) должен быть < entry ({entry_price}) для LONG", None
            
            if take_profit <= entry_price:
                return False, f"TP ({take_profit}) должен быть > entry ({entry_price}) для LONG", None
            
            # 3. Подтверждение (если требуется)
            if confirm_callback:
                confirmed = confirm_callback(
                    symbol=symbol,
                    size_usdt=size_usdt,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )
                if not confirmed:
                    return False, "Отменено пользователем", None
            
            try:
                # 4. Конвертируем символ
                binance_symbol = symbol.replace('/USDT:USDT', 'USDT').replace('/USDT', 'USDT').replace('/', '')
                
                # 5. Получаем текущую цену
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # 6. Рассчитываем количество
                amount = size_usdt / current_price
                
                # 7. Получаем информацию о минимальном лоте
                market = self.exchange.market(symbol)
                min_amount = market.get('limits', {}).get('amount', {}).get('min', 0)
                amount_precision = market.get('precision', {}).get('amount', 8)
                
                # Округляем количество
                amount = round(amount, amount_precision)
                
                if amount < min_amount:
                    return False, f"Количество {amount} меньше минимума {min_amount}", None
                
                # 8. Устанавливаем плечо
                self._set_leverage(symbol, DEFAULT_LEVERAGE)
                
                # 9. Открываем LONG (market order)
                logger.info(f"[LIVE] Открытие LONG: {symbol} amount={amount} price~{current_price}")
                
                order = self.exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side='buy',  # LONG = buy
                    amount=amount,
                    params={'reduceOnly': False}
                )
                
                actual_price = float(order.get('average', current_price))
                order_id = order.get('id', '')
                
                logger.info(f"[LIVE] Ордер исполнен: {order_id} @ ${actual_price}")
                
                # 10. Создаем позицию
                position_id = f"LIVE-{int(time.time())}"
                position = LivePosition(
                    id=position_id,
                    symbol=symbol,
                    side='LONG',
                    entry_price=actual_price,
                    current_price=actual_price,
                    size=amount,
                    size_usdt=amount * actual_price,
                    leverage=DEFAULT_LEVERAGE,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    opened_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    order_ids={'entry': order_id}
                )
                
                # 11. Устанавливаем SL/TP ордера
                self._set_stop_orders(position)
                
                self.positions[position_id] = position
                self.stats['total_trades'] += 1
                self.stats['last_trade_time'] = datetime.utcnow().isoformat()
                
                logger.info(f"[LIVE] ✅ LONG открыт: {symbol} @ ${actual_price} (SL: ${stop_loss}, TP: ${take_profit})")
                
                return True, f"LONG открыт @ ${actual_price}", position
                
            except ccxt.InsufficientFunds as e:
                logger.error(f"[LIVE] Недостаточно средств: {e}")
                return False, "Недостаточно средств", None
                
            except ccxt.InvalidOrder as e:
                logger.error(f"[LIVE] Неверный ордер: {e}")
                return False, f"Ошибка ордера: {str(e)[:50]}", None
                
            except Exception as e:
                logger.error(f"[LIVE] Ошибка открытия LONG: {e}")
                return False, f"Ошибка: {str(e)[:50]}", None
    
    def _set_stop_orders(self, position: LivePosition) -> bool:
        """Установить SL/TP ордера для SHORT и LONG"""
        try:
            symbol = position.symbol
            amount = position.size
            
            # Для SHORT: закрытие = buy. Для LONG: закрытие = sell
            close_side = 'buy' if position.side == 'SHORT' else 'sell'
            
            # Stop Loss
            sl_order = self.exchange.create_order(
                symbol=symbol,
                type='stop_market',
                side=close_side,
                amount=amount,
                params={
                    'stopPrice': position.stop_loss,
                    'reduceOnly': True
                }
            )
            position.order_ids['stop_loss'] = sl_order.get('id', '')
            logger.info(f"[LIVE] SL ордер установлен: {sl_order.get('id')}")
            
            # Take Profit
            tp_order = self.exchange.create_order(
                symbol=symbol,
                type='take_profit_market',
                side=close_side,
                amount=amount,
                params={
                    'stopPrice': position.take_profit,
                    'reduceOnly': True
                }
            )
            position.order_ids['take_profit'] = tp_order.get('id', '')
            logger.info(f"[LIVE] TP ордер установлен: {tp_order.get('id')}")
            
            return True
            
        except Exception as e:
            logger.error(f"[LIVE] Ошибка установки SL/TP: {e}")
            return False
    
    def close_position(self, position_id: str, reason: str = "MANUAL") -> Tuple[bool, str, float]:
        """
        Закрыть позицию
        
        Returns:
            (success, message, pnl)
        """
        if position_id not in self.positions:
            return False, "Позиция не найдена", 0.0
        
        with self.lock:
            position = self.positions[position_id]
            
            try:
                symbol = position.symbol
                amount = position.size
                
                # Закрываем позицию (market order)
                close_side = 'sell' if position.side == 'LONG' else 'buy'
                order = self.exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side=close_side,
                    amount=amount,
                    params={'reduceOnly': True}
                )
                
                exit_price = float(order.get('average', position.current_price))
                
                # Рассчитываем PnL
                if position.side == 'SHORT':
                    pnl_percent = (position.entry_price - exit_price) / position.entry_price * 100
                else:  # LONG
                    pnl_percent = (exit_price - position.entry_price) / position.entry_price * 100
                pnl_usdt = position.size_usdt * (pnl_percent / 100) * position.leverage
                
                # Отменяем оставшиеся ордера
                self._cancel_stop_orders(position)
                
                # Обновляем статистику
                self.daily_pnl += pnl_usdt
                self.weekly_pnl += pnl_usdt
                self.stats['total_pnl'] += pnl_usdt
                
                if pnl_usdt >= 0:
                    self.stats['winning_trades'] += 1
                else:
                    self.stats['losing_trades'] += 1
                
                del self.positions[position_id]
                
                logger.info(f"[LIVE] ✅ Позиция закрыта: {symbol} @ ${exit_price} | PnL: ${pnl_usdt:+.2f} | {reason}")
                
                return True, f"Закрыто @ ${exit_price} | PnL: ${pnl_usdt:+.2f}", pnl_usdt
                
            except Exception as e:
                logger.error(f"[LIVE] Ошибка закрытия: {e}")
                return False, f"Ошибка: {str(e)[:50]}", 0.0
    
    def _cancel_stop_orders(self, position: LivePosition):
        """Отменить SL/TP ордера"""
        try:
            for order_type, order_id in position.order_ids.items():
                if order_type in ['stop_loss', 'take_profit'] and order_id:
                    try:
                        self.exchange.cancel_order(order_id, position.symbol)
                        logger.info(f"[LIVE] Ордер отменен: {order_id}")
                    except Exception:
                        pass  # Ордер уже исполнен или отменен
        except Exception as e:
            logger.error(f"[LIVE] Ошибка отмены ордеров: {e}")
    
    def update_stop_loss(self, position_id: str, new_sl: float) -> Tuple[bool, str]:
        """Обновить Stop Loss (для трейлинга)"""
        if position_id not in self.positions:
            return False, "Позиция не найдена"
        
        with self.lock:
            position = self.positions[position_id]
            
            # Для SHORT: новый SL должен быть ниже старого (двигаем вниз)
            # Для LONG: новый SL должен быть выше старого (двигаем вверх)
            if position.side == 'SHORT':
                if new_sl >= position.stop_loss:
                    return False, f"Новый SL ({new_sl}) должен быть < текущего ({position.stop_loss}) для SHORT"
            else:  # LONG
                if new_sl <= position.stop_loss:
                    return False, f"Новый SL ({new_sl}) должен быть > текущего ({position.stop_loss}) для LONG"
            
            try:
                # Отменяем старый SL
                old_sl_id = position.order_ids.get('stop_loss')
                if old_sl_id:
                    try:
                        self.exchange.cancel_order(old_sl_id, position.symbol)
                    except Exception:
                        pass
                
                # Создаем новый SL
                close_side = 'buy' if position.side == 'SHORT' else 'sell'
                sl_order = self.exchange.create_order(
                    symbol=position.symbol,
                    type='stop_market',
                    side=close_side,
                    amount=position.size,
                    params={
                        'stopPrice': new_sl,
                        'reduceOnly': True
                    }
                )
                
                position.order_ids['stop_loss'] = sl_order.get('id', '')
                old_sl = position.stop_loss
                position.stop_loss = new_sl
                
                logger.info(f"[LIVE] SL обновлен: {position.symbol} ${old_sl} -> ${new_sl}")
                
                return True, f"SL обновлен: ${old_sl:.4f} -> ${new_sl:.4f}"
                
            except Exception as e:
                logger.error(f"[LIVE] Ошибка обновления SL: {e}")
                return False, f"Ошибка: {str(e)[:50]}"
    
    def sync_positions(self) -> int:
        """
        Синхронизация позиций с биржей
        
        Returns:
            Количество синхронизированных позиций
        """
        if not self.exchange or not self.connected:
            return 0
        
        try:
            exchange_positions = self.exchange.fetch_positions()
            
            synced = 0
            for pos in exchange_positions:
                contracts = float(pos.get('contracts', 0))
                if contracts > 0:
                    symbol = pos.get('symbol', '')
                    side = pos.get('side', '')
                    
                    # Проверяем есть ли у нас эта позиция
                    found = False
                    for p in self.positions.values():
                        if p.symbol == symbol:
                            # Обновляем данные
                            p.current_price = float(pos.get('markPrice', p.current_price))
                            p.size = contracts
                            found = True
                            synced += 1
                            break
                    
                    if not found and side in ('short', 'long'):
                        # Позиция на бирже, но не у нас - добавляем
                        logger.warning(f"[LIVE] Найдена внешняя позиция: {symbol}")
            
            return synced
            
        except Exception as e:
            logger.error(f"[LIVE] Ошибка синхронизации: {e}")
            return 0
    
    def get_positions(self) -> List[Dict]:
        """Получить список позиций"""
        return [
            {
                'id': p.id,
                'symbol': p.symbol,
                'side': p.side,
                'entry_price': p.entry_price,
                'current_price': p.current_price,
                'size': p.size,
                'size_usdt': p.size_usdt,
                'leverage': p.leverage,
                'stop_loss': p.stop_loss,
                'take_profit': p.take_profit,
                'pnl_usdt': p.pnl_usdt,
                'pnl_percent': p.pnl_percent,
                'opened_at': p.opened_at
            }
            for p in self.positions.values()
        ]
    
    def get_stats(self) -> Dict:
        """Получить статистику"""
        return {
            'enabled': self.enabled,
            'connected': self.connected,
            'testnet': self.testnet,
            'positions_count': len(self.positions),
            'daily_pnl': self.daily_pnl,
            'weekly_pnl': self.weekly_pnl,
            **self.stats
        }
    
    def enable(self, enabled: bool = True):
        """Включить/выключить live trading"""
        self.enabled = enabled
        status = "ВКЛЮЧЕН" if enabled else "ВЫКЛЮЧЕН"
        logger.info(f"[LIVE] Live trading {status}")
    
    def close_all_positions(self, reason: str = "EMERGENCY") -> Tuple[int, float]:
        """
        Закрыть все позиции (экстренно)
        
        Returns:
            (closed_count, total_pnl)
        """
        closed = 0
        total_pnl = 0.0
        
        for position_id in list(self.positions.keys()):
            success, msg, pnl = self.close_position(position_id, reason)
            if success:
                closed += 1
                total_pnl += pnl
        
        logger.info(f"[LIVE] Закрыто позиций: {closed}, PnL: ${total_pnl:+.2f}")
        return closed, total_pnl


# Глобальный экземпляр
live_trader = BinanceLiveTrader()
