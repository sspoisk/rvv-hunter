import sqlite3
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# Timezone offset for GMT+2
TZ_OFFSET = timedelta(hours=2)

def get_gmt2_time():
    return datetime.utcnow() + TZ_OFFSET

def get_gmt2_str():
    return get_gmt2_time().strftime('%Y-%m-%d %H:%M:%S')

class Database:
    """Основной класс для работы с SQLite базой данных"""
    def __init__(self, db_path: str = "data/rvv_hunter.db"):
        self.db_path = db_path
        self.local = threading.local()
        self._ensure_dir()
        self._init_db()
        logger.info(f"[DB] Initialized: {db_path}")
    
    def _ensure_dir(self):
        """Создаём директорию если нет"""
        dir_path = os.path.dirname(self.db_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)
    
    def _get_conn(self) -> sqlite3.Connection:
        """Получить connection для текущего потока"""
        if not hasattr(self.local, 'conn') or self.local.conn is None:
            self.local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.local.conn.row_factory = sqlite3.Row
        return self.local.conn
    
    @contextmanager
    def get_cursor(self):
        """Context manager для курсора"""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[DB] Error: {e}")
            raise
        finally:
            cursor.close()
    
    def _init_db(self):
        """Инициализация таблиц"""
        with self.get_cursor() as cur:
            # 1. Журнал сделок с полной поддержкой LONG/SHORT
            cur.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE,
                symbol TEXT NOT NULL,
                side TEXT DEFAULT 'SHORT',
                opened_at TEXT,
                closed_at TEXT,
                day_of_week INTEGER,
                hour_opened INTEGER,
                entry_price REAL,
                exit_price REAL,
                stop_loss REAL,
                trailing_stop REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                result TEXT,
                pnl_usdt REAL DEFAULT 0,
                pnl_percent REAL DEFAULT 0,
                close_reason TEXT,
                ai_confidence INTEGER DEFAULT 0,
                ai_reason TEXT,
                ai_analysis_ru TEXT,
                ai_provider TEXT DEFAULT 'deepseek',
                change_24h REAL DEFAULT 0,
                volume_24h REAL DEFAULT 0,
                atr_percent REAL DEFAULT 0,
                position_size REAL,
                leverage INTEGER DEFAULT 5,
                duration_minutes INTEGER DEFAULT 0,
                trade_mode TEXT DEFAULT 'PAPER',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 2. История рынка (пампы и дампы)
            cur.execute('''
            CREATE TABLE IF NOT EXISTS market_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                change_24h REAL,
                price REAL,
                volume REAL,
                reversal_1h REAL,
                reversal_4h REAL,
                reversal_24h REAL,
                max_drawdown REAL,
                max_profit REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timestamp)
            )
            ''')
            
            # 3. Рекомендации AI
            cur.execute('''
            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                parameter TEXT NOT NULL,
                current_value REAL,
                suggested_value REAL,
                reasoning TEXT,
                sample_size INTEGER DEFAULT 0,
                expected_improvement REAL DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                applied_at TEXT,
                applied_by TEXT
            )
            ''')
            
            # 4. История изменений параметров
            cur.execute('''
            CREATE TABLE IF NOT EXISTS settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                changed_at TEXT,
                parameter TEXT NOT NULL,
                old_value REAL,
                new_value REAL,
                change_source TEXT,
                recommendation_id INTEGER,
                FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
            )
            ''')
            
            # 5. Настройки (замена JSON файла)
            cur.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            ''')
            
            # 6. Логи активности
            cur.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                log_type TEXT,
                message TEXT,
                data TEXT
            )
            ''')
            
            # 7. Черный список монет
            cur.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE NOT NULL,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                reason TEXT,
                added_by TEXT DEFAULT 'MANUAL',
                enabled BOOLEAN DEFAULT 1
            )
            ''')
            
            # 8. Адаптивный трейлинг по символам
            cur.execute('''
            CREATE TABLE IF NOT EXISTS symbol_trailing_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE NOT NULL,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                avg_atr_percent REAL DEFAULT 0,
                avg_pump_percent REAL DEFAULT 0,
                avg_reversal_1h REAL DEFAULT 0,
                avg_reversal_4h REAL DEFAULT 0,
                activation_pct REAL DEFAULT 1.0,
                distance_pct REAL DEFAULT 1.5,
                mode TEXT DEFAULT 'auto',
                enabled BOOLEAN DEFAULT 1,
                last_updated TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 9. Пост-мортем убыточных сделок
            cur.execute('''
            CREATE TABLE IF NOT EXISTS post_mortem (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                loss_amount REAL,
                loss_percent REAL,
                hour_opened INTEGER,
                day_of_week INTEGER,
                atr_at_entry REAL,
                trailing_distance_used REAL,
                continued_pump_percent REAL,
                analysis TEXT,
                recommendations TEXT,
                user_action TEXT,
                action_taken_at TEXT,
                side TEXT DEFAULT 'SHORT',
                rsi_at_entry REAL DEFAULT 50,
                bollinger_b_at_entry REAL DEFAULT 50,
                macd_divergence_at_entry TEXT DEFAULT 'none',
                confidence_at_entry INTEGER DEFAULT 0,
                btc_trend_at_entry TEXT DEFAULT 'neutral',
                btc_strength_at_entry TEXT DEFAULT 'weak',
                problem_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
            )
            ''')
            
            # 10. A/B тестирование AI провайдеров
            cur.execute('''
            CREATE TABLE IF NOT EXISTS ai_ab_test (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                deepseek_action TEXT,
                deepseek_confidence INTEGER,
                deepseek_response_time REAL,
                groq_action TEXT,
                groq_confidence INTEGER,
                groq_response_time REAL,
                consensus BOOLEAN,
                chosen_provider TEXT,
                trade_opened BOOLEAN DEFAULT 0,
                trade_result TEXT,
                trade_pnl REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 11. Статус здоровья системы
            cur.execute('''
            CREATE TABLE IF NOT EXISTS health_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                binance_status TEXT,
                binance_ping_ms INTEGER,
                deepseek_status TEXT,
                groq_status TEXT,
                telegram_status TEXT,
                daily_pnl REAL,
                daily_pnl_limit_pct REAL,
                win_rate_today REAL,
                trades_today INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 12. История анализов AI
            cur.execute('''
            CREATE TABLE IF NOT EXISTS ai_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT NOT NULL,
                ai_provider TEXT,
                action TEXT,
                confidence REAL,
                entry_price REAL,
                sl_original REAL,
                sl_corrected REAL,
                sl_was_fixed INTEGER DEFAULT 0,
                tp1 REAL,
                tp2 REAL,
                analysis_text TEXT,
                change_24h REAL,
                atr_percent REAL,
                trade_opened INTEGER DEFAULT 0,
                trade_id TEXT,
                side TEXT DEFAULT 'SHORT',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 13. История хода сделки (цена каждые 5 минут)
            cur.execute('''
            CREATE TABLE IF NOT EXISTS trade_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                timestamp TEXT,
                event_type TEXT,
                price REAL,
                pnl_percent REAL,
                trailing_stop REAL,
                details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 14. Кастомный промпт AI
            cur.execute('''
            CREATE TABLE IF NOT EXISTS ai_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                prompt_text TEXT,
                is_active INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
            ''')
            
            # 15. Статистика по направлениям (LONG/SHORT)
            cur.execute('''
            CREATE TABLE IF NOT EXISTS direction_statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                side TEXT NOT NULL,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                avg_profit REAL DEFAULT 0,
                avg_loss REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 16. История тренда Bitcoin
            cur.execute('''
            CREATE TABLE IF NOT EXISTS btc_trend_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                trend TEXT NOT NULL,
                strength TEXT NOT NULL,
                rsi_1h REAL,
                change_24h REAL,
                confidence INTEGER,
                signal_impact_short REAL,
                signal_impact_long REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # ═══════════════════════════════════════════════════════════════
            # AGENT V3 TABLES - Полная автономность агента
            # ═══════════════════════════════════════════════════════════════
            
            # 17. Память агента о монетах (персистентная)
            cur.execute('''
            CREATE TABLE IF NOT EXISTS agent_coin_memory (
                symbol TEXT PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                avg_pnl REAL DEFAULT 0,
                avg_hold_minutes REAL DEFAULT 0,
                best_trade_pnl REAL DEFAULT 0,
                worst_trade_pnl REAL DEFAULT 0,
                win_streak INTEGER DEFAULT 0,
                loss_streak INTEGER DEFAULT 0,
                current_streak INTEGER DEFAULT 0,
                last_trade_at TEXT,
                last_result TEXT,
                blacklisted INTEGER DEFAULT 0,
                blacklist_reason TEXT,
                blacklist_until TEXT,
                ai_notes TEXT,
                preferred_side TEXT,
                avg_win_pnl REAL DEFAULT 0,
                avg_loss_pnl REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 18. История решений агента
            cur.execute('''
            CREATE TABLE IF NOT EXISTS agent_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                trade_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT,
                action TEXT NOT NULL,
                action_params TEXT,
                reason TEXT,
                reasoning TEXT,
                trigger TEXT,
                pnl_before REAL,
                pnl_after REAL,
                was_correct INTEGER,
                market_context TEXT,
                btc_price REAL,
                btc_trend TEXT,
                tools_used TEXT,
                ai_provider TEXT,
                execution_time_ms INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 19. Уроки агента (что выучил на ошибках)
            cur.execute('''
            CREATE TABLE IF NOT EXISTS agent_lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson TEXT NOT NULL,
                category TEXT,
                symbol TEXT,
                confidence REAL DEFAULT 0.5,
                times_applied INTEGER DEFAULT 0,
                times_correct INTEGER DEFAULT 0,
                last_applied_at TEXT,
                source_decision_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_decision_id) REFERENCES agent_decisions(id)
            )
            ''')
            
            # 20. Состояние агента (для восстановления после рестарта)
            cur.execute('''
            CREATE TABLE IF NOT EXISTS agent_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # ========== STRATEGIES TABLE v5.6 ==========
            cur.execute('''
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                -- Параметры торговли
                stop_loss_pct REAL DEFAULT 5.0,
                take_profit_pct REAL DEFAULT 7.0,
                trailing_activation_pct REAL DEFAULT 2.0,
                trailing_distance_pct REAL DEFAULT 1.0,
                trailing_enabled INTEGER DEFAULT 1,
                -- AI
                ai_provider TEXT DEFAULT 'mock',
                min_confidence INTEGER DEFAULT 70,
                -- Фильтры RSI
                rsi_short_min REAL DEFAULT 70,
                rsi_long_max REAL DEFAULT 30,
                -- Статус
                is_active INTEGER DEFAULT 0,
                -- Результаты бэктеста
                backtest_trades INTEGER,
                backtest_win_rate REAL,
                backtest_pnl REAL,
                backtest_max_drawdown REAL,
                backtest_date TEXT,
                -- Мета
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
            ''')
            
            # Индексы для оптимизации запросов
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trades_result ON trades(result)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trades_mode ON trades(trade_mode)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trades_side ON trades(side)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_market_symbol ON market_history(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_market_timestamp ON market_history(timestamp)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_market_change ON market_history(change_24h)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_recommendations_created ON recommendations(created_at)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_blacklist_symbol ON blacklist(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_blacklist_enabled ON blacklist(enabled)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_symbol_trailing ON symbol_trailing_config(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_symbol_stats ON symbol_trailing_config(total_trades, wins)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_post_mortem_trade ON post_mortem(trade_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_post_mortem_created ON post_mortem(created_at)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_ab_test_symbol ON ai_ab_test(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_ab_test_created ON ai_ab_test(created_at)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_ab_test_provider ON ai_ab_test(chosen_provider)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_analyses_symbol ON ai_analyses(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_analyses_timestamp ON ai_analyses(timestamp)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_analyses_provider ON ai_analyses(ai_provider)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_analyses_confidence ON ai_analyses(confidence)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trade_price_history_trade ON trade_price_history(trade_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trade_price_history_timestamp ON trade_price_history(timestamp)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trade_price_history_type ON trade_price_history(event_type)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_direction_stats_date ON direction_statistics(date)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_direction_stats_side ON direction_statistics(side)')
            
            cur.execute('CREATE INDEX IF NOT EXISTS idx_btc_trend_timestamp ON btc_trend_history(timestamp)')
            
            # Agent V3 индексы
            cur.execute('CREATE INDEX IF NOT EXISTS idx_agent_decisions_symbol ON agent_decisions(symbol)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_agent_decisions_timestamp ON agent_decisions(timestamp)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_agent_decisions_action ON agent_decisions(action)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_agent_decisions_trade ON agent_decisions(trade_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_agent_lessons_category ON agent_lessons(category)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_agent_lessons_symbol ON agent_lessons(symbol)')
            
            # Миграции для существующих таблиц
            self._run_migrations(cur)
            logger.info("[DB] Tables initialized")
    
    def _run_migrations(self, cur):
        """Запуск миграций для обновления существующих таблиц"""
        try:
            # 1. Проверяем и добавляем недостающие колонки в таблицу trades
            cur.execute("PRAGMA table_info(trades)")
            columns = [col[1] for col in cur.fetchall()]
            
            # Список необходимых колонок с их типами
            required_columns = {
                'side': 'TEXT DEFAULT "SHORT"',
                'trailing_stop': 'REAL',
                'ai_provider': 'TEXT DEFAULT "deepseek"',
                'atr_percent': 'REAL DEFAULT 0',
                'trade_mode': 'TEXT DEFAULT "PAPER"',
                'volume_24h': 'REAL DEFAULT 0',
                'change_24h_at_open': 'REAL DEFAULT 0'
            }
            
            for col_name, col_type in required_columns.items():
                if col_name not in columns:
                    cur.execute(f'ALTER TABLE trades ADD COLUMN {col_name} {col_type}')
                    logger.info(f"[DB] Migration: added {col_name} column to trades")
            
            # 2. Добавляем индекс для колонки side
            cur.execute("PRAGMA index_list('trades')")
            indexes = [idx[1] for idx in cur.fetchall()]
            if 'idx_trades_side' not in indexes:
                cur.execute('CREATE INDEX IF NOT EXISTS idx_trades_side ON trades(side)')
                logger.info("[DB] Migration: added index idx_trades_side")
            
            # 3. Создаем таблицу direction_statistics если её нет
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='direction_statistics'")
            if not cur.fetchone():
                cur.execute('''
                CREATE TABLE direction_statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    side TEXT NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    avg_profit REAL DEFAULT 0,
                    avg_loss REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                cur.execute('CREATE INDEX IF NOT EXISTS idx_direction_stats_date ON direction_statistics(date)')
                cur.execute('CREATE INDEX IF NOT EXISTS idx_direction_stats_side ON direction_statistics(side)')
                logger.info("[DB] Migration: created table direction_statistics")
            
            # 4. Создаем таблицу btc_trend_history если её нет
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='btc_trend_history'")
            if not cur.fetchone():
                cur.execute('''
                CREATE TABLE btc_trend_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    trend TEXT NOT NULL,
                    strength TEXT NOT NULL,
                    rsi_1h REAL,
                    change_24h REAL,
                    confidence INTEGER,
                    signal_impact_short REAL,
                    signal_impact_long REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                cur.execute('CREATE INDEX IF NOT EXISTS idx_btc_trend_timestamp ON btc_trend_history(timestamp)')
                logger.info("[DB] Migration: created table btc_trend_history")
            
            # 5. Добавляем колонку side в ai_analyses если её нет
            cur.execute("PRAGMA table_info(ai_analyses)")
            ai_columns = [col[1] for col in cur.fetchall()]
            if 'side' not in ai_columns:
                cur.execute('ALTER TABLE ai_analyses ADD COLUMN side TEXT DEFAULT "SHORT"')
                logger.info("[DB] Migration: added side column to ai_analyses")
            
            # 6. Добавляем колонки индикаторов в post_mortem если их нет
            cur.execute("PRAGMA table_info(post_mortem)")
            pm_columns = [col[1] for col in cur.fetchall()]
            pm_new_columns = {
                'side': 'TEXT DEFAULT "SHORT"',
                'rsi_at_entry': 'REAL DEFAULT 50',
                'bollinger_b_at_entry': 'REAL DEFAULT 50',
                'macd_divergence_at_entry': 'TEXT DEFAULT "none"',
                'confidence_at_entry': 'INTEGER DEFAULT 0',
                'btc_trend_at_entry': 'TEXT DEFAULT "neutral"',
                'btc_strength_at_entry': 'TEXT DEFAULT "weak"',
                'problem_count': 'INTEGER DEFAULT 0'
            }
            for col_name, col_type in pm_new_columns.items():
                if col_name not in pm_columns:
                    cur.execute(f'ALTER TABLE post_mortem ADD COLUMN {col_name} {col_type}')
                    logger.info(f"[DB] Migration: added {col_name} column to post_mortem")
            
            # 7. Обновляем индексы для оптимизации запросов
            index_updates = [
                ('idx_trades_created', 'trades(created_at)'),
                ('idx_ai_ab_test_provider', 'ai_ab_test(chosen_provider)'),
                ('idx_ai_analyses_confidence', 'ai_analyses(confidence)'),
                ('idx_ai_analyses_side', 'ai_analyses(side)'),
                ('idx_trade_price_history_type', 'trade_price_history(event_type)'),
                ('idx_post_mortem_side', 'post_mortem(side)')
            ]
            
            for index_name, table_column in index_updates:
                cur.execute(f"SELECT name FROM sqlite_master WHERE type='index' AND name='{index_name}'")
                if not cur.fetchone():
                    cur.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON {table_column}')
                    logger.info(f"[DB] Migration: added index {index_name}")
            
            # 8. Фиксируем все изменения
            logger.info("[DB] Migrations completed successfully")
            
        except Exception as e:
            logger.error(f"[DB] Migration error: {e}")
    
    # =========================================================================
    # TRADES - Журнал сделок с поддержкой LONG/SHORT
    # =========================================================================
    
    def save_trade_open(self, trade_data: Dict) -> int:
        """Записать открытие сделки с поддержкой LONG/SHORT"""
        now = get_gmt2_time()
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO trades (
                trade_id, symbol, side,
                opened_at, day_of_week, hour_opened,
                entry_price, stop_loss, trailing_stop, take_profit_1, take_profit_2,
                ai_confidence, ai_reason, ai_analysis_ru, ai_provider,
                change_24h, change_24h_at_open, atr_percent, position_size, leverage, trade_mode,
                volume_24h
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_data.get('trade_id'),
                trade_data.get('symbol'),
                trade_data.get('side', 'SHORT'),
                now.strftime('%Y-%m-%d %H:%M:%S'),
                now.weekday(),
                now.hour,
                trade_data.get('entry_price'),
                trade_data.get('stop_loss'),
                trade_data.get('trailing_stop', trade_data.get('stop_loss')),
                trade_data.get('take_profit_1'),
                trade_data.get('take_profit_2'),
                trade_data.get('ai_confidence', 0),
                trade_data.get('ai_reason', ''),
                trade_data.get('ai_analysis_ru', ''),
                trade_data.get('ai_provider', 'deepseek'),
                trade_data.get('change_24h', 0),
                trade_data.get('change_24h_at_open', 0),
                trade_data.get('atr_percent', 0),
                trade_data.get('position_size'),
                trade_data.get('leverage', 5),
                trade_data.get('trade_mode', 'PAPER'),
                trade_data.get('volume_24h', 0)
            ))
            trade_id = cur.lastrowid
            
            # Обновляем статистику по направлениям
            self._update_direction_statistics(trade_data.get('side', 'SHORT'))
            
            return trade_id
    
    def save_trade_close(self, trade_id: str, close_data: Dict) -> bool:
        """Записать закрытие сделки с поддержкой LONG/SHORT"""
        now = get_gmt2_time()
        with self.get_cursor() as cur:
            cur.execute('SELECT opened_at, symbol, side FROM trades WHERE trade_id = ?', (trade_id,))
            row = cur.fetchone()
            if not row:
                return False
            
            opened_at = datetime.strptime(row['opened_at'], '%Y-%m-%d %H:%M:%S')
            duration = int((now - opened_at).total_seconds() / 60)
            pnl = close_data.get('pnl_usdt', 0)
            
            if pnl > 0:
                result = 'WIN'
            elif pnl < 0:
                result = 'LOSS'
            else:
                result = 'BREAKEVEN'
            
            cur.execute('''
            UPDATE trades SET
                closed_at = ?,
                exit_price = ?,
                result = ?,
                pnl_usdt = ?,
                pnl_percent = ?,
                close_reason = ?,
                duration_minutes = ?
            WHERE trade_id = ?
            ''', (
                now.strftime('%Y-%m-%d %H:%M:%S'),
                close_data.get('exit_price'),
                result,
                pnl,
                close_data.get('pnl_percent', 0),
                close_data.get('close_reason', 'UNKNOWN'),
                duration,
                trade_id
            ))
            
            # Обновляем статистику символа
            self._update_symbol_stats(row['symbol'], result, close_data)
            
            # Обновляем статистику по направлениям
            self._update_direction_statistics(row['side'], result, pnl)
            
            return True
    
    def get_last_trade_counter(self) -> int:
        """Получить последний номер сделки из БД для корректного продолжения нумерации"""
        with self.get_cursor() as cur:
            cur.execute("SELECT trade_id FROM trades WHERE trade_id LIKE 'RVV-%' ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                try:
                    return int(row['trade_id'].replace('RVV-', ''))
                except Exception:
                    pass
            return 0
    
    def get_trades(self, limit: int = 100, only_closed: bool = False,
                  trade_mode: str = None, side: str = None, 
                  days: int = None, symbol: str = None) -> List[Dict]:
        """Получить сделки с фильтрацией"""
        with self.get_cursor() as cur:
            query = 'SELECT * FROM trades WHERE 1=1'
            params = []
            
            if only_closed:
                query += ' AND result IS NOT NULL'
            
            if trade_mode:
                query += ' AND trade_mode = ?'
                params.append(trade_mode)
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            if days:
                # Фильтр по дате (за последние N дней)
                from datetime import datetime, timedelta
                cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
                query += ' AND opened_at >= ?'
                params.append(cutoff)
            
            if symbol:
                # Фильтр по символу
                clean_symbol = symbol.upper().replace('/USDT', '').replace(':USDT', '')
                query += ' AND symbol LIKE ?'
                params.append(f'%{clean_symbol}%')
            
            query += ' ORDER BY opened_at DESC'
            
            if limit:
                query += ' LIMIT ?'
                params.append(limit)
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_open_trades(self, side: str = None) -> List[Dict]:
        """Получить открытые сделки с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = 'SELECT * FROM trades WHERE result IS NULL'
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    # =========================================================================
    # DIRECTION STATISTICS - Статистика по направлениям LONG/SHORT
    # =========================================================================
    
    def _update_direction_statistics(self, side: str, result: str = None, pnl: float = 0):
        """Обновить статистику по направлениям"""
        today = get_gmt2_time().strftime('%Y-%m-%d')
        
        with self.get_cursor() as cur:
            # Проверяем есть ли запись за сегодня
            cur.execute('''
            SELECT * FROM direction_statistics
            WHERE date = ? AND side = ?
            ''', (today, side.upper()))
            
            row = cur.fetchone()
            
            if row:
                # Обновляем существующую запись
                updates = ['total_trades = total_trades + 1']
                if result == 'WIN':
                    updates.append('wins = wins + 1')
                if pnl != 0:
                    updates.append(f'total_pnl = total_pnl + {pnl}')
                    if pnl > 0:
                        updates.append(f'avg_profit = (avg_profit * (total_trades - 1) + {pnl}) / total_trades')
                    else:
                        updates.append(f'avg_loss = (avg_loss * (total_trades - 1) + {abs(pnl)}) / total_trades')
                
                cur.execute(f'''
                UPDATE direction_statistics
                SET {', '.join(updates)}
                WHERE date = ? AND side = ?
                ''', (today, side.upper()))
            else:
                # Создаем новую запись
                cur.execute('''
                INSERT INTO direction_statistics (
                    date, side, total_trades, wins, total_pnl, avg_profit, avg_loss
                ) VALUES (?, ?, 1, ?, ?, ?, ?)
                ''', (
                    today,
                    side.upper(),
                    1 if result == 'WIN' else 0,
                    pnl,
                    pnl if pnl > 0 else 0,
                    abs(pnl) if pnl < 0 else 0
                ))
    
    def get_direction_statistics(self, days: int = 30) -> Dict:
        """Получить статистику по направлениям за последние N дней"""
        with self.get_cursor() as cur:
            # Получаем данные за последние N дней
            start_date = (get_gmt2_time() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            cur.execute('''
            SELECT 
                side,
                SUM(total_trades) as total_trades,
                SUM(wins) as total_wins,
                SUM(total_pnl) as total_pnl,
                AVG(avg_profit) as avg_profit,
                AVG(avg_loss) as avg_loss,
                COUNT(*) as days_count
            FROM direction_statistics
            WHERE date >= ?
            GROUP BY side
            ''', (start_date,))
            
            results = {}
            for row in cur.fetchall():
                side = row['side'].upper()
                total_trades = row['total_trades'] or 0
                total_wins = row['total_wins'] or 0
                total_pnl = row['total_pnl'] or 0
                avg_profit = row['avg_profit'] or 0
                avg_loss = row['avg_loss'] or 0
                
                win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
                
                results[side] = {
                    'total_trades': total_trades,
                    'win_rate': win_rate,
                    'total_pnl': total_pnl,
                    'avg_profit': avg_profit,
                    'avg_loss': avg_loss,
                    'profit_factor': (total_wins * avg_profit) / (max(1, total_trades - total_wins) * avg_loss) if avg_loss > 0 else 0
                }
            
            return results
    
    def get_daily_direction_stats(self, date: str = None) -> Dict:
        """Получить статистику по направлениям за конкретный день"""
        if not date:
            date = get_gmt2_time().strftime('%Y-%m-%d')
        
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT side, total_trades, wins, total_pnl, avg_profit, avg_loss
            FROM direction_statistics
            WHERE date = ?
            ''', (date,))
            
            results = {}
            for row in cur.fetchall():
                side = row['side'].upper()
                results[side] = {
                    'total_trades': row['total_trades'],
                    'wins': row['wins'],
                    'win_rate': (row['wins'] / row['total_trades'] * 100) if row['total_trades'] > 0 else 0,
                    'total_pnl': row['total_pnl'],
                    'avg_profit': row['avg_profit'],
                    'avg_loss': row['avg_loss']
                }
            
            return results
    
    # =========================================================================
    # ADAPTIVE TRAILING - Адаптивный трейлинг для LONG/SHORT
    # =========================================================================
    
    def get_symbol_trailing_config(self, symbol: str) -> Optional[Dict]:
        """Получить конфиг трейлинга для символа"""
        clean_symbol = self._clean_symbol(symbol)
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT * FROM symbol_trailing_config WHERE symbol = ?
            ''', (clean_symbol,))
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_all_trailing_configs(self) -> List[Dict]:
        """Получить все конфиги трейлинга"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT * FROM symbol_trailing_config
            ORDER BY total_trades DESC
            ''')
            return [dict(row) for row in cur.fetchall()]
    
    def save_symbol_trailing_config(self, config: Dict) -> bool:
        """Сохранить/обновить конфиг трейлинга"""
        clean_symbol = self._clean_symbol(config.get('symbol', ''))
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT OR REPLACE INTO symbol_trailing_config (
                symbol, total_trades, wins, losses,
                avg_atr_percent, avg_pump_percent, avg_reversal_1h, avg_reversal_4h,
                activation_pct, distance_pct, mode, enabled, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                clean_symbol,
                config.get('total_trades', 0),
                config.get('wins', 0),
                config.get('losses', 0),
                config.get('avg_atr_percent', 0),
                config.get('avg_pump_percent', 0),
                config.get('avg_reversal_1h', 0),
                config.get('avg_reversal_4h', 0),
                config.get('activation_pct', 1.0),
                config.get('distance_pct', 1.5),
                config.get('mode', 'auto'),
                config.get('enabled', True),
                get_gmt2_str()
            ))
            return True
    
    def update_symbol_trailing_mode(self, symbol: str, mode: str, enabled: bool = True) -> bool:
        """Обновить режим трейлинга для символа"""
        clean_symbol = self._clean_symbol(symbol)
        with self.get_cursor() as cur:
            cur.execute('''
            UPDATE symbol_trailing_config
            SET mode = ?, enabled = ?, last_updated = ?
            WHERE symbol = ?
            ''', (mode, enabled, get_gmt2_str(), clean_symbol))
            return cur.rowcount > 0
    
    def _update_symbol_stats(self, symbol: str, result: str, close_data: Dict):
        """Обновить статистику символа после закрытия сделки"""
        clean_symbol = self._clean_symbol(symbol)
        with self.get_cursor() as cur:
            # Получаем текущий конфиг
            cur.execute('SELECT * FROM symbol_trailing_config WHERE symbol = ?', (clean_symbol,))
            row = cur.fetchone()
            if row:
                config = dict(row)
                config['total_trades'] = config.get('total_trades', 0) + 1
                if result == 'WIN':
                    config['wins'] = config.get('wins', 0) + 1
                elif result == 'LOSS':
                    config['losses'] = config.get('losses', 0) + 1
                else:
                    config = {
                        'symbol': clean_symbol,
                        'total_trades': 1,
                        'wins': 1 if result == 'WIN' else 0,
                        'losses': 1 if result == 'LOSS' else 0,
                        'avg_atr_percent': close_data.get('atr_percent', 0),
                        'avg_pump_percent': close_data.get('change_24h', 0),
                    }
                # Пересчитываем средние
                if config['total_trades'] >= 3:
                    config = self._recalculate_symbol_params(clean_symbol, config)
                self.save_symbol_trailing_config(config)
    
    def _recalculate_symbol_params(self, symbol: str, config: Dict) -> Dict:
        """Пересчитать оптимальные параметры для символа"""
        with self.get_cursor() as cur:
            # Получаем последние сделки по символу
            cur.execute('''
            SELECT atr_percent, change_24h, pnl_percent, close_reason, side
            FROM trades
            WHERE symbol LIKE ? AND result IS NOT NULL
            ORDER BY closed_at DESC LIMIT 20
            ''', (f'%{symbol}%',))
            rows = cur.fetchall()
            if not rows:
                return config
            
            # Считаем средние
            atr_values = [r['atr_percent'] for r in rows if r['atr_percent']]
            pump_values = [r['change_24h'] for r in rows if r['change_24h']]
            
            if atr_values:
                config['avg_atr_percent'] = sum(atr_values) / len(atr_values)
            if pump_values:
                config['avg_pump_percent'] = sum(pump_values) / len(pump_values)
            
            # Если режим auto - рассчитываем оптимальные параметры
            if config.get('mode') == 'auto':
                avg_atr = config.get('avg_atr_percent', 2.0)
                win_rate = config['wins'] / max(config['total_trades'], 1) * 100
                
                # Базовая формула: distance = ATR × множитель
                # Множитель зависит от win rate и направления
                short_trades = [r for r in rows if r['side'] == 'SHORT']
                long_trades = [r for r in rows if r['side'] == 'LONG']
                
                if len(short_trades) >= 3 and len(long_trades) >= 3:
                    # Разные параметры для SHORT и LONG
                    short_win_rate = len([t for t in short_trades if t['pnl_percent'] > 0]) / len(short_trades) * 100
                    long_win_rate = len([t for t in long_trades if t['pnl_percent'] > 0]) / len(long_trades) * 100
                    
                    if short_win_rate >= 80 and long_win_rate >= 80:
                        multiplier = 0.8  # Можно уже
                    elif short_win_rate >= 60 or long_win_rate >= 60:
                        multiplier = 1.0  # Стандартно
                    else:
                        multiplier = 1.3  # Шире для защиты
                else:
                    # Общие параметры
                    if win_rate >= 80:
                        multiplier = 0.8  # Можно уже
                    elif win_rate >= 60:
                        multiplier = 1.0  # Стандартно
                    else:
                        multiplier = 1.3  # Шире для защиты
                
                config['distance_pct'] = max(0.8, min(3.0, avg_atr * multiplier))
                config['activation_pct'] = max(0.5, min(1.5, avg_atr * 0.4))
            
            return config
    
    def calculate_adaptive_trailing(self, symbol: str, current_atr: float = None, side: str = 'SHORT') -> Dict:
        """
        Рассчитать адаптивные параметры трейлинга для символа с учетом направления
        """
        config = self.get_symbol_trailing_config(symbol)
        if not config or not config.get('enabled'):
            # Если нет конфига но есть ATR - используем ATR
            if current_atr and current_atr > 0:
                # Для SHORT: Трейлинг = 1.0-1.2x ATR (не меньше ATR!)
                # Для LONG: Трейлинг = 0.8-1.0x ATR (более агрессивный)
                if side.upper() == 'SHORT':
                    distance = current_atr * 1.1
                    distance = max(1.0, min(8.0, distance))  # Лимиты: 1% - 8%
                    activation = current_atr * 0.5  # Активация на 50% ATR прибыли
                else:  # LONG
                    distance = current_atr * 0.9
                    distance = max(0.8, min(6.0, distance))  # Лимиты для LONG: 0.8% - 6%
                    activation = current_atr * 0.4  # Активация раньше для LONG
                
                activation = max(0.3, min(3.0, activation))
                return {
                    'activation_pct': activation,
                    'distance_pct': distance,
                    'mode': 'atr_based',
                    'learned': False,
                    'based_on_atr': current_atr,
                    'formula': f'ATR({current_atr:.2f}%) * {1.1 if side.upper() == "SHORT" else 0.9} = {distance:.2f}% (для {side})'
                }
            # Дефолтные значения в зависимости от направления
            return {
                'activation_pct': 1.0 if side.upper() == 'SHORT' else 0.8,
                'distance_pct': 1.5 if side.upper() == 'SHORT' else 1.2,
                'mode': 'default',
                'learned': False
            }
        
        # Если есть текущий ATR - используем его для динамической подстройки
        if current_atr and current_atr > 0 and config.get('mode') == 'auto':
            avg_atr = config.get('avg_atr_percent', 2.0)
            win_rate = config['wins'] / max(config['total_trades'], 1) * 100
            
            # Разные параметры для SHORT и LONG
            if side.upper() == 'SHORT':
                # Для SHORT: чем выше волатильность - тем шире трейлинг
                if current_atr > 3.0:
                    multiplier = 1.2  # Очень волатильная - трейлинг = 120% ATR
                elif current_atr > 2.0:
                    multiplier = 1.1  # Волатильная - трейлинг = 110% ATR
                else:
                    multiplier = 1.0  # Нормальная - трейлинг = 100% ATR
                
                distance = current_atr * multiplier
                distance = max(1.0, min(8.0, distance))  # Лимиты увеличены: 1% - 8%
                
                # Активация раньше для волатильных монет
                activation = current_atr * 0.4
                activation = max(0.5, min(2.5, activation))
            else:  # LONG
                # Для LONG: более агрессивный трейлинг (ближе к цене)
                if current_atr > 3.0:
                    multiplier = 0.9  # Очень волатильная - трейлинг = 90% ATR
                elif current_atr > 2.0:
                    multiplier = 0.85  # Волатильная - трейлинг = 85% ATR
                else:
                    multiplier = 0.8  # Нормальная - трейлинг = 80% ATR
                
                distance = current_atr * multiplier
                distance = max(0.8, min(6.0, distance))  # Лимиты для LONG: 0.8% - 6%
                
                # Активация раньше для LONG позиций
                activation = current_atr * 0.3
                activation = max(0.3, min(2.0, activation))
            
            return {
                'activation_pct': activation,
                'distance_pct': distance,
                'mode': 'adaptive',
                'learned': config['total_trades'] >= 3,
                'based_on_atr': current_atr,
                'multiplier': multiplier,
                'formula': f'ATR({current_atr:.2f}%) * {multiplier:.2f} = {distance:.2f}% (для {side})',
                'symbol_stats': {
                    'total_trades': config['total_trades'],
                    'win_rate': win_rate,
                    'avg_atr': avg_atr
                }
            }
        
        # Используем сохраненные параметры с учетом направления
        base_activation = config.get('activation_pct', 1.0)
        base_distance = config.get('distance_pct', 1.5)
        
        if side.upper() == 'LONG':
            # Для LONG используем более агрессивные параметры
            activation = max(0.3, base_activation * 0.8)
            distance = max(0.5, base_distance * 0.8)
        else:
            activation = base_activation
            distance = base_distance
        
        return {
            'activation_pct': activation,
            'distance_pct': distance,
            'mode': config.get('mode', 'auto'),
            'learned': config['total_trades'] >= 3,
            'symbol_stats': {
                'total_trades': config['total_trades'],
                'win_rate': config['wins'] / max(config['total_trades'], 1) * 100
            }
        }
    
    # =========================================================================
    # POST-MORTEM - Анализ убытков для LONG/SHORT
    # =========================================================================
    
    def save_post_mortem(self, data: Dict) -> int:
        """Сохранить пост-мортем анализ с полной окружающей обстановкой"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO post_mortem (
                trade_id, symbol, loss_amount, loss_percent,
                hour_opened, day_of_week, atr_at_entry, trailing_distance_used,
                continued_pump_percent, analysis, recommendations, side,
                rsi_at_entry, bollinger_b_at_entry, macd_divergence_at_entry,
                confidence_at_entry, btc_trend_at_entry, btc_strength_at_entry, problem_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('trade_id'),
                data.get('symbol'),
                data.get('loss_amount'),
                data.get('loss_percent'),
                data.get('hour_opened'),
                data.get('day_of_week'),
                data.get('atr_at_entry'),
                data.get('trailing_distance_used'),
                data.get('continued_pump_percent'),
                data.get('analysis'),
                json.dumps(data.get('recommendations', [])),
                data.get('side', 'SHORT'),
                data.get('rsi_at_entry', 50),
                data.get('bollinger_b_at_entry', 50),
                data.get('macd_divergence_at_entry', 'none'),
                data.get('confidence_at_entry', 0),
                data.get('btc_trend_at_entry', 'neutral'),
                data.get('btc_strength_at_entry', 'weak'),
                data.get('problem_count', 0)
            ))
            return cur.lastrowid
    
    def get_post_mortems(self, limit: int = 50, side: str = None) -> List[Dict]:
        """Получить список пост-мортемов с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT * FROM post_mortem
            WHERE 1=1
            '''
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += '''
            ORDER BY created_at DESC LIMIT ?
            '''
            params.append(limit)
            
            cur.execute(query, params)
            result = []
            for row in cur.fetchall():
                d = dict(row)
                if d.get('recommendations'):
                    try:
                        d['recommendations'] = json.loads(d['recommendations'])
                    except Exception:
                        d['recommendations'] = []
                result.append(d)
            return result
    
    def get_pending_post_mortems(self, side: str = None) -> List[Dict]:
        """Получить необработанные пост-мортемы с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT * FROM post_mortem
            WHERE user_action IS NULL
            '''
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += '''
            ORDER BY created_at DESC
            '''
            
            cur.execute(query, params)
            result = []
            for row in cur.fetchall():
                d = dict(row)
                if d.get('recommendations'):
                    try:
                        d['recommendations'] = json.loads(d['recommendations'])
                    except Exception:
                        d['recommendations'] = []
                result.append(d)
            return result
    
    def update_post_mortem_action(self, post_mortem_id: int, action: str) -> bool:
        """Обновить действие пользователя по пост-мортему"""
        with self.get_cursor() as cur:
            cur.execute('''
            UPDATE post_mortem
            SET user_action = ?, action_taken_at = ?
            WHERE id = ?
            ''', (action, get_gmt2_str(), post_mortem_id))
            return cur.rowcount > 0
    
    # =========================================================================
    # A/B TESTING - Тестирование AI провайдеров с поддержкой LONG/SHORT
    # =========================================================================
    
    def save_ai_ab_test(self, data: Dict) -> int:
        """Сохранить результат A/B теста"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO ai_ab_test (
                timestamp, symbol,
                deepseek_action, deepseek_confidence, deepseek_response_time,
                groq_action, groq_confidence, groq_response_time,
                consensus, chosen_provider, trade_opened, side
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                get_gmt2_str(),
                data.get('symbol'),
                data.get('deepseek_action'),
                data.get('deepseek_confidence'),
                data.get('deepseek_response_time'),
                data.get('groq_action'),
                data.get('groq_confidence'),
                data.get('groq_response_time'),
                data.get('consensus'),
                data.get('chosen_provider'),
                data.get('trade_opened', False),
                data.get('side', 'SHORT')  # Добавляем направление
            ))
            return cur.lastrowid
    
    def update_ai_ab_test_result(self, ab_test_id: int, result: str, pnl: float) -> bool:
        """Обновить результат A/B теста после закрытия сделки"""
        with self.get_cursor() as cur:
            cur.execute('''
            UPDATE ai_ab_test
            SET trade_result = ?, trade_pnl = ?
            WHERE id = ?
            ''', (result, pnl, ab_test_id))
            return cur.rowcount > 0
    
    def get_ai_ab_statistics(self, side: str = None) -> Dict:
        """Получить статистику A/B тестирования с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT
                chosen_provider,
                COUNT(*) as total,
                SUM(CASE WHEN trade_result = 'WIN' THEN 1 ELSE 0 END) as wins,
                AVG(trade_pnl) as avg_pnl,
                AVG(CASE WHEN chosen_provider = 'deepseek' THEN deepseek_response_time
                ELSE groq_response_time END) as avg_response_time
            FROM ai_ab_test
            WHERE trade_result IS NOT NULL
            '''
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += '''
            GROUP BY chosen_provider
            '''
            
            cur.execute(query, params)
            result = {}
            for row in cur.fetchall():
                provider = row['chosen_provider']
                if provider:
                    result[provider] = {
                        'total': row['total'],
                        'wins': row['wins'],
                        'win_rate': row['wins'] / row['total'] * 100 if row['total'] > 0 else 0,
                        'avg_pnl': row['avg_pnl'] or 0,
                        'avg_response_time': row['avg_response_time'] or 0
                    }
            
            # Считаем консенсус
            cur.execute('''
            SELECT
                SUM(CASE WHEN consensus = 1 THEN 1 ELSE 0 END) as consensus_count,
                COUNT(*) as total
            FROM ai_ab_test
            WHERE trade_result IS NOT NULL
            ''')
            row = cur.fetchone()
            if row and row['total'] > 0:
                result['consensus_rate'] = row['consensus_count'] / row['total'] * 100
            
            return result
    
    # =========================================================================
    # HEALTH STATUS - Статус здоровья
    # =========================================================================
    
    def save_health_status(self, status: Dict) -> int:
        """Сохранить статус здоровья"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO health_status (
                timestamp, binance_status, binance_ping_ms,
                deepseek_status, groq_status, telegram_status,
                daily_pnl, daily_pnl_limit_pct, win_rate_today, trades_today
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                get_gmt2_str(),
                status.get('binance_status'),
                status.get('binance_ping_ms'),
                status.get('deepseek_status'),
                status.get('groq_status'),
                status.get('telegram_status'),
                status.get('daily_pnl'),
                status.get('daily_pnl_limit_pct'),
                status.get('win_rate_today'),
                status.get('trades_today')
            ))
            return cur.lastrowid
    
    def get_latest_health_status(self) -> Optional[Dict]:
        """Получить последний статус здоровья"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT * FROM health_status
            ORDER BY id DESC LIMIT 1
            ''')
            row = cur.fetchone()
            return dict(row) if row else None
    
    # =========================================================================
    # MARKET HISTORY - История пампингов и дампов
    # =========================================================================
    
    def save_market_pump(self, pump_data: Dict) -> bool:
        """Сохранить данные о пампинге или дампе"""
        with self.get_cursor() as cur:
            try:
                cur.execute('''
                INSERT OR REPLACE INTO market_history (
                    symbol, timestamp, change_24h, price, volume,
                    reversal_1h, reversal_4h, reversal_24h,
                    max_drawdown, max_profit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    pump_data.get('symbol'),
                    pump_data.get('timestamp'),
                    pump_data.get('change_24h'),
                    pump_data.get('price'),
                    pump_data.get('volume'),
                    pump_data.get('reversal_1h'),
                    pump_data.get('reversal_4h'),
                    pump_data.get('reversal_24h'),
                    pump_data.get('max_drawdown'),
                    pump_data.get('max_profit'),
                    get_gmt2_str()
                ))
                return True
            except Exception as e:
                logger.error(f"[DB] Save pump error: {e}")
                return False
    
    def get_market_history_count(self) -> int:
        """Количество записей в истории"""
        with self.get_cursor() as cur:
            cur.execute('SELECT COUNT(*) as cnt FROM market_history')
            return cur.fetchone()['cnt']
    
    def get_market_statistics(self) -> Dict:
        """Получить статистику по истории рынка с разделением на пампы и дампы"""
        with self.get_cursor() as cur:
            # Общая статистика
            cur.execute('''
            SELECT
                COUNT(*) as total_pumps,
                AVG(reversal_4h) as avg_reversal_4h,
                AVG(reversal_24h) as avg_reversal_24h,
                AVG(CASE WHEN reversal_4h < -3 THEN 1 ELSE 0 END) as reversal_3pct_rate,
                AVG(CASE WHEN reversal_4h < -5 THEN 1 ELSE 0 END) as reversal_5pct_rate,
                AVG(CASE WHEN change_24h > 0 THEN change_24h ELSE 0 END) as avg_pump_size,
                AVG(CASE WHEN change_24h < 0 THEN change_24h ELSE 0 END) as avg_dump_size
            FROM market_history
            ''')
            general = dict(cur.fetchone())
            
            # Статистика по пампам (рост > 0)
            cur.execute('''
            SELECT
                COUNT(*) as pump_count,
                AVG(reversal_4h) as avg_pump_reversal,
                AVG(max_profit) as avg_max_profit,
                AVG(max_drawdown) as avg_max_drawdown
            FROM market_history
            WHERE change_24h > 0
            ''')
            pumps = dict(cur.fetchone())
            
            # Статистика по дампам (падение < 0)
            cur.execute('''
            SELECT
                COUNT(*) as dump_count,
                AVG(reversal_4h) as avg_dump_reversal,
                AVG(max_profit) as avg_dump_profit,
                AVG(max_drawdown) as avg_dump_drawdown
            FROM market_history
            WHERE change_24h < 0
            ''')
            dumps = dict(cur.fetchone())
            
            return {
                **general,
                'pumps': pumps,
                'dumps': dumps
            }
    
    # =========================================================================
    # RECOMMENDATIONS - Рекомендации с поддержкой LONG/SHORT
    # =========================================================================
    
    def save_recommendation(self, rec_data: Dict) -> int:
        """Сохранить рекомендацию"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO recommendations (
                created_at, parameter, current_value, suggested_value,
                reasoning, sample_size, expected_improvement, status, side
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
            ''', (
                get_gmt2_str(),
                rec_data.get('parameter'),
                rec_data.get('current_value'),
                rec_data.get('suggested_value'),
                rec_data.get('reasoning'),
                rec_data.get('sample_size', 0),
                rec_data.get('expected_improvement', 0),
                rec_data.get('side', 'BOTH')  # BOTH, SHORT, LONG
            ))
            return cur.lastrowid
    
    def get_pending_recommendations(self, side: str = None) -> List[Dict]:
        """Получить ожидающие рекомендации с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT * FROM recommendations
            WHERE status = 'PENDING'
            '''
            params = []
            
            if side and side.upper() != 'BOTH':
                query += ' AND (side = ? OR side = "BOTH")'
                params.append(side.upper())
            
            query += ' ORDER BY created_at DESC'
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_all_recommendations(self, limit: int = 50, side: str = None) -> List[Dict]:
        """Получить все рекомендации с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = 'SELECT * FROM recommendations'
            params = []
            
            if side and side.upper() != 'BOTH':
                query += ' WHERE (side = ? OR side = "BOTH")'
                params.append(side.upper())
            
            query += ' ORDER BY created_at DESC LIMIT ?'
            params.append(limit)
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def apply_recommendation(self, rec_id: int, applied_by: str = 'MANUAL') -> bool:
        """Применить рекомендацию"""
        with self.get_cursor() as cur:
            cur.execute('''
            UPDATE recommendations SET
                status = 'APPLIED',
                applied_at = ?,
                applied_by = ?
            WHERE id = ?
            ''', (get_gmt2_str(), applied_by, rec_id))
            return cur.rowcount > 0
    
    def ignore_recommendation(self, rec_id: int) -> bool:
        """Игнорировать рекомендацию"""
        with self.get_cursor() as cur:
            cur.execute('''
            UPDATE recommendations SET status = 'IGNORED' WHERE id = ?
            ''', (rec_id,))
            return cur.rowcount > 0
    
    def get_recommendations_count(self, status: str = 'PENDING', side: str = None) -> int:
        """Количество рекомендаций по статусу и направлению"""
        with self.get_cursor() as cur:
            query = 'SELECT COUNT(*) as cnt FROM recommendations WHERE status = ?'
            params = [status]
            
            if side and side.upper() != 'BOTH':
                query += ' AND (side = ? OR side = "BOTH")'
                params.append(side.upper())
            
            cur.execute(query, params)
            return cur.fetchone()['cnt']
    
    # =========================================================================
    # SETTINGS HISTORY - История изменений
    # =========================================================================
    
    def log_setting_change(self, parameter: str, old_value: Any, new_value: Any,
                         source: str = 'MANUAL', recommendation_id: int = None):
        """Записать изменение настройки"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO settings_history (
                changed_at, parameter, old_value, new_value,
                change_source, recommendation_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                get_gmt2_str(),
                parameter,
                old_value,
                new_value,
                source,
                recommendation_id
            ))
    
    def get_settings_history(self, limit: int = 50) -> List[Dict]:
        """Получить историю изменений"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT * FROM settings_history
            ORDER BY changed_at DESC LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cur.fetchall()]
    
    # =========================================================================
    # SETTINGS - Настройки
    # =========================================================================
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Получить настройку"""
        with self.get_cursor() as cur:
            cur.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cur.fetchone()
            if row:
                try:
                    return json.loads(row['value'])
                except Exception:
                    return row['value']
            return default
    
    def set_setting(self, key: str, value: Any):
        """Установить настройку"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ''', (key, json.dumps(value), get_gmt2_str()))
    
    def get_all_settings(self) -> Dict:
        """Получить все настройки"""
        with self.get_cursor() as cur:
            cur.execute('SELECT key, value FROM settings')
            result = {}
            for row in cur.fetchall():
                try:
                    result[row['key']] = json.loads(row['value'])
                except Exception:
                    result[row['key']] = row['value']
            return result
    
    # =========================================================================
    # ACTIVITY LOGS - Логи
    # =========================================================================
    
    def add_log(self, log_type: str, message: str, data: Dict = None):
        """Добавить лог"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO activity_logs (timestamp, log_type, message, data)
            VALUES (?, ?, ?, ?)
            ''', (
                get_gmt2_str(),
                log_type,
                message,
                json.dumps(data) if data else None
            ))
            # Чистим старые логи (оставляем 1000)
            cur.execute('''
            DELETE FROM activity_logs WHERE id NOT IN (
                SELECT id FROM activity_logs ORDER BY id DESC LIMIT 1000
            )
            ''')
    
    def get_logs(self, limit: int = 100, log_type: str = None) -> List[Dict]:
        """Получить логи"""
        with self.get_cursor() as cur:
            if log_type:
                cur.execute('''
                SELECT * FROM activity_logs
                WHERE log_type = ?
                ORDER BY id DESC LIMIT ?
                ''', (log_type, limit))
            else:
                cur.execute('''
                SELECT * FROM activity_logs
                ORDER BY id DESC LIMIT ?
                ''', (limit,))
            result = []
            for row in cur.fetchall():
                d = dict(row)
                if d.get('data'):
                    try:
                        d['data'] = json.loads(d['data'])
                    except Exception:
                        pass
                result.append(d)
            return result
    
    # =========================================================================
    # BLACKLIST - Черный список
    # =========================================================================
    
    def add_to_blacklist(self, symbol: str, reason: str = "", added_by: str = "MANUAL") -> bool:
        """Добавить символ в черный список"""
        with self.get_cursor() as cur:
            try:
                clean_symbol = self._clean_symbol(symbol)
                cur.execute('SELECT id FROM blacklist WHERE symbol = ? AND enabled = 1', (clean_symbol,))
                if cur.fetchone():
                    logger.info(f"[DB] Symbol already in blacklist: {clean_symbol}")
                    return False
                
                cur.execute('SELECT id FROM blacklist WHERE symbol = ? AND enabled = 0', (clean_symbol,))
                existing = cur.fetchone()
                if existing:
                    cur.execute('''
                    UPDATE blacklist
                    SET reason = ?, added_at = ?, added_by = ?, enabled = 1
                    WHERE symbol = ?
                    ''', (reason, get_gmt2_str(), added_by, clean_symbol))
                else:
                    cur.execute('''
                    INSERT INTO blacklist (symbol, reason, added_at, added_by, enabled)
                    VALUES (?, ?, ?, ?, 1)
                    ''', (clean_symbol, reason, get_gmt2_str(), added_by))
                logger.info(f"[DB] Added to blacklist: {clean_symbol} - {reason}")
                return True
            except Exception as e:
                logger.error(f"[DB] Error adding to blacklist: {e}")
                return False
    
    def remove_from_blacklist(self, symbol: str, permanent: bool = False) -> bool:
        """Удалить символ из черного списка"""
        with self.get_cursor() as cur:
            clean_symbol = self._clean_symbol(symbol)
            if permanent:
                cur.execute('DELETE FROM blacklist WHERE symbol = ?', (clean_symbol,))
            else:
                cur.execute('''
                UPDATE blacklist SET enabled = 0 WHERE symbol = ?
                ''', (clean_symbol,))
            logger.info(f"[DB] {'Permanently removed' if permanent else 'Disabled'} from blacklist: {clean_symbol}")
            return cur.rowcount > 0
    
    def get_blacklist(self, include_disabled: bool = False) -> List[Dict]:
        """Получить весь черный список"""
        with self.get_cursor() as cur:
            if include_disabled:
                cur.execute('SELECT * FROM blacklist ORDER BY added_at DESC')
            else:
                cur.execute('SELECT * FROM blacklist WHERE enabled = 1 ORDER BY added_at DESC')
            return [dict(row) for row in cur.fetchall()]
    
    def is_blacklisted(self, symbol: str) -> bool:
        """Проверить, находится ли символ в черном списке"""
        with self.get_cursor() as cur:
            clean_symbol = self._clean_symbol(symbol)
            cur.execute('SELECT 1 FROM blacklist WHERE symbol = ? AND enabled = 1', (clean_symbol,))
            return cur.fetchone() is not None
    
    def get_blacklist_count(self) -> int:
        """Количество символов в черном списке"""
        with self.get_cursor() as cur:
            cur.execute('SELECT COUNT(*) as cnt FROM blacklist WHERE enabled = 1')
            return cur.fetchone()['cnt']
    
    def _clean_symbol(self, symbol: str) -> str:
        """Очистка символа от суффиксов"""
        if not symbol:
            return ""
        symbol = symbol.strip().upper()
        if ':USDT' in symbol:
            symbol = symbol.replace(':USDT', '')
        if '/USDT' not in symbol and symbol:
            symbol = symbol + '/USDT'
        return symbol
    
    def update_blacklist_reason(self, symbol: str, reason: str) -> bool:
        """Обновить причину в черном списке"""
        with self.get_cursor() as cur:
            clean_symbol = self._clean_symbol(symbol)
            cur.execute('''
            UPDATE blacklist SET reason = ? WHERE symbol = ? AND enabled = 1
            ''', (reason, clean_symbol))
            return cur.rowcount > 0
    
    def clear_blacklist(self) -> int:
        """Очистить весь черный список"""
        with self.get_cursor() as cur:
            cur.execute('UPDATE blacklist SET enabled = 0')
            return cur.rowcount
    
    # =========================================================================
    # STATISTICS - Статистика для аналитики с поддержкой LONG/SHORT
    # =========================================================================
    
    def get_trade_statistics(self, trade_mode: str = None, side: str = None) -> Dict:
        """Общая статистика по сделкам с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses,
                SUM(pnl_usdt) as total_pnl,
                AVG(pnl_usdt) as avg_pnl,
                AVG(CASE WHEN result = 'WIN' THEN pnl_usdt END) as avg_win,
                AVG(CASE WHEN result = 'LOSS' THEN pnl_usdt END) as avg_loss,
                AVG(duration_minutes) as avg_duration
            FROM trades WHERE result IS NOT NULL
            '''
            params = []
            
            if trade_mode:
                query += ' AND trade_mode = ?'
                params.append(trade_mode)
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            cur.execute(query, params)
            general = dict(cur.fetchone())
            if general['total_trades'] and general['total_trades'] > 0:
                general['win_rate'] = (general['wins'] or 0) / general['total_trades'] * 100
            else:
                general['win_rate'] = 0
            
            # Получаем статистику по направлениям если не фильтруем по конкретному направлению
            if not side:
                cur.execute('''
                SELECT side, COUNT(*) as count, 
                       SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                       SUM(pnl_usdt) as pnl
                FROM trades 
                WHERE result IS NOT NULL
                GROUP BY side
                ''')
                by_direction = {}
                for row in cur.fetchall():
                    side_name = row['side'].upper()
                    total = row['count']
                    wins = row['wins']
                    by_direction[side_name] = {
                        'total': total,
                        'wins': wins,
                        'win_rate': (wins / total * 100) if total > 0 else 0,
                        'pnl': row['pnl'] or 0
                    }
                general['by_direction'] = by_direction
            
            return general
    
    def get_hourly_statistics(self, side: str = None) -> List[Dict]:
        """Статистика по часам с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT
                hour_opened as hour,
                COUNT(*) as total,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
                SUM(pnl_usdt) as total_pnl
            FROM trades
            WHERE result IS NOT NULL
            '''
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += '''
            GROUP BY hour_opened
            ORDER BY hour_opened
            '''
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_daily_statistics(self, side: str = None) -> List[Dict]:
        """Статистика по дням недели с фильтрацией по направлению"""
        days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        with self.get_cursor() as cur:
            query = '''
            SELECT
                day_of_week,
                COUNT(*) as total,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
                SUM(pnl_usdt) as total_pnl
            FROM trades
            WHERE result IS NOT NULL
            '''
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += '''
            GROUP BY day_of_week
            ORDER BY day_of_week
            '''
            
            cur.execute(query, params)
            result = []
            for row in cur.fetchall():
                d = dict(row)
                d['day_name'] = days[d['day_of_week']] if d['day_of_week'] < 7 else '?'
                result.append(d)
            return result
    
    def get_symbol_statistics(self, side: str = None) -> List[Dict]:
        """Статистика по символам с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT
                symbol,
                COUNT(*) as total,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
                SUM(pnl_usdt) as total_pnl
            FROM trades
            WHERE result IS NOT NULL
            '''
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += '''
            GROUP BY symbol
            ORDER BY total DESC
            LIMIT 20
            '''
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_confidence_statistics(self, side: str = None) -> List[Dict]:
        """Статистика по confidence с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT
                CASE
                    WHEN ai_confidence >= 95 THEN '95-100'
                    WHEN ai_confidence >= 90 THEN '90-94'
                    WHEN ai_confidence >= 85 THEN '85-89'
                    WHEN ai_confidence >= 80 THEN '80-84'
                    ELSE '<80'
                END as confidence_range,
                COUNT(*) as total,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
                SUM(pnl_usdt) as total_pnl
            FROM trades
            WHERE result IS NOT NULL
            '''
            params = []
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += '''
            GROUP BY confidence_range
            ORDER BY confidence_range DESC
            '''
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_today_statistics(self) -> Dict:
        """Статистика за сегодня"""
        today = get_gmt2_time().strftime('%Y-%m-%d')
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT
                COUNT(*) as trades_today,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins_today,
                SUM(pnl_usdt) as pnl_today
            FROM trades
            WHERE result IS NOT NULL AND opened_at LIKE ?
            ''', (f'{today}%',))
            row = cur.fetchone()
            result = dict(row) if row else {'trades_today': 0, 'wins_today': 0, 'pnl_today': 0}
            if result['trades_today'] > 0:
                result['win_rate_today'] = result['wins_today'] / result['trades_today'] * 100
            else:
                result['win_rate_today'] = 0
            return result
    
    # =========================================================================
    # EXPORT - Экспорт данных
    # =========================================================================
    
    def export_trades_csv(self, side: str = None) -> str:
        """Экспорт сделок в CSV формат с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = 'SELECT * FROM trades'
            params = []
            
            if side:
                query += ' WHERE side = ?'
                params.append(side.upper())
            
            query += ' ORDER BY opened_at DESC'
            
            cur.execute(query, params)
            rows = cur.fetchall()
            if not rows:
                return "No data"
            headers = rows[0].keys()
            lines = [','.join(headers)]
            for row in rows:
                values = [str(row[h] or '').replace(',', ';') for h in headers]  # Экранируем запятые в данных
                lines.append(','.join(values))
            return '\n'.join(lines)
    
    def export_statistics_csv(self) -> str:
        """Экспорт статистики в CSV"""
        stats = self.get_trade_statistics()
        hourly = self.get_hourly_statistics()
        daily = self.get_daily_statistics()
        lines = ["=== GENERAL STATISTICS ==="]
        for k, v in stats.items():
            lines.append(f"{k},{v}")
        lines.append("\n=== HOURLY STATISTICS ===")
        lines.append("hour,total,wins,win_rate,total_pnl")
        for h in hourly:
            lines.append(f"{h['hour']},{h['total']},{h['wins']},{h['win_rate']},{h['total_pnl']}")
        lines.append("\n=== DAILY STATISTICS ===")
        lines.append("day,total,wins,win_rate,total_pnl")
        for d in daily:
            lines.append(f"{d['day_name']},{d['total']},{d['wins']},{d['win_rate']},{d['total_pnl']}")
        return '\n'.join(lines)
    
    # =========================================================================
    # AI ANALYSES - История анализов с поддержкой LONG/SHORT
    # =========================================================================
    
    def save_ai_analysis(self, data: Dict) -> int:
        """Сохранить анализ AI в историю"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO ai_analyses (
                timestamp, symbol, ai_provider, action, confidence,
                entry_price, sl_original, sl_corrected, sl_was_fixed,
                tp1, tp2, analysis_text, change_24h, atr_percent,
                trade_opened, trade_id, side
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                get_gmt2_str(),
                data.get('symbol', ''),
                data.get('ai_provider', ''),
                data.get('action', ''),
                data.get('confidence', 0),
                data.get('entry_price', 0),
                data.get('sl_original', 0),
                data.get('sl_corrected', 0),
                1 if data.get('sl_was_fixed', False) else 0,
                data.get('tp1', 0),
                data.get('tp2', 0),
                data.get('analysis_text', '')[:5000],  # Ограничиваем размер
                data.get('change_24h', 0),
                data.get('atr_percent', 0),
                1 if data.get('trade_opened', False) else 0,
                data.get('trade_id', ''),
                data.get('side', 'SHORT')  # Добавляем направление
            ))
            return cur.lastrowid
    
    def get_ai_analyses(self, limit: int = 100, offset: int = 0,
                        symbol: str = None, provider: str = None,
                        side: str = None) -> List[Dict]:
        """Получить историю анализов AI с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = 'SELECT * FROM ai_analyses WHERE 1=1'
            params = []
            
            if symbol:
                query += ' AND symbol LIKE ?'
                params.append(f'%{symbol}%')
            
            if provider:
                query += ' AND ai_provider = ?'
                params.append(provider)
            
            if side:
                query += ' AND side = ?'
                params.append(side.upper())
            
            query += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_ai_analyses_count(self, side: str = None) -> int:
        """Количество записей анализов с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = 'SELECT COUNT(*) FROM ai_analyses'
            params = []
            
            if side:
                query += ' WHERE side = ?'
                params.append(side.upper())
            
            cur.execute(query, params)
            return cur.fetchone()[0]
    
    def export_ai_analyses_csv(self, side: str = None) -> str:
        """Экспорт анализов AI в CSV с фильтрацией по направлению"""
        with self.get_cursor() as cur:
            query = '''
            SELECT id, timestamp, symbol, ai_provider, action, confidence,
            entry_price, sl_original, sl_corrected, sl_was_fixed,
            tp1, tp2, change_24h, atr_percent, trade_opened, trade_id, side
            FROM ai_analyses
            '''
            params = []
            
            if side:
                query += ' WHERE side = ?'
                params.append(side.upper())
            
            query += ' ORDER BY timestamp DESC'
            
            cur.execute(query, params)
            rows = cur.fetchall()
            if not rows:
                return "No data"
            headers = ['id', 'timestamp', 'symbol', 'ai_provider', 'action', 'confidence',
                      'entry_price', 'sl_original', 'sl_corrected', 'sl_was_fixed',
                      'tp1', 'tp2', 'change_24h', 'atr_percent', 'trade_opened', 'trade_id', 'side']
            lines = [','.join(headers)]
            for row in rows:
                values = [str(row[h] or '').replace(',', ';') for h in headers]
                lines.append(','.join(values))
            return '\n'.join(lines)
    
    # =========================================================================
    # TRADE PRICE HISTORY - История хода сделки
    # =========================================================================
    
    def save_trade_price_event(self, trade_id: str, event_type: str,
                             price: float, pnl_percent: float = 0,
                             trailing_stop: float = 0, details: str = '') -> int:
        """Сохранить событие в истории сделки"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO trade_price_history (
                trade_id, timestamp, event_type, price, pnl_percent, trailing_stop, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (trade_id, get_gmt2_str(), event_type, price, pnl_percent, trailing_stop, details))
            return cur.lastrowid
    
    def get_trade_price_history(self, trade_id: str) -> List[Dict]:
        """Получить историю хода конкретной сделки"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT * FROM trade_price_history
            WHERE trade_id = ? ORDER BY timestamp ASC
            ''', (trade_id,))
            return [dict(row) for row in cur.fetchall()]
    
    def export_trade_price_history_csv(self, trade_id: str = None) -> str:
        """Экспорт истории хода сделок в CSV"""
        with self.get_cursor() as cur:
            if trade_id:
                cur.execute('''
                SELECT * FROM trade_price_history
                WHERE trade_id = ? ORDER BY timestamp ASC
                ''', (trade_id,))
            else:
                cur.execute('SELECT * FROM trade_price_history ORDER BY timestamp DESC LIMIT 10000')
            rows = cur.fetchall()
            if not rows:
                return "No data"
            headers = rows[0].keys()
            lines = [','.join(headers)]
            for row in rows:
                values = [str(row[h] or '').replace(',', ';') for h in headers]
                lines.append(','.join(values))
            return '\n'.join(lines)
    
    # =========================================================================
    # AI PROMPTS - Кастомные промпты
    # =========================================================================
    
    def save_ai_prompt(self, name: str, prompt_text: str, is_active: bool = False) -> int:
        """Сохранить промпт"""
        with self.get_cursor() as cur:
            # Если активируем новый - деактивируем остальные
            if is_active:
                cur.execute('UPDATE ai_prompts SET is_active = 0')
            # Проверяем существует ли
            cur.execute('SELECT id FROM ai_prompts WHERE name = ?', (name,))
            existing = cur.fetchone()
            if existing:
                cur.execute('''
                UPDATE ai_prompts
                SET prompt_text = ?, is_active = ?, updated_at = ?
                WHERE name = ?
                ''', (prompt_text, 1 if is_active else 0, get_gmt2_str(), name))
                return existing[0]
            else:
                cur.execute('''
                INSERT INTO ai_prompts (name, prompt_text, is_active, updated_at)
                VALUES (?, ?, ?, ?)
                ''', (name, prompt_text, 1 if is_active else 0, get_gmt2_str()))
                return cur.lastrowid
    
    def get_active_prompt(self) -> Optional[Dict]:
        """Получить активный промпт"""
        with self.get_cursor() as cur:
            cur.execute('SELECT * FROM ai_prompts WHERE is_active = 1 LIMIT 1')
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_all_prompts(self) -> List[Dict]:
        """Получить все промпты"""
        with self.get_cursor() as cur:
            cur.execute('SELECT * FROM ai_prompts ORDER BY updated_at DESC')
            return [dict(row) for row in cur.fetchall()]
    
    def delete_prompt(self, name: str) -> bool:
        """Удалить промпт"""
        with self.get_cursor() as cur:
            cur.execute('DELETE FROM ai_prompts WHERE name = ? AND name != "default"', (name,))
            return cur.rowcount > 0
    
    # =========================================================================
    # BTC TREND - Статистика по тренду Bitcoin
    # =========================================================================
    
    def save_btc_trend(self, trend_data: Dict) -> int:
        """Сохранить данные о тренде Bitcoin"""
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO btc_trend_history (
                timestamp, trend, strength, rsi_1h, change_24h,
                confidence, signal_impact_short, signal_impact_long
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                get_gmt2_str(),
                trend_data.get('trend', 'neutral'),
                trend_data.get('strength', 'weak'),
                trend_data.get('rsi_1h', 50.0),
                trend_data.get('change_24h', 0.0),
                trend_data.get('confidence', 50),
                trend_data.get('signal_impact', {}).get('short_confidence_modifier', 0),
                trend_data.get('signal_impact', {}).get('long_confidence_modifier', 0)
            ))
            return cur.lastrowid
    
    # =========================================================================
    # EXPORT ENHANCED - Расширенный экспорт сделок
    # =========================================================================
    
    def export_trades_detailed_csv(self, side: str = None) -> str:
        """Экспорт сделок с детальной информацией о SL и направлении"""
        with self.get_cursor() as cur:
            query = '''
            SELECT
                t.id, t.trade_id, t.symbol, t.side,
                t.opened_at, t.closed_at,
                t.entry_price, t.exit_price,
                t.stop_loss, t.trailing_stop,
                t.take_profit_1, t.take_profit_2,
                t.pnl_percent, t.pnl_usdt, t.result, t.close_reason,
                t.ai_provider, t.ai_confidence as confidence, t.atr_percent, t.trade_mode,
                a.sl_original, a.sl_corrected, a.sl_was_fixed
            FROM trades t
            LEFT JOIN ai_analyses a ON t.trade_id = a.trade_id
            WHERE 1=1
            '''
            params = []
            if side:
                query += ' AND t.side = ?'
                params.append(side.upper())
            
            query += ' ORDER BY t.opened_at DESC'
            
            cur.execute(query, params)
            rows = cur.fetchall()
            if not rows:
                return "No data"
            headers = ['id', 'trade_id', 'symbol', 'side',
                      'opened_at', 'closed_at',
                      'entry_price', 'exit_price',
                      'stop_loss', 'trailing_stop',
                      'take_profit_1', 'take_profit_2',
                      'pnl_percent', 'pnl_usdt', 'result', 'close_reason',
                      'ai_provider', 'confidence', 'atr_percent', 'trade_mode',
                      'sl_original', 'sl_corrected', 'sl_was_fixed']
            lines = [','.join(headers)]
            for row in rows:
                values = [str(row[h] if row[h] is not None else '').replace(',', ';') for h in headers]
                lines.append(','.join(values))
            return '\n'.join(lines)
    
    # =========================================================================
    # AGENT V3 - Память о монетах
    # =========================================================================
    
    def get_coin_memory(self, symbol: str) -> Optional[Dict]:
        """Получить память о монете"""
        sym = self._normalize_symbol(symbol)
        with self.get_cursor() as cur:
            cur.execute('SELECT * FROM agent_coin_memory WHERE symbol = ?', (sym,))
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_all_coin_memory(self) -> List[Dict]:
        """Получить всю память о монетах"""
        with self.get_cursor() as cur:
            cur.execute('SELECT * FROM agent_coin_memory ORDER BY total_trades DESC')
            return [dict(row) for row in cur.fetchall()]
    
    def update_coin_memory(self, symbol: str, pnl: float, hold_minutes: float = 0, side: str = "") -> bool:
        """Обновить память о монете после сделки"""
        sym = self._normalize_symbol(symbol)
        now = get_gmt2_str()
        result = 'WIN' if pnl > 0 else 'LOSS'
        
        with self.get_cursor() as cur:
            # Получаем текущие данные
            cur.execute('SELECT * FROM agent_coin_memory WHERE symbol = ?', (sym,))
            existing = cur.fetchone()
            
            if existing:
                data = dict(existing)
                total = data['total_trades'] + 1
                wins = data['wins'] + (1 if pnl > 0 else 0)
                losses = data['losses'] + (1 if pnl <= 0 else 0)
                total_pnl = data['total_pnl'] + pnl
                
                # Обновляем streak
                if result == data.get('last_result'):
                    current_streak = data['current_streak'] + 1
                else:
                    current_streak = 1
                
                win_streak = max(data['win_streak'], current_streak if result == 'WIN' else 0)
                loss_streak = max(data['loss_streak'], current_streak if result == 'LOSS' else 0)
                
                # Лучшая/худшая сделка
                best = max(data['best_trade_pnl'], pnl)
                worst = min(data['worst_trade_pnl'], pnl)
                
                # Средние
                avg_pnl = total_pnl / total if total > 0 else 0
                avg_hold = ((data['avg_hold_minutes'] * data['total_trades']) + hold_minutes) / total if total > 0 else 0
                
                # Средний WIN/LOSS
                if pnl > 0:
                    old_win_count = data['wins']
                    old_avg_win = data['avg_win_pnl']
                    avg_win = ((old_avg_win * old_win_count) + pnl) / wins if wins > 0 else pnl
                    avg_loss = data['avg_loss_pnl']
                else:
                    old_loss_count = data['losses']
                    old_avg_loss = data['avg_loss_pnl']
                    avg_loss = ((old_avg_loss * old_loss_count) + abs(pnl)) / losses if losses > 0 else abs(pnl)
                    avg_win = data['avg_win_pnl']
                
                cur.execute('''
                UPDATE agent_coin_memory SET
                    total_trades = ?, wins = ?, losses = ?, total_pnl = ?,
                    avg_pnl = ?, avg_hold_minutes = ?, best_trade_pnl = ?, worst_trade_pnl = ?,
                    win_streak = ?, loss_streak = ?, current_streak = ?,
                    last_trade_at = ?, last_result = ?,
                    avg_win_pnl = ?, avg_loss_pnl = ?, updated_at = ?
                WHERE symbol = ?
                ''', (total, wins, losses, total_pnl, avg_pnl, avg_hold, best, worst,
                      win_streak, loss_streak, current_streak, now, result,
                      avg_win, avg_loss, now, sym))
            else:
                # Новая монета
                cur.execute('''
                INSERT INTO agent_coin_memory 
                (symbol, total_trades, wins, losses, total_pnl, avg_pnl, avg_hold_minutes,
                 best_trade_pnl, worst_trade_pnl, current_streak, last_trade_at, last_result,
                 avg_win_pnl, avg_loss_pnl, preferred_side, created_at, updated_at)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                ''', (sym, 1 if pnl > 0 else 0, 0 if pnl > 0 else 1, pnl, pnl, hold_minutes,
                      pnl if pnl > 0 else 0, pnl if pnl < 0 else 0, now, result,
                      pnl if pnl > 0 else 0, abs(pnl) if pnl < 0 else 0, side, now, now))
            return True
    
    def set_coin_blacklist(self, symbol: str, blacklisted: bool, reason: str = "", hours: int = 24) -> bool:
        """Установить/снять blacklist для монеты"""
        sym = self._normalize_symbol(symbol)
        now = get_gmt2_str()
        until = (get_gmt2_time() + timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S') if blacklisted else None
        
        with self.get_cursor() as cur:
            cur.execute('SELECT symbol FROM agent_coin_memory WHERE symbol = ?', (sym,))
            if cur.fetchone():
                cur.execute('''
                UPDATE agent_coin_memory SET blacklisted = ?, blacklist_reason = ?, blacklist_until = ?, updated_at = ?
                WHERE symbol = ?
                ''', (1 if blacklisted else 0, reason, until, now, sym))
            else:
                cur.execute('''
                INSERT INTO agent_coin_memory (symbol, blacklisted, blacklist_reason, blacklist_until, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (sym, 1 if blacklisted else 0, reason, until, now, now))
            return True
    
    def is_coin_blacklisted_by_agent(self, symbol: str) -> Tuple[bool, str]:
        """Проверить в blacklist ли монета у агента"""
        sym = self._normalize_symbol(symbol)
        with self.get_cursor() as cur:
            cur.execute('SELECT blacklisted, blacklist_reason, blacklist_until FROM agent_coin_memory WHERE symbol = ?', (sym,))
            row = cur.fetchone()
            if not row or not row['blacklisted']:
                return False, ""
            # Проверяем срок
            if row['blacklist_until']:
                until = datetime.strptime(row['blacklist_until'], '%Y-%m-%d %H:%M:%S')
                if datetime.now() > until:
                    # Срок истёк
                    cur.execute('UPDATE agent_coin_memory SET blacklisted = 0 WHERE symbol = ?', (sym,))
                    return False, ""
            return True, row['blacklist_reason'] or ""
    
    def set_coin_notes(self, symbol: str, notes: str) -> bool:
        """Установить AI заметки для монеты"""
        sym = self._normalize_symbol(symbol)
        now = get_gmt2_str()
        with self.get_cursor() as cur:
            cur.execute('SELECT symbol FROM agent_coin_memory WHERE symbol = ?', (sym,))
            if cur.fetchone():
                cur.execute('UPDATE agent_coin_memory SET ai_notes = ?, updated_at = ? WHERE symbol = ?', (notes, now, sym))
            else:
                cur.execute('INSERT INTO agent_coin_memory (symbol, ai_notes, created_at, updated_at) VALUES (?, ?, ?, ?)', (sym, notes, now, now))
            return True
    
    def _normalize_symbol(self, symbol: str) -> str:
        """Единая нормализация символа: CLO/USDT:USDT → CLO"""
        if not symbol:
            return ""
        s = symbol.upper()
        # Убираем :USDT в конце
        if ':USDT' in s:
            s = s.split(':')[0]
        # Убираем /USDT
        s = s.replace('/USDT', '').replace('USDT', '')
        # Убираем оставшиеся /
        s = s.replace('/', '')
        return s.strip()
    
    # =========================================================================
    # AGENT V3 - История решений
    # =========================================================================
    
    def save_agent_decision(self, decision: Dict) -> int:
        """Сохранить решение агента"""
        now = get_gmt2_str()
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO agent_decisions 
            (timestamp, trade_id, symbol, side, action, action_params, reason, reasoning, trigger,
             pnl_before, market_context, btc_price, btc_trend, tools_used, ai_provider, execution_time_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                now,
                decision.get('trade_id'),
                self._normalize_symbol(decision.get('symbol', '')),
                decision.get('side'),
                decision.get('action'),
                json.dumps(decision.get('params', {}), ensure_ascii=False) if decision.get('params') else None,
                decision.get('reason'),
                decision.get('reasoning'),
                decision.get('trigger'),
                decision.get('pnl_before'),
                json.dumps(decision.get('market_context', {}), ensure_ascii=False) if decision.get('market_context') else None,
                decision.get('btc_price'),
                decision.get('btc_trend'),
                ','.join(decision.get('tools_used', [])) if decision.get('tools_used') else None,
                decision.get('ai_provider'),
                decision.get('execution_time_ms'),
                now
            ))
            return cur.lastrowid
    
    def update_decision_result(self, decision_id: int, pnl_after: float, was_correct: bool) -> bool:
        """Обновить результат решения (для обучения)"""
        with self.get_cursor() as cur:
            cur.execute('''
            UPDATE agent_decisions SET pnl_after = ?, was_correct = ? WHERE id = ?
            ''', (pnl_after, 1 if was_correct else 0, decision_id))
            return True
    
    def get_agent_decisions(self, limit: int = 50, symbol: str = None, action: str = None) -> List[Dict]:
        """Получить историю решений"""
        with self.get_cursor() as cur:
            query = 'SELECT * FROM agent_decisions WHERE 1=1'
            params = []
            if symbol:
                query += ' AND symbol = ?'
                params.append(self._normalize_symbol(symbol))
            if action:
                query += ' AND action = ?'
                params.append(action)
            query += ' ORDER BY timestamp DESC LIMIT ?'
            params.append(limit)
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_pending_decision_results(self) -> List[Dict]:
        """Получить решения без результата (для обучения)"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT * FROM agent_decisions 
            WHERE was_correct IS NULL AND action != 'HOLD'
            ORDER BY timestamp DESC LIMIT 100
            ''')
            return [dict(row) for row in cur.fetchall()]
    
    # =========================================================================
    # AGENT V3 - Уроки (обучение на ошибках)
    # =========================================================================
    
    def save_agent_lesson(self, lesson: str, category: str = None, symbol: str = None, 
                          confidence: float = 0.5, source_decision_id: int = None) -> int:
        """Сохранить урок агента"""
        now = get_gmt2_str()
        with self.get_cursor() as cur:
            cur.execute('''
            INSERT INTO agent_lessons (lesson, category, symbol, confidence, source_decision_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (lesson, category, self._normalize_symbol(symbol) if symbol else None, confidence, source_decision_id, now))
            return cur.lastrowid
    
    def get_agent_lessons(self, category: str = None, symbol: str = None, min_confidence: float = 0) -> List[Dict]:
        """Получить уроки агента"""
        with self.get_cursor() as cur:
            query = 'SELECT * FROM agent_lessons WHERE confidence >= ?'
            params = [min_confidence]
            if category:
                query += ' AND category = ?'
                params.append(category)
            if symbol:
                query += ' AND (symbol = ? OR symbol IS NULL)'
                params.append(self._normalize_symbol(symbol))
            query += ' ORDER BY confidence DESC, times_correct DESC'
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def update_lesson_stats(self, lesson_id: int, was_correct: bool) -> bool:
        """Обновить статистику применения урока"""
        now = get_gmt2_str()
        with self.get_cursor() as cur:
            cur.execute('''
            UPDATE agent_lessons SET 
                times_applied = times_applied + 1,
                times_correct = times_correct + ?,
                confidence = CAST(times_correct + ? AS REAL) / (times_applied + 1),
                last_applied_at = ?
            WHERE id = ?
            ''', (1 if was_correct else 0, 1 if was_correct else 0, now, lesson_id))
            return True
    
    # =========================================================================
    # AGENT V3 - Состояние агента
    # =========================================================================
    
    def get_agent_state(self, key: str) -> Optional[str]:
        """Получить состояние агента"""
        with self.get_cursor() as cur:
            cur.execute('SELECT value FROM agent_state WHERE key = ?', (key,))
            row = cur.fetchone()
            return row['value'] if row else None
    
    def set_agent_state(self, key: str, value: str) -> bool:
        """Установить состояние агента"""
        now = get_gmt2_str()
        with self.get_cursor() as cur:
            cur.execute('SELECT key FROM agent_state WHERE key = ?', (key,))
            if cur.fetchone():
                cur.execute('UPDATE agent_state SET value = ?, updated_at = ? WHERE key = ?', (value, now, key))
            else:
                cur.execute('INSERT INTO agent_state (key, value, updated_at) VALUES (?, ?, ?)', (key, value, now))
            return True
    
    def get_all_agent_state(self) -> Dict[str, str]:
        """Получить всё состояние агента"""
        with self.get_cursor() as cur:
            cur.execute('SELECT key, value FROM agent_state')
            return {row['key']: row['value'] for row in cur.fetchall()}
    
    # =========================================================================
    # AGENT V3 - Доступ к истории сделок и аналитика
    # =========================================================================
    
    def get_symbol_trade_history(self, symbol: str, limit: int = 20) -> List[Dict]:
        """Получить историю сделок по монете"""
        sym = self._normalize_symbol(symbol)
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT trade_id, symbol, side, opened_at, closed_at,
                   entry_price, exit_price, stop_loss, take_profit_1,
                   pnl_usdt, pnl_percent, result, close_reason,
                   ai_confidence, duration_minutes, atr_percent
            FROM trades
            WHERE symbol LIKE ? AND closed_at IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
            ''', (f'%{sym}%', limit))
            return [dict(row) for row in cur.fetchall()]
    
    def get_symbol_statistics(self, symbol: str) -> Dict:
        """Полная статистика по монете"""
        sym = self._normalize_symbol(symbol)
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT 
                COUNT(*) as total_trades,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses,
                SUM(pnl_usdt) as total_pnl,
                AVG(pnl_usdt) as avg_pnl,
                AVG(CASE WHEN result = 'WIN' THEN pnl_usdt END) as avg_win,
                AVG(CASE WHEN result = 'LOSS' THEN pnl_usdt END) as avg_loss,
                AVG(duration_minutes) as avg_duration,
                MAX(pnl_usdt) as best_trade,
                MIN(pnl_usdt) as worst_trade,
                AVG(ai_confidence) as avg_confidence,
                AVG(atr_percent) as avg_atr
            FROM trades
            WHERE symbol LIKE ? AND closed_at IS NOT NULL
            ''', (f'%{sym}%',))
            row = cur.fetchone()
            if row and row['total_trades'] > 0:
                result = dict(row)
                result['win_rate'] = (result['wins'] / result['total_trades'] * 100) if result['total_trades'] > 0 else 0
                return result
            return {'total_trades': 0, 'win_rate': 0}
    
    def get_trade_events(self, trade_id: str) -> List[Dict]:
        """Получить историю хода сделки"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT timestamp, event_type, price, pnl_percent, trailing_stop, details
            FROM trade_price_history
            WHERE trade_id = ?
            ORDER BY timestamp ASC
            ''', (trade_id,))
            return [dict(row) for row in cur.fetchall()]
    
    def get_similar_trades(self, symbol: str = None, side: str = None, 
                           rsi_min: float = None, rsi_max: float = None,
                           confidence_min: int = None, limit: int = 20) -> List[Dict]:
        """Найти похожие сделки по условиям"""
        with self.get_cursor() as cur:
            query = '''
            SELECT t.trade_id, t.symbol, t.side, t.opened_at, t.closed_at,
                   t.entry_price, t.exit_price, t.pnl_usdt, t.pnl_percent,
                   t.result, t.close_reason, t.ai_confidence, t.duration_minutes,
                   p.rsi_at_entry, p.bollinger_b_at_entry, p.btc_trend_at_entry
            FROM trades t
            LEFT JOIN post_mortem p ON t.trade_id = p.trade_id
            WHERE t.closed_at IS NOT NULL
            '''
            params = []
            
            if symbol:
                query += ' AND t.symbol LIKE ?'
                params.append(f'%{self._normalize_symbol(symbol)}%')
            if side:
                query += ' AND t.side = ?'
                params.append(side.upper())
            if rsi_min is not None:
                query += ' AND p.rsi_at_entry >= ?'
                params.append(rsi_min)
            if rsi_max is not None:
                query += ' AND p.rsi_at_entry <= ?'
                params.append(rsi_max)
            if confidence_min is not None:
                query += ' AND t.ai_confidence >= ?'
                params.append(confidence_min)
            
            query += ' ORDER BY t.closed_at DESC LIMIT ?'
            params.append(limit)
            
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_hourly_statistics(self) -> List[Dict]:
        """Статистика по часам"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT hour_opened as hour,
                   COUNT(*) as total,
                   SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                   ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
                   SUM(pnl_usdt) as total_pnl,
                   AVG(pnl_usdt) as avg_pnl
            FROM trades
            WHERE closed_at IS NOT NULL AND hour_opened IS NOT NULL
            GROUP BY hour_opened
            ORDER BY hour_opened
            ''')
            return [dict(row) for row in cur.fetchall()]
    
    def get_confidence_statistics(self) -> List[Dict]:
        """Статистика по уровням confidence"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT 
                CASE 
                    WHEN ai_confidence >= 85 THEN '85-100'
                    WHEN ai_confidence >= 80 THEN '80-84'
                    WHEN ai_confidence >= 75 THEN '75-79'
                    WHEN ai_confidence >= 70 THEN '70-74'
                    ELSE '<70'
                END as confidence_range,
                COUNT(*) as total,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
                SUM(pnl_usdt) as total_pnl,
                AVG(pnl_usdt) as avg_pnl
            FROM trades
            WHERE closed_at IS NOT NULL
            GROUP BY confidence_range
            ORDER BY confidence_range DESC
            ''')
            return [dict(row) for row in cur.fetchall()]
    
    def get_side_statistics(self) -> Dict:
        """Статистика по направлениям SHORT/LONG"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT side,
                   COUNT(*) as total,
                   SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                   ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
                   SUM(pnl_usdt) as total_pnl,
                   AVG(pnl_usdt) as avg_pnl,
                   AVG(duration_minutes) as avg_duration
            FROM trades
            WHERE closed_at IS NOT NULL
            GROUP BY side
            ''')
            result = {}
            for row in cur.fetchall():
                result[row['side']] = dict(row)
            return result
    
    def get_best_worst_coins(self, limit: int = 10) -> Dict:
        """Лучшие и худшие монеты по PnL"""
        with self.get_cursor() as cur:
            # Лучшие
            cur.execute('''
            SELECT symbol,
                   COUNT(*) as trades,
                   SUM(pnl_usdt) as total_pnl,
                   ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate
            FROM trades
            WHERE closed_at IS NOT NULL
            GROUP BY symbol
            HAVING COUNT(*) >= 2
            ORDER BY total_pnl DESC
            LIMIT ?
            ''', (limit,))
            best = [dict(row) for row in cur.fetchall()]
            
            # Худшие
            cur.execute('''
            SELECT symbol,
                   COUNT(*) as trades,
                   SUM(pnl_usdt) as total_pnl,
                   ROUND(SUM(CASE WHEN result = 'WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate
            FROM trades
            WHERE closed_at IS NOT NULL
            GROUP BY symbol
            HAVING COUNT(*) >= 2
            ORDER BY total_pnl ASC
            LIMIT ?
            ''', (limit,))
            worst = [dict(row) for row in cur.fetchall()]
            
            return {'best': best, 'worst': worst}
    
    def get_close_reason_statistics(self) -> List[Dict]:
        """Статистика по причинам закрытия"""
        with self.get_cursor() as cur:
            cur.execute('''
            SELECT close_reason,
                   COUNT(*) as total,
                   SUM(pnl_usdt) as total_pnl,
                   AVG(pnl_usdt) as avg_pnl,
                   AVG(duration_minutes) as avg_duration
            FROM trades
            WHERE closed_at IS NOT NULL AND close_reason IS NOT NULL
            GROUP BY close_reason
            ORDER BY total DESC
            ''')
            return [dict(row) for row in cur.fetchall()]
    
    def get_recent_ai_analyses(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        """Получить последние AI анализы"""
        with self.get_cursor() as cur:
            query = '''
            SELECT timestamp, symbol, ai_provider, action, confidence,
                   entry_price, sl_original, tp1, tp2, change_24h, atr_percent,
                   trade_opened, side
            FROM ai_analyses
            '''
            params = []
            if symbol:
                query += ' WHERE symbol LIKE ?'
                params.append(f'%{self._normalize_symbol(symbol)}%')
            query += ' ORDER BY timestamp DESC LIMIT ?'
            params.append(limit)
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    
    def get_pattern_analysis(self) -> Dict:
        """Анализ паттернов для поиска закономерностей"""
        patterns = {}
        
        # Паттерн 1: Лучшие часы для торговли
        patterns['best_hours'] = self.get_hourly_statistics()
        
        # Паттерн 2: Оптимальный confidence
        patterns['confidence_stats'] = self.get_confidence_statistics()
        
        # Паттерн 3: SHORT vs LONG
        patterns['side_stats'] = self.get_side_statistics()
        
        # Паттерн 4: Лучшие/худшие монеты
        patterns['coins'] = self.get_best_worst_coins()
        
        # Паттерн 5: Причины закрытия
        patterns['close_reasons'] = self.get_close_reason_statistics()
        
        return patterns

# Глобальный экземпляр

    # =========================================================================
    # STRATEGIES v5.6 - Управление стратегиями
    # =========================================================================
    
    def get_all_strategies(self) -> List[Dict]:
        """Получить все стратегии"""
        with self.get_cursor() as cur:
            cur.execute('SELECT * FROM strategies ORDER BY is_active DESC, created_at DESC')
            return [dict(row) for row in cur.fetchall()]
    
    def get_active_strategy(self) -> Optional[Dict]:
        """Получить активную стратегию"""
        with self.get_cursor() as cur:
            cur.execute('SELECT * FROM strategies WHERE is_active = 1 LIMIT 1')
            row = cur.fetchone()
            return dict(row) if row else None
    
    def get_strategy_by_name(self, name: str) -> Optional[Dict]:
        """Получить стратегию по имени"""
        with self.get_cursor() as cur:
            cur.execute('SELECT * FROM strategies WHERE name = ?', (name,))
            row = cur.fetchone()
            return dict(row) if row else None
    
    def save_strategy(self, name: str, params: Dict, description: str = None) -> bool:
        """Сохранить стратегию"""
        try:
            with self.get_cursor() as cur:
                cur.execute(
                    "INSERT OR REPLACE INTO strategies (name, description, stop_loss_pct, take_profit_pct, "
                    "trailing_activation_pct, trailing_distance_pct, trailing_enabled, ai_provider, "
                    "min_confidence, rsi_short_min, rsi_long_max, is_active, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)",
                    (name, description or f"Strategy {name}",
                     params.get('stop_loss_pct', 5.0), params.get('take_profit_pct', 7.0),
                     params.get('trailing_activation_pct', 2.0), params.get('trailing_distance_pct', 1.0),
                     params.get('trailing_enabled', True), params.get('ai_provider', 'mock'),
                     params.get('min_confidence', 70), params.get('rsi_short_min', 70),
                     params.get('rsi_long_max', 30)))
            logger.info(f"[DB] Strategy saved: {name}")
            return True
        except Exception as e:
            logger.error(f"[DB] Error saving strategy: {e}")
            return False
    
    def activate_strategy(self, name: str) -> bool:
        """Активировать стратегию"""
        try:
            with self.get_cursor() as cur:
                cur.execute('UPDATE strategies SET is_active = 0')
                cur.execute('UPDATE strategies SET is_active = 1 WHERE name = ?', (name,))
            logger.info(f"[DB] Strategy activated: {name}")
            return True
        except Exception as e:
            logger.error(f"[DB] Error activating strategy: {e}")
            return False
    
    def delete_strategy(self, name: str) -> bool:
        """Удалить стратегию"""
        try:
            with self.get_cursor() as cur:
                cur.execute('DELETE FROM strategies WHERE name = ?', (name,))
            logger.info(f"[DB] Strategy deleted: {name}")
            return True
        except Exception as e:
            logger.error(f"[DB] Error deleting strategy: {e}")
            return False
    
    def update_strategy_backtest(self, name: str, results: Dict) -> bool:
        """Обновить результаты бэктеста"""
        try:
            with self.get_cursor() as cur:
                cur.execute(
                    "UPDATE strategies SET backtest_trades=?, backtest_win_rate=?, "
                    "backtest_pnl=?, backtest_max_drawdown=?, backtest_date=CURRENT_TIMESTAMP WHERE name=?",
                    (results.get('trades', 0), results.get('win_rate', 0),
                     results.get('pnl', 0), results.get('max_drawdown', 0), name))
            return True
        except Exception as e:
            logger.error(f"[DB] Error updating backtest: {e}")
            return False
    
    def get_trade_statistics_for_agent(self, days: int = 30) -> Dict:
        """Статистика сделок для агента"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins, "
                "SUM(CASE WHEN result='LOSE' THEN 1 ELSE 0 END) as losses, "
                "SUM(pnl_usdt) as total_pnl, "
                "AVG(CASE WHEN result='WIN' THEN pnl_usdt ELSE NULL END) as avg_win, "
                "AVG(CASE WHEN result='LOSE' THEN pnl_usdt ELSE NULL END) as avg_loss "
                "FROM trades WHERE status='CLOSED' AND closed_at > datetime('now', ?)",
                (f'-{days} days',))
            row = cur.fetchone()
            total = row['total'] or 0
            wins = row['wins'] or 0
            return {
                'total_trades': total, 'wins': wins, 'losses': row['losses'] or 0,
                'win_rate': (wins / total * 100) if total > 0 else 0,
                'total_pnl': row['total_pnl'] or 0,
                'avg_win': row['avg_win'] or 0, 'avg_loss': row['avg_loss'] or 0,
                'days': days
            }

# Глобальный экземпляр
db = Database()
