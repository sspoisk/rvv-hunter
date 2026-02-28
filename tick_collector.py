#!/root/rvv_hunter/venv/bin/python3
# -*- coding: utf-8 -*-
"""
RVV Hunter — Tick Collector
Собирает тики с OKX WebSocket и агрегирует в 1-секундные микросвечи.
Хранит в SQLite: data/ticks.db

Использование:
  python tick_collector.py                  # топ-20 по объёму
  python tick_collector.py --pairs 50       # топ-50
  python tick_collector.py --symbols BTC ETH SOL  # конкретные
  python tick_collector.py --retention 14   # хранить 14 дней (по умолчанию 30)
"""

import json
import logging
import os
import sqlite3
import sys
import signal
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'ticks.db')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_PATH = os.path.join(BASE_DIR, 'logs', 'tick_collector.log')
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('tick_collector')

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    logger.error("websocket-client не установлен! pip install websocket-client")

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False
    logger.warning("ccxt не установлен, --symbols обязателен")


# ─── Database ───────────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    """Создать БД и таблицы."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS micro_candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            trade_count INTEGER NOT NULL,
            UNIQUE(symbol, timestamp)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mc_symbol_ts
        ON micro_candles(symbol, timestamp)
    """)
    conn.commit()
    return conn


def cleanup_old_data(conn: sqlite3.Connection, retention_days: int = 30):
    """Удалить записи старше retention_days."""
    cutoff_ms = int((datetime.utcnow() - timedelta(days=retention_days)).timestamp() * 1000)
    cursor = conn.execute("DELETE FROM micro_candles WHERE timestamp < ?", (cutoff_ms,))
    deleted = cursor.rowcount
    conn.commit()
    if deleted > 0:
        logger.info(f"Очистка: удалено {deleted} записей старше {retention_days} дней")
        conn.execute("PRAGMA optimize")


# ─── Fetch top pairs ────────────────────────────────────────────────────────

def fetch_top_pairs(count: int = 20) -> list:
    """Получить топ пар по объёму с OKX."""
    if not CCXT_AVAILABLE:
        logger.error("ccxt не доступен, используйте --symbols")
        return []

    try:
        exchange = ccxt.okx({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        tickers = exchange.fetch_tickers()

        pairs = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT:USDT'):
                continue
            vol = ticker.get('quoteVolume') or 0
            if vol == 0 and ticker.get('baseVolume') and ticker.get('last'):
                vol = float(ticker['baseVolume']) * float(ticker['last'])
            if vol > 0:
                clean = symbol.replace('/USDT:USDT', '')
                pairs.append({'symbol': clean, 'volume': vol})

        pairs.sort(key=lambda x: x['volume'], reverse=True)
        result = [p['symbol'] for p in pairs[:count]]
        logger.info(f"Топ-{count} пар: {', '.join(result[:10])}...")
        return result
    except Exception as e:
        logger.error(f"Ошибка получения пар: {e}")
        return []


# ─── Tick Aggregator ────────────────────────────────────────────────────────

class TickAggregator:
    """Агрегирует тики в 1-секундные OHLCV микросвечи."""

    def __init__(self, db_conn: sqlite3.Connection):
        self.conn = db_conn
        self.lock = threading.Lock()
        # Буфер: symbol -> {ts, open, high, low, close, volume, count}
        self.buffer: Dict[str, Dict] = {}
        self.stats = {
            'ticks_received': 0,
            'candles_flushed': 0,
            'flush_errors': 0,
            'last_tick_time': None,
        }

    def on_trade(self, symbol: str, price: float, size: float, ts_ms: int):
        """Обработать один тик (трейд)."""
        self.stats['ticks_received'] += 1
        self.stats['last_tick_time'] = datetime.utcnow()

        # Округляем timestamp до начала секунды
        sec_ts = ts_ms - (ts_ms % 1000)

        with self.lock:
            key = symbol
            if key not in self.buffer or self.buffer[key]['ts'] != sec_ts:
                # Если есть предыдущая свеча — она уйдёт при flush
                if key in self.buffer and self.buffer[key]['ts'] != sec_ts:
                    pass  # flush подберёт
                # Новая секунда
                if key not in self.buffer or self.buffer[key]['ts'] != sec_ts:
                    if key in self.buffer:
                        # Сохраняем старую в pending
                        self._stage_candle(key)
                    self.buffer[key] = {
                        'ts': sec_ts,
                        'open': price,
                        'high': price,
                        'low': price,
                        'close': price,
                        'volume': size,
                        'count': 1,
                    }
            else:
                buf = self.buffer[key]
                buf['high'] = max(buf['high'], price)
                buf['low'] = min(buf['low'], price)
                buf['close'] = price
                buf['volume'] += size
                buf['count'] += 1

    _pending: list = []

    def _stage_candle(self, symbol: str):
        """Переместить завершённую свечу в pending для flush."""
        if symbol in self.buffer:
            buf = self.buffer[symbol]
            self._pending.append((
                symbol, buf['ts'], buf['open'], buf['high'],
                buf['low'], buf['close'], buf['volume'], buf['count']
            ))

    def flush(self):
        """Сбросить все завершённые свечи в SQLite."""
        with self.lock:
            # Переносим все свечи кроме текущей секунды
            now_sec = int(time.time() * 1000)
            now_sec = now_sec - (now_sec % 1000)

            to_flush = list(self._pending)
            self._pending = []

            # Свечи текущих символов, если они уже не текущей секунды
            for sym, buf in list(self.buffer.items()):
                if buf['ts'] < now_sec:
                    to_flush.append((
                        sym, buf['ts'], buf['open'], buf['high'],
                        buf['low'], buf['close'], buf['volume'], buf['count']
                    ))
                    del self.buffer[sym]

        if not to_flush:
            return

        try:
            self.conn.executemany(
                "INSERT OR IGNORE INTO micro_candles "
                "(symbol, timestamp, open, high, low, close, volume, trade_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                to_flush
            )
            self.conn.commit()
            self.stats['candles_flushed'] += len(to_flush)
        except Exception as e:
            self.stats['flush_errors'] += 1
            logger.error(f"Flush error: {e}")


# ─── WebSocket Collector ────────────────────────────────────────────────────

class TickCollector:
    """Подключается к OKX WebSocket trades и собирает тики."""

    WS_URL = "wss://ws.okx.com:8443/ws/v5/public"

    def __init__(self, symbols: list, aggregator: TickAggregator):
        self.symbols = symbols
        self.aggregator = aggregator
        self.ws: Optional[websocket.WebSocketApp] = None
        self.running = False
        self.connected = False
        self.reconnect_count = 0
        self.max_reconnects = 50
        self._flush_thread: Optional[threading.Thread] = None

    def start(self):
        """Запустить сборщик."""
        if not WEBSOCKET_AVAILABLE:
            logger.error("websocket-client не установлен!")
            return

        self.running = True

        # Flush thread — каждую секунду
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

        # WebSocket loop
        self._ws_loop()

    def stop(self):
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def _flush_loop(self):
        """Периодический flush буфера в SQLite."""
        while self.running:
            time.sleep(1)
            try:
                self.aggregator.flush()
            except Exception as e:
                logger.error(f"Flush loop error: {e}")

    def _ws_loop(self):
        """Основной цикл WebSocket с реконнектом."""
        # Убираем прокси из окружения
        saved_env = {}
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                     'ALL_PROXY', 'all_proxy', 'SOCKS_PROXY', 'socks_proxy'):
            if key in os.environ:
                saved_env[key] = os.environ.pop(key)

        try:
            while self.running:
                try:
                    logger.info(f"Подключаемся к OKX WebSocket ({len(self.symbols)} символов)...")

                    self.ws = websocket.WebSocketApp(
                        self.WS_URL,
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close,
                        on_open=self._on_open
                    )
                    self.ws.run_forever(ping_interval=20, ping_timeout=10)

                except Exception as e:
                    logger.error(f"WS loop error: {e}")

                if self.running:
                    self.reconnect_count += 1
                    if self.reconnect_count > self.max_reconnects:
                        logger.warning("Лимит реконнектов, пауза 5 мин...")
                        self.reconnect_count = 0
                        time.sleep(300)
                        continue
                    delay = min(5 * self.reconnect_count, 60)
                    logger.info(f"Реконнект через {delay}с (попытка {self.reconnect_count})")
                    time.sleep(delay)
        finally:
            os.environ.update(saved_env)

    def _on_open(self, ws):
        self.connected = True
        self.reconnect_count = 0
        logger.info(f"WebSocket подключён, подписываемся на {len(self.symbols)} символов...")

        # OKX trades: подписка батчами (max ~240 args)
        batch_size = 50
        for i in range(0, len(self.symbols), batch_size):
            batch = self.symbols[i:i + batch_size]
            args = [{"channel": "trades", "instId": f"{sym}-USDT-SWAP"} for sym in batch]
            msg = json.dumps({"op": "subscribe", "args": args})
            ws.send(msg)
            logger.info(f"  Подписка отправлена: {len(args)} инструментов")

    def _on_close(self, ws, code, msg):
        self.connected = False
        logger.warning(f"WebSocket отключён: {code} - {msg}")

    def _on_error(self, ws, error):
        logger.error(f"WebSocket ошибка: {error}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)

            # Пропускаем события подписки
            if 'event' in data:
                if data.get('event') == 'error':
                    logger.error(f"OKX error: {data}")
                return

            trades = data.get('data', [])
            for trade in trades:
                # OKX trade format: {instId, tradeId, px, sz, side, ts}
                inst_id = trade.get('instId', '')
                if not inst_id:
                    continue

                symbol = inst_id.split('-')[0]  # BTC-USDT-SWAP → BTC
                price = float(trade.get('px', 0))
                size = float(trade.get('sz', 0))
                ts = int(trade.get('ts', 0))

                if price > 0 and size > 0 and ts > 0:
                    self.aggregator.on_trade(symbol, price, size, ts)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"Message error: {e}")


# ─── Status reporter ────────────────────────────────────────────────────────

def status_reporter(aggregator: TickAggregator, collector: TickCollector, interval: int = 60):
    """Периодически логирует статистику."""
    while collector.running:
        time.sleep(interval)
        stats = aggregator.stats
        logger.info(
            f"[STATUS] ticks={stats['ticks_received']:,} | "
            f"candles={stats['candles_flushed']:,} | "
            f"errors={stats['flush_errors']} | "
            f"connected={collector.connected} | "
            f"last_tick={stats['last_tick_time']}"
        )
        # DB size
        try:
            if os.path.exists(DB_PATH):
                size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
                logger.info(f"[STATUS] DB size: {size_mb:.1f} MB")
        except Exception:
            pass


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='RVV Hunter Tick Collector')
    parser.add_argument('--pairs', type=int, default=20, help='Кол-во топ пар (default: 20)')
    parser.add_argument('--symbols', nargs='+', help='Конкретные символы (BTC ETH SOL)')
    parser.add_argument('--retention', type=int, default=30, help='Хранить N дней (default: 30)')
    parser.add_argument('--status-interval', type=int, default=60, help='Интервал статуса в секундах')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("RVV Hunter — Tick Collector")
    logger.info("=" * 60)

    # Инициализация БД
    conn = init_db()
    logger.info(f"БД инициализирована: {DB_PATH}")

    # Очистка старых данных
    cleanup_old_data(conn, args.retention)

    # Получаем символы
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = fetch_top_pairs(args.pairs)
        if not symbols:
            # Fallback
            symbols = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX',
                        'DOT', 'LINK', 'MATIC', 'UNI', 'SHIB', 'LTC', 'BCH',
                        'NEAR', 'APT', 'ARB', 'OP', 'FIL', 'ATOM']
            logger.warning(f"Используем fallback список: {len(symbols)} пар")

    logger.info(f"Символы ({len(symbols)}): {', '.join(symbols)}")

    # Агрегатор
    aggregator = TickAggregator(conn)

    # Коллектор
    collector = TickCollector(symbols, aggregator)

    # Graceful shutdown
    def shutdown(sig, frame):
        logger.info(f"Получен сигнал {sig}, останавливаемся...")
        collector.stop()
        # Финальный flush
        aggregator.flush()
        conn.close()
        logger.info("Остановлен.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Status reporter thread
    status_thread = threading.Thread(
        target=status_reporter,
        args=(aggregator, collector, args.status_interval),
        daemon=True
    )
    status_thread.start()

    # Запуск (блокирующий)
    collector.start()


if __name__ == '__main__':
    main()
