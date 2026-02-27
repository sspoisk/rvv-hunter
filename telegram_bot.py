# -*- coding: utf-8 -*-
"""
RVV Hunter v6.0 - Telegram Bot Module
Умные уведомления о сделках, трейлинге и ошибках
"""

import logging
import threading
import queue
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Пробуем импортировать requests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("[TELEGRAM] requests library not installed")


class TelegramNotifier:
    """Модуль уведомлений в Telegram"""
    
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = False
        self.message_queue = queue.Queue()
        self.worker_thread = None
        self.running = False
        
        # Настройки уведомлений
        self.notify_open = True
        self.notify_close = True
        self.notify_trailing = True
        self.notify_errors = True
        self.notify_daily_summary = True
        
        # Статистика
        self.stats = {
            'messages_sent': 0,
            'messages_failed': 0,
            'last_message_time': None
        }
        
        if bot_token and chat_id:
            self.configure(bot_token, chat_id)
    
    def configure(self, bot_token: str, chat_id: str) -> bool:
        """Настройка бота"""
        if not REQUESTS_AVAILABLE:
            logger.error("[TELEGRAM] requests library required")
            return False
        
        self.bot_token = bot_token
        self.chat_id = chat_id
        
        # Проверяем подключение
        if self.test_connection():
            self.enabled = True
            self._start_worker()
            logger.info("[TELEGRAM] Bot configured successfully")
            return True
        else:
            self.enabled = False
            return False
    
    def test_connection(self) -> bool:
        """Тестирование подключения к Telegram API"""
        if not self.bot_token or not self.chat_id:
            return False
        
        if not REQUESTS_AVAILABLE:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    bot_name = data.get('result', {}).get('username', 'Unknown')
                    logger.info(f"[TELEGRAM] Connected to bot: @{bot_name}")
                    return True
            
            logger.error(f"[TELEGRAM] Connection test failed: {response.status_code}")
            return False
            
        except Exception as e:
            logger.error(f"[TELEGRAM] Connection test error: {e}")
            return False
    
    def _start_worker(self):
        """Запуск фонового потока для отправки сообщений"""
        if self.worker_thread and self.worker_thread.is_alive():
            return
        
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info("[TELEGRAM] Worker thread started")
    
    def _worker_loop(self):
        """Цикл обработки очереди сообщений"""
        while self.running:
            try:
                # Получаем сообщение из очереди с таймаутом
                try:
                    message = self.message_queue.get(timeout=1)
                except queue.Empty:
                    continue
                
                # Отправляем
                self._send_message_sync(message)
                self.message_queue.task_done()
                
            except Exception as e:
                logger.error(f"[TELEGRAM] Worker error: {e}")
    
    def _send_message_sync(self, text: str) -> bool:
        """Синхронная отправка сообщения"""
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            payload = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                self.stats['messages_sent'] += 1
                self.stats['last_message_time'] = datetime.utcnow().isoformat()
                return True
            else:
                self.stats['messages_failed'] += 1
                logger.error(f"[TELEGRAM] Send failed: {response.status_code}")
                return False
                
        except Exception as e:
            self.stats['messages_failed'] += 1
            logger.error(f"[TELEGRAM] Send error: {e}")
            return False
    
    def send(self, text: str, immediate: bool = False):
        """Отправка сообщения (асинхронно через очередь или сразу)"""
        if not self.enabled:
            return
        
        if immediate:
            self._send_message_sync(text)
        else:
            self.message_queue.put(text)
    
    # =========================================================================
    # УМНЫЕ УВЕДОМЛЕНИЯ
    # =========================================================================
    
    def notify_position_opened(self, position: Dict):
        """Уведомление об открытии позиции"""
        if not self.enabled or not self.notify_open:
            return
        
        symbol = position.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
        entry = position.get('entry_price', 0)
        sl = position.get('stop_loss', 0)
        tp1 = position.get('take_profit_1', 0)
        tp2 = position.get('take_profit_2', 0)
        confidence = position.get('ai_confidence', 0)
        change_24h = position.get('change_24h', 0)
        provider = position.get('ai_provider', 'AI')
        trade_mode = position.get('trade_mode', 'PAPER')
        
        # Расчёт процентов
        sl_pct = ((sl / entry) - 1) * 100 if entry > 0 else 0
        tp1_pct = ((tp1 / entry) - 1) * 100 if entry > 0 else 0
        tp2_pct = ((tp2 / entry) - 1) * 100 if entry > 0 else 0
        
        # Эмодзи режима
        mode_emoji = "💰" if trade_mode == "LIVE" else "📝"
        
        text = f"""🔴 <b>SHORT {symbol}</b> @ ${entry:.6f}

📊 <b>Параметры:</b>
├ Confidence: {confidence}%
├ Pump 24h: +{change_24h:.1f}%
├ AI: {provider.upper()}
└ Режим: {trade_mode} {mode_emoji}

🎯 <b>Уровни:</b>
├ SL: ${sl:.6f} ({sl_pct:+.2f}%)
├ TP1: ${tp1:.6f} ({tp1_pct:+.2f}%)
└ TP2: ${tp2:.6f} ({tp2_pct:+.2f}%)

#SHORT #{symbol} #{trade_mode}"""

        self.send(text)
    
    def notify_position_closed(self, position: Dict, result: Dict):
        """Уведомление о закрытии позиции"""
        if not self.enabled or not self.notify_close:
            return
        
        symbol = position.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
        pnl = result.get('pnl', 0)
        pnl_pct = position.get('pnl_percent', 0)
        reason = result.get('reason', 'UNKNOWN')
        trail_activated = result.get('trail_activated', False)
        duration = position.get('duration_minutes', 0)
        trade_mode = position.get('trade_mode', 'PAPER')
        
        # Эмодзи результата
        if pnl >= 0:
            result_emoji = "✅"
            result_text = "PROFIT"
        else:
            result_emoji = "❌"
            result_text = "LOSS"
        
        # Причина закрытия
        reason_emoji = "🚀" if reason == "TRAILING_STOP" else "⚡" if reason == "TAKE_PROFIT" else "🛑" if reason == "STOP_LOSS" else "👆"
        
        # Режим
        mode_emoji = "💰" if trade_mode == "LIVE" else "📝"
        
        # Форматирование времени
        if duration >= 60:
            time_str = f"{duration // 60}ч {duration % 60}м"
        else:
            time_str = f"{duration}м"
        
        text = f"""{result_emoji} <b>ЗАКРЫТО: {symbol}</b>

💵 <b>Результат:</b> {result_text}
├ PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)
├ Причина: {reason} {reason_emoji}
├ Время: {time_str}
└ Режим: {trade_mode} {mode_emoji}

{"🚀 Трейлинг-стоп сработал!" if trail_activated else ""}

#CLOSED #{symbol} #{result_text}"""

        self.send(text)
    
    def notify_trailing_activated(self, symbol: str, profit_pct: float, new_sl: float):
        """Уведомление об активации трейлинг-стопа"""
        if not self.enabled or not self.notify_trailing:
            return
        
        symbol_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
        
        text = f"""🚀 <b>ТРЕЙЛИНГ АКТИВИРОВАН</b>

📍 {symbol_clean}
├ Прибыль: +{profit_pct:.2f}%
└ Новый SL: ${new_sl:.6f}

#TRAILING #{symbol_clean}"""

        self.send(text)
    
    def notify_trailing_moved(self, symbol: str, old_sl: float, new_sl: float, profit_pct: float):
        """Уведомление о перемещении трейлинг-стопа"""
        if not self.enabled or not self.notify_trailing:
            return
        
        symbol_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
        
        text = f"""📈 <b>ТРЕЙЛИНГ ОБНОВЛЁН</b>

📍 {symbol_clean}
├ SL: ${old_sl:.6f} → ${new_sl:.6f}
└ Прибыль: +{profit_pct:.2f}%

#TRAILING #{symbol_clean}"""

        self.send(text)
    
    def notify_error(self, error_type: str, message: str):
        """Уведомление об ошибке"""
        if not self.enabled or not self.notify_errors:
            return
        
        text = f"""⚠️ <b>ОШИБКА: {error_type}</b>

{message}

#ERROR"""

        self.send(text, immediate=True)
    
    def notify_daily_summary(self, stats: Dict):
        """Ежедневная сводка"""
        if not self.enabled or not self.notify_daily_summary:
            return
        
        trades = stats.get('trades_today', 0)
        wins = stats.get('wins_today', 0)
        pnl = stats.get('pnl_today', 0)
        win_rate = stats.get('win_rate_today', 0)
        
        if trades == 0:
            return
        
        result_emoji = "🟢" if pnl >= 0 else "🔴"
        
        text = f"""📊 <b>ДНЕВНАЯ СВОДКА</b>

{result_emoji} PnL: ${pnl:+.2f}
├ Сделок: {trades}
├ Побед: {wins}
└ Win Rate: {win_rate:.0f}%

#DAILY #SUMMARY"""

        self.send(text)
    
    def notify_limit_reached(self, limit_type: str, current_value: float, limit_value: float):
        """Уведомление о достижении лимита"""
        if not self.enabled:
            return
        
        text = f"""🛑 <b>ЛИМИТ ДОСТИГНУТ</b>

⚠️ {limit_type}
├ Текущее: ${current_value:.2f}
└ Лимит: ${limit_value:.2f}

Торговля приостановлена!

#LIMIT #WARNING"""

        self.send(text, immediate=True)
    
    def notify_post_mortem(self, post_mortem: Dict):
        """Уведомление о пост-мортем анализе"""
        if not self.enabled or not self.notify_errors:
            return
        
        symbol = post_mortem.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', '')
        loss = post_mortem.get('loss_amount', 0)
        analysis = post_mortem.get('analysis', '')
        recommendations = post_mortem.get('recommendations', [])
        
        rec_text = ""
        if recommendations:
            rec_items = [f"• {r.get('description', '')}" for r in recommendations[:3]]
            rec_text = "\n".join(rec_items)
        
        text = f"""📋 <b>ПОСТ-МОРТЕМ: {symbol}</b>

💸 Убыток: -${loss:.2f}

📊 <b>Анализ:</b>
{analysis[:200]}

💡 <b>Рекомендации:</b>
{rec_text if rec_text else "Нет конкретных рекомендаций"}

#POSTMORTEM #{symbol}"""

        self.send(text)
    
    # =========================================================================
    # УПРАВЛЕНИЕ
    # =========================================================================
    
    def update_settings(self, settings: Dict):
        """Обновление настроек уведомлений"""
        self.notify_open = settings.get('telegram_notify_open', True)
        self.notify_close = settings.get('telegram_notify_close', True)
        self.notify_trailing = settings.get('telegram_notify_trailing', True)
        self.notify_errors = settings.get('telegram_notify_errors', True)
    
    def get_stats(self) -> Dict:
        """Получение статистики"""
        return {
            'enabled': self.enabled,
            'connected': self.test_connection() if self.enabled else False,
            **self.stats
        }
    
    def disable(self):
        """Отключение бота"""
        self.enabled = False
        self.running = False
        logger.info("[TELEGRAM] Bot disabled")
    
    def send_test_message(self) -> bool:
        """Отправка тестового сообщения"""
        if not self.enabled:
            return False
        
        text = """🤖 <b>RVV Hunter v6.0</b>

✅ Telegram уведомления работают!

Вы будете получать:
• 🔴 Открытие позиций
• ✅/❌ Закрытие позиций
• 🚀 Активация трейлинга
• ⚠️ Ошибки и предупреждения

#TEST"""

        return self._send_message_sync(text)


# Глобальный экземпляр
telegram_bot = TelegramNotifier()
