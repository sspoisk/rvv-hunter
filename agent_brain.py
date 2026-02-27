"""
RVV Hunter v5.0 - Agent Brain (Память агента)
Отдельная БД для хранения памяти, команд, стратегий
ПЕРЕНОСИМАЯ между ботами!
"""

import sqlite3
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# Путь к БД памяти агента (отдельный файл!)
BRAIN_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'agent_brain.db')


class AgentBrain:
    """
    Память агента - хранит:
    - Разговоры с пользователем
    - Команды пользователя ("не торгуй ALPACA")
    - Правила торговли
    - Стратегии (с результатами бэктеста)
    - Выводы по монетам
    - Уроки из ошибок
    - Состояние агента
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or BRAIN_DB_PATH
        self.lock = threading.Lock()
        self._ensure_db_dir()
        self._init_db()
        logger.info(f"[BRAIN] Initialized: {self.db_path}")
    
    def _ensure_db_dir(self):
        """Создать директорию для БД если нет"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
    
    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для соединения"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[BRAIN] DB error: {e}")
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Инициализация всех таблиц"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            # 1. CONVERSATIONS - История диалогов
            cur.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    user_message TEXT NOT NULL,
                    agent_response TEXT,
                    tools_used TEXT,
                    tokens_used INTEGER DEFAULT 0,
                    provider TEXT DEFAULT 'deepseek',
                    context_summary TEXT,
                    sentiment TEXT
                )
            ''')
            
            # 2. USER_COMMANDS - Команды пользователя
            cur.execute('''
                CREATE TABLE IF NOT EXISTS user_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    command_type TEXT DEFAULT 'rule',
                    target TEXT,
                    parameters TEXT,
                    is_active INTEGER DEFAULT 1,
                    priority INTEGER DEFAULT 5,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT,
                    times_applied INTEGER DEFAULT 0,
                    last_applied TEXT
                )
            ''')
            
            # 3. TRADING_RULES - Правила торговли
            cur.execute('''
                CREATE TABLE IF NOT EXISTS trading_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    condition TEXT NOT NULL,
                    action TEXT NOT NULL,
                    priority INTEGER DEFAULT 5,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    created_by TEXT DEFAULT 'user',
                    times_triggered INTEGER DEFAULT 0,
                    last_triggered TEXT,
                    success_rate REAL DEFAULT 0
                )
            ''')
            
            # 4. STRATEGIES - Стратегии
            cur.execute('''
                CREATE TABLE IF NOT EXISTS strategies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    strategy_type TEXT DEFAULT 'custom',
                    parameters TEXT NOT NULL,
                    entry_conditions TEXT,
                    exit_conditions TEXT,
                    risk_params TEXT,
                    backtest_results TEXT,
                    live_results TEXT,
                    total_trades INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    profit_factor REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0,
                    is_active INTEGER DEFAULT 0,
                    is_recommended INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 5. COIN_INSIGHTS - Выводы по монетам
            cur.execute('''
                CREATE TABLE IF NOT EXISTS coin_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    insights TEXT,
                    best_timeframe TEXT,
                    best_entry_conditions TEXT,
                    avoid_conditions TEXT,
                    total_trades INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    avg_hold_time_minutes REAL DEFAULT 0,
                    best_side TEXT,
                    volatility_profile TEXT,
                    correlation_btc REAL DEFAULT 0,
                    last_trade_at TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 6. LESSONS_LEARNED - Уроки из ошибок
            cur.execute('''
                CREATE TABLE IF NOT EXISTS lessons_learned (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT,
                    symbol TEXT,
                    mistake_type TEXT,
                    description TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    prevention_rule TEXT,
                    severity TEXT DEFAULT 'medium',
                    is_applied INTEGER DEFAULT 0,
                    times_prevented INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 7. AGENT_STATE - Состояние агента
            cur.execute('''
                CREATE TABLE IF NOT EXISTS agent_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 8. CANDLES_CACHE - Кэш свечей (до 1 месяца)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS candles_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, timeframe, timestamp)
                )
            ''')
            
            # 9. TRADING_PATTERNS - Паттерны торговли (время, день, BTC тренд)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS trading_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_hash TEXT NOT NULL,
                    factor_type TEXT NOT NULL,
                    factor_value TEXT NOT NULL,
                    trades_count INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    gross_profit REAL DEFAULT 0,
                    gross_loss REAL DEFAULT 0,
                    profit_factor REAL DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(strategy_hash, factor_type, factor_value)
                )
            ''')
            
            # Индексы для быстрого поиска
            cur.execute('CREATE INDEX IF NOT EXISTS idx_conversations_ts ON conversations(timestamp)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_commands_active ON user_commands(is_active)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_rules_active ON trading_rules(is_active)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies(is_active)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_insights_symbol ON coin_insights(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_lessons_symbol ON lessons_learned(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf ON candles_cache(symbol, timeframe)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_candles_timestamp ON candles_cache(timestamp)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_patterns_hash ON trading_patterns(strategy_hash)')
            
            conn.commit()
            logger.info("[BRAIN] All tables initialized")
    
    # =========================================================================
    # CONVERSATIONS - Диалоги
    # =========================================================================
    
    def save_conversation(self, user_message: str, agent_response: str,
                         tools_used: List[str] = None, tokens: int = 0,
                         provider: str = 'deepseek') -> int:
        """Сохранить диалог"""
        try:
            with self.lock:
                with self.get_connection() as conn:
                    cur = conn.cursor()
                    # Авто-создание таблицы если не существует (старые БД)
                    cur.execute('''
                        CREATE TABLE IF NOT EXISTS conversations (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT,
                            user_message TEXT,
                            agent_response TEXT,
                            tools_used TEXT,
                            tokens_used INTEGER DEFAULT 0,
                            provider TEXT DEFAULT 'deepseek'
                        )
                    ''')
                    cur.execute('''
                        INSERT INTO conversations 
                        (user_message, agent_response, tools_used, tokens_used, provider, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        user_message,
                        agent_response,
                        json.dumps(tools_used or []),
                        tokens,
                        provider,
                        datetime.now().isoformat()
                    ))
                    return cur.lastrowid
        except Exception as e:
            logger.warning(f"[BRAIN] save_conversation error (non-critical): {e}")
            return 0
    
    def get_recent_conversations(self, limit: int = 10) -> List[Dict]:
        """Получить последние диалоги"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM conversations 
                ORDER BY timestamp DESC LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cur.fetchall()]
    
    def get_conversation_context(self, last_n: int = 5) -> str:
        """Получить контекст последних разговоров для AI"""
        conversations = self.get_recent_conversations(last_n)
        if not conversations:
            return ""
        
        context_parts = []
        for conv in reversed(conversations):  # От старых к новым
            context_parts.append(f"User: {conv['user_message']}")
            if conv['agent_response']:
                # Сокращаем длинные ответы
                response = conv['agent_response']
                if len(response) > 500:
                    response = response[:500] + "..."
                context_parts.append(f"Agent: {response}")
        
        return "\n".join(context_parts)
    
    def search_conversations(self, query: str, limit: int = 20) -> List[Dict]:
        """Поиск по истории диалогов"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM conversations 
                WHERE user_message LIKE ? OR agent_response LIKE ?
                ORDER BY timestamp DESC LIMIT ?
            ''', (f'%{query}%', f'%{query}%', limit))
            return [dict(row) for row in cur.fetchall()]
    
    # =========================================================================
    # USER_COMMANDS - Команды пользователя
    # =========================================================================
    
    def save_command(self, command: str, command_type: str = 'rule',
                    target: str = None, parameters: Dict = None,
                    priority: int = 5, expires_at: str = None) -> int:
        """Сохранить команду пользователя"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO user_commands 
                    (command, command_type, target, parameters, priority, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    command,
                    command_type,
                    target,
                    json.dumps(parameters or {}),
                    priority,
                    expires_at
                ))
                logger.info(f"[BRAIN] Saved command: {command[:50]}...")
                return cur.lastrowid
    
    def get_active_commands(self, command_type: str = None) -> List[Dict]:
        """Получить активные команды"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if command_type:
                cur.execute('''
                    SELECT * FROM user_commands 
                    WHERE is_active = 1 AND command_type = ?
                    ORDER BY priority DESC, created_at DESC
                ''', (command_type,))
            else:
                cur.execute('''
                    SELECT * FROM user_commands 
                    WHERE is_active = 1
                    ORDER BY priority DESC, created_at DESC
                ''')
            return [dict(row) for row in cur.fetchall()]
    
    def deactivate_command(self, command_id: int) -> bool:
        """Деактивировать команду"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('UPDATE user_commands SET is_active = 0 WHERE id = ?', (command_id,))
                return cur.rowcount > 0
    
    def mark_command_applied(self, command_id: int):
        """Отметить применение команды"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    UPDATE user_commands 
                    SET times_applied = times_applied + 1, last_applied = ?
                    WHERE id = ?
                ''', (datetime.now().isoformat(), command_id))
    
    def find_command_for_symbol(self, symbol: str) -> Optional[Dict]:
        """Найти команду для символа (например, blacklist)"""
        commands = self.get_active_commands()
        for cmd in commands:
            if cmd['target'] and symbol.upper() in cmd['target'].upper():
                return cmd
            if cmd['command'] and symbol.upper() in cmd['command'].upper():
                return cmd
        return None
    
    # =========================================================================
    # TRADING_RULES - Правила торговли
    # =========================================================================
    
    def save_rule(self, name: str, condition: str, action: str,
                 description: str = None, priority: int = 5,
                 created_by: str = 'user') -> int:
        """Сохранить правило торговли"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO trading_rules 
                    (name, description, condition, action, priority, created_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (name, description, condition, action, priority, created_by))
                logger.info(f"[BRAIN] Saved rule: {name}")
                return cur.lastrowid
    
    def get_active_rules(self) -> List[Dict]:
        """Получить активные правила"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM trading_rules 
                WHERE is_active = 1 
                ORDER BY priority DESC
            ''')
            return [dict(row) for row in cur.fetchall()]
    
    def check_rules(self, context: Dict) -> List[Dict]:
        """Проверить какие правила срабатывают для контекста"""
        triggered = []
        rules = self.get_active_rules()
        
        for rule in rules:
            try:
                condition = rule['condition']
                # Простая проверка условий
                if self._evaluate_condition(condition, context):
                    triggered.append(rule)
                    self._mark_rule_triggered(rule['id'])
            except Exception as e:
                logger.debug(f"[BRAIN] Rule check error: {e}")
        
        return triggered
    
    def _evaluate_condition(self, condition: str, context: Dict) -> bool:
        """Оценить условие правила"""
        try:
            # Заменяем переменные на значения из контекста
            for key, value in context.items():
                condition = condition.replace(f'{{{key}}}', str(value))
                condition = condition.replace(f'${key}', str(value))
            
            # Простые проверки
            if 'btc_change' in condition.lower():
                btc_change = context.get('btc_change', 0)
                if '>' in condition:
                    threshold = float(condition.split('>')[-1].strip().replace('%', ''))
                    return btc_change > threshold
                elif '<' in condition:
                    threshold = float(condition.split('<')[-1].strip().replace('%', ''))
                    return btc_change < threshold
            
            return False
        except Exception:
            return False
    
    def _mark_rule_triggered(self, rule_id: int):
        """Отметить срабатывание правила"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    UPDATE trading_rules 
                    SET times_triggered = times_triggered + 1, last_triggered = ?
                    WHERE id = ?
                ''', (datetime.now().isoformat(), rule_id))
    
    # =========================================================================
    # STRATEGIES - Стратегии
    # =========================================================================
    
    def save_strategy(self, name: str, parameters: Dict,
                     description: str = None, strategy_type: str = 'custom',
                     entry_conditions: str = None, exit_conditions: str = None,
                     backtest_results: Dict = None) -> int:
        """Сохранить стратегию"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                
                # Проверяем существует ли
                cur.execute('SELECT id FROM strategies WHERE name = ?', (name,))
                existing = cur.fetchone()
                
                if existing:
                    # Обновляем
                    cur.execute('''
                        UPDATE strategies SET
                        description = ?, parameters = ?, strategy_type = ?,
                        entry_conditions = ?, exit_conditions = ?,
                        backtest_results = ?, updated_at = ?
                        WHERE name = ?
                    ''', (
                        description,
                        json.dumps(parameters),
                        strategy_type,
                        entry_conditions,
                        exit_conditions,
                        json.dumps(backtest_results or {}),
                        datetime.now().isoformat(),
                        name
                    ))
                    return existing[0]
                else:
                    # Создаём новую
                    cur.execute('''
                        INSERT INTO strategies 
                        (name, description, parameters, strategy_type, 
                         entry_conditions, exit_conditions, backtest_results)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        name,
                        description,
                        json.dumps(parameters),
                        strategy_type,
                        entry_conditions,
                        exit_conditions,
                        json.dumps(backtest_results or {})
                    ))
                    logger.info(f"[BRAIN] Saved strategy: {name}")
                    return cur.lastrowid
    
    def get_strategy(self, name: str) -> Optional[Dict]:
        """Получить стратегию по имени"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM strategies WHERE name = ?', (name,))
            row = cur.fetchone()
            if row:
                result = dict(row)
                result['parameters'] = json.loads(result.get('parameters', '{}'))
                result['backtest_results'] = json.loads(result.get('backtest_results', '{}'))
                return result
            return None
    
    def get_all_strategies(self, active_only: bool = False) -> List[Dict]:
        """Получить все стратегии"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if active_only:
                cur.execute('SELECT * FROM strategies WHERE is_active = 1 ORDER BY win_rate DESC')
            else:
                cur.execute('SELECT * FROM strategies ORDER BY updated_at DESC')
            
            results = []
            for row in cur.fetchall():
                r = dict(row)
                r['parameters'] = json.loads(r.get('parameters', '{}'))
                r['backtest_results'] = json.loads(r.get('backtest_results', '{}'))
                results.append(r)
            return results
    
    def get_recommended_strategies(self) -> List[Dict]:
        """Получить рекомендованные стратегии"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM strategies 
                WHERE is_recommended = 1 OR (win_rate > 50 AND total_trades > 10)
                ORDER BY profit_factor DESC
            ''')
            results = []
            for row in cur.fetchall():
                r = dict(row)
                r['parameters'] = json.loads(r.get('parameters', '{}'))
                results.append(r)
            return results
    
    def update_strategy_results(self, name: str, win_rate: float,
                               profit_factor: float, total_trades: int,
                               max_drawdown: float = 0):
        """Обновить результаты стратегии"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    UPDATE strategies SET
                    win_rate = ?, profit_factor = ?, total_trades = ?,
                    max_drawdown = ?, updated_at = ?,
                    is_recommended = CASE WHEN ? > 55 AND ? > 1.2 THEN 1 ELSE 0 END
                    WHERE name = ?
                ''', (win_rate, profit_factor, total_trades, max_drawdown,
                      datetime.now().isoformat(), win_rate, profit_factor, name))
    
    def activate_strategy(self, name: str, active: bool = True) -> bool:
        """Активировать/деактивировать стратегию"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('UPDATE strategies SET is_active = ? WHERE name = ?',
                           (1 if active else 0, name))
                return cur.rowcount > 0
    
    # =========================================================================
    # COIN_INSIGHTS - Выводы по монетам
    # =========================================================================
    
    def save_coin_insight(self, symbol: str, insights: str,
                         best_timeframe: str = None, best_entry: str = None,
                         avoid_conditions: str = None, win_rate: float = 0,
                         avg_pnl: float = 0, total_trades: int = 0) -> int:
        """Сохранить выводы по монете"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                
                cur.execute('SELECT id FROM coin_insights WHERE symbol = ?', (symbol,))
                existing = cur.fetchone()
                
                if existing:
                    cur.execute('''
                        UPDATE coin_insights SET
                        insights = ?, best_timeframe = ?, best_entry_conditions = ?,
                        avoid_conditions = ?, win_rate = ?, avg_pnl = ?,
                        total_trades = ?, updated_at = ?
                        WHERE symbol = ?
                    ''', (insights, best_timeframe, best_entry, avoid_conditions,
                          win_rate, avg_pnl, total_trades,
                          datetime.now().isoformat(), symbol))
                    return existing[0]
                else:
                    cur.execute('''
                        INSERT INTO coin_insights 
                        (symbol, insights, best_timeframe, best_entry_conditions,
                         avoid_conditions, win_rate, avg_pnl, total_trades)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (symbol, insights, best_timeframe, best_entry,
                          avoid_conditions, win_rate, avg_pnl, total_trades))
                    return cur.lastrowid
    
    def get_coin_insight(self, symbol: str) -> Optional[Dict]:
        """Получить выводы по монете"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM coin_insights WHERE symbol = ?', (symbol,))
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_best_coins(self, min_trades: int = 5, min_win_rate: float = 50) -> List[Dict]:
        """Получить лучшие монеты для торговли"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM coin_insights 
                WHERE total_trades >= ? AND win_rate >= ?
                ORDER BY win_rate DESC, avg_pnl DESC
            ''', (min_trades, min_win_rate))
            return [dict(row) for row in cur.fetchall()]
    
    def get_worst_coins(self, min_trades: int = 3) -> List[Dict]:
        """Получить худшие монеты (избегать)"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM coin_insights 
                WHERE total_trades >= ? AND win_rate < 40
                ORDER BY win_rate ASC
            ''', (min_trades,))
            return [dict(row) for row in cur.fetchall()]
    
    # =========================================================================
    # LESSONS_LEARNED - Уроки из ошибок
    # =========================================================================
    
    def save_lesson(self, description: str, lesson: str,
                   trade_id: str = None, symbol: str = None,
                   mistake_type: str = None, prevention_rule: str = None,
                   severity: str = 'medium') -> int:
        """Сохранить урок из ошибки"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO lessons_learned 
                    (trade_id, symbol, mistake_type, description, lesson,
                     prevention_rule, severity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (trade_id, symbol, mistake_type, description, lesson,
                      prevention_rule, severity))
                logger.info(f"[BRAIN] Saved lesson: {lesson[:50]}...")
                return cur.lastrowid
    
    def get_lessons(self, symbol: str = None, limit: int = 20) -> List[Dict]:
        """Получить уроки"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if symbol:
                cur.execute('''
                    SELECT * FROM lessons_learned 
                    WHERE symbol = ?
                    ORDER BY created_at DESC LIMIT ?
                ''', (symbol, limit))
            else:
                cur.execute('''
                    SELECT * FROM lessons_learned 
                    ORDER BY created_at DESC LIMIT ?
                ''', (limit,))
            return [dict(row) for row in cur.fetchall()]
    
    def get_lessons_summary(self) -> str:
        """Получить сводку уроков для AI"""
        lessons = self.get_lessons(limit=10)
        if not lessons:
            return "Уроков пока нет."
        
        summary_parts = ["Уроки из прошлых ошибок:"]
        for lesson in lessons:
            summary_parts.append(f"• {lesson['lesson']}")
        
        return "\n".join(summary_parts)
    
    # =========================================================================
    # AGENT_STATE - Состояние агента
    # =========================================================================
    
    def set_state(self, key: str, value: Any):
        """Сохранить состояние агента"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                cur.execute('''
                    INSERT OR REPLACE INTO agent_state (key, value, updated_at)
                    VALUES (?, ?, ?)
                ''', (key, value_str, datetime.now().isoformat()))
    
    def get_state(self, key: str, default: Any = None) -> Any:
        """Получить состояние агента"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT value FROM agent_state WHERE key = ?', (key,))
            row = cur.fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except Exception:
                    return row[0]
            return default
    
    def get_all_state(self) -> Dict:
        """Получить всё состояние"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT key, value FROM agent_state')
            result = {}
            for row in cur.fetchall():
                try:
                    result[row[0]] = json.loads(row[1])
                except Exception:
                    result[row[0]] = row[1]
            return result
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    def get_full_context_for_ai(self) -> str:
        """Получить полный контекст для AI запроса"""
        parts = []
        
        # Последние диалоги
        conv_context = self.get_conversation_context(3)
        if conv_context:
            parts.append("=== ПОСЛЕДНИЕ РАЗГОВОРЫ ===")
            parts.append(conv_context)
        
        # Активные команды пользователя
        commands = self.get_active_commands()
        if commands:
            parts.append("\n=== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ===")
            for cmd in commands[:5]:
                parts.append(f"• {cmd['command']}")
        
        # Активные правила
        rules = self.get_active_rules()
        if rules:
            parts.append("\n=== АКТИВНЫЕ ПРАВИЛА ===")
            for rule in rules[:5]:
                parts.append(f"• {rule['name']}: {rule['condition']} â†’ {rule['action']}")
        
        # Уроки
        lessons_summary = self.get_lessons_summary()
        if lessons_summary != "Уроков пока нет.":
            parts.append(f"\n=== {lessons_summary}")
        
        return "\n".join(parts)
    
    def get_brain_stats(self) -> Dict:
        """Статистика памяти"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            stats = {}
            
            cur.execute('SELECT COUNT(*) FROM conversations')
            stats['total_conversations'] = cur.fetchone()[0]
            
            cur.execute('SELECT COUNT(*) FROM user_commands WHERE is_active = 1')
            stats['active_commands'] = cur.fetchone()[0]
            
            cur.execute('SELECT COUNT(*) FROM trading_rules WHERE is_active = 1')
            stats['active_rules'] = cur.fetchone()[0]
            
            cur.execute('SELECT COUNT(*) FROM strategies')
            stats['total_strategies'] = cur.fetchone()[0]
            
            cur.execute('SELECT COUNT(*) FROM strategies WHERE is_active = 1')
            stats['active_strategies'] = cur.fetchone()[0]
            
            cur.execute('SELECT COUNT(*) FROM coin_insights')
            stats['coins_analyzed'] = cur.fetchone()[0]
            
            cur.execute('SELECT COUNT(*) FROM lessons_learned')
            stats['lessons_learned'] = cur.fetchone()[0]
            
            return stats
    
    def export_brain(self) -> Dict:
        """Экспорт всей памяти (для переноса)"""
        return {
            'conversations': self.get_recent_conversations(100),
            'commands': self.get_active_commands(),
            'rules': self.get_active_rules(),
            'strategies': self.get_all_strategies(),
            'coin_insights': self.get_best_coins(min_trades=1, min_win_rate=0),
            'lessons': self.get_lessons(limit=50),
            'state': self.get_all_state(),
            'exported_at': datetime.now().isoformat()
        }
    
    def import_brain(self, data: Dict) -> bool:
        """Импорт памяти из экспорта"""
        try:
            # Импортируем команды
            for cmd in data.get('commands', []):
                self.save_command(
                    cmd['command'], cmd.get('command_type', 'rule'),
                    cmd.get('target'), json.loads(cmd.get('parameters', '{}'))
                )
            
            # Импортируем правила
            for rule in data.get('rules', []):
                self.save_rule(
                    rule['name'], rule['condition'], rule['action'],
                    rule.get('description'), rule.get('priority', 5)
                )
            
            # Импортируем стратегии
            for strategy in data.get('strategies', []):
                self.save_strategy(
                    strategy['name'], strategy.get('parameters', {}),
                    strategy.get('description'), strategy.get('strategy_type', 'custom')
                )
            
            logger.info("[BRAIN] Import completed successfully")
            return True
        except Exception as e:
            logger.error(f"[BRAIN] Import error: {e}")
            return False
    
    # =========================================================================
    # CANDLES CACHE - Кэш свечей (до 1 месяца)
    # =========================================================================
    
    def save_candles(self, symbol: str, timeframe: str, candles: List[List]) -> int:
        """
        Сохранить свечи в кэш
        candles: [[timestamp, open, high, low, close, volume], ...]
        Возвращает количество сохранённых
        """
        if not candles:
            return 0
        
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                saved = 0
                for c in candles:
                    try:
                        cur.execute('''
                            INSERT OR REPLACE INTO candles_cache 
                            (symbol, timeframe, timestamp, open, high, low, close, volume, fetched_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            symbol.upper(),
                            timeframe,
                            int(c[0]),  # timestamp
                            float(c[1]),  # open
                            float(c[2]),  # high
                            float(c[3]),  # low
                            float(c[4]),  # close
                            float(c[5]),  # volume
                            datetime.now().isoformat()
                        ))
                        saved += 1
                    except Exception as e:
                        logger.debug(f"[BRAIN] Candle save error: {e}")
                        continue
                
                conn.commit()
                logger.info(f"[BRAIN] Saved {saved} candles for {symbol} {timeframe}")
                return saved
    
    def get_candles(self, symbol: str, timeframe: str, 
                   start_ts: int = None, end_ts: int = None,
                   limit: int = None) -> List[Dict]:
        """
        Получить свечи из кэша
        Возвращает: [{'timestamp': ..., 'open': ..., ...}, ...]
        """
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                
                query = '''
                    SELECT timestamp, open, high, low, close, volume, fetched_at
                    FROM candles_cache 
                    WHERE symbol = ? AND timeframe = ?
                '''
                params = [symbol.upper(), timeframe]
                
                if start_ts:
                    query += ' AND timestamp >= ?'
                    params.append(start_ts)
                
                if end_ts:
                    query += ' AND timestamp <= ?'
                    params.append(end_ts)
                
                query += ' ORDER BY timestamp ASC'
                
                if limit:
                    query += ' LIMIT ?'
                    params.append(limit)
                
                cur.execute(query, params)
                rows = cur.fetchall()
                
                return [{
                    'timestamp': r['timestamp'],
                    'open': r['open'],
                    'high': r['high'],
                    'low': r['low'],
                    'close': r['close'],
                    'volume': r['volume'],
                    'fetched_at': r['fetched_at']
                } for r in rows]
    
    def get_candles_range(self, symbol: str, timeframe: str) -> Dict:
        """Получить диапазон дат для кэшированных свечей"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts, COUNT(*) as count
                    FROM candles_cache 
                    WHERE symbol = ? AND timeframe = ?
                ''', (symbol.upper(), timeframe))
                row = cur.fetchone()
                
                if row and row['min_ts']:
                    return {
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'start': datetime.fromtimestamp(row['min_ts'] / 1000).isoformat(),
                        'end': datetime.fromtimestamp(row['max_ts'] / 1000).isoformat(),
                        'count': row['count']
                    }
                return {'symbol': symbol, 'timeframe': timeframe, 'count': 0}
    
    def cleanup_old_candles(self, days: int = 35):
        """Удалить свечи старше N дней (по умолчанию 35 дней = ~1 месяц + буфер)"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cutoff_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
                cur.execute('DELETE FROM candles_cache WHERE timestamp < ?', (cutoff_ts,))
                deleted = cur.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"[BRAIN] Cleaned up {deleted} old candles")
                return deleted
    
    def get_cached_symbols(self, timeframe: str = None) -> List[Dict]:
        """Получить список символов в кэше"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                
                if timeframe:
                    cur.execute('''
                        SELECT symbol, timeframe, COUNT(*) as candle_count,
                               MIN(timestamp) as start_ts, MAX(timestamp) as end_ts
                        FROM candles_cache 
                        WHERE timeframe = ?
                        GROUP BY symbol, timeframe
                    ''', (timeframe,))
                else:
                    cur.execute('''
                        SELECT symbol, timeframe, COUNT(*) as candle_count,
                               MIN(timestamp) as start_ts, MAX(timestamp) as end_ts
                        FROM candles_cache 
                        GROUP BY symbol, timeframe
                    ''')
                
                return [dict(r) for r in cur.fetchall()]
    
    # =========================================================================
    # STRATEGIES - Расширенные методы для стратегий
    # =========================================================================
    
    def save_strategy_full(self, name: str, parameters: Dict, 
                          description: str = None,
                          entry_conditions: Dict = None,
                          exit_conditions: Dict = None,
                          risk_params: Dict = None,
                          backtest_results: Dict = None,
                          is_active: bool = False) -> int:
        """Сохранить полную стратегию со всеми параметрами"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                
                # Проверяем существует ли
                cur.execute('SELECT id FROM strategies WHERE name = ?', (name,))
                existing = cur.fetchone()
                
                if existing:
                    # Обновляем
                    cur.execute('''
                        UPDATE strategies SET
                            parameters = ?,
                            description = ?,
                            entry_conditions = ?,
                            exit_conditions = ?,
                            risk_params = ?,
                            backtest_results = ?,
                            is_active = ?,
                            updated_at = ?
                        WHERE name = ?
                    ''', (
                        json.dumps(parameters),
                        description,
                        json.dumps(entry_conditions or {}),
                        json.dumps(exit_conditions or {}),
                        json.dumps(risk_params or {}),
                        json.dumps(backtest_results or {}),
                        1 if is_active else 0,
                        datetime.now().isoformat(),
                        name
                    ))
                    return existing['id']
                else:
                    # Создаём новую
                    cur.execute('''
                        INSERT INTO strategies 
                        (name, parameters, description, entry_conditions, exit_conditions,
                         risk_params, backtest_results, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        name,
                        json.dumps(parameters),
                        description,
                        json.dumps(entry_conditions or {}),
                        json.dumps(exit_conditions or {}),
                        json.dumps(risk_params or {}),
                        json.dumps(backtest_results or {}),
                        1 if is_active else 0,
                        datetime.now().isoformat(),
                        datetime.now().isoformat()
                    ))
                    return cur.lastrowid
    
    def get_strategy_by_name(self, name: str) -> Optional[Dict]:
        """Получить стратегию по имени"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT * FROM strategies WHERE name = ?', (name,))
                row = cur.fetchone()
                
                if row:
                    return {
                        'id': row['id'],
                        'name': row['name'],
                        'description': row['description'],
                        'parameters': json.loads(row['parameters'] or '{}'),
                        'entry_conditions': json.loads(row['entry_conditions'] or '{}'),
                        'exit_conditions': json.loads(row['exit_conditions'] or '{}'),
                        'risk_params': json.loads(row['risk_params'] or '{}'),
                        'backtest_results': json.loads(row['backtest_results'] or '{}'),
                        'is_active': bool(row['is_active']),
                        'win_rate': row['win_rate'],
                        'profit_factor': row['profit_factor'],
                        'total_trades': row['total_trades'],
                        'created_at': row['created_at'],
                        'updated_at': row['updated_at']
                    }
                return None
    
    def set_active_strategy(self, name: str) -> bool:
        """Установить стратегию как активную (деактивирует остальные)"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                # Сначала деактивируем все
                cur.execute('UPDATE strategies SET is_active = 0')
                # Активируем нужную
                cur.execute('UPDATE strategies SET is_active = 1 WHERE name = ?', (name,))
                conn.commit()
                return cur.rowcount > 0
    
    def get_active_strategy(self) -> Optional[Dict]:
        """Получить текущую активную стратегию"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT * FROM strategies WHERE is_active = 1 LIMIT 1')
                row = cur.fetchone()
                
                if row:
                    return {
                        'id': row['id'],
                        'name': row['name'],
                        'description': row['description'],
                        'parameters': json.loads(row['parameters'] or '{}'),
                        'entry_conditions': json.loads(row['entry_conditions'] or '{}'),
                        'exit_conditions': json.loads(row['exit_conditions'] or '{}'),
                        'risk_params': json.loads(row['risk_params'] or '{}'),
                        'backtest_results': json.loads(row['backtest_results'] or '{}'),
                        'win_rate': row['win_rate'],
                        'profit_factor': row['profit_factor'],
                        'created_at': row['created_at']
                    }
                return None
    
    def update_strategy_results(self, name: str, backtest_results: Dict = None,
                               live_results: Dict = None, 
                               win_rate: float = None,
                               profit_factor: float = None,
                               total_trades: int = None):
        """Обновить результаты стратегии"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                updates = []
                params = []
                
                if backtest_results is not None:
                    updates.append('backtest_results = ?')
                    params.append(json.dumps(backtest_results))
                if live_results is not None:
                    updates.append('live_results = ?')
                    params.append(json.dumps(live_results))
                if win_rate is not None:
                    updates.append('win_rate = ?')
                    params.append(win_rate)
                if profit_factor is not None:
                    updates.append('profit_factor = ?')
                    params.append(profit_factor)
                if total_trades is not None:
                    updates.append('total_trades = ?')
                    params.append(total_trades)
                
                if updates:
                    updates.append('updated_at = ?')
                    params.append(datetime.now().isoformat())
                    params.append(name)
                    
                    query = f"UPDATE strategies SET {', '.join(updates)} WHERE name = ?"
                    cur.execute(query, params)
                    conn.commit()
    
    def list_all_strategies(self) -> List[Dict]:
        """Получить список всех стратегий с кратким описанием"""
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT name, description, is_active, win_rate, profit_factor, 
                           total_trades, created_at, updated_at
                    FROM strategies 
                    ORDER BY is_active DESC, updated_at DESC
                ''')
                
                return [{
                    'name': r['name'],
                    'description': r['description'],
                    'is_active': bool(r['is_active']),
                    'win_rate': r['win_rate'],
                    'profit_factor': r['profit_factor'],
                    'total_trades': r['total_trades'],
                    'created_at': r['created_at'],
                    'updated_at': r['updated_at']
                } for r in cur.fetchall()]
    
    def delete_strategy(self, name: str) -> bool:
        """Удалить стратегию (кроме DEFAULT)"""
        if name.upper() == 'DEFAULT':
            return False
        
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('DELETE FROM strategies WHERE name = ?', (name,))
                conn.commit()
                return cur.rowcount > 0


# Глобальный экземпляр
brain = AgentBrain()
