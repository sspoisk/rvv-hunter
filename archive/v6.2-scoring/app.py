import os
import sys
import time
import logging
import threading
import json
import math
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Set

# ============================================================================
# .ENV LOADER (без зависимостей — загружает переменные из .env файла)
# ============================================================================
def _load_dotenv(path='.env'):
    """Загрузка переменных окружения из .env файла"""
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if not os.environ.get(key):  # Не перезаписываем существующие
                    os.environ[key] = value
    except Exception:
        pass

_load_dotenv()

import ccxt
from flask import Flask, render_template, jsonify, request, Response, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from ai_engine import create_ai_engine, MultiAIEngine
from trader import VirtualTrader
from database import db, get_gmt2_time, get_gmt2_str
from analytics import analytics
from history_loader import history_loader
from telegram_bot import telegram_bot
from binance_live import live_trader

# ============================================================================
# LOGGING SETUP (должен быть ДО импорта агента)
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Импорт агента после настройки логгера
try:
    from crypto_agent import CryptoAgentV3 as CryptoAgent
    AGENT_AVAILABLE = True
    logger.info("[AGENT] Crypto Agent V3 loaded (FULL AUTONOMY)")
except ImportError as e:
    AGENT_AVAILABLE = False
    logger.warning(f"Crypto Agent V3 not available: {e}")

# Импорт Smart Agent v5.0
try:
    from smart_agent import SmartAgent, create_smart_agent, get_smart_agent
    from agent_brain import brain as agent_brain
    from agent_tools import AgentTools
    SMART_AGENT_AVAILABLE = True
    logger.info("[SMART_AGENT] Smart Agent v5.0 loaded")
except ImportError as e:
    SMART_AGENT_AVAILABLE = False
    logger.warning(f"Smart Agent v5.0 not available: {e}")

# Импорт WebSocket Manager v5.9
try:
    from websocket_manager import BinanceWebSocketManager, get_ws_manager
    WEBSOCKET_AVAILABLE = True
    logger.info("[WS] WebSocket Manager loaded")
except ImportError as e:
    WEBSOCKET_AVAILABLE = False
    logger.warning(f"WebSocket Manager not available: {e}")

# Устаревшие модули (для совместимости)
SMART_AI_AVAILABLE = False
SMART_PM_AVAILABLE = False

# ============================================================================
# FLASK APP INITIALIZATION
# ============================================================================
app = Flask(__name__, template_folder='templates', static_folder='static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # For reverse proxy support
# Отключаем строгий CSP для работы inline скриптов
@app.after_request
def add_security_headers(response):
    # Разрешаем inline скрипты и eval для LightweightCharts
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://fonts.googleapis.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' ; connect-src 'self';"
    return response
# ============================================================================
# GLOBAL STATE MANAGEMENT
# ============================================================================
class AppState:
    def __init__(self):
        self.exchange = None
        self.ai_engine = None
        self.trader = None
        self.agent = None  # Единый Crypto Agent
        self.smart_agent = None  # Smart Agent v5.0
        self.ws_manager = None  # WebSocket Manager v5.9
        self.running = False
        self.last_scan_time = None
        self.scan_results: List[Dict] = []
        self.filtered_coins: List[Dict] = []
        self.candidates: Set[str] = set()  # Кандидаты для WebSocket
        self.lock = threading.Lock()
        self.health_status = {
            'binance': {'status': 'unknown', 'ping_ms': 0},
            'deepseek': {'status': 'unknown'},
            'groq': {'status': 'unknown'},
            'telegram': {'status': 'unknown'},
            'database': {'status': 'ok'},
            'smart_ai': {'status': 'unknown'},
            'smart_agent': {'status': 'unknown'},
            'websocket': {'status': 'unknown'}  # v5.9
        }
        self.market_prices = {}  # Кэш текущих цен
        self.last_price_update = datetime.utcnow()
        self.btc_trend_cache = None  # Кэш тренда Bitcoin
        self.btc_trend_last_update = None
        self.volume_analysis_cache = {}  # Кэш анализа объемов

state = AppState()
# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
def get_volume_analysis(symbol: str, timeframe: str = '15m') -> Dict:
    """Получить анализ объемов для символа"""
    if symbol in state.volume_analysis_cache:
        cache_time = state.volume_analysis_cache[symbol].get('timestamp', datetime.utcnow() - timedelta(minutes=5))
        if (datetime.utcnow() - cache_time).total_seconds() < 60:
            return state.volume_analysis_cache[symbol]
    try:
        # Получаем данные по объемам
        ohlcv = state.exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        if not ohlcv or len(ohlcv) < 20:
            return {}
        volumes = [candle[5] for candle in ohlcv]  # 5th index is volume
        closes = [candle[4] for candle in ohlcv]   # 4th index is close price
        # Рассчитываем аналитику объемов
        current_volume = volumes[-1]
        avg_volume = sum(volumes[-20:]) / 20
        volume_trend = ((current_volume / avg_volume) - 1) * 100 if avg_volume > 0 else 0
        # Анализ соотношения объем/цена
        price_changes = []
        volume_changes = []
        for i in range(1, len(closes)):
            price_change = (closes[i] - closes[i-1]) / closes[i-1] * 100
            volume_change = (volumes[i] - volumes[i-1]) / volumes[i-1] * 100 if volumes[i-1] > 0 else 0
            price_changes.append(price_change)
            volume_changes.append(volume_change)
        # Корреляция объемов и цены
        if len(price_changes) > 1 and len(volume_changes) > 1:
            mean_price = sum(price_changes) / len(price_changes)
            mean_volume = sum(volume_changes) / len(volume_changes)
            covariance = sum((p - mean_price) * (v - mean_volume) for p, v in zip(price_changes, volume_changes)) / len(price_changes)
            variance_price = sum((p - mean_price) ** 2 for p in price_changes) / len(price_changes)
            variance_volume = sum((v - mean_volume) ** 2 for v in volume_changes) / len(volume_changes)
            correlation = covariance / (math.sqrt(variance_price) * math.sqrt(variance_volume)) if variance_price > 0 and variance_volume > 0 else 0
        else:
            correlation = 0
        # Спайк объемов
        max_volume = max(volumes[-20:])
        volume_spike = current_volume > max_volume * 1.5
        analysis = {
            'current_volume': current_volume,
            'avg_volume': avg_volume,
            'volume_trend': volume_trend,
            'volume_correlation': correlation,
            'volume_spike': volume_spike,
            'volume_change_24h': (volumes[-1] / volumes[0] - 1) * 100 if volumes[0] > 0 else 0,
            'timestamp': datetime.utcnow()
        }
        state.volume_analysis_cache[symbol] = analysis
        return analysis
    except Exception as e:
        logger.error(f"[VOLUME] Error analyzing {symbol}: {e}")
        return {}
# ============================================================================
# CORE SYSTEM INITIALIZATION
# ============================================================================
def load_config() -> Dict:
    """Загрузка конфигурации"""
    config = {}
    # 1. Из config.json
    if os.path.exists('config.json'):
        try:
            with open('config.json', 'r') as f:
                config = json.load(f)
            logger.info(f"[CONFIG] Загружена конфигурация из config.json")
        except Exception as e:
            logger.warning(f"[CONFIG] Ошибка чтения config.json: {e}")
    # 2. Из переменных окружения
    env_keys = ['DEEPSEEK_API_KEY', 'GROQ_API_KEY', 'BINANCE_API_KEY',
                'BINANCE_SECRET_KEY', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'PORT']
    for key in env_keys:
        if os.getenv(key):
            config[key.lower()] = os.getenv(key)
            logger.debug(f"[CONFIG] Загружена переменная окружения: {key.lower()}")
    # 3. Дополнительные параметры по умолчанию
    if 'port' not in config:
        config['port'] = 8080
    if 'debug' not in config:
        config['debug'] = False
    return config

def _rotate_tor_circuit():
    """Запрашиваем новую цепочку через контрол-порт Tor (NEWNYM)"""
    try:
        import socket as _socket
        # Читаем cookie-файл для аутентификации
        cookie_path = '/run/tor/control.authcookie'
        with open(cookie_path, 'rb') as f:
            cookie = f.read().hex()
        s = _socket.socket()
        s.connect(('127.0.0.1', 9051))
        s.sendall(f'AUTHENTICATE {cookie}\r\nSIGNAL NEWNYM\r\nQUIT\r\n'.encode())
        resp = s.recv(256).decode(errors='ignore')
        s.close()
        if '250' in resp:
            logger.info("[TOR] Новая цепочка запрошена через NEWNYM")
        else:
            logger.warning(f"[TOR] Неожиданный ответ контрол-порта: {resp.strip()}")
    except Exception as e:
        logger.warning(f"[TOR] Контрол-порт недоступен, используем SIGHUP: {e}")
        try:
            import subprocess
            result = subprocess.run(['pgrep', '-x', 'tor'], capture_output=True, text=True)
            pid = int(result.stdout.strip().split('\n')[0])
            import signal as _sig, os as _os
            _os.kill(pid, _sig.SIGHUP)
        except Exception:
            pass
    time.sleep(12)  # ждём пока новая Tor-цепочка поднимется

def _is_tor_blocked_error(e: Exception) -> bool:
    """Проверяем — это 451 или гео-блокировка от Binance?"""
    err = str(e)
    return any(code in err for code in ['451', '403', 'Forbidden', 'restricted location',
                                         'blocked', 'CloudFront', 'ExchangeNotAvailable'])

def _exchange_with_retry(func, *args, max_retries: int = 3, **kwargs):
    """
    Обёртка для вызовов exchange с авторетраем.
    - При блокировке Tor (403/451) — ротируем цепочку и повторяем
    - При сетевых ошибках (таймаут, обрыв) — повторяем с задержкой
    """
    import ccxt as _ccxt
    use_tor = os.getenv('USE_TOR', '1') != '0'
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt >= max_retries:
                raise
            if use_tor and _is_tor_blocked_error(e):
                logger.warning(f"[TOR] Блокировка при попытке {attempt}/{max_retries}, ротируем цепочку...")
                _rotate_tor_circuit()
            elif isinstance(e, (_ccxt.NetworkError, _ccxt.RequestTimeout, _ccxt.ExchangeNotAvailable, ConnectionError, TimeoutError)):
                delay = min(5 * attempt, 30)
                logger.warning(f"[NET] Сетевая ошибка при попытке {attempt}/{max_retries}: {str(e)[:80]}. Пауза {delay}с...")
                time.sleep(delay)
            else:
                raise
    raise last_err

def init_exchange():
    """Инициализация биржи (OKX или Binance) по config.json → exchange"""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    exchange_id = 'binance'
    try:
        with open(config_path, 'r') as f:
            cfg = json.load(f)
        exchange_id = cfg.get('exchange', 'binance').lower()
    except Exception:
        pass

    if exchange_id == 'okx':
        return _init_okx()
    else:
        return _init_binance()


def _init_okx():
    """Инициализация OKX — работает из США без прокси"""
    exchange_config = {
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
        },
        'timeout': 30000,
    }
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            exchange = ccxt.okx(exchange_config)
            exchange.load_markets(reload=True)
            swap_count = sum(1 for s in exchange.symbols if ':USDT' in s)
            logger.info(f"[EXCHANGE] OKX Swap инициализирован: {swap_count} USDT пар (попытка {attempt})")
            return exchange
        except Exception as e:
            logger.error(f"[EXCHANGE] OKX попытка {attempt}/{max_attempts}: {e}")
            if attempt >= max_attempts:
                raise
            time.sleep(5)


def _init_binance():
    """Инициализация Binance Futures с Tor прокси"""
    use_tor = os.getenv('USE_TOR', '1') != '0'
    exchange_config = {
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',
            'adjustForTimeDifference': True,
            'recvWindow': 10000,
        },
        'timeout': 30000,
        'rateLimit': 200,
    }
    if use_tor:
        exchange_config['proxies'] = {
            'http': 'socks5h://127.0.0.1:9050',
            'https': 'socks5h://127.0.0.1:9050',
        }
        logger.info("[EXCHANGE] Tor SOCKS5 прокси включён (127.0.0.1:9050)")

    max_attempts = 8 if use_tor else 2
    for attempt in range(1, max_attempts + 1):
        try:
            exchange = ccxt.binance(exchange_config)
            exchange.load_markets(reload=True)
            logger.info(f"[EXCHANGE] Binance Futures инициализирован: {len(exchange.symbols)} пар (попытка {attempt})")
            return exchange
        except Exception as e:
            err = str(e)
            is_blocked = any(code in err for code in ['403', '451', 'Forbidden', 'blocked', 'CloudFront'])
            if use_tor and is_blocked and attempt < max_attempts:
                logger.warning(f"[EXCHANGE] Попытка {attempt}: Tor-узел заблокирован, меняем цепочку...")
                _rotate_tor_circuit()
            else:
                logger.error(f"[EXCHANGE] Попытка {attempt} провалилась: {e}")
                if attempt >= max_attempts:
                    raise

def init_ai_engine(settings: Dict):
    """Инициализация AI движка"""
    deepseek_key = settings.get('deepseek_api_key', '')
    groq_key = settings.get('groq_api_key', '')
    ai_provider = settings.get('ai_provider', 'mock')
    ab_mode = settings.get('ai_ab_mode', False)
    consensus = settings.get('ai_consensus_required', False)
    # Подробные логи для отладки
    logger.info(f"[AI] ===== ИНИЦИАЛИЗАЦИЯ AI =====")
    logger.info(f"[AI] Провайдер: {ai_provider}")
    logger.info(f"[AI] DeepSeek ключ: {'ЕСТЬ (' + deepseek_key[:10] + '...)' if deepseek_key else 'НЕТ'}")
    logger.info(f"[AI] Groq ключ: {'ЕСТЬ (' + groq_key[:10] + '...)' if groq_key else 'НЕТ'}")
    logger.info(f"[AI] A/B режим: {ab_mode}, Консенсус: {consensus}")
    if ab_mode or consensus:
        mode = 'consensus' if consensus else 'ab'
    else:
        mode = ai_provider
    engine = create_ai_engine(
        deepseek_key=deepseek_key,
        groq_key=groq_key,
        mode=mode
    )
    
    # Устанавливаем режим торговли
    trading_style = settings.get('trading_style', 'normal')
    if hasattr(engine, 'set_trading_style'):
        engine.set_trading_style(trading_style)
    elif hasattr(engine, 'engines'):
        for e in engine.engines.values():
            if hasattr(e, 'set_trading_style'):
                e.set_trading_style(trading_style)
    
    # Показываем какие движки реально подключены
    if hasattr(engine, 'engines'):
        active_engines = list(engine.engines.keys())
        logger.info(f"[AI] Подключенные движки: {active_engines}")
        if 'mock' in active_engines and len(active_engines) == 1:
            logger.warning("[AI] ВНИМАНИЕ: Работает только MOCK! Проверьте API ключи!")
    logger.info(f"[AI] ===== AI ГОТОВ (режим: {mode}) =====")
    return engine

def init_agent(settings: Dict):
    """Инициализация Crypto Agent V3 с полной автономностью"""
    if not AGENT_AVAILABLE:
        logger.warning("[AGENT] Module not available")
        return None
    
    if not settings.get('agent_enabled', False):
        logger.info("[AGENT] Disabled in settings")
        return None
    
    deepseek_key = settings.get('deepseek_api_key', '')
    if not deepseek_key:
        logger.warning("[AGENT] No DeepSeek API key")
        return None
    
    groq_key = settings.get('groq_api_key', '')
    
    try:
        # Telegram callback
        def telegram_callback(message: str):
            if telegram_bot.enabled:
                telegram_bot.send_message(message, parse_mode='Markdown')
        
        # Trader callbacks для полной автономности
        trader_callbacks = {
            'close_position': lambda trade_id, reason: state.trader.close_position_manual(trade_id, reason) if state.trader else None,
            'partial_close': lambda trade_id, pct: state.trader.partial_close(trade_id, pct) if state.trader else None,
            'adjust_sl': lambda trade_id, new_sl: state.trader.update_stop_loss(trade_id, new_sl) if state.trader else False,
            'adjust_tp': lambda trade_id, new_tp: state.trader.update_take_profit(trade_id, new_tp) if state.trader else False,
            'set_breakeven': lambda trade_id: state.trader.set_breakeven(trade_id) if state.trader else False,
            'toggle_trailing': lambda trade_id, enabled: state.trader.toggle_trailing(trade_id, enabled) if state.trader else False,
            'pause_scanner': lambda: state.trader.pause_scanner() if state.trader else None,
            'resume_scanner': lambda: state.trader.resume_scanner() if state.trader else None,
            'get_positions': lambda: state.trader.get_open_positions() if state.trader else [],
        }
        
        # Create Agent V3
        from crypto_agent import CryptoAgentV3
        agent = CryptoAgentV3(
            deepseek_key=deepseek_key,
            groq_key=groq_key,
            trader_callbacks=trader_callbacks,
            telegram_callback=telegram_callback
        )
        
        # Apply settings
        agent.update_settings({
            'mode': settings.get('agent_mode', 'auto'),
            'aggressiveness': settings.get('agent_aggressiveness', 2),
            'validate_signals': settings.get('agent_validate_signals', True),
            'learn_from_mistakes': settings.get('agent_learn', True),
            'min_position_age_minutes': settings.get('agent_min_age', 10),
            'profit_to_protect_percent': settings.get('agent_profit_protect', 3.0),
            'drawdown_trigger_percent': settings.get('agent_drawdown_trigger', 2.0),
            'position_cooldown_minutes': settings.get('agent_cooldown', 5),
        })
        
        # Запускаем агент
        agent.start()
        logger.info("[AGENT] 🤖 Crypto Agent V3 started (FULL AUTONOMY)")
        
        state.health_status['agent'] = {'status': 'ok', 'version': 'v3'}
        return agent
    except Exception as e:
        logger.error(f"[AGENT] Init error: {e}")
        import traceback
        traceback.print_exc()
        state.health_status['agent'] = {'status': 'error', 'message': str(e)}
        return None


def init_telegram(settings: Dict):
    """Инициализация Telegram"""
    token = settings.get('telegram_bot_token', '')
    chat_id = settings.get('telegram_chat_id', '')
    if token and chat_id:
        telegram_bot.configure(token, chat_id)
        telegram_bot.update_settings({
            'notify_open': settings.get('telegram_notify_open', True),
            'notify_close': settings.get('telegram_notify_close', True),
            'notify_trailing': settings.get('telegram_notify_trailing', False),
            'notify_errors': settings.get('telegram_notify_errors', True),
            'notify_signals': settings.get('telegram_notify_signals', True),
            'notify_analysis': settings.get('telegram_notify_analysis', False)
        })
        logger.info("[TELEGRAM] Bot configured")
        # Тест подключения
        telegram_bot.send_message("📱 Telegram бот запущен!")
    else:
        logger.info("[TELEGRAM] Not configured (no token/chat_id)")

def init_live_trading(settings: Dict):
    """Инициализация реальной торговли"""
    api_key = settings.get('binance_api_key', '')
    api_secret = settings.get('binance_secret_key', '')
    if api_key and api_secret:
        live_trader.configure(api_key, api_secret, testnet=False)
        # Тест подключения
        success, message, info = live_trader.test_connection()
        if success:
            logger.info(f"[LIVE] Trading configured, balance: ${info.get('total_usdt_balance', 0):.2f}")
        else:
            logger.error(f"[LIVE] Connection test failed: {message}")
    else:
        logger.info("[LIVE] Not configured (no API keys)")

# ============================================================================
# BITCOIN TREND ANALYSIS - УЛУЧШЕННАЯ ВЕРСИЯ v2.0
# ============================================================================
def _calculate_ema(closes: list, period: int) -> float:
    """Рассчёт EMA"""
    if len(closes) < period:
        return closes[-1] if closes else 0
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def _count_trend_candles(closes: list, period: int = 12) -> tuple:
    """Подсчёт свечей роста/падения за период"""
    if len(closes) < period + 1:
        return 0, 0, 0
    up = 0
    down = 0
    for i in range(-period, 0):
        if closes[i] > closes[i-1]:
            up += 1
        elif closes[i] < closes[i-1]:
            down += 1
    return up, down, period

def get_btc_trend() -> Dict:
    """
    Получить тренд Bitcoin с улучшенной логикой
    Использует: RSI, EMA, направление свечей, мульти-таймфрейм
    """
    now = datetime.utcnow()
    # Проверяем кэш (30 секунд для более актуальных данных)
    if state.btc_trend_cache and state.btc_trend_last_update:
        cache_age = (now - state.btc_trend_last_update).total_seconds()
        if cache_age < 30:
            return state.btc_trend_cache
    
    try:
        btc_symbol = 'BTC/USDT:USDT'
        
        # Получаем данные с ДВУХ таймфреймов (с ретраем при 451)
        ohlcv_15m = _exchange_with_retry(state.exchange.fetch_ohlcv, btc_symbol, '15m', limit=50, max_retries=10)
        ohlcv_1h = _exchange_with_retry(state.exchange.fetch_ohlcv, btc_symbol, '1h', limit=50, max_retries=10)
        
        if not ohlcv_1h or len(ohlcv_1h) < 15:
            logger.warning("[BTC TREND] Insufficient data")
            return _create_default_btc_trend()
        
        closes_15m = [c[4] for c in ohlcv_15m] if ohlcv_15m else []
        closes_1h = [c[4] for c in ohlcv_1h]
        current_price = closes_1h[-1]
        
        # === ИНДИКАТОРЫ ===
        rsi_1h = _calculate_rsi(closes_1h[-15:])
        rsi_15m = _calculate_rsi(closes_15m[-15:]) if len(closes_15m) >= 15 else rsi_1h
        
        # EMA для определения направления тренда
        ema_9 = _calculate_ema(closes_1h, 9)
        ema_21 = _calculate_ema(closes_1h, 21)
        ema_trend = 'up' if ema_9 > ema_21 else 'down' if ema_9 < ema_21 else 'flat'
        
        # Подсчёт свечей роста/падения (последние 12 часов на 1h)
        up_candles, down_candles, total = _count_trend_candles(closes_1h, 12)
        candle_ratio = up_candles / max(total, 1)  # 0.0 - 1.0
        
        # Изменение за разные периоды
        ticker = _exchange_with_retry(state.exchange.fetch_ticker, btc_symbol, max_retries=10)
        change_24h = ticker.get('percentage', 0) or 0
        change_4h = ((closes_1h[-1] / closes_1h[-5]) - 1) * 100 if len(closes_1h) >= 5 else 0
        change_12h = ((closes_1h[-1] / closes_1h[-13]) - 1) * 100 if len(closes_1h) >= 13 else 0
        
        # === ОПРЕДЕЛЕНИЕ ТРЕНДА (улучшенная логика) ===
        trend = 'neutral'
        strength = 'weak'
        trend_confidence = 50
        trend_signals = []
        
        bullish_score = 0
        bearish_score = 0
        
        # 1. RSI сигналы (вес: 2)
        if rsi_1h > 60:
            bullish_score += 2
            trend_signals.append(f"RSI(1h)={rsi_1h:.0f}>60")
        elif rsi_1h > 50:
            bullish_score += 1
        elif rsi_1h < 40:
            bearish_score += 2
            trend_signals.append(f"RSI(1h)={rsi_1h:.0f}<40")
        elif rsi_1h < 50:
            bearish_score += 1
        
        # 2. EMA тренд (вес: 2)
        if ema_trend == 'up':
            bullish_score += 2
            trend_signals.append("EMA9>EMA21")
        elif ema_trend == 'down':
            bearish_score += 2
            trend_signals.append("EMA9<EMA21")
        
        # 3. Направление свечей (вес: 2)
        if candle_ratio > 0.65:  # >65% свечей вверх
            bullish_score += 2
            trend_signals.append(f"Candles:{up_candles}/{total}â†‘")
        elif candle_ratio > 0.55:
            bullish_score += 1
        elif candle_ratio < 0.35:  # >65% свечей вниз
            bearish_score += 2
            trend_signals.append(f"Candles:{down_candles}/{total}â†“")
        elif candle_ratio < 0.45:
            bearish_score += 1
        
        # 4. Изменение за 4ч (вес: 1)
        if change_4h > 1.0:
            bullish_score += 1
            trend_signals.append(f"4h:+{change_4h:.1f}%")
        elif change_4h < -1.0:
            bearish_score += 1
            trend_signals.append(f"4h:{change_4h:.1f}%")
        
        # 5. Изменение за 12ч (вес: 1)
        if change_12h > 1.5:
            bullish_score += 1
            trend_signals.append(f"12h:+{change_12h:.1f}%")
        elif change_12h < -1.5:
            bearish_score += 1
            trend_signals.append(f"12h:{change_12h:.1f}%")
        
        # 6. Изменение за 24ч (вес: до 3 — ОСНОВНОЙ индикатор для пользователя)
        if change_24h > 3.0:
            bullish_score += 3
            trend_signals.append(f"24h:+{change_24h:.1f}%🔥")
        elif change_24h > 1.5:
            bullish_score += 2
            trend_signals.append(f"24h:+{change_24h:.1f}%")
        elif change_24h > 0.5:
            bullish_score += 1
            trend_signals.append(f"24h:+{change_24h:.1f}%")
        elif change_24h < -3.0:
            bearish_score += 3
            trend_signals.append(f"24h:{change_24h:.1f}%🔥")
        elif change_24h < -1.5:
            bearish_score += 2
            trend_signals.append(f"24h:{change_24h:.1f}%")
        elif change_24h < -0.5:
            bearish_score += 1
            trend_signals.append(f"24h:{change_24h:.1f}%")
        
        # === ФИНАЛЬНОЕ РЕШЕНИЕ ===
        # Максимум: 11 очков в каждую сторону
        if bullish_score >= 5 and bullish_score > bearish_score + 2:
            trend = 'bullish'
            if bullish_score >= 7:
                strength = 'strong'
                trend_confidence = 85
            elif bullish_score >= 5:
                strength = 'moderate'
                trend_confidence = 70
        elif bearish_score >= 5 and bearish_score > bullish_score + 2:
            trend = 'bearish'
            if bearish_score >= 7:
                strength = 'strong'
                trend_confidence = 85
            elif bearish_score >= 5:
                strength = 'moderate'
                trend_confidence = 70
        elif bullish_score >= 3 and bullish_score > bearish_score:
            trend = 'bullish'
            strength = 'weak'
            trend_confidence = 55
        elif bearish_score >= 3 and bearish_score > bullish_score:
            trend = 'bearish'
            strength = 'weak'
            trend_confidence = 55
        
        # Сила тренда в % — используем change_24h (то что видно на дашборде)
        trend_pct = change_24h
        
        btc_trend = {
            'trend': trend,
            'strength': strength,
            'trend_pct': round(trend_pct, 3),  # Сила тренда = change_24h в %
            'price': current_price,  # ДОБАВЛЕНО!
            'rsi': rsi_1h,  # Для совместимости
            'rsi_1h': rsi_1h,
            'rsi_15m': rsi_15m,
            'ema_trend': ema_trend,
            'change_24h': change_24h,
            'change_12h': change_12h,
            'change_4h': change_4h,
            'up_candles': up_candles,
            'down_candles': down_candles,
            'bullish_score': bullish_score,
            'bearish_score': bearish_score,
            'trend_signals': trend_signals,
            'confidence': trend_confidence,
            'signal_impact': {'short_confidence_modifier': 0, 'long_confidence_modifier': 0},
            'timestamp': now.isoformat()
        }
        
        # Влияние на сигналы
        if trend == 'bullish':
            modifier = -10 if strength == 'weak' else -15 if strength == 'moderate' else -20
            btc_trend['signal_impact'] = {
                'short_confidence_modifier': modifier,
                'long_confidence_modifier': abs(modifier) // 2
            }
        elif trend == 'bearish':
            modifier = -10 if strength == 'weak' else -15 if strength == 'moderate' else -20
            btc_trend['signal_impact'] = {
                'short_confidence_modifier': abs(modifier) // 2,
                'long_confidence_modifier': modifier
            }
        
        # Кэшируем
        state.btc_trend_cache = btc_trend
        state.btc_trend_last_update = now
        
        signals_str = ', '.join(trend_signals[:3]) if trend_signals else 'нет явных'
        logger.info(f"[BTC TREND] {trend.upper()} ({strength}), 24h={change_24h:+.2f}%, Score: Bull={bullish_score} Bear={bearish_score}, Signals: {signals_str}")
        
        return btc_trend
        
    except Exception as e:
        logger.error(f"[BTC TREND] Error: {e}", exc_info=True)
        return _create_default_btc_trend()

def _create_default_btc_trend() -> Dict:
    """Создать нейтральный тренд по умолчанию"""
    return {
        'trend': 'neutral',
        'strength': 'stable',
        'trend_pct': 0.0,  # Сила тренда = change_24h
        'price': 0.0,  # Будет 0 если данные не получены
        'rsi': 50.0,
        'rsi_1h': 50.0,
        'change_24h': 0.0,
        'confidence': 100,
        'signal_impact': {
            'short_confidence_modifier': 0,
            'long_confidence_modifier': 0
        },
        'timestamp': datetime.utcnow().isoformat()
    }

def _calculate_rsi(closes: List[float], period: int = 14) -> float:
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
    
    # Расчёт RSI
    if losses <= 1e-12:
        rsi = 99.0  # Аномалия: только рост, ограничиваем до 99
    elif gains <= 1e-12:
        rsi = 1.0   # Аномалия: только падение, ограничиваем до 1
    else:
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
    
    # Ограничиваем диапазон 5-95 для фильтрации аномалий
    rsi = max(5.0, min(95.0, rsi))
    return rsi

def get_rsi_for_symbol(symbol: str, timeframe: str = '15m') -> Optional[float]:
    """Получить RSI для символа"""
    try:
        ohlcv = state.exchange.fetch_ohlcv(symbol, timeframe, limit=50)
        if not ohlcv or len(ohlcv) < 15:
            return None
        closes = [candle[4] for candle in ohlcv]
        return _calculate_rsi(closes)
    except Exception as e:
        logger.warning(f"[RSI] Error for {symbol}: {e}")
        return None

def get_macd_for_symbol(symbol: str) -> Optional[Dict]:
    """Получить MACD для символа"""
    try:
        ohlcv = state.exchange.fetch_ohlcv(symbol, '15m', limit=50)
        if not ohlcv or len(ohlcv) < 35:
            return None
        closes = [candle[4] for candle in ohlcv]
        
        # EMA расчёт
        def ema(data, period):
            result = [data[0]]
            k = 2 / (period + 1)
            for i in range(1, len(data)):
                result.append(data[i] * k + result[-1] * (1 - k))
            return result
        
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        macd_line = [ema12[i] - ema26[i] for i in range(len(closes))]
        signal_line = ema(macd_line, 9)
        histogram = macd_line[-1] - signal_line[-1]
        
        return {
            'macd': round(macd_line[-1], 6),
            'signal': round(signal_line[-1], 6),
            'histogram': round(histogram, 6),
            'trend': 'bullish' if histogram > 0 else 'bearish'
        }
    except Exception as e:
        logger.warning(f"[MACD] Error for {symbol}: {e}")
        return None

def get_volume_analysis(symbol: str) -> Optional[Dict]:
    """Получить анализ объёма"""
    try:
        ohlcv = state.exchange.fetch_ohlcv(symbol, '15m', limit=20)
        if not ohlcv or len(ohlcv) < 10:
            return None
        
        volumes = [candle[5] for candle in ohlcv]
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
        current_volume = volumes[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
        
        return {
            'current': current_volume,
            'average': avg_volume,
            'ratio': round(volume_ratio, 2),
            'spike': volume_ratio > 2.0,
            'trend': 'high' if volume_ratio > 1.5 else 'low' if volume_ratio < 0.5 else 'normal'
        }
    except Exception as e:
        logger.warning(f"[VOLUME] Error for {symbol}: {e}")
        return None

# ============================================================================
# MARKET DATA & PRICE MONITORING
# ============================================================================
def update_market_prices():
    """Обновление всех рыночных цен (с ретраем при 451)"""
    try:
        # Получаем все тикеры
        tickers = _exchange_with_retry(state.exchange.fetch_tickers, max_retries=10)
        # Создаем временный словарь для новых цен
        new_prices = {}
        for symbol, ticker in tickers.items():
            # Фильтруем только USDT пары
            if 'USDT' in symbol and ('/USDT' in symbol or ':USDT' in symbol):
                price = ticker.get('last')
                if price and isinstance(price, (int, float)) and price > 0:
                    # Нормализуем символ
                    if '/USDT:USDT' in symbol:
                        clean_symbol = symbol.replace('/USDT:USDT', '')
                    elif ':USDT' in symbol:
                        clean_symbol = symbol.split(':')[0]
                    elif '/USDT' in symbol:
                        clean_symbol = symbol.replace('/USDT', '')
                    else:
                        clean_symbol = symbol
                    new_prices[clean_symbol] = {
                        'price': float(price),
                        'change_24h': ticker.get('percentage', 0) or 0,
                        'volume': ticker.get('quoteVolume', 0) or 0,
                        'timestamp': datetime.utcnow().isoformat(),
                        'full_symbol': symbol
                    }
        # Обновляем кэш
        with state.lock:
            state.market_prices = new_prices
            state.last_price_update = datetime.utcnow()
        logger.debug(f"[PRICES] Обновлено {len(state.market_prices)} цен")
        return True
    except Exception as e:
        logger.error(f"[PRICES] Ошибка обновления цен: {e}")
        return False

def get_price_for_symbol(symbol: str) -> float:
    """Получение цены для конкретного символа"""
    try:
        # Нормализуем символ
        if '/USDT:USDT' in symbol:
            clean_symbol = symbol.replace('/USDT:USDT', '')
        elif ':USDT' in symbol:
            clean_symbol = symbol.split(':')[0]
        elif '/USDT' in symbol:
            clean_symbol = symbol.replace('/USDT', '')
        else:
            clean_symbol = symbol
        # Проверяем кэш
        if clean_symbol in state.market_prices:
            return state.market_prices[clean_symbol]['price']
        # Если нет в кэше, запрашиваем отдельно
        full_symbol = symbol
        if '/USDT' in symbol and not (symbol.endswith(':USDT') or '/USDT:USDT' in symbol):
            full_symbol = symbol + ':USDT'
        ticker = state.exchange.fetch_ticker(full_symbol)
        price = ticker.get('last')
        if price and isinstance(price, (int, float)) and price > 0:
            # Сохраняем в кэш
            state.market_prices[clean_symbol] = {
                'price': float(price),
                'change_24h': ticker.get('percentage', 0) or 0,
                'timestamp': datetime.utcnow().isoformat(),
                'full_symbol': full_symbol
            }
            return float(price)
    except Exception as e:
        logger.error(f"[PRICE] Ошибка получения цены для {symbol}: {e}")
        return 0.0

def update_open_positions():
    """Обновление цен для открытых позиций - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    try:
        if not state.trader:
            logger.warning("[POSITION] Трейдер не инициализирован")
            return
        # Получаем открытые позиции напрямую из трейдера
        try:
            if hasattr(state.trader, 'positions'):
                positions = list(state.trader.positions.values())
                open_positions = [p for p in positions if hasattr(p, 'status') and p.status == "OPEN"]
            elif hasattr(state.trader, 'get_open_positions'):
                open_positions = state.trader.get_open_positions()
            else:
                logger.error("[POSITION] Не могу получить открытые позиции")
                return
        except Exception as e:
            logger.error(f"[POSITION] Ошибка получения позиций: {e}")
            return
        if not open_positions:
            return
        logger.debug(f"[POSITION] Найдено {len(open_positions)} открытых позиций")
        # Собираем все цены в один словарь
        prices_to_update = {}
        for pos in open_positions:
            try:
                # Получаем символ позиции
                if hasattr(pos, 'symbol'):
                    symbol = pos.symbol
                elif isinstance(pos, dict) and 'symbol' in pos:
                    symbol = pos['symbol']
                else:
                    continue
                # Получаем текущую цену
                current_price = get_price_for_symbol(symbol)
                if current_price <= 0:
                    continue
                # Добавляем в общий словарь
                prices_to_update[symbol] = current_price
                # Обновляем анализ объемов для символа
                get_volume_analysis(symbol)
            except Exception as e:
                logger.error(f"[POSITION] Ошибка подготовки цены для {symbol}: {e}")
                continue
        # Обновляем все позиции ОДИН раз
        if prices_to_update:
            if hasattr(state.trader, 'update_positions'):
                state.trader.update_positions(prices_to_update)
                logger.debug(f"[POSITION] Обновлено {len(prices_to_update)} цен")
                
                # === CRYPTO AGENT ===
                # Агент получает данные ВСЕГДА (даже когда выключен) чтобы:
                # 1. В режиме observe видеть позиции
                # 2. При включении сразу иметь актуальные данные
                if state.agent:
                    # Обновляем контекст рынка
                    btc_trend = get_btc_trend()
                    state.agent.update_market(
                        btc_price=btc_trend.get('price', 0) if btc_trend else 0,
                        btc_trend=btc_trend.get('trend', 'neutral') if btc_trend else 'neutral'
                    )
                    
                    # Обновляем данные позиций в Agent
                    current_trader_ids = set()
                    for pos in open_positions:
                        try:
                            pos_id = pos.id if hasattr(pos, 'id') else None
                            if pos_id:
                                current_trader_ids.add(pos_id)
                            
                            # Полные данные позиции для Agent V3
                            pos_data = {
                                'id': pos_id,
                                'trade_id': pos_id,
                                'symbol': pos.symbol if hasattr(pos, 'symbol') else '',
                                'side': pos.side if hasattr(pos, 'side') else 'SHORT',
                                'entry_price': pos.entry_price if hasattr(pos, 'entry_price') else 0,
                                'current_price': pos.current_price if hasattr(pos, 'current_price') else 0,
                                'stop_loss': pos.stop_loss if hasattr(pos, 'stop_loss') else 0,
                                'take_profit_1': pos.take_profit_1 if hasattr(pos, 'take_profit_1') else 0,
                                'take_profit_2': pos.take_profit_2 if hasattr(pos, 'take_profit_2') else 0,
                                'trailing_stop': pos.trailing_stop if hasattr(pos, 'trailing_stop') else 0,
                                'trail_activated': pos.trail_activated if hasattr(pos, 'trail_activated') else False,
                                'size_usdt': pos.size_usdt if hasattr(pos, 'size_usdt') else 0,
                                'pnl_usdt': pos.pnl_usdt if hasattr(pos, 'pnl_usdt') else 0,
                                'pnl_percent': pos.pnl_percent if hasattr(pos, 'pnl_percent') else 0,
                                'opened_at': pos.opened_at if hasattr(pos, 'opened_at') else '',
                                'ai_confidence': pos.ai_confidence if hasattr(pos, 'ai_confidence') else 0,
                                'rsi_at_entry': pos.rsi_at_entry if hasattr(pos, 'rsi_at_entry') else 50,
                            }
                            if pos_data['id']:  # Только если есть ID
                                state.agent.track_position(pos_data)
                                state.agent.update_position(pos_data)
                        except Exception as e:
                            logger.warning(f"[AGENT] Position update error for {getattr(pos, 'symbol', '?')}: {e}")
                    
                    # СИНХРОНИЗАЦИЯ: удаляем из агента позиции которых нет у trader
                    with state.agent.positions_lock:
                        agent_ids = set(state.agent.positions.keys())
                        orphaned = agent_ids - current_trader_ids
                        for oid in orphaned:
                            del state.agent.positions[oid]
                            logger.info(f"[AGENT] 🧹 Removed orphaned position: {oid}")
            else:
                logger.error(f"[POSITION] У трейдера нет метода update_positions")
    except Exception as e:
        logger.error(f"[POSITION] Ошибка обновления позиций: {e}")

# ============================================================================
# MARKET SCANNING SYSTEM - ИСПРАВЛЕННАЯ ВЕРСИЯ ДЛЯ LONG/SHORT
# ============================================================================
def fetch_top_pairs(limit: int = 300) -> List[Dict]:
    """Топ пар по объёму (с ретраем при 451 от Tor-узла)"""
    try:
        tickers = _exchange_with_retry(state.exchange.fetch_tickers, max_retries=10)
        pairs = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT:USDT') or (symbol.endswith('USDT') and ':' in symbol):
                vol = ticker.get('quoteVolume', 0) or 0
                # OKX: quoteVolume=None, используем baseVolume × price
                if not vol:
                    base_vol = ticker.get('baseVolume', 0) or 0
                    price = ticker.get('last', 0) or 0
                    vol = base_vol * price
                if vol > 100000:
                    pairs.append({
                        'symbol': symbol,
                        'volume': vol,
                        'change_24h': ticker.get('percentage', 0) or 0,
                        'price': ticker.get('last', 0) or 0
                    })
        pairs.sort(key=lambda x: x['volume'], reverse=True)
        logger.info(f"[EXCHANGE] Получено {len(pairs[:limit])} пар")
        return pairs[:limit]
    except Exception as e:
        logger.error(f"[EXCHANGE] Ошибка получения данных: {e}")
        return []

def fetch_ohlcv_multi(symbol: str) -> Dict:
    """OHLCV для нескольких таймфреймов"""
    data = {}
    for tf in ['5m', '15m', '1h']:
        try:
            ohlcv = state.exchange.fetch_ohlcv(symbol, tf, limit=50)
            if ohlcv:
                data[tf] = {
                    'timestamp': [c[0] for c in ohlcv],
                    'open': [c[1] for c in ohlcv],
                    'high': [c[2] for c in ohlcv],
                    'low': [c[3] for c in ohlcv],
                    'close': [c[4] for c in ohlcv],
                    'volume': [c[5] for c in ohlcv]
                }
            time.sleep(0.1)
        except Exception as e:
            logger.debug(f"[EXCHANGE] OHLCV error {symbol} {tf}: {e}")
    return data

def get_prices(symbols: List[str]) -> Dict[str, float]:
    """Получение текущих цен"""
    prices = {}
    try:
        if not symbols:
            return prices
        tickers = state.exchange.fetch_tickers(symbols)
        for s, t in tickers.items():
            price = t.get('last')
            if price is not None and isinstance(price, (int, float)) and price > 0:
                prices[s] = float(price)
    except Exception as e:
        logger.error(f"[EXCHANGE] Ошибка получения цен: {e}")
    return prices

def scan_cycle():
    """Один цикл сканирования с поддержкой LONG и SHORT - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    logger.info("[SCAN] Запуск цикла сканирования...")
    if state.trader:
        state.trader._add_log("scan", "🔍 Запуск сканирования рынка...")
    
    # === 1. Получаем BTC тренд (КРИТИЧНО - нужен для фильтров и автозакрытия) ===
    btc_trend = None
    try:
        btc_trend = get_btc_trend()
        if state.trader:
            state.trader.btc_trend_data = btc_trend
            logger.debug(f"[SCAN] BTC trend: {btc_trend.get('trend')} ({btc_trend.get('change_24h', 0):+.1f}%)")
    except Exception as e:
        logger.error(f"[SCAN] Ошибка получения BTC тренда: {e}")
        btc_trend = {'trend': 'neutral', 'change_24h': 0, 'strength': 'weak', 'rsi_1h': 50, 'trend_pct': 0}
    
    # === 2. Автозакрытие позиций (ОТДЕЛЬНЫЙ блок - не зависит от постмортемов) ===
    try:
        if state.trader and btc_trend:
            # 2a. Автозакрытие при нейтрали
            if btc_trend.get('trend') == 'neutral':
                closed_neutral = state.trader.check_neutral_auto_close()
                if closed_neutral:
                    logger.info(f"[SCAN] Автозакрытие при нейтрали BTC: {closed_neutral}")
            
            # 2b. Автозакрытие при ослаблении тренда (v6.1)
            closed_weak = state.trader.check_trend_weakness_auto_close()
            if closed_weak:
                logger.info(f"[SCAN] Автозакрытие при ослаблении тренда: {closed_weak}")
    except Exception as e:
        logger.error(f"[SCAN] Ошибка автозакрытия: {e}", exc_info=True)
    
    # === 3. Загружаем посмертные анализы для обучения AI (не критично) ===
    try:
        postmortems = db.get_post_mortems(limit=20)
        stats_context = analytics.get_ai_context() if hasattr(analytics, 'get_ai_context') else ""
        btc_context = f"""\
===== ТЕКУЩИЙ ТРЕНД BITCOIN =====
Направление: {btc_trend.get('trend', 'neutral').upper()}
Сила: {btc_trend.get('strength', 'weak')}
RSI(1h): {btc_trend.get('rsi_1h', 50):.1f}
Изменение 24ч: {btc_trend.get('change_24h', 0):+.1f}%
"""
        if state.ai_engine and hasattr(state.ai_engine, 'set_learning_context'):
            state.ai_engine.set_learning_context(postmortems, stats_context, btc_context)
        if postmortems:
            logger.info(f"[SCAN] AI загрузил {len(postmortems)} посмертных анализов для обучения")
    except Exception as e:
        logger.error(f"[SCAN] Ошибка контекста обучения: {e}")
    
    try:
        pairs = fetch_top_pairs(300)
        if not pairs:
            logger.warning("[SCAN] Нет данных от биржи")
            return
        
        # ИСПРАВЛЕНО: Фильтруем монеты по абсолютному изменению для обоих направлений
        min_change = state.trader.settings.min_change_filter
        # Для SHORT ищем растущие монеты (перекупленность)
        short_candidates = [p for p in pairs if p['change_24h'] >= min_change]
        # Для LONG ищем падающие монеты (перепроданность)
        long_candidates = [p for p in pairs if p['change_24h'] <= -min_change]
        
        # Объединяем все кандидаты
        all_candidates = short_candidates + long_candidates
        all_candidates = all_candidates[:100]  # Ограничиваем общее количество
        
        with state.lock:
            state.filtered_coins = all_candidates
        
        # v5.9: Обновляем WebSocket символы
        try:
            update_websocket_symbols()
        except Exception as e:
            logger.debug(f"[WS] Update symbols error: {e}")
        
        logger.info(f"[SCAN] Найдено {len(short_candidates)} кандидатов для SHORT, {len(long_candidates)} для LONG")
        
        if state.trader:
            if len(all_candidates) > 0:
                top_short = short_candidates[:3] if short_candidates else []
                top_long = long_candidates[:3] if long_candidates else []
                
                short_list = ', '.join([f"{c['symbol'].replace('/USDT:USDT', '')} (+{c['change_24h']:.1f}%)" for c in top_short])
                long_list = ', '.join([f"{c['symbol'].replace('/USDT:USDT', '')} ({c['change_24h']:+.1f}%)" for c in top_long])
                
                msg = ""
                if short_list:
                    msg += f"📉 SHORT кандидаты: {short_list}"
                if long_list:
                    if msg: msg += "\n"
                    msg += f"📈 LONG кандидаты: {long_list}"
                
                if msg:
                    state.trader._add_log("scan", msg)
            else:
                state.trader._add_log("scan", f"❌ Нет кандидатов >={abs(min_change)}%")
        
        max_to_analyze = state.trader.settings.max_to_analyze if state.trader else 15
        analyzed_count = 0
        signals_found = {'SHORT': 0, 'LONG': 0}
        skip_reasons = {}  # Подсчёт причин пропуска для диагностики
        
        # ИСПРАВЛЕНО: итерируем ВСЕ кандидаты, но анализируем только max_to_analyze чистых
        # Заблокированные монеты НЕ считаются в лимит — они просто пропускаются
        # Безопасный лимит: не более 100 итераций (чтобы не крутить 500+ монет)
        max_iterations = min(len(all_candidates), max_to_analyze * 5, 100)
        
        # === НАСТРОЙКИ RECHECK (один раз перед циклом) ===
        try:
            with open('config.json', 'r', encoding='utf-8') as _cf:
                _cfg = json.load(_cf)
                _recheck_enabled = _cfg.get('filters', {}).get('recheck_change_at_open', True)
                _max_cache_age = _cfg.get('max_cache_age_seconds', 120)
        except Exception:
            _recheck_enabled = True
            _max_cache_age = 120
        
        for idx, coin in enumerate(all_candidates[:max_iterations]):
            # Достигнут лимит проанализированных — выходим
            if analyzed_count >= max_to_analyze:
                break
            
            symbol = coin['symbol']
            
            # Проверка черного списка (НЕ считаем в лимит)
            if db.is_blacklisted(symbol):
                skip_reasons['blacklist'] = skip_reasons.get('blacklist', 0) + 1
                continue
            
            # Проверка белого списка (НЕ считаем в лимит)
            settings = state.trader.get_settings() if state.trader else {}
            whitelist_enabled = settings.get('whitelist_enabled', False)
            whitelist_symbols = settings.get('whitelist_symbols', '[]')
            
            if whitelist_enabled:
                try:
                    whitelist = json.loads(whitelist_symbols) if isinstance(whitelist_symbols, str) else whitelist_symbols
                    clean_sym = symbol.replace('/USDT:USDT', '').replace('/USDT', '').replace(':USDT', '')
                    if whitelist and clean_sym not in whitelist:
                        skip_reasons['whitelist'] = skip_reasons.get('whitelist', 0) + 1
                        continue
                except Exception as e:
                    logger.warning(f"[SCAN] Ошибка парсинга whitelist: {e}")
            
            # Проверка открытой позиции (НЕ считаем в лимит)
            if any(p['symbol'] == symbol for p in state.trader.get_open_positions()):
                skip_reasons['already_open'] = skip_reasons.get('already_open', 0) + 1
                continue
            
            # ═══ КУЛДАУН ПО МОНЕТЕ (НЕ считаем в лимит) ═══
            sym_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
            max_sl = state.trader.settings.max_symbol_losses_daily
            daily_losses = state.trader.symbol_daily_losses.get(sym_clean, 0)
            if daily_losses >= max_sl:
                skip_reasons['daily_sl_limit'] = skip_reasons.get('daily_sl_limit', 0) + 1
                continue
            if sym_clean in state.trader.symbol_cooldowns:
                cd_end = state.trader.symbol_cooldowns[sym_clean] + timedelta(
                    minutes=state.trader.settings.symbol_cooldown_min)
                if get_gmt2_time() < cd_end:
                    skip_reasons['cooldown'] = skip_reasons.get('cooldown', 0) + 1
                    continue
            
            # Проверка лимитов (это РЕАЛЬНЫЙ лимит — прекращаем)
            can, msg = state.trader.can_open_position()
            if not can:
                logger.info(f"[SCAN] Достигнут лимит: {msg}")
                skip_reasons[f'limit:{msg}'] = skip_reasons.get(f'limit:{msg}', 0) + 1
                break
            
            analyzed_count += 1
            logger.info(f"[SCAN] [{analyzed_count}/{max_to_analyze}] Анализ {symbol} ({coin['change_24h']:+.1f}%)")
            if state.trader:
                state.trader._add_log("analysis", f"🔍 Анализ {symbol.replace('/USDT:USDT', '')} ({coin['change_24h']:+.1f}%)...")
            
            # Получаем OHLCV
            ohlcv = fetch_ohlcv_multi(symbol)
            if not ohlcv:
                logger.warning(f"[SCAN] Нет данных OHLCV для {symbol}")
                continue
            
            # === ФИЛЬТР ЗРЕЛОСТИ МОНЕТЫ (v6.3) ===
            # Защита от новых/неликвидных монет: мало свечей или нет движения цены
            _candles_15m = ohlcv.get('15m', {})
            _closes_15m = _candles_15m.get('close', [])
            _min_candles = 50  # Минимум 50 свечей = ~12 часов истории на 15m
            if len(_closes_15m) < _min_candles:
                _sym_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
                logger.info(f"[SCAN] {_sym_clean}: пропущен — мало свечей ({len(_closes_15m)} < {_min_candles}), монета новая/неликвидная")
                state.trader._add_log("filter", f"🚫 {_sym_clean}: новая монета, мало данных ({len(_closes_15m)} свечей)")
                skip_reasons['new_coin'] = skip_reasons.get('new_coin', 0) + 1
                continue
            # Проверяем есть ли реальное движение (не плоский рынок)
            if _closes_15m:
                _price_min = min(_closes_15m)
                _price_max = max(_closes_15m)
                _volatility = (_price_max - _price_min) / _price_min if _price_min > 0 else 0
                if _volatility < 0.002:  # < 0.2% движение за всю историю — мёртвая монета
                    _sym_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
                    logger.info(f"[SCAN] {_sym_clean}: пропущен — нет волатильности ({_volatility*100:.3f}% за {len(_closes_15m)} свечей)")
                    state.trader._add_log("filter", f"🚫 {_sym_clean}: нет движения цены ({_volatility*100:.3f}%), пропущена")
                    skip_reasons['no_volatility'] = skip_reasons.get('no_volatility', 0) + 1
                    continue
            
            # Получаем тренд Bitcoin
            btc_trend = get_btc_trend()
            
            # Передаём BTC тренд в trader (без circular import!)
            if state.trader:
                state.trader.btc_trend_data = btc_trend
            
            # AI анализ с учетом тренда Bitcoin
            signal = state.ai_engine.analyze_coin(symbol, ohlcv, coin['change_24h'], btc_trend)
            
            if signal:
                action = signal.get('action', 'WAIT')
                conf = signal.get('confidence', 0)
                
                # Сохраняем анализ в БД
                analysis_record = {
                    'symbol': symbol,
                    'ai_provider': signal.get('chosen_provider', signal.get('ai_provider', 'unknown')),
                    'action': action,
                    'confidence': conf,
                    'entry_price': signal.get('entry_price', 0),
                    'sl_original': signal.get('sl_original', signal.get('stop_loss', 0)),
                    'sl_corrected': signal.get('stop_loss', 0),
                    'sl_was_fixed': signal.get('sl_was_fixed', False),
                    'tp1': signal['take_profit'][0] if signal.get('take_profit') else 0,
                    'tp2': signal['take_profit'][1] if signal.get('take_profit') and len(signal['take_profit']) > 1 else 0,
                    'analysis_text': signal.get('analysis_raw', signal.get('analysis_ru', ''))[:5000],
                    'change_24h': coin['change_24h'],
                    'atr_percent': signal.get('atr_percent', 0),
                    'trade_opened': False,
                    'trade_id': '',
                    'btc_trend': json.dumps(btc_trend) if btc_trend else None,
                    'side': action if action in ['SHORT', 'LONG'] else 'WAIT'  # ДОБАВЛЕНО!
                }
                
                with state.lock:
                    state.scan_results.append(signal)
                    state.scan_results = state.scan_results[-50:]
                
                analysis_ru = signal.get('analysis_ru', '')
                
                # Логирование для отладки confidence
                threshold = state.trader.settings.confidence_threshold
                logger.info(f"[SCAN] {symbol}: action={action}, confidence={conf}%, threshold={threshold}%")
                
                if action in ['SHORT', 'LONG']:
                    signals_found[action] += 1
                    logger.info(f"[SCAN] ✅ НАЙДЕН {action}: {symbol} ({conf}%)")
                    
                    if state.trader:
                        short_analysis = analysis_ru[:300] + "..." if len(analysis_ru) > 300 else analysis_ru
                        state.trader._add_log("signal", f"🎯 {symbol}: {action} ({conf}%)")
                        state.trader._add_log("analysis", short_analysis)
                
                # Открываем позицию для SHORT или LONG
                if action in ['SHORT', 'LONG'] and conf >= state.trader.settings.confidence_threshold:
                    # === ПРОВЕРКА BTC ТРЕНДА (v6.0 гранулярный) ===
                    btc_blocked = False
                    btc_block_reason = ""
                    if state.trader.settings.btc_trend_filter_enabled:
                        btc_tr = btc_trend.get('trend', 'neutral')
                        btc_pct = abs(btc_trend.get('trend_pct', 0.0))
                        if btc_pct == 0:
                            btc_pct = abs(btc_trend.get('change_24h', 0.0))
                        
                        # Определяем режим с учётом мин. силы тренда
                        if btc_tr == 'bullish':
                            min_str = state.trader.settings.btc_bullish_min_strength
                            if min_str > 0 and btc_pct < min_str:
                                # Тренд слишком слабый → считаем нейтральным
                                mode = state.trader.settings.btc_neutral_mode
                            else:
                                mode = state.trader.settings.btc_bullish_mode
                        elif btc_tr == 'bearish':
                            min_str = state.trader.settings.btc_bearish_min_strength
                            if min_str > 0 and btc_pct < min_str:
                                mode = state.trader.settings.btc_neutral_mode
                            else:
                                mode = state.trader.settings.btc_bearish_mode
                        else:
                            mode = state.trader.settings.btc_neutral_mode
                            # any_incl_neutral из бычьего/медвежьего = разрешить всё при нейтрали
                            # НО: если нейтральный = 'none', не перезаписываем!
                            if mode not in ('any', 'any_incl_neutral', 'none'):
                                if state.trader.settings.btc_bullish_mode == 'any_incl_neutral' or state.trader.settings.btc_bearish_mode == 'any_incl_neutral':
                                    mode = 'any'
                        
                        # Проверяем направление по итоговому mode
                        if mode == 'none':
                            btc_blocked = True
                            btc_block_reason = f"Торговля запрещена: BTC {btc_tr} ({btc_pct:.2f}%) → режим 'не торговать'"
                        elif mode == 'long_only' and action == 'SHORT':
                            btc_blocked = True
                            btc_block_reason = f"SHORT заблокирован: BTC {btc_tr} ({btc_pct:.2f}%) → режим '{mode}'"
                        elif mode == 'short_only' and action == 'LONG':
                            btc_blocked = True
                            btc_block_reason = f"LONG заблокирован: BTC {btc_tr} ({btc_pct:.2f}%) → режим '{mode}'"
                    
                    if btc_blocked:
                        logger.info(f"[SCAN] â›” {btc_block_reason}")
                        if state.trader:
                            state.trader._add_log("btc_filter", f"â›” {btc_block_reason}")
                        continue
                    
                    # === SMART AI VALIDATION ===
                    smart_ai_blocked = False
                    smart_ai_reason = ""
                    if state.agent and state.agent.settings.validate_signals:
                        try:
                            validation = state.agent.validate_signal({
                                'symbol': symbol,
                                'side': action,
                                'direction': action,
                                'confidence': conf,
                                'reason': signal.get('reason', ''),
                                'rsi': signal.get('indicators', {}).get('rsi', 50),
                                'entry_price': signal.get('entry_price', 0),
                                'stop_loss_pct': signal.get('stop_loss_pct', 3),
                                'take_profit_1_pct': signal.get('take_profit_1_pct', 4)
                            })
                            
                            if not validation.get('approved', True):
                                smart_ai_blocked = True
                                smart_ai_reason = validation.get('reason', 'Agent rejected')
                                logger.info(f"[AGENT] â›” {symbol} {action} REJECTED: {smart_ai_reason}")
                                state.trader._add_log("agent", f"â›” {symbol}: {smart_ai_reason}")
                        except Exception as e:
                            logger.error(f"[AGENT] Validation error: {e}")
                    
                    if smart_ai_blocked:
                        continue
                    
                    logger.info(f"[SCAN] 🟢 ОТКРЫВАЕМ {action}: {symbol} ({conf}% >= {threshold}%)")
                    try:
                        # v6.3: Запоминаем change_24h В МОМЕНТ ОТКРЫТИЯ (может отличаться от сканирования)
                        symbol_clean_for_price = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
                        price_data = state.market_prices.get(symbol_clean_for_price, {})
                        if not isinstance(price_data, dict):
                            price_data = {}
                        
                        # Берём текущее change из кэша; fallback на значение скана
                        _cached_change = price_data.get('change_24h')
                        _scan_change = coin.get('change_24h', 0) or 0
                        real_change_now = _cached_change if _cached_change is not None else _scan_change
                        signal['change_24h_at_open'] = real_change_now
                        logger.info(f"[LATE_TRACK] {symbol}: scan={_scan_change:+.1f}%, at_open={real_change_now:+.1f}%, cache={'hit' if _cached_change is not None else 'miss'}")
                        
                        # === RECHECK CHANGE AT OPEN (v6.3) ===
                        # Настройки _recheck_enabled и _max_cache_age загружены перед циклом
                        if _recheck_enabled:
                            _min_ch = state.trader.settings.min_change_filter
                            # Проверяем кэш: нет данных → miss
                            if _cached_change is None:
                                logger.info(f"[RECHECK] {symbol}: cache=miss (нет данных), recheck пропущен")
                            else:
                                # Проверяем TTL кэша
                                _cache_ts = price_data.get('timestamp')
                                _cache_ok = False
                                _cache_status = 'hit'
                                if _cache_ts:
                                    try:
                                        _age = (datetime.utcnow() - datetime.fromisoformat(_cache_ts)).total_seconds()
                                        if _age <= _max_cache_age:
                                            _cache_ok = True
                                            _cache_status = f'hit ({_age:.0f}s)'
                                        else:
                                            _cache_status = f'stale ({_age:.0f}s > {_max_cache_age}s)'
                                            logger.warning(f"[RECHECK] {symbol}: cache=stale, данные {_age:.0f}с старые (лимит {_max_cache_age}с), recheck пропущен")
                                            state.trader._add_log("filter", f"⚠️ {symbol_clean_for_price}: кэш устарел ({_age:.0f}с), recheck пропущен")
                                    except Exception as _te:
                                        logger.warning(f"[RECHECK] {symbol}: ошибка парсинга timestamp: {_te}")
                                        _cache_status = 'error'
                                else:
                                    # timestamp отсутствует — старая запись без метки
                                    _cache_status = 'no-ts'
                                    logger.info(f"[RECHECK] {symbol}: cache без timestamp, recheck пропущен")
                                
                                logger.info(f"[RECHECK] {symbol}: cache={_cache_status}")
                                
                                if _cache_ok:
                                    _actual_ch = abs(real_change_now)
                                    if _actual_ch < _min_ch:
                                        logger.info(f"[RECHECK] {symbol}: ЗАБЛОКИРОВАН — change упал {_actual_ch:.1f}% < {_min_ch}%")
                                        state.trader._add_log("filter", f"⏰ {symbol_clean_for_price}: опоздавший вход пропущен ({_actual_ch:.1f}% < {_min_ch}%)")
                                        continue
                        
                        pos = state.trader.open_position(signal)
                        if pos:
                            symbol_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
                            entry = signal['entry_price']
                            sl = signal['stop_loss']
                            tp1 = signal['take_profit'][0]
                            tp2 = signal['take_profit'][1] if len(signal['take_profit']) > 1 else 0
                            
                            if action == 'SHORT':
                                emoji = "🔴"
                                hashtag = "#SHORT"
                            else:  # LONG
                                emoji = "🟢"
                                hashtag = "#LONG"
                            
                            telegram_msg = f"""{emoji} {action} {symbol_clean} @ ${entry:.6f}
SL: ${sl:.6f} | TP1: ${tp1:.6f} | TP2: ${tp2:.6f}
📊 АНАЛИЗ:
{analysis_ru}
{hashtag} #{symbol_clean.replace('/', '')}"""
                            
                            state.trader._add_log("trade_open", telegram_msg, {
                                "analysis": analysis_ru,
                                "symbol": symbol,
                                "confidence": conf,
                                "entry_price": entry,
                                "stop_loss": sl,
                                "take_profit": signal['take_profit'],
                                "action": action
                            })
                            
                            # Telegram уведомление
                            if telegram_bot.enabled:
                                telegram_bot.notify_position_opened(
                                    symbol=symbol,
                                    entry_price=entry,
                                    stop_loss=sl,
                                    take_profit=tp1,
                                    confidence=conf,
                                    change_24h=coin['change_24h'],
                                    ai_provider=signal.get('ai_provider', 'unknown'),
                                    trade_mode=state.trader.settings.trade_mode,
                                    action=action
                                )
                            
                            # === CRYPTO AGENT: Уведомляем сразу при открытии ===
                            if state.agent:
                                try:
                                    pos_data = {
                                        'id': pos.id,
                                        'symbol': pos.symbol,
                                        'side': pos.side,
                                        'entry_price': pos.entry_price,
                                        'current_price': pos.current_price,
                                        'pnl_usdt': 0,
                                        'pnl_percent': 0,
                                        'opened_at': pos.opened_at
                                    }
                                    state.agent.track_position(pos_data)
                                    logger.info(f"[AGENT] Tracked new position: {pos.symbol} {pos.side}")
                                except Exception as e:
                                    logger.warning(f"[AGENT] Failed to track position: {e}")
                            
                            # Обновляем запись анализа - трейд открыт
                            analysis_record['trade_opened'] = True
                            analysis_record['trade_id'] = pos.trade_id if hasattr(pos, 'trade_id') else ''
                            
                            # Сохраняем историю хода сделки - событие OPEN
                            if hasattr(pos, 'trade_id'):
                                db.save_trade_price_event(
                                    trade_id=pos.trade_id,
                                    event_type='OPEN',
                                    price=entry,
                                    pnl_percent=0,
                                    trailing_stop=sl,
                                    details=f"Entry {action}, SL={sl:.6f}, TP1={tp1:.6f}"
                                )
                    except Exception as e:
                        logger.error(f"[SCAN] Ошибка открытия позиции {symbol}: {e}")
                        if state.trader:
                            state.trader._add_log("error", f"Ошибка открытия {action}: {str(e)[:100]}")
                
                # Сохраняем анализ в БД
                try:
                    db.save_ai_analysis(analysis_record)
                except Exception as e:
                    logger.error(f"[SCAN] Ошибка сохранения анализа: {e}")
                
                time.sleep(1.2)
        
        state.last_scan_time = datetime.utcnow()
        logger.info(f"[SCAN] Цикл завершен: проанализировано {analyzed_count}, SHORT: {signals_found['SHORT']}, LONG: {signals_found['LONG']}")
        
        # Диагностика: почему не торгуем
        if skip_reasons:
            skip_info = ', '.join([f"{k}:{v}" for k, v in skip_reasons.items()])
            logger.info(f"[SCAN] Пропущено: {skip_info}")
        
        if state.trader:
            total_signals = signals_found['SHORT'] + signals_found['LONG']
            daily_pnl = state.trader.daily_pnl
            
            if total_signals > 0:
                state.trader._add_log("scan", f"✅ Найдено {signals_found['SHORT']} SHORT, {signals_found['LONG']} LONG сигналов")
            elif analyzed_count > 0:
                state.trader._add_log("scan", f"📊 Проанализировано {analyzed_count} монет, сигналов нет")
            elif skip_reasons:
                # Если ничего не анализировали — показать почему
                skip_parts = []
                if 'blacklist' in skip_reasons: skip_parts.append(f"⬛ чёрный список: {skip_reasons['blacklist']}")
                if 'cooldown' in skip_reasons: skip_parts.append(f"⏳ кулдаун: {skip_reasons['cooldown']}")
                if 'daily_sl_limit' in skip_reasons: skip_parts.append(f"🛑 лимит SL: {skip_reasons['daily_sl_limit']}")
                if 'already_open' in skip_reasons: skip_parts.append(f"📌 уже открыты: {skip_reasons['already_open']}")
                if 'new_coin' in skip_reasons: skip_parts.append(f"🆕 новые монеты: {skip_reasons['new_coin']}")
                if 'no_volatility' in skip_reasons: skip_parts.append(f"📉 нет движения: {skip_reasons['no_volatility']}")
                for k, v in skip_reasons.items():
                    if k.startswith('limit:'): skip_parts.append(f"🚫 {k.split(':',1)[1]}")
                if skip_parts:
                    state.trader._add_log("scan", f"⚠️ Пропущено: {'; '.join(skip_parts)} | PnL дня: ${daily_pnl:+.2f}")
    except Exception as e:
        logger.error(f"[SCAN] Ошибка сканирования: {e}", exc_info=True)
        if state.trader:
            state.trader._add_log("error", f"Ошибка сканирования: {str(e)[:100]}")

def scanner_loop():
    """Основной цикл сканера"""
    logger.info("[SCANNER] Поток запущен")
    time.sleep(5)
    while state.running:
        if state.trader and not state.trader.scanner_paused:
            try:
                scan_cycle()
            except Exception as e:
                logger.error(f"[SCANNER] Ошибка цикла: {e}")
        interval = state.trader.settings.scan_interval if state.trader else 300
        for _ in range(interval):
            if not state.running:
                break
            time.sleep(1)

# ============================================================================
# WEBSOCKET PRICE HANDLER v5.9
# ============================================================================
def on_websocket_price(symbol: str, price: float, data: dict):
    """
    Callback для обработки цены от WebSocket
    Вызывается при КАЖДОМ обновлении цены (каждые ~100-300ms)
    """
    try:
        # 1. Обновляем кэш цен — ТОЛЬКО если запись уже существует от update_market_prices()
        full_symbol = f"{symbol}/USDT:USDT"
        existing = state.market_prices.get(symbol) or state.market_prices.get(full_symbol)
        if existing is None or not isinstance(existing, dict):
            # Нет записи от update_market_prices — не создаём с нулевым change_24h
            # Только обновляем позиции ниже
            pass
        else:
            state.market_prices[symbol] = {
                'price': float(price),
                'change_24h': existing.get('change_24h'),  # сохраняем как есть
                'timestamp': datetime.utcnow().isoformat(),
                'full_symbol': full_symbol
            }
            # Алиас по full_symbol для обратной совместимости
            state.market_prices[full_symbol] = state.market_prices[symbol]
            state.last_price_update = datetime.utcnow()
        
        # 2. Если есть открытые позиции - проверяем SL/TP/Trailing
        if state.trader:
            positions = state.trader.get_open_positions()
            for pos in positions:
                pos_symbol = pos.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
                if pos_symbol.upper() == symbol.upper():
                    trade_id = pos.get('trade_id')
                    if trade_id:
                        # Обновляем цену позиции
                        state.trader.update_position_price(trade_id, price)
                        
        # 3. Проверяем стопы (это быстрая операция)
        if state.trader and hasattr(state.trader, 'check_stops'):
            state.trader.check_stops()
            
    except Exception as e:
        logger.error(f"[WS] Price callback error for {symbol}: {e}")

def on_websocket_connection(connected: bool):
    """Callback для статуса WebSocket подключения"""
    status = 'connected' if connected else 'disconnected'
    state.health_status['websocket'] = {'status': status}
    logger.info(f"[WS] Connection status: {status}")

def init_websocket():
    """Инициализация WebSocket менеджера"""
    if not WEBSOCKET_AVAILABLE:
        logger.warning("[WS] WebSocket не доступен")
        return False
        
    try:
        state.ws_manager = get_ws_manager()
        state.ws_manager.set_price_callback(on_websocket_price)
        state.ws_manager.set_connection_callback(on_websocket_connection)
        
        # Собираем символы: открытые позиции + кандидаты
        symbols = set()
        
        # Открытые позиции
        if state.trader:
            for pos in state.trader.get_open_positions():
                sym = pos.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
                if sym:
                    symbols.add(sym)
                    
        # Кандидаты из последнего сканирования
        for coin in state.filtered_coins[:20]:  # Топ 20 кандидатов
            sym = coin.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
            if sym:
                symbols.add(sym)
                
        if symbols:
            state.ws_manager.add_symbols(list(symbols))
            state.ws_manager.start()
            logger.info(f"[WS] ✅ Запущен для {len(symbols)} символов: {list(symbols)[:5]}...")
            state.health_status['websocket'] = {'status': 'starting', 'symbols': len(symbols)}
            return True
        else:
            logger.info("[WS] Нет символов для отслеживания, запуск отложен")
            state.health_status['websocket'] = {'status': 'waiting_for_symbols'}
            return False
            
    except Exception as e:
        logger.error(f"[WS] Init error: {e}")
        state.health_status['websocket'] = {'status': 'error', 'error': str(e)}
        return False

def update_websocket_symbols():
    """Обновить список символов в WebSocket (или инициализировать если не запущен)"""
    symbols = set()
    
    # Открытые позиции (приоритет!)
    if state.trader:
        for pos in state.trader.get_open_positions():
            sym = pos.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
            if sym:
                symbols.add(sym)
                
    # Кандидаты
    for coin in state.filtered_coins[:20]:
        sym = coin.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
        if sym:
            symbols.add(sym)
    
    # Если WS еще не инициализирован и есть символы - запускаем
    if not state.ws_manager and symbols and WEBSOCKET_AVAILABLE:
        logger.info(f"[WS] Первая инициализация с {len(symbols)} символами")
        init_websocket()
        return
            
    # Обновляем если ws_manager существует и список изменился
    if state.ws_manager:
        current = state.ws_manager.symbols if state.ws_manager else set()
        if symbols and symbols != current:
            state.ws_manager.set_symbols(list(symbols))
            # Запускаем если еще не запущен
            if not state.ws_manager.running:
                state.ws_manager.start()
                logger.info(f"[WS] ✅ Запущен после сканирования: {len(symbols)} символов")
            else:
                logger.info(f"[WS] Обновлены символы: {len(symbols)} шт")

def price_loop():
    """Обновление цен позиций и рыночных данных - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    logger.info("[PRICE] Поток запущен")
    tick_counter = 0
    btc_refresh_counter = 0
    while state.running:
        try:
            # 1. Обновляем рыночные цены (кэш)
            update_market_prices()
            # 2. Обновляем цены для открытых позиций
            update_open_positions()
            # 3. Каждые 5 минут (300 тиков по 1 сек) записываем в историю
            tick_counter += 1
            
            # 3.5 Каждые 60 сек обновляем BTC trend данные для автозакрытия
            btc_refresh_counter += 1
            if btc_refresh_counter >= 60 and state.trader:
                btc_refresh_counter = 0
                try:
                    # Если btc_trend_data ещё не установлен — пробуем получить полный тренд
                    if not state.trader.btc_trend_data:
                        btc_trend = get_btc_trend()
                        state.trader.btc_trend_data = btc_trend
                        logger.info(f"[PRICE] BTC trend инициализирован: {btc_trend.get('trend')} ({btc_trend.get('change_24h', 0):+.1f}%)")
                    else:
                        # Быстрое обновление change_24h из кэша цен
                        btc_data = state.market_prices.get('BTC', {})
                        if btc_data:
                            fresh_change = btc_data.get('change_24h', 0)
                            if fresh_change != 0:
                                state.trader.btc_trend_data['change_24h'] = fresh_change
                                state.trader.btc_trend_data['trend_pct'] = fresh_change
                except Exception as e:
                    logger.debug(f"[PRICE] BTC refresh error: {e}")
            if tick_counter >= 300:
                tick_counter = 0
                if state.trader:
                    positions = state.trader.get_open_positions()
                    for pos in positions:
                        if pos.get('trade_id') and pos.get('symbol'):
                            current_price = get_price_for_symbol(pos['symbol'])
                            if current_price > 0:
                                entry_price = pos.get('entry_price', current_price)
                                side = pos.get('side', 'SHORT')
                                # Корректный расчет PnL для LONG и SHORT
                                if side == 'SHORT':
                                    pnl_pct = ((entry_price - current_price) / entry_price * 100) if entry_price > 0 else 0
                                else:  # LONG
                                    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                                try:
                                    db.save_trade_price_event(
                                        trade_id=pos['trade_id'],
                                        event_type='TICK',
                                        price=current_price,
                                        pnl_percent=pnl_pct,
                                        trailing_stop=pos.get('trailing_stop', 0),
                                        details=''
                                    )
                                except Exception as e:
                                    logger.debug(f"[PRICE] Tick save error: {e}")
            # 4. Проверяем стоп-лоссы и тейк-профиты
            if state.trader and hasattr(state.trader, 'check_stops'):
                state.trader.check_stops()
            
            # 5. Автозакрытие при ослаблении тренда (проверяем каждые 10 сек)
            if tick_counter % 10 == 0 and state.trader:
                try:
                    closed_weak = state.trader.check_trend_weakness_auto_close()
                    if closed_weak:
                        logger.info(f"[PRICE] Автозакрытие тренд: {closed_weak}")
                except Exception as e:
                    logger.error(f"[PRICE] Trend auto-close error: {e}")
        except Exception as e:
            logger.error(f"[PRICE] Ошибка: {e}")
        # Обновляем каждую 1 секунду - v5.8.6 максимально быстрая реакция на SL
        time.sleep(1)

def health_check_loop():
    """Проверка здоровья системы"""
    logger.info("[HEALTH] Поток запущен")
    while state.running:
        try:
            health = {
                'binance_status': 'ok',
                'binance_ping_ms': 0,
                'deepseek_status': 'unknown',
                'groq_status': 'unknown',
                'telegram_status': 'disabled',
                'database_status': 'ok',
                'daily_pnl': 0,
                'daily_pnl_limit_pct': 5.0,
                'win_rate_today': 0,
                'trades_today': 0,
                'open_positions': 0,
                'last_price_update': state.last_price_update.isoformat() if state.last_price_update else None
            }
            # Binance ping
            try:
                start = time.time()
                state.exchange.fetch_time()
                health['binance_ping_ms'] = int((time.time() - start) * 1000)
                health['binance_status'] = 'ok'
            except Exception:
                health['binance_status'] = 'error'
            # AI status
            if state.ai_engine:
                stats = state.ai_engine.get_stats()
                if isinstance(state.ai_engine, MultiAIEngine):
                    for provider, engine in state.ai_engine.engines.items():
                        if engine:
                            health[f'{provider}_status'] = 'ok'
                else:
                    health['deepseek_status'] = 'ok' if not stats.get('mock_mode') else 'mock'
            # Telegram
            if telegram_bot.enabled:
                health['telegram_status'] = 'ok' if telegram_bot.connected else 'error'
            # Database
            try:
                db.get_setting('test', '')
                health['database_status'] = 'ok'
            except Exception as e:
                health['database_status'] = f'error: {str(e)[:50]}'
            # Trading stats
            if state.trader:
                portfolio = state.trader.get_portfolio()
                health['daily_pnl'] = portfolio.get('daily_pnl', 0)
                health['daily_pnl_limit_pct'] = state.trader.settings.max_daily_loss_pct
                health['win_rate_today'] = portfolio.get('win_rate', 0)
                health['trades_today'] = portfolio.get('total_trades', 0)
                # Количество открытых позиций
                try:
                    open_positions = state.trader.get_open_positions()
                    health['open_positions'] = len(open_positions)
                except Exception:
                    health['open_positions'] = 0
            # Save to DB
            db.save_health_status(health)
            state.health_status = health
        except Exception as e:
            logger.error(f"[HEALTH] Ошибка: {e}")
        time.sleep(60)

# ============================================================================
# API ROUTES - ОСНОВНЫЕ
# ============================================================================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def api_status():
    settings = state.trader.get_settings() if state.trader else {}
    ai_stats = state.ai_engine.get_stats() if state.ai_engine else {}
    
    # v5.9: WebSocket статус
    ws_stats = {}
    if state.ws_manager:
        ws_stats = state.ws_manager.get_stats()
    
    # v6.0: Краткая сводка по кулдаунам
    cooldowns_count = 0
    if state.trader:
        now = get_gmt2_time()
        for sym, cd_time in state.trader.symbol_cooldowns.items():
            cd_end = cd_time + timedelta(minutes=state.trader.settings.symbol_cooldown_min)
            if now < cd_end:
                cooldowns_count += 1
        # Плюс заблокированные по daily SL limit
        max_sl = state.trader.settings.max_symbol_losses_daily
        for sym, losses in state.trader.symbol_daily_losses.items():
            if losses >= max_sl and sym not in state.trader.symbol_cooldowns:
                cooldowns_count += 1
    
    return jsonify({
        "running": state.running,
        "scanner_paused": state.trader.scanner_paused if state.trader else False,
        "trade_mode": settings.get('trade_mode', 'PAPER'),
        "ai_provider": settings.get('ai_provider', 'mock'),
        "ai_ab_mode": settings.get('ai_ab_mode', False),
        "ai_stats": ai_stats,
        "last_scan": state.last_scan_time.isoformat() if state.last_scan_time else None,
        "filtered_coins_count": len(state.filtered_coins),
        "scan_results_count": len(state.scan_results),
        "blacklist_count": db.get_blacklist_count(),
        "btc_trend": get_btc_trend(),
        "health": state.health_status,
        "market_prices_count": len(state.market_prices),
        "last_price_update": state.last_price_update.isoformat() if state.last_price_update else None,
        "volume_analysis_cache_size": len(state.volume_analysis_cache),
        "websocket": ws_stats,  # v5.9
        "cooldowns_count": cooldowns_count  # v6.0
    })

@app.route('/api/cooldowns')
def api_cooldowns():
    """Текущие кулдауны и блокировки монет"""
    if not state.trader:
        return jsonify({"blocked": [], "cooldowns": [], "daily_losses": {}})
    
    now = get_gmt2_time()
    blocked = []     # Заблокированы на весь день (daily SL limit)
    cooldowns = []   # Временный кулдаун
    max_sl = state.trader.settings.max_symbol_losses_daily
    cd_min = state.trader.settings.symbol_cooldown_min
    
    # Монеты с дневным лимитом SL
    for sym, losses in state.trader.symbol_daily_losses.items():
        if losses >= max_sl:
            blocked.append({"symbol": sym, "losses": losses, "max": max_sl, "reason": "daily_sl_limit"})
    
    # Монеты в кулдауне
    for sym, cd_time in state.trader.symbol_cooldowns.items():
        cd_end = cd_time + timedelta(minutes=cd_min)
        remaining = (cd_end - now).total_seconds()
        if remaining > 0:
            cooldowns.append({
                "symbol": sym,
                "remaining_sec": int(remaining),
                "remaining_min": round(remaining / 60, 1),
                "reason": "symbol_cooldown"
            })
    
    # Глобальный кулдаун после последнего лосса
    global_cooldown = None
    if state.trader.last_loss_time and state.trader.settings.cooldown_after_loss_min > 0:
        gc_end = state.trader.last_loss_time + timedelta(minutes=state.trader.settings.cooldown_after_loss_min)
        gc_remaining = (gc_end - now).total_seconds()
        if gc_remaining > 0:
            global_cooldown = {
                "remaining_sec": int(gc_remaining),
                "remaining_min": round(gc_remaining / 60, 1)
            }
    
    return jsonify({
        "blocked": blocked,
        "cooldowns": cooldowns,
        "global_cooldown": global_cooldown,
        "daily_loss_stop": state.trader.daily_loss_stop,
        "daily_pnl": round(state.trader.daily_pnl, 2),
        "daily_losses_all": dict(state.trader.symbol_daily_losses)
    })

@app.route('/api/cooldowns/reset', methods=['POST'])
def api_reset_cooldowns():
    """Сбросить все кулдауны и дневные счётчики SL"""
    if not state.trader:
        return jsonify({"error": "Trader not initialized"}), 500
    
    with state.trader.lock:
        state.trader.symbol_cooldowns.clear()
        state.trader.symbol_daily_losses.clear()
        state.trader.last_loss_time = None
        state.trader._add_log("system", "🔄 Все кулдауны и дневные счётчики SL сброшены вручную")
        logger.info("[COOLDOWNS] Manual reset: all cooldowns and daily SL counters cleared")
    
    return jsonify({"success": True})

@app.route('/api/btc_trend')
def api_btc_trend():
    """Получить текущий тренд Bitcoin"""
    return jsonify(get_btc_trend())

@app.route('/api/health')
def api_health():
    """Статус здоровья системы"""
    health = db.get_latest_health_status() or state.health_status
    # Определяем общий статус
    overall = 'healthy'
    issues = []
    if health.get('binance_status') != 'ok':
        overall = 'problem'
        issues.append('Binance недоступен')
    elif health.get('binance_ping_ms', 0) > 2000:
        overall = 'warning'
        issues.append('Высокий пинг Binance')
    daily_pnl = health.get('daily_pnl', 0)
    if daily_pnl < -50:
        overall = 'warning'
        issues.append(f'Большой дневной убыток: ${daily_pnl:.2f}')
    return jsonify({
        'overall': overall,
        'issues': issues,
        **health
    })

@app.route('/api/portfolio')
def api_portfolio():
    if state.trader:
        portfolio = state.trader.get_portfolio()
        # Добавляем текущие цены из кэша
        if 'positions' in portfolio:
            for pos in portfolio['positions']:
                symbol = pos.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
                if symbol in state.market_prices:
                    pos['current_price'] = state.market_prices[symbol]['price']
        return jsonify(portfolio)
    return jsonify({})

@app.route('/api/positions')
def api_positions():
    if state.trader:
        try:
            limit = request.args.get('limit', 50, type=int)
            open_positions = state.trader.get_open_positions()
            
            # Для лимитов > 100 загружаем из БД (RAM хранит только последние 100)
            if limit > 100 and db:
                try:
                    db_trades = db.get_trades(limit=limit, only_closed=True)
                    closed_positions = []
                    for t in db_trades:
                        closed_positions.append({
                            'symbol': t.get('symbol', ''),
                            'side': t.get('side', 'SHORT'),
                            'pnl_usdt': t.get('pnl_usdt', 0),
                            'close_reason': t.get('result', ''),
                            'closed_at': t.get('closed_at', ''),
                            'entry_price': t.get('entry_price', 0),
                            'exit_price': t.get('exit_price', 0),
                            'partial_tp_pnl': t.get('partial_tp_pnl', 0),
                        })
                except Exception as e:
                    logger.error(f"[API] DB trades load error: {e}")
                    closed_positions = state.trader.get_closed_positions(limit)
            else:
                closed_positions = state.trader.get_closed_positions(limit)
            # Обогащаем открытые позиции текущими ценами
            enriched_open = []
            for pos in open_positions:
                if isinstance(pos, dict):
                    pos_dict = pos
                else:
                    # Преобразуем объект в словарь
                    pos_dict = pos.to_dict() if hasattr(pos, 'to_dict') else {}
                symbol = pos_dict.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
                # Добавляем текущую цену
                if symbol in state.market_prices:
                    pos_dict['current_price'] = state.market_prices[symbol]['price']
                    pos_dict['price_change_24h'] = state.market_prices[symbol]['change_24h']
                # Рассчитываем текущий PnL с учетом направления
                entry_price = pos_dict.get('entry_price', 0)
                side = pos_dict.get('side', 'SHORT')
                if entry_price > 0:
                    current_price = pos_dict['current_price']
                    # Корректный расчет PnL для LONG и SHORT
                    if side == 'SHORT':
                        pos_dict['current_pnl_percent'] = ((entry_price - current_price) / entry_price * 100)
                    else:  # LONG
                        pos_dict['current_pnl_percent'] = ((current_price - entry_price) / entry_price * 100)
                    # Рассчитываем PnL в USD
                    position_size = pos_dict.get('position_size', 0)
                    if position_size > 0:
                        pos_dict['current_pnl_usd'] = position_size * (pos_dict['current_pnl_percent'] / 100)
                    else:
                        pos_dict['current_pnl_usd'] = 0
                enriched_open.append(pos_dict)
            return jsonify({
                "open": enriched_open,
                "closed": closed_positions,
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.error(f"[API] Ошибка получения позиций: {e}")
            return jsonify({"open": [], "closed": [], "error": str(e)})
    return jsonify({"open": [], "closed": []})

@app.route('/api/logs')
def api_logs():
    limit = request.args.get('limit', 50, type=int)
    if state.trader:
        return jsonify(state.trader.get_logs(limit))
    return jsonify([])

# ============================================================================
# API - ЦЕНЫ И РЫНОЧНЫЕ ДАННЫЕ
# ============================================================================
@app.route('/api/prices')
def api_prices():
    """Получить все текущие цены"""
    with state.lock:
        return jsonify({
            "prices": state.market_prices,
            "timestamp": datetime.utcnow().isoformat(),
            "count": len(state.market_prices),
            "last_update": state.last_price_update.isoformat() if state.last_price_update else None
        })

@app.route('/api/price/<symbol>')
def api_price(symbol):
    """Получить цену для конкретного символа"""
    try:
        price = get_price_for_symbol(symbol)
        return jsonify({
            "symbol": symbol,
            "price": price,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ohlcv/<path:symbol>')
def api_ohlcv(symbol):
    tf = request.args.get('tf', '15m')
    limit = request.args.get('limit', 200, type=int)
    try:
        # Нормализуем символ в формат Binance Futures
        original = symbol
        symbol = symbol.replace('%2F', '/').replace('%3A', ':')  # URL decode
        if not symbol.endswith(':USDT'):
            if '/USDT' in symbol:
                symbol = symbol + ':USDT'
            else:
                symbol = symbol + '/USDT:USDT'
        logger.info(f"[API] OHLCV request: {original} -> {symbol}, tf={tf}, limit={limit}")
        ohlcv = state.exchange.fetch_ohlcv(symbol, tf, limit=limit)
        logger.info(f"[API] OHLCV returned {len(ohlcv) if ohlcv else 0} candles")
        return jsonify(ohlcv if ohlcv else [])
    except Exception as e:
        logger.error(f"[API] OHLCV error for {symbol}: {e}")
        return jsonify([])

@app.route('/api/ticker/<path:symbol>')
def api_ticker(symbol):
    try:
        t = state.exchange.fetch_ticker(symbol)
        return jsonify({
            "symbol": symbol,
            "price": t['last'],
            "change_24h": t.get('percentage', 0),
            "high_24h": t.get('high', 0),
            "low_24h": t.get('low', 0),
            "volume": t.get('quoteVolume', 0)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/market_data')
def api_market_data():
    """Получить рыночные данные для графика"""
    symbol = request.args.get('symbol', 'BTC/USDT')
    timeframe = request.args.get('timeframe', '15m')
    limit = request.args.get('limit', 100, type=int)
    try:
        # Конвертируем символ
        if not symbol.endswith(':USDT') and '/USDT' in symbol:
            symbol = symbol + ':USDT'
        # Получаем OHLCV данные
        ohlcv = state.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        # Форматируем для графика
        chart_data = []
        for candle in ohlcv:
            chart_data.append({
                'time': candle[0] / 1000,
                'open': candle[1],
                'high': candle[2],
                'low': candle[3],
                'close': candle[4],
                'volume': candle[5]
            })
        # Получаем текущую цену
        ticker = state.exchange.fetch_ticker(symbol)
        return jsonify({
            "success": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "data": chart_data,
            "current_price": ticker['last'],
            "change_24h": ticker.get('percentage', 0)
        })
    except Exception as e:
        logger.error(f"[API] Market data error: {e}")
        return jsonify({"success": False, "error": str(e)})

# ============================================================================
# API - НАСТРОЙКИ
# ============================================================================
@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    if state.trader:
        s = state.trader.get_settings()
        # Маскируем ключи
        for key in ['deepseek_api_key', 'groq_api_key', 'binance_api_key',
                    'binance_secret_key', 'telegram_bot_token']:
            if s.get(key):
                s[f'{key}_masked'] = f"...{s[key][-4:]}" if len(s[key]) > 4 else '***'
                s[key] = ''
        return jsonify(s)
    return jsonify({})

@app.route('/api/settings', methods=['POST'])
def api_update_settings():
    data = request.get_json() or {}
    if state.trader:
        # Обновляем настройки
        if state.trader.update_settings(data):
            # ═══ v5.8: СИНХРОНИЗАЦИЯ С CONFIG.JSON ═══
            # Сохраняем trading параметры в config.json для Mock
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                # Обновляем секцию trading
                if 'trading' not in config:
                    config['trading'] = {}
                
                trading_keys = ['stop_loss_pct', 'take_profit_pct', 'trailing_activation_pct', 
                               'trailing_distance_pct', 'position_size', 'leverage']
                for key in trading_keys:
                    if key in data:
                        config['trading'][key] = data[key]
                
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                
                logger.info(f"[SETTINGS] Config.json synced: trading={config.get('trading', {})}")
            except Exception as e:
                logger.warning(f"[SETTINGS] Config sync error: {e}")
            
            # Используем RAW настройки с полными ключами для инициализации
            settings_raw = state.trader.get_settings_raw()
            # Переинициализируем AI если изменились ключи
            if any(k in data for k in ['deepseek_api_key', 'groq_api_key', 'ai_provider', 'ai_ab_mode']):
                state.ai_engine = init_ai_engine(settings_raw)
            # Переинициализируем Crypto Agent если изменились настройки
            if any(k in data for k in ['agent_enabled', 'deepseek_api_key']):
                if state.agent:
                    state.agent.stop()
                state.agent = init_agent(settings_raw)
            # Обновляем настройки Agent без переинициализации
            if state.agent and any(k in data for k in ['agent_aggressiveness', 'agent_stagnation_hours', 'agent_auto_close', 'agent_validate_signals']):
                state.agent.update_settings({
                    'aggressiveness': data.get('agent_aggressiveness', 2),
                    'stagnation_hours': data.get('agent_stagnation_hours', 2.0),
                    'auto_close_enabled': data.get('agent_auto_close', True),
                    'auto_validate_signals': data.get('agent_validate_signals', True)
                })
            # Обновляем trading_style без переинициализации
            if 'trading_style' in data and state.ai_engine:
                new_style = data['trading_style']
                if hasattr(state.ai_engine, 'set_trading_style'):
                    state.ai_engine.set_trading_style(new_style)
                elif hasattr(state.ai_engine, 'engines'):
                    for e in state.ai_engine.engines.values():
                        if hasattr(e, 'set_trading_style'):
                            e.set_trading_style(new_style)
                logger.info(f"[SETTINGS] Trading style updated to: {new_style}")
            # Переинициализируем Telegram
            if any(k in data for k in ['telegram_bot_token', 'telegram_chat_id']):
                init_telegram(settings_raw)
            # Переинициализируем Live trading
            if any(k in data for k in ['binance_api_key', 'binance_secret_key']):
                init_live_trading(settings_raw)
            return jsonify({"success": True})
        return jsonify({"error": "Failed"}), 500

# ============================================================================
# API - ТОРГОВЛЯ
# ============================================================================

# API для фильтров торговли
@app.route('/api/filters', methods=['GET'])
def api_get_filters():
    """Получить текущие фильтры"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        return jsonify({
            "filters": config.get('filters', {
                "allowed_hours": list(range(24)),
                "allowed_days": list(range(7)),
                "allowed_directions": ["SHORT", "LONG"],
                "btc_bullish_mode": "long_only",
                "btc_bearish_mode": "short_only",
                "btc_neutral_mode": "none",
                "btc_bullish_min_strength": 0.5,
                "btc_bearish_min_strength": 0.5,
                "close_long_on_neutral": False,
                "close_short_on_neutral": False,
                "close_long_on_weak_bull": False,
                "close_long_weak_bull_threshold": 0.5,
                "close_short_on_weak_bear": False,
                "close_short_weak_bear_threshold": 0.5,
                "min_confidence": 70
            }),
            "entry_filters": config.get('entry_filters', {
                "parabolic_enabled": True,
                "parabolic_multiplier": 3.0,
                "rvol_enabled": True,
                "min_rvol": 1.2,
                "multi_tf_enabled": True,
                "multi_tf_ema_period": 20
            })
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/filters', methods=['POST'])
def api_set_filters():
    """Сохранить фильтры"""
    try:
        data = request.get_json() or {}
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        config['filters'] = {
            "allowed_hours": data.get('allowed_hours', list(range(24))),
            "allowed_days": data.get('allowed_days', list(range(7))),
            "allowed_directions": data.get('allowed_directions', ["SHORT", "LONG"]),
            "btc_bullish_mode": data.get('btc_bullish_mode', 'long_only'),
            "btc_bearish_mode": data.get('btc_bearish_mode', 'short_only'),
            "btc_neutral_mode": data.get('btc_neutral_mode', 'none'),
            "btc_bullish_min_strength": float(data.get('btc_bullish_min_strength', 0.5)),
            "btc_bearish_min_strength": float(data.get('btc_bearish_min_strength', 0.5)),
            "close_long_on_neutral": data.get('close_long_on_neutral', False),
            "close_short_on_neutral": data.get('close_short_on_neutral', False),
            "close_long_on_weak_bull": data.get('close_long_on_weak_bull', False),
            "close_long_weak_bull_threshold": float(data.get('close_long_weak_bull_threshold', 0.5)),
            "close_short_on_weak_bear": data.get('close_short_on_weak_bear', False),
            "close_short_weak_bear_threshold": float(data.get('close_short_weak_bear_threshold', 0.5)),
            "min_confidence": data.get('min_confidence', 70)
        }
        
        # Entry filters (v6.4)
        if 'entry_filters' in data:
            ef = data['entry_filters']
            config['entry_filters'] = {
                "parabolic_enabled": ef.get('parabolic_enabled', True),
                "parabolic_multiplier": float(ef.get('parabolic_multiplier', 3.0)),
                "rvol_enabled": ef.get('rvol_enabled', True),
                "min_rvol": float(ef.get('min_rvol', 1.2)),
                "multi_tf_enabled": ef.get('multi_tf_enabled', True),
                "multi_tf_ema_period": int(ef.get('multi_tf_ema_period', 20))
            }

        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        # Обновляем trader если есть
        if state.trader:
            state.trader.filters = config['filters']
            # Синхронизируем BTC-режимы в trader settings
            state.trader.settings.btc_bullish_mode = config['filters'].get('btc_bullish_mode', 'long_only')
            state.trader.settings.btc_bearish_mode = config['filters'].get('btc_bearish_mode', 'short_only')
            state.trader.settings.btc_neutral_mode = config['filters'].get('btc_neutral_mode', 'none')
            state.trader.settings.btc_bullish_min_strength = float(config['filters'].get('btc_bullish_min_strength', 0.5))
            state.trader.settings.btc_bearish_min_strength = float(config['filters'].get('btc_bearish_min_strength', 0.5))
            state.trader.settings.close_long_on_neutral = config['filters'].get('close_long_on_neutral', False)
            state.trader.settings.close_short_on_neutral = config['filters'].get('close_short_on_neutral', False)
            # Автозакрытие при ослаблении тренда (v6.1)
            state.trader.settings.close_long_on_weak_bull = config['filters'].get('close_long_on_weak_bull', False)
            state.trader.settings.close_long_weak_bull_threshold = float(config['filters'].get('close_long_weak_bull_threshold', 0.5))
            state.trader.settings.close_short_on_weak_bear = config['filters'].get('close_short_on_weak_bear', False)
            state.trader.settings.close_short_weak_bear_threshold = float(config['filters'].get('close_short_weak_bear_threshold', 0.5))
        
        logger.info(f"[FILTERS] Updated: filters={config['filters']}, entry_filters={config.get('entry_filters', {})}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"[FILTERS] Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/pnl_history')
def api_pnl_history():
    """История PnL для графика — ПОТОЧЕЧНО по сделкам"""
    try:
        days = request.args.get('days', '30')
        if days == 'all':
            days = 365
        else:
            days = int(days)
        
        if not db:
            return jsonify({"history": [], "total_trades": 0})
        
        # Читаем дату последнего сброса
        pnl_reset_at = db.get_setting('pnl_reset_at', None)
        
        trades = db.get_trades(limit=None, only_closed=True, days=days)
        
        if not trades:
            return jsonify({"history": [], "total_trades": 0})
        
        # Фильтруем: только сделки ПОСЛЕ сброса
        if pnl_reset_at:
            trades = [t for t in trades if t.get('closed_at', '') >= pnl_reset_at]
            logger.info(f"[PNL_HISTORY] After reset filter ({pnl_reset_at}): {len(trades)} trades")
        
        if not trades:
            return jsonify({"history": [], "total_trades": 0, "reset_at": pnl_reset_at})
        
        # Сортируем по времени закрытия
        trades.sort(key=lambda t: t.get('closed_at', ''))
        
        # Каждая сделка = отдельная точка на графике
        cumulative = 0
        history = []
        
        # Стартовая точка: 0
        first_time = trades[0].get('closed_at', '')
        if first_time:
            history.append({
                "time": first_time,
                "pnl": 0,
                "cumulative_pnl": 0,
                "symbol": "",
                "reason": "START"
            })
        
        for t in trades:
            pnl = t.get('pnl_usdt', 0)
            cumulative += pnl
            closed_at = t.get('closed_at', '')
            history.append({
                "time": closed_at,
                "pnl": round(pnl, 2),
                "cumulative_pnl": round(cumulative, 2),
                "symbol": t.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', ''),
                "reason": t.get('close_reason', '')
            })
        
        return jsonify({
            "history": history,
            "total_trades": len(trades),
            "total_pnl": round(cumulative, 2),
            "reset_at": pnl_reset_at
        })
    except Exception as e:
        logger.error(f"[PNL_HISTORY] Error: {e}")
        return jsonify({"history": [], "error": str(e)})

@app.route('/api/scanner/pause', methods=['POST'])
def api_pause():
    data = request.get_json() or {}
    if state.trader:
        state.trader.pause_scanner(data.get('paused', True))
        return jsonify({"success": True, "paused": state.trader.scanner_paused})
    return jsonify({"error": "Not initialized"}), 500

@app.route('/api/close_position/<trade_id>', methods=['POST'])
def api_close(trade_id):
    if state.trader:
        r = state.trader.close_position_manual(trade_id)
        if r:
            return jsonify({"success": True, "result": r})
        return jsonify({"error": "Not found"}), 404

@app.route('/api/force_scan', methods=['POST'])
def api_force():
    if state.trader and state.trader.scanner_paused:
        return jsonify({"error": "Scanner paused"}), 400
    threading.Thread(target=scan_cycle, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/reset', methods=['POST'])
def api_reset():
    d = request.get_json() or {}
    if state.trader:
        state.trader.reset(keep_settings=d.get('keep_settings', False))
        with state.lock:
            state.scan_results.clear()
            state.filtered_coins.clear()
            state.market_prices.clear()
        return jsonify({"success": True})
    return jsonify({"error": "Not initialized"}), 500

@app.route('/api/equity', methods=['GET'])
def api_equity_info():
    """Получить информацию об эквити"""
    if state.trader:
        return jsonify(state.trader.get_equity_info())
    return jsonify({"error": "Not initialized"}), 500

@app.route('/api/equity/reset', methods=['POST'])
def api_equity_reset():
    """Сбросить пик эквити"""
    if state.trader:
        state.trader.reset_equity_peak()
        return jsonify({"success": True, "new_peak": state.trader.equity_peak})
    return jsonify({"error": "Not initialized"}), 500

# ============================================================================
# API - SMART AI
# ============================================================================
# ============================================================================
# API - CRYPTO AGENT
# ============================================================================
@app.route('/api/agent/status', methods=['GET'])
def api_agent_status():
    """Получить статус Crypto Agent"""
    if state.agent:
        return jsonify(state.agent.get_status())
    return jsonify({
        "enabled": False,
        "available": AGENT_AVAILABLE,
        "message": "Agent not initialized"
    })

@app.route('/api/agent/toggle', methods=['POST'])
def api_agent_toggle():
    """Включить/выключить Agent"""
    data = request.get_json() or {}
    enabled = data.get('enabled', True)
    
    if state.agent:
        state.agent.settings.enabled = enabled
        if enabled:
            state.agent.start()
        else:
            state.agent.stop()
        return jsonify({"success": True, "enabled": enabled})
    return jsonify({"error": "Agent not initialized"}), 500

@app.route('/api/agent/analyze_market', methods=['POST'])
def api_agent_analyze_market():
    """Принудительный анализ рынка"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    try:
        btc_trend = get_btc_trend()
        if btc_trend:
            state.agent.update_market(
                btc_price=btc_trend.get('price', 0),
                btc_trend=btc_trend.get('trend', 'neutral'),
                btc_rsi=btc_trend.get('rsi', 50),
                btc_change_1h=btc_trend.get('change_1h', 0),
                btc_change_24h=btc_trend.get('change_24h', 0)
            )
        
        # Возвращаем текущее состояние
        m = state.agent.market
        return jsonify({
            "success": True,
            "mode": m.market_mode,
            "btc_trend": m.btc_trend,
            "btc_price": m.btc_price,
            "btc_rsi": m.btc_rsi
        })
    except Exception as e:
        logger.error(f"[AGENT] Market analysis error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/memory', methods=['GET'])
def api_agent_memory():
    """Получить память о монетах (из SQLite)"""
    try:
        memory = db.get_all_coin_memory()
        return jsonify({"coins": memory, "count": len(memory)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/memory/reset', methods=['POST'])
def api_agent_memory_reset():
    """Сбросить память о сделках"""
    # В V3 память в SQLite, не сбрасываем легко
    return jsonify({"success": False, "message": "Memory is persistent in V3, use DB tools to reset"})

@app.route('/api/agent/settings', methods=['POST'])
def api_agent_settings():
    """Обновить настройки Agent"""
    data = request.get_json() or {}
    
    if state.agent:
        state.agent.update_settings(data)
        return jsonify({"success": True})
    return jsonify({"error": "Agent not initialized"}), 500

@app.route('/api/agent/check_positions', methods=['POST'])
def api_agent_check_positions():
    """Принудительная проверка всех позиций"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    try:
        state.agent._check_all_positions()
        return jsonify({
            "success": True,
            "checked": len(state.agent.positions)
        })
    except Exception as e:
        logger.error(f"[AGENT] Check positions error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/chat', methods=['POST'])
def api_agent_chat():
    """Чат с AI агентом"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    data = request.get_json() or {}
    message = data.get('message', '')
    
    if not message:
        return jsonify({"error": "No message provided"}), 400
    
    try:
        response = state.agent.ask_ai(message)
        return jsonify({
            "success": True,
            "response": response
        })
    except Exception as e:
        logger.error(f"[AGENT] Chat error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/diagnose', methods=['POST'])
def api_agent_diagnose():
    """Диагностика проблем"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    try:
        # В V3 нет отдельного diagnose, используем ask_ai
        result = state.agent.ask_ai("Проанализируй текущее состояние, найди проблемы и предложи решения.")
        return jsonify({
            "success": True,
            "diagnosis": result
        })
    except Exception as e:
        logger.error(f"[AGENT] Diagnose error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/decisions', methods=['GET'])
def api_agent_decisions():
    """История решений агента"""
    if not state.agent:
        return jsonify([])
    n = request.args.get('n', 20, type=int)
    return jsonify(state.agent.get_decisions(n))

@app.route('/api/agent/logs', methods=['GET'])
def api_agent_logs():
    """Логи агента"""
    if not state.agent:
        return jsonify([])
    n = request.args.get('n', 50, type=int)
    return jsonify(state.agent.get_logs(n))

@app.route('/api/agent/debug_positions', methods=['GET'])
def api_agent_debug_positions():
    """DEBUG: Позиции в агенте vs позиции в трейдере"""
    result = {
        'agent_positions': [],
        'trader_positions': [],
        'sync_status': 'unknown'
    }
    
    # Позиции агента
    if state.agent:
        with state.agent.positions_lock:
            for tid, pos in state.agent.positions.items():
                result['agent_positions'].append({
                    'trade_id': tid,
                    'symbol': pos.symbol,
                    'side': pos.side,
                    'pnl_percent': pos.pnl_percent
                })
    
    # Позиции трейдера
    if state.trader:
        for tid, pos in state.trader.positions.items():
            if pos.status == "OPEN":
                result['trader_positions'].append({
                    'trade_id': tid,
                    'symbol': pos.symbol,
                    'side': pos.side,
                    'pnl_percent': pos.pnl_percent
                })
    
    # Проверка синхронизации
    agent_ids = set(p['trade_id'] for p in result['agent_positions'])
    trader_ids = set(p['trade_id'] for p in result['trader_positions'])
    
    if agent_ids == trader_ids:
        result['sync_status'] = 'OK'
    else:
        result['sync_status'] = 'MISMATCH'
        result['only_in_agent'] = list(agent_ids - trader_ids)
        result['only_in_trader'] = list(trader_ids - agent_ids)
    
    return jsonify(result)

@app.route('/api/agent/ask_position', methods=['POST'])
def api_agent_ask_position():
    """Спросить AI о конкретной позиции"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    data = request.get_json() or {}
    trade_id = data.get('trade_id')
    
    if not trade_id:
        return jsonify({"error": "trade_id required"}), 400
    
    try:
        # Получаем позицию
        with state.agent.positions_lock:
            pos = state.agent.positions.get(trade_id)
        
        if not pos:
            return jsonify({"error": "Position not found"}), 404
        
        # Формируем вопрос
        question = f"Проанализируй позицию {pos.symbol} {pos.side}:\n{pos.to_prompt_str()}\n\nЧто делать с этой позицией?"
        response = state.agent.ask_ai(question)
        
        return jsonify({
            "success": True,
            "response": response
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/generate_strategy', methods=['POST'])
def api_agent_generate_strategy():
    """Сгенерировать новую стратегию на основе истории"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    try:
        strategy = state.agent.generate_strategy()
        return jsonify({
            "success": True,
            "strategy": strategy
        })
    except Exception as e:
        logger.error(f"[AGENT] Strategy generation error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/insights', methods=['GET'])
def api_agent_insights():
    """Получить инсайты из истории торговли"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    try:
        insights = state.agent.tools.get_trading_insights()
        patterns = state.agent.tools.get_pattern_analysis()
        return jsonify({
            "success": True,
            "insights": insights,
            "patterns": patterns
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/coin_history/<symbol>', methods=['GET'])
def api_agent_coin_history(symbol):
    """История торговли по конкретной монете"""
    try:
        history = db.get_symbol_trade_history(symbol, limit=20)
        stats = db.get_symbol_statistics(symbol)
        return jsonify({
            "success": True,
            "symbol": symbol,
            "statistics": stats,
            "history": history
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/analytics', methods=['GET'])
def api_agent_analytics():
    """Полная аналитика торговли"""
    try:
        return jsonify({
            "success": True,
            "hourly": db.get_hourly_statistics(),
            "confidence": db.get_confidence_statistics(),
            "sides": db.get_side_statistics(),
            "coins": db.get_best_worst_coins(),
            "close_reasons": db.get_close_reason_statistics()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/debug', methods=['GET'])
def api_agent_debug():
    """Debug: показать что видит агент"""
    if not state.agent:
        return jsonify({"error": "Agent not initialized"}), 500
    
    try:
        with state.agent.positions_lock:
            positions_data = {}
            for tid, pos in state.agent.positions.items():
                positions_data[tid] = {
                    'symbol': pos.symbol,
                    'side': pos.side,
                    'entry_price': pos.entry_price,
                    'current_price': pos.current_price,
                    'pnl_percent': pos.pnl_percent,
                    'pnl_usdt': pos.pnl_usdt,
                    'stop_loss': pos.stop_loss,
                    'age_minutes': pos.age_minutes,
                    'trail_activated': pos.trail_activated
                }
        
        # Получаем позиции от trader для сравнения
        trader_positions = []
        trader_ids = set()
        if state.trader:
            for pos in state.trader.get_open_positions():
                # pos может быть объектом Position или dict
                if hasattr(pos, 'id'):
                    pid = pos.id
                    psym = pos.symbol
                    pside = pos.side
                    ppnl = pos.pnl_percent
                elif isinstance(pos, dict):
                    pid = pos.get('id', pos.get('trade_id'))
                    psym = pos.get('symbol', '?')
                    pside = pos.get('side', '?')
                    ppnl = pos.get('pnl_percent', 0)
                else:
                    continue
                
                trader_ids.add(pid)
                trader_positions.append({
                    'id': pid,
                    'symbol': psym,
                    'side': pside,
                    'pnl_percent': ppnl
                })
        
        # СИНХРОНИЗАЦИЯ: удаляем из агента позиции которых нет у trader
        agent_ids = set(positions_data.keys())
        orphaned = agent_ids - trader_ids
        if orphaned:
            for oid in orphaned:
                state.agent.untrack_position(oid)
            logger.info(f"[AGENT] 🧹 Synced: removed {len(orphaned)} orphaned positions: {orphaned}")
        
        return jsonify({
            "success": True,
            "agent_positions_count": len(positions_data),
            "agent_positions": positions_data,
            "trader_positions_count": len(trader_positions),
            "trader_positions": trader_positions,
            "orphaned_removed": list(orphaned) if orphaned else [],
            "market": {
                "btc_price": state.agent.market.btc_price,
                "btc_trend": state.agent.market.btc_trend,
                "market_mode": state.agent.market.market_mode
            }
        })
    except Exception as e:
        import traceback
        logger.error(f"[AGENT] Debug error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/agent/sync', methods=['POST'])
def api_agent_sync():
    """Принудительная синхронизация позиций агента с trader"""
    if not state.agent or not state.trader:
        return jsonify({"error": "Agent or trader not initialized"}), 500
    
    try:
        # Получаем актуальные ID от trader
        trader_ids = set()
        for pos in state.trader.get_open_positions():
            if hasattr(pos, 'id'):
                trader_ids.add(pos.id)
            elif isinstance(pos, dict):
                trader_ids.add(pos.get('id', pos.get('trade_id')))
        
        # Удаляем устаревшие из агента
        removed = []
        with state.agent.positions_lock:
            agent_ids = set(state.agent.positions.keys())
            orphaned = agent_ids - trader_ids
            for oid in orphaned:
                del state.agent.positions[oid]
                removed.append(oid)
        
        return jsonify({
            "success": True,
            "removed": removed,
            "remaining": len(state.agent.positions)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# ✅ ИСПРАВЛЕННЫЙ РУЧНОЙ ТРЕЙД-ХЭНДЛЕР — ПОДДЕРЖКА SHORT/LONG
# ============================================================================
@app.route('/api/manual_trade', methods=['POST'])
def api_manual():
    d = request.get_json() or {}
    try:
        symbol = d['symbol']
        side = d.get('side', 'SHORT').upper()
        if side not in ['SHORT', 'LONG']:
            return jsonify({"error": "Направление должно быть SHORT или LONG"}), 400
        entry_price = float(d['entry_price'])
        stop_loss = float(d['stop_loss'])
        take_profit_1 = float(d['take_profit_1'])
        take_profit_2 = float(d.get('take_profit_2', take_profit_1 * (0.97 if side == 'SHORT' else 1.03)))
        if entry_price <= 0:
            return jsonify({"error": "Entry price must be > 0"}), 400
        # Валидация уровней в зависимости от направления
        if side == "SHORT":
            if stop_loss <= entry_price:
                return jsonify({"error": f"Для SHORT: SL должен быть > entry"}), 400
            if take_profit_1 >= entry_price:
                return jsonify({"error": f"Для SHORT: TP должен быть < entry"}), 400
        else:  # LONG
            if stop_loss >= entry_price:
                return jsonify({"error": f"Для LONG: SL должен быть < entry"}), 400
            if take_profit_1 <= entry_price:
                return jsonify({"error": f"Для LONG: TP должен быть > entry"}), 400
        signal = {
            'symbol': symbol,
            'action': side,
            'direction': side,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': [take_profit_1, take_profit_2],
            'confidence': 0,
            'reason': 'Manual trade',
            'analysis_ru': f'Ручная позиция {side}',
            'ai_provider': 'manual'
        }
        if state.trader:
            # Синхронизируем BTC тренд перед открытием
            state.trader.btc_trend_data = get_btc_trend()
            # v6.2: Захватываем change_24h в момент открытия
            symbol_clean_mp = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
            price_data = state.market_prices.get(symbol_clean_mp, {})
            signal['change_24h'] = price_data.get('change_24h', 0)
            signal['change_24h_at_open'] = price_data.get('change_24h', 0)
            pos = state.trader.open_position(signal)
            if pos:
                return jsonify({"success": True, "position": pos.to_dict()})
            else:
                return jsonify({"error": "Не удалось открыть позицию"}), 400
    except KeyError as e:
        return jsonify({"error": f"Missing field: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ============================================================================
# API - ОБНОВЛЕНИЕ УРОВНЕЙ
# ============================================================================
@app.route('/api/update_sl/<trade_id>', methods=['POST'])
def api_update_sl(trade_id):
    """Обновить Stop Loss позиции"""
    d = request.get_json() or {}
    new_sl = d.get('stop_loss')
    if not new_sl:
        return jsonify({"error": "stop_loss required"}), 400
    if state.trader:
        success = state.trader.update_stop_loss(trade_id, float(new_sl))
        if success:
            return jsonify({"success": True})
        return jsonify({"error": "Failed"}), 400

@app.route('/api/update_tp/<trade_id>', methods=['POST'])
def api_update_tp(trade_id):
    """Обновить Take Profit позиции"""
    d = request.get_json() or {}
    new_tp = d.get('take_profit')
    if not new_tp:
        return jsonify({"error": "take_profit required"}), 400
    if state.trader:
        success = state.trader.update_take_profit(trade_id, float(new_tp))
        if success:
            return jsonify({"success": True})
        return jsonify({"error": "Failed"}), 400

# ============================================================================
# API - ЧЕРНЫЙ СПИСОК (ИСПРАВЛЕННАЯ ВЕРСИЯ)
# ============================================================================
@app.route('/api/blacklist', methods=['GET'])
def api_blacklist():
    """Получить весь черный список"""
    try:
        blacklist = db.get_blacklist()
        return jsonify({
            "success": True,
            "blacklist": blacklist,
            "count": len(blacklist)
        })
    except Exception as e:
        logger.error(f"[API] Ошибка получения черного списка: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/blacklist/add', methods=['POST'])
def api_blacklist_add():
    """Добавить пару в черный список"""
    data = request.get_json() or {}
    symbol = data.get('symbol', '').strip()
    reason = data.get('reason', '').strip()
    if not symbol:
        return jsonify({"success": False, "error": "Symbol is required"}), 400
    try:
        # Нормализуем символ (убираем возможные дублирования USDT)
        if '/USDT:USDT' in symbol:
            clean_symbol = symbol.replace('/USDT:USDT', '')
        elif ':USDT' in symbol:
            clean_symbol = symbol.split(':')[0]
        elif '/USDT' in symbol:
            clean_symbol = symbol.replace('/USDT', '')
        else:
            clean_symbol = symbol
        # Добавляем в формате для фьючерсов
        futures_symbol = clean_symbol + '/USDT:USDT'
        # Используем существующую функцию из database
        success = db.add_to_blacklist(futures_symbol, reason)
        if success:
            logger.info(f"[BLACKLIST] Добавлена пара {futures_symbol} по причине: {reason}")
            # Закрываем все открытые позиции по этому символу
            if state.trader:
                open_positions = state.trader.get_open_positions()
                for pos in open_positions:
                    pos_symbol = pos.get('symbol', '')
                    if db._clean_symbol(pos_symbol) == db._clean_symbol(futures_symbol):
                        trade_id = pos.get('id') or pos.get('trade_id')
                        if trade_id:
                            state.trader.close_position_manual(trade_id, reason="BLACKLIST")
            # Отправляем уведомление в Telegram
            if telegram_bot.enabled:
                telegram_bot.send_message(f"⛔ ЧЕРНЫЙ СПИСОК: {clean_symbol}\nПричина: {reason}")
            return jsonify({
                "success": True,
                "symbol": clean_symbol,
                "futures_symbol": futures_symbol,
                "reason": reason,
                "message": f"Пара {clean_symbol} добавлена в черный список"
            })
        else:
            return jsonify({"success": False, "error": "Already in blacklist or failed"}), 400
    except Exception as e:
        logger.error(f"[API] Ошибка добавления в черный список: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/blacklist/remove', methods=['POST'])
def api_blacklist_remove():
    """Удалить пару из черного списка"""
    data = request.get_json() or {}
    symbol = data.get('symbol', '').strip()
    if not symbol:
        return jsonify({"success": False, "error": "Symbol is required"}), 400
    try:
        # Нормализуем символ
        if '/USDT:USDT' in symbol:
            clean_symbol = symbol.replace('/USDT:USDT', '')
        elif ':USDT' in symbol:
            clean_symbol = symbol.split(':')[0]
        elif '/USDT' in symbol:
            clean_symbol = symbol.replace('/USDT', '')
        else:
            clean_symbol = symbol
        # Ищем и удаляем в формате фьючерсов
        futures_symbol = clean_symbol + '/USDT:USDT'
        success = db.remove_from_blacklist(futures_symbol)
        if success:
            logger.info(f"[BLACKLIST] Удалена пара {futures_symbol}")
            # Отправляем уведомление в Telegram
            if telegram_bot.enabled:
                telegram_bot.send_message(f"✅ УДАЛЕН из ЧС: {clean_symbol}")
            return jsonify({
                "success": True,
                "symbol": clean_symbol,
                "futures_symbol": futures_symbol,
                "message": f"Пара {clean_symbol} удалена из черного списка"
            })
        else:
            return jsonify({"success": False, "error": "Symbol not found in blacklist"}), 404
    except Exception as e:
        logger.error(f"[API] Ошибка удаления из черного списка: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/blacklist/clear', methods=['POST'])
def api_blacklist_clear():
    """Очистить весь черный список"""
    try:
        count = db.clear_blacklist()
        logger.info(f"[BLACKLIST] Очищен черный список, удалено {count} записей")
        return jsonify({
            "success": True,
            "message": f"Черный список очищен, удалено {count} записей"
        })
    except Exception as e:
        logger.error(f"[API] Ошибка очистки черного списка: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================================
# API - АНАЛИТИКА
# ============================================================================
@app.route('/api/analytics/statistics')
def api_analytics_statistics():
    try:
        stats = analytics.get_full_statistics()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analytics/recommendations')
def api_analytics_recommendations():
    try:
        pending = db.get_pending_recommendations()
        return jsonify({"recommendations": pending, "count": len(pending)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analytics/recommendations/generate', methods=['POST'])
def api_generate_recommendations():
    try:
        current_settings = state.trader.get_settings()
        recommendations = analytics.analyze_and_recommend(current_settings)
        return jsonify({
            "success": True,
            "new_recommendations": len(recommendations),
            "recommendations": recommendations
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analytics/recommendations/<int:rec_id>/apply', methods=['POST'])
def api_apply_recommendation(rec_id):
    try:
        current_settings = state.trader.get_settings()
        success, message, new_settings = analytics.apply_recommendation(rec_id, current_settings)
        if success:
            state.trader.update_settings(new_settings, source='RECOMMENDATION')
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "message": message}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analytics/recommendations/<int:rec_id>/ignore', methods=['POST'])
def api_ignore_recommendation(rec_id):
    try:
        db.ignore_recommendation(rec_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - ПОСТ-МОРТЕМ
# ============================================================================
@app.route('/api/post_mortems')
def api_post_mortems():
    """Получить пост-мортемы"""
    try:
        pending = db.get_pending_post_mortems()
        all_pm = db.get_post_mortems(limit=50)
        return jsonify({
            "pending": pending,
            "all": all_pm,
            "pending_count": len(pending)
        })
    except Exception as e:
        logger.error(f"[API] Post-mortems error: {e}")
        return jsonify({"error": str(e), "pending": [], "all": [], "pending_count": 0}), 500

@app.route('/api/post_mortems/<int:pm_id>/apply', methods=['POST'])
def api_apply_post_mortem(pm_id):
    """Применить рекомендации пост-мортема"""
    try:
        current_settings = state.trader.get_settings()
        success, message, new_settings = analytics.apply_post_mortem_action(
            pm_id, 'apply', current_settings
        )
        if success:
            state.trader.update_settings(new_settings, source='POST_MORTEM')
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "message": message}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/post_mortems/<int:pm_id>/dismiss', methods=['POST'])
def api_dismiss_post_mortem(pm_id):
    """Отклонить пост-мортем"""
    try:
        db.update_post_mortem_action(pm_id, 'DISMISSED')
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - AI
# ============================================================================
@app.route('/api/ai/status')
def api_ai_status():
    """Статус AI движков"""
    result = {
        'deepseek_connected': False,
        'groq_connected': False,
        'active_provider': 'none',
        'providers': {}
    }
    if state.ai_engine:
        # Проверяем какие движки подключены
        if hasattr(state.ai_engine, 'engines'):
            for name, engine in state.ai_engine.engines.items():
                if engine:
                    stats = engine.get_stats() if hasattr(engine, 'get_stats') else {}
                    is_mock = stats.get('mock_mode', False)
                    if name == 'deepseek' and not is_mock:
                        result['deepseek_connected'] = True
                    elif name == 'groq' and not is_mock:
                        result['groq_connected'] = True
                    elif name == 'mock':
                        pass  # Mock не считается подключенным
                    result['providers'][name] = {
                        'connected': not is_mock,
                        'total_requests': stats.get('total_requests', 0),
                        'successful_requests': stats.get('successful_requests', 0),
                        'failed_requests': stats.get('failed_requests', 0),
                        'last_error': stats.get('last_error', '')
                    }
            # Активный провайдер
            if hasattr(state.ai_engine, 'active_provider'):
                result['active_provider'] = state.ai_engine.active_provider
        else:
            # Одиночный движок
            stats = state.ai_engine.get_stats()
            if not stats.get('mock_mode'):
                provider = stats.get('provider', 'unknown')
                if provider == 'deepseek':
                    result['deepseek_connected'] = True
                elif provider == 'groq':
                    result['groq_connected'] = True
    return jsonify(result)

@app.route('/api/ai/ab_statistics')
def api_ai_ab_statistics():
    """Статистика A/B тестирования"""
    try:
        stats = db.get_ai_ab_statistics()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ai/test_key', methods=['POST'])
def api_ai_test_key():
    """Тестирование AI ключа"""
    data = request.get_json() or {}
    provider = data.get('provider', 'deepseek')
    api_key = data.get('api_key', '')
    if not api_key:
        return jsonify({"success": False, "error": "API key required"}), 400
    try:
        if provider == 'deepseek':
            from ai_engine import DeepSeekEngine
            engine = DeepSeekEngine(api_key)
        elif provider == 'groq':
            from ai_engine import GroqEngine
            engine = GroqEngine(api_key)
        else:
            return jsonify({"success": False, "error": "Unknown provider"}), 400
        if engine.test_connection():
            return jsonify({"success": True, "message": f"{provider} API работает"})
        else:
            return jsonify({"success": False, "error": "Ключ не работает"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================================
# API - ДИАГНОСТИКА
# ============================================================================
@app.route('/api/diagnostic')
def api_diagnostic():
    """Диагностика почему не открываются сделки"""
    try:
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "version": "v5.0",
            "checks": []
        }
        # 1. Сканер активен
        scanner_ok = state.trader and not state.trader.scanner_paused
        result["checks"].append({
            "name": "Сканер активен",
            "status": "OK" if scanner_ok else "FAIL",
            "value": "Работает" if scanner_ok else "НА ПАУЗЕ!"
        })
        # 2. Дневной лимит убытков
        if state.trader:
            daily_ok = not state.trader.daily_loss_stop
            result["checks"].append({
                "name": "Дневной лимит убытков",
                "status": "OK" if daily_ok else "FAIL",
                "value": "Не достигнут" if daily_ok else "ДОСТИГНУТ!"
            })
        # 3. Лимит позиций
        if state.trader:
            open_count = len([p for p in state.trader.positions.values() if p.status == "OPEN"])
            max_pos = state.trader.settings.max_positions
            pos_ok = open_count < max_pos
            result["checks"].append({
                "name": "Лимит позиций",
                "status": "OK" if pos_ok else "FAIL",
                "value": f"{open_count}/{max_pos} открыто"
            })
        # 4. Баланс
        if state.trader:
            margin_needed = state.trader.settings.position_size / state.trader.settings.leverage
            balance_ok = state.trader.balance >= margin_needed
            result["checks"].append({
                "name": "Баланс",
                "status": "OK" if balance_ok else "FAIL",
                "value": f"${state.trader.balance:.2f} (нужно ${margin_needed:.2f})"
            })
        # 5. Порог Confidence
        if state.trader:
            result["checks"].append({
                "name": "Порог Confidence",
                "status": "INFO",
                "value": f"{state.trader.settings.confidence_threshold}%"
            })
        # 6. AI провайдер
        if state.trader:
            result["checks"].append({
                "name": "AI провайдер",
                "status": "INFO",
                "value": state.trader.settings.ai_provider
            })
        # 6.1. Адаптивный трейлинг
        if state.trader:
            adaptive_on = state.trader.settings.adaptive_trailing_enabled
            trail_dist = state.trader.settings.trailing_distance_pct
            result["checks"].append({
                "name": "Адаптивный трейлинг",
                "status": "OK" if adaptive_on else "INFO",
                "value": f"{'ВКЛ (ATR-based)' if adaptive_on else 'ВЫКЛ'} | По умолч: {trail_dist}%"
            })
        # 7. Растущие и падающие монеты
        with state.lock:
            all_count = len(state.filtered_coins)
            # Подсчитываем отдельно
            short_candidates = [c for c in state.filtered_coins if c.get('change_24h', 0) >= state.trader.settings.min_change_filter]
            long_candidates = [c for c in state.filtered_coins if c.get('change_24h', 0) <= -state.trader.settings.min_change_filter]
            
            result["checks"].append({
                "name": "Кандидатов для SHORT/LONG",
                "status": "OK" if all_count > 0 else "WARN",
                "value": f"Всего: {all_count} (SHORT: {len(short_candidates)}, LONG: {len(long_candidates)})"
            })
        # 8. AI статистика - показываем реальные движки
        if state.ai_engine:
            if hasattr(state.ai_engine, 'engines'):
                engines_list = list(state.ai_engine.engines.keys())
                result["checks"].append({
                    "name": "AI движки подключены",
                    "status": "OK" if 'deepseek' in engines_list or 'groq' in engines_list else "WARN",
                    "value": ", ".join(engines_list) if engines_list else "НЕТ"
                })
            # Проверяем есть ли реальные ключи
            if state.trader:
                ds_key = bool(state.trader.settings.deepseek_api_key)
                gr_key = bool(state.trader.settings.groq_api_key)
                result["checks"].append({
                    "name": "API ключи настроены",
                    "status": "OK" if ds_key or gr_key else "FAIL",
                    "value": f"DeepSeek: {'Да' if ds_key else 'Нет'}, Groq: {'Да' if gr_key else 'Нет'}"
                })
            ai_stats = state.ai_engine.get_stats()
            if isinstance(ai_stats, dict):
                for provider, stats in ai_stats.items():
                    if isinstance(stats, dict):
                        total = stats.get('total_requests', 0)
                        success = stats.get('successful_requests', 0)
                        failed = stats.get('failed_requests', 0)
                        last_error = stats.get('last_error', '')
                        # Определяем статус
                        if total > 0 and success == 0:
                            status = "FAIL"
                        elif failed > success and total > 3:
                            status = "WARN"
                        else:
                            status = "INFO"
                        value = f"Запросов: {total}, Успешных: {success}, Ошибок: {failed}"
                        result["checks"].append({
                            "name": f"AI {provider}",
                            "status": status,
                            "value": value
                        })
                        # Если есть ошибка - показываем отдельно
                        if last_error and failed > 0:
                            result["checks"].append({
                                "name": f"AI {provider} ошибка",
                                "status": "FAIL",
                                "value": last_error[:100]
                            })
        # 9. Последнее сканирование
        if state.last_scan_time:
            time_since = (datetime.utcnow() - state.last_scan_time).total_seconds()
            result["checks"].append({
                "name": "Последнее сканирование",
                "status": "OK" if time_since < 600 else "WARN",
                "value": f"{time_since:.0f} сек назад"
            })
        # Рекомендация
        fails = [c for c in result["checks"] if c["status"] == "FAIL"]
        if fails:
            result["recommendation"] = f"ПРОБЛЕМА: {fails[0]['name']} - {fails[0]['value']}"
        else:
            warns = [c for c in result["checks"] if c["status"] == "WARN"]
            if warns:
                # Специальная проверка на mock
                engines_check = next((c for c in result["checks"] if c["name"] == "AI движки подключены"), None)
                if engines_check and 'mock' in engines_check["value"].lower():
                    result["recommendation"] = "ВНИМАНИЕ: Работает MOCK AI! Введите API ключ DeepSeek или Groq в Настройках."
                else:
                    result["recommendation"] = f"ВНИМАНИЕ: {warns[0]['name']} - {warns[0]['value']}"
            else:
                result["recommendation"] = "Все OK. Ждем сигналов с confidence >= порога."
        return jsonify(result)
    except Exception as e:
        logger.error(f"[API] Diagnostic error: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - ИСТОРИЯ
# ============================================================================
@app.route('/api/history/status')
def api_history_status():
    return jsonify(history_loader.get_status())

@app.route('/api/history/load', methods=['POST'])
def api_history_load():
    if history_loader.is_loading:
        return jsonify({"error": "Already loading"}), 400
    data = request.get_json() or {}
    days = data.get('days', 30)
    min_change = data.get('min_change', 10.0)
    def load_thread():
        history_loader.load_history(days=days, min_change=min_change)
    threading.Thread(target=load_thread, daemon=True).start()
    return jsonify({"success": True, "message": f"Загрузка: {days} дней"})

@app.route('/api/analytics/settings_history')
def api_settings_history():
    try:
        limit = request.args.get('limit', 50, type=int)
        history = db.get_settings_history(limit)
        return jsonify(history)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - ЭКСПОРТ
# ============================================================================
@app.route('/api/export/trades')
def api_export_trades():
    try:
        csv_data = db.export_trades_csv()
        filename = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export/statistics')
def api_export_statistics():
    try:
        csv_data = db.export_statistics_csv()
        filename = f"statistics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export/trades_detailed')
def api_export_trades_detailed():
    """Экспорт сделок с детальной информацией о SL"""
    try:
        csv_data = db.export_trades_detailed_csv()
        filename = f"trades_detailed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export/ai_analyses')
def api_export_ai_analyses():
    """Экспорт истории анализов AI"""
    try:
        csv_data = db.export_ai_analyses_csv()
        filename = f"ai_analyses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export/trade_history')
def api_export_trade_history():
    """Экспорт истории хода сделок"""
    try:
        trade_id = request.args.get('trade_id')
        csv_data = db.export_trade_price_history_csv(trade_id)
        filename = f"trade_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export/diagnostic')
def api_export_diagnostic():
    """Экспорт диагностики в JSON"""
    try:
        # Получаем данные диагностики
        result = {"checks": [], "timestamp": datetime.utcnow().isoformat(), "version": "v5.0"}
        # Собираем данные
        if state.trader:
            settings = state.trader.get_settings()
            portfolio = state.trader.get_portfolio()
            result['settings'] = settings
            result['portfolio'] = portfolio
        if state.ai_engine:
            result['ai_stats'] = state.ai_engine.get_stats()
            if hasattr(state.ai_engine, 'engines'):
                result['engines'] = list(state.ai_engine.engines.keys())
        result['health'] = state.health_status
        filename = f"diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            json.dumps(result, indent=2, ensure_ascii=False),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - AI ANALYSES (История анализов)
# ============================================================================
@app.route('/api/ai/analyses')
def api_ai_analyses():
    """Получить историю анализов AI"""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        symbol = request.args.get('symbol')
        provider = request.args.get('provider')
        analyses = db.get_ai_analyses(limit=limit, offset=offset, symbol=symbol, provider=provider)
        total = db.get_ai_analyses_count()
        return jsonify({
            "analyses": analyses,
            "total": total,
            "limit": limit,
            "offset": offset
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trade/<trade_id>/history')
def api_trade_history(trade_id):
    """Получить историю хода конкретной сделки"""
    try:
        history = db.get_trade_price_history(trade_id)
        return jsonify({"trade_id": trade_id, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - PROMPTS (Управление промптами)
# ============================================================================
@app.route('/api/prompts')
def api_get_prompts():
    """Получить все промпты"""
    try:
        prompts = db.get_all_prompts()
        active = db.get_active_prompt()
        # Добавляем дефолтный промпт если его нет в БД
        from ai_engine import BaseAIEngine
        default_prompt = BaseAIEngine._get_default_prompt(None)
        return jsonify({
            "prompts": prompts,
            "active": active,
            "default_prompt": default_prompt
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/prompts', methods=['POST'])
def api_save_prompt():
    """Сохранить промпт"""
    try:
        data = request.get_json()
        name = data.get('name', 'custom')
        prompt_text = data.get('prompt_text', '')
        is_active = data.get('is_active', True)
        if not prompt_text:
            return jsonify({"error": "Prompt text is required"}), 400
        prompt_id = db.save_ai_prompt(name, prompt_text, is_active)
        return jsonify({"success": True, "id": prompt_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/prompts/<name>', methods=['DELETE'])
def api_delete_prompt(name):
    """Удалить промпт"""
    try:
        success = db.delete_prompt(name)
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/prompts/reset', methods=['POST'])
def api_reset_prompt():
    """Сбросить на дефолтный промпт"""
    try:
        # Деактивируем все кастомные промпты
        with db.get_cursor() as cur:
            cur.execute('UPDATE ai_prompts SET is_active = 0')
        return jsonify({"success": True, "message": "Reset to default prompt"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - WORK HOURS (Рабочие часы)
# ============================================================================
@app.route('/api/work_hours/status')
def api_work_hours_status():
    """Статус рабочих часов"""
    try:
        if not state.trader:
            return jsonify({"error": "Trader not initialized"}), 500
        in_hours, msg = state.trader.is_work_hours()
        settings = state.trader.settings
        return jsonify({
            "enabled": settings.work_hours_enabled,
            "start": settings.work_hours_start,
            "end": settings.work_hours_end,
            "in_work_hours": in_hours,
            "message": msg,
            "current_time": datetime.utcnow().strftime("%H:%M")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - TELEGRAM
# ============================================================================
@app.route('/api/telegram/status')
def api_telegram_status():
    return jsonify(telegram_bot.get_stats())

@app.route('/api/telegram/test', methods=['POST'])
def api_telegram_test():
    """Отправить тестовое сообщение"""
    try:
        success = telegram_bot.send_test_message()
        if success:
            return jsonify({"success": True, "message": "Тестовое сообщение отправлено"})
        else:
            return jsonify({"success": False, "error": "Не удалось отправить"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================================
# API - АДАПТИВНЫЙ ТРЕЙЛИНГ
# ============================================================================
@app.route('/api/trailing/configs')
def api_trailing_configs():
    """Получить конфигурации трейлинга по символам"""
    try:
        # Получаем все символы с сделками
        trades = db.get_trades(limit=500, only_closed=True)
        symbols = list(set(t['symbol'] for t in trades))
        configs = []
        for symbol in symbols[:50]:  # Лимит 50
            config = db.get_symbol_trailing_config(symbol)
            if config:
                configs.append(config)
        return jsonify(configs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trailing/config/<path:symbol>')
def api_trailing_config(symbol):
    """Получить конфигурацию трейлинга для символа"""
    try:
        config = db.get_symbol_trailing_config(symbol)
        return jsonify(config or {})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trailing/config/<path:symbol>', methods=['POST'])
def api_update_trailing_config(symbol):
    """Обновить конфигурацию трейлинга для символа"""
    data = request.get_json() or {}
    try:
        db.save_symbol_trailing_config(symbol, data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - SMART AGENT v5.0
# ============================================================================
@app.route('/api/smart/status')
def api_smart_status():
    """Статус Smart Agent"""
    if state.smart_agent:
        return jsonify(state.smart_agent.get_status())
    return jsonify({
        "available": SMART_AGENT_AVAILABLE,
        "running": False,
        "message": "Smart Agent not initialized"
    })

@app.route('/api/smart/chat', methods=['POST'])
def api_smart_chat():
    """Чат с умным агентом"""
    if not state.smart_agent:
        logger.error("[SMART_AGENT] Chat called but agent not initialized!")
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    data = request.get_json() or {}
    message = data.get('message', '')
    
    if not message:
        return jsonify({"error": "No message provided"}), 400
    
    try:
        logger.info(f"[SMART_AGENT] Chat message: {message}")
        logger.info(f"[SMART_AGENT] tools.trader = {state.smart_agent.tools.trader}")
        
        response = state.smart_agent.process_message(message)
        
        logger.info(f"[SMART_AGENT] Response (first 200 chars): {response[:200] if response else 'None'}")
        
        return jsonify({
            "success": True,
            "response": response
        })
    except Exception as e:
        logger.error(f"[SMART_AGENT] Chat error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/quick/<action>')
def api_smart_quick(action):
    """Быстрые команды Smart Agent"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    try:
        if action == 'btc':
            response = state.smart_agent.quick_btc()
        elif action == 'positions':
            response = state.smart_agent.quick_positions()
        elif action == 'stats':
            response = state.smart_agent.quick_stats()
        elif action == 'suggest':
            response = state.smart_agent.quick_suggest()
        elif action == 'diagnose':
            response = state.smart_agent.diagnose()
        elif action == 'capabilities':
            response = state.smart_agent.explain_capabilities()
        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400
        
        return jsonify({
            "success": True,
            "action": action,
            "response": response
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/analyze/<symbol>')
def api_smart_analyze(symbol):
    """Анализ символа через Smart Agent"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    try:
        response = state.smart_agent.quick_analyze(symbol)
        return jsonify({
            "success": True,
            "symbol": symbol,
            "analysis": response
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/close/<trade_id>', methods=['POST'])
def api_smart_close(trade_id):
    """Прямое закрытие позиции через Smart Agent (без AI)"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    try:
        data = request.get_json() or {}
        reason = data.get('reason', 'AGENT_DIRECT')
        
        response = state.smart_agent.quick_close(trade_id, reason)
        success = "✅" in response or "закрыта" in response.lower()
        
        return jsonify({
            "success": success,
            "trade_id": trade_id,
            "response": response
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/diagnose')
def api_smart_diagnose():
    """Диагностика Smart Agent"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    try:
        response = state.smart_agent.diagnose()
        return jsonify({
            "success": True,
            "diagnosis": response
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/capabilities')
def api_smart_capabilities():
    """Возможности Smart Agent"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    try:
        response = state.smart_agent.capabilities()
        return jsonify({
            "success": True,
            "capabilities": response
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/recalculate_pnl', methods=['POST'])
def api_recalculate_pnl():
    """Пересчитать PnL из закрытых позиций"""
    if not state.trader:
        return jsonify({"error": "Trader not initialized"}), 500
    
    try:
        result = state.trader.recalculate_pnl()
        return jsonify({
            "success": True,
            "result": result
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/analyze_positions', methods=['POST'])
def api_smart_analyze_positions():
    """Автономный анализ позиций через Smart Agent"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    try:
        response = state.smart_agent.analyze_positions()
        return jsonify({
            "success": True,
            "analysis": response
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/brain/stats')
def api_smart_brain_stats():
    """Статистика памяти агента"""
    try:
        from agent_brain import brain
        stats = brain.get_brain_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/brain/commands')
def api_smart_brain_commands():
    """Команды пользователя из памяти"""
    try:
        from agent_brain import brain
        commands = brain.get_active_commands()
        return jsonify({
            "commands": commands,
            "count": len(commands)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/brain/conversations')
def api_smart_brain_conversations():
    """История разговоров"""
    try:
        from agent_brain import brain
        limit = request.args.get('limit', 20, type=int)
        conversations = brain.get_recent_conversations(limit)
        return jsonify({
            "conversations": conversations,
            "count": len(conversations)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/brain/strategies')
def api_smart_brain_strategies():
    """Стратегии из памяти"""
    try:
        from agent_brain import brain
        strategies = brain.get_all_strategies()
        recommended = brain.get_recommended_strategies()
        return jsonify({
            "all": strategies,
            "recommended": recommended
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/brain/lessons')
def api_smart_brain_lessons():
    """Уроки из ошибок"""
    try:
        from agent_brain import brain
        lessons = brain.get_lessons(limit=30)
        return jsonify({
            "lessons": lessons,
            "count": len(lessons)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/tools/suggest_strategies', methods=['POST'])
def api_smart_suggest_strategies():
    """Сгенерировать предложения стратегий"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    try:
        strategies = state.smart_agent.tools.generate_strategy_suggestions()
        return jsonify({
            "success": True,
            "strategies": strategies
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/tools/backtest', methods=['POST'])
def api_smart_backtest():
    """Бэктест стратегии"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    data = request.get_json() or {}
    strategy = data.get('strategy', {})
    symbol = data.get('symbol')
    period_days = data.get('period_days', 30)
    
    try:
        result = state.smart_agent.tools.backtest_strategy(strategy, symbol, period_days)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/smart/tools/patterns')
def api_smart_patterns():
    """Найти паттерны в сделках"""
    if not state.smart_agent:
        return jsonify({"error": "Smart Agent not initialized"}), 500
    
    pattern_type = request.args.get('type', 'losing')
    limit = request.args.get('limit', 50, type=int)
    
    try:
        result = state.smart_agent.tools.find_patterns(pattern_type, limit)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - LIVE TRADING v6.0
# ============================================================================
@app.route('/api/live/status')
def api_live_status():
    """Статус реальной торговли"""
    try:
        stats = live_trader.get_stats()
        balance = live_trader.get_balance()
        positions = live_trader.get_positions()
        return jsonify({
            'success': True,
            'api_configured': live_trader.connected,
            'enabled': live_trader.enabled,
            'testnet': live_trader.testnet,
            'stats': stats,
            'balance': balance,
            'positions': positions
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'api_configured': False,
            'enabled': False,
            'error': str(e)
        })

@app.route('/api/live/test')
def api_live_test():
    """Тест подключения к Binance"""
    try:
        success, message, info = live_trader.test_connection()
        return jsonify({
            'success': success,
            'message': message,
            'info': info
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/balance')
def api_live_balance():
    """Баланс на Binance"""
    try:
        balance = live_trader.get_balance()
        return jsonify({
            'success': True,
            'balance': balance
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/enable', methods=['POST'])
def api_live_enable():
    """Включить/выключить реальную торговлю"""
    try:
        data = request.get_json() or {}
        enable = data.get('enable', False)
        
        if enable and not live_trader.connected:
            return jsonify({
                'success': False,
                'error': 'Сначала настройте API ключи Binance'
            })
        
        live_trader.enable(enable)
        
        return jsonify({
            'success': True,
            'enabled': enable,
            'message': f"Live trading {'ВКЛЮЧЕН' if enable else 'ВЫКЛЮЧЕН'}"
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/configure', methods=['POST'])
def api_live_configure():
    """Настройка API ключей Binance"""
    try:
        data = request.get_json() or {}
        api_key = data.get('api_key', '')
        api_secret = data.get('api_secret', '')
        testnet = data.get('testnet', False)
        
        if not api_key or not api_secret:
            return jsonify({
                'success': False,
                'error': 'API ключи не указаны'
            })
        
        # Сохраняем в настройки трейдера
        if state.trader:
            state.trader.update_settings({
                'binance_api_key': api_key,
                'binance_secret_key': api_secret
            }, source='live_config')
        
        # Конфигурируем live trader
        success = live_trader.configure(api_key, api_secret, testnet)
        
        if success:
            # Тестируем подключение
            test_ok, test_msg, test_info = live_trader.test_connection()
            return jsonify({
                'success': True,
                'connected': test_ok,
                'message': test_msg,
                'info': test_info
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Не удалось подключиться к Binance'
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/positions')
def api_live_positions():
    """Список LIVE позиций"""
    try:
        positions = live_trader.get_positions()
        return jsonify({
            'success': True,
            'positions': positions,
            'count': len(positions)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/close/<position_id>', methods=['POST'])
def api_live_close(position_id):
    """Закрыть LIVE позицию"""
    try:
        data = request.get_json() or {}
        reason = data.get('reason', 'Manual close')
        
        success, message, pnl = live_trader.close_position(position_id, reason)
        
        return jsonify({
            'success': success,
            'message': message,
            'pnl': pnl
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/close_all', methods=['POST'])
def api_live_close_all():
    """Закрыть все LIVE позиции"""
    try:
        data = request.get_json() or {}
        reason = data.get('reason', 'Close all')
        
        closed, total_pnl = live_trader.close_all_positions(reason)
        
        return jsonify({
            'success': True,
            'closed': closed,
            'total_pnl': total_pnl
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/config', methods=['GET', 'POST'])
def api_live_config():
    """Получить/обновить конфигурацию LIVE торговли"""
    try:
        if request.method == 'GET':
            return jsonify({
                'success': True,
                'config': live_trader.config
            })
        else:
            data = request.get_json() or {}
            live_trader.update_config(data)
            return jsonify({
                'success': True,
                'config': live_trader.config
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/smart/brain/export')
def api_smart_export_brain():
    """Экспорт памяти агента"""
    try:
        from agent_brain import brain
        data = brain.export_brain()
        filename = f"agent_brain_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            json.dumps(data, indent=2, ensure_ascii=False),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# API - ГРАФИКИ
# ============================================================================
@app.route('/api/chart/data')
def api_chart_data():
    """Данные для графика"""
    symbol = request.args.get('symbol', 'BTC/USDT')
    timeframe = request.args.get('timeframe', '15m')
    limit = request.args.get('limit', 100, type=int)
    try:
        # Конвертируем символ
        chart_symbol = symbol
        if not chart_symbol.endswith(':USDT') and '/USDT' in chart_symbol:
            chart_symbol = chart_symbol + ':USDT'
        # Получаем OHLCV
        ohlcv = state.exchange.fetch_ohlcv(chart_symbol, timeframe, limit=limit)
        # Форматируем данные
        data = []
        for candle in ohlcv:
            data.append({
                'time': candle[0] / 1000,
                'open': candle[1],
                'high': candle[2],
                'low': candle[3],
                'close': candle[4]
            })
        # Получаем текущую цену
        current_price = get_price_for_symbol(symbol)
        return jsonify({
            'success': True,
            'symbol': symbol,
            'timeframe': timeframe,
            'data': data,
            'current_price': current_price,
            'count': len(data)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# STARTUP & SHUTDOWN
# ============================================================================
def shutdown_handler(signum, frame):
    """Обработчик graceful shutdown"""
    logger.info(f"[APP] Received signal {signum} - shutting down gracefully")
    state.running = False
    # Ждем завершения всех потоков
    timeout = 10  # секунд
    start_time = time.time()
    while time.time() - start_time < timeout:
        active_threads = [t for t in threading.enumerate() if t != threading.current_thread()]
        if not active_threads:
            break
        time.sleep(0.5)
    logger.info("[APP] Graceful shutdown completed")
    sys.exit(0)

def start_app():
    """Основная инициализация приложения"""
    logger.info("=" * 60)
    logger.info("RVV Hunter v6.0 - LIVE Trading Ready")
    logger.info("=" * 60)
    # Загружаем конфиг
    config = load_config()
    # Инициализация
    state.exchange = init_exchange()
    state.trader = VirtualTrader()  # ✅ только один раз
    # Загружаем настройки из конфига
    if config:
        state.trader.update_settings(config)
    
    # === МИГРАЦИЯ: обновляем старые фильтры если нужно ===
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            filters = cfg.get('filters', {})
            migrated = False
            # Убедимся что btc_neutral_mode существует (без принудительной перезаписи)
            if 'btc_neutral_mode' not in filters:
                filters['btc_neutral_mode'] = 'none'
                migrated = True
            if 'btc_bullish_min_strength' not in filters:
                filters['btc_bullish_min_strength'] = 0.5
                migrated = True
            if 'btc_bearish_min_strength' not in filters:
                filters['btc_bearish_min_strength'] = 0.5
                migrated = True
            if migrated:
                cfg['filters'] = filters
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                logger.info("[CONFIG] ✅ Миграция фильтров: btc_neutral_mode → 'none'")
            # Явно синхронизируем фильтры в trader
            if state.trader and filters:
                state.trader.settings.btc_bullish_mode = filters.get('btc_bullish_mode', 'long_only')
                state.trader.settings.btc_bearish_mode = filters.get('btc_bearish_mode', 'short_only')
                state.trader.settings.btc_neutral_mode = filters.get('btc_neutral_mode', 'none')
                state.trader.settings.btc_bullish_min_strength = float(filters.get('btc_bullish_min_strength', 0.5))
                state.trader.settings.btc_bearish_min_strength = float(filters.get('btc_bearish_min_strength', 0.5))
                state.trader.settings.close_long_on_neutral = filters.get('close_long_on_neutral', False)
                state.trader.settings.close_short_on_neutral = filters.get('close_short_on_neutral', False)
                # Автозакрытие при ослаблении тренда (v6.1)
                state.trader.settings.close_long_on_weak_bull = filters.get('close_long_on_weak_bull', False)
                state.trader.settings.close_long_weak_bull_threshold = float(filters.get('close_long_weak_bull_threshold', 0.5))
                state.trader.settings.close_short_on_weak_bear = filters.get('close_short_on_weak_bear', False)
                state.trader.settings.close_short_weak_bear_threshold = float(filters.get('close_short_weak_bear_threshold', 0.5))
                state.trader.filters = filters
                logger.info(f"[CONFIG] ✅ Фильтры синхронизированы: neutral={filters.get('btc_neutral_mode')}, bull_min={filters.get('btc_bullish_min_strength')}, bear_min={filters.get('btc_bearish_min_strength')}")
    except Exception as e:
        logger.warning(f"[CONFIG] Ошибка миграции фильтров: {e}")
    # Получаем RAW настройки с полными ключами для инициализации
    settings_raw = state.trader.get_settings_raw()
    # Инициализируем компоненты
    state.ai_engine = init_ai_engine(settings_raw)
    state.agent = init_agent(settings_raw)  # Единый Crypto Agent
    
    # â•â•â• SMART AGENT v5.0 â•â•â•
    if SMART_AGENT_AVAILABLE:
        try:
            deepseek_key = settings_raw.get('deepseek_api_key', '')
            groq_key = settings_raw.get('groq_api_key', '')
            state.smart_agent = create_smart_agent(deepseek_key, groq_key)
            state.smart_agent.set_components(
                trader=state.trader,
                exchange=state.exchange,
                db=db
            )
            state.health_status['smart_agent'] = {'status': 'ok', 'version': 'v5.0'}
            logger.info("[SMART_AGENT] âœ… Smart Agent v5.0 initialized")
        except Exception as e:
            logger.error(f"[SMART_AGENT] Init error: {e}")
            state.health_status['smart_agent'] = {'status': 'error', 'message': str(e)}
    
    # Подключаем callback для записи результатов в память (ВСЕГДА, независимо от агента!)
    def on_position_closed_handler(sym, pnl, reason, side, trade_id):
        """Callback при закрытии позиции — записывает в память и уведомляет агента"""
        try:
            # Записываем результат в персистентную память (SQLite) - ВСЕГДА!
            db.update_coin_memory(sym, pnl, hold_minutes=0, side=side)
            logger.info(f"[MEMORY] Saved: {sym} {side}, PnL: ${pnl:.2f}")
            
            # Если агент есть - удаляем позицию из отслеживания
            if state.agent:
                state.agent.untrack_position(trade_id)
        except Exception as e:
            logger.error(f"[MEMORY] on_position_closed error: {e}")
    
    state.trader.on_position_closed = on_position_closed_handler
    
    init_telegram(settings_raw)
    init_live_trading(settings_raw)
    state.running = True
    # Запускаем потоки
    threading.Thread(target=scanner_loop, daemon=True, name="ScannerThread").start()
    threading.Thread(target=price_loop, daemon=True, name="PriceThread").start()
    threading.Thread(target=health_check_loop, daemon=True, name="HealthThread").start()
    
    # v5.9: Инициализируем WebSocket (после небольшой задержки для загрузки данных)
    def delayed_ws_init():
        time.sleep(5)  # Даём время для первого сканирования
        init_websocket()
    threading.Thread(target=delayed_ws_init, daemon=True, name="WebSocketInit").start()
    
    # Первоначальное обновление цен
    update_market_prices()
    logger.info("[APP] Система запущена")
    logger.info(f"[DB] Записей в базе: {db.get_market_history_count()}")
    logger.info(f"[BLACKLIST] В черном списке: {db.get_blacklist_count()} монет")
    logger.info(f"[SETTINGS] Торговый режим: {state.trader.settings.trade_mode}")

if __name__ == '__main__':
    # Регистрируем обработчики сигналов
    import signal
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    start_app()
    # Получаем порт из конфига или используем 8080 по умолчанию
    config = load_config()
    config_port = config.get('port', 8082)
    debug_mode = config.get('debug', False)
    print("=" * 60)
    print(f"  RVV Hunter v6.0 - LIVE Trading Ready")
    print(f"  http://127.0.0.1:{config_port}")
    print("=" * 60)
    app.run(host='127.0.0.1', port=config_port, debug=debug_mode, threaded=True, use_reloader=False)