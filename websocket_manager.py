"""
WebSocket Manager для RVV Hunter v6.0
Поддержка Binance Futures и OKX Swap WebSocket для real-time цен
"""

import json
import logging
import os
import threading
import time
from typing import Dict, Set, Callable, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    logger.warning("[WS] websocket-client не установлен. pip install websocket-client")


class ExchangeWebSocketManager:
    """Универсальный менеджер WebSocket — Binance и OKX"""

    def __init__(self, exchange_id: str = 'binance'):
        self.exchange_id = exchange_id.lower()
        self.symbols: Set[str] = set()
        self.ws: Optional[websocket.WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.running = False
        self.connected = False
        self.reconnect_delay = 5
        self.max_reconnects = 10
        self.reconnect_count = 0

        self.price_callback: Optional[Callable] = None
        self.connection_callback: Optional[Callable] = None

        self.stats = {
            'messages_received': 0,
            'last_message_time': None,
            'connects': 0,
            'disconnects': 0
        }

        self.lock = threading.Lock()
        self.prices: Dict[str, float] = {}

    def set_price_callback(self, callback: Callable[[str, float, Dict], None]):
        self.price_callback = callback

    def set_connection_callback(self, callback: Callable[[bool], None]):
        self.connection_callback = callback

    def add_symbols(self, symbols: list):
        with self.lock:
            for symbol in symbols:
                clean = self._normalize_symbol(symbol)
                if clean:
                    self.symbols.add(clean)

            if self.connected:
                logger.info(f"[WS] Добавлены символы, переподключаемся: {symbols}")
                self._reconnect()

    def remove_symbols(self, symbols: list):
        with self.lock:
            for symbol in symbols:
                clean = self._normalize_symbol(symbol)
                self.symbols.discard(clean)

            if self.connected and self.symbols:
                self._reconnect()
            elif self.connected and not self.symbols:
                self.stop()

    def set_symbols(self, symbols: list):
        with self.lock:
            new_symbols = set()
            for symbol in symbols:
                clean = self._normalize_symbol(symbol)
                if clean:
                    new_symbols.add(clean)

            if new_symbols != self.symbols:
                self.symbols = new_symbols
                if self.connected:
                    self._reconnect()

    def _normalize_symbol(self, symbol: str) -> str:
        if not symbol:
            return ""
        clean = symbol.upper().replace('/USDT', '').replace(':USDT', '').replace('USDT', '')
        return clean

    def _build_url(self) -> str:
        if not self.symbols:
            return ""

        if self.exchange_id == 'okx':
            # OKX: подключаемся к публичному WS, подписка через сообщение
            return "wss://ws.okx.com:8443/ws/v5/public"
        else:
            # Binance: стримы в URL
            streams = [f"{s.lower()}usdt@miniTicker" for s in self.symbols]
            return "wss://fstream.binance.com/stream?streams=" + "/".join(streams)

    def _build_subscribe_msg(self) -> Optional[str]:
        """OKX требует подписку через сообщение после подключения"""
        if self.exchange_id != 'okx':
            return None

        args = []
        for sym in self.symbols:
            args.append({
                "channel": "tickers",
                "instId": f"{sym}-USDT-SWAP"
            })
        return json.dumps({"op": "subscribe", "args": args})

    def start(self):
        if not WEBSOCKET_AVAILABLE:
            logger.error("[WS] websocket-client не установлен!")
            return False

        if self.running:
            logger.warning("[WS] Уже запущен")
            return True

        if not self.symbols:
            logger.warning("[WS] Нет символов для отслеживания")
            return False

        self.running = True
        self.reconnect_count = 0

        self.ws_thread = threading.Thread(target=self._ws_loop, daemon=True, name="WebSocketThread")
        self.ws_thread.start()

        logger.info(f"[WS] [{self.exchange_id.upper()}] Запущен для {len(self.symbols)} символов")
        return True

    def stop(self):
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.connected = False
        logger.info("[WS] Остановлен")

    def _reconnect(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def _ws_loop(self):
        # Убираем прокси — WS не работает через SOCKS5/Tor
        saved_env = {}
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                     'ALL_PROXY', 'all_proxy', 'SOCKS_PROXY', 'socks_proxy'):
            if key in os.environ:
                saved_env[key] = os.environ.pop(key)

        try:
            self._ws_loop_inner()
        finally:
            os.environ.update(saved_env)

    def _ws_loop_inner(self):
        while self.running:
            try:
                url = self._build_url()
                if not url:
                    logger.warning("[WS] Нет URL для подключения")
                    time.sleep(5)
                    continue

                logger.info(f"[WS] [{self.exchange_id.upper()}] Подключаемся к {len(self.symbols)} потокам...")

                self.ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open
                )

                self.ws.run_forever(ping_interval=30, ping_timeout=10)

            except Exception as e:
                logger.error(f"[WS] Ошибка в цикле: {e}")

            if self.running:
                self.reconnect_count += 1
                if self.reconnect_count > self.max_reconnects:
                    logger.warning(f"[WS] Лимит переподключений ({self.max_reconnects}), пауза 5 мин...")
                    self.reconnect_count = 0
                    time.sleep(300)
                    continue

                delay = min(self.reconnect_delay * self.reconnect_count, 60)
                logger.info(f"[WS] Переподключение через {delay} сек (попытка {self.reconnect_count})")
                time.sleep(delay)

    def _on_open(self, ws):
        self.connected = True
        self.reconnect_count = 0
        self.stats['connects'] += 1

        # OKX: отправляем подписку
        sub_msg = self._build_subscribe_msg()
        if sub_msg:
            ws.send(sub_msg)
            logger.info(f"[WS] [OKX] Подписка отправлена на {len(self.symbols)} инструментов")

        logger.info(f"[WS] Подключено! Отслеживаем {len(self.symbols)} символов")

        if self.connection_callback:
            try:
                self.connection_callback(True)
            except Exception as e:
                logger.error(f"[WS] Connection callback error: {e}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        self.stats['disconnects'] += 1
        logger.warning(f"[WS] Отключено: {close_status_code} - {close_msg}")

        if self.connection_callback:
            try:
                self.connection_callback(False)
            except Exception as e:
                logger.error(f"[WS] Connection callback error: {e}")

    def _on_error(self, ws, error):
        logger.error(f"[WS] Ошибка: {error}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            self.stats['messages_received'] += 1
            self.stats['last_message_time'] = datetime.now()

            if self.exchange_id == 'okx':
                self._parse_okx(data)
            else:
                self._parse_binance(data)

        except json.JSONDecodeError as e:
            logger.error(f"[WS] JSON decode error: {e}")
        except Exception as e:
            logger.error(f"[WS] Message processing error: {e}")

    def _parse_binance(self, data: dict):
        """Парсинг Binance miniTicker"""
        if 'data' in data:
            ticker_data = data['data']
        else:
            ticker_data = data

        symbol_raw = ticker_data.get('s', '')
        price_str = ticker_data.get('c', '0')

        if not symbol_raw:
            return

        symbol = symbol_raw.replace('USDT', '')
        price = float(price_str)

        with self.lock:
            self.prices[symbol] = price

        if self.price_callback:
            try:
                self.price_callback(symbol, price, ticker_data)
            except Exception as e:
                logger.error(f"[WS] Price callback error for {symbol}: {e}")

    def _parse_okx(self, data: dict):
        """Парсинг OKX tickers channel"""
        # OKX отправляет событие подписки — пропускаем
        if 'event' in data:
            return

        items = data.get('data', [])
        for item in items:
            # instId = "BTC-USDT-SWAP"
            inst_id = item.get('instId', '')
            last_str = item.get('last', '0')

            if not inst_id:
                continue

            # BTC-USDT-SWAP → BTC
            symbol = inst_id.split('-')[0]
            price = float(last_str)

            with self.lock:
                self.prices[symbol] = price

            if self.price_callback:
                try:
                    self.price_callback(symbol, price, item)
                except Exception as e:
                    logger.error(f"[WS] Price callback error for {symbol}: {e}")

    def get_price(self, symbol: str) -> float:
        clean = self._normalize_symbol(symbol)
        with self.lock:
            return self.prices.get(clean, 0)

    def get_all_prices(self) -> Dict[str, float]:
        with self.lock:
            return self.prices.copy()

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            'exchange': self.exchange_id,
            'symbols_count': len(self.symbols),
            'symbols': list(self.symbols),
            'connected': self.connected,
            'running': self.running,
            'prices_cached': len(self.prices)
        }

    def is_connected(self) -> bool:
        return self.connected and self.running


# Обратная совместимость
BinanceWebSocketManager = ExchangeWebSocketManager

ws_manager: Optional[ExchangeWebSocketManager] = None


def get_ws_manager(exchange_id: str = None) -> ExchangeWebSocketManager:
    global ws_manager
    if ws_manager is None:
        if exchange_id is None:
            # Читаем из конфига
            try:
                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
                with open(config_path, 'r') as f:
                    cfg = json.load(f)
                exchange_id = cfg.get('exchange', 'binance')
            except Exception:
                exchange_id = 'binance'
        ws_manager = ExchangeWebSocketManager(exchange_id)
    return ws_manager
