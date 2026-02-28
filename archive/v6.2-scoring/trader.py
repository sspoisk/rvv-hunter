import json
import os
import threading
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple, Any
import logging
from database import db, get_gmt2_time, get_gmt2_str
import numpy as np

logger = logging.getLogger(__name__)
TZ_OFFSET = timedelta(hours=2)

@dataclass
class Position:
    """Позиция в портфеле"""
    # Обязательные поля
    id: str
    symbol: str
    side: str  # SHORT или LONG
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    size_usdt: float
    leverage: int
    # Поля со значениями по умолчанию
    trailing_stop: float = 0.0
    initial_stop_loss: float = 0.0
    status: str = "OPEN"
    pnl_usdt: float = 0.0
    pnl_percent: float = 0.0
    opened_at: str = ""
    closed_at: str = ""
    close_reason: str = ""
    ai_confidence: int = 0
    ai_reason: str = ""
    ai_analysis_ru: str = ""
    ai_provider: str = "deepseek"
    change_24h: float = 0.0
    atr_percent: float = 0.0
    trail_activated: bool = False
    trade_mode: str = "PAPER"
    # Адаптивный трейлинг
    adaptive_trailing_enabled: bool = False
    trailing_activation_pct: float = 1.0   # Оптимизировано
    trailing_distance_pct: float = 0.25    # Оптимизировано
    # Данные для анализа тренда Bitcoin
    btc_trend_at_open: Dict = field(default_factory=dict)
    # Объемы
    volume_trend: float = 0.0
    volume_spike: bool = False
    # Индикаторы при входе (для пост-мортем анализа)
    rsi_at_entry: float = 50.0
    bollinger_b_at_entry: float = 50.0
    macd_histogram_at_entry: float = 0.0
    macd_divergence_at_entry: str = "none"
    # Защита прибыли
    partial_tp_done: bool = False  # Сработал ли частичный TP
    breakeven_activated: bool = False  # Переставлен ли SL на вход
    original_size_usdt: float = 0.0  # Изначальный размер до частичного TP
    partial_tp_pnl: float = 0.0  # Зафиксированный PnL от частичного TP
    exit_price: float = 0.0  # Цена выхода (записывается при закрытии)
    last_valid_price: float = 0.0  # Последняя валидная цена
    last_price_update: datetime = field(default_factory=datetime.now)
    max_pnl_percent: float = 0.0  # Максимальная достигнутая прибыль (для защиты)
    change_24h_at_open: float = 0.0  # Реальный change_24h в момент открытия (для анализа опозданий)
    
    def to_dict(self):
        d = asdict(self)
        btc_trend = d.get('btc_trend_at_open')
        # Защита от двойной сериализации: сериализуем ТОЛЬКО если это dict
        if isinstance(btc_trend, dict):
            d['btc_trend_at_open'] = json.dumps(btc_trend, ensure_ascii=False)
        elif btc_trend is None:
            d['btc_trend_at_open'] = '{}'
        # Если уже строка — оставляем как есть
        
        # v5.9: Конвертируем datetime в строку для JSON
        if isinstance(d.get('last_price_update'), datetime):
            d['last_price_update'] = d['last_price_update'].isoformat()
        return d
    
    def calculate_current_pnl(self, commission_rate: float = 0.08) -> Tuple[float, float]:
        """Рассчитать текущий PnL для SHORT и LONG позиций с учётом комиссий"""
        if self.side == "SHORT":
            # Для SHORT: прибыль когда цена НИЖЕ entry
            pct = (self.entry_price - self.current_price) / self.entry_price * 100
        else:  # LONG
            # Для LONG: прибыль когда цена ВЫШЕ entry
            pct = (self.current_price - self.entry_price) / self.entry_price * 100
        
        # Учитываем комиссию (открытие + закрытие)
        pct -= commission_rate
        
        pnl_percent = pct * self.leverage
        pnl_usdt = self.size_usdt * (pnl_percent / 100)
        return pnl_usdt, pnl_percent

@dataclass
class Settings:
    """Настройки трейдера"""
    # Баланс и размер позиции
    initial_balance: float = 10000.0
    position_size: float = 500.0
    position_size_live: float = 10.0  # Для реальной торговли
    leverage: int = 5
    max_positions: int = 5
    # Риск-менеджмент
    max_daily_loss_pct: float = 15.0
    max_weekly_loss_pct: float = 15.0
    max_daily_loss_live: float = 5.0  # $ для реальной торговли
    cooldown_after_loss_min: int = 5
    symbol_cooldown_min: int = 30       # Кулдаун ПО МОНЕТЕ после SL (минут)
    max_symbol_losses_daily: int = 2    # Макс SL по одной монете за день
    # SL/TP параметры для бэктеста и стратегии (ДОБАВЛЕНО v5.6)
    stop_loss_pct: float = 3.5        # Стоп-лосс 3.5% floor (ATR×1.5 если выше)
    take_profit_pct: float = 12.0     # Тейк-профит 12%
    # Трейлинг-стоп
    trailing_enabled: bool = True
    trailing_distance_pct: float = 0.8   # Дистанция 0.8% — даёт позиции дышать
    trailing_activation_pct: float = 2.0  # Активация при 2% профита
    # ATR-адаптивный SL/Trailing (v6.2)
    atr_adaptive_sl: bool = True           # Включить ATR-адаптивный SL и trailing
    atr_sl_multiplier: float = 1.5         # SL = ATR × multiplier (min = stop_loss_pct)
    atr_trail_activation_multiplier: float = 3.0  # Trail activation = ATR × multiplier
    atr_trail_distance_multiplier: float = 0.7    # Trail distance = ATR × multiplier
    # Адаптивный трейлинг (legacy)
    adaptive_trailing_enabled: bool = False
    adaptive_min_trades: int = 3  # Мин. сделок для обучения
    # Частичное закрытие
    partial_close_enabled: bool = True
    partial_close_pct: float = 50.0
    # Сканер
    scan_interval: int = 300
    max_to_analyze: int = 15            # Макс. монет для анализа за цикл
    min_change_filter: float = 5.0  # Снижено с 10.0 для более частых LONG
    confidence_threshold: int = 75   # Снижено с 80
    start_paused: bool = False  # Сканер запускается АКТИВНЫМ
    # Рабочие часы (GMT+2)
    work_hours_enabled: bool = False
    work_hours_start: str = "06:00"
    work_hours_end: str = "23:00"
    # Тренд Bitcoin
    btc_trend_filter_enabled: bool = True
    # Гранулярные BTC-фильтры: long_only, short_only, any, any_incl_neutral
    btc_bullish_mode: str = "long_only"     # При бычьем BTC
    btc_bearish_mode: str = "short_only"    # При медвежьем BTC
    btc_neutral_mode: str = "none"           # При нейтральном BTC (по умолч. не торговать)
    # Минимальная сила тренда для торговли (%)
    btc_bullish_min_strength: float = 0.5   # Мин. % для бычьего
    btc_bearish_min_strength: float = 0.5   # Мин. % для медвежьего
    # Автозакрытие при нейтрали
    close_long_on_neutral: bool = False     # Закрывать LONG при нейтральном BTC
    close_short_on_neutral: bool = False    # Закрывать SHORT при нейтральном BTC
    # Автозакрытие при ослаблении тренда (v6.1)
    close_long_on_weak_bull: bool = False       # Закрывать LONG если бычий тренд ослаб
    close_long_weak_bull_threshold: float = 0.5 # Порог: если BTC 24h < +X% → закрыть LONG
    close_short_on_weak_bear: bool = False      # Закрывать SHORT если медвежий тренд ослаб
    close_short_weak_bear_threshold: float = 0.5 # Порог: если BTC 24h > -X% → закрыть SHORT
    # Режим торговли
    trade_mode: str = "PAPER"  # PAPER или LIVE
    # API ключи
    deepseek_api_key: str = ""
    groq_api_key: str = ""
    binance_api_key: str = ""
    binance_secret_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # AI настройки
    ai_provider: str = "mock"  # mock (по умолчанию), deepseek, groq
    ai_ab_mode: bool = False
    ai_consensus_required: bool = False
    # Рекомендации
    auto_apply_recommendations: bool = False
    # Максимальное время жизни позиции (часы). 0 = без ограничений
    max_position_age_hours: int = 48
    # Черные списки
    avoid_hours: str = "[]"
    blacklist_symbols: str = "[]"
    # Белый список (торговать ТОЛЬКО этими монетами)
    whitelist_symbols: str = "[]"
    whitelist_enabled: bool = False
    # Уведомления
    notifications_sound: bool = True
    telegram_enabled: bool = False
    telegram_notify_open: bool = True
    telegram_notify_close: bool = True
    telegram_notify_trailing: bool = True
    telegram_notify_errors: bool = True
    # Защита "толстых пальцев"
    live_confirm_before_open: bool = True
    live_max_position_size: float = 10.0
    live_max_positions: int = 3
    # Объемы
    volume_trend_required: bool = False  # По умолчанию выключено
    volume_spike_filter: bool = False    # По умолчанию выключено
    # КОМИССИИ БИРЖИ (Binance Futures)
    commission_rate: float = 0.08  # 0.04% открытие + 0.04% закрытие = 0.08% на сделку
    commission_enabled: bool = True  # Учитывать комиссии в PnL
    # РЕЖИМ ТОРГОВЛИ AI
    # aggressive - больше сделок, ниже точность (RSI: SHORT>65, LONG<45, conf>=65%)
    # normal - баланс (RSI: SHORT>70, LONG<40, conf>=75%)
    # conservative - меньше сделок, выше точность (RSI: SHORT>75, LONG<35, conf>=85%)
    trading_style: str = "normal"  # aggressive, normal, conservative
    # ═══ ЗАЩИТА ПРИБЫЛИ ═══
    # 1. Частичный TP - закрыть часть позиции на TP1
    partial_tp_enabled: bool = True
    partial_tp_percent: float = 50.0  # Сколько % позиции закрыть на TP1
    # 2. Breakeven Stop - перевод SL в безубыток
    breakeven_enabled: bool = True
    breakeven_trigger_pct: float = 3.0  # При какой прибыли переставить SL на вход
    # 3. Equity Protection - защита от просадки портфеля
    equity_protection_enabled: bool = True
    equity_drawdown_pct: float = 25.0  # Макс просадка от пика в %
    equity_drawdown_abs: float = 150.0  # Макс просадка в $ (что раньше сработает)
    equity_activation_multiplier: float = 3.0  # Множитель комиссий для активации (пик > комиссии × множитель)
    # ═══ CRYPTO AGENT ═══
    agent_enabled: bool = False  # Включить AI Agent
    agent_aggressiveness: int = 2  # 1-Консервативный, 2-Умеренный, 3-Агрессивный
    agent_auto_close: bool = True  # Авто-закрытие позиций
    agent_validate_signals: bool = True  # Проверка сигналов
    agent_stagnation_hours: float = 2.0  # Часов без движения для проверки
    # ═══ SPREAD CHECK v5.0 ═══
    spread_check_enabled: bool = True  # Проверять спред перед входом
    max_spread_pct: float = 0.5  # Максимальный спред в % (0.5% = 50 пунктов на $10000)
    
    def to_dict(self):
        return asdict(self)

class VirtualTrader:
    """Виртуальный и реальный трейдер с поддержкой LONG/SHORT позиций"""
    def __init__(self):
        self.lock = threading.Lock()
        self.settings = Settings()
        self.balance = self.settings.initial_balance
        self.positions: Dict[str, Position] = {}
        self.closed_positions: List[Position] = []
        self.log_history: List[Dict] = []
        # Восстанавливаем счётчик из БД чтобы не было коллизий после перезапуска
        try:
            self.trade_counter = db.get_last_trade_counter()
            if self.trade_counter > 0:
                logger.info(f"[TRADER] Восстановлен счётчик сделок: {self.trade_counter} (продолжаем с RVV-{self.trade_counter+1:04d})")
        except Exception as _e:
            logger.warning(f"[TRADER] Не удалось восстановить счётчик: {_e}")
            self.trade_counter = 0
        self.scanner_paused = self.settings.start_paused  # Берём из настроек
        self.daily_loss_stop = False
        self.weekly_loss_stop = False
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.daily_date = ""
        self.weekly_start = ""
        self.total_pnl = 0.0
        self.winning_trades = 0
        self.losing_trades = 0
        # Cooldown после убытка
        self.last_loss_time = None
        # Cooldown ПО МОНЕТЕ (антизацикливание)
        self.symbol_cooldowns: Dict[str, datetime] = {}   # символ → время последнего SL
        self.symbol_daily_losses: Dict[str, int] = {}     # символ → кол-во SL за день
        # Binance Live клиент
        self.binance_client = None
        # Кэш тренда Bitcoin — устанавливается из app.py перед open_position
        self.btc_trend_data = None
        self.btc_trend_last_used = None
        # ═══ EQUITY PROTECTION ═══
        self.equity_peak = 0.0  # Максимальное эквити (PnL)
        self.equity_protection_triggered = False  # Сработала ли защита
        self._equity_activation_threshold = 0.0  # Порог активации (комиссии × множитель)
        # ═══ CALLBACKS ═══
        self.on_position_closed = None  # Callback при закрытии позиции
        self._load_state()
        # ═══ v5.8.2: СИНХРОНИЗАЦИЯ С CONFIG.JSON ═══
        self._sync_from_config()
        logger.info(f"[TRADER] Init: ${self.balance:.2f}, mode: {self.settings.trade_mode}, SL={self.settings.stop_loss_pct}%")
    
    def _add_log(self, log_type: str, message: str, data: Dict = None):
        """Добавить лог"""
        now = get_gmt2_time()
        entry = {
            "timestamp": now.strftime("%H:%M:%S"),
            "type": log_type,
            "message": message,
            "data": data or {}
        }
        self.log_history.append(entry)
        if len(self.log_history) > 500:
            self.log_history = self.log_history[-500:]
        try:
            db.add_log(log_type, message, data)
        except Exception as e:
            logger.error(f"[TRADER] Log error: {e}")
    
    def _sync_from_config(self):
        """
        v5.8.2: Синхронизация настроек trading из config.json
        Config.json имеет ПРИОРИТЕТ над БД для trading параметров
        """
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            trading = config.get('trading', {})
            if trading:
                # Синхронизируем trading параметры
                mapping = {
                    'stop_loss_pct': 'stop_loss_pct',
                    'take_profit_pct': 'take_profit_pct',
                    'trailing_activation_pct': 'trailing_activation_pct',
                    'trailing_distance_pct': 'trailing_distance_pct',
                    'position_size': 'position_size',
                    'leverage': 'leverage',
                    'max_positions': 'max_positions',
                    'max_to_analyze': 'max_to_analyze',
                    'min_change_filter': 'min_change_filter',
                    'atr_adaptive_sl': 'atr_adaptive_sl',
                    'atr_sl_multiplier': 'atr_sl_multiplier',
                    'atr_trail_activation_multiplier': 'atr_trail_activation_multiplier',
                    'atr_trail_distance_multiplier': 'atr_trail_distance_multiplier'
                }
                
                for config_key, settings_key in mapping.items():
                    if config_key in trading:
                        old_val = getattr(self.settings, settings_key, None)
                        new_val = trading[config_key]
                        if old_val != new_val:
                            setattr(self.settings, settings_key, new_val)
                            print(f"[TRADER] ✅ {settings_key}: {old_val} → {new_val} (from config.json)")
                
                # Сохраняем в БД
                self._save_state()
                logger.info(f"[TRADER] Synced from config.json: SL={self.settings.stop_loss_pct}%, TP={self.settings.take_profit_pct}%")
            
            # Синхронизируем фильтры
            filters = config.get('filters', {})
            if filters:
                self.filters = filters
                # Синхронизируем BTC-режимы в settings
                if 'btc_bullish_mode' in filters:
                    self.settings.btc_bullish_mode = filters['btc_bullish_mode']
                if 'btc_bearish_mode' in filters:
                    self.settings.btc_bearish_mode = filters['btc_bearish_mode']
                if 'btc_neutral_mode' in filters:
                    self.settings.btc_neutral_mode = filters['btc_neutral_mode']
                if 'btc_bullish_min_strength' in filters:
                    self.settings.btc_bullish_min_strength = float(filters['btc_bullish_min_strength'])
                if 'btc_bearish_min_strength' in filters:
                    self.settings.btc_bearish_min_strength = float(filters['btc_bearish_min_strength'])
                if 'close_long_on_neutral' in filters:
                    self.settings.close_long_on_neutral = filters['close_long_on_neutral']
                if 'close_short_on_neutral' in filters:
                    self.settings.close_short_on_neutral = filters['close_short_on_neutral']
                # Автозакрытие при ослаблении тренда (v6.1)
                if 'close_long_on_weak_bull' in filters:
                    self.settings.close_long_on_weak_bull = filters['close_long_on_weak_bull']
                if 'close_long_weak_bull_threshold' in filters:
                    self.settings.close_long_weak_bull_threshold = float(filters['close_long_weak_bull_threshold'])
                if 'close_short_on_weak_bear' in filters:
                    self.settings.close_short_on_weak_bear = filters['close_short_on_weak_bear']
                if 'close_short_weak_bear_threshold' in filters:
                    self.settings.close_short_weak_bear_threshold = float(filters['close_short_weak_bear_threshold'])
                
        except Exception as e:
            logger.warning(f"[TRADER] Config sync error: {e}")
    
    def get_logs(self, limit: int = 100) -> List[Dict]:
        return list(reversed(self.log_history[-limit:]))
    
    # =========================================================================
    # ANALYSIS FUNCTIONS — ИСПРАВЛЕНО
    # =========================================================================
    def _get_btc_trend_from_state(self) -> Dict:
        """
        Получить тренд Bitcoin.
        Данные устанавливаются из app.py через self.btc_trend_data
        перед каждым вызовом open_position/can_open_position.
        Никаких импортов — никаких circular import проблем.
        """
        if self.btc_trend_data and isinstance(self.btc_trend_data, dict):
            if self.btc_trend_data.get('trend'):
                return self.btc_trend_data
        return self._create_default_btc_trend()
    
    def _create_default_btc_trend(self) -> Dict:
        """Создать нейтральный тренд по умолчанию"""
        return {
            'trend': 'neutral',
            'strength': 'stable',
            'trend_pct': 0.0,
            'rsi_1h': 50.0,
            'change_24h': 0.0,
            'confidence': 100,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """Расчет RSI для списка цен"""
        if len(closes) < period + 1:
            return 50.0
        gains = 0.0
        losses = 0.0
        for i in range(-period-1, -1):
            delta = closes[i+1] - closes[i]
            if delta > 0:
                gains += delta
            else:
                losses += abs(delta)
        if losses == 0:
            return 100.0
        rs = gains / losses
        return 100 - (100 / (1 + rs))
    
    # =========================================================================
    # АДАПТИВНЫЙ ТРЕЙЛИНГ-СТОП С УЧЁТОМ ВОЛАТИЛЬНОСТИ И НАПРАВЛЕНИЯ
    # =========================================================================
    def _get_adaptive_trailing_params(self, symbol: str, current_atr: float = None) -> Dict:
        """
        Получить адаптивные параметры трейлинга для символа
        Возвращает:
        - activation_pct: когда активировать трейлинг
        - distance_pct: расстояние от цены
        - mode: default/adaptive/learned
        """
        # v6.2: ATR-адаптивный trailing
        if self.settings.atr_adaptive_sl and current_atr and current_atr > 0:
            atr_act = round(current_atr * self.settings.atr_trail_activation_multiplier, 2)
            atr_dist = round(current_atr * self.settings.atr_trail_distance_multiplier, 2)
            # Floor: не меньше значений из config
            act_pct = max(self.settings.trailing_activation_pct, atr_act)
            dist_pct = max(self.settings.trailing_distance_pct, atr_dist)
            # Ceiling: distance не больше activation - 0.5% (гарантия прибыли)
            dist_pct = min(dist_pct, act_pct - 0.5)
            params = {
                'activation_pct': act_pct,
                'distance_pct': dist_pct,
                'mode': 'atr_adaptive',
                'learned': False
            }
            logger.info(f"[TRAILING] {symbol}: ATR-ADAPTIVE, ATR={current_atr:.2f}%, activation={act_pct}%, distance={dist_pct}%")
            return params

        # Фиксированный режим
        if not self.settings.adaptive_trailing_enabled:
            params = {
                'activation_pct': self.settings.trailing_activation_pct,
                'distance_pct': self.settings.trailing_distance_pct,
                'mode': 'fixed',
                'learned': False
            }
            logger.info(f"[TRAILING] {symbol}: FIXED mode, activation={params['activation_pct']}%, distance={params['distance_pct']}%")
            return params
        
        # Пробуем получить из базы данных
        adaptive_params = db.calculate_adaptive_trailing(symbol, current_atr)
        logger.info(f"[TRAILING] {symbol}: ADAPTIVE mode, activation={adaptive_params.get('activation_pct')}%, distance={adaptive_params.get('distance_pct')}%")
        return adaptive_params
    
    def _update_trailing_stop(self, pos: Position, current_price: float) -> Tuple[bool, float]:
        """
        Обновление адаптивного трейлинг-стопа с учётом текущей волатильности и направления позиции
        ВАЖНО: Trailing Stop НИКОГДА не должен давать убыток!
        Возвращает: (изменён, новый_стоп_лосс)
        """
        if not self.settings.trailing_enabled:
            return False, pos.stop_loss
        
        # Для SHORT: прибыль когда цена НИЖЕ entry
        # Для LONG: прибыль когда цена ВЫШЕ entry
        if pos.side == "SHORT":
            profit_pct = (pos.entry_price - current_price) / pos.entry_price * 100
        else:  # LONG
            profit_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        
        # Получаем параметры трейлинга
        activation_pct = pos.trailing_activation_pct
        
        # Если прибыль превысила порог активации
        if profit_pct >= activation_pct and not pos.trail_activated:
            pos.trail_activated = True
            logger.info(f"[TRAILING] {pos.symbol} {pos.side}: Activated at {profit_pct:.2f}% profit")
            self._add_log("trailing",
                f"🚀 Трейлинг активирован: {pos.symbol} {pos.side} (прибыль: {profit_pct:.2f}%)")
            
            # Сохраняем событие TRAIL_ACTIVATED
            try:
                db.save_trade_price_event(
                    trade_id=pos.id,
                    event_type='TRAIL_ACTIVATED',
                    price=current_price,
                    pnl_percent=profit_pct,
                    trailing_stop=pos.stop_loss,
                    details=f"Trailing ON at {profit_pct:.2f}%"
                )
            except Exception as e:
                logger.debug(f"[TRAILING] Event save error: {e}")
        
        # Если трейлинг активирован
        if pos.trail_activated:
            # Волатильность-множитель: сравниваем текущий ATR% с рыночной нормой (~2.0%)
            market_avg_atr = 2.0
            atr_percent = pos.atr_percent if pos.atr_percent > 0 else market_avg_atr
            volatility_multiplier = max(0.8, min(2.5, atr_percent / market_avg_atr))
            
            # Динамическое расстояние трейлинга с учётом волатильности
            dynamic_distance_pct = pos.trailing_distance_pct * volatility_multiplier
            
            # ========== ИСПРАВЛЕНО v5.6: ОГРАНИЧЕНИЕ DISTANCE ==========
            # Distance НЕ МОЖЕТ быть больше (activation - 0.5%), иначе не будет гарантированной прибыли
            max_distance = activation_pct - 0.5
            if dynamic_distance_pct > max_distance:
                dynamic_distance_pct = max_distance
            
            # ========== ЗАЩИТА ОТ ПОТЕРИ БОЛЬШОЙ ПРИБЫЛИ ==========
            # Если прибыль была большой, гарантируем минимум 60% от неё
            max_profit = getattr(pos, 'max_pnl_percent', profit_pct)
            if profit_pct > max_profit:
                pos.max_pnl_percent = profit_pct
                max_profit = profit_pct
            
            # При прибыли >20% уменьшаем distance для лучшей защиты
            if max_profit >= 20:
                # Чем больше была прибыль, тем ближе trailing
                protection_factor = min(0.5, max_profit / 100)  # До 50% ближе
                dynamic_distance_pct = dynamic_distance_pct * (1 - protection_factor)
                logger.debug(f"[TRAILING] {pos.symbol}: High profit protection, distance reduced to {dynamic_distance_pct:.2f}%")
            
            # Разная логика для SHORT и LONG
            if pos.side == "SHORT":
                # Для SHORT: новый стоп = текущая цена + динамическое расстояние
                new_stop = current_price * (1 + dynamic_distance_pct / 100)
                
                # ========== ИСПРАВЛЕНО v5.6: ГАРАНТИЯ МИНИМАЛЬНОЙ ПРИБЫЛИ ==========
                # Минимальная прибыль = activation - distance (например: 2% - 1% = 1%)
                min_guaranteed_profit = max(0.5, activation_pct - pos.trailing_distance_pct)
                max_stop = pos.entry_price * (1 - min_guaranteed_profit / 100)
                
                # SL не может быть выше гарантированного максимума (для SHORT выше = хуже)
                if new_stop > max_stop:
                    new_stop = max_stop
                
                # Для SHORT: двигаем стоп только ВНИЗ (уменьшаем)
                if new_stop < pos.stop_loss:
                    old_stop = pos.stop_loss
                    pos.stop_loss = new_stop
                    pos.trailing_stop = new_stop
                    logger.info(f"[TRAILING] {pos.symbol} SHORT: SL moved {old_stop:.6f} -> {new_stop:.6f} " +
                              f"(volatility multiplier: {volatility_multiplier:.2f})")
                    
                    # Сохраняем событие TRAIL_UPDATED
                    try:
                        db.save_trade_price_event(
                            trade_id=pos.id,
                            event_type='TRAIL_UPDATED',
                            price=current_price,
                            pnl_percent=profit_pct,
                            trailing_stop=new_stop,
                            details=f"SL: {old_stop:.6f} -> {new_stop:.6f}, волатильность x{volatility_multiplier:.2f}"
                        )
                    except Exception as e:
                        logger.debug(f"[TRAILING] Event save error: {e}")
                        
                    return True, new_stop
            else:  # LONG
                # Для LONG: новый стоп = текущая цена - динамическое расстояние
                new_stop = current_price * (1 - dynamic_distance_pct / 100)
                
                # ========== ИСПРАВЛЕНО v5.6: ГАРАНТИЯ МИНИМАЛЬНОЙ ПРИБЫЛИ ==========
                # Минимальная прибыль = activation - distance (например: 2% - 1% = 1%)
                min_guaranteed_profit = max(0.5, activation_pct - pos.trailing_distance_pct)
                min_stop = pos.entry_price * (1 + min_guaranteed_profit / 100)
                
                # SL не может быть ниже гарантированного минимума
                if new_stop < min_stop:
                    new_stop = min_stop
                
                # Для LONG: двигаем стоп только ВВЕРХ (увеличиваем)
                if new_stop > pos.stop_loss:
                    old_stop = pos.stop_loss
                    pos.stop_loss = new_stop
                    pos.trailing_stop = new_stop
                    logger.info(f"[TRAILING] {pos.symbol} LONG: SL moved {old_stop:.6f} -> {new_stop:.6f} " +
                              f"(volatility multiplier: {volatility_multiplier:.2f})")
                    
                    # Сохраняем событие TRAIL_UPDATED
                    try:
                        db.save_trade_price_event(
                            trade_id=pos.id,
                            event_type='TRAIL_UPDATED',
                            price=current_price,
                            pnl_percent=profit_pct,
                            trailing_stop=new_stop,
                            details=f"SL: {old_stop:.6f} -> {new_stop:.6f}, волатильность x{volatility_multiplier:.2f}"
                        )
                    except Exception as e:
                        logger.debug(f"[TRAILING] Event save error: {e}")
                        
                    return True, new_stop
        
        return False, pos.stop_loss
    
    # =========================================================================
    # ПРОВЕРКИ И ВАЛИДАЦИЯ - ИСПРАВЛЕНО ДЛЯ LONG
    # =========================================================================
    def is_work_hours(self) -> Tuple[bool, str]:
        """Проверка рабочих часов (GMT+2)"""
        if not self.settings.work_hours_enabled:
            return True, "Work hours disabled"
        
        now = get_gmt2_time()
        current_time = now.strftime("%H:%M")
        start = self.settings.work_hours_start
        end = self.settings.work_hours_end
        
        # Сравниваем строки времени  
        if start <= end:
            # Обычный случай: 06:00 - 23:00
            in_hours = start <= current_time <= end
        else:
            # Через полночь: 22:00 - 06:00
            in_hours = current_time >= start or current_time <= end
        
        if in_hours:
            return True, f"Work hours: {start}-{end}"
        else:
            return False, f"Outside work hours ({start}-{end}), current: {current_time}"
    
    def can_open_position(self, symbol: str = "", position_type: str = None) -> Tuple[bool, str]:
        """Проверка возможности открытия позиции с учетом тренда Bitcoin - ИСПРАВЛЕНО ДЛЯ LONG"""
        if self.scanner_paused:
            return False, "Scanner paused"
        
        # Проверка рабочих часов
        in_hours, hours_msg = self.is_work_hours()
        if not in_hours:
            return False, hours_msg
        
        if self.daily_loss_stop:
            return False, "Daily loss limit reached"
        
        if self.weekly_loss_stop:
            return False, "Weekly loss limit reached"
        
        # Проверка cooldown после убытка
        if self.last_loss_time:
            cooldown_end = self.last_loss_time + timedelta(minutes=self.settings.cooldown_after_loss_min)
            if get_gmt2_time() < cooldown_end:
                remaining = (cooldown_end - get_gmt2_time()).seconds // 60
                return False, f"Cooldown after loss: {remaining} min remaining"
        
        # Лимит позиций (разный для PAPER и LIVE)
        if self.settings.trade_mode == "LIVE":
            max_pos = self.settings.live_max_positions
        else:
            max_pos = self.settings.max_positions
        
        # ФИКС: считаем ВСЕ открытые позиции (не по типу!)
        open_count = len([p for p in self.positions.values() if p.status == "OPEN"])
        if open_count >= max_pos:
            return False, f"Max {max_pos} positions reached ({open_count} open)"
        
        # Проверка баланса  
        if self.settings.trade_mode == "LIVE":
            margin = self.settings.position_size_live / self.settings.leverage
        else:
            margin = self.settings.position_size / self.settings.leverage
        
        if self.balance < margin:
            return False, "Low balance"
        
        # Проверка часов
        try:
            avoid_hours = json.loads(self.settings.avoid_hours)
            if avoid_hours:
                current_hour = get_gmt2_time().hour
                if current_hour in avoid_hours:
                    return False, f"Hour {current_hour}:00 is avoided"
        except Exception as e:
            logger.debug(f"[TRADER] Avoid hours error: {e}")
        
        # BTC-тренд фильтр (гранулярный v6.0)
        if self.settings.btc_trend_filter_enabled and position_type:
            btc_trend = self._get_btc_trend_from_state()
            trend = btc_trend.get('trend', 'neutral')
            # Сила тренда: trend_pct (новый) или change_24h (всегда есть) 
            trend_pct = abs(btc_trend.get('trend_pct', 0.0))
            if trend_pct == 0:
                trend_pct = abs(btc_trend.get('change_24h', 0.0))
            
            logger.info(f"[BTC FILTER] can_open: trend={trend}, pct={trend_pct:.2f}%, type={position_type}, has_data={self.btc_trend_data is not None}")
            
            # Определяем режим для текущего тренда
            if trend == 'bullish':
                min_strength = self.settings.btc_bullish_min_strength
                if min_strength > 0 and trend_pct < min_strength:
                    # Тренд слишком слабый → считаем нейтральным
                    mode = self.settings.btc_neutral_mode
                    logger.info(f"[BTC FILTER] {trend} {trend_pct:.2f}% < мин {min_strength}% → neutral mode={mode}")
                else:
                    mode = self.settings.btc_bullish_mode
            elif trend == 'bearish':
                min_strength = self.settings.btc_bearish_min_strength
                if min_strength > 0 and trend_pct < min_strength:
                    mode = self.settings.btc_neutral_mode
                    logger.info(f"[BTC FILTER] {trend} {trend_pct:.2f}% < мин {min_strength}% → neutral mode={mode}")
                else:
                    mode = self.settings.btc_bearish_mode
            else:
                mode = self.settings.btc_neutral_mode
                if mode not in ('any', 'any_incl_neutral', 'none'):
                    if self.settings.btc_bullish_mode == 'any_incl_neutral' or self.settings.btc_bearish_mode == 'any_incl_neutral':
                        mode = 'any'
            
            # Проверяем направление по итоговому mode
            if mode == 'none':
                return False, f"BTC {trend} ({trend_pct:.2f}%): торговля запрещена (режим 'не торговать')"
            elif mode == 'long_only' and position_type == 'SHORT':
                return False, f"BTC {trend} ({trend_pct:.2f}%): только LONG разрешены"
            elif mode == 'short_only' and position_type == 'LONG':
                return False, f"BTC {trend} ({trend_pct:.2f}%): только SHORT разрешены"
            # 'any' и 'any_incl_neutral' разрешают всё
        
        return True, "OK"
    
    def _check_blacklist(self, symbol: str) -> bool:
        """Проверка черного списка"""
        # Сначала проверяем в БД
        if db.is_blacklisted(symbol):
            return True
        
        # Потом в настройках
        try:
            blacklist = json.loads(self.settings.blacklist_symbols)
            if blacklist and symbol in blacklist:
                return True
        except Exception as e:
            logger.debug(f"[TRADER] Blacklist error: {e}")
        
        return False
    
    def check_neutral_auto_close(self) -> List[str]:
        """
        Автозакрытие позиций при нейтральном BTC тренде.
        Вызывается из scan_cycle.
        Returns: список закрытых trade_id
        """
        closed = []
        try:
            if not self.settings.btc_trend_filter_enabled:
                return closed
            if not self.settings.close_long_on_neutral and not self.settings.close_short_on_neutral:
                return closed
            
            btc_trend = self._get_btc_trend_from_state()
            trend = btc_trend.get('trend', 'neutral')
            
            if trend != 'neutral':
                return closed  # Не нейтраль — ничего не закрываем
            
            with self.lock:
                for trade_id, pos in list(self.positions.items()):
                    if pos.status != "OPEN":
                        continue
                    
                    if pos.side == "LONG" and self.settings.close_long_on_neutral:
                        logger.info(f"[NEUTRAL_CLOSE] Закрываем LONG {trade_id} — BTC нейтральный")
                        result = self._close_position(trade_id, "BTC_NEUTRAL")
                        if result is not None:
                            closed.append(trade_id)
                            self._add_log("close", f"⚖️ {pos.symbol} LONG закрыт: BTC нейтральный")
                    
                    elif pos.side == "SHORT" and self.settings.close_short_on_neutral:
                        logger.info(f"[NEUTRAL_CLOSE] Закрываем SHORT {trade_id} — BTC нейтральный")
                        result = self._close_position(trade_id, "BTC_NEUTRAL")
                        if result is not None:
                            closed.append(trade_id)
                            self._add_log("close", f"⚖️ {pos.symbol} SHORT закрыт: BTC нейтральный")
        except Exception as e:
            logger.error(f"[NEUTRAL_CLOSE] Error: {e}")
        
        return closed
    
    def check_trend_weakness_auto_close(self) -> List[str]:
        """
        Автозакрытие позиций при ослаблении BTC тренда (v6.1).
        """
        closed = []
        try:
            if not self.settings.btc_trend_filter_enabled:
                return closed
            
            # Проверяем настройки
            long_enabled = self.settings.close_long_on_weak_bull
            long_thr = self.settings.close_long_weak_bull_threshold
            short_enabled = self.settings.close_short_on_weak_bear
            short_thr = self.settings.close_short_weak_bear_threshold
            
            if not long_enabled and not short_enabled:
                return closed  # Обе фичи выключены
            
            # Получаем данные BTC
            btc_trend = self._get_btc_trend_from_state()
            change_24h = btc_trend.get('change_24h', 0.0)
            if change_24h == 0.0:
                change_24h = btc_trend.get('trend_pct', 0.0)
            
            with self.lock:
                # Считаем открытые позиции по типу
                open_longs = [(tid, p) for tid, p in self.positions.items() if p.status == "OPEN" and p.side == "LONG"]
                open_shorts = [(tid, p) for tid, p in self.positions.items() if p.status == "OPEN" and p.side == "SHORT"]
                
                if not open_longs and not open_shorts:
                    return closed
                
                logger.info(f"[TREND_CLOSE] BTC 24h={change_24h:+.2f}% | "
                            f"LONG: {len(open_longs)} шт, авто={'ВКЛ' if long_enabled else 'ВЫКЛ'}(порог<+{long_thr}%) | "
                            f"SHORT: {len(open_shorts)} шт, авто={'ВКЛ' if short_enabled else 'ВЫКЛ'}(порог>-{short_thr}%)")
                
                # === Закрытие LONG при ослаблении бычьего тренда ===
                # v6.2: Только убыточные позиции! Прибыльные пусть идут к TP/trailing
                if long_enabled and open_longs:
                    if change_24h < long_thr:
                        for trade_id, pos in open_longs:
                            # Проверяем: позиция в убытке? (учитываем partial TP)
                            total_pnl = pos.pnl_usdt + getattr(pos, 'partial_tp_pnl', 0)
                            if total_pnl > 0:
                                logger.info(f"[TREND_CLOSE] ⏭️ Пропускаем LONG {pos.symbol} — в прибыли ${total_pnl:+.2f}")
                                continue
                            logger.info(f"[TREND_CLOSE] → Закрываем LONG {pos.symbol} — BTC {change_24h:+.2f}% < +{long_thr}%, PnL=${total_pnl:.2f}")
                            result = self._close_position(trade_id, "BTC_TREND_WEAK")
                            if result is not None:
                                closed.append(trade_id)
                                self._add_log("close", f"📉 {pos.symbol} LONG закрыт: BTC ослаб ({change_24h:+.1f}%), PnL=${pos.pnl_usdt:.2f}")
                            else:
                                logger.error(f"[TREND_CLOSE] _close_position вернул None для LONG {trade_id}")
                
                # === Закрытие SHORT при ослаблении медвежьего тренда ===
                # v6.2: Только убыточные позиции!
                if short_enabled and open_shorts:
                    if change_24h > -short_thr:
                        for trade_id, pos in open_shorts:
                            total_pnl = pos.pnl_usdt + getattr(pos, 'partial_tp_pnl', 0)
                            if total_pnl > 0:
                                logger.info(f"[TREND_CLOSE] ⏭️ Пропускаем SHORT {pos.symbol} — в прибыли ${total_pnl:+.2f}")
                                continue
                            logger.info(f"[TREND_CLOSE] → Закрываем SHORT {pos.symbol} — BTC {change_24h:+.2f}% > -{short_thr}%, PnL=${total_pnl:.2f}")
                            result = self._close_position(trade_id, "BTC_TREND_WEAK")
                            if result is not None:
                                closed.append(trade_id)
                                self._add_log("close", f"📈 {pos.symbol} SHORT закрыт: BTC ослаб ({change_24h:+.1f}%), PnL=${pos.pnl_usdt:.2f}")
                            else:
                                logger.error(f"[TREND_CLOSE] _close_position вернул None для SHORT {trade_id}")
            
            if closed:
                logger.info(f"[TREND_CLOSE] ✅ Закрыто {len(closed)} позиций")
        except Exception as e:
            logger.error(f"[TREND_CLOSE] ОШИБКА: {e}", exc_info=True)
        
        return closed
    
    def _check_filters(self, direction: str, confidence: int = 100) -> Tuple[bool, str]:
        """
        Проверка фильтров торговли (v5.8)
        
        Returns:
            (is_ok, message)
        """
        try:
            # Фильтры загружены при инициализации в _sync_from_config()
            filters = getattr(self, 'filters', None)
            if not filters:
                return True, "No filters configured"
            
            now = datetime.now()
            current_hour = now.hour
            current_day = now.weekday()  # 0=Пн, 6=Вс
            
            # Проверка часа
            allowed_hours = filters.get('allowed_hours', list(range(24)))
            if current_hour not in allowed_hours:
                return False, f"Час {current_hour}:00 не разрешён"
            
            # Проверка дня недели
            allowed_days = filters.get('allowed_days', list(range(7)))
            day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            if current_day not in allowed_days:
                return False, f"{day_names[current_day]} не разрешён"
            
            # Проверка направления
            allowed_directions = filters.get('allowed_directions', ['SHORT', 'LONG'])
            if direction not in allowed_directions:
                return False, f"{direction} отключен в фильтрах"
            
            # Проверка confidence
            min_confidence = filters.get('min_confidence', 70)
            if confidence < min_confidence:
                return False, f"Confidence {confidence}% < {min_confidence}%"
            
            # BTC тренд фильтр (гранулярный v6.0)
            if self.settings.btc_trend_filter_enabled:
                btc_data = self._get_btc_trend_from_state()
                trend = btc_data.get('trend', 'neutral')
                trend_pct = abs(btc_data.get('trend_pct', 0.0))
                if trend_pct == 0:
                    trend_pct = abs(btc_data.get('change_24h', 0.0))
                
                logger.info(f"[BTC FILTER] _check_filters: trend={trend}, trend_pct={trend_pct:.2f}%, has_data={self.btc_trend_data is not None}, keys={list(btc_data.keys())[:5]}")
                
                btc_bullish_mode = filters.get('btc_bullish_mode', self.settings.btc_bullish_mode)
                btc_bearish_mode = filters.get('btc_bearish_mode', self.settings.btc_bearish_mode)
                btc_neutral_mode = filters.get('btc_neutral_mode', self.settings.btc_neutral_mode)
                btc_bullish_min_str = float(filters.get('btc_bullish_min_strength', self.settings.btc_bullish_min_strength))
                btc_bearish_min_str = float(filters.get('btc_bearish_min_strength', self.settings.btc_bearish_min_strength))
                
                if trend == 'bullish':
                    if btc_bullish_min_str > 0 and trend_pct < btc_bullish_min_str:
                        mode = btc_neutral_mode
                    else:
                        mode = btc_bullish_mode
                elif trend == 'bearish':
                    if btc_bearish_min_str > 0 and trend_pct < btc_bearish_min_str:
                        mode = btc_neutral_mode
                    else:
                        mode = btc_bearish_mode
                else:
                    mode = btc_neutral_mode
                    if mode not in ('any', 'any_incl_neutral', 'none'):
                        if btc_bullish_mode == 'any_incl_neutral' or btc_bearish_mode == 'any_incl_neutral':
                            mode = 'any'
                
                if mode == 'none':
                    return False, f"BTC {trend} ({trend_pct:.2f}%): торговля запрещена"
                elif mode == 'long_only' and direction == 'SHORT':
                    return False, f"BTC {trend} ({trend_pct:.2f}%): только LONG (фильтр)"
                elif mode == 'short_only' and direction == 'LONG':
                    return False, f"BTC {trend} ({trend_pct:.2f}%): только SHORT (фильтр)"
            
            return True, "OK"
            
        except Exception as e:
            logger.debug(f"[FILTERS] Error: {e}")
            return True, f"Filter error: {e}"
    
    def _check_spread(self, symbol: str, max_spread_pct: float = 0.5) -> Tuple[bool, float, str]:
        """
        Проверка спреда перед входом в позицию (v5.0)
        
        Returns:
            (is_ok, spread_pct, message)
        """
        try:
            # Получаем orderbook
            from app import state
            if not state.exchange:
                return True, 0.0, "Exchange not available"
            
            orderbook = state.exchange.fetch_order_book(symbol, limit=5)
            if not orderbook or not orderbook.get('bids') or not orderbook.get('asks'):
                return True, 0.0, "No orderbook data"
            
            best_bid = orderbook['bids'][0][0] if orderbook['bids'] else 0
            best_ask = orderbook['asks'][0][0] if orderbook['asks'] else 0
            
            if best_bid <= 0 or best_ask <= 0:
                return True, 0.0, "Invalid bid/ask"
            
            # Рассчитываем спред в процентах
            spread_pct = ((best_ask - best_bid) / best_bid) * 100
            
            if spread_pct > max_spread_pct:
                msg = f"Спред {spread_pct:.3f}% > {max_spread_pct}% (bid=${best_bid:.6f}, ask=${best_ask:.6f})"
                logger.warning(f"[SPREAD] {symbol}: {msg}")
                return False, spread_pct, msg
            
            return True, spread_pct, f"OK ({spread_pct:.3f}%)"
            
        except Exception as e:
            logger.debug(f"[SPREAD] Error checking {symbol}: {e}")
            return True, 0.0, f"Error: {str(e)}"
    
    def _validate_position_levels(self, entry_price: float, stop_loss: float,
                                 take_profit_1: float, take_profit_2: float,
                                 side: str = "SHORT", atr_percent: float = 0) -> tuple:
        """
        Валидация уровней для SHORT и LONG
        v5.8.6: МИНИМАЛЬНАЯ валидация - только проверка направления, 
        НЕ меняем SL/TP если они уже правильные по направлению
        """
        corrected = False
        original_sl = stop_loss
        
        if side == "SHORT":
            # SHORT: SL должен быть ВЫШЕ цены, TP ниже
            # v5.8.6: Только проверяем направление, НЕ навязываем min_buffer!
            
            if stop_loss <= entry_price:
                # SL должен быть выше entry для SHORT - это единственное требование
                stop_loss = entry_price * 1.02  # Дефолт 2% если совсем неправильно
                corrected = True
                logger.warning(f"[VALIDATE] SHORT SL был <= entry, исправлен на +2%")
            
            # Валидация TP для SHORT - должны быть НИЖЕ entry
            if take_profit_1 >= entry_price:
                take_profit_1 = entry_price * 0.965  # TP1 = -3.5%
                corrected = True
            if take_profit_2 >= entry_price:
                take_profit_2 = entry_price * 0.93   # TP2 = -7%
                corrected = True
            if take_profit_2 >= take_profit_1:
                take_profit_2 = take_profit_1 * 0.97
                corrected = True
                
        else:  # LONG
            # LONG: SL должен быть НИЖЕ цены, TP выше
            # v5.8.6: Только проверяем направление, НЕ навязываем min_buffer!
            
            if stop_loss >= entry_price:
                # SL должен быть ниже entry для LONG - это единственное требование
                stop_loss = entry_price * 0.98  # Дефолт 2% если совсем неправильно
                corrected = True
                logger.warning(f"[VALIDATE] LONG SL был >= entry, исправлен на -2%")
            
            # Валидация TP для LONG - должны быть ВЫШЕ entry
            if take_profit_1 <= entry_price:
                take_profit_1 = entry_price * 1.035  # TP1 = +3.5%
                corrected = True
            if take_profit_2 <= entry_price:
                take_profit_2 = entry_price * 1.07   # TP2 = +7%
                corrected = True
            if take_profit_2 <= take_profit_1:
                take_profit_2 = take_profit_1 * 1.03
                corrected = True
        
        if corrected:
            sl_pct = ((stop_loss / entry_price - 1) * 100) if side == "SHORT" else ((1 - stop_loss / entry_price) * 100)
            logger.info(f"[TRADER] Levels corrected for {side}: SL {original_sl:.6f} -> {stop_loss:.6f} ({'+' if sl_pct > 0 else ''}{sl_pct:.2f}%)")
        
        return stop_loss, take_profit_1, take_profit_2, corrected
    
    # =========================================================================
    # ОТКРЫТИЕ И ЗАКРЫТИЕ ПОЗИЦИЙ
    # =========================================================================
    def open_position(self, signal: Dict) -> Optional[Position]:
        """Открыть позицию (SHORT или LONG)"""
        with self.lock:
            # Определяем направление позиции
            position_side = signal.get('direction', signal.get('action', 'SHORT')).upper()
            if position_side not in ['SHORT', 'LONG']:
                self._add_log("info", f"🚫 {signal['symbol']}: Неизвестное направление {position_side}")
                return None
            
            symbol = signal['symbol']
            
            # ═══ ЗАЩИТА: Дубликат позиции на том же символе ═══
            for p in self.positions.values():
                if p.status == "OPEN" and p.symbol == symbol:
                    self._add_log("info", f"⏭️ {symbol}: уже есть открытая {p.side} позиция")
                    return None
            
            # ═══ ЗАЩИТА: Жёсткий лимит позиций (абсолютный) ═══
            open_count = len([p for p in self.positions.values() if p.status == "OPEN"])
            hard_limit = max(self.settings.max_positions, self.settings.live_max_positions) * 2
            if hard_limit < 20:
                hard_limit = 20  # Минимальный жёсткий лимит
            if open_count >= hard_limit:
                self._add_log("error", f"🛑 ЖЁСТКИЙ лимит: {open_count} позиций (макс {hard_limit})")
                return None
            
            # ═══ ПРОВЕРКА ФИЛЬТРОВ v5.8 ═══
            filter_ok, filter_msg = self._check_filters(position_side, signal.get('confidence', 100))
            if not filter_ok:
                self._add_log("filter", f"🎛️ {symbol}: {filter_msg}")
                return None
            
            # Проверка возможности открытия позиции
            can, msg = self.can_open_position(symbol=symbol, position_type=position_side)
            if not can:
                self._add_log("error", msg)
                return None
            
            # Проверка черного списка
            if self._check_blacklist(symbol):
                self._add_log("info", f"⛔ {symbol} в черном списке")
                return None
            
            # ═══ ПРОВЕРКА СПРЕДА v5.0 ═══
            if self.settings.spread_check_enabled:
                spread_ok, spread_pct, spread_msg = self._check_spread(symbol, self.settings.max_spread_pct)
                if not spread_ok:
                    self._add_log("spread", f"⛔ {symbol}: {spread_msg}")
                    return None
            
            self.trade_counter += 1
            trade_id = f"RVV-{self.trade_counter:04d}"
            now = get_gmt2_time()
            entry_price = float(signal['entry_price'])
            atr_percent = signal.get('atr_percent', 0)
            
            # ========== v6.2: ATR-АДАПТИВНЫЙ SL ==========
            tp_pct = self.settings.take_profit_pct
            if self.settings.atr_adaptive_sl and atr_percent > 0:
                atr_sl = round(atr_percent * self.settings.atr_sl_multiplier, 2)
                sl_pct = max(self.settings.stop_loss_pct, atr_sl)
                logger.info(f"[TRADER] {symbol}: ATR-SL={sl_pct}% (ATR={atr_percent:.2f}% × {self.settings.atr_sl_multiplier}, floor={self.settings.stop_loss_pct}%)")
            elif atr_percent > 0:
                atr_sl = round(atr_percent * 2.0, 2)
                sl_pct = max(self.settings.stop_loss_pct, atr_sl)
                logger.info(f"[TRADER] {symbol}: SL={sl_pct}% (ATR={atr_percent:.2f}%, legacy mode)")
            else:
                sl_pct = self.settings.stop_loss_pct
            
            if position_side == "SHORT":
                # SHORT: SL выше цены, TP ниже
                stop_loss = entry_price * (1 + sl_pct / 100)
                take_profit_1 = entry_price * (1 - tp_pct / 100 * 0.5)  # TP1 = 50% от TP
                take_profit_2 = entry_price * (1 - tp_pct / 100)        # TP2 = полный TP
            else:  # LONG
                # LONG: SL ниже цены, TP выше
                stop_loss = entry_price * (1 - sl_pct / 100)
                take_profit_1 = entry_price * (1 + tp_pct / 100 * 0.5)  # TP1 = 50% от TP
                take_profit_2 = entry_price * (1 + tp_pct / 100)        # TP2 = полный TP
            
            logger.info(f"[TRADER] {symbol} {position_side}: SL={sl_pct}%, TP={tp_pct}% (из Settings)")
            
            # v5.8.6: Детальное логирование ДО валидации
            logger.info(f"[TRADER] {symbol} BEFORE validate: entry={entry_price:.6f}, SL={stop_loss:.6f}, TP1={take_profit_1:.6f}")
            
            # Валидация уровней
            stop_loss, take_profit_1, take_profit_2, was_corrected = self._validate_position_levels(
                entry_price, stop_loss, take_profit_1, take_profit_2, position_side, atr_percent
            )
            
            # v5.8.6: Логирование ПОСЛЕ валидации
            actual_sl_pct = abs((stop_loss / entry_price - 1) * 100) if position_side == "SHORT" else abs((1 - stop_loss / entry_price) * 100)
            logger.info(f"[TRADER] {symbol} AFTER validate: SL={stop_loss:.6f} ({actual_sl_pct:.2f}%), corrected={was_corrected}")
            
            if was_corrected:
                self._add_log("warning", f"⚠️ SL/TP скорректированы для {symbol} {position_side} (ATR: {atr_percent:.2f}%)")
            
            # Получаем адаптивные параметры трейлинга
            adaptive_params = self._get_adaptive_trailing_params(symbol, atr_percent)
            
            # Определяем размер позиции
            if self.settings.trade_mode == "LIVE":
                position_size = self.settings.position_size_live
            else:
                position_size = self.settings.position_size
            
            # Получаем тренд Bitcoin для контекста
            btc_trend = self._get_btc_trend_from_state()
            
            pos = Position(
                id=trade_id,
                symbol=symbol,
                side=position_side,  # SHORT или LONG
                entry_price=entry_price,
                current_price=entry_price,
                stop_loss=stop_loss,
                take_profit_1=take_profit_1,
                take_profit_2=take_profit_2,
                size_usdt=position_size,
                leverage=self.settings.leverage,
                trailing_stop=stop_loss,
                initial_stop_loss=stop_loss,
                opened_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                ai_confidence=signal.get('confidence', 0),
                ai_reason=signal.get('reason', ''),
                ai_analysis_ru=signal.get('analysis_ru', ''),
                ai_provider=signal.get('provider', 'deepseek'),
                change_24h=signal.get('change_24h', 0),
                change_24h_at_open=signal.get('change_24h_at_open', signal.get('change_24h', 0)),
                atr_percent=atr_percent,
                trade_mode=self.settings.trade_mode,
                adaptive_trailing_enabled=self.settings.adaptive_trailing_enabled,
                # v5.8.6: Дефолты из settings, а не хардкод 2.0!
                trailing_activation_pct=adaptive_params.get('activation_pct', self.settings.trailing_activation_pct),
                trailing_distance_pct=adaptive_params.get('distance_pct', self.settings.trailing_distance_pct),
                btc_trend_at_open=btc_trend,
                # Индикаторы при входе для пост-мортем анализа
                rsi_at_entry=signal.get('rsi_15m', 50.0),
                bollinger_b_at_entry=signal.get('bollinger_b', 50.0),
                macd_histogram_at_entry=signal.get('macd_histogram', 0.0),
                macd_divergence_at_entry=signal.get('macd_divergence', 'none'),
                # Защита прибыли
                original_size_usdt=position_size
            )
            
            # Вычитаем маржу
            margin = position_size / self.settings.leverage
            self.balance -= margin
            self.positions[trade_id] = pos
            
            # Сохраняем в БД
            try:
                db.save_trade_open({
                    'trade_id': trade_id,
                    'symbol': symbol,
                    'side': position_side,  # Сохраняем направление
                    'entry_price': pos.entry_price,
                    'stop_loss': pos.stop_loss,
                    'trailing_stop': pos.trailing_stop,
                    'take_profit_1': pos.take_profit_1,
                    'take_profit_2': pos.take_profit_2,
                    'ai_confidence': pos.ai_confidence,
                    'ai_reason': pos.ai_reason,
                    'ai_analysis_ru': pos.ai_analysis_ru,
                    'ai_provider': pos.ai_provider,
                    'change_24h': pos.change_24h,
                    'change_24h_at_open': pos.change_24h_at_open,
                    'atr_percent': pos.atr_percent,
                    'position_size': pos.size_usdt,
                    'leverage': pos.leverage,
                    'trade_mode': pos.trade_mode,
                    'sl_pct_used': sl_pct,
                    'trail_activation_used': pos.trailing_activation_pct,
                    'trail_distance_used': pos.trailing_distance_pct,
                    'sl_mode': adaptive_params.get('mode', 'fixed')
                })
            except Exception as e:
                logger.error(f"[TRADER] DB save error: {e}")
            
            # Лог с информацией об ATR и адаптивном трейлинге
            mode = adaptive_params.get('mode', 'default')
            formula = adaptive_params.get('formula', '')
            if mode in ['adaptive', 'atr_based']:
                trailing_info = f"ATR-адаптивный: {pos.trailing_distance_pct:.1f}%"
                if formula:
                    logger.info(f"[TRADER] {symbol} трейлинг: {formula}")
            else:
                trailing_info = f"Фиксированный: {pos.trailing_distance_pct:.1f}%"
            
            # Разные эмодзи и цвета для SHORT и LONG
            emoji = "🔴" if position_side == "SHORT" else "🟢"
            self._add_log("trade_open",
                f"{emoji} {position_side} {symbol} @ ${pos.entry_price:.6f} | " +
                f"SL: ${pos.stop_loss:.6f} | TP1: ${pos.take_profit_1:.6f} | " +
                f"Трейлинг: {trailing_info}")
            
            # Подробный лог в консоль
            logger.info(f"[TRADER] Открыт {position_side} {symbol}: ATR={atr_percent:.2f}%, " +
                      f"Трейлинг={pos.trailing_distance_pct:.1f}% (режим: {mode})")
            self._save_state()
            return pos
    
    def update_positions(self, prices: Dict[str, float]):
        """Обновление позиций с адаптивным трейлинг-стопом и защитой прибыли"""
        with self.lock:
            to_close = []
            total_unrealized_pnl = 0.0  # Для equity protection
            
            for tid, pos in list(self.positions.items()):
                if pos.status != "OPEN":
                    continue
                
                if pos.symbol not in prices:
                    continue
                
                # ═══ АВТОЗАКРЫТИЕ: Позиция слишком старая ═══
                if self.settings.max_position_age_hours > 0:
                    try:
                        opened_at = pos.opened_at
                        if isinstance(opened_at, str):
                            opened_at = datetime.strptime(opened_at, '%Y-%m-%d %H:%M:%S')
                        age_hours = (datetime.now() - opened_at).total_seconds() / 3600
                        if age_hours > self.settings.max_position_age_hours:
                            to_close.append((tid, "MAX_AGE"))
                            self._add_log("close", f"⏰ {pos.symbol} {pos.side}: закрыт по возрасту ({age_hours:.0f}ч > {self.settings.max_position_age_hours}ч)")
                            continue
                    except Exception as e:
                        logger.debug(f"[TRADER] Age check error: {e}")
                
                price = prices[pos.symbol]
                # Валидация цены
                if price is None or price <= 0:
                    logger.warning(f"[TRADER] Invalid price for {pos.symbol}: {price}")
                    continue
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА: цена должна быть в разумных пределах от entry
                price_change_pct = abs(price - pos.entry_price) / pos.entry_price * 100
                
                # Если цена изменилась более чем на 50% - это аномалия, НЕ обновляем
                if price_change_pct > 50:
                    logger.warning(f"[TRADER] Suspicious price change {pos.symbol}: {price_change_pct:.1f}% (entry={pos.entry_price:.6f}, price={price:.6f})")
                    continue
                
                # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: цена должна быть в том же порядке величины
                # Если entry = $1.779, а price = $0.8 - это явно баг!
                if pos.entry_price > 0:
                    price_ratio = price / pos.entry_price
                    if price_ratio < 0.3 or price_ratio > 3.0:
                        logger.error(f"[TRADER] ⚠️ PRICE ANOMALY {pos.symbol}: ratio={price_ratio:.2f} (entry={pos.entry_price:.6f}, price={price:.6f})")
                        continue
                
                # Цена прошла валидацию - сохраняем
                pos.current_price = price
                pos.last_valid_price = price
                pos.last_price_update = datetime.now()
                
                # Расчёт PnL для SHORT и LONG с учётом комиссий
                commission = self.settings.commission_rate if self.settings.commission_enabled else 0
                pnl_usdt, pnl_percent = pos.calculate_current_pnl(commission)
                pos.pnl_usdt = pnl_usdt
                pos.pnl_percent = pnl_percent
                
                # Обновляем максимальный PnL для защиты прибыли
                if pnl_percent > pos.max_pnl_percent:
                    pos.max_pnl_percent = pnl_percent
                
                # Суммируем нереализованный PnL
                total_unrealized_pnl += pnl_usdt + pos.partial_tp_pnl
                
                # ═══ ЗАЩИТА ПРИБЫЛИ ═══
                
                # 1. BREAKEVEN STOP - ОТКЛЮЧЕНО v5.6!
                # Причина: Trailing Stop делает всё лучше (99.98% успеха в бэктесте)
                # Breakeven создаёт путаницу и убытки из-за комиссий
                # if self.settings.breakeven_enabled and not pos.breakeven_activated:
                #     ... (код отключён)
                # 2. PARTIAL TP - закрыть часть позиции на TP1
                if self.settings.partial_tp_enabled and not pos.partial_tp_done:
                    tp1_hit = False
                    if pos.side == "SHORT" and price <= pos.take_profit_1:
                        tp1_hit = True
                    elif pos.side == "LONG" and price >= pos.take_profit_1:
                        tp1_hit = True
                    
                    if tp1_hit:
                        # Закрываем часть позиции
                        close_percent = self.settings.partial_tp_percent / 100.0
                        partial_size = pos.size_usdt * close_percent
                        partial_pnl = pnl_usdt * close_percent
                        
                        # Возвращаем часть маржи + PnL в balance (ИСПРАВЛЕНО v5.5!)
                        partial_margin = partial_size / self.settings.leverage
                        self.balance += partial_margin + partial_pnl
                        
                        # Уменьшаем размер позиции
                        pos.size_usdt = pos.size_usdt * (1 - close_percent)
                        pos.partial_tp_pnl = partial_pnl
                        pos.partial_tp_done = True
                        
                        # Фиксируем прибыль (не дублируем в total_pnl - уже в balance!)
                        self.total_pnl += partial_pnl
                        self.daily_pnl += partial_pnl
                        
                        logger.info(f"[PARTIAL_TP] {pos.symbol} {pos.side}: закрыто {close_percent*100:.0f}% = ${partial_pnl:.2f}, маржа ${partial_margin:.2f}")
                        self._add_log("protection", f"💰 {pos.symbol}: Частичный TP! Закрыто {close_percent*100:.0f}%, зафиксировано ${partial_pnl:.2f}")
                        db.save_trade_event(pos.id, "PARTIAL_TP", price, pnl_percent, pos.stop_loss, f"Closed {close_percent*100:.0f}%, PnL: ${partial_pnl:.2f}")
                
                # АДАПТИВНЫЙ ТРЕЙЛИНГ-СТОП С ВОЛАТИЛЬНОСТЬЮ
                if self.settings.trailing_enabled:
                    trail_updated, new_stop = self._update_trailing_stop(pos, price)
                    if trail_updated:
                        profit_pct = (pos.entry_price - price) / pos.entry_price * 100 if pos.side == "SHORT" else (price - pos.entry_price) / pos.entry_price * 100
                        self._add_log("trailing",
                            f"📈 {pos.symbol} {pos.side}: SL → ${pos.stop_loss:.6f} (прибыль: {profit_pct:.2f}%)")
                
                # Проверка SL (включая трейлинг)
                if pos.stop_loss > 0:
                    if (pos.side == "SHORT" and price >= pos.stop_loss) or (pos.side == "LONG" and price <= pos.stop_loss):
                        # ИСПРАВЛЕНО v5.6: Trailing имеет приоритет над Breakeven!
                        if pos.trail_activated:
                            reason = "TRAILING_STOP"
                        elif pos.breakeven_activated:
                            reason = "BREAKEVEN_STOP"
                        else:
                            reason = "STOP_LOSS"
                        to_close.append((tid, reason))
                        logger.info(f"[TRADER] {reason} hit: {pos.symbol} {pos.side}")
                
                # Проверка TP (ИСПРАВЛЕНО v5.6: отдельный if вместо elif!)
                if pos.take_profit_1 > 0:
                    # Если partial TP включен и сработал - ждём TP2
                    if self.settings.partial_tp_enabled and pos.partial_tp_done:
                        if (pos.side == "SHORT" and price <= pos.take_profit_2) or (pos.side == "LONG" and price >= pos.take_profit_2):
                            to_close.append((tid, "TAKE_PROFIT_2"))
                            logger.info(f"[TRADER] TP2 hit: {pos.symbol} {pos.side}")
                    else:
                        if (pos.side == "SHORT" and price <= pos.take_profit_1) or (pos.side == "LONG" and price >= pos.take_profit_1):
                            to_close.append((tid, "TAKE_PROFIT"))
                            logger.info(f"[TRADER] TP hit: {pos.symbol} {pos.side}")
            
            # 3. EQUITY PROTECTION - проверяем просадку от пика
            current_equity = self.total_pnl + total_unrealized_pnl
            
            # Рассчитываем порог активации = комиссии × множитель
            open_positions = [p for p in self.positions.values() if p.status == "OPEN"]
            total_size = sum(p.size_usdt for p in open_positions)
            commission_rate = self.settings.commission_rate if self.settings.commission_enabled else 0.05
            total_commissions = (total_size * commission_rate / 100) * 2  # открытие + закрытие
            activation_threshold = total_commissions * self.settings.equity_activation_multiplier
            
            # Сохраняем для отображения в UI
            self._equity_activation_threshold = activation_threshold
            
            # Обновляем пик когда equity растёт И превысил порог активации
            if current_equity > self.equity_peak:
                # Пик записываем только если превысили порог активации
                if current_equity >= activation_threshold:
                    old_peak = self.equity_peak
                    self.equity_peak = current_equity
                    if old_peak < activation_threshold or current_equity > old_peak * 1.1:
                        logger.info(f"[EQUITY] 📈 New peak: ${current_equity:.2f} (threshold: ${activation_threshold:.2f})")
            
            # Проверяем просадку
            if self.settings.equity_protection_enabled and not self.equity_protection_triggered:
                # Защита активна только если пик >= порога активации
                if self.equity_peak >= activation_threshold:
                    drawdown_pct = ((self.equity_peak - current_equity) / self.equity_peak * 100)
                    drawdown_abs = self.equity_peak - current_equity
                    
                    threshold_pct = self.settings.equity_drawdown_pct
                    threshold_abs = self.settings.equity_drawdown_abs
                    
                    # Срабатывает если просадка >= порога (% ИЛИ абсолютная)
                    if drawdown_pct >= threshold_pct or drawdown_abs >= threshold_abs:
                        self.equity_protection_triggered = True
                        logger.warning(f"[EQUITY PROTECTION] 🛑 TRIGGERED! Peak: ${self.equity_peak:.2f}, Current: ${current_equity:.2f}, Drawdown: {drawdown_pct:.1f}%")
                        self._add_log("protection", f"⛔ EQUITY PROTECTION! Просадка {drawdown_pct:.1f}% от пика ${self.equity_peak:.2f}. ЗАКРЫВАЕМ ВСЁ!")
                        
                        # Закрываем ВСЕ открытые позиции
                        for tid, pos in list(self.positions.items()):
                            if pos.status == "OPEN":
                                to_close.append((tid, "EQUITY_PROTECTION"))
            
            # Закрываем позиции
            for tid, reason in to_close:
                self._close_position(tid, reason)
            
            # Проверка дневного лимита
            self._check_daily_limits()
            
            # Проверка недельного лимита
            self._check_weekly_limits()
    
    def check_stops(self):
        """Проверка стоп-лоссов и тейк-профитов"""
        with self.lock:
            to_close = []
            current_time = datetime.now()
            
            for tid, pos in list(self.positions.items()):
                if pos.status != "OPEN":
                    continue
                
                # Если цена не обновлена долгое время, пропускаем
                lpu = getattr(pos, 'last_price_update', None)
                if lpu is None:
                    continue
                if isinstance(lpu, str):
                    try:
                        lpu = datetime.fromisoformat(lpu)
                    except Exception:
                        continue
                if (current_time - lpu).total_seconds() > 300:
                    continue
                
                current_price = pos.current_price
                
                # Проверка стоп-лосса
                if pos.stop_loss > 0:
                    if (pos.side == "SHORT" and current_price >= pos.stop_loss) or (pos.side == "LONG" and current_price <= pos.stop_loss):
                        reason = "TRAILING_STOP" if pos.trail_activated else "STOP_LOSS"
                        to_close.append((tid, reason))
                        logger.info(f"[TRADER] {reason} hit: {pos.symbol} {pos.side}")
                
                # Проверка тейк-профита
                if pos.take_profit_1 > 0:
                    if (pos.side == "SHORT" and current_price <= pos.take_profit_1) or (pos.side == "LONG" and current_price >= pos.take_profit_1):
                        to_close.append((tid, "TAKE_PROFIT"))
                        logger.info(f"[TRADER] TP hit: {pos.symbol} {pos.side}")
            
            # Закрытие позиций
            for tid, reason in to_close:
                self._close_position(tid, reason)
    
    def _close_position(self, trade_id: str, reason: str) -> Optional[Dict]:
        """Закрыть позицию"""
        if trade_id not in self.positions:
            return None
        
        pos = self.positions[trade_id]
        pos.status = reason
        pos.closed_at = get_gmt2_str()
        pos.close_reason = reason
        
        # ═══ v5.8.4: ЗАКРЫТИЕ ПО ЦЕНЕ УРОВНЯ (симуляция лимитного ордера) ═══
        # Для SL/TP закрываем по цене уровня + небольшое проскальзывание
        # Это симулирует реальный лимитный ордер на бирже
        
        slippage_pct = 0.05  # 0.05% проскальзывание (реалистично для лимиток)
        
        if "STOP_LOSS" in reason.upper() or "TRAILING" in reason.upper():
            # Закрываем по цене SL (trailing_stop или stop_loss)
            sl_price = pos.trailing_stop if pos.trail_activated else pos.stop_loss
            if sl_price > 0:
                # Добавляем небольшое проскальзывание в худшую сторону
                if pos.side == "SHORT":
                    exit_price = sl_price * (1 + slippage_pct / 100)  # Чуть выше для SHORT
                else:  # LONG
                    exit_price = sl_price * (1 - slippage_pct / 100)  # Чуть ниже для LONG
                logger.info(f"[CLOSE] {reason} @ SL price {sl_price:.6f} + slip = {exit_price:.6f}")
            else:
                exit_price = pos.current_price
        elif "TAKE_PROFIT" in reason.upper():
            # Закрываем по цене TP
            tp_price = pos.take_profit_1
            if tp_price > 0:
                # Добавляем небольшое проскальзывание в худшую сторону
                if pos.side == "SHORT":
                    exit_price = tp_price * (1 - slippage_pct / 100)  # Чуть ниже для SHORT (хуже)
                else:  # LONG
                    exit_price = tp_price * (1 + slippage_pct / 100)  # Чуть выше для LONG (лучше, но slip)
                logger.info(f"[CLOSE] TP @ TP price {tp_price:.6f} + slip = {exit_price:.6f}")
            else:
                exit_price = pos.current_price
        else:
            # Для ручного закрытия - по текущей цене
            exit_price = pos.current_price
        
        # ═══ ВАЛИДАЦИЯ EXIT_PRICE v5.5 ═══
        # Проверка что exit_price в разумных пределах от entry
        if pos.entry_price > 0:
            price_ratio = exit_price / pos.entry_price if exit_price > 0 else 0
            # Если ratio вне диапазона 0.5-2.0 - это аномалия!
            if price_ratio < 0.5 or price_ratio > 2.0:
                logger.warning(f"[CLOSE] ⚠️ EXIT_PRICE ANOMALY {pos.symbol}: ratio={price_ratio:.2f}")
                # Пробуем использовать last_valid_price
                if pos.last_valid_price > 0:
                    alt_ratio = pos.last_valid_price / pos.entry_price
                    if 0.5 <= alt_ratio <= 2.0:
                        exit_price = pos.last_valid_price
                        logger.info(f"[CLOSE] Using last_valid_price: {exit_price:.6f}")
                    else:
                        exit_price = pos.entry_price
                        logger.warning(f"[CLOSE] Using entry_price as fallback: {exit_price:.6f}")
                else:
                    exit_price = pos.entry_price
                    logger.warning(f"[CLOSE] Using entry_price as fallback: {exit_price:.6f}")
        
        # Сохраняем exit_price
        pos.exit_price = exit_price
        pos.current_price = exit_price  # Синхронизируем
        
        # ═══ ПЕРЕСЧИТЫВАЕМ PnL С ПРАВИЛЬНОЙ ЦЕНОЙ ═══
        commission = self.settings.commission_rate if self.settings.commission_enabled else 0
        pnl_usdt, pnl_percent = pos.calculate_current_pnl(commission)
        pos.pnl_usdt = pnl_usdt
        pos.pnl_percent = pnl_percent
        
        # ═══ ИТОГОВЫЙ РАСЧЁТ PnL v5.5 ═══
        final_remaining_pnl = pos.pnl_usdt  # PnL от оставшейся части
        total_trade_pnl = final_remaining_pnl + pos.partial_tp_pnl  # Общий PnL сделки
        
        # Возвращаем маржу + PnL оставшейся части
        margin = pos.size_usdt / self.settings.leverage
        self.balance += margin + final_remaining_pnl
        self.total_pnl += final_remaining_pnl  # partial_tp_pnl уже был добавлен ранее
        self.daily_pnl += final_remaining_pnl
        self.weekly_pnl += final_remaining_pnl
        
        # Win/Lose определяем по ОБЩЕМУ PnL сделки (включая partial)
        if total_trade_pnl >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
            self.last_loss_time = get_gmt2_time()
            # Кулдаун ПО МОНЕТЕ — не входить повторно после SL
            sym_clean = pos.symbol.replace('/USDT:USDT', '').replace('/USDT', '')
            self.symbol_cooldowns[sym_clean] = get_gmt2_time()
            self.symbol_daily_losses[sym_clean] = self.symbol_daily_losses.get(sym_clean, 0) + 1
            logger.info(f"[COOLDOWN] {sym_clean}: SL #{self.symbol_daily_losses[sym_clean]} за день, "
                       f"кулдаун {self.settings.symbol_cooldown_min} мин")
        
        # Сохраняем общий PnL для отображения
        pos.pnl_usdt = total_trade_pnl  # Теперь содержит полный PnL
        # Пересчитываем процент от изначального размера позиции
        original_size = pos.original_size_usdt if pos.original_size_usdt > 0 else pos.size_usdt
        if original_size > 0:
            pos.pnl_percent = (total_trade_pnl / original_size) * 100 * self.settings.leverage
        
        # ПОСТ-МОРТЕМ для убыточных сделок
        if pos.pnl_usdt < 0:
            self._create_post_mortem(pos)
        
        self.closed_positions.append(pos)
        if len(self.closed_positions) > 500:
            self.closed_positions = self.closed_positions[-500:]
        
        del self.positions[trade_id]
        
        # Сохраняем в БД
        try:
            db.save_trade_close(trade_id, {
                'exit_price': pos.exit_price,  # Используем валидированный exit_price
                'pnl_usdt': pos.pnl_usdt,
                'pnl_percent': pos.pnl_percent,
                'close_reason': reason,
                'atr_percent': pos.atr_percent,
                'change_24h': pos.change_24h,
                'side': pos.side,
                'max_pnl_percent': getattr(pos, 'max_pnl_percent', 0)
            })
            
            # Сохраняем событие CLOSE в истории хода сделки
            db.save_trade_price_event(
                trade_id=pos.id,
                event_type='CLOSE',
                price=pos.exit_price,  # Используем валидированный exit_price
                pnl_percent=pos.pnl_percent,
                trailing_stop=pos.trailing_stop,
                details=f"{reason}, PnL=${pos.pnl_usdt:.2f}"
            )
        except Exception as e:
            logger.error(f"[TRADER] DB close error: {e}")
        
        # Лог
        emoji = "🟢" if pos.pnl_usdt >= 0 else "🔴"
        trail_info = " (Трейлинг)" if reason == "TRAILING_STOP" else ""
        self._add_log("trade_close",
            f"{emoji} {pos.symbol} {pos.side}{trail_info} | {reason} | " +
            f"${pos.pnl_usdt:+.2f} ({pos.pnl_percent:+.1f}%)")
        self._save_state()
        
        # Callback для Crypto Agent
        if hasattr(self, 'on_position_closed') and self.on_position_closed:
            try:
                # Передаём trade_id для untrack_position
                self.on_position_closed(pos.symbol, pos.pnl_usdt, reason, pos.side, pos.id)
            except Exception as e:
                logger.error(f"[TRADER] on_position_closed error: {e}")
        
        return {"pnl": pos.pnl_usdt, "reason": reason, "trail_activated": pos.trail_activated}
    
    def close_position_manual(self, trade_id: str, reason: str = "MANUAL") -> Optional[Dict]:
        """Ручное закрытие позиции"""
        with self.lock:
            return self._close_position(trade_id, reason)
    
    def close_stale_positions(self, max_stale_minutes: int = 60) -> List[Dict]:
        """
        Закрыть зависшие позиции (цена не обновлялась долго)
        
        Args:
            max_stale_minutes: Максимальное время без обновления цены
            
        Returns:
            Список закрытых позиций
        """
        closed = []
        current_time = get_gmt2_time()
        
        with self.lock:
            stale_positions = []
            
            for tid, pos in self.positions.items():
                if pos.status != "OPEN":
                    continue
                
                # Проверяем признаки зависшей позиции:
                # 1. PnL ≈ -0.4% (комиссия) и не меняется
                # 2. Цена не обновлялась (current_price == entry_price или очень близко)
                price_diff_pct = abs(pos.current_price - pos.entry_price) / pos.entry_price * 100 if pos.entry_price > 0 else 0
                
                is_stale = (
                    abs(pos.pnl_percent) < 1.0 and  # PnL почти не изменился
                    price_diff_pct < 0.5 and  # Цена почти не изменилась
                    pos.pnl_usdt < 0  # Убыток (комиссия)
                )
                
                if is_stale:
                    # Проверяем время открытия
                    try:
                        opened_at = datetime.strptime(pos.opened_at, '%Y-%m-%d %H:%M:%S')
                        minutes_open = (current_time - opened_at).total_seconds() / 60
                        
                        if minutes_open > max_stale_minutes:
                            stale_positions.append(tid)
                            logger.warning(f"[TRADER] Stale position detected: {pos.symbol} {pos.side}, "
                                         f"open for {minutes_open:.0f} min, PnL={pos.pnl_percent:.2f}%")
                    except Exception as e:
                        logger.error(f"[TRADER] Error checking stale position {tid}: {e}")
            
            # Закрываем зависшие
            for tid in stale_positions:
                result = self._close_position(tid, "STALE_DATA")
                if result:
                    pos = self.closed_positions[-1] if self.closed_positions else None
                    closed.append({
                        'trade_id': tid,
                        'symbol': pos.symbol if pos else 'N/A',
                        'pnl': result.get('pnl', 0),
                        'reason': 'STALE_DATA (нет данных по монете)'
                    })
                    self._add_log("system", f"⚠️ Закрыта зависшая позиция: {pos.symbol if pos else tid}")
        
        return closed
    
    def update_stop_loss(self, trade_id: str, new_stop_loss: float) -> bool:
        """Обновить Stop Loss для позиции"""
        with self.lock:
            if trade_id not in self.positions:
                return False
            
            pos = self.positions[trade_id]
            if pos.status != "OPEN":
                return False
            
            old_sl = pos.stop_loss
            
            # Валидация нового уровня в зависимости от направления
            if pos.side == "SHORT":
                # Для SHORT: SL должен быть ВЫШЕ цены входа
                if new_stop_loss <= pos.entry_price:
                    logger.warning(f"[TRADER] Invalid SL for SHORT {pos.symbol}: {new_stop_loss} <= entry {pos.entry_price}")
                    return False
                if new_stop_loss < pos.current_price * 0.95:  # Защита от слишком низкого SL
                    new_stop_loss = pos.current_price * 0.95
            else:  # LONG
                # Для LONG: SL должен быть НИЖЕ цены входа
                if new_stop_loss >= pos.entry_price:
                    logger.warning(f"[TRADER] Invalid SL for LONG {pos.symbol}: {new_stop_loss} >= entry {pos.entry_price}")
                    return False
                if new_stop_loss > pos.current_price * 1.05:  # Защита от слишком высокого SL
                    new_stop_loss = pos.current_price * 1.05
            
            pos.stop_loss = new_stop_loss
            
            # Обновляем трейлинг-стоп если он активирован
            if pos.trail_activated:
                pos.trailing_stop = new_stop_loss
            
            # Лог изменений
            self._add_log("update", f"🎯 {pos.symbol} {pos.side}: SL изменен с ${old_sl:.6f} на ${new_stop_loss:.6f}")
            
            # Сохраняем в БД
            try:
                db.update_trade_stop_loss(trade_id, new_stop_loss)
                # Сохраняем событие обновления SL
                db.save_trade_price_event(
                    trade_id=trade_id,
                    event_type='SL_UPDATED',
                    price=pos.current_price,
                    pnl_percent=pos.pnl_percent,
                    trailing_stop=new_stop_loss,
                    details=f"SL: {old_sl:.6f} -> {new_stop_loss:.6f}"
                )
            except Exception as e:
                logger.error(f"[TRADER] SL update DB error: {e}")
            
            self._save_state()
            return True
    
    def update_position_price(self, trade_id: str, new_price: float) -> bool:
        """
        Обновить текущую цену позиции (для WebSocket)
        v5.9: Вызывается при каждом тике от WebSocket
        """
        with self.lock:
            if trade_id not in self.positions:
                return False
            
            pos = self.positions[trade_id]
            if pos.status != "OPEN":
                return False
            
            # Обновляем цену
            old_price = pos.current_price
            pos.current_price = new_price
            pos.last_price_update = datetime.now()
            
            # Валидация цены (сохраняем last_valid_price)
            if pos.entry_price > 0:
                ratio = new_price / pos.entry_price
                if 0.5 <= ratio <= 2.0:
                    pos.last_valid_price = new_price
            
            # Обновляем PnL
            commission = self.settings.commission_rate if self.settings.commission_enabled else 0
            pos.pnl_usdt, pos.pnl_percent = pos.calculate_current_pnl(commission)
            
            # Обновляем trailing stop
            if self.settings.trailing_enabled:
                self._update_trailing_stop(pos, new_price)
            
            return True
    
    def update_take_profit(self, trade_id: str, new_take_profit: float) -> bool:
        """Обновить Take Profit для позиции"""
        with self.lock:
            if trade_id not in self.positions:
                return False
            
            pos = self.positions[trade_id]
            if pos.status != "OPEN":
                return False
            
            old_tp = pos.take_profit_1
            
            # Валидация нового уровня в зависимости от направления
            if pos.side == "SHORT":
                # Для SHORT: TP должен быть НИЖЕ цены входа
                if new_take_profit >= pos.entry_price:
                    logger.warning(f"[TRADER] Invalid TP for SHORT {pos.symbol}: {new_take_profit} >= entry {pos.entry_price}")
                    return False
                if new_take_profit > pos.current_price * 1.05:  # Защита от слишком высокого TP
                    new_take_profit = pos.current_price * 1.05
            else:  # LONG
                # Для LONG: TP должен быть ВЫШЕ цены входа
                if new_take_profit <= pos.entry_price:
                    logger.warning(f"[TRADER] Invalid TP for LONG {pos.symbol}: {new_take_profit} <= entry {pos.entry_price}")
                    return False
                if new_take_profit < pos.current_price * 0.95:  # Защита от слишком низкого TP
                    new_take_profit = pos.current_price * 0.95
            
            pos.take_profit_1 = new_take_profit
            
            # Лог изменений
            self._add_log("update", f"💰 {pos.symbol} {pos.side}: TP1 изменен с ${old_tp:.6f} на ${new_take_profit:.6f}")
            
            # Сохраняем в БД
            try:
                db.update_trade_take_profit(trade_id, new_take_profit)
                # Сохраняем событие обновления TP
                db.save_trade_price_event(
                    trade_id=trade_id,
                    event_type='TP_UPDATED',
                    price=pos.current_price,
                    pnl_percent=pos.pnl_percent,
                    trailing_stop=pos.trailing_stop,
                    details=f"TP1: {old_tp:.6f} -> {new_take_profit:.6f}"
                )
            except Exception as e:
                logger.error(f"[TRADER] TP update DB error: {e}")
            
            self._save_state()
            return True
    
    # =========================================================================
    # AGENT V3 METHODS - Методы для полной автономности агента
    # =========================================================================
    
    def partial_close(self, trade_id: str, percent: int) -> Optional[Dict]:
        """Частичное закрытие позиции (25/50/75%)"""
        with self.lock:
            if trade_id not in self.positions:
                logger.warning(f"[PARTIAL_CLOSE] Position {trade_id} NOT FOUND! Available: {list(self.positions.keys())}")
                return None
            
            pos = self.positions[trade_id]
            if pos.status != "OPEN":
                logger.warning(f"[PARTIAL_CLOSE] Position {trade_id} not OPEN (status={pos.status})")
                return None
            
            if percent not in [25, 50, 75]:
                logger.warning(f"[TRADER] Invalid partial close percent: {percent}")
                return None
            
            # Вычисляем размер для закрытия
            close_size = pos.size_usdt * (percent / 100)
            remaining_size = pos.size_usdt - close_size
            
            # Вычисляем PnL закрываемой части
            partial_pnl = pos.pnl_usdt * (percent / 100)
            
            # ИСПРАВЛЕНО v5.5: Возвращаем часть маржи + PnL в balance
            partial_margin = close_size / self.settings.leverage
            self.balance += partial_margin + partial_pnl
            self.total_pnl += partial_pnl
            self.daily_pnl += partial_pnl
            
            # Обновляем позицию
            pos.size_usdt = remaining_size
            pos.original_size_usdt = pos.original_size_usdt or (pos.size_usdt + close_size)
            pos.partial_tp_pnl += partial_pnl
            pos.partial_tp_done = True
            
            # Пересчитываем PnL оставшейся части
            pos.pnl_usdt = pos.pnl_usdt * ((100 - percent) / 100)
            
            self._add_log("partial_close", f"📊 {pos.symbol}: Частичное закрытие {percent}% (${partial_pnl:.2f})")
            
            # Сохраняем событие
            try:
                db.save_trade_price_event(
                    trade_id=trade_id,
                    event_type='PARTIAL_CLOSE',
                    price=pos.current_price,
                    pnl_percent=pos.pnl_percent,
                    trailing_stop=pos.trailing_stop,
                    details=f"Partial {percent}%: ${partial_pnl:.2f}"
                )
            except Exception as e:
                logger.error(f"[TRADER] Partial close DB error: {e}")
            
            self._save_state()
            return {'partial_pnl': partial_pnl, 'remaining_size': remaining_size}
    
    def set_breakeven(self, trade_id: str) -> bool:
        """Перевести SL на уровень безубытка (entry price + комиссия)
        ВАЖНО: Не ухудшает защиту если trailing уже подтянул SL лучше!
        """
        with self.lock:
            if trade_id not in self.positions:
                return False
            
            pos = self.positions[trade_id]
            if pos.status != "OPEN":
                return False
            
            # Breakeven = entry + комиссия (примерно 0.1%)
            commission_offset = pos.entry_price * 0.001
            
            if pos.side == "SHORT":
                # Для SHORT: breakeven ниже entry
                breakeven_price = pos.entry_price - commission_offset
                
                # ЗАЩИТА: Не ухудшать если trailing уже лучше!
                # Для SHORT: SL лучше если он НИЖЕ (меньше)
                if pos.stop_loss < breakeven_price:
                    logger.info(f"[BREAKEVEN] {pos.symbol} SHORT: Trailing SL ({pos.stop_loss:.6f}) уже лучше breakeven ({breakeven_price:.6f})")
                    return True  # Уже защищено лучше
                
                # Проверяем что текущая цена позволяет
                if pos.current_price >= breakeven_price:
                    logger.warning(f"[TRADER] Cannot set breakeven for SHORT {pos.symbol}: price too high")
                    return False
            else:  # LONG
                # Для LONG: breakeven выше entry
                breakeven_price = pos.entry_price + commission_offset
                
                # ЗАЩИТА: Не ухудшать если trailing уже лучше!
                # Для LONG: SL лучше если он ВЫШЕ (больше)
                if pos.stop_loss > breakeven_price:
                    logger.info(f"[BREAKEVEN] {pos.symbol} LONG: Trailing SL ({pos.stop_loss:.6f}) уже лучше breakeven ({breakeven_price:.6f})")
                    return True  # Уже защищено лучше
                
                if pos.current_price <= breakeven_price:
                    logger.warning(f"[TRADER] Cannot set breakeven for LONG {pos.symbol}: price too low")
                    return False
            
            old_sl = pos.stop_loss
            pos.stop_loss = breakeven_price
            pos.trailing_stop = breakeven_price
            pos.breakeven_activated = True
            
            self._add_log("breakeven", f"🎯 {pos.symbol}: Breakeven активирован (SL: ${old_sl:.6f} → ${breakeven_price:.6f})")
            
            try:
                db.save_trade_price_event(
                    trade_id=trade_id,
                    event_type='BREAKEVEN',
                    price=pos.current_price,
                    pnl_percent=pos.pnl_percent,
                    trailing_stop=breakeven_price,
                    details=f"Breakeven: SL moved to entry"
                )
            except Exception as e:
                logger.error(f"[TRADER] Breakeven DB error: {e}")
            
            self._save_state()
            return True
    
    def toggle_trailing(self, trade_id: str, enabled: bool) -> bool:
        """Включить/отключить трейлинг для позиции"""
        with self.lock:
            if trade_id not in self.positions:
                return False
            
            pos = self.positions[trade_id]
            if pos.status != "OPEN":
                return False
            
            old_state = pos.trail_activated
            pos.trail_activated = enabled
            
            if enabled and not old_state:
                # При включении устанавливаем трейлинг на текущий SL
                pos.trailing_stop = pos.stop_loss
                self._add_log("trailing", f"📈 {pos.symbol}: Trailing ON")
            elif not enabled and old_state:
                self._add_log("trailing", f"📉 {pos.symbol}: Trailing OFF")
            
            self._save_state()
            return True
    
    def pause_scanner(self):
        """Остановить сканер"""
        self.scanner_paused = True
        self._add_log("scanner", "⏸️ Сканер остановлен агентом")
    
    def resume_scanner(self):
        """Возобновить сканер"""
        self.scanner_paused = False
        self._add_log("scanner", "▶️ Сканер возобновлен агентом")
    
    # =========================================================================
    # ПОСТ-МОРТЕМ
    # =========================================================================
    def _create_post_mortem(self, pos: Position):
        """Создать пост-мортем анализ для убыточной сделки"""
        try:
            now = get_gmt2_time()
            opened = datetime.strptime(pos.opened_at, "%Y-%m-%d %H:%M:%S")
            
            # Анализируем причины убытка
            analysis_points = []
            recommendations = []
            symbol = pos.symbol
            hour_opened = opened.hour
            day_of_week = opened.weekday()
            atr_percent = pos.atr_percent
            trailing_distance = pos.trailing_distance_pct
            change_24h = pos.change_24h
            pnl = pos.pnl_usdt
            close_reason = pos.close_reason
            ai_provider = pos.ai_provider
            confidence = pos.ai_confidence
            
            # 1. Анализ по времени
            hourly_stats = db.get_hourly_statistics()
            hour_stat = next((h for h in hourly_stats if h['hour'] == hour_opened), None)
            if hour_stat and hour_stat.get('total', 0) >= 5:
                wr = hour_stat.get('win_rate', 50)
                if wr < 45:
                    analysis_points.append(f"⏰ Час {hour_opened}:00 имеет низкий WR ({wr:.0f}%)")
                    recommendations.append({
                        'type': 'avoid_hour',
                        'value': hour_opened,
                        'reason': f'WR в этот час: {wr:.0f}%'
                    })
            
            # 2. Анализ по дню недели
            daily_stats = db.get_daily_statistics()
            day_stat = next((d for d in daily_stats if d['day_of_week'] == day_of_week), None)
            days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            if day_stat and day_stat.get('total', 0) >= 5:
                wr = day_stat.get('win_rate', 50)
                if wr < 45:
                    day_name = days[day_of_week] if day_of_week < 7 else 'N/A'
                    analysis_points.append(f"📅 {day_name} имеет низкий WR ({wr:.0f}%)")
                    recommendations.append({
                        'type': 'avoid_day',
                        'value': day_of_week,
                        'reason': f'WR в этот день: {wr:.0f}%'
                    })
            
            # 3. Анализ трейлинг vs ATR
            if atr_percent > 0 and trailing_distance > 0:
                ratio = trailing_distance / atr_percent
                if ratio < 0.5 and pos.side == "SHORT":
                    analysis_points.append(f"📉 Трейлинг ({trailing_distance:.1f}%) слишком узкий относительно ATR ({atr_percent:.1f}%)")
                    recommendations.append({
                        'type': 'increase_trailing',
                        'value': atr_percent * 0.7,
                        'reason': f'ATR {atr_percent:.1f}%, трейлинг был {trailing_distance:.1f}%'
                    })
                elif ratio > 2.0 and pos.side == "SHORT":
                    analysis_points.append(f"📈 Трейлинг ({trailing_distance:.1f}%) слишком широкый относительно ATR ({atr_percent:.1f}%)")
            
            # 4. Анализ по символу
            symbol_stats = db.get_symbol_statistics()
            symbol_stat = next((s for s in symbol_stats if s['symbol'] == symbol), None)
            if symbol_stat and symbol_stat.get('total', 0) >= 3:
                wr = symbol_stat.get('win_rate', 50)
                if wr < 35:
                    clean_symbol = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
                    analysis_points.append(f"🔴 {clean_symbol} имеет очень низкий WR ({wr:.0f}%)")
                    recommendations.append({
                        'type': 'blacklist_symbol',
                        'value': symbol,
                        'reason': f'WR по этой монете: {wr:.0f}%'
                    })
            
            # 5. Анализ по confidence
            if confidence < 80:
                analysis_points.append(f"⚠️ Низкий AI confidence ({confidence}%)")
                recommendations.append({
                    'type': 'increase_confidence_threshold',
                    'value': 80,
                    'reason': f'Сделка была с confidence {confidence}%'
                })
            
            # 6. Анализ причины закрытия
            if 'STOP_LOSS' in close_reason.upper():
                analysis_points.append(f"🛑 Сработал Stop Loss")
                if not recommendations:
                    recommendations.append({
                        'type': 'review_sl_distance',
                        'value': None,
                        'reason': 'SL сработал слишком быстро'
                    })
            elif 'TRAILING' in close_reason.upper():
                analysis_points.append(f"🔄 Сработал Trailing Stop")
            
            # 7. Анализ размера убытка
            if pnl < -100:
                analysis_points.append(f"💸 Большой убыток: ${abs(pnl):.2f}")
                recommendations.append({
                    'type': 'reduce_position_size',
                    'value': None,
                    'reason': f'Убыток ${abs(pnl):.2f} превышает норму'
                })
            
            # 8. Анализ тренда Bitcoin
            btc_trend = pos.btc_trend_at_open
            if isinstance(btc_trend, str):
                try:
                    btc_trend = json.loads(btc_trend)
                except Exception:
                    btc_trend = {}
            if btc_trend:
                trend = btc_trend.get('trend', 'neutral')
                strength = btc_trend.get('strength', 'weak')
                rsi_1h = btc_trend.get('rsi_1h', 50)
                if pos.side == "SHORT" and trend == "bullish":
                    analysis_points.append(f"₿ BTC был в бычьем тренде (RSI: {rsi_1h:.1f}%) - SHORT рискованный")
                    recommendations.append({
                        'type': 'respect_btc_trend',
                        'value': 'avoid_short_in_bullish_btc',
                        'reason': f'BTC RSI(1h): {rsi_1h:.1f}%'
                    })
                elif pos.side == "LONG" and trend == "bearish":
                    analysis_points.append(f"₿ BTC был в медвежьем тренде (RSI: {rsi_1h:.1f}%) - LONG рискованный")
                    recommendations.append({
                        'type': 'respect_btc_trend',
                        'value': 'avoid_long_in_bearish_btc',
                        'reason': f'BTC RSI(1h): {rsi_1h:.1f}%'
                    })
            
            # 9. НОВОЕ: Анализ RSI при входе
            rsi_at_entry = pos.rsi_at_entry
            if pos.side == "SHORT" and rsi_at_entry < 75:
                analysis_points.append(f"📊 RSI при входе: {rsi_at_entry:.1f} (нужен >75 для SHORT)")
                recommendations.append({
                    'type': 'weak_rsi_signal',
                    'value': 'require_rsi_75_for_short',
                    'reason': f'RSI был {rsi_at_entry:.1f}%, нужен >75%'
                })
            elif pos.side == "LONG" and rsi_at_entry > 40:
                analysis_points.append(f"📊 RSI при входе: {rsi_at_entry:.1f} (нужен <40 для LONG)")
                recommendations.append({
                    'type': 'weak_rsi_signal',
                    'value': 'require_rsi_40_for_long',
                    'reason': f'RSI был {rsi_at_entry:.1f}%, нужен <40%'
                })
            
            # 10. НОВОЕ: Анализ Bollinger при входе
            bollinger_b = pos.bollinger_b_at_entry
            if pos.side == "SHORT" and bollinger_b < 90:
                analysis_points.append(f"📈 Bollinger %B: {bollinger_b:.1f}% (нужен >90 для SHORT)")
                recommendations.append({
                    'type': 'weak_bollinger_signal',
                    'value': 'require_bollinger_90_for_short',
                    'reason': f'Bollinger %B был {bollinger_b:.1f}%, нужен >90%'
                })
            elif pos.side == "LONG" and bollinger_b > 10:
                analysis_points.append(f"📉 Bollinger %B: {bollinger_b:.1f}% (нужен <10 для LONG)")
                recommendations.append({
                    'type': 'weak_bollinger_signal',
                    'value': 'require_bollinger_10_for_long',
                    'reason': f'Bollinger %B был {bollinger_b:.1f}%, нужен <10%'
                })
            
            # 11. НОВОЕ: Анализ MACD дивергенции
            macd_divergence = pos.macd_divergence_at_entry
            if pos.side == "SHORT" and macd_divergence == "bullish":
                analysis_points.append(f"⚠️ MACD показывал бычью дивергенцию - против SHORT!")
                recommendations.append({
                    'type': 'ignored_macd_divergence',
                    'value': 'check_macd_divergence',
                    'reason': 'MACD bullish divergence игнорирована'
                })
            elif pos.side == "LONG" and macd_divergence == "bearish":
                analysis_points.append(f"⚠️ MACD показывал медвежью дивергенцию - против LONG!")
                recommendations.append({
                    'type': 'ignored_macd_divergence',
                    'value': 'check_macd_divergence',
                    'reason': 'MACD bearish divergence игнорирована'
                })
            
            # Подсчёт проблем для общего урока
            problem_count = len(analysis_points)
            if problem_count >= 3:
                recommendations.append({
                    'type': 'too_many_warnings',
                    'value': problem_count,
                    'reason': f'Было {problem_count} предупреждений - нужно минимум 3 подтверждения для входа'
                })
            
            # Формируем текстовый анализ
            btc_info = ""
            if btc_trend:
                btc_info = f"₿ BTC тренд: {btc_trend.get('trend', 'N/A')} ({btc_trend.get('strength', 'N/A')})"
            
            analysis_text = f"""📊 ПОСТ-МОРТЕМ АНАЛИЗ
🔴 Убыток: ${abs(pnl):.2f} ({abs(pos.pnl_percent):.1f}%)
📍 Символ: {symbol.replace('/USDT:USDT', '')}
🧭 Направление: {pos.side}
⏰ Время: {hour_opened}:00 ({days[day_of_week] if day_of_week < 7 else 'N/A'})
🤖 AI: {ai_provider} ({confidence}%)
📈 Изменение 24ч: {change_24h:+.1f}%
🎯 Причина закрытия: {close_reason}

📊 ИНДИКАТОРЫ ПРИ ВХОДЕ:
• RSI: {rsi_at_entry:.1f}%
• Bollinger %B: {bollinger_b:.1f}%
• MACD divergence: {macd_divergence}
• ATR: {atr_percent:.2f}%
{btc_info}

⚠️ ВЫЯВЛЕННЫЕ ПРОБЛЕМЫ ({problem_count}):
{chr(10).join(['• ' + p for p in analysis_points]) if analysis_points else '• Специфических проблем не выявлено'}

💡 УРОКИ:
{chr(10).join(['• ' + r['reason'] for r in recommendations]) if recommendations else '• Рекомендаций нет'}
"""
            
            # Сохраняем пост-мортем с полной окружающей обстановкой
            db.save_post_mortem({
                'trade_id': pos.id,
                'symbol': pos.symbol,
                'loss_amount': abs(pos.pnl_usdt),
                'loss_percent': abs(pos.pnl_percent),
                'hour_opened': opened.hour,
                'day_of_week': opened.weekday(),
                'atr_at_entry': pos.atr_percent,
                'trailing_distance_used': pos.trailing_distance_pct,
                'continued_pump_percent': 0,
                'analysis': analysis_text,
                'recommendations': recommendations,
                'side': pos.side,
                # НОВОЕ: Индикаторы при входе
                'rsi_at_entry': rsi_at_entry,
                'bollinger_b_at_entry': bollinger_b,
                'macd_divergence_at_entry': macd_divergence,
                'confidence_at_entry': confidence,
                'btc_trend_at_entry': btc_trend.get('trend', 'neutral') if btc_trend else 'neutral',
                'btc_strength_at_entry': btc_trend.get('strength', 'weak') if btc_trend else 'weak',
                'problem_count': problem_count
            })
            
            logger.info(f"[POST-MORTEM] Created for {pos.id}: {len(recommendations)} recommendations")
        except Exception as e:
            logger.error(f"[POST-MORTEM] Error: {e}")
    
    # =========================================================================
    # ЛИМИТЫ И ЗАЩИТА
    # =========================================================================
    def _check_daily_limits(self):
        """Проверка дневных лимитов (по GMT+2)"""
        today = get_gmt2_time().date().isoformat()
        if self.daily_date != today:
            logger.info(f"[LIMITS] Новый день (GMT+2): {today}, сброс daily_pnl (было: ${self.daily_pnl:.2f})")
            self.daily_date = today
            self.daily_pnl = 0.0
            self.daily_loss_stop = False
            self.last_loss_time = None
            self.symbol_cooldowns.clear()
            self.symbol_daily_losses.clear()
        
        if self.settings.trade_mode == "LIVE":
            max_loss = self.settings.max_daily_loss_live
        else:
            max_loss = self.settings.initial_balance * self.settings.max_daily_loss_pct / 100
        
        if self.daily_pnl < -max_loss:
            if not self.daily_loss_stop:
                self.daily_loss_stop = True
                self._add_log("warning", f"⚠️ Дневной лимит убытка достигнут: ${self.daily_pnl:.2f}")
    
    def _check_weekly_limits(self):
        """Проверка недельных лимитов (по GMT+2)"""
        today = get_gmt2_time().date()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        if self.weekly_start != week_start:
            self.weekly_start = week_start
            self.weekly_pnl = 0.0
            self.weekly_loss_stop = False
        
        max_loss = self.settings.initial_balance * self.settings.max_weekly_loss_pct / 100
        if self.weekly_pnl < -max_loss:
            if not self.weekly_loss_stop:
                self.weekly_loss_stop = True
                self._add_log("warning", f"⚠️ Недельный лимит убытка достигнут: ${self.weekly_pnl:.2f}")
    
    def reset_equity_peak(self):
        """Сбросить пик эквити и флаг защиты"""
        with self.lock:
            self.equity_peak = self.total_pnl  # Сбрасываем на текущий PnL
            self.equity_protection_triggered = False
            self._add_log("protection", f"🔄 Equity peak сброшен на ${self.equity_peak:.2f}")
            self._save_state()
    
    def get_equity_info(self) -> Dict:
        """Получить информацию об эквити"""
        with self.lock:
            # Считаем нереализованный PnL
            unrealized_pnl = sum(p.pnl_usdt + p.partial_tp_pnl for p in self.positions.values() if p.status == "OPEN")
            current_equity = self.total_pnl + unrealized_pnl
            
            drawdown_pct = ((self.equity_peak - current_equity) / self.equity_peak * 100) if self.equity_peak > 0 else 0
            drawdown_abs = self.equity_peak - current_equity if self.equity_peak > 0 else 0
            
            threshold_pct = self.equity_peak * (1 - self.settings.equity_drawdown_pct / 100) if self.equity_peak > 0 else 0
            threshold_abs = self.equity_peak - self.settings.equity_drawdown_abs if self.equity_peak > 0 else 0
            
            # Порог активации защиты
            activation_threshold = self._equity_activation_threshold
            is_active = self.equity_peak >= activation_threshold and activation_threshold > 0
            
            return {
                'current_equity': current_equity,
                'equity_peak': self.equity_peak,
                'drawdown_pct': drawdown_pct,
                'drawdown_abs': drawdown_abs,
                'threshold_pct': threshold_pct,
                'threshold_abs': threshold_abs,
                'protection_enabled': self.settings.equity_protection_enabled,
                'protection_triggered': self.equity_protection_triggered,
                'activation_threshold': activation_threshold,
                'activation_multiplier': self.settings.equity_activation_multiplier,
                'is_active': is_active  # Защита активна (пик >= порога)
            }
    
    # =========================================================================
    # GETTERS
    # =========================================================================
    def get_open_positions(self) -> List[Dict]:
        """Получить открытые позиции"""
        with self.lock:
            positions = []
            for p in self.positions.values():
                if p.status == "OPEN":
                    pos_dict = p.to_dict()
                    pos_dict['trailing_info'] = {
                        'enabled': self.settings.trailing_enabled,
                        'adaptive': self.settings.adaptive_trailing_enabled,
                        'activation_pct': p.trailing_activation_pct,
                        'distance_pct': p.trailing_distance_pct,
                        'activated': p.trail_activated,
                        'current_stop': p.stop_loss,
                        'initial_stop': p.initial_stop_loss
                    }
                    positions.append(pos_dict)
            return positions
    
    def get_closed_positions(self, limit: int = 50) -> List[Dict]:
        """Получить закрытые позиции (новые первыми!). Если в памяти нет — из БД."""
        with self.lock:
            if self.closed_positions:
                return [p.to_dict() for p in reversed(self.closed_positions[-limit:])]
            # Fallback: читаем из БД (после reset памяти)
            try:
                db_trades = db.get_trades(limit=limit, only_closed=True)
                return db_trades  # уже отсортированы DESC
            except Exception as e:
                logger.error(f"[TRADER] DB fallback error: {e}")
                return []
    
    def get_portfolio(self) -> Dict:
        """Получить данные портфеля"""
        with self.lock:
            unrealized = sum(p.pnl_usdt for p in self.positions.values() if p.status == "OPEN")
            # Также учитываем partial_tp_pnl для открытых позиций
            unrealized_with_partial = sum(
                p.pnl_usdt + p.partial_tp_pnl 
                for p in self.positions.values() 
                if p.status == "OPEN"
            )
            total_trades = self.winning_trades + self.losing_trades
            win_rate = (self.winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            # Проверка: сумма PnL из закрытых позиций
            closed_pnl_sum = sum(p.pnl_usdt for p in self.closed_positions)
            
            # Отладка: ожидаемый баланс vs реальный
            # balance должен = initial + total_pnl (с учётом маржи открытых позиций)
            open_margin = sum(p.size_usdt / self.settings.leverage for p in self.positions.values() if p.status == "OPEN")
            expected_free_balance = self.settings.initial_balance + self.total_pnl - open_margin
            
            return {
                "balance": self.balance,
                "initial_balance": self.settings.initial_balance,
                "open_margin": open_margin,
                "expected_free_balance": expected_free_balance,
                "balance_diff": round(self.balance - expected_free_balance, 2),  # Должно быть ~0
                "equity": self.balance + unrealized,
                "total_pnl": self.total_pnl,
                "daily_pnl": self.daily_pnl,
                "weekly_pnl": self.weekly_pnl,
                "unrealized_pnl": unrealized,
                "unrealized_with_partial": unrealized_with_partial,
                "closed_pnl_sum": closed_pnl_sum,
                "pnl_diff": round(self.total_pnl - closed_pnl_sum, 2),  # Должно быть ~0 или учитывать partial
                "open_positions_count": len([p for p in self.positions.values() if p.status == "OPEN"]),
                "total_trades": total_trades,
                "win_rate": win_rate,
                "max_positions": self.settings.max_positions if self.settings.trade_mode == "PAPER" else self.settings.live_max_positions,
                "trade_mode": self.settings.trade_mode,
                "trailing_enabled": self.settings.trailing_enabled,
                "adaptive_trailing_enabled": self.settings.adaptive_trailing_enabled,
                "btc_trend_filter_enabled": self.settings.btc_trend_filter_enabled,
                "daily_loss_stop": self.daily_loss_stop,
                "weekly_loss_stop": self.weekly_loss_stop
            }
    
    def get_settings(self) -> Dict:
        """Получить настройки (с маскированными ключами для UI)"""
        with self.lock:
            settings = self.settings.to_dict()
            # Маскируем ключи
            for key in ['deepseek_api_key', 'groq_api_key', 'binance_api_key',
                       'binance_secret_key', 'telegram_bot_token']:
                if settings.get(key):
                    val = settings[key]
                    if len(val) > 8:
                        settings[f'{key}_masked'] = f"{val[:4]}...{val[-4:]}"
                        settings[key] = ''  # Не отправляем полный ключ
                    else:
                        settings[f'{key}_masked'] = 'Не настроен'
            return settings
    
    def recalculate_pnl(self, from_db: bool = True) -> Dict:
        """
        Пересчитать total_pnl из закрытых позиций.
        
        Args:
            from_db: True = загрузить ВСЕ сделки из БД (точнее)
                    False = использовать только память (быстрее)
        """
        with self.lock:
            old_total = self.total_pnl
            
            if from_db:
                # Загружаем ВСЕ закрытые сделки из БД
                try:
                    all_trades = db.get_trades(limit=10000, only_closed=True, days=365)  # За год
                    
                    # Учитываем pnl_reset_at — считаем только сделки после сброса
                    pnl_reset_at = db.get_setting('pnl_reset_at', None)
                    if pnl_reset_at:
                        all_trades = [t for t in all_trades if t.get('closed_at', '') >= pnl_reset_at]
                        logger.info(f"[TRADER] Filtered by reset ({pnl_reset_at}): {len(all_trades)} trades")
                    
                    # pnl_usdt в БД уже содержит полный PnL (включая partial)
                    new_total = sum(t.get('pnl_usdt', 0) for t in all_trades if t.get('pnl_usdt') is not None)
                    logger.info(f"[TRADER] Loaded {len(all_trades)} trades from DB, total PnL: ${new_total:.2f}")
                except Exception as e:
                    logger.error(f"[TRADER] Error loading trades from DB: {e}")
                    # Fallback на память
                    new_total = sum(p.pnl_usdt for p in self.closed_positions)
            else:
                # Только из памяти (последние 100)
                # pnl_usdt уже содержит полный PnL (включая partial)
                new_total = sum(p.pnl_usdt for p in self.closed_positions)
            
            # Partial PnL от ОТКРЫТЫХ позиций (ещё не закрытых)
            # Этот partial уже был добавлен в total_pnl при срабатывании,
            # но НЕ входит в closed_positions, поэтому добавляем
            partial_from_open = sum(
                p.partial_tp_pnl 
                for p in self.positions.values() 
                if p.status == "OPEN" and p.partial_tp_done
            )
            
            # Итоговый PnL = закрытые + partial от открытых
            corrected_total = new_total + partial_from_open
            difference = corrected_total - old_total
            
            result = {
                'old_total_pnl': old_total,
                'closed_pnl_sum': new_total,
                'partial_from_open': partial_from_open,
                'new_total_pnl': corrected_total,
                'difference': difference,
                'corrected': False,
                'trades_count': len(all_trades) if from_db else len(self.closed_positions)
            }
            
            if abs(difference) > 0.01:
                logger.warning(f"[TRADER] PnL MISMATCH! Old: ${old_total:.2f}, Calculated: ${corrected_total:.2f}, Diff: ${difference:.2f}")
                self.total_pnl = corrected_total
                self._save_state()
                result['corrected'] = True
                self._add_log("system", f"⚠️ PnL пересчитан: ${old_total:.2f} → ${corrected_total:.2f} (разница: ${difference:.2f})")
            
            return result
    
    def get_settings_raw(self) -> Dict:
        """Получить настройки с полными ключами (для внутреннего использования)"""
        with self.lock:
            return self.settings.to_dict()
    
    def update_settings(self, data: Dict, source: str = 'MANUAL') -> bool:
        """Обновить настройки"""
        with self.lock:
            for k, v in data.items():
                if hasattr(self.settings, k):
                    current = getattr(self.settings, k)
                    old_value = current
                    # Преобразование типов
                    if isinstance(current, bool):
                        v = bool(v)
                    elif isinstance(current, int) and not isinstance(current, bool):
                        v = int(v)
                    elif isinstance(current, float):
                        v = float(v)
                    setattr(self.settings, k, v)
                    
                    # Логируем изменения (кроме ключей)
                    if old_value != v and 'key' not in k.lower() and 'token' not in k.lower():
                        try:
                            db.log_setting_change(k, old_value, v, source)
                        except Exception as e:
                            logger.debug(f"[TRADER] Setting change log error: {e}")
            
            self._save_state()
            return True
    
    # =========================================================================
    # УПРАВЛЕНИЕ
    # =========================================================================
    def pause_scanner(self, paused: bool):
        """Пауза сканера"""
        with self.lock:
            self.scanner_paused = paused
            status = "⏸️ Сканер на паузе" if paused else "▶️ Сканер запущен"
            self._add_log("scanner", status)
    
    def set_trade_mode(self, mode: str) -> bool:
        """Переключение режима торговли"""
        if mode not in ["PAPER", "LIVE"]:
            return False
        
        with self.lock:
            old_mode = self.settings.trade_mode
            self.settings.trade_mode = mode
            if mode == "LIVE":
                self._add_log("system", f"⚠️ РЕЖИМ ИЗМЕНЁН: {old_mode} → LIVE (реальная торговля!)")
            else:
                self._add_log("system", f"🔄 РЕЖИМ ИЗМЕНЁН: {old_mode} → PAPER (виртуальная торговля)")
            self._save_state()
            return True
    
    def reset(self, keep_settings: bool = True):
        """Сброс трейдера"""
        with self.lock:
            # Сохраняем важные настройки
            saved_keys = {
                'deepseek_api_key': self.settings.deepseek_api_key,
                'groq_api_key': self.settings.groq_api_key,
                'binance_api_key': self.settings.binance_api_key,
                'binance_secret_key': self.settings.binance_secret_key,
                'telegram_bot_token': self.settings.telegram_bot_token,
                'telegram_chat_id': self.settings.telegram_chat_id
            }
            
            if not keep_settings:
                self.settings = Settings()
            
            # Восстанавливаем ключи
            for k, v in saved_keys.items():
                setattr(self.settings, k, v)
            
            self.balance = self.settings.initial_balance
            self.positions.clear()
            self.closed_positions.clear()
            self.log_history.clear()
            # Восстанавливаем счётчик из БД чтобы не было коллизий trade_id
            try:
                self.trade_counter = db.get_last_trade_counter()
                logger.info(f"[RESET] Trade counter restored from DB: {self.trade_counter}")
            except Exception:
                self.trade_counter = 0
            self.total_pnl = 0.0
            self.winning_trades = 0
            self.losing_trades = 0
            self.daily_pnl = 0.0
            self.weekly_pnl = 0.0
            self.scanner_paused = False
            self.daily_loss_stop = False
            self.weekly_loss_stop = False
            self.last_loss_time = None
            self.symbol_cooldowns.clear()
            self.symbol_daily_losses.clear()
            
            self._add_log("system", "🔄 Сброс выполнен")
            self._save_state()
            
            # Сохраняем дату сброса — график PnL будет показывать только сделки после этой даты
            try:
                db.set_setting('pnl_reset_at', get_gmt2_time().strftime('%Y-%m-%d %H:%M:%S'))
                logger.info(f"[RESET] PnL reset timestamp saved")
            except Exception as e:
                logger.error(f"[RESET] Failed to save reset timestamp: {e}")
    
    # =========================================================================
    # СОСТОЯНИЕ
    # =========================================================================
    def _save_state(self):
        """Сохранение состояния"""
        try:
            state = {
                "version": 9,  # Версия 9: + daily/weekly persistence
                "balance": self.balance,
                "trade_counter": self.trade_counter,
                "total_pnl": self.total_pnl,
                "winning": self.winning_trades,
                "losing": self.losing_trades,
                "scanner_paused": self.scanner_paused,
                "equity_peak": self.equity_peak,
                # v9: Сохраняем дневные/недельные данные
                "daily_pnl": self.daily_pnl,
                "daily_date": self.daily_date,
                "weekly_pnl": self.weekly_pnl,
                "weekly_start": self.weekly_start,
                "daily_loss_stop": self.daily_loss_stop,
                "weekly_loss_stop": self.weekly_loss_stop,
                "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "closed": [p.to_dict() for p in self.closed_positions[-500:]],  # Увеличено до 500
            }
            db.set_setting('trader_state', state)
            db.set_setting('trader_settings', self.settings.to_dict())
        except Exception as e:
            logger.error(f"[TRADER] Save error: {e}")
    
    def _load_state(self):
        """Загрузка состояния"""
        try:
            # Загружаем настройки
            saved_settings = db.get_setting('trader_settings', {})
            if saved_settings:
                for k, v in saved_settings.items():
                    if hasattr(self.settings, k):
                        setattr(self.settings, k, v)
            
            # Загружаем состояние
            state = db.get_setting('trader_state', {})
            if state:
                self.balance = state.get("balance", self.settings.initial_balance)
                state_counter = state.get("trade_counter", 0)
                db_counter = db.get_last_trade_counter()
                self.trade_counter = max(state_counter, db_counter)
                if self.trade_counter != state_counter:
                    logger.info(f"[TRADER] Trade counter: state={state_counter}, DB={db_counter} → using {self.trade_counter}")
                self.total_pnl = state.get("total_pnl", 0)
                self.winning_trades = state.get("winning", 0)
                self.losing_trades = state.get("losing", 0)
                self.scanner_paused = state.get("scanner_paused", False)
                self.equity_peak = state.get("equity_peak", 0.0)
                # v9: Восстанавливаем дневные/недельные данные
                self.daily_pnl = state.get("daily_pnl", 0.0)
                self.daily_date = state.get("daily_date", "")
                self.weekly_pnl = state.get("weekly_pnl", 0.0)
                self.weekly_start = state.get("weekly_start", "")
                self.daily_loss_stop = state.get("daily_loss_stop", False)
                self.weekly_loss_stop = state.get("weekly_loss_stop", False)
                
                # Загружаем позиции
                for k, v in state.get("positions", {}).items():
                    try:
                        # Добавляем новые поля если их нет
                        defaults = {
                            'trailing_stop': v.get('stop_loss', 0),
                            'initial_stop_loss': v.get('stop_loss', 0),
                            'trail_activated': False,
                            'trade_mode': 'PAPER',
                            'adaptive_trailing_enabled': True,
                            'trailing_activation_pct': 2.0,
                            'trailing_distance_pct': 1.0,
                            'ai_provider': 'deepseek',
                            'atr_percent': 0,
                            'side': 'SHORT',  # Новое поле для направления
                            'btc_trend_at_open': {},
                            # Защита прибыли
                            'partial_tp_done': False,
                            'breakeven_activated': False,
                            'original_size_usdt': v.get('size_usdt', 0),
                            'partial_tp_pnl': 0.0,
                            'max_pnl_percent': 0.0,  # Максимальная прибыль для защиты
                            # Индикаторы
                            'rsi_at_entry': 50.0,
                            'bollinger_b_at_entry': 50.0,
                            'macd_histogram_at_entry': 0.0,
                            'macd_divergence_at_entry': 'none'
                        }
                        for dk, dv in defaults.items():
                            if dk not in v:
                                v[dk] = dv
                        
                        # Конвертируем btc_trend_at_open из строки в dict если нужно
                        if isinstance(v.get('btc_trend_at_open'), str):
                            try:
                                v['btc_trend_at_open'] = json.loads(v['btc_trend_at_open'])
                            except Exception:
                                v['btc_trend_at_open'] = {}
                        
                        self.positions[k] = Position(**v)
                    except Exception as e:
                        logger.error(f"[TRADER] Error loading position {k}: {e}")
                
                # Загружаем закрытые позиции
                for p in state.get("closed", []):
                    try:
                        # Конвертируем btc_trend_at_open если нужно
                        if isinstance(p.get('btc_trend_at_open'), str):
                            try:
                                p['btc_trend_at_open'] = json.loads(p['btc_trend_at_open'])
                            except Exception:
                                p['btc_trend_at_open'] = {}
                        
                        # Добавляем поле side если его нет
                        if 'side' not in p:
                            p['side'] = 'SHORT'
                        
                        self.closed_positions.append(Position(**p))
                    except Exception as e:
                        logger.error(f"[TRADER] Error loading closed position: {e}")
            
            logger.info(f"[TRADER] Loaded from DB: ${self.balance:.2f}")
        except Exception as e:
            logger.error(f"[TRADER] Load error: {e}")