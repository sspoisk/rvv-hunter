"""
RVV Hunter v5.1 - ЕДИНЫЙ УМНЫЙ АГЕНТ
=====================================
Один агент который ДУМАЕТ, ПОМНИТ, ДЕЙСТВУЕТ и УЧИТСЯ.

Провайдеры:
- DeepSeek chat     → команды, диалог (основной)
- DeepSeek reasoner → глубокий анализ, стратегии
- GROQ instant      → fallback (резервный)

Автор: RVV Hunter Team
"""

import json
import re
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import requests

from agent_brain import AgentBrain, brain as default_brain
from agent_tools import AgentTools

logger = logging.getLogger(__name__)


# =============================================================================
# AI ПРОВАЙДЕР - 3 модели
# =============================================================================

class AIProvider:
    """
    Провайдер AI с 3 моделями:
    - DeepSeek chat (быстрый, для команд)
    - DeepSeek reasoner (умный, для анализа)
    - GROQ instant (резервный, fallback)
    """
    
    def __init__(self, deepseek_key: str = None, groq_key: str = None):
        self.deepseek_key = deepseek_key
        self.groq_key = groq_key
        
        # Определяем доступность провайдеров
        self.deepseek_available = bool(deepseek_key)
        self.groq_available = bool(groq_key)
        
        # Статистика
        self.stats = {
            'deepseek-chat': {'requests': 0, 'errors': 0, 'tokens': 0},
            'deepseek-reasoner': {'requests': 0, 'errors': 0, 'tokens': 0},
            'groq-instant': {'requests': 0, 'errors': 0, 'tokens': 0}
        }
        
        logger.info(f"[AI] DeepSeek: {'✅' if self.deepseek_available else 'âŒ'}, GROQ: {'✅' if self.groq_available else 'âŒ'}")
    
    def chat(self, messages: List[Dict], mode: str = 'chat',
             max_tokens: int = 2000, temperature: float = 0.7) -> Tuple[str, str]:
        """
        Отправить сообщение в AI
        
        Args:
            messages: История сообщений
            mode: 'chat' (быстро) или 'reason' (глубоко)
            
        Returns:
            (response_text, used_model)
        """
        # Выбираем модель по режиму
        if mode == 'reason' and self.deepseek_available:
            # Сначала пробуем reasoner
            response, success = self._call_deepseek(messages, 'deepseek-reasoner', max_tokens, temperature)
            if success:
                return response, 'deepseek-reasoner'
            # Fallback на chat
            response, success = self._call_deepseek(messages, 'deepseek-chat', max_tokens, temperature)
            if success:
                return response, 'deepseek-chat'
        elif self.deepseek_available:
            # Обычный режим - chat
            response, success = self._call_deepseek(messages, 'deepseek-chat', max_tokens, temperature)
            if success:
                return response, 'deepseek-chat'
        
        # Fallback на GROQ
        if self.groq_available:
            logger.info("[AI] Fallback to GROQ instant")
            response, success = self._call_groq(messages, max_tokens, temperature)
            if success:
                return response, 'groq-instant'
        
        return "❌ AI не доступен. Проверьте API ключи.", 'none'
    
    def _call_deepseek(self, messages: List[Dict], model: str,
                       max_tokens: int, temperature: float) -> Tuple[str, bool]:
        """Вызов DeepSeek API"""
        try:
            self.stats[model]['requests'] += 1
            
            headers = {
                'Authorization': f'Bearer {self.deepseek_key}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'model': model,
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': temperature
            }
            
            response = requests.post(
                'https://api.deepseek.com/v1/chat/completions',
                headers=headers,
                json=payload,
                timeout=90 if model == 'deepseek-reasoner' else 60
            )
            
            if response.status_code == 200:
                data = response.json()
                text = data['choices'][0]['message']['content']
                tokens = data.get('usage', {}).get('total_tokens', 0)
                self.stats[model]['tokens'] += tokens
                return text, True
            else:
                self.stats[model]['errors'] += 1
                logger.error(f"[AI] DeepSeek {model} error: {response.status_code}")
                return f"Error {response.status_code}", False
                
        except Exception as e:
            self.stats[model]['errors'] += 1
            logger.error(f"[AI] DeepSeek {model} exception: {e}")
            return str(e), False
    
    def _call_groq(self, messages: List[Dict], max_tokens: int,
                   temperature: float) -> Tuple[str, bool]:
        """Вызов GROQ API (llama-3.1-8b-instant)"""
        try:
            self.stats['groq-instant']['requests'] += 1
            
            headers = {
                'Authorization': f'Bearer {self.groq_key}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'model': 'llama-3.1-8b-instant',
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': temperature
            }
            
            response = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                text = data['choices'][0]['message']['content']
                tokens = data.get('usage', {}).get('total_tokens', 0)
                self.stats['groq-instant']['tokens'] += tokens
                return text, True
            else:
                self.stats['groq-instant']['errors'] += 1
                logger.error(f"[AI] GROQ error: {response.status_code}")
                return f"Error {response.status_code}", False
                
        except Exception as e:
            self.stats['groq-instant']['errors'] += 1
            logger.error(f"[AI] GROQ exception: {e}")
            return str(e), False
    
    def get_stats(self) -> Dict:
        """Статистика провайдеров"""
        return {
            'deepseek_available': self.deepseek_available,
            'groq_available': self.groq_available,
            'models': self.stats
        }


# =============================================================================
# ЕДИНЫЙ УМНЫЙ АГЕНТ
# =============================================================================

class SmartAgent:
    """
    ЕДИНЫЙ умный агент RVV Hunter v5.1
    
    Возможности:
    🧠 Думает - анализирует, рассуждает, принимает решения
    💾 Помнит - команды, уроки, стратегии, разговоры
    🔧 Действует - закрывает, меняет SL/TP, управляет сканером
    📊 Исследует - загружает историю, ищет паттерны, бэктест
    🎯 Строит стратегии - создаёт, тестирует, адаптирует
    """
    
    VERSION = "5.2"
    
    def __init__(self, deepseek_key: str = None, groq_key: str = None,
                 brain: AgentBrain = None, tools: AgentTools = None):
        
        self.ai = AIProvider(deepseek_key, groq_key)
        self.brain = brain or default_brain
        self.tools = tools or AgentTools()
        
        self.lock = threading.Lock()
        self.running = False
        self.autonomous_mode = False
        
        # Системный промпт
        self.system_prompt = self._build_system_prompt()
        
        logger.info(f"[AGENT] Smart Agent v{self.VERSION} initialized")
    
    def _build_system_prompt(self) -> str:
        """Системный промпт - личность агента v5.7.2"""
        return """Ты - торговый агент RVV Hunter v5.7.2. Говори кратко и по делу.

⚠️ КРИТИЧЕСКИ ВАЖНО - НЕ ВЫДУМЫВАЙ:
В системе есть ТОЛЬКО эти параметры для входа:
- RSI: ≥70 для SHORT, ≤30 для LONG
- Stop Loss % (sl)
- Take Profit % (tp)  
- Trailing Activation % (trailing_activation)
- Trailing Distance % (trailing_distance)

❌ НЕ СУЩЕСТВУЕТ в системе (НЕ УПОМИНАЙ):
- MACD - НЕТ!
- EMA, SMA - НЕТ!
- Пробой уровней - НЕТ!
- Объём для входа - НЕТ!
- Свечные паттерны - НЕТ!

Когда спрашивают "какая стратегия" - ТОЛЬКО [TOOL:STRATEGY], без выдумок.

🔧 ИНСТРУМЕНТЫ:

**ПОЗИЦИИ:**
[TOOL:POSITIONS] - открытые позиции
[TOOL:CLOSE:RVV-0012] - закрыть позицию

**СТРАТЕГИЯ:**
[TOOL:STRATEGY] - показать текущую (ТОЛЬКО реальные параметры!)
[TOOL:STRATEGY:SET:sl:4.5] - изменить SL
[TOOL:STRATEGY:SET:tp:6.0] - изменить TP
[TOOL:STRATEGY:SET:trailing_activation:3.0] - активация trailing
[TOOL:STRATEGY:SET:trailing_distance:1.5] - дистанция trailing
[TOOL:STRATEGY:SAVE:MyStrategy] - сохранить
[TOOL:STRATEGY:LIST] - список стратегий

**ФИЛЬТРЫ:**
[TOOL:SET_PARAM:btc_filter:false] - ВЫКЛЮЧИТЬ фильтр BTC
[TOOL:SET_PARAM:btc_filter:true] - включить фильтр BTC
[TOOL:SET_PARAM:volume_filter:false] - выключить фильтр объёма
[TOOL:SET_PARAM:volume_filter:true] - включить фильтр объёма

**ОПТИМИЗАЦИЯ (Grid Search):**
[TOOL:GRID_OPTIMIZE:30:100] - перебор за 30 дней на 100 монетах
[TOOL:GRID_OPTIMIZE:30:500] - перебор за 30 дней на 500 монетах
[TOOL:APPLY_BEST] - применить лучшие параметры
Формат: [TOOL:GRID_OPTIMIZE:дни:монеты]

🎯 **ОПТИМИЗАЦИЯ BTC УРОВНЕЙ:**
[TOOL:OPTIMIZE_BTC:30:50] - оптимизация BTC уровней (30 дней, 50 монет)
[TOOL:OPTIMIZE_BTC:14:100:full] - полная оптимизация (больше комбинаций)
[TOOL:APPLY_BTC] - применить найденные BTC уровни
Оптимизирует: порог открытия (бык/медв) + порог автозакрытия (LONG/SHORT)

**БЭКТЕСТ:**
[TOOL:BACKTEST:current:30:500] - бэктест (стратегия:дни:монеты)
[TOOL:BACKTEST_PATTERNS:60:200] - бэктест паттернов (симуляция)
[TOOL:ANALYZE_PATTERNS:30] - анализ РЕАЛЬНЫХ сделок за N дней

**АНАЛИЗ:**
[TOOL:MARKET] - обзор рынка
[TOOL:STATS:30] - статистика за N дней
[TOOL:TOP:50] - топ 50 монет по объёму
[TOOL:ANALYZE_LATE] - анализ опоздавших входов (порог из min_change_filter)
[TOOL:ANALYZE_LATE:all] - анализ опоздавших (все сделки)

**BLACKLIST:**
[TOOL:BLACKLIST_WORST] - добавить худшие из бэктеста

📋 ПРАВИЛА:
1. Отвечай на русском
2. ТОЛЬКО реальные параметры - НЕ ВЫДУМЫВАЙ!
3. Когда просят оптимизировать на N монетах - [TOOL:GRID_OPTIMIZE:30:N]
4. Сначала [TOOL:...], потом пояснение

💡 ПРИМЕРЫ:

Пользователь: "выключи фильтр BTC"
Ты: [TOOL:SET_PARAM:btc_filter:false]
Готово!

Пользователь: "оптимизируй на 500 монетах"
Ты: [TOOL:GRID_OPTIMIZE:30:500]
Запускаю Grid Search...

Пользователь: "какая стратегия?"
Ты: [TOOL:STRATEGY]
"""

    def set_components(self, trader=None, exchange=None, db=None):
        """Подключить компоненты бота"""
        logger.info(f"[AGENT] set_components called: trader={trader}, exchange={exchange is not None}")
        self.tools.set_components(trader=trader, exchange=exchange, db=db, brain=self.brain)
        logger.info(f"[AGENT] After set_components: self.tools.trader={self.tools.trader}")
        logger.info(f"[AGENT] Components: trader={'✅' if self.tools.trader else 'âŒ'}, exchange={'✅' if self.tools.exchange else 'âŒ'}")
    
    # =========================================================================
    # ОСНОВНОЙ МЕТОД - ОБРАБОТКА СООБЩЕНИЯ
    # =========================================================================
    
    def process_message(self, user_message: str, deep_analysis: bool = False) -> str:
        """
        Обработать сообщение пользователя
        
        Args:
            user_message: Сообщение от пользователя
            deep_analysis: Использовать DeepSeek Reasoner для глубокого анализа
            
        Returns:
            Ответ агента
        """
        try:
            # Определяем режим AI
            mode = 'reason' if deep_analysis or self._needs_deep_analysis(user_message) else 'chat'
            
            # Собираем контекст
            context = self._build_context()
            
            # Собираем сообщения для AI
            messages = [
                {'role': 'system', 'content': self.system_prompt},
                {'role': 'system', 'content': context},
                {'role': 'user', 'content': user_message}
            ]
            
            # Вызываем AI
            response, model_used = self.ai.chat(messages, mode=mode, max_tokens=2000)
            
            # Если AI недоступен - используем тупой режим
            if model_used == 'none':
                logger.warning("[AGENT] AI unavailable, using dumb mode")
                return self._dumb_mode_response(user_message)
            
            # Выполняем инструменты
            final_response, tools_used = self._execute_tools(response)
            
            # Сохраняем в память
            self.brain.save_conversation(
                user_message, 
                final_response, 
                tools_used,
                provider=model_used
            )
            
            return final_response
            
        except Exception as e:
            logger.error(f"[AGENT] process_message error: {e}")
            return f"❌ Ошибка: {str(e)}"
    
    def _needs_deep_analysis(self, message: str) -> bool:
        """Определить нужен ли глубокий анализ"""
        deep_keywords = [
            'почему', 'проанализируй', 'объясни', 'стратегия', 'паттерн',
            'причина', 'детально', 'подробно', 'построй', 'разработай',
            'оптимизируй', 'улучши', 'исследуй'
        ]
        message_lower = message.lower()
        return any(kw in message_lower for kw in deep_keywords)
    
    
    def _dumb_mode_response(self, user_message: str) -> str:
        """
        'Тупой' режим когда AI провайдеры недоступны.
        Показывает только статистику из БД без анализа.
        """
        msg_lower = user_message.lower()
        
        # Простые команды которые можно выполнить без AI
        if 'позиц' in msg_lower or 'position' in msg_lower:
            positions = self.tools.get_open_positions()
            if positions:
                lines = ["📊 ОТКРЫТЫЕ ПОЗИЦИИ:"]
                for p in positions:
                    sym = p.get('symbol', '?').replace('/USDT:USDT', '').replace('/USDT', '')
                    side = p.get('side', '?')
                    pnl = p.get('pnl_percent', 0)
                    lines.append(f"  • {sym} {side}: {pnl:+.2f}%")
                return '\n'.join(lines) + "\n\n⚠️ AI провайдеры недоступны - только базовая статистика"
            else:
                return "📊 Нет открытых позиций\n\n⚠️ AI провайдеры недоступны"
        
        elif 'стат' in msg_lower or 'stat' in msg_lower:
            try:
                from database import db
                stats = db.get_trade_statistics_for_agent(days=30)
                return f"""📈 СТАТИСТИКА (30 дней):
• Всего сделок: {stats['total_trades']}
• Побед: {stats['wins']} | Поражений: {stats['losses']}
• Win Rate: {stats['win_rate']:.1f}%
• Общий PnL: ${stats['total_pnl']:.2f}
• Средняя победа: ${stats['avg_win']:.2f}
• Средний убыток: ${stats['avg_loss']:.2f}

⚠️ AI провайдеры недоступны - только базовая статистика"""
            except Exception as e:
                return f"❌ Ошибка получения статистики: {e}\n\n⚠️ AI провайдеры недоступны"
        
        elif 'стратег' in msg_lower or 'strateg' in msg_lower:
            try:
                from database import db
                strategies = db.get_all_strategies()
                if strategies:
                    lines = ["📋 СОХРАНЁННЫЕ СТРАТЕГИИ:"]
                    for s in strategies:
                        active = "✅" if s.get('is_active') else ""
                        wr = s.get('backtest_win_rate', 0) or 0
                        lines.append(f"  {active} {s['name']}: SL={s['stop_loss_pct']}% TP={s['take_profit_pct']}% WR={wr:.0f}%")
                    return '\n'.join(lines) + "\n\n⚠️ AI провайдеры недоступны"
                else:
                    return "📋 Нет сохранённых стратегий\n\n⚠️ AI провайдеры недоступны"
            except Exception as e:
                return f"❌ Ошибка: {e}\n\n⚠️ AI провайдеры недоступны"
        
        elif 'помощь' in msg_lower or 'help' in msg_lower:
            return """🤖 ТУПОЙ РЕЖИМ (AI недоступен)

Доступные команды:
• "позиции" - показать открытые позиции
• "статистика" - статистика за 30 дней
• "стратегии" - список сохранённых стратегий

⚠️ Для полного функционала нужен API ключ DeepSeek или Groq"""
        
        else:
            return """⚠️ AI провайдеры недоступны (DeepSeek и Groq).

В тупом режиме доступны только базовые команды:
• "позиции" - показать открытые
• "статистика" - статистика сделок  
• "стратегии" - список стратегий
• "помощь" - справка

Для полного функционала добавьте API ключи в настройках."""

    def _build_context(self) -> str:
        """Построить контекст для AI"""
        parts = []
        
        # Проверяем подключение trader
        if not self.tools.trader:
            logger.warning("[AGENT] _build_context: trader NOT connected!")
            parts.append("⚠️ ВНИМАНИЕ: trader не подключен, данные недоступны")
        
        # Позиции
        positions = self.tools.get_open_positions()
        logger.info(f"[AGENT] _build_context: got {len(positions)} positions")
        if positions:
            pos_lines = ["📊 ОТКРЫТЫЕ ПОЗИЦИИ:"]
            for p in positions:
                tid = p.get('id', '?')
                sym = p.get('symbol', '?').replace('/USDT:USDT', '').replace('/USDT', '')
                side = p.get('side', '?')
                pnl = p.get('pnl_percent', 0)
                partial = p.get('partial_tp_pnl', 0)
                partial_mark = ' 💰' if partial > 0 else ''
                pos_lines.append(f"  {tid}: {sym} {side} PnL:{pnl:+.2f}%{partial_mark}")
            parts.append('\n'.join(pos_lines))
        else:
            parts.append("📊 Нет открытых позиций")
        
        # Память
        memory_context = self.brain.get_full_context_for_ai()
        if memory_context:
            parts.append(f"\n💾 ПАМЯТЬ:\n{memory_context}")
        
        # История разговоров
        history = self.brain.get_conversation_context(3)
        if history:
            parts.append(f"\n💬 НЕДАВНИЕ РАЗГОВОРЫ:\n{history}")
        
        return '\n'.join(parts)
    
    # =========================================================================
    # ВЫПОЛНЕНИЕ ИНСТРУМЕНТОВ
    # =========================================================================
    
    def _execute_tools(self, response: str) -> Tuple[str, List[str]]:
        """
        Найти и выполнить все инструменты в ответе
        
        Returns:
            (final_response, list_of_tools_used)
        """
        tools_used = []
        results = []
        
        # Ищем [TOOL:...] команды (новый формат)
        tool_pattern = r'\[TOOL:([A-Z_]+)(?::([^\]]*))?\]'
        matches = list(re.findall(tool_pattern, response))
        
        # ВСЕГДА также проверяем старый формат ACTION: (добавляем к matches)
        legacy_matches = self._parse_legacy_actions(response)
        if legacy_matches:
            matches.extend(legacy_matches)
            logger.info(f"[AGENT] Found legacy actions: {legacy_matches}")
        
        if matches:
            logger.info(f"[AGENT] Total tools to execute: {matches}")
        
        for tool_name, params in matches:
            result = self._run_tool(tool_name, params)
            if result:
                results.append(result)
                tools_used.append(tool_name)
                logger.info(f"[AGENT] Tool {tool_name}: {result[:80]}...")
        
        # Убираем [TOOL:...] из ответа
        clean_response = re.sub(tool_pattern, '', response).strip()
        
        # Убираем старый формат ACTION:
        clean_response = re.sub(r'ACTION:\s*[A-Z_]+\([^)]*\)', '', clean_response, flags=re.IGNORECASE)
        clean_response = re.sub(r'ACTION:\s*[A-Z_]+', '', clean_response, flags=re.IGNORECASE)
        
        # Собираем финальный ответ
        if results:
            final = clean_response + '\n\n' + '\n\n'.join(results)
        else:
            final = clean_response
        
        return final.strip(), tools_used
    
    def _parse_legacy_actions(self, response: str) -> List[Tuple[str, str]]:
        """Парсинг старого формата ACTION: CLOSE(RVV-0001)"""
        matches = []
        
        # ACTION: CLOSE(RVV-0001)
        for m in re.finditer(r'ACTION:\s*CLOSE\s*\(([^)]+)\)', response, re.IGNORECASE):
            matches.append(('CLOSE', m.group(1)))
        
        # ACTION: HOLD
        if re.search(r'ACTION:\s*HOLD', response, re.IGNORECASE):
            matches.append(('HOLD', ''))
        
        return matches
    
    def _run_tool(self, tool_name: str, params: str = '') -> Optional[str]:
        """Выполнить один инструмент"""
        try:
            params = params.strip() if params else ''
            
            # =============== ПОЗИЦИИ ===============
            if tool_name == 'POSITIONS':
                positions = self.tools.get_open_positions()
                if not positions:
                    return "📭 Нет открытых позиций"
                lines = [f"📊 Открытые позиции ({len(positions)}):"]
                for p in positions:
                    tid = p.get('id', '?')
                    sym = p.get('symbol', '?').replace('/USDT:USDT', '').replace('/USDT', '')
                    side = p.get('side', '?')
                    pnl = p.get('pnl_percent', 0)
                    pnl_usd = p.get('pnl_usdt', 0)
                    partial = p.get('partial_tp_pnl', 0)
                    emoji = '🟢' if pnl >= 0 else '🔴'
                    partial_mark = ' 💰' if partial > 0 else ''
                    lines.append(f"{emoji} {tid}: {sym} {side} | {pnl:+.2f}% (${pnl_usd:+.2f}){partial_mark}")
                return '\n'.join(lines)
            
            # =============== ЗАКРЫТИЕ ===============
            elif tool_name == 'CLOSE':
                if not params:
                    return "❌ Укажи trade_id. Используй [TOOL:POSITIONS] чтобы увидеть ID."
                parts = params.split(':')
                trade_id = parts[0].strip()
                reason = parts[1].strip() if len(parts) > 1 else 'AGENT'
                
                logger.info(f"[AGENT] Executing CLOSE: trade_id={trade_id}, reason={reason}")
                logger.info(f"[AGENT] tools.trader = {self.tools.trader}")
                
                result = self.tools.close_position(trade_id, reason)
                logger.info(f"[AGENT] CLOSE result: {result}")
                
                if result.get('success'):
                    pnl = result.get('pnl', 0)
                    emoji = '✅' if pnl >= 0 else 'âš ï¸'
                    return f"{emoji} Позиция {trade_id} ЗАКРЫТА! PnL: ${pnl:+.2f}"
                else:
                    return f"❌ Не удалось закрыть {trade_id}: {result.get('error', 'Unknown')}"
            
            elif tool_name == 'CLOSE_ALL':
                parts = params.split(':') if params else ['all']
                filter_type = parts[0] if parts else 'all'
                filter_value = float(parts[1]) if len(parts) > 1 else None
                result = self.tools.close_all_positions(filter_type, filter_value)
                return result.get('summary', str(result))
            
            elif tool_name == 'HOLD':
                return "⏸️ Позиции оставлены без изменений"
            
            # =============== АНАЛИЗ ===============
            elif tool_name == 'ANALYZE':
                symbol = params or 'BTC'
                result = self.tools.analyze_symbol(symbol)
                return result.get('summary', str(result))
            
            elif tool_name == 'BTC':
                result = self.tools.get_btc_trend()
                return result.get('summary', str(result))
            
            elif tool_name == 'MARKET':
                result = self.tools.get_market_overview()
                return result.get('summary', str(result))
            
            elif tool_name == 'PATTERNS':
                ptype = params or 'losing'
                result = self.tools.find_patterns(ptype)
                return result.get('summary', str(result))
            
            elif tool_name == 'STATS':
                days = int(params) if params and params.isdigit() else 30
                result = self.tools.get_trading_statistics(days)
                return result.get('summary', str(result))
            
            # =============== СТРАТЕГИИ ===============
            elif tool_name == 'SUGGEST':
                strategies = self.tools.generate_strategy_suggestions()
                if not strategies:
                    return "Не удалось сгенерировать стратегии"
                lines = ["💡 Предлагаемые стратегии:\n"]
                for i, s in enumerate(strategies, 1):
                    lines.append(f"{i}. **{s['name']}**")
                    lines.append(f"   {s['description']}")
                    lines.append(f"   Обоснование: {s['rationale']}\n")
                return '\n'.join(lines)
            
            # =============== ПАМЯТЬ ===============
            elif tool_name == 'REMEMBER':
                if params:
                    result = self.tools.remember_command(params)
                    return result.get('summary', str(result))
                return "❌ Укажи что запомнить"
            
            elif tool_name == 'RECALL':
                result = self.tools.recall_commands(params)
                return result.get('summary', str(result))
            
            elif tool_name == 'BLACKLIST':
                parts = params.split(':') if params else []
                symbol = parts[0] if parts else None
                reason = parts[1] if len(parts) > 1 else 'Agent'
                if symbol:
                    result = self.tools.add_to_blacklist(symbol, reason)
                    return result.get('summary', str(result))
                return "❌ Укажи символ"
            
            elif tool_name == 'BLACKLIST_WORST':
                # Добавляет худшие монеты из последнего бэктеста в ЧС
                max_coins = int(params) if params and params.isdigit() else 5
                result = self.tools.blacklist_worst_from_backtest(max_coins=max_coins)
                return result.get('summary', str(result))
            
            elif tool_name == 'WHITELIST_PROFITABLE':
                # Установить whitelist из прибыльных монет бэктеста
                min_pnl = float(params) if params else 0
                result = self.tools.whitelist_profitable_from_backtest(min_pnl=min_pnl)
                return result.get('summary', str(result))
            
            elif tool_name == 'SET_PARAM':
                # Изменить параметр стратегии: [TOOL:SET_PARAM:param_name:value]
                parts = params.split(':') if params else []
                if len(parts) >= 2:
                    param_name = parts[0].strip()
                    param_value = parts[1].strip()
                    result = self.tools.set_strategy_param(param_name, param_value)
                    return result.get('summary', str(result))
                return "❌ Формат: [TOOL:SET_PARAM:param:value]"
            
            # =============== УПРАВЛЕНИЕ ===============
            elif tool_name == 'PAUSE':
                paused = params.lower() == 'true' if params else True
                result = self.tools.pause_scanner(paused)
                return result.get('summary', str(result))
            
            elif tool_name == 'SETTINGS':
                try:
                    settings = json.loads(params) if params else {}
                    result = self.tools.change_settings(settings)
                    return result.get('summary', str(result))
                except json.JSONDecodeError:
                    return "❌ Неверный JSON для настроек"
            
            elif tool_name == 'SL':
                parts = params.split(':') if params else []
                if len(parts) >= 2:
                    trade_id = parts[0]
                    new_sl = float(parts[1])
                    result = self.tools.update_stop_loss(trade_id, new_sl)
                    return result.get('summary', str(result))
                return "❌ Формат: [TOOL:SL:TRADE_ID:PRICE]"
            
            elif tool_name == 'TP':
                parts = params.split(':') if params else []
                if len(parts) >= 2:
                    trade_id = parts[0]
                    new_tp = float(parts[1])
                    result = self.tools.update_take_profit(trade_id, new_tp)
                    return result.get('summary', str(result))
                return "❌ Формат: [TOOL:TP:TRADE_ID:PRICE]"
            
            # =============== СТРАТЕГИЯ (НОВЫЕ) ===============
            elif tool_name == 'STRATEGY':
                if not params:
                    # Просто показать текущую стратегию
                    result = self.tools.get_current_strategy()
                    return result.get('summary', str(result))
                
                parts = params.split(':')
                action = parts[0].upper() if parts else ''
                
                if action == 'SET' and len(parts) >= 3:
                    # [TOOL:STRATEGY:SET:param:value]
                    param = parts[1]
                    value = parts[2]
                    # Валидация - не placeholder'ы
                    if param.upper() in ['PARAM', 'ПАРАМЕТР'] or value.upper() in ['VALUE', 'ЗНАЧЕНИЕ']:
                        return "❌ Укажи конкретный параметр и значение! Пример: [TOOL:STRATEGY:SET:sl:4.5]"
                    result = self.tools.set_strategy_param(param, value)
                    return result.get('summary', str(result))
                
                elif action == 'SAVE' and len(parts) >= 2:
                    # [TOOL:STRATEGY:SAVE:name]
                    name = parts[1]
                    # Валидация - не placeholder
                    if name.upper() in ['NAME', 'ИМЯ', 'НАЗВАНИЕ']:
                        return "❌ Укажи конкретное имя стратегии! Пример: [TOOL:STRATEGY:SAVE:MyStrategy_v1]"
                    result = self.tools.save_strategy(name)
                    return result.get('summary', str(result))
                
                elif action == 'LOAD' and len(parts) >= 2:
                    # [TOOL:STRATEGY:LOAD:name]
                    name = parts[1]
                    result = self.tools.load_strategy(name)
                    return result.get('summary', str(result))
                
                elif action == 'LIST':
                    # [TOOL:STRATEGY:LIST]
                    result = self.tools.list_strategies()
                    return result.get('summary', str(result))
                
                else:
                    return "❌ Неизвестное действие. Используй: STRATEGY, STRATEGY:SET:param:value, STRATEGY:SAVE:name, STRATEGY:LOAD:name, STRATEGY:LIST"
            
            elif tool_name == 'OPTIMIZE':
                days = int(params) if params and params.isdigit() else 30
                result = self.tools.optimize_strategy(days)
                return result.get('summary', str(result))
            
            elif tool_name == 'GRID_OPTIMIZE':
                # [TOOL:GRID_OPTIMIZE:days:top_n] или [TOOL:GRID_OPTIMIZE:days]
                parts = params.split(':') if params else []
                days = int(parts[0]) if parts and parts[0].isdigit() else 30
                top_n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 50
                result = self.tools.grid_optimize_strategy(days, top_n)
                return result.get('summary', str(result))
            
            elif tool_name == 'APPLY_BEST':
                result = self.tools.apply_best_grid_params()
                return result.get('summary', str(result))
            
            elif tool_name == 'OPTIMIZE_BTC':
                # [TOOL:OPTIMIZE_BTC:days:top_n] или [TOOL:OPTIMIZE_BTC:days]
                parts = params.split(':') if params else []
                days = int(parts[0]) if parts and parts[0].isdigit() else 30
                top_n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 50
                fast = True  # По умолчанию быстрый режим
                if len(parts) > 2 and parts[2].lower() == 'full':
                    fast = False
                result = self.tools.optimize_btc_levels(days, top_n, fast_mode=fast)
                # Сохраняем для apply
                if result.get('best_combo'):
                    self._last_btc_opt = result['best_combo']
                return result.get('summary', str(result))
            
            elif tool_name == 'APPLY_BTC':
                # [TOOL:APPLY_BTC]
                levels = getattr(self, '_last_btc_opt', None)
                result = self.tools.apply_btc_levels(levels)
                return result.get('summary', str(result))
            
            elif tool_name == 'BACKTEST_PATTERNS':
                # [TOOL:BACKTEST_PATTERNS:days:top_n]
                parts = params.split(':') if params else []
                days = int(parts[0]) if parts and parts[0].isdigit() else 60
                top_n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 200
                result = self.tools.backtest_patterns(days, top_n)
                return result.get('summary', str(result))
            
            elif tool_name == 'ANALYZE_PATTERNS':
                # [TOOL:ANALYZE_PATTERNS:days] - анализ РЕАЛЬНЫХ сделок
                days = int(params) if params and params.isdigit() else 30
                result = self.tools.analyze_real_patterns(days)
                return result.get('summary', str(result))
            
            elif tool_name == 'ANALYZE_LATE':
                # [TOOL:ANALYZE_LATE] — после сброса, порог из min_change_filter
                # [TOOL:ANALYZE_LATE:all] — все сделки
                mode = params.strip().lower() if params else 'reset'
                if mode not in ('reset', 'all'):
                    mode = 'reset'
                result = self.tools.analyze_late_entries(mode=mode)
                return result.get('summary', str(result))
            
            elif tool_name == 'BACKTEST_CHANGE':
                # [TOOL:BACKTEST_CHANGE:30:100] — бэктест порогов 5-20% за N дней на M монетах
                parts = params.split(':') if params else []
                days = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 30
                top_n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 100
                result = self.tools.backtest_change_thresholds(days=days, top_n=top_n)
                return result.get('summary', str(result))
            
            elif tool_name == 'TOP':
                limit = int(params) if params and params.isdigit() else 20
                result = self.tools.get_top_liquid_coins(limit)
                return result.get('summary', str(result))
            
            elif tool_name == 'HISTORY':
                days = int(params) if params and params.isdigit() else 30
                result = self.tools.get_trade_history(days)
                return result.get('summary', str(result))
            
            elif tool_name == 'CANDLES':
                # [TOOL:CANDLES:SYMBOL:TF:DAYS]
                parts = params.split(':') if params else ['BTC', '15m', '30']
                symbol = parts[0] if parts else 'BTC'
                tf = parts[1] if len(parts) > 1 else '15m'
                days = int(parts[2]) if len(parts) > 2 else 30
                result = self.tools.load_candles_cached(symbol, tf, days)
                return result.get('summary', str(result))
            
            elif tool_name == 'BACKTEST':
                # [TOOL:BACKTEST:STRATEGY:DAYS:TOP_N] или [TOOL:BACKTEST:current:30:20]
                parts = params.split(':') if params else ['current', '30', '20']
                strategy_name = parts[0] if parts else 'current'
                days = int(parts[1]) if len(parts) > 1 else 30
                top_n = int(parts[2]) if len(parts) > 2 else None
                
                if top_n:
                    result = self.tools.run_backtest_multi(strategy_name, days=days, use_top=top_n)
                else:
                    result = self.tools.run_backtest_multi(strategy_name, days=days)
                return result.get('summary', str(result))
            
            # =============== WFA ===============
            elif tool_name == 'WFA':
                # [TOOL:WFA:days:top_n]
                parts = params.split(':') if params else []
                days = int(parts[0]) if parts and parts[0].isdigit() else 30
                top_n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 100
                result = self.tools.walk_forward_analysis(days, top_n)
                return result.get('summary', str(result))
            
            # =============== LIVE TRADING ===============
            elif tool_name == 'LIVE_STATUS':
                result = self.tools.live_status()
                return result.get('summary', str(result))
            
            elif tool_name == 'LIVE_ENABLE':
                enable = params.lower() in ('true', '1', 'yes', 'on') if params else True
                result = self.tools.live_enable(enable)
                return result.get('summary', str(result))
            
            elif tool_name == 'LIVE_OPEN':
                # [TOOL:LIVE_OPEN:SYMBOL:SIDE:SIZE]
                parts = params.split(':') if params else []
                if len(parts) < 2:
                    return "❌ Формат: [TOOL:LIVE_OPEN:SYMBOL:SIDE:SIZE]. Пример: [TOOL:LIVE_OPEN:BTC:SHORT:50]"
                symbol = parts[0]
                side = parts[1].upper()
                size = float(parts[2]) if len(parts) > 2 else None
                result = self.tools.live_open(symbol, side, size)
                return result.get('summary', str(result))
            
            elif tool_name in ('LIVE_CLOSE', 'LIVE_CLOSE_ALL'):
                # [TOOL:LIVE_CLOSE:ID] или [TOOL:LIVE_CLOSE_ALL]
                if tool_name == 'LIVE_CLOSE_ALL' or params.lower() == 'all':
                    result = self.tools.live_close()  # Без ID = закрыть все
                else:
                    result = self.tools.live_close(position_id=params)
                return result.get('summary', str(result))
            
            elif tool_name == 'LIVE_BALANCE':
                result = self.tools.live_balance()
                return result.get('summary', str(result))
            
            elif tool_name == 'LIVE_TEST':
                result = self.tools.live_test_connection()
                return result.get('summary', str(result))
            
            elif tool_name == 'LIVE_SL':
                # [TOOL:LIVE_SL:ID:PCT]
                parts = params.split(':') if params else []
                if len(parts) < 2:
                    return "❌ Формат: [TOOL:LIVE_SL:POSITION_ID:NEW_SL_PCT]"
                pos_id = parts[0]
                new_sl = float(parts[1])
                result = self.tools.live_update_sl(pos_id, new_sl)
                return result.get('summary', str(result))
            
            # =============== MEMORY ===============
            elif tool_name == 'MEMORY':
                # [TOOL:MEMORY:action:data]
                parts = params.split(':') if params else ['list']
                action = parts[0].lower() if parts else 'list'
                data = ':'.join(parts[1:]) if len(parts) > 1 else ''
                if action == 'list':
                    result = self.tools.recall_commands(data)
                elif action in ('add', 'save', 'remember'):
                    result = self.tools.remember_command(data)
                else:
                    result = self.tools.recall_commands(action)
                return result.get('summary', str(result))
            
            else:
                return f"⚠️ Неизвестный инструмент: {tool_name}"
                
        except Exception as e:
            logger.error(f"[AGENT] Tool {tool_name} error: {e}")
            return f"❌ Ошибка {tool_name}: {str(e)}"
    
    # =========================================================================
    # БЫСТРЫЕ КОМАНДЫ (без AI)
    # =========================================================================
    
    def quick_positions(self) -> str:
        """Быстрый список позиций"""
        return self._run_tool('POSITIONS', '')
    
    def quick_close(self, trade_id: str, reason: str = 'MANUAL') -> str:
        """Быстрое закрытие"""
        return self._run_tool('CLOSE', f'{trade_id}:{reason}')
    
    def quick_btc(self) -> str:
        """Быстрый BTC статус"""
        return self._run_tool('BTC', '')
    
    def quick_stats(self, days: int = 7) -> str:
        """Быстрая статистика"""
        return self._run_tool('STATS', str(days))
    
    def quick_suggest(self) -> str:
        """Быстрые рекомендации"""
        return self._run_tool('SUGGEST', '')
    
    # =========================================================================
    # АВТОНОМНЫЙ РЕЖИМ
    # =========================================================================
    
    def analyze_positions(self) -> str:
        """Автономный анализ позиций (для crypto_agent совместимости)"""
        try:
            positions = self.tools.get_open_positions()
            if not positions:
                return "📭 Нет открытых позиций для анализа"
            
            # Собираем контекст
            context = self._build_context()
            
            # Промпт для автономного анализа
            prompt = f"""Проанализируй текущие позиции и реши что делать.

ПРАВИЛА:
1. Убыток > 5% без признаков разворота → закрыть
2. Прибыль > 3% и RSI разворачивается → активировать трейлинг или закрыть часть
3. Позиция в боковике > 2 часов → оценить целесообразность
4. BTC в DANGER → закрыть слабые позиции

Отвечай кратко. Используй [TOOL:CLOSE:ID] если нужно закрыть.
Используй [TOOL:HOLD] если позиции в норме.

{context}"""
            
            messages = [
                {'role': 'system', 'content': self.system_prompt},
                {'role': 'user', 'content': prompt}
            ]
            
            response, model = self.ai.chat(messages, mode='chat', max_tokens=1000)
            final_response, tools = self._execute_tools(response)
            
            return final_response
            
        except Exception as e:
            logger.error(f"[AGENT] analyze_positions error: {e}")
            return f"❌ Ошибка анализа: {str(e)}"
    
    # =========================================================================
    # СТАТУС И ДИАГНОСТИКА
    # =========================================================================
    
    def get_status(self) -> Dict:
        """Статус агента"""
        brain_stats = self.brain.get_brain_stats()
        ai_stats = self.ai.get_stats()
        
        return {
            'version': self.VERSION,
            'running': self.running,
            'autonomous_mode': self.autonomous_mode,
            'ai': ai_stats,
            'brain': brain_stats,
            'trader_connected': self.tools.trader is not None,
            'exchange_connected': self.tools.exchange is not None
        }
    
    def diagnose(self) -> str:
        """Диагностика проблем"""
        lines = [f"🔍 Диагностика Smart Agent v{self.VERSION}:\n"]
        
        # AI
        ai_stats = self.ai.get_stats()
        if ai_stats['deepseek_available']:
            lines.append("✅ DeepSeek (chat + reasoner)")
        else:
            lines.append("❌ DeepSeek НЕ настроен")
            
        if ai_stats['groq_available']:
            lines.append("✅ GROQ instant (fallback)")
        else:
            lines.append("⚠️ GROQ не настроен (нет fallback)")
        
        # Компоненты
        if self.tools.trader:
            lines.append("✅ Trader подключен")
        else:
            lines.append("❌ Trader НЕ подключен - действия не работают!")
        
        if self.tools.exchange:
            lines.append("✅ Exchange подключен")
        else:
            lines.append("⚠️ Exchange не подключен")
        
        # Память
        brain_stats = self.brain.get_brain_stats()
        lines.append(f"\n📝 Память:")
        lines.append(f"  Разговоров: {brain_stats['total_conversations']}")
        lines.append(f"  Команд: {brain_stats['active_commands']}")
        lines.append(f"  Стратегий: {brain_stats['strategies_count']}")
        lines.append(f"  Уроков: {brain_stats['lessons_count']}")
        
        return '\n'.join(lines)
    
    def capabilities(self) -> str:
        """Объяснить возможности"""
        return """🤖 **Smart Agent v5.1 - ЕДИНЫЙ РУКОВОДИТЕЛЬ**

🧠 **Думаю** (DeepSeek chat + reasoner)
• Анализирую ситуацию на рынке
• Рассуждаю о причинах убытков/профитов
• Принимаю решения на основе данных

💾 **Помню** (7 таблиц SQLite)
• Команды: "не торгуй ALPACA" → навсегда
• Уроки: почему сделка провалилась
• Стратегии: какие работают, какие нет
• Разговоры: контекст обсуждений

🔧 **Действую** (20+ инструментов)
• Закрываю позиции: [TOOL:CLOSE:RVV-0012]
• Меняю SL/TP: [TOOL:SL:RVV-0012:0.0850]
• Управляю сканером: [TOOL:PAUSE:true]
• Добавляю в ЧС: [TOOL:BLACKLIST:LINA]

📊 **Исследую**
• Загружаю исторические свечи
• Ищу паттерны в сделках
• Тестирую стратегии (бэктест)

🎯 **Строю стратегии**
• Генерирую на основе данных
• Тестирую перед применением
• Адаптирую к рынку

⚡ **Резерв: GROQ instant** (если DeepSeek упал)
"""


# =============================================================================
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# =============================================================================

smart_agent: Optional[SmartAgent] = None


def create_smart_agent(deepseek_key: str = None, groq_key: str = None) -> SmartAgent:
    """Создать агента"""
    global smart_agent
    smart_agent = SmartAgent(deepseek_key, groq_key)
    return smart_agent


def get_smart_agent() -> Optional[SmartAgent]:
    """Получить агента"""
    return smart_agent
