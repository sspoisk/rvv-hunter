"""
â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—
â•‘                        CRYPTO AGENT V3 - FULL AUTONOMY                       â•‘
â•‘                                                                              â•‘
║  Полностью автономный AI-агент для управления криптоторговлей               ║
║  - Управляет SL/TP/Trailing Stop                                            ║
║  - Принимает решения об открытии/закрытии                                   ║
║  - Учится на своих ошибках (персистентная память)                           ║
║  - Fallback между провайдерами (DeepSeek → GROQ)                            ║
â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
"""

import json
import logging
import re
import threading
import time
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Callable
from enum import Enum

logger = logging.getLogger(__name__)

# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class AgentMode(Enum):
    """Режим работы агента"""
    OBSERVE = "observe"      # Только наблюдение
    RECOMMEND = "recommend"  # Рекомендации без действий
    AUTO = "auto"           # Полная автономность


class AgentAction(Enum):
    """Возможные действия агента"""
    CLOSE = "CLOSE"
    PARTIAL_CLOSE_25 = "PARTIAL_CLOSE_25"
    PARTIAL_CLOSE_50 = "PARTIAL_CLOSE_50"
    PARTIAL_CLOSE_75 = "PARTIAL_CLOSE_75"
    CLOSE_ALL = "CLOSE_ALL"
    SET_SL = "SET_SL"
    SET_BREAKEVEN = "SET_BREAKEVEN"
    TIGHTEN_SL = "TIGHTEN_SL"
    SET_TP = "SET_TP"
    ENABLE_TRAILING = "ENABLE_TRAILING"
    DISABLE_TRAILING = "DISABLE_TRAILING"
    APPROVE_SIGNAL = "APPROVE_SIGNAL"
    REJECT_SIGNAL = "REJECT_SIGNAL"
    PAUSE_SCANNER = "PAUSE_SCANNER"
    RESUME_SCANNER = "RESUME_SCANNER"
    BLACKLIST_COIN = "BLACKLIST_COIN"
    HOLD = "HOLD"


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class PositionData:
    """Данные позиции для агента"""
    trade_id: str
    symbol: str
    side: str
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    trailing_stop: float
    pnl_usdt: float
    pnl_percent: float
    size_usdt: float
    opened_at: datetime
    trail_activated: bool = False
    max_pnl_percent: float = 0.0
    last_check: datetime = None
    check_count: int = 0
    
    @property
    def age_minutes(self) -> float:
        return (datetime.now() - self.opened_at).total_seconds() / 60
    
    @property
    def age_hours(self) -> float:
        return self.age_minutes / 60
    
    @property
    def drawdown_from_max(self) -> float:
        if self.max_pnl_percent <= 0:
            return 0
        return self.max_pnl_percent - self.pnl_percent
    
    @property
    def cooldown_ok(self) -> bool:
        """Прошло ли 5 минут с последней проверки"""
        if not self.last_check:
            return True
        return (datetime.now() - self.last_check).total_seconds() >= 300
    
    def mark_checked(self):
        self.last_check = datetime.now()
        self.check_count += 1
    
    def to_prompt_str(self) -> str:
        return f"""- [{self.trade_id}] {self.symbol} {self.side}
  Entry: ${self.entry_price:.6f} | Current: ${self.current_price:.6f}
  PnL: ${self.pnl_usdt:.2f} ({self.pnl_percent:+.2f}%)
  SL: ${self.stop_loss:.6f} | TP1: ${self.take_profit_1:.6f}
  Age: {self.age_minutes:.0f} min | Max PnL: {self.max_pnl_percent:.2f}%
  Trailing: {'ON' if self.trail_activated else 'OFF'} | Size: ${self.size_usdt:.0f}"""


@dataclass
class MarketContext:
    """Контекст рынка"""
    btc_price: float = 0.0
    btc_trend: str = "neutral"
    btc_change_1h: float = 0.0
    btc_change_24h: float = 0.0
    btc_rsi: float = 50.0
    market_mode: str = "NORMAL"
    volatility: str = "normal"
    
    def to_prompt_str(self) -> str:
        return f"""BTC: ${self.btc_price:,.0f} ({self.btc_trend})
RSI: {self.btc_rsi:.1f} | 1h: {self.btc_change_1h:+.2f}% | 24h: {self.btc_change_24h:+.2f}%
Market Mode: {self.market_mode}"""


@dataclass
class AgentSettings:
    """Настройки агента"""
    enabled: bool = False
    mode: AgentMode = AgentMode.AUTO
    aggressiveness: int = 2
    
    primary_provider: str = "deepseek"
    fallback_provider: str = "groq"
    
    main_loop_interval: int = 30
    position_check_interval: int = 120
    market_analysis_interval: int = 1800
    learning_interval: int = 3600
    
    min_position_age_minutes: int = 10
    profit_to_protect_percent: float = 3.0
    drawdown_trigger_percent: float = 2.0
    stagnation_minutes: int = 60
    position_cooldown_minutes: int = 5
    
    max_ai_calls_per_minute: int = 10
    max_actions_per_minute: int = 5
    
    auto_adjust_sl: bool = True
    auto_adjust_tp: bool = True
    auto_trailing: bool = True
    auto_partial_close: bool = True
    validate_signals: bool = True
    learn_from_mistakes: bool = True


# ============================================================================
# AI PROVIDER WITH FALLBACK
# ============================================================================

class AIProvider:
    """AI провайдер с fallback между DeepSeek и GROQ"""
    
    DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
    GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    
    def __init__(self, deepseek_key: str, groq_key: str = None):
        self.deepseek_key = deepseek_key
        self.groq_key = groq_key
        self.providers = []
        
        if deepseek_key:
            self.providers.append({
                'name': 'deepseek',
                'url': self.DEEPSEEK_URL,
                'key': deepseek_key,
                'model': 'deepseek-reasoner',
                'failures': 0,
                'last_failure': None
            })
        
        if groq_key:
            self.providers.append({
                'name': 'groq',
                'url': self.GROQ_URL,
                'key': groq_key,
                'model': 'llama-3.1-70b-versatile',
                'failures': 0,
                'last_failure': None
            })
        
        self.call_count = 0
    
    def call(self, prompt: str, system_prompt: str = None, max_tokens: int = 2000) -> Tuple[Optional[str], str, int]:
        """Вызов AI с автоматическим fallback. Returns: (response, provider_name, exec_time_ms)"""
        if not self.providers:
            logger.error("[AIProvider] No providers configured!")
            return None, "none", 0
        
        start_time = time.time()
        
        for provider in self.providers:
            if provider['last_failure']:
                cooldown = 60 if provider['failures'] < 3 else 300
                if (datetime.now() - provider['last_failure']).seconds < cooldown:
                    continue
            
            try:
                response = self._call_provider(provider, prompt, system_prompt, max_tokens)
                if response:
                    provider['failures'] = 0
                    provider['last_failure'] = None
                    self.call_count += 1
                    exec_time = int((time.time() - start_time) * 1000)
                    return response, provider['name'], exec_time
            except Exception as e:
                logger.warning(f"[AIProvider] {provider['name']} failed: {e}")
                provider['failures'] += 1
                provider['last_failure'] = datetime.now()
                continue
        
        logger.error("[AIProvider] All providers failed!")
        return None, "none", 0
    
    def _call_provider(self, provider: Dict, prompt: str, system_prompt: str, max_tokens: int) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {provider['key']}",
            "Content-Type": "application/json"
        }
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": provider['model'],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7
        }
        
        response = requests.post(provider['url'], headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            data = response.json()
            return data.get('choices', [{}])[0].get('message', {}).get('content', '')
        else:
            raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")
    
    def get_status(self) -> Dict:
        return {
            'providers': [
                {'name': p['name'], 'failures': p['failures'], 
                 'available': p['last_failure'] is None or (datetime.now() - p['last_failure']).seconds > 60}
                for p in self.providers
            ],
            'total_calls': self.call_count
        }


# ============================================================================
# AGENT TOOLS
# ============================================================================

class AgentTools:
    """Инструменты агента для получения рыночных данных и истории"""
    
    def __init__(self, agent: 'CryptoAgentV3'):
        self.agent = agent
    
    # ========== ТЕКУЩИЕ ИНДИКАТОРЫ ==========
    
    def get_rsi(self, symbol: str, timeframe: str = '15m') -> Dict:
        try:
            from app import get_rsi_for_symbol
            rsi = get_rsi_for_symbol(symbol, timeframe)
            return {'symbol': symbol, 'rsi': round(rsi, 2) if rsi else None}
        except Exception as e:
            return {'error': str(e)}
    
    def get_macd(self, symbol: str) -> Dict:
        try:
            from app import get_macd_for_symbol
            return get_macd_for_symbol(symbol) or {'error': 'no data'}
        except Exception as e:
            return {'error': str(e)}
    
    def get_volume(self, symbol: str) -> Dict:
        try:
            from app import get_volume_analysis
            return get_volume_analysis(symbol) or {'error': 'no data'}
        except Exception as e:
            return {'error': str(e)}
    
    # ========== ПАМЯТЬ АГЕНТА ==========
    
    def get_coin_memory(self, symbol: str = None) -> Dict:
        from database import db
        if symbol:
            return db.get_coin_memory(symbol) or {'symbol': symbol, 'trades': 0}
        return {'coins': db.get_all_coin_memory()[:10]}
    
    def get_lessons(self, symbol: str = None) -> List[Dict]:
        from database import db
        return db.get_agent_lessons(symbol=symbol, min_confidence=0.3)
    
    # ========== ИСТОРИЯ СДЕЛОК ==========
    
    def get_trade_history(self, symbol: str, limit: int = 10) -> List[Dict]:
        """История сделок по монете"""
        from database import db
        return db.get_symbol_trade_history(symbol, limit)
    
    def get_coin_statistics(self, symbol: str) -> Dict:
        """Полная статистика по монете"""
        from database import db
        return db.get_symbol_statistics(symbol)
    
    def get_trade_events(self, trade_id: str) -> List[Dict]:
        """Как развивалась конкретная сделка"""
        from database import db
        return db.get_trade_events(trade_id)
    
    def get_similar_trades(self, symbol: str = None, side: str = None,
                           rsi_min: float = None, rsi_max: float = None,
                           confidence_min: int = None) -> List[Dict]:
        """Найти похожие сделки"""
        from database import db
        return db.get_similar_trades(symbol, side, rsi_min, rsi_max, confidence_min)
    
    # ========== АНАЛИТИКА ==========
    
    def get_hourly_stats(self) -> List[Dict]:
        """Какие часы лучше для торговли"""
        from database import db
        return db.get_hourly_statistics()
    
    def get_confidence_stats(self) -> List[Dict]:
        """Какой confidence лучше работает"""
        from database import db
        return db.get_confidence_statistics()
    
    def get_side_stats(self) -> Dict:
        """SHORT vs LONG статистика"""
        from database import db
        return db.get_side_statistics()
    
    def get_best_worst_coins(self) -> Dict:
        """Лучшие и худшие монеты"""
        from database import db
        return db.get_best_worst_coins()
    
    def get_pattern_analysis(self) -> Dict:
        """Полный анализ паттернов"""
        from database import db
        return db.get_pattern_analysis()
    
    def get_recent_analyses(self, symbol: str = None, limit: int = 20) -> List[Dict]:
        """Последние AI анализы"""
        from database import db
        return db.get_recent_ai_analyses(symbol, limit)
    
    # ========== СВОДКА ДЛЯ AI ==========
    
    def get_full_context(self, symbol: str) -> str:
        """Полный контекст о монете для AI"""
        from database import db
        
        # Статистика монеты
        stats = db.get_symbol_statistics(symbol)
        
        # Последние сделки
        history = db.get_symbol_trade_history(symbol, limit=5)
        
        # Память агента
        memory = db.get_coin_memory(symbol)
        
        # Уроки
        lessons = db.get_agent_lessons(symbol=symbol, min_confidence=0.4)
        
        context = f"""ИСТОРИЯ {symbol}:
Всего сделок: {stats.get('total_trades', 0)} | Win Rate: {stats.get('win_rate', 0):.0f}%
Общий PnL: ${stats.get('total_pnl', 0):.2f} | Avg: ${stats.get('avg_pnl', 0):.2f}
Лучшая: ${stats.get('best_trade', 0):.2f} | Худшая: ${stats.get('worst_trade', 0):.2f}
Avg Duration: {stats.get('avg_duration', 0):.0f} min"""

        if history:
            context += "\n\nПОСЛЕДНИЕ СДЕЛКИ:"
            for t in history[:3]:
                context += f"\n  {t.get('closed_at', '')[:10]} {t.get('side')}: {t.get('result')} ${t.get('pnl_usdt', 0):.2f} ({t.get('close_reason', '')})"
        
        if lessons:
            context += "\n\nУРОКИ:"
            for l in lessons[:3]:
                context += f"\n  - {l.get('lesson', '')} (conf: {l.get('confidence', 0):.1f})"
        
        return context
    
    def get_trading_insights(self) -> str:
        """Инсайты для принятия решений"""
        from database import db
        
        patterns = db.get_pattern_analysis()
        
        insights = []
        
        # Лучшие часы
        best_hours = [h for h in patterns.get('best_hours', []) 
                      if h.get('total', 0) >= 3 and h.get('win_rate', 0) >= 50]
        if best_hours:
            hours_str = ', '.join([f"{h['hour']}:00 ({h['win_rate']}%)" for h in best_hours[:3]])
            insights.append(f"Лучшие часы: {hours_str}")
        
        # Оптимальный confidence
        conf_stats = patterns.get('confidence_stats', [])
        best_conf = max(conf_stats, key=lambda x: x.get('win_rate', 0)) if conf_stats else None
        if best_conf:
            insights.append(f"Лучший confidence: {best_conf['confidence_range']} ({best_conf['win_rate']}% WR)")
        
        # SHORT vs LONG
        side_stats = patterns.get('side_stats', {})
        for side, data in side_stats.items():
            insights.append(f"{side}: {data.get('win_rate', 0)}% WR, PnL: ${data.get('total_pnl', 0):.0f}")
        
        # Худшие монеты (избегать)
        worst = patterns.get('coins', {}).get('worst', [])[:3]
        if worst:
            avoid = ', '.join([f"{c['symbol'].split('/')[0]} (${c['total_pnl']:.0f})" for c in worst])
            insights.append(f"Избегать: {avoid}")
        
        return '\n'.join(insights) if insights else "Недостаточно данных для анализа"


# ============================================================================
# MAIN AGENT CLASS
# ============================================================================

class CryptoAgentV3:
    """Crypto Agent V3 - Полностью автономный AI-агент"""
    
    SYSTEM_PROMPT = """Ты опытный криптотрейдер-алгоритмист. Это ТВОИ деньги. Торгуешь криптофьючерсами.

КРИТИЧЕСКИ ВАЖНО:
- Работай ТОЛЬКО с данными которые тебе даны
- НИКОГДА не выдумывай позиции, цены или сделки
- Если данных нет — так и скажи, не фантазируй
- Используй ТОЧНЫЕ trade_id из данных (формат RVV-XXXX)

КОМАНДЫ (используй ТОЧНЫЙ trade_id):
- ACTION: CLOSE(RVV-XXXX) — закрыть позицию полностью
- ACTION: PARTIAL_CLOSE(RVV-XXXX, 50) — закрыть 25/50/75%
- ACTION: SET_SL(RVV-XXXX, 0.1234) — новый стоп-лосс
- ACTION: SET_BREAKEVEN(RVV-XXXX) — SL на уровень входа
- ACTION: TIGHTEN_SL(RVV-XXXX, 2) — подтянуть SL на X% ближе
- ACTION: SET_TP(RVV-XXXX, 0.1234) — новый тейк-профит
- ACTION: ENABLE_TRAILING(RVV-XXXX) — включить трейлинг
- ACTION: DISABLE_TRAILING(RVV-XXXX) — отключить трейлинг
- ACTION: BLACKLIST(SYMBOL, 24, причина) — в ЧС на N часов
- ACTION: PAUSE_SCANNER — остановить открытие новых позиций
- ACTION: RESUME_SCANNER — возобновить сканер
- ACTION: CLOSE_ALL(причина) — закрыть ВСЕ (только обвал рынка!)
- ACTION: HOLD — ничего не делать

СТРАТЕГИЯ:
1. Прибыль > 3% → SET_BREAKEVEN (защита от разворота)
2. Прибыль > 5% → PARTIAL_CLOSE 50% (фиксация части)
3. Прибыль > 2% и трейлинг OFF → ENABLE_TRAILING
4. Убыток > 5% и RSI разворачивается → CLOSE (режь убытки)
5. Монета с плохой историей (WR < 30%) → не торгуй, BLACKLIST
6. BTC падает сильно → PAUSE_SCANNER или CLOSE_ALL

ФОРМАТ ОТВЕТА:
1. Краткий анализ ситуации (2-3 предложения)
2. Решение с обоснованием
3. ACTION: КОМАНДА(точные параметры)

Пример:
"VVV +3.5%, RSI снижается с 85. Фиксирую часть прибыли и защищаю остаток.
ACTION: SET_BREAKEVEN(RVV-0010)
ACTION: PARTIAL_CLOSE(RVV-0010, 50)"
"""

    def __init__(self, 
                 deepseek_key: str,
                 groq_key: str = None,
                 trader_callbacks: Dict[str, Callable] = None,
                 telegram_callback: Callable = None):
        
        self.settings = AgentSettings()
        self.ai_provider = AIProvider(deepseek_key, groq_key)
        self.tools = AgentTools(self)
        
        self.trader_callbacks = trader_callbacks or {}
        self.telegram_callback = telegram_callback
        
        self.positions: Dict[str, PositionData] = {}
        self.market: MarketContext = MarketContext()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
        self.positions_lock = threading.Lock()
        self.action_lock = threading.Lock()
        
        self.stats = {
            'ai_calls': 0, 'actions_taken': 0, 'positions_closed': 0,
            'sl_adjustments': 0, 'lessons_learned': 0, 'errors': 0
        }
        
        self._last_market_analysis = 0
        self._last_learning_cycle = 0
        self._action_timestamps: List[datetime] = []
        
        self.recent_logs: List[str] = []
        self.recent_decisions: List[Dict] = []
        
        logger.info("[AgentV3] Initialized with full autonomy")
    
    # ========================================================================
    # LIFECYCLE
    # ========================================================================
    
    def start(self):
        if self.running:
            return
        self.running = True
        self.settings.enabled = True
        self.thread = threading.Thread(target=self._main_loop, daemon=True, name="AgentV3")
        self.thread.start()
        self._log("✅ Agent V3 запущен (FULL AUTONOMY)")
        self._restore_state()
    
    def stop(self):
        self.running = False
        self.settings.enabled = False
        self._save_state()
        self._log("⏹️ Agent V3 остановлен")
    
    def _main_loop(self):
        while self.running:
            try:
                now = time.time()
                
                if now - self._last_market_analysis > self.settings.market_analysis_interval:
                    self._analyze_market()
                    self._last_market_analysis = now
                
                if self.positions:
                    self._check_all_positions()
                
                if self.settings.learn_from_mistakes:
                    if now - self._last_learning_cycle > self.settings.learning_interval:
                        self._learning_cycle()
                        self._last_learning_cycle = now
                
                time.sleep(self.settings.main_loop_interval)
                
            except Exception as e:
                logger.error(f"[AgentV3] Main loop error: {e}")
                self.stats['errors'] += 1
                time.sleep(60)
    
    # ========================================================================
    # MARKET ANALYSIS
    # ========================================================================
    
    def _analyze_market(self):
        if not self.market.btc_price:
            return
        
        # Формируем список реальных позиций
        positions_text = self._format_all_positions()
        
        prompt = f"""Краткий анализ рынка и позиций.

РЫНОК:
{self.market.to_prompt_str()}

ОТКРЫТЫЕ ПОЗИЦИИ ({len(self.positions)}):
{positions_text if positions_text else "НЕТ ОТКРЫТЫХ ПОЗИЦИЙ"}

Оценка рынка: NORMAL / CAUTION / PAUSE
Если нужно — ACTION: PAUSE_SCANNER или RESUME_SCANNER

⚠️ Работай ТОЛЬКО с позициями из списка выше. Не выдумывай!"""

        response, provider, exec_time = self.ai_provider.call(prompt, self.SYSTEM_PROMPT, max_tokens=500)
        
        if response:
            self.stats['ai_calls'] += 1
            
            if 'PAUSE' in response.upper() and 'SCANNER' not in response.upper():
                self.market.market_mode = 'PAUSE'
            elif 'CAUTION' in response.upper():
                self.market.market_mode = 'CAUTION'
            else:
                self.market.market_mode = 'NORMAL'
            
            self._parse_and_execute(response, "market")
            self._log(f"📊 Рынок: {self.market.market_mode} [{provider}]")
    
    # ========================================================================
    # POSITION MANAGEMENT
    # ========================================================================
    
    def _check_all_positions(self):
        with self.positions_lock:
            positions = list(self.positions.values())
        
        if not positions:
            return
        
        to_check = [p for p in positions 
                    if p.age_minutes >= self.settings.min_position_age_minutes and p.cooldown_ok]
        
        if not to_check:
            return
        
        to_check.sort(key=lambda p: abs(p.pnl_percent), reverse=True)
        
        for pos in to_check[:3]:
            self._check_position(pos)
            pos.mark_checked()
    
    def _check_position(self, pos: PositionData):
        from database import db
        
        # Получаем полный контекст о монете
        full_context = self.tools.get_full_context(pos.symbol)
        
        # Получаем текущий RSI
        rsi_data = self.tools.get_rsi(pos.symbol)
        current_rsi = rsi_data.get('rsi', 'N/A')
        
        prompt = f"""Проанализируй позицию и прими решение.

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
ТЕКУЩАЯ ПОЗИЦИЯ:
{pos.to_prompt_str()}
RSI сейчас: {current_rsi}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
РЫНОК:
{self.market.to_prompt_str()}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
{full_context}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
СТИЛЬ: {'КОНСЕРВАТИВНЫЙ - закрывай раньше, защищай прибыль' if self.settings.aggressiveness == 1 else 'УМЕРЕННЫЙ - баланс риск/прибыль' if self.settings.aggressiveness == 2 else 'АГРЕССИВНЫЙ - держи дольше, больше риска'}

Что делать с позицией {pos.trade_id}?
Дай чёткую команду ACTION: или ACTION: HOLD если ничего не нужно."""

        response, provider, exec_time = self.ai_provider.call(prompt, self.SYSTEM_PROMPT, max_tokens=800)
        
        if response:
            self.stats['ai_calls'] += 1
            
            decision = {
                'trade_id': pos.trade_id,
                'symbol': pos.symbol,
                'side': pos.side,
                'pnl_before': pos.pnl_percent,
                'btc_price': self.market.btc_price,
                'btc_trend': self.market.btc_trend,
                'ai_provider': provider,
                'execution_time_ms': exec_time,
                'reasoning': response[:500]
            }
            
            actions = self._parse_and_execute(response, f"pos_{pos.trade_id}")
            decision['action'] = actions[0] if actions else 'HOLD'
            
            db.save_agent_decision(decision)
            self.recent_decisions.append(decision)
            if len(self.recent_decisions) > 50:
                self.recent_decisions = self.recent_decisions[-50:]
            
            # Логируем только не-HOLD или каждую 10-ю проверку
            if decision['action'] != 'HOLD' or pos.check_count % 10 == 1:
                action_text = decision['action'] if decision['action'] != 'HOLD' else 'продолжаем держать'
                self._log(f"{'â¸ï¸' if decision['action'] == 'HOLD' else 'ðŸŽ¯'} {pos.symbol}: {action_text}")
    
    # ========================================================================
    # SIGNAL VALIDATION
    # ========================================================================
    
    def validate_signal(self, signal: Dict) -> Dict:
        """Валидация сигнала с учётом истории торговли"""
        if not self.settings.validate_signals:
            return {'approved': True, 'signal': signal}
        
        if self.settings.mode == AgentMode.OBSERVE:
            return {'approved': True, 'signal': signal}
        
        symbol = signal.get('symbol', '')
        side = signal.get('side', signal.get('direction', 'SHORT'))
        
        from database import db
        
        # 1. Проверка blacklist агента
        is_blacklisted, reason = db.is_coin_blacklisted_by_agent(symbol)
        if is_blacklisted:
            return {'approved': False, 'reason': f"Agent blacklist: {reason}"}
        
        # 2. Проверка памяти агента
        coin_mem = db.get_coin_memory(symbol)
        if coin_mem:
            # 3+ убытка подряд
            if coin_mem.get('current_streak', 0) <= -3 and coin_mem.get('last_result') == 'LOSS':
                return {'approved': False, 'reason': "3+ losses in a row"}
            
            # Win rate < 20% при 5+ сделках
            total = coin_mem.get('total_trades', 0)
            wins = coin_mem.get('wins', 0)
            if total >= 5 and (wins / max(total, 1)) < 0.2:
                # Автоматически добавляем в blacklist
                db.set_coin_blacklist(symbol, True, f"Auto-blacklist: WR {wins}/{total}", hours=48)
                return {'approved': False, 'reason': f"Win rate too low: {wins}/{total} (auto-blacklisted)"}
        
        # 3. Проверка истории из trades
        stats = db.get_symbol_statistics(symbol)
        if stats.get('total_trades', 0) >= 3:
            wr = stats.get('win_rate', 0)
            avg_pnl = stats.get('avg_pnl', 0)
            
            # Монета стабильно убыточна
            if wr < 25 and avg_pnl < -10:
                return {'approved': False, 'reason': f"Bad history: {wr:.0f}% WR, avg ${avg_pnl:.0f}"}
        
        # 4. Проверка режима рынка
        if self.market.market_mode == 'PAUSE':
            return {'approved': False, 'reason': "Market PAUSE mode"}
        
        # 5. Проверка BTC тренда
        if self.market.btc_trend == 'bearish' and side == 'LONG':
            # На медвежьем рынке лонги рискованнее
            if self.settings.aggressiveness < 3:
                return {'approved': False, 'reason': "LONG blocked on bearish BTC"}
        
        if self.market.btc_trend == 'bullish' and side == 'SHORT':
            # На бычьем рынке шорты рискованнее
            if self.settings.aggressiveness < 2:
                return {'approved': False, 'reason': "SHORT blocked on bullish BTC"}
        
        return {'approved': True, 'signal': signal}
    
    # ========================================================================
    # COMMAND PARSING & EXECUTION
    # ========================================================================
    
    def _parse_and_execute(self, response: str, source: str) -> List[str]:
        actions_taken = []
        
        patterns = [
            (r'ACTION:\s*CLOSE\(([^)]+)\)', self._exec_close),
            (r'ACTION:\s*PARTIAL_CLOSE\(([^,]+),\s*(\d+)\)', self._exec_partial_close),
            (r'ACTION:\s*SET_SL\(([^,]+),\s*([0-9.]+)\)', self._exec_set_sl),
            (r'ACTION:\s*SET_BREAKEVEN\(([^)]+)\)', self._exec_set_breakeven),
            (r'ACTION:\s*TIGHTEN_SL\(([^,]+),\s*([0-9.]+)\)', self._exec_tighten_sl),
            (r'ACTION:\s*SET_TP\(([^,]+),\s*([0-9.]+)\)', self._exec_set_tp),
            (r'ACTION:\s*ENABLE_TRAILING\(([^)]+)\)', self._exec_enable_trailing),
            (r'ACTION:\s*DISABLE_TRAILING\(([^)]+)\)', self._exec_disable_trailing),
            (r'ACTION:\s*BLACKLIST\(([^,]+),\s*(\d+),\s*([^)]+)\)', self._exec_blacklist),
            (r'ACTION:\s*PAUSE_SCANNER', self._exec_pause_scanner),
            (r'ACTION:\s*RESUME_SCANNER', self._exec_resume_scanner),
            (r'ACTION:\s*CLOSE_ALL\(([^)]+)\)', self._exec_close_all),
        ]
        
        for pattern, handler in patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                if self._can_take_action():
                    try:
                        result = handler(*match) if isinstance(match, tuple) else handler(match) if match else handler()
                        if result:
                            actions_taken.append(result)
                            self._record_action()
                    except Exception as e:
                        logger.error(f"[AgentV3] Action error: {e}")
                        self.stats['errors'] += 1
        
        return actions_taken
    
    # ========================================================================
    # EXECUTION METHODS
    # ========================================================================
    
    def _exec_close(self, trade_id: str) -> Optional[str]:
        trade_id = trade_id.strip()
        
        if self.settings.mode == AgentMode.OBSERVE:
            self._log(f"👁️ [OBSERVE] Закрыл бы {trade_id}")
            return None
        
        if self.settings.mode == AgentMode.RECOMMEND:
            self._log(f"💡 [RECOMMEND] Закрыть {trade_id}")
            self._notify(f"💡 Рекомендация: закрыть {trade_id}")
            return "RECOMMEND_CLOSE"
        
        callback = self.trader_callbacks.get('close_position')
        if callback:
            result = callback(trade_id, "ðŸ¤– Agent V3")
            if result:
                self.stats['positions_closed'] += 1
                self._log(f"✅ Закрыто: {trade_id}")
                with self.positions_lock:
                    if trade_id in self.positions:
                        del self.positions[trade_id]
                return "CLOSE"
            else:
                self._log(f"❌ Не удалось закрыть: {trade_id}")
        return None
    
    def _exec_partial_close(self, trade_id: str, percent: str) -> Optional[str]:
        trade_id = trade_id.strip()
        pct = int(percent)
        
        # ИСПРАВЛЕНО v5.5: partial_close работает и в NORMAL режиме!
        if self.settings.mode == AgentMode.MONITOR:
            self._log(f"💡 [MONITOR] Частичное закрытие {trade_id} {pct}% (только мониторинг)")
            return None
        
        callback = self.trader_callbacks.get('partial_close')
        if callback:
            result = callback(trade_id, pct)
            if result:
                self._log(f"✅ Частичное закрытие {pct}%: {trade_id}")
                return f"PARTIAL_CLOSE_{pct}"
            else:
                # НОВОЕ: логируем когда partial_close не сработал
                self._log(f"❌ Partial close FAILED: {trade_id} - позиция не найдена или уже закрыта")
                logger.warning(f"[AgentV3] Partial close failed for {trade_id}. Agent positions: {list(self.positions.keys())}")
        else:
            self._log(f"❌ Partial close callback не определён!")
        return None
    
    def _exec_set_sl(self, trade_id: str, price: str) -> Optional[str]:
        trade_id = trade_id.strip()
        new_sl = float(price)
        
        if self.settings.mode != AgentMode.AUTO:
            self._log(f"ðŸ’¡ SL {trade_id} â†’ ${new_sl}")
            return None
        
        callback = self.trader_callbacks.get('adjust_sl')
        if callback:
            result = callback(trade_id, new_sl)
            if result:
                self.stats['sl_adjustments'] += 1
                self._log(f"âœ… SL: {trade_id} â†’ ${new_sl}")
                return "SET_SL"
        return None
    
    def _exec_set_breakeven(self, trade_id: str) -> Optional[str]:
        trade_id = trade_id.strip()
        
        if self.settings.mode != AgentMode.AUTO:
            self._log(f"ðŸ’¡ Breakeven {trade_id}")
            return None
        
        callback = self.trader_callbacks.get('set_breakeven')
        if callback:
            result = callback(trade_id)
            if result:
                self.stats['sl_adjustments'] += 1
                self._log(f"âœ… Breakeven: {trade_id}")
                return "SET_BREAKEVEN"
        return None
    
    def _exec_tighten_sl(self, trade_id: str, percent: str) -> Optional[str]:
        trade_id = trade_id.strip()
        pct = float(percent)
        
        if self.settings.mode != AgentMode.AUTO:
            self._log(f"💡 Подтянуть SL {trade_id} на {pct}%")
            return None
        
        with self.positions_lock:
            pos = self.positions.get(trade_id)
        
        if not pos:
            return None
        
        if pos.side == 'LONG':
            new_sl = pos.stop_loss * (1 + pct / 100)
        else:
            new_sl = pos.stop_loss * (1 - pct / 100)
        
        return self._exec_set_sl(trade_id, str(new_sl))
    
    def _exec_set_tp(self, trade_id: str, price: str) -> Optional[str]:
        trade_id = trade_id.strip()
        new_tp = float(price)
        
        if self.settings.mode != AgentMode.AUTO:
            self._log(f"ðŸ’¡ TP {trade_id} â†’ ${new_tp}")
            return None
        
        callback = self.trader_callbacks.get('adjust_tp')
        if callback:
            result = callback(trade_id, new_tp)
            if result:
                self._log(f"âœ… TP: {trade_id} â†’ ${new_tp}")
                return "SET_TP"
        return None
    
    def _exec_enable_trailing(self, trade_id: str) -> Optional[str]:
        trade_id = trade_id.strip()
        if self.settings.mode != AgentMode.AUTO:
            return None
        callback = self.trader_callbacks.get('toggle_trailing')
        if callback and callback(trade_id, True):
            self._log(f"âœ… Trailing ON: {trade_id}")
            return "ENABLE_TRAILING"
        return None
    
    def _exec_disable_trailing(self, trade_id: str) -> Optional[str]:
        trade_id = trade_id.strip()
        if self.settings.mode != AgentMode.AUTO:
            return None
        callback = self.trader_callbacks.get('toggle_trailing')
        if callback and callback(trade_id, False):
            self._log(f"âœ… Trailing OFF: {trade_id}")
            return "DISABLE_TRAILING"
        return None
    
    def _exec_blacklist(self, symbol: str, hours: str, reason: str) -> Optional[str]:
        from database import db
        db.set_coin_blacklist(symbol.strip(), True, reason.strip(), int(hours))
        self._log(f"🚫 Blacklist: {symbol} на {hours}ч")
        return "BLACKLIST"
    
    def _exec_pause_scanner(self, *args) -> Optional[str]:
        if self.settings.mode != AgentMode.AUTO:
            self._log("💡 Остановить сканер")
            return None
        callback = self.trader_callbacks.get('pause_scanner')
        if callback:
            callback()
            self._log("⏸️ Сканер остановлен")
            self._notify("⏸️ Agent остановил сканер")
            return "PAUSE_SCANNER"
        return None
    
    def _exec_resume_scanner(self, *args) -> Optional[str]:
        if self.settings.mode != AgentMode.AUTO:
            return None
        callback = self.trader_callbacks.get('resume_scanner')
        if callback:
            callback()
            self._log("▶️ Сканер возобновлён")
            return "RESUME_SCANNER"
        return None
    
    def _exec_close_all(self, reason: str) -> Optional[str]:
        reason = reason.strip()
        
        if self.settings.mode != AgentMode.AUTO:
            self._log(f"ðŸ’¡ CLOSE ALL: {reason}")
            self._notify(f"🚨 Рекомендация: закрыть ВСЕ - {reason}")
            return None
        
        with self.positions_lock:
            trade_ids = list(self.positions.keys())
        
        closed = 0
        for trade_id in trade_ids:
            if self._exec_close(trade_id):
                closed += 1
        
        self._log(f"🚨 CLOSE ALL: {closed} позиций - {reason}")
        self._notify(f"🚨 Закрыто {closed} позиций: {reason}")
        return f"CLOSE_ALL_{closed}"
    
    # ========================================================================
    # LEARNING
    # ========================================================================
    
    def _learning_cycle(self):
        from database import db
        pending = db.get_pending_decision_results()
        
        for decision in pending:
            trade_id = decision.get('trade_id')
            if not trade_id:
                continue
            
            trades = db.get_trades(limit=100, only_closed=True)
            for trade in trades:
                if trade.get('trade_id') == trade_id:
                    pnl_after = trade.get('pnl_usdt', 0)
                    pnl_before = decision.get('pnl_before', 0)
                    action = decision.get('action', 'HOLD')
                    
                    was_correct = False
                    if action == 'CLOSE' and pnl_after < pnl_before:
                        was_correct = True
                    elif action == 'HOLD' and pnl_after > pnl_before:
                        was_correct = True
                    elif action.startswith('SET_') and pnl_after > 0:
                        was_correct = True
                    
                    db.update_decision_result(decision['id'], pnl_after, was_correct)
                    
                    if not was_correct:
                        self._create_lesson(decision, trade)
                    break
    
    def _create_lesson(self, decision: Dict, trade: Dict):
        from database import db
        symbol = decision.get('symbol', '')
        action = decision.get('action', '')
        pnl_before = decision.get('pnl_before', 0)
        pnl_after = trade.get('pnl_usdt', 0)
        
        lesson = f"Не {action} для {symbol} при PnL {pnl_before:.1f}%"
        db.save_agent_lesson(lesson=lesson, category="position", symbol=symbol, confidence=0.5, source_decision_id=decision.get('id'))
        self.stats['lessons_learned'] += 1
    
    # ========================================================================
    # POSITION TRACKING
    # ========================================================================
    
    def track_position(self, data: Dict):
        trade_id = data.get('id') or data.get('trade_id')
        if not trade_id:
            logger.warning(f"[AgentV3] track_position called without trade_id! Data: {data}")
            return
        
        opened_at = data.get('opened_at')
        if isinstance(opened_at, str):
            try:
                opened_at = datetime.strptime(opened_at, '%Y-%m-%d %H:%M:%S')
            except Exception:
                opened_at = datetime.now()
        elif not isinstance(opened_at, datetime):
            opened_at = datetime.now()
        
        with self.positions_lock:
            if trade_id not in self.positions:
                self.positions[trade_id] = PositionData(
                    trade_id=trade_id,
                    symbol=data.get('symbol', ''),
                    side=data.get('side', 'SHORT'),
                    entry_price=float(data.get('entry_price', 0)),
                    current_price=float(data.get('current_price', 0)),
                    stop_loss=float(data.get('stop_loss', 0)),
                    take_profit_1=float(data.get('take_profit_1', 0)),
                    take_profit_2=float(data.get('take_profit_2', 0)),
                    trailing_stop=float(data.get('trailing_stop', 0)),
                    pnl_usdt=float(data.get('pnl_usdt', 0)),
                    pnl_percent=float(data.get('pnl_percent', 0)),
                    size_usdt=float(data.get('size_usdt', 0)),
                    opened_at=opened_at,
                    trail_activated=data.get('trail_activated', False)
                )
                logger.info(f"[AgentV3] âž• Tracked: {trade_id} {data.get('symbol')} {data.get('side')}")
    
    def update_position(self, data: Dict):
        trade_id = data.get('id') or data.get('trade_id')
        if not trade_id:
            return
        
        with self.positions_lock:
            if trade_id in self.positions:
                pos = self.positions[trade_id]
                old_pnl = pos.pnl_percent
                
                pos.current_price = float(data.get('current_price', pos.current_price))
                pos.pnl_usdt = float(data.get('pnl_usdt', pos.pnl_usdt))
                pos.pnl_percent = float(data.get('pnl_percent', pos.pnl_percent))
                pos.trailing_stop = float(data.get('trailing_stop', pos.trailing_stop))
                pos.trail_activated = data.get('trail_activated', pos.trail_activated)
                pos.stop_loss = float(data.get('stop_loss', pos.stop_loss))
                
                # Обновляем максимум
                if pos.pnl_percent > pos.max_pnl_percent:
                    pos.max_pnl_percent = pos.pnl_percent
                
                # ========== КРИТИЧЕСКИЙ ТРИГГЕР: DRAWDOWN ==========
                # Если позиция была в хорошем плюсе (>3%), а теперь резко упала
                drawdown = pos.max_pnl_percent - pos.pnl_percent
                
                # Триггер 1: Был в +3% или более, упал на 2%+ от максимума
                if pos.max_pnl_percent >= 3.0 and drawdown >= 2.0:
                    # Срочная проверка (сброс cooldown)
                    pos.last_check = None
                    self._log(f"⚠️ DRAWDOWN {pos.symbol}: был +{pos.max_pnl_percent:.1f}%, сейчас +{pos.pnl_percent:.1f}%")
                
                # Триггер 2: Был в плюсе, ушёл в минус
                if old_pnl > 0 and pos.pnl_percent < -1.0:
                    pos.last_check = None
                    self._log(f"🔴 REVERSAL {pos.symbol}: было +{old_pnl:.1f}%, стало {pos.pnl_percent:.1f}%")
    
    def untrack_position(self, trade_id: str):
        with self.positions_lock:
            if trade_id in self.positions:
                del self.positions[trade_id]
    
    def update_market(self, btc_price: float, btc_trend: str = "neutral", 
                      btc_rsi: float = 50, btc_change_1h: float = 0, btc_change_24h: float = 0):
        self.market.btc_price = btc_price
        self.market.btc_trend = btc_trend
        self.market.btc_rsi = btc_rsi
        self.market.btc_change_1h = btc_change_1h
        self.market.btc_change_24h = btc_change_24h
    
    def record_trade_result(self, symbol: str, pnl: float, hold_minutes: float = 0, side: str = ""):
        from database import db
        db.update_coin_memory(symbol, pnl, hold_minutes, side)
    
    # ========================================================================
    # UTILITIES
    # ========================================================================
    
    def _can_take_action(self) -> bool:
        now = datetime.now()
        self._action_timestamps = [t for t in self._action_timestamps if (now - t).seconds < 60]
        return len(self._action_timestamps) < self.settings.max_actions_per_minute
    
    def _record_action(self):
        self._action_timestamps.append(datetime.now())
        self.stats['actions_taken'] += 1
    
    def _log(self, message: str):
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_entry = f"[{timestamp}] {message}"
        self.recent_logs.append(log_entry)
        if len(self.recent_logs) > 100:
            self.recent_logs = self.recent_logs[-100:]
        logger.info(f"[AgentV3] {message}")
    
    def _notify(self, message: str):
        if self.telegram_callback:
            try:
                self.telegram_callback(message)
            except Exception as e:
                logger.error(f"[AgentV3] Telegram error: {e}")
    
    def _format_coin_memory(self, mem: Dict) -> str:
        if not mem or mem.get('total_trades', 0) == 0:
            return "Нет истории"
        total = max(mem.get('total_trades', 1), 1)
        return f"""Сделок: {total} | Win: {mem.get('wins', 0)} | Loss: {mem.get('losses', 0)}
WR: {(mem.get('wins', 0) / total * 100):.0f}% | PnL: ${mem.get('total_pnl', 0):.2f}
Streak: {mem.get('current_streak', 0)} | Last: {mem.get('last_result', 'N/A')}"""
    
    def _format_lessons(self, lessons: List[Dict]) -> str:
        if not lessons:
            return "Нет уроков"
        return "\n".join([f"- {l.get('lesson', '')} ({l.get('confidence', 0):.1f})" for l in lessons[:3]])
    
    def _save_state(self):
        from database import db
        db.set_agent_state('stats', json.dumps(self.stats))
        db.set_agent_state('market_mode', self.market.market_mode)
    
    def _restore_state(self):
        from database import db
        stats_json = db.get_agent_state('stats')
        if stats_json:
            try:
                self.stats = json.loads(stats_json)
            except Exception:
                pass
        market_mode = db.get_agent_state('market_mode')
        if market_mode:
            self.market.market_mode = market_mode
    
    # ========================================================================
    # PUBLIC API
    # ========================================================================
    
    def get_status(self) -> Dict:
        return {
            'enabled': self.settings.enabled,
            'running': self.running,
            'mode': self.settings.mode.value,
            'positions_tracked': len(self.positions),
            'market_mode': self.market.market_mode,
            'btc_price': self.market.btc_price,
            'btc_trend': self.market.btc_trend,
            'stats': self.stats,
            'ai_provider': self.ai_provider.get_status(),
            'aggressiveness': self.settings.aggressiveness
        }
    
    def get_logs(self, n: int = 50) -> List[str]:
        return self.recent_logs[-n:]
    
    def get_decisions(self, n: int = 20) -> List[Dict]:
        return self.recent_decisions[-n:]
    
    def update_settings(self, settings: Dict):
        if 'mode' in settings:
            mode_str = str(settings['mode']).lower()
            self.settings.mode = {'observe': AgentMode.OBSERVE, 'recommend': AgentMode.RECOMMEND}.get(mode_str, AgentMode.AUTO)
        
        for key in ['aggressiveness', 'enabled', 'min_position_age_minutes', 'profit_to_protect_percent',
                    'drawdown_trigger_percent', 'stagnation_minutes', 'position_cooldown_minutes',
                    'validate_signals', 'learn_from_mistakes', 'auto_adjust_sl', 'auto_adjust_tp']:
            if key in settings:
                setattr(self.settings, key, settings[key])
    
    def ask_ai(self, question: str) -> str:
        """Задать вопрос AI с полным контекстом"""
        # Добавляем инсайты из истории
        insights = self.tools.get_trading_insights()
        
        # Формируем список РЕАЛЬНЫХ позиций
        positions_text = self._format_all_positions()
        
        prompt = f"""ТЕКУЩЕЕ СОСТОЯНИЕ:
{self.market.to_prompt_str()}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
ОТКРЫТЫЕ ПОЗИЦИИ ({len(self.positions)}):
{positions_text if positions_text else "НЕТ ОТКРЫТЫХ ПОЗИЦИЙ"}
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

ИНСАЙТЫ ИЗ ИСТОРИИ:
{insights}

ВОПРОС: {question}

⚠️ КРИТИЧЕСКИ ВАЖНО:
- Работай ТОЛЬКО с позициями из списка выше
- Если позиций нет — так и скажи
- НИКОГДА не выдумывай позиции, ID или цены
- Используй ТОЧНЫЕ trade_id (формат RVV-XXXX)"""
        
        response, provider, _ = self.ai_provider.call(prompt, self.SYSTEM_PROMPT, max_tokens=1500)
        return response or "AI недоступен"
    
    def _format_all_positions(self) -> str:
        """Форматирование всех позиций для AI"""
        with self.positions_lock:
            if not self.positions:
                return ""
            
            lines = []
            for trade_id, pos in self.positions.items():
                lines.append(pos.to_prompt_str())
            return '\n'.join(lines)
    
    def generate_strategy(self) -> str:
        """Генерация новой стратегии на основе анализа истории"""
        from database import db
        
        # Собираем все паттерны
        patterns = db.get_pattern_analysis()
        
        # Формируем промпт для AI
        prompt = f"""Проанализируй историю торговли и предложи КОНКРЕТНЫЕ улучшения стратегии.

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
СТАТИСТИКА ПО ЧАСАМ:
{self._format_hourly_stats(patterns.get('best_hours', []))}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
СТАТИСТИКА ПО CONFIDENCE:
{self._format_confidence_stats(patterns.get('confidence_stats', []))}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
SHORT vs LONG:
{self._format_side_stats(patterns.get('side_stats', {}))}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
ЛУЧШИЕ МОНЕТЫ (торговать чаще):
{self._format_coins(patterns.get('coins', {}).get('best', []))}

ХУДШИЕ МОНЕТЫ (избегать):
{self._format_coins(patterns.get('coins', {}).get('worst', []))}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
ПРИЧИНЫ ЗАКРЫТИЯ:
{self._format_close_reasons(patterns.get('close_reasons', []))}

â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

На основе этих данных:
1. Какие часы лучше для торговли?
2. Какой минимальный confidence ставить?
3. SHORT или LONG работает лучше?
4. Какие монеты добавить в blacklist?
5. Как улучшить управление позициями?

Дай КОНКРЕТНЫЕ рекомендации с числами."""

        response, provider, _ = self.ai_provider.call(prompt, 
            "Ты аналитик торговых стратегий. Анализируй данные и давай конкретные рекомендации.", 
            max_tokens=2000)
        
        if response:
            self._log(f"📊 Стратегия сгенерирована [{provider}]")
            
            # Сохраняем как урок
            db.save_agent_lesson(
                lesson=f"Strategy update: {response[:200]}...",
                category="strategy",
                confidence=0.7,
                source_decision_id=None
            )
        
        return response or "Не удалось сгенерировать стратегию"
    
    def _format_hourly_stats(self, stats: List[Dict]) -> str:
        if not stats:
            return "Нет данных"
        lines = []
        for h in stats:
            lines.append(f"  {h.get('hour', 0):02d}:00 — {h.get('total', 0)} сделок, WR: {h.get('win_rate', 0):.0f}%, PnL: ${h.get('total_pnl', 0):.0f}")
        return '\n'.join(lines) or "Нет данных"
    
    def _format_confidence_stats(self, stats: List[Dict]) -> str:
        if not stats:
            return "Нет данных"
        lines = []
        for c in stats:
            lines.append(f"  {c.get('confidence_range', '?')} — {c.get('total', 0)} сделок, WR: {c.get('win_rate', 0):.0f}%, PnL: ${c.get('total_pnl', 0):.0f}")
        return '\n'.join(lines) or "Нет данных"
    
    def _format_side_stats(self, stats: Dict) -> str:
        if not stats:
            return "Нет данных"
        lines = []
        for side, data in stats.items():
            lines.append(f"  {side}: {data.get('total', 0)} сделок, WR: {data.get('win_rate', 0):.0f}%, PnL: ${data.get('total_pnl', 0):.0f}")
        return '\n'.join(lines) or "Нет данных"
    
    def _format_coins(self, coins: List[Dict]) -> str:
        if not coins:
            return "Нет данных"
        lines = []
        for c in coins[:5]:
            sym = c.get('symbol', '?').split('/')[0]
            lines.append(f"  {sym}: {c.get('trades', 0)} сделок, WR: {c.get('win_rate', 0):.0f}%, PnL: ${c.get('total_pnl', 0):.0f}")
        return '\n'.join(lines) or "Нет данных"
    
    def _format_close_reasons(self, reasons: List[Dict]) -> str:
        if not reasons:
            return "Нет данных"
        lines = []
        for r in reasons:
            lines.append(f"  {r.get('close_reason', '?')}: {r.get('total', 0)}x, PnL: ${r.get('total_pnl', 0):.0f}, avg: ${r.get('avg_pnl', 0):.1f}")
        return '\n'.join(lines) or "Нет данных"


# Алиас для совместимости
CryptoAgent = CryptoAgentV3