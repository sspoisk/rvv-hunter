import logging
import random
import json
import time
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from abc import ABC, abstractmethod
import numpy as np
import re

logger = logging.getLogger(__name__)

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================
def calculate_rsi(closes: List[float], period: int = 14) -> float:
    """Простой расчёт RSI без pandas"""
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
    
    # Расчёт RSI с защитой от аномалий
    if losses <= 1e-12:
        rsi = 99.0  # Только рост - аномалия
    elif gains <= 1e-12:
        rsi = 1.0   # Только падение - аномалия
    else:
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))
    
    # Ограничиваем диапазон 5-95
    return max(5.0, min(95.0, rsi))

def calculate_volume_profile(volumes: List[float], closes: List[float], period: int = 20) -> Dict:
    """Анализ объемов и их распределения"""
    if len(volumes) < period or len(closes) < period:
        return {'avg_volume': 0, 'volume_trend': 0, 'volume_spike': False}
    
    recent_volumes = volumes[-period:]
    avg_volume = sum(recent_volumes) / period
    
    # Тренд объемов (последние 5 против предыдущих 5)
    if len(recent_volumes) >= 10:
        recent_avg = sum(recent_volumes[-5:]) / 5
        prev_avg = sum(recent_volumes[-10:-5]) / 5
        volume_trend = (recent_avg - prev_avg) / prev_avg * 100 if prev_avg > 0 else 0
    else:
        volume_trend = 0
    
    # Спайк объемов
    max_volume = max(recent_volumes)
    volume_spike = max_volume > avg_volume * 2.5
    
    # Анализ соотношения объем/цена
    if len(closes) >= period:
        price_changes = []
        for i in range(-period, -1):
            if closes[i] > 0:
                change = (closes[i+1] - closes[i]) / closes[i] * 100
                price_changes.append(change)
        
        avg_price_change = sum(price_changes) / len(price_changes) if price_changes else 0
        volume_price_ratio = volume_trend / avg_price_change if avg_price_change != 0 else 0
    else:
        volume_price_ratio = 0
    
    return {
        'avg_volume': avg_volume,
        'volume_trend': volume_trend,
        'volume_spike': volume_spike,
        'volume_price_ratio': volume_price_ratio,
        'max_volume': max_volume,
        'current_volume': recent_volumes[-1] if recent_volumes else 0
    }

def calculate_obv(closes: List[float], volumes: List[float]) -> List[float]:
    """Расчет On-Balance Volume"""
    if not closes or not volumes or len(closes) != len(volumes):
        return []
    
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv

def calculate_bollinger_bands(closes: List[float], period: int = 20, std_dev: float = 2.0) -> Dict:
    """
    Расчёт полос Боллинджера
    Returns: upper, middle, lower, %B (позиция цены в полосах)
    """
    if len(closes) < period:
        return {'upper': 0, 'middle': 0, 'lower': 0, 'percent_b': 50, 'bandwidth': 0}
    
    # SMA (средняя полоса)
    recent_closes = closes[-period:]
    middle = sum(recent_closes) / period
    
    # Стандартное отклонение
    variance = sum((x - middle) ** 2 for x in recent_closes) / period
    std = variance ** 0.5
    
    # Верхняя и нижняя полосы
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    
    # %B - позиция текущей цены в полосах (0-100)
    current_price = closes[-1]
    if upper != lower:
        percent_b = ((current_price - lower) / (upper - lower)) * 100
    else:
        percent_b = 50
    
    # Bandwidth - ширина полос (волатильность)
    bandwidth = ((upper - lower) / middle) * 100 if middle > 0 else 0
    
    return {
        'upper': upper,
        'middle': middle,
        'lower': lower,
        'percent_b': percent_b,
        'bandwidth': bandwidth,
        'price_vs_upper': ((current_price - upper) / upper * 100) if upper > 0 else 0,
        'price_vs_lower': ((current_price - lower) / lower * 100) if lower > 0 else 0
    }

def calculate_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
    """
    Расчёт MACD
    Returns: macd_line, signal_line, histogram, trend
    """
    if len(closes) < slow + signal:
        return {'macd': 0, 'signal': 0, 'histogram': 0, 'trend': 'neutral', 'divergence': 'none'}
    
    def ema(data: List[float], period: int) -> List[float]:
        result = []
        multiplier = 2 / (period + 1)
        # Начальное значение - SMA
        sma = sum(data[:period]) / period
        result.append(sma)
        for i in range(period, len(data)):
            result.append((data[i] - result[-1]) * multiplier + result[-1])
        return result
    
    # EMA быстрая и медленная
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    
    # Выравниваем длины
    min_len = min(len(ema_fast), len(ema_slow))
    ema_fast = ema_fast[-min_len:]
    ema_slow = ema_slow[-min_len:]
    
    # MACD линия
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    
    # Сигнальная линия (EMA от MACD)
    if len(macd_line) >= signal:
        signal_line = ema(macd_line, signal)
    else:
        signal_line = macd_line
    
    # Гистограмма
    min_len = min(len(macd_line), len(signal_line))
    histogram = [m - s for m, s in zip(macd_line[-min_len:], signal_line[-min_len:])]
    
    current_macd = macd_line[-1] if macd_line else 0
    current_signal = signal_line[-1] if signal_line else 0
    current_histogram = histogram[-1] if histogram else 0
    
    # Определяем тренд
    if current_macd > current_signal and current_histogram > 0:
        trend = 'bullish'
    elif current_macd < current_signal and current_histogram < 0:
        trend = 'bearish'
    else:
        trend = 'neutral'
    
    # Проверка дивергенции (упрощённая)
    divergence = 'none'
    if len(closes) >= 10 and len(macd_line) >= 5:
        price_trend = closes[-1] > closes[-5]
        macd_trend = macd_line[-1] > macd_line[-5] if len(macd_line) >= 5 else False
        if price_trend and not macd_trend:
            divergence = 'bearish'  # Цена растёт, MACD падает - медвежья дивергенция
        elif not price_trend and macd_trend:
            divergence = 'bullish'  # Цена падает, MACD растёт - бычья дивергенция
    
    return {
        'macd': current_macd,
        'signal': current_signal,
        'histogram': current_histogram,
        'trend': trend,
        'divergence': divergence
    }

# ============================================================================
# BASE AI ENGINE (абстрактный класс)
# ============================================================================
class BaseAIEngine(ABC):
    """Базовый класс для AI движков"""
    def __init__(self, api_key: str, provider_name: str):
        self.api_key = api_key
        self.provider_name = provider_name
        self.postmortem_cache = []  # Кэш посмертных анализов
        self.stats_context = ""     # Контекст статистики
        self.btc_trend_context = "" # Контекст тренда Bitcoin
        self.trading_style = "normal"  # aggressive, normal, conservative
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'total_tokens_used': 0,
            'last_request_time': None,
            'avg_response_time': 0
        }

    def set_trading_style(self, style: str):
        """Установить режим торговли: aggressive, normal, conservative"""
        if style in ['aggressive', 'normal', 'conservative']:
            self.trading_style = style
            logger.info(f"[{self.provider_name.upper()}] Trading style set to: {style}")

    @abstractmethod
    def test_connection(self) -> bool:
        pass

    @abstractmethod
    def analyze_coin(self, symbol: str, ohlcv: Dict, change_24h: float, btc_trend: Dict = None) -> Optional[Dict]:
        pass

    def get_stats(self) -> Dict:
        return {**self.stats, 'provider': self.provider_name, 'trading_style': self.trading_style}

    def set_learning_context(self, postmortems: List[Dict], stats_context: str = "", btc_trend_context: str = ""):
        """Загрузить посмертные анализы для обучения AI"""
        self.postmortem_cache = postmortems[-10:] if postmortems else []
        self.stats_context = stats_context
        self.btc_trend_context = btc_trend_context
        if self.postmortem_cache:
            logger.info(f"[{self.provider_name.upper()}] Загружено {len(self.postmortem_cache)} посмертных анализов")

    def _build_learning_prompt(self) -> str:
        """Построить умную часть промпта с обучением на ошибках"""
        if not hasattr(self, 'postmortem_cache') or not self.postmortem_cache:
            return ""
        
        lines = ["\n===== 🎓 ОБУЧЕНИЕ НА ОШИБКАХ ====="]
        
        # Контекст статистики
        if hasattr(self, 'stats_context') and self.stats_context:
            lines.append(self.stats_context)
        
        # Контекст BTC
        if hasattr(self, 'btc_trend_context') and self.btc_trend_context:
            lines.append(self.btc_trend_context)
        
        # Анализируем паттерны ошибок
        weak_rsi_losses = 0
        weak_bollinger_losses = 0
        against_btc_losses = 0
        low_confidence_losses = 0
        symbol_losses = {}
        total_losses = len(self.postmortem_cache)
        
        for pm in self.postmortem_cache:
            symbol = pm.get('symbol', 'N/A')
            side = pm.get('side', 'SHORT')
            rsi = pm.get('rsi_at_entry', 50)
            bollinger_b = pm.get('bollinger_b_at_entry', 50)
            btc_trend = pm.get('btc_trend_at_entry', 'neutral')
            confidence = pm.get('confidence_at_entry', 0)
            
            # Подсчёт паттернов
            if side == 'SHORT' and rsi < 75:
                weak_rsi_losses += 1
            elif side == 'LONG' and rsi > 40:
                weak_rsi_losses += 1
            
            if side == 'SHORT' and bollinger_b < 90:
                weak_bollinger_losses += 1
            elif side == 'LONG' and bollinger_b > 10:
                weak_bollinger_losses += 1
            
            if (side == 'SHORT' and btc_trend == 'bullish') or (side == 'LONG' and btc_trend == 'bearish'):
                against_btc_losses += 1
            
            if confidence < 80:
                low_confidence_losses += 1
            
            # Статистика по монетам
            clean_symbol = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
            symbol_losses[clean_symbol] = symbol_losses.get(clean_symbol, 0) + 1
        
        # Формируем ОБЩИЕ УРОКИ
        lines.append("\n📊 ОБЩИЕ УРОКИ (паттерны ошибок):")
        
        if weak_rsi_losses >= 2:
            pct = weak_rsi_losses / total_losses * 100
            lines.append(f"⚠️ {weak_rsi_losses} убытков ({pct:.0f}%) при слабом RSI → ТРЕБУЙ RSI >75 для SHORT, <40 для LONG!")
        
        if weak_bollinger_losses >= 2:
            pct = weak_bollinger_losses / total_losses * 100
            lines.append(f"⚠️ {weak_bollinger_losses} убытков ({pct:.0f}%) при слабом Bollinger → ТРЕБУЙ %B >90 для SHORT, <10 для LONG!")
        
        if against_btc_losses >= 2:
            pct = against_btc_losses / total_losses * 100
            lines.append(f"⚠️ {against_btc_losses} убытков ({pct:.0f}%) против тренда BTC → НЕ ТОРГУЙ ПРОТИВ BTC!")
        
        if low_confidence_losses >= 2:
            pct = low_confidence_losses / total_losses * 100
            lines.append(f"⚠️ {low_confidence_losses} убытков ({pct:.0f}%) при confidence <80% → ПОВЫСЬ ПОРОГ УВЕРЕННОСТИ!")
        
        # Формируем СПЕЦИФИЧНЫЕ УРОКИ (по монетам)
        bad_symbols = [s for s, count in symbol_losses.items() if count >= 2]
        if bad_symbols:
            lines.append("\n🚫 ПРОБЛЕМНЫЕ МОНЕТЫ (>=2 убытков):")
            for sym in bad_symbols[:5]:
                lines.append(f"• {sym}: {symbol_losses[sym]} убытков → ИЗБЕГАЙ или ПОВЫСЬ ТРЕБОВАНИЯ!")
        
        # Последние 3 убытка для контекста
        lines.append("\n📉 Последние убытки:")
        for i, pm in enumerate(self.postmortem_cache[:3], 1):
            symbol = pm.get('symbol', 'N/A').replace('/USDT:USDT', '')
            side = pm.get('side', '?')
            loss = pm.get('loss_amount', 0)
            rsi = pm.get('rsi_at_entry', 50)
            btc = pm.get('btc_trend_at_entry', 'N/A')
            problems = pm.get('problem_count', 0)
            lines.append(f"{i}. {symbol} {side}: -${loss:.2f} | RSI:{rsi:.0f} | BTC:{btc} | Проблем:{problems}")
        
        lines.append("\n🎯 ВЫВОД: Входи только когда >=3 индикатора подтверждают сигнал!")
        return '\n'.join(lines)

    def _calculate_atr(self, highs: List[float], lows: List[float],
                      closes: List[float], period: int = 14) -> float:
        """Расчет Average True Range (ATR)"""
        try:
            if len(highs) < period or len(lows) < period or len(closes) < period:
                return 0.0
            
            high_arr = np.array(highs[-period:])
            low_arr = np.array(lows[-period:])
            close_arr = np.array(closes[-period:])
            
            tr1 = high_arr - low_arr
            tr2 = np.abs(high_arr - np.roll(close_arr, 1))
            tr3 = np.abs(low_arr - np.roll(close_arr, 1))
            tr = np.maximum(np.maximum(tr1, tr2), tr3)
            tr = tr[1:]
            
            atr = np.mean(tr) if len(tr) > 0 else 0.0
            return float(atr)
        except Exception as e:
            logger.debug(f"[AI] ATR calculation error: {e}")
            return 0.0

    def _calculate_support_resistance(self, closes: List[float], num_levels: int = 3) -> List[float]:
        """Вычисление уровней поддержки и сопротивления"""
        try:
            if len(closes) < 20:
                return []
            
            data = closes[-50:]
            min_price = min(data)
            max_price = max(data)
            price_range = max_price - min_price
            
            levels = []
            for i in range(num_levels):
                level = min_price + (price_range * (i + 1) / (num_levels + 1))
                levels.append(level)
            return levels
        except Exception as e:
            logger.debug(f"[AI] S/R calculation error: {e}")
            return []

    def _analyze_volume_context(self, volume_data: Dict, position_type: str) -> str:
        """Анализ контекста объемов для промпта"""
        if not volume_data:
            return ""
        
        avg_vol = volume_data.get('avg_volume', 0)
        vol_trend = volume_data.get('volume_trend', 0)
        vol_spike = volume_data.get('volume_spike', False)
        vol_ratio = volume_data.get('volume_price_ratio', 0)
        
        context = f"\n===== АНАЛИЗ ОБЪЕМОВ =====\n"
        
        if position_type == "SHORT":
            if vol_trend > 0:
                context += f"⚠️ Растущие объемы при росте цены - возможен продолжение тренда\n"
            if vol_spike:
                context += f"🔥 Спайк объемов - высокая волатильность, возможен резкий откат\n"
            if vol_ratio < 0.5:
                context += f"📉 Объемы не подтверждают рост - сигнал SHORT надежнее\n"
        else:  # LONG
            if vol_trend > 0:
                context += f"✅ Растущие объемы при падении цены - возможен сильный отскок\n"
            if vol_spike:
                context += f"🔥 Спайк объемов при падении - возможен разворот\n"
            if vol_ratio > 1.5:
                context += f"📈 Объемы подтверждают падение - будь осторожен с LONG\n"
        
        context += f"Средний объем: {avg_vol:,.0f}\n"
        context += f"Тренд объемов: {vol_trend:+.1f}%\n"
        return context

    def _prepare_market_data(self, symbol: str, ohlcv: Dict, change_24h: float) -> str:
        """Подготовка рыночных данных для AI"""
        try:
            data_15m = ohlcv.get('15m', {})
            data_1h = ohlcv.get('1h', {})
            if not data_15m:
                return ""
            
            closes_15m = data_15m.get('close', [])
            closes_1h = data_1h.get('close', []) if data_1h else closes_15m
            current_price = closes_15m[-1] if closes_15m else 0
            
            # ═══ ПРОВЕРКА МИНИМУМА ДАННЫХ ═══
            # Нужно минимум 20 свечей для корректного расчёта индикаторов
            if len(closes_15m) < 20:
                symbol_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
                logger.warning(f"[AI] ❌ SKIP {symbol_clean}: недостаточно свечей ({len(closes_15m)}<20)")
                return ""
            
            volume_data = {}
            if data_15m.get('volume') and len(data_15m['volume']) >= 20:
                volume_data = calculate_volume_profile(
                    data_15m['volume'],
                    closes_15m,
                    period=20
                )
            
            # Расчет OBV для анализа
            obv = []
            if data_15m.get('volume') and closes_15m:
                obv = calculate_obv(closes_15m, data_15m['volume'])
            
            atr_15m = self._calculate_atr(
                data_15m.get('high', []),
                data_15m.get('low', []),
                closes_15m
            )
            
            atr_1h = self._calculate_atr(
                data_1h.get('high', []),
                data_1h.get('low', []),
                closes_1h
            ) if data_1h else atr_15m * 2
            
            sr_levels = self._calculate_support_resistance(closes_15m)
            
            if len(closes_15m) > 10:
                change_5m = ((closes_15m[-1] / closes_15m[-6]) - 1) * 100 if len(closes_15m) >= 6 else 0
                change_15m = ((closes_15m[-1] / closes_15m[-15]) - 1) * 100 if len(closes_15m) >= 15 else 0
            else:
                change_5m = change_15m = 0
            
            # BOLLINGER BANDS
            bollinger = calculate_bollinger_bands(closes_15m, period=20, std_dev=2.0)
            
            # MACD
            macd = calculate_macd(closes_15m, fast=12, slow=26, signal=9)
            
            market_data = {
                "symbol": symbol,
                "current_price": current_price,
                "change_24h": change_24h,
                "change_15m": change_15m,
                "change_5m": change_5m,
                "atr_15m": atr_15m,
                "atr_1h": atr_1h,
                "atr_percent_15m": (atr_15m / current_price * 100) if current_price > 0 else 0,
                "support_resistance_levels": sr_levels,
                "volume_data": volume_data,
                "recent_prices": closes_15m[-20:] if len(closes_15m) >= 20 else closes_15m,
                "timestamp": datetime.utcnow().isoformat(),
                # НОВЫЕ ИНДИКАТОРЫ
                "bollinger": {
                    "upper": bollinger['upper'],
                    "middle": bollinger['middle'],
                    "lower": bollinger['lower'],
                    "percent_b": bollinger['percent_b'],  # 0-100, <20 перепродан, >80 перекуплен
                    "bandwidth": bollinger['bandwidth']   # волатильность
                },
                "macd": {
                    "value": macd['macd'],
                    "signal": macd['signal'],
                    "histogram": macd['histogram'],
                    "trend": macd['trend'],        # bullish/bearish/neutral
                    "divergence": macd['divergence']  # bullish/bearish/none
                }
            }
            
            if obv:
                market_data["obv_trend"] = "up" if obv[-1] > obv[-5] else "down" if obv[-1] < obv[-5] else "neutral"
            
            return json.dumps(market_data, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[AI] Data preparation error: {e}")
            return ""

    def _create_analysis_prompt(self, market_data_str: str, atr: float, current_price: float,
                               rsi_15m: float, symbol: str, change_24h: float, btc_trend: Dict = None,
                               trading_style: str = "normal") -> str:
        """Создание промпта для анализа с поддержкой LONG/SHORT, Bollinger, MACD и режимов торговли"""
        learning_prompt = self._build_learning_prompt()
        
        # Настройки под режим торговли
        style_config = {
            'aggressive': {'rsi_short': 65, 'rsi_long': 45, 'min_conf': 65, 'desc': 'АГРЕССИВНЫЙ - больше сделок'},
            'normal': {'rsi_short': 70, 'rsi_long': 40, 'min_conf': 75, 'desc': 'НОРМАЛЬНЫЙ - баланс'},
            'conservative': {'rsi_short': 75, 'rsi_long': 35, 'min_conf': 85, 'desc': 'КОНСЕРВАТИВНЫЙ - только лучшие'}
        }
        style = style_config.get(trading_style, style_config['normal'])
        
        # Контекст тренда Bitcoin
        btc_context = ""
        if btc_trend:
            trend = btc_trend.get('trend', 'neutral')
            rsi_1h = btc_trend.get('rsi_1h', 50)
            change_24h_btc = btc_trend.get('change_24h', 0)
            strength = btc_trend.get('strength', 'stable')
            bull_score = btc_trend.get('bullish_score', 0)
            bear_score = btc_trend.get('bearish_score', 0)
            signals = btc_trend.get('trend_signals', [])
            
            signals_str = ', '.join(signals[:3]) if signals else 'нет явных сигналов'
            
            if trend == 'bullish':
                btc_context = f"""
===== КОНТЕКСТ BITCOIN =====
📈 BTC БЫЧИЙ ТРЕНД ({strength.upper()})
Score: Bull={bull_score} vs Bear={bear_score} | RSI(1h): {rsi_1h:.1f} | 24h: {change_24h_btc:+.1f}%
Сигналы: {signals_str}
⚠️ SHORT РИСКОВАННО - требуется повышенная уверенность (+15% к confidence)
✅ LONG ПРЕДПОЧТИТЕЛЬНЕЕ - можно ослабить RSI требования на 5 пунктов
"""
            elif trend == 'bearish':
                btc_context = f"""
===== КОНТЕКСТ BITCOIN =====
📉 BTC МЕДВЕЖИЙ ТРЕНД ({strength.upper()})
Score: Bull={bull_score} vs Bear={bear_score} | RSI(1h): {rsi_1h:.1f} | 24h: {change_24h_btc:+.1f}%
Сигналы: {signals_str}
⚠️ LONG РИСКОВАННО - требуется повышенная уверенность (+15% к confidence)
✅ SHORT ПРЕДПОЧТИТЕЛЬНЕЕ - можно ослабить RSI требования на 5 пунктов
"""
            else:
                btc_context = f"""
===== КОНТЕКСТ BITCOIN =====
⚖️ BTC НЕЙТРАЛЬНЫЙ ТРЕНД
Score: Bull={bull_score} vs Bear={bear_score} | RSI(1h): {rsi_1h:.1f} | 24h: {change_24h_btc:+.1f}%
• Торговать по внутренним сигналам монеты
"""
        
        return f"""{btc_context}
{learning_prompt}

🎯 РЕЖИМ: {style['desc']}
📊 Символ: {symbol} | Изменение 24ч: {change_24h:+.2f}%
📈 RSI(14) на 15m: {rsi_15m:.1f}

===== ИНДИКАТОРЫ ДЛЯ АНАЛИЗА =====
1. RSI (Relative Strength Index):
   • Для SHORT: RSI > {style['rsi_short']} = перекупленность
   • Для LONG: RSI < {style['rsi_long']} = перепроданность

2. BOLLINGER BANDS (смотри в данных):
   • %B > 100: цена ВЫШЕ верхней полосы → сильный сигнал SHORT
   • %B < 0: цена НИЖЕ нижней полосы → сильный сигнал LONG
   • %B = 50: цена на средней линии

3. MACD (смотри в данных):
   • trend=bullish + histogram>0: восходящий импульс → LONG
   • trend=bearish + histogram<0: нисходящий импульс → SHORT
   • divergence=bearish: цена растёт, MACD падает → готовься к развороту вниз
   • divergence=bullish: цена падает, MACD растёт → готовься к развороту вверх

===== КОМБИНИРОВАННЫЕ СИГНАЛЫ =====
СИЛЬНЫЙ SHORT (3+ совпадения):
✓ RSI > {style['rsi_short']}
✓ Bollinger %B > 95 (у верхней полосы)
✓ MACD histogram < 0 или divergence=bearish
✓ Памп > 8% за 24ч

СИЛЬНЫЙ LONG (3+ совпадения):
✓ RSI < {style['rsi_long']}
✓ Bollinger %B < 5 (у нижней полосы)
✓ MACD histogram > 0 или divergence=bullish
✓ Падение > 5% за 24ч

РЫНОЧНЫЕ ДАННЫЕ:
{market_data_str}

===== ПРАВИЛА ВЫДАЧИ СИГНАЛОВ =====
1. Минимальный confidence: {style['min_conf']}%
2. Нужно минимум 2-3 подтверждающих индикатора
3. При сильном тренде BTC - торгуй только по тренду
4. При дивергенции MACD - повышай confidence на +10%
5. При Bollinger %B в экстремумах (<5 или >95) - сигнал надежнее

ФОРМАТ ОТВЕТА — СТРОГО ВАЛИДНЫЙ JSON:
{{
"action": "SHORT" или "LONG" или "WAIT",
"confidence": 0-100,
"reason": "краткое объяснение (макс 100 символов)",
"stop_loss_pct": 2.0-5.0,
"take_profit_1_pct": 3.0-7.0,
"take_profit_2_pct": 6.0-12.0
}}

SL/TP ПРАВИЛА:
• SHORT: SL выше цены, TP ниже цены
• LONG: SL ниже цены, TP выше цены
• SL = 1-2x ATR, TP1 = 2-3x ATR, TP2 = 4-5x ATR

Анализируй и ответь ТОЛЬКО JSON."""

    def _get_system_prompt(self) -> str:
        """Системный промпт для AI - загружает кастомный если есть"""
        try:
            from database import db
            custom_prompt = db.get_active_prompt()
            if custom_prompt and custom_prompt.get('prompt_text'):
                logger.debug(f"[AI] Using custom prompt: {custom_prompt.get('name')}")
                return custom_prompt['prompt_text']
        except Exception as e:
            logger.debug(f"[AI] Could not load custom prompt: {e}")
        return self._get_default_prompt()

    def _get_default_prompt(self) -> str:
        """Дефолтный системный промпт"""
        return """Ты опытный трейдер-аналитик криптовалютного рынка, специализирующийся на поиске точек входа для SHORT и LONG позиций.
Твоя задача:
1. Для SHORT: находить перекупленные активы после сильного пампа
2. Для LONG: находить перепроданные активы после сильного падения или коррекции
3. Точно рассчитывать стоп-лосс и тейк-профит уровни
4. Учитывать объемы, RSI и тренд Bitcoin

ВАЖНО:
- Для SHORT: Stop Loss должен быть ВЫШЕ цены, Take Profit НИЖЕ цены
- Для LONG: Stop Loss должен быть НИЖЕ цены, Take Profit ВЫШЕ цены
- Используй точку как десятичный разделитель, а не запятую!
- Пример правильного формата: $0.01430

- Будь гибким: учитывай контекст тренда Bitcoin
- Для LONG: RSI < 40 хороший сигнал, RSI < 35 - отличный сигнал
- Для SHORT: RSI > 75 хороший сигнал, RSI > 80 - отличный сигнал

Будь точным и конкретным в расчетах. Не используй шаблонные фразы."""

    def _parse_ai_response(self, response: str, current_price: float, atr: float, position_type: str = "SHORT") -> Dict:
        """Парсинг ответа от AI с поддержкой LONG/SHORT"""
        try:
            response_clean = response.strip()
            json_pattern = r'\{.*\}'
            json_match = re.search(json_pattern, response_clean, re.DOTALL)
            
            if json_match:
                try:
                    json_str = json_match.group(0)
                    data = json.loads(json_str)
                    
                    action = data.get('action', 'WAIT').upper()
                    confidence = float(data.get('confidence', 70))
                    stop_loss_pct = float(data.get('stop_loss_pct', 3.0))
                    take_profit_1_pct = float(data.get('take_profit_1_pct', 5.0))
                    take_profit_2_pct = float(data.get('take_profit_2_pct', 10.0))
                    
                    # Определяем тип позиции
                    if action in ['SHORT', 'SELL']:
                        position_type = 'SHORT'
                    elif action in ['LONG', 'BUY']:
                        position_type = 'LONG'
                    else:
                        position_type = 'WAIT'
                    
                    # Рассчитываем уровни в зависимости от типа позиции
                    if position_type == 'SHORT':
                        stop_loss = current_price * (1 + stop_loss_pct/100)
                        take_profit_1 = current_price * (1 - take_profit_1_pct/100)
                        take_profit_2 = current_price * (1 - take_profit_2_pct/100)
                    elif position_type == 'LONG':
                        stop_loss = current_price * (1 - stop_loss_pct/100)
                        take_profit_1 = current_price * (1 + take_profit_1_pct/100)
                        take_profit_2 = current_price * (1 + take_profit_2_pct/100)
                    else:
                        stop_loss = 0
                        take_profit_1 = 0
                        take_profit_2 = 0
                    
                    reason = data.get('reason', 'AI Analysis')
                    
                    return {
                        'action': position_type,
                        'confidence': confidence,
                        'stop_loss': stop_loss,
                        'take_profit': [take_profit_1, take_profit_2],
                        'reason': reason,
                        'analysis_raw': response,
                        'direction': position_type,
                        'stop_loss_pct': stop_loss_pct,
                        'take_profit_1_pct': take_profit_1_pct,
                        'take_profit_2_pct': take_profit_2_pct
                    }
                except json.JSONDecodeError as e:
                    logger.warning(f"[AI] Failed to parse JSON: {e}")
            
            # Fallback для старых форматов
            lines = response.strip().split('\n')
            confidence = 70
            action = "WAIT"
            stop_loss = current_price * 1.03
            take_profit_1 = current_price * 0.97
            take_profit_2 = current_price * 0.94
            reason = "AI Analysis"
            
            for line in lines:
                line_lower = line.lower()
                if 'уверенность' in line_lower or 'confidence' in line_lower:
                    for word in line.split():
                        if '%' in word:
                            try:
                                conf = float(word.replace('%', '').strip())
                                if 0 <= conf <= 100:
                                    confidence = conf
                            except Exception:
                                pass
                if 'short' in line_lower or 'шорт' in line_lower:
                    if 'рекомендую' in line_lower or 'сигнал' in line_lower:
                        action = "SHORT"
                if 'long' in line_lower or 'лонг' in line_lower or 'покуп' in line_lower:
                    if 'рекомендую' in line_lower or 'сигнал' in line_lower:
                        action = "LONG"
                if 'sl:' in line_lower or 'стоп:' in line_lower or 'stop loss:' in line_lower:
                    for word in line.split():
                        word_clean = word.replace('$', '').replace(',', '.').strip()
                        try:
                            sl = float(word_clean)
                            if sl > 0 and sl < current_price * 2:
                                stop_loss = sl
                        except Exception:
                            pass
                if 'tp:' in line_lower or 'тейк:' in line_lower or 'take profit:' in line_lower:
                    parts = line.split(':')
                    if len(parts) > 1:
                        tp_str = parts[1]
                        tp_values = []
                        tp_str_clean = tp_str.replace(',', '.')
                        for val in tp_str_clean.split():
                            val_clean = val.replace('$', '').strip()
                            try:
                                tp = float(val_clean)
                                if tp > 0 and tp < current_price * 2 and tp > current_price * 0.5:
                                    tp_values.append(tp)
                            except Exception:
                                pass
                        if len(tp_values) >= 1:
                            take_profit_1 = tp_values[0]
                        if len(tp_values) >= 2:
                            take_profit_2 = tp_values[1]
            
            # Корректировка уровней для SHORT
            if action == "SHORT":
                if stop_loss <= current_price or stop_loss > current_price * 1.20:
                    stop_loss = current_price * 1.03
                if take_profit_1 >= current_price:
                    take_profit_1 = current_price * 0.97
                if take_profit_2 >= current_price:
                    take_profit_2 = current_price * 0.94
                if take_profit_2 >= take_profit_1:
                    take_profit_2 = take_profit_1 * 0.97
            
            # Корректировка уровней для LONG
            elif action == "LONG":
                if stop_loss >= current_price or stop_loss < current_price * 0.80:
                    stop_loss = current_price * 0.97
                if take_profit_1 <= current_price:
                    take_profit_1 = current_price * 1.03
                if take_profit_2 <= current_price:
                    take_profit_2 = current_price * 1.06
                if take_profit_2 <= take_profit_1:
                    take_profit_2 = take_profit_1 * 1.03
            
            return {
                'action': action,
                'confidence': confidence,
                'stop_loss': stop_loss,
                'take_profit': [take_profit_1, take_profit_2],
                'reason': reason,
                'analysis_raw': response,
                'direction': action
            }
            
        except Exception as e:
            logger.error(f"[AI] Response parsing error: {e}")
            return self._create_default_signal(current_price)

    def _create_default_signal(self, current_price: float) -> Dict:
        """Создание сигнала по умолчанию"""
        return {
            'action': "WAIT",
            'confidence': 50,
            'stop_loss': current_price * 1.03,
            'take_profit': [current_price * 0.97, current_price * 0.94],
            'reason': "Default signal",
            'analysis_raw': "",
            'direction': "NEUTRAL"
        }

# ============================================================================
# DEEPSEEK AI ENGINE
# ============================================================================
class DeepSeekEngine(BaseAIEngine):
    """AI движок на DeepSeek API"""
    def __init__(self, api_key: str, max_retries: int = 3):
        super().__init__(api_key, "deepseek")
        self.max_retries = max_retries
        try:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com"
            )
            logger.info("[DeepSeek] Client initialized")
        except ImportError:
            raise ImportError("openai library not installed. Run: pip install openai")
        except Exception as e:
            logger.error(f"[DeepSeek] Init error: {e}")
            raise

    def test_connection(self) -> bool:
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hi."}
                ],
                max_tokens=5,
                temperature=0.1
            )
            if response.choices[0].message.content:
                logger.info("[DeepSeek] Connection test: SUCCESS")
                return True
            return False
        except Exception as e:
            logger.error(f"[DeepSeek] Connection test: FAILED - {str(e)}")
            return False

    def _call_api(self, prompt: str, system_prompt: str = None) -> Tuple[Optional[str], float]:
        """Вызов DeepSeek API, возвращает (ответ, время_ответа)"""
        if not system_prompt:
            system_prompt = self._get_system_prompt()
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        start_time = time.time()
        for attempt in range(self.max_retries):
            try:
                self.stats['total_requests'] += 1
                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1500,
                    top_p=0.95,
                    frequency_penalty=0.3,
                    presence_penalty=0.3
                )
                elapsed_time = time.time() - start_time
                self.stats['last_request_time'] = datetime.utcnow()
                self.stats['avg_response_time'] = (
                    self.stats['avg_response_time'] * 0.8 + elapsed_time * 0.2
                )
                if response.usage:
                    self.stats['total_tokens_used'] += response.usage.total_tokens
                
                result = response.choices[0].message.content
                self.stats['successful_requests'] += 1
                logger.info(f"[DeepSeek] Request success ({elapsed_time:.2f}s)")
                return result, elapsed_time
                
            except Exception as e:
                self.stats['failed_requests'] += 1
                logger.warning(f"[DeepSeek] Attempt {attempt+1} failed: {str(e)[:100]}")
                if attempt < self.max_retries - 1:
                    delay = 2 ** attempt + random.uniform(0, 1)
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"[DeepSeek] All attempts failed: {e}")
                    return None, time.time() - start_time
        
        return None, time.time() - start_time

    def analyze_coin(self, symbol: str, ohlcv: Dict, change_24h: float, btc_trend: Dict = None) -> Optional[Dict]:
        logger.info(f"[DeepSeek] Analyzing {symbol} ({change_24h:+.1f}%)...")
        try:
            market_data_str = self._prepare_market_data(symbol, ohlcv, change_24h)
            if not market_data_str:
                return None
            
            data_15m = ohlcv.get('15m', {})
            data_1h = ohlcv.get('1h', {})
            closes_15m = data_15m.get('close', [])
            closes_1h = data_1h.get('close', []) if data_1h else closes_15m
            
            current_price = closes_15m[-1] if closes_15m else 0
            if current_price <= 0:
                return None
            
            atr = self._calculate_atr(
                data_15m.get('high', []),
                data_15m.get('low', []),
                closes_15m
            )
            
            # Рассчитываем RSI(14) на 15m
            if len(closes_15m) >= 15:
                rsi_15m = calculate_rsi(closes_15m[-50:])
            else:
                rsi_15m = 50.0
            
            # Рассчитываем Bollinger и MACD для пост-мортем анализа
            bollinger = calculate_bollinger_bands(closes_15m, period=20, std_dev=2.0)
            macd = calculate_macd(closes_15m, fast=12, slow=26, signal=9)
            
            prompt = self._create_analysis_prompt(market_data_str, atr, current_price, rsi_15m, symbol, change_24h, btc_trend, self.trading_style)
            response, response_time = self._call_api(prompt)
            
            if not response:
                return self._create_default_signal(current_price)
            
            signal = self._parse_ai_response(response, current_price, atr)
            
            # Добавляем индикаторы для пост-мортем анализа
            signal['rsi_15m'] = rsi_15m
            signal['bollinger_b'] = bollinger.get('percent_b', 50.0)
            signal['macd_histogram'] = macd.get('histogram', 0.0)
            signal['macd_divergence'] = macd.get('divergence', 'none')
            
            symbol_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
            atr_percent = (atr / current_price * 100) if current_price > 0 else 0
            
            # ═══ БЛОКИРОВКА: Невалидные данные ═══
            # RSI=100 означает что все свечи были зелёными (нет данных)
            # ATR<0.1% означает что свечи не загрузились
            if rsi_15m >= 99.9 or atr_percent < 0.1:
                logger.warning(f"[DeepSeek] ❌ SKIP {symbol_clean}: невалидные данные (RSI={rsi_15m:.1f}, ATR={atr_percent:.2f}%)")
                return None
            
            # Генерируем анализ на русском (v5.6 - исправлена кодировка)
            if signal['action'] in ['SHORT', 'LONG']:
                price_info = f"Цена: ${current_price:.6f}"
                if signal['action'] == 'SHORT':
                    analysis_header = f"🔴 AI СИГНАЛ SHORT: {symbol_clean}"
                    entry_info = f"Вход: ${current_price:.6f} | Рост 24ч: +{change_24h:.1f}%"
                    direction_emoji = "🔻"
                else:
                    analysis_header = f"🟢 AI СИГНАЛ LONG: {symbol_clean}"
                    entry_info = f"Вход: ${current_price:.6f} | Падение 24ч: {change_24h:.1f}%"
                    direction_emoji = "🔺"
                
                signal['analysis_ru'] = f"""{analysis_header}
{direction_emoji} НАПРАВЛЕНИЕ: {signal['action']}

📊 АНАЛИЗ:
{response}

🎯 ПАРАМЕТРЫ:
- {price_info}
- {entry_info}
- RSI(14) на 15m: {rsi_15m:.1f}
- ATR: {atr_percent:.2f}%
- Уверенность: {signal['confidence']:.0f}%

⚡ УРОВНИ (AI рекомендует):
SL: ${signal['stop_loss']:.6f}
TP1: ${signal['take_profit'][0]:.6f}
TP2: ${signal['take_profit'][1]:.6f}
⚠️ Реальные SL/TP = из Settings!"""
            else:
                signal['analysis_ru'] = f"""⚪ AI: ОЖИДАНИЕ ({symbol_clean})
{response}

📊 СТАТУС:
- Цена: ${current_price:.6f}
- Изменение 24ч: {change_24h:+.1f}%
- RSI(14) на 15m: {rsi_15m:.1f}
- Уверенность: {signal['confidence']:.0f}%"""

            signal.update({
                'symbol': symbol,
                'entry_price': current_price,
                'change_24h': change_24h,
                'timestamp': datetime.utcnow().isoformat(),
                'atr': atr,
                'atr_percent': atr_percent,
                'provider': 'deepseek',
                'response_time': response_time,
                'rsi_15m': rsi_15m,
                'btc_trend': btc_trend
            })
            
            return signal
        except Exception as e:
            logger.error(f"[DeepSeek] Analysis error for {symbol}: {e}", exc_info=True)
            return None

# ============================================================================
# GROQ AI ENGINE
# ============================================================================
class GroqEngine(BaseAIEngine):
    """AI движок на Groq API - использует requests для совместимости с Python 3.8"""
    GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
    
    def __init__(self, api_key: str, model: str = "llama-3.1-8b-instant", max_retries: int = 3):
        super().__init__(api_key, "groq")
        self.model = model
        self.max_retries = max_retries
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        logger.info(f"[Groq] Initialized with model: {model} (using requests)")

    def test_connection(self) -> bool:
        """Тест подключения к Groq API"""
        try:
            logger.info(f"[Groq] Testing connection with model: {self.model}")
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hi."}
                ],
                "max_tokens": 10,
                "temperature": 0.1
            }
            response = requests.post(
                self.GROQ_API_URL,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('choices') and data['choices'][0].get('message', {}).get('content'):
                    logger.info("[Groq] Connection test: SUCCESS")
                    return True
                logger.warning("[Groq] Connection test: Empty response")
                return False
            else:
                error_msg = response.text[:200]
                logger.error(f"[Groq] Connection test FAILED: {response.status_code} - {error_msg}")
                if response.status_code == 401:
                    logger.error("[Groq] AUTH ERROR - invalid API key!")
                elif response.status_code == 404:
                    logger.error(f"[Groq] MODEL ERROR - model '{self.model}' not found!")
                elif response.status_code == 429:
                    logger.error("[Groq] RATE LIMIT exceeded!")
                return False
        except requests.exceptions.Timeout:
            logger.error("[Groq] Connection test: TIMEOUT")
            return False
        except Exception as e:
            logger.error(f"[Groq] Connection test: FAILED - {str(e)}")
            return False

    def _call_api(self, prompt: str, system_prompt: str = None) -> Tuple[Optional[str], float]:
        """Вызов Groq API через requests"""
        if not system_prompt:
            system_prompt = self._get_system_prompt()
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 1500,
            "top_p": 0.95
        }
        
        start_time = time.time()
        for attempt in range(self.max_retries):
            try:
                self.stats['total_requests'] += 1
                response = requests.post(
                    self.GROQ_API_URL,
                    headers=self.headers,
                    json=payload,
                    timeout=60
                )
                elapsed_time = time.time() - start_time
                self.stats['last_request_time'] = datetime.utcnow()
                self.stats['avg_response_time'] = (
                    self.stats['avg_response_time'] * 0.8 + elapsed_time * 0.2
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # Учитываем токены
                    if data.get('usage'):
                        self.stats['total_tokens_used'] += data['usage'].get('total_tokens', 0)
                    
                    result = data['choices'][0]['message']['content']
                    self.stats['successful_requests'] += 1
                    logger.info(f"[Groq] Request success ({elapsed_time:.2f}s)")
                    return result, elapsed_time
                
                else:
                    # Обработка ошибок
                    error_msg = response.text[:200]
                    self.stats['failed_requests'] += 1
                    self.stats['last_error'] = f"HTTP {response.status_code}: {error_msg[:100]}"
                    
                    if response.status_code == 429:
                        logger.warning(f"[Groq] Rate limit, attempt {attempt+1}")
                        self.stats['last_error'] = "Rate limit - слишком много запросов"
                        delay = 5 + attempt * 2
                        time.sleep(delay)
                        continue
                    elif response.status_code == 401:
                        logger.error("[Groq] Auth error - check API key!")
                        self.stats['last_error'] = "Auth error - неверный API ключ"
                        return None, elapsed_time
                    elif response.status_code == 404:
                        logger.error(f"[Groq] Model '{self.model}' not found!")
                        self.stats['last_error'] = f"Model '{self.model}' не найдена"
                        return None, elapsed_time
                    else:
                        logger.warning(f"[Groq] Error {response.status_code}: {error_msg}")
            
            except requests.exceptions.Timeout:
                self.stats['failed_requests'] += 1
                self.stats['last_error'] = "Timeout - сервер не отвечает"
                logger.warning(f"[Groq] Timeout, attempt {attempt+1}")
            
            except Exception as e:
                self.stats['failed_requests'] += 1
                error_str = str(e)
                self.stats['last_error'] = error_str[:200]
                logger.warning(f"[Groq] Attempt {attempt+1} failed: {error_str[:150]}")
                
                if attempt < self.max_retries - 1:
                    delay = 2 ** attempt + random.uniform(0, 1)
                    time.sleep(delay)
        
        logger.error(f"[Groq] All {self.max_retries} attempts failed")
        return None, time.time() - start_time

    def analyze_coin(self, symbol: str, ohlcv: Dict, change_24h: float, btc_trend: Dict = None) -> Optional[Dict]:
        logger.info(f"[Groq] Analyzing {symbol} ({change_24h:+.1f}%)...")
        try:
            market_data_str = self._prepare_market_data(symbol, ohlcv, change_24h)
            if not market_data_str:
                return None
            
            data_15m = ohlcv.get('15m', {})
            data_1h = ohlcv.get('1h', {})
            closes_15m = data_15m.get('close', [])
            closes_1h = data_1h.get('close', []) if data_1h else closes_15m
            
            current_price = closes_15m[-1] if closes_15m else 0
            if current_price <= 0:
                return None
            
            atr = self._calculate_atr(
                data_15m.get('high', []),
                data_15m.get('low', []),
                closes_15m
            )
            
            # Рассчитываем RSI(14) на 15m
            if len(closes_15m) >= 15:
                rsi_15m = calculate_rsi(closes_15m[-50:])
            else:
                rsi_15m = 50.0
            
            # Рассчитываем Bollinger и MACD для пост-мортем анализа
            bollinger = calculate_bollinger_bands(closes_15m, period=20, std_dev=2.0)
            macd = calculate_macd(closes_15m, fast=12, slow=26, signal=9)
            
            prompt = self._create_analysis_prompt(market_data_str, atr, current_price, rsi_15m, symbol, change_24h, btc_trend, self.trading_style)
            response, response_time = self._call_api(prompt)
            
            if not response:
                return self._create_default_signal(current_price)
            
            signal = self._parse_ai_response(response, current_price, atr)
            
            # Добавляем индикаторы для пост-мортем анализа
            signal['rsi_15m'] = rsi_15m
            signal['bollinger_b'] = bollinger.get('percent_b', 50.0)
            signal['macd_histogram'] = macd.get('histogram', 0.0)
            signal['macd_divergence'] = macd.get('divergence', 'none')
            
            symbol_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
            atr_percent = (atr / current_price * 100) if current_price > 0 else 0
            
            # ═══ БЛОКИРОВКА: Невалидные данные ═══
            # RSI=100 означает что все свечи были зелёными (нет данных)
            # ATR<0.1% означает что свечи не загрузились
            if rsi_15m >= 99.9 or atr_percent < 0.1:
                logger.warning(f"[Groq] ❌ SKIP {symbol_clean}: невалидные данные (RSI={rsi_15m:.1f}, ATR={atr_percent:.2f}%)")
                return None
            
            # Генерируем анализ на русском (v5.6 - исправлена кодировка)
            if signal['action'] in ['SHORT', 'LONG']:
                price_info = f"Цена: ${current_price:.6f}"
                if signal['action'] == 'SHORT':
                    analysis_header = f"🔴 AI СИГНАЛ SHORT: {symbol_clean} [Groq]"
                    entry_info = f"Вход: ${current_price:.6f} | Рост 24ч: +{change_24h:.1f}%"
                    direction_emoji = "🔻"
                else:
                    analysis_header = f"🟢 AI СИГНАЛ LONG: {symbol_clean} [Groq]"
                    entry_info = f"Вход: ${current_price:.6f} | Падение 24ч: {change_24h:.1f}%"
                    direction_emoji = "🔺"
                
                signal['analysis_ru'] = f"""{analysis_header}
{direction_emoji} НАПРАВЛЕНИЕ: {signal['action']}

📊 АНАЛИЗ:
{response}

🎯 ПАРАМЕТРЫ:
- {price_info}
- {entry_info}
- RSI(14) на 15m: {rsi_15m:.1f}
- ATR: {atr_percent:.2f}%
- Уверенность: {signal['confidence']:.0f}%

⚡ УРОВНИ (AI рекомендует):
SL: ${signal['stop_loss']:.6f}
TP1: ${signal['take_profit'][0]:.6f}
TP2: ${signal['take_profit'][1]:.6f}
⚠️ Реальные SL/TP = из Settings!"""
            else:
                signal['analysis_ru'] = f"""⚪ AI: ОЖИДАНИЕ ({symbol_clean}) [Groq]
{response}

📊 СТАТУС:
- Цена: ${current_price:.6f}
- Изменение 24ч: {change_24h:+.1f}%
- RSI(14) на 15m: {rsi_15m:.1f}
- Уверенность: {signal['confidence']:.0f}%"""

            signal.update({
                'symbol': symbol,
                'entry_price': current_price,
                'change_24h': change_24h,
                'timestamp': datetime.utcnow().isoformat(),
                'atr': atr,
                'atr_percent': atr_percent,
                'provider': 'groq',
                'response_time': response_time,
                'rsi_15m': rsi_15m,
                'btc_trend': btc_trend
            })
            
            return signal
        except Exception as e:
            logger.error(f"[Groq] Analysis error for {symbol}: {e}", exc_info=True)
            return None

# ============================================================================
# MOCK AI ENGINE (для тестирования)
# ============================================================================
class MockAIEngine(BaseAIEngine):
    """Алгоритмический движок — RSI + Bollinger + MACD без AI"""
    def __init__(self):
        super().__init__("mock", "mock")
        self.stats['mock_mode'] = True
        logger.info("[ALGO] Algorithmic engine initialized (RSI + BB + MACD)")

    def test_connection(self) -> bool:
        return True

    def _calc_bollinger(self, closes, period=20, std_mult=2.0):
        """Bollinger Bands → возвращает (upper, middle, lower, pct_b)"""
        if len(closes) < period:
            return None, None, None, 50.0
        window = closes[-period:]
        middle = sum(window) / period
        variance = sum((x - middle) ** 2 for x in window) / period
        std = variance ** 0.5
        upper = middle + std_mult * std
        lower = middle - std_mult * std
        price = closes[-1]
        pct_b = ((price - lower) / (upper - lower) * 100) if (upper - lower) > 0 else 50.0
        return upper, middle, lower, pct_b

    def _calc_macd(self, closes):
        """MACD → возвращает (macd_line, signal_line, histogram)"""
        if len(closes) < 35:
            return 0, 0, 0
        def ema_series(data, period):
            result = [data[0]]
            k = 2 / (period + 1)
            for i in range(1, len(data)):
                result.append(data[i] * k + result[-1] * (1 - k))
            return result
        ema12 = ema_series(closes, 12)
        ema26 = ema_series(closes, 26)
        macd_line = [ema12[i] - ema26[i] for i in range(len(closes))]
        signal_line = ema_series(macd_line, 9)
        histogram = macd_line[-1] - signal_line[-1]
        return macd_line[-1], signal_line[-1], histogram

    def _calc_atr(self, highs, lows, closes, period=14):
        """ATR из OHLC данных"""
        if len(closes) < period + 1:
            return 0
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
        atr = sum(trs[-period:]) / period
        return atr

    def analyze_coin(self, symbol: str, ohlcv: Dict, change_24h: float, btc_trend: Dict = None) -> Optional[Dict]:
        self.stats['total_requests'] += 1
        self.stats['successful_requests'] += 1

        try:
            data_15m = ohlcv.get('15m', {}) if ohlcv else {}
            closes = data_15m.get('close', []) if data_15m else []
            highs = data_15m.get('high', []) if data_15m else []
            lows = data_15m.get('low', []) if data_15m else []

            if not closes or len(closes) < 20:
                return None

            current_price = closes[-1]
            symbol_clean = symbol.replace('/USDT:USDT', '').replace('/USDT', '')

            # ═══════════════════════════════════════════
            # ИНДИКАТОРЫ
            # ═══════════════════════════════════════════
            rsi = calculate_rsi(closes[-50:]) if len(closes) >= 15 else 50.0
            upper, middle, lower, bb_pct = self._calc_bollinger(closes)
            macd_val, signal_val, macd_hist = self._calc_macd(closes)
            atr = self._calc_atr(highs, lows, closes) if highs and lows else current_price * 0.02
            atr_pct = (atr / current_price * 100) if current_price > 0 else 2.0

            # Объём — используем предпоследнюю свечу (последняя ещё не завершена)
            volumes = data_15m.get('volume', []) if data_15m else []
            vol_ratio = 1.0
            if volumes and len(volumes) >= 12:
                # volumes[-1] = текущая незавершённая, volumes[-2] = последняя завершённая
                avg_vol = sum(volumes[-22:-2]) / max(len(volumes[-22:-2]), 1)
                vol_ratio = volumes[-2] / avg_vol if avg_vol > 0 else 1.0

            # ═══════════════════════════════════════════
            # ENTRY FILTERS + STRATEGY CONFIG
            # ═══════════════════════════════════════════
            full_cfg = {}
            entry_filter_cfg = {}
            strategy_cfg = {}
            try:
                with open('config.json', 'r') as f:
                    full_cfg = json.load(f)
                entry_filter_cfg = full_cfg.get('entry_filters', {})
                strategy_cfg = full_cfg.get('strategy', {})
            except Exception:
                pass

            strategy_type = strategy_cfg.get('type', 'vol_momentum')
            min_rvol = strategy_cfg.get('min_rvol', 2.0)
            min_move_pct = strategy_cfg.get('min_move_pct', 0.5)
            lookback = strategy_cfg.get('lookback_candles', 3)
            strategy_side_filter = strategy_cfg.get('side_filter', 'any')

            # --- PARABOLIC FILTER ---
            is_parabolic = False
            parabolic_ratio = 0.0
            if entry_filter_cfg.get('parabolic_enabled', True) and highs and lows and len(highs) >= 16:
                parabolic_mult = entry_filter_cfg.get('parabolic_multiplier', 3.0)
                ranges = [highs[i] - lows[i] for i in range(len(highs))]
                current_range = ranges[-1]
                avg_range = sum(ranges[-16:-1]) / max(len(ranges[-16:-1]), 1)
                if avg_range > 0:
                    parabolic_ratio = current_range / avg_range
                    if parabolic_ratio > parabolic_mult:
                        is_parabolic = True
                        logger.info(f"[ALGO] {symbol_clean}: PARABOLIC FILTER — свеча {parabolic_ratio:.1f}x avg (>{parabolic_mult:.1f}x), пропуск")

            # --- MULTI-TF TREND FILTER ---
            trend_1h = None
            if entry_filter_cfg.get('multi_tf_enabled', True):
                data_1h = ohlcv.get('1h', {}) if ohlcv else {}
                closes_1h = data_1h.get('close', []) if data_1h else []
                ema_period = entry_filter_cfg.get('multi_tf_ema_period', 20)
                if closes_1h and len(closes_1h) >= ema_period:
                    ema = closes_1h[0]
                    k = 2 / (ema_period + 1)
                    for c in closes_1h[1:]:
                        ema = c * k + ema * (1 - k)
                    price_1h = closes_1h[-1]
                    ema_diff_pct = (price_1h - ema) / ema * 100 if ema > 0 else 0
                    if ema_diff_pct > 0.3:
                        trend_1h = "UP"
                    elif ema_diff_pct < -0.3:
                        trend_1h = "DOWN"

            # ═══════════════════════════════════════════
            # STRATEGY: VOL_MOMENTUM или SCORING (fallback)
            # ═══════════════════════════════════════════
            score = 0
            signals = []
            action = "WAIT"
            confidence = 50.0

            if strategy_type == 'vol_momentum':
                # ── VOL_MOMENTUM: volume spike + trend continuation ──
                # 1. Check RVOL
                rvol_ok = vol_ratio >= min_rvol
                if not rvol_ok:
                    signals.append(f"RVOL={vol_ratio:.1f}x < {min_rvol}x — нет спайка")
                else:
                    signals.append(f"RVOL={vol_ratio:.1f}x ✅ (мин {min_rvol}x)")

                    # 2. Direction: close[now] vs close[now - lookback]
                    if len(closes) > lookback:
                        move_pct = (closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback] * 100
                    else:
                        move_pct = 0.0

                    if abs(move_pct) < min_move_pct:
                        signals.append(f"Move={move_pct:+.2f}% < {min_move_pct}% — слабое движение")
                    else:
                        # 3. Momentum: follow the direction
                        if move_pct > 0:
                            action = "LONG"
                            signals.append(f"Move=+{move_pct:.2f}% → LONG (momentum)")
                        else:
                            action = "SHORT"
                            signals.append(f"Move={move_pct:.2f}% → SHORT (momentum)")
                        confidence = min(60 + vol_ratio * 5, 95)

                    # Side filter from strategy config
                    if action != "WAIT" and strategy_side_filter != 'any':
                        if strategy_side_filter == 'short_only' and action == 'LONG':
                            action = "WAIT"
                            signals.append("⛔ Side filter: short_only")
                        elif strategy_side_filter == 'long_only' and action == 'SHORT':
                            action = "WAIT"
                            signals.append("⛔ Side filter: long_only")
            else:
                # ── SCORING (legacy mean-reversion) ──
                # RSI (вес 3)
                if rsi >= 75:
                    score += 3
                    signals.append(f"RSI={rsi:.0f} сильно перекуплен")
                elif rsi >= 70:
                    score += 2
                    signals.append(f"RSI={rsi:.0f} перекуплен")
                elif rsi <= 25:
                    score -= 3
                    signals.append(f"RSI={rsi:.0f} сильно перепродан")
                elif rsi <= 30:
                    score -= 2
                    signals.append(f"RSI={rsi:.0f} перепродан")

                # BB %B (вес 2)
                if bb_pct is not None:
                    if bb_pct >= 95:
                        score += 2
                    elif bb_pct >= 80:
                        score += 1
                    elif bb_pct <= 5:
                        score -= 2
                    elif bb_pct <= 20:
                        score -= 1

                # MACD (вес 1)
                if macd_hist != 0:
                    if macd_hist < 0 and score > 0:
                        score += 1
                    elif macd_hist > 0 and score < 0:
                        score -= 1

                # 24h change (вес 1)
                if change_24h >= 8:
                    score += 1
                elif change_24h <= -8:
                    score -= 1

                if score >= 3:
                    action = "SHORT"
                    confidence = min(55 + score * 5, 95)
                elif score <= -3:
                    action = "LONG"
                    confidence = min(55 + abs(score) * 5, 95)

            # ═══════════════════════════════════════════
            # ENTRY FILTERS — отклоняем сигнал если фильтры не прошли
            # ═══════════════════════════════════════════
            filter_reject_reason = None
            if action != "WAIT":
                # 1. Parabolic filter
                if is_parabolic:
                    filter_reject_reason = f"PARABOLIC: свеча {parabolic_ratio:.1f}x средней"
                # 2. Multi-TF — против тренда 1h
                elif trend_1h is not None:
                    if action == "LONG" and trend_1h == "DOWN":
                        filter_reject_reason = f"TREND 1H: DOWN — LONG против тренда"
                    elif action == "SHORT" and trend_1h == "UP":
                        filter_reject_reason = f"TREND 1H: UP — SHORT против тренда"

            if filter_reject_reason:
                logger.info(f"[ALGO] {symbol_clean}: {action} ОТКЛОНЁН → {filter_reject_reason}")
                action = "WAIT"
                confidence = 50.0
                signals.append(f"⛔ {filter_reject_reason}")

            # ═══════════════════════════════════════════
            # SL/TP из настроек
            # ═══════════════════════════════════════════
            sl_pct = 0.05
            tp_pct = 0.07
            try:
                from app import state as app_state
                if app_state.trader and app_state.trader.settings:
                    sl_pct = app_state.trader.settings.stop_loss_pct / 100
                    tp_pct = app_state.trader.settings.take_profit_pct / 100
            except Exception:
                try:
                    with open('config.json', 'r') as f:
                        cfg = json.load(f)
                    t = cfg.get('trading', {})
                    sl_pct = t.get('stop_loss_pct', 5.0) / 100
                    tp_pct = t.get('take_profit_pct', 7.0) / 100
                except Exception:
                    pass

            if action == "SHORT":
                stop_loss = current_price * (1 + sl_pct)
                tp1 = current_price * (1 - tp_pct * 0.5)
                tp2 = current_price * (1 - tp_pct)
            elif action == "LONG":
                stop_loss = current_price * (1 - sl_pct)
                tp1 = current_price * (1 + tp_pct * 0.5)
                tp2 = current_price * (1 + tp_pct)
            else:
                stop_loss = current_price * 1.03
                tp1 = current_price * 0.97
                tp2 = current_price * 0.94

            # ═══════════════════════════════════════════
            # ФОРМИРОВАНИЕ ОТВЕТА
            # ═══════════════════════════════════════════
            btc_info = ""
            if btc_trend:
                t = btc_trend.get('trend', 'neutral')
                s = btc_trend.get('strength', '')
                btc_info = f" | BTC: {t} ({s})"

            # Строка фильтров для отображения
            filter_status = f"Parab={'⛔' if is_parabolic else '✅'}{parabolic_ratio:.1f}x | Trend1h={'⛔' if filter_reject_reason and 'TREND' in filter_reject_reason else '✅'}{trend_1h or '—'}"

            if action != "WAIT":
                emoji = "🔴" if action == "SHORT" else "🟢"
                signal_list = "\n".join(f"  • {s}" for s in signals)
                if strategy_type == 'vol_momentum':
                    move_pct_val = 0.0
                    if len(closes) > lookback:
                        move_pct_val = (closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback] * 100
                    analysis_ru = f"""{emoji} VOL_MOMENTUM {action}: {symbol_clean}{btc_info}
RVOL: {vol_ratio:.1f}x | Move: {move_pct_val:+.2f}% | Conf: {confidence:.0f}%
📊 Индикаторы:
  RSI={rsi:.1f} | BB%B={bb_pct:.0f} | ATR={atr_pct:.2f}% | 24h={change_24h:+.1f}%
🔒 Фильтры: {filter_status}
📋 Сигналы:
{signal_list}"""
                else:
                    analysis_ru = f"""{emoji} SCORING {action}: {symbol_clean}{btc_info}
Score: {score:+d}/7 | Confidence: {confidence:.0f}%
📊 RSI={rsi:.1f} | BB%B={bb_pct:.0f} | MACD={'+'if macd_hist>0 else ''}{macd_hist:.6f}
  ATR={atr_pct:.2f}% | Vol={vol_ratio:.1f}x | 24h={change_24h:+.1f}%
🔒 Фильтры: {filter_status}
📋 Сигналы:
{signal_list}"""
            else:
                reject_info = f"\n  ⛔ {filter_reject_reason}" if filter_reject_reason else ""
                analysis_ru = f"""⚪ ALGO WAIT: {symbol_clean}{btc_info}
Strategy: {strategy_type} | RVOL={vol_ratio:.1f}x | RSI={rsi:.1f}
  🔒 {filter_status}{reject_info}"""

            logger.info(f"[ALGO] {symbol_clean}: {action} strategy={strategy_type} RVOL={vol_ratio:.1f}x RSI={rsi:.0f}")

            return {
                'symbol': symbol,
                'action': action,
                'confidence': confidence,
                'entry_price': current_price,
                'stop_loss': stop_loss,
                'take_profit': [tp1, tp2],
                'change_24h': change_24h,
                'timestamp': datetime.utcnow().isoformat(),
                'reason': f'{strategy_type} RVOL={vol_ratio:.1f}x score={score:+d} RSI={rsi:.0f}',
                'analysis_ru': analysis_ru,
                'analysis_raw': f'strategy={strategy_type} rvol={vol_ratio:.1f} score={score} rsi={rsi:.1f}',
                'atr': atr,
                'atr_percent': atr_pct,
                'rsi_15m': rsi,
                'indicators': {
                    'rsi': rsi, 'bb_pct': bb_pct, 'bb_upper': upper, 'bb_lower': lower,
                    'macd': macd_val, 'macd_signal': signal_val, 'macd_hist': macd_hist,
                    'atr': atr, 'atr_pct': atr_pct, 'vol_ratio': vol_ratio,
                    'parabolic_ratio': parabolic_ratio, 'is_parabolic': is_parabolic,
                    'rvol_ok': rvol_ok, 'trend_1h': trend_1h,
                    'filter_reject': filter_reject_reason
                },
                'provider': 'algo',
                'response_time': 0.001,
                'mock': False,
                'direction': action,
                'btc_trend': btc_trend
            }
        except Exception as e:
            logger.error(f"[ALGO] Error analyzing {symbol}: {e}")
            return None

# ============================================================================
# MULTI AI ENGINE (A/B тестирование)
# ============================================================================
class MultiAIEngine:
    """Мульти-AI движок для A/B тестирования и переключения провайдеров"""
    def __init__(self):
        self.engines: Dict[str, BaseAIEngine] = {}
        self.active_provider = None
        self.ab_mode = False
        self.consensus_required = False
        logger.info("[MultiAI] Engine initialized")

    def add_engine(self, name: str, engine: BaseAIEngine):
        """Добавить AI движок"""
        self.engines[name] = engine
        logger.info(f"[MultiAI] Added engine: {name}")
        if self.active_provider is None:
            self.active_provider = name

    def set_active_provider(self, name: str) -> bool:
        """Установить активного провайдера"""
        if name in self.engines:
            self.active_provider = name
            logger.info(f"[MultiAI] Active provider: {name}")
            return True
        return False

    def enable_ab_mode(self, enabled: bool = True, consensus: bool = False):
        """Включить/выключить A/B режим"""
        self.ab_mode = enabled
        self.consensus_required = consensus
        logger.info(f"[MultiAI] A/B mode: {enabled}, consensus: {consensus}")

    def test_connections(self) -> Dict[str, bool]:
        """Тестировать все подключения"""
        results = {}
        for name, engine in self.engines.items():
            results[name] = engine.test_connection()
        return results

    def get_stats(self) -> Dict:
        """Получить статистику всех движков"""
        return {
            name: engine.get_stats()
            for name, engine in self.engines.items()
        }

    def set_learning_context(self, postmortems: List[Dict], stats_context: str = "", btc_trend_context: str = ""):
        """Передать контекст обучения всем движкам"""
        for name, engine in self.engines.items():
            if hasattr(engine, 'set_learning_context'):
                engine.set_learning_context(postmortems, stats_context, btc_trend_context)

    def analyze_coin(self, symbol: str, ohlcv: Dict, change_24h: float, btc_trend: Dict = None) -> Optional[Dict]:
        """
        Анализ монеты с поддержкой A/B тестирования и учета тренда BTC
        """
        if not self.engines:
            logger.error("[MultiAI] No engines configured")
            return None
        
        # Режим A/B тестирования
        if self.ab_mode and len(self.engines) >= 2:
            return self._analyze_ab_mode(symbol, ohlcv, change_24h, btc_trend)
        
        # Обычный режим - используем активного провайдера
        if self.active_provider and self.active_provider in self.engines:
            engine = self.engines[self.active_provider]
            signal = engine.analyze_coin(symbol, ohlcv, change_24h, btc_trend)
            if signal:
                signal['chosen_provider'] = self.active_provider
                return signal
        
        # Fallback на другие движки
        for name, engine in self.engines.items():
            if name != self.active_provider and name != 'mock':
                signal = engine.analyze_coin(symbol, ohlcv, change_24h, btc_trend)
                if signal:
                    signal['chosen_provider'] = name
                    return signal
        
        # Последний fallback - mock
        if 'mock' in self.engines:
            signal = self.engines['mock'].analyze_coin(symbol, ohlcv, change_24h, btc_trend)
            if signal:
                signal['chosen_provider'] = 'mock'
                return signal
        
        return None

    def _analyze_ab_mode(self, symbol: str, ohlcv: Dict, change_24h: float, btc_trend: Dict = None) -> Optional[Dict]:
        """A/B анализ с обоими провайдерами"""
        results = {}
        
        # Запускаем анализ на всех движках
        for name, engine in self.engines.items():
            try:
                signal = engine.analyze_coin(symbol, ohlcv, change_24h, btc_trend)
                if signal:
                    results[name] = signal
            except Exception as e:
                logger.error(f"[MultiAI] {name} error: {e}")
        
        if not results:
            return None
        
        # Собираем данные для A/B теста
        ab_data = {
            'symbol': symbol,
            'consensus': False,
            'chosen_provider': None,
            'trade_opened': False
        }
        
        # Анализируем консенсус
        actions = {name: sig.get('action') for name, sig in results.items()}
        confidences = {name: sig.get('confidence', 0) for name, sig in results.items()}
        
        # Проверяем согласие (должны быть одинаковые действия и оба должны быть не WAIT)
        unique_actions = set(actions.values())
        all_short_or_long = all(action in ['SHORT', 'LONG'] for action in actions.values())
        ab_data['consensus'] = len(unique_actions) == 1 and all_short_or_long
        
        # Добавляем данные по провайдерам
        for name in ['deepseek', 'groq']:
            if name in results:
                ab_data[f'{name}_action'] = results[name].get('action')
                ab_data[f'{name}_confidence'] = results[name].get('confidence')
                ab_data[f'{name}_response_time'] = results[name].get('response_time')
                ab_data[f'{name}_rsi'] = results[name].get('rsi_15m', 0)
        
        # Выбираем финальный сигнал
        final_signal = None
        
        if self.consensus_required:
            # Требуется консенсус
            if ab_data['consensus']:
                # Берём сигнал с большей уверенностью
                best_provider = max(confidences, key=confidences.get)
                final_signal = results[best_provider]
                ab_data['chosen_provider'] = best_provider
            else:
                # Нет консенсуса - не открываем сделку
                logger.info(f"[MultiAI] No consensus for {symbol}: {actions}")
                # Возвращаем WAIT сигнал
                if self.active_provider in results:
                    final_signal = results[self.active_provider]
                else:
                    final_signal = list(results.values())[0]
                
                final_signal['action'] = 'WAIT'
                final_signal['reason'] = 'No AI consensus'
                ab_data['chosen_provider'] = 'none'
        else:
            # Консенсус не требуется - выбираем лучшего по confidence
            best_provider = max(confidences, key=confidences.get)
            final_signal = results[best_provider]
            ab_data['chosen_provider'] = best_provider
        
        if final_signal:
            final_signal['ab_test_data'] = ab_data
            final_signal['all_providers_results'] = {
                name: {
                    'action': sig.get('action'),
                    'confidence': sig.get('confidence'),
                    'response_time': sig.get('response_time'),
                    'rsi_15m': sig.get('rsi_15m', 0)
                }
                for name, sig in results.items()
            }
        
        return final_signal

# ============================================================================
# FACTORY FUNCTION
# ============================================================================
def create_ai_engine(deepseek_key: str = None, groq_key: str = None,
                    mode: str = 'auto') -> MultiAIEngine:
    """
    Фабрика для создания AI движка
    Args:
        deepseek_key: API ключ DeepSeek
        groq_key: API ключ Groq
        mode: 'mock', 'deepseek', 'groq', 'ab', 'auto'
    Returns:
        MultiAIEngine
    """
    multi_engine = MultiAIEngine()
    logger.info(f"[AI Factory] Creating engine, mode={mode}")
    logger.info(f"[AI Factory] DeepSeek key: {'YES (' + deepseek_key[:10] + '...)' if deepseek_key else 'NO'}")
    logger.info(f"[AI Factory] Groq key: {'YES (' + groq_key[:10] + '...)' if groq_key else 'NO'}")
    
    # ========== ИСПРАВЛЕНО v5.6: mode='mock' использует ТОЛЬКО MockAIEngine ==========
    if mode == 'mock':
        logger.info("[AI Factory] MODE=MOCK - using MockAIEngine only (ignoring API keys)")
        multi_engine.add_engine('mock', MockAIEngine())
        multi_engine.set_active_provider('mock')
        logger.info(f"[AI Factory] Final engines: {list(multi_engine.engines.keys())}")
        return multi_engine
    
    # Пробуем добавить DeepSeek
    if deepseek_key and deepseek_key.startswith('sk-'):
        try:
            logger.info("[AI Factory] Initializing DeepSeek...")
            engine = DeepSeekEngine(deepseek_key)
            logger.info("[AI Factory] Testing DeepSeek connection...")
            if engine.test_connection():
                multi_engine.add_engine('deepseek', engine)
                logger.info("[AI Factory] DeepSeek added successfully")
            else:
                logger.warning("[AI Factory] DeepSeek connection test FAILED")
        except Exception as e:
            logger.error(f"[AI Factory] DeepSeek init error: {e}")
    
    # Пробуем добавить Groq
    if groq_key and groq_key.startswith('gsk_'):
        try:
            logger.info("[AI Factory] Initializing Groq...")
            engine = GroqEngine(groq_key)
            logger.info("[AI Factory] Testing Groq connection...")
            if engine.test_connection():
                multi_engine.add_engine('groq', engine)
                logger.info("[AI Factory] Groq added successfully")
            else:
                logger.warning("[AI Factory] Groq connection test FAILED")
        except Exception as e:
            import traceback
            logger.error(f"[AI Factory] Groq init error: {e}")
            logger.error(f"[AI Factory] Groq traceback: {traceback.format_exc()}")
    
    # Если нет реальных движков - добавляем мок
    if not multi_engine.engines:
        logger.warning("[AI Factory] No real engines available, using MockAIEngine")
        multi_engine.add_engine('mock', MockAIEngine())
    
    # Настраиваем режим
    if mode == 'ab' and len(multi_engine.engines) >= 2:
        multi_engine.enable_ab_mode(True, consensus=False)
    elif mode == 'consensus' and len(multi_engine.engines) >= 2:
        multi_engine.enable_ab_mode(True, consensus=True)
    elif mode == 'deepseek' and 'deepseek' in multi_engine.engines:
        multi_engine.set_active_provider('deepseek')
    elif mode == 'groq' and 'groq' in multi_engine.engines:
        multi_engine.set_active_provider('groq')
    
    logger.info(f"[AI Factory] Final engines: {list(multi_engine.engines.keys())}")
    return multi_engine
