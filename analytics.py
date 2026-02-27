# -*- coding: utf-8 -*-
"""
RVV Hunter v4.0 - Analytics Engine
Расширенная аналитика, пост-мортем анализ, рекомендации
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from database import db, get_gmt2_time

logger = logging.getLogger(__name__)


# ============================================================================
# ЛИМИТЫ И ПРАВИЛА
# ============================================================================

AUTO_TUNING_LIMITS = {
    'min_change_filter': (5.0, 30.0),
    'confidence_threshold': (70, 95),
    'scan_interval': (120, 600),
    'max_positions': (1, 10),
    'position_size': (100, 2000),
    'trailing_distance_pct': (0.5, 5.0),
    'trailing_activation_pct': (0.3, 3.0),
}

AUTO_TUNING_RULES = {
    'min_sample_size': 20,
    'max_change_percent': 25,
    'cooldown_hours': 12,
    'min_improvement': 5,
}


class AnalyticsEngine:
    """Движок аналитики с пост-мортем"""
    
    def __init__(self):
        self.last_analysis = None
        self.cached_stats = None
        logger.info("[ANALYTICS] Engine initialized")
    
    # =========================================================================
    # ОСНОВНАЯ СТАТИСТИКА
    # =========================================================================
    
    def get_full_statistics(self) -> Dict:
        """Получить полную статистику"""
        return {
            'general': db.get_trade_statistics(),
            'hourly': db.get_hourly_statistics(),
            'daily': db.get_daily_statistics(),
            'by_symbol': db.get_symbol_statistics(),
            'by_confidence': db.get_confidence_statistics(),
            'by_ai_provider': self._get_ai_provider_statistics(),
            'trailing_stats': self._get_trailing_statistics(),
            'market_history': db.get_market_statistics(),
            'market_history_count': db.get_market_history_count(),
            'pending_recommendations': db.get_recommendations_count('PENDING'),
            'pending_post_mortems': len(db.get_pending_post_mortems()),
        }
    
    def _get_ai_provider_statistics(self) -> Dict:
        """Статистика по AI провайдерам"""
        try:
            trades = db.get_trades(limit=500, only_closed=True)
            
            stats = {
                'deepseek': {'total': 0, 'wins': 0, 'pnl': 0.0},
                'groq': {'total': 0, 'wins': 0, 'pnl': 0.0},
                'mock': {'total': 0, 'wins': 0, 'pnl': 0.0},
            }
            
            for t in trades:
                provider = t.get('ai_provider', 'mock').lower()
                if provider not in stats:
                    provider = 'mock'
                
                stats[provider]['total'] += 1
                if t.get('result') == 'WIN':
                    stats[provider]['wins'] += 1
                stats[provider]['pnl'] += t.get('pnl_usdt', 0)
            
            # Рассчитываем win rate
            for provider in stats:
                total = stats[provider]['total']
                if total > 0:
                    stats[provider]['win_rate'] = stats[provider]['wins'] / total * 100
                else:
                    stats[provider]['win_rate'] = 0
            
            return stats
            
        except Exception as e:
            logger.error(f"[ANALYTICS] AI provider stats error: {e}")
            return {}
    
    def _get_trailing_statistics(self) -> Dict:
        """Статистика по трейлинг-стопу"""
        try:
            trades = db.get_trades(limit=500, only_closed=True)
            
            trailing_activated = {'total': 0, 'wins': 0, 'pnl': 0.0}
            trailing_not_activated = {'total': 0, 'wins': 0, 'pnl': 0.0}
            
            for t in trades:
                reason = t.get('close_reason', '')
                pnl = t.get('pnl_usdt', 0)
                is_win = t.get('result') == 'WIN'
                
                if 'TRAILING' in reason.upper():
                    trailing_activated['total'] += 1
                    if is_win:
                        trailing_activated['wins'] += 1
                    trailing_activated['pnl'] += pnl
                else:
                    trailing_not_activated['total'] += 1
                    if is_win:
                        trailing_not_activated['wins'] += 1
                    trailing_not_activated['pnl'] += pnl
            
            # Win rates
            if trailing_activated['total'] > 0:
                trailing_activated['win_rate'] = trailing_activated['wins'] / trailing_activated['total'] * 100
            else:
                trailing_activated['win_rate'] = 0
            
            if trailing_not_activated['total'] > 0:
                trailing_not_activated['win_rate'] = trailing_not_activated['wins'] / trailing_not_activated['total'] * 100
            else:
                trailing_not_activated['win_rate'] = 0
            
            return {
                'trailing_activated': trailing_activated,
                'trailing_not_activated': trailing_not_activated
            }
            
        except Exception as e:
            logger.error(f"[ANALYTICS] Trailing stats error: {e}")
            return {}
    
    # =========================================================================
    # РЕКОМЕНДАЦИИ
    # =========================================================================
    
    def analyze_and_recommend(self, current_settings: Dict) -> List[Dict]:
        """Анализирует данные и генерирует рекомендации"""
        stats = db.get_trade_statistics()
        total_trades = stats.get('total_trades', 0)
        
        if total_trades < AUTO_TUNING_RULES['min_sample_size']:
            logger.info(f"[ANALYTICS] Недостаточно сделок ({total_trades}) для рекомендаций")
            return []
        
        recommendations = []
        
        # 1. Анализ по часам
        hour_rec = self._analyze_hours(current_settings)
        if hour_rec:
            recommendations.append(hour_rec)
        
        # 2. Анализ по confidence
        conf_rec = self._analyze_confidence(current_settings)
        if conf_rec:
            recommendations.append(conf_rec)
        
        # 3. Анализ min_change_filter
        change_rec = self._analyze_change_filter(current_settings)
        if change_rec:
            recommendations.append(change_rec)
        
        # 4. Анализ трейлинг-стопа
        trailing_rec = self._analyze_trailing(current_settings)
        if trailing_rec:
            recommendations.append(trailing_rec)
        
        # 5. Анализ по символам
        symbol_rec = self._analyze_symbols()
        if symbol_rec:
            recommendations.append(symbol_rec)
        
        # 6. Анализ AI провайдеров
        ai_rec = self._analyze_ai_providers(current_settings)
        if ai_rec:
            recommendations.append(ai_rec)
        
        # Сохраняем новые рекомендации
        for rec in recommendations:
            existing = db.get_pending_recommendations()
            if not any(r['parameter'] == rec['parameter'] for r in existing):
                rec_id = db.save_recommendation(rec)
                rec['id'] = rec_id
                logger.info(f"[ANALYTICS] Новая рекомендация: {rec['parameter']} -> {rec['suggested_value']}")
        
        self.last_analysis = get_gmt2_time()
        return recommendations
    
    def _analyze_hours(self, settings: Dict) -> Optional[Dict]:
        """Анализ лучших/худших часов"""
        hourly = db.get_hourly_statistics()
        
        if len(hourly) < 6:
            return None
        
        worst_hours = [h for h in hourly if h['win_rate'] and h['win_rate'] < 40 and h['total'] >= 5]
        
        if worst_hours:
            hours_str = ', '.join([f"{h['hour']}:00" for h in worst_hours])
            avg_wr = sum(h['win_rate'] for h in worst_hours) / len(worst_hours)
            
            general = db.get_trade_statistics()
            general_wr = general.get('win_rate', 50)
            
            if general_wr - avg_wr >= AUTO_TUNING_RULES['min_improvement']:
                return {
                    'parameter': 'avoid_hours',
                    'current_value': None,
                    'suggested_value': [h['hour'] for h in worst_hours],
                    'reasoning': f'Часы {hours_str} убыточны (WR {avg_wr:.0f}% vs {general_wr:.0f}% в среднем)',
                    'sample_size': sum(h['total'] for h in worst_hours),
                    'expected_improvement': general_wr - avg_wr
                }
        
        return None
    
    def _analyze_confidence(self, settings: Dict) -> Optional[Dict]:
        """Анализ порога confidence"""
        by_conf = db.get_confidence_statistics()
        
        if len(by_conf) < 3:
            return None
        
        current_threshold = settings.get('confidence_threshold', 80)
        
        high_conf = [c for c in by_conf if c['confidence_range'] in ['95-100', '90-94']]
        low_conf = [c for c in by_conf if c['confidence_range'] in ['80-84', '<80']]
        
        if high_conf and low_conf:
            high_total = sum(c['total'] for c in high_conf)
            low_total = sum(c['total'] for c in low_conf)
            
            if high_total >= 5 and low_total >= 5:
                high_wr = sum(c['wins'] for c in high_conf) / high_total * 100
                low_wr = sum(c['wins'] for c in low_conf) / low_total * 100
                
                diff = high_wr - low_wr
                
                if diff >= AUTO_TUNING_RULES['min_improvement'] and current_threshold < 90:
                    return {
                        'parameter': 'confidence_threshold',
                        'current_value': current_threshold,
                        'suggested_value': 90,
                        'reasoning': f'При confidence >90% WR {high_wr:.0f}%, при <85% WR {low_wr:.0f}%',
                        'sample_size': high_total + low_total,
                        'expected_improvement': diff
                    }
        
        return None
    
    def _analyze_change_filter(self, settings: Dict) -> Optional[Dict]:
        """Анализ min_change_filter"""
        trades = db.get_trades(limit=300, only_closed=True)
        growing_trades = [t for t in trades if t.get('change_24h', 0) > 0]
        
        if len(growing_trades) < AUTO_TUNING_RULES['min_sample_size']:
            return None
        
        current_filter = settings.get('min_change_filter', 10)
        
        ranges = {
            '10-15': {'wins': 0, 'total': 0},
            '15-25': {'wins': 0, 'total': 0},
            '25-40': {'wins': 0, 'total': 0},
            '40+': {'wins': 0, 'total': 0},
        }
        
        for t in growing_trades:
            change = t.get('change_24h', 0)
            result = t.get('result')
            
            if 10 <= change < 15:
                ranges['10-15']['total'] += 1
                if result == 'WIN':
                    ranges['10-15']['wins'] += 1
            elif 15 <= change < 25:
                ranges['15-25']['total'] += 1
                if result == 'WIN':
                    ranges['15-25']['wins'] += 1
            elif 25 <= change < 40:
                ranges['25-40']['total'] += 1
                if result == 'WIN':
                    ranges['25-40']['wins'] += 1
            elif change >= 40:
                ranges['40+']['total'] += 1
                if result == 'WIN':
                    ranges['40+']['wins'] += 1
        
        total_wins = sum(1 for t in growing_trades if t.get('result') == 'WIN')
        current_wr = total_wins / len(growing_trades) * 100 if growing_trades else 0
        
        best_range = None
        best_wr = 0
        
        for name, data in ranges.items():
            if data['total'] >= 8:
                wr = data['wins'] / data['total'] * 100
                if wr > best_wr:
                    best_wr = wr
                    best_range = name
        
        if best_range and best_wr - current_wr >= AUTO_TUNING_RULES['min_improvement']:
            suggested = {
                '10-15': 10,
                '15-25': 15,
                '25-40': 25,
                '40+': 40,
            }.get(best_range, current_filter)
            
            if suggested != current_filter:
                return {
                    'parameter': 'min_change_filter',
                    'current_value': current_filter,
                    'suggested_value': suggested,
                    'reasoning': f'При росте {best_range}% WR {best_wr:.0f}% (текущий {current_wr:.0f}%)',
                    'sample_size': ranges[best_range]['total'],
                    'expected_improvement': best_wr - current_wr
                }
        
        return None
    
    def _analyze_trailing(self, settings: Dict) -> Optional[Dict]:
        """Анализ параметров трейлинг-стопа"""
        trailing_stats = self._get_trailing_statistics()
        
        activated = trailing_stats.get('trailing_activated', {})
        not_activated = trailing_stats.get('trailing_not_activated', {})
        
        if activated.get('total', 0) < 10 or not_activated.get('total', 0) < 10:
            return None
        
        act_wr = activated.get('win_rate', 0)
        not_act_wr = not_activated.get('win_rate', 0)
        
        current_activation = settings.get('trailing_activation_pct', 0.5)
        current_distance = settings.get('trailing_distance_pct', 1.0)
        
        # Если трейлинг сильно лучше - предлагаем более агрессивную активацию
        if act_wr > not_act_wr + 10:
            if current_activation > 0.5:
                return {
                    'parameter': 'trailing_activation_pct',
                    'current_value': current_activation,
                    'suggested_value': max(0.3, current_activation - 0.2),
                    'reasoning': f'Трейлинг эффективен (WR {act_wr:.0f}% vs {not_act_wr:.0f}%). Раньше активировать.',
                    'sample_size': activated['total'],
                    'expected_improvement': act_wr - not_act_wr
                }
        
        # Если трейлинг хуже - предлагаем увеличить расстояние
        elif not_act_wr > act_wr + 10:
            if current_distance < 2.5:
                return {
                    'parameter': 'trailing_distance_pct',
                    'current_value': current_distance,
                    'suggested_value': min(3.0, current_distance + 0.5),
                    'reasoning': f'Трейлинг слишком узкий. Увеличить расстояние.',
                    'sample_size': not_activated['total'],
                    'expected_improvement': not_act_wr - act_wr
                }
        
        return None
    
    def _analyze_symbols(self) -> Optional[Dict]:
        """Анализ убыточных символов"""
        trades = db.get_trades(limit=300, only_closed=True)
        growing_trades = [t for t in trades if t.get('change_24h', 0) > 0]
        
        symbol_stats = {}
        for t in growing_trades:
            symbol = t['symbol']
            result = t.get('result')
            
            if symbol not in symbol_stats:
                symbol_stats[symbol] = {'wins': 0, 'total': 0}
            
            symbol_stats[symbol]['total'] += 1
            if result == 'WIN':
                symbol_stats[symbol]['wins'] += 1
        
        bad_symbols = []
        for symbol, stats in symbol_stats.items():
            if stats['total'] >= 5:
                win_rate = stats['wins'] / stats['total'] * 100
                if win_rate < 30:
                    bad_symbols.append({
                        'symbol': symbol,
                        'total': stats['total'],
                        'win_rate': win_rate
                    })
        
        if bad_symbols:
            symbols_str = ', '.join([s['symbol'].replace('/USDT:USDT', '') for s in bad_symbols[:5]])
            avg_wr = sum(s['win_rate'] for s in bad_symbols) / len(bad_symbols)
            
            return {
                'parameter': 'blacklist_symbols',
                'current_value': [],
                'suggested_value': [s['symbol'] for s in bad_symbols],
                'reasoning': f'Убыточные пары: {symbols_str} (WR {avg_wr:.0f}%)',
                'sample_size': sum(s['total'] for s in bad_symbols),
                'expected_improvement': 50 - avg_wr
            }
        
        return None
    
    def _analyze_ai_providers(self, settings: Dict) -> Optional[Dict]:
        """Анализ эффективности AI провайдеров"""
        ai_stats = self._get_ai_provider_statistics()
        
        deepseek = ai_stats.get('deepseek', {})
        groq = ai_stats.get('groq', {})
        
        if deepseek.get('total', 0) < 10 or groq.get('total', 0) < 10:
            return None
        
        ds_wr = deepseek.get('win_rate', 0)
        gr_wr = groq.get('win_rate', 0)
        
        current_provider = settings.get('ai_provider', 'deepseek')
        
        diff = abs(ds_wr - gr_wr)
        
        if diff >= 10:
            better_provider = 'deepseek' if ds_wr > gr_wr else 'groq'
            better_wr = max(ds_wr, gr_wr)
            worse_wr = min(ds_wr, gr_wr)
            
            if better_provider != current_provider:
                return {
                    'parameter': 'ai_provider',
                    'current_value': current_provider,
                    'suggested_value': better_provider,
                    'reasoning': f'{better_provider.title()} эффективнее (WR {better_wr:.0f}% vs {worse_wr:.0f}%)',
                    'sample_size': deepseek['total'] + groq['total'],
                    'expected_improvement': diff
                }
        
        return None
    
    # =========================================================================
    # ПОСТ-МОРТЕМ АНАЛИЗ
    # =========================================================================
    
    def generate_post_mortem(self, trade: Dict) -> Dict:
        """
        Генерирует детальный пост-мортем анализ для убыточной сделки
        
        Args:
            trade: Данные закрытой сделки
            
        Returns:
            Пост-мортем с анализом и рекомендациями
        """
        recommendations = []
        analysis_points = []
        
        symbol = trade.get('symbol', '')
        hour_opened = trade.get('hour_opened', 0)
        day_of_week = trade.get('day_of_week', 0)
        atr_percent = trade.get('atr_percent', 0)
        trailing_distance = trade.get('trailing_distance_pct', 1.0)
        change_24h = trade.get('change_24h', 0)
        pnl = trade.get('pnl_usdt', 0)
        close_reason = trade.get('close_reason', '')
        ai_provider = trade.get('ai_provider', 'unknown')
        confidence = trade.get('ai_confidence', 0)
        
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
            
            if ratio < 0.5:
                analysis_points.append(f"📉 Трейлинг ({trailing_distance:.1f}%) слишком узкий относительно ATR ({atr_percent:.1f}%)")
                recommendations.append({
                    'type': 'increase_trailing',
                    'value': atr_percent * 0.7,
                    'reason': f'ATR {atr_percent:.1f}%, трейлинг был {trailing_distance:.1f}%'
                })
            elif ratio > 2.0:
                analysis_points.append(f"📈 Трейлинг ({trailing_distance:.1f}%) слишком широкий относительно ATR ({atr_percent:.1f}%)")
        
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
        
        # Формируем текстовый анализ
        analysis_text = f"""📊 ПОСТ-МОРТЕМ АНАЛИЗ

🔴 Убыток: ${abs(pnl):.2f}
📍 Символ: {symbol.replace('/USDT:USDT', '')}
⏰ Время: {hour_opened}:00 ({days[day_of_week] if day_of_week < 7 else 'N/A'})
🤖 AI: {ai_provider} ({confidence}%)
📈 Изменение 24ч: +{change_24h:.1f}%
🎯 Причина закрытия: {close_reason}

ПРОБЛЕМЫ:
{chr(10).join(['• ' + p for p in analysis_points]) if analysis_points else '• Специфических проблем не выявлено'}

РЕКОМЕНДАЦИИ:
{chr(10).join(['• ' + r['reason'] for r in recommendations]) if recommendations else '• Рекомендаций нет'}
"""
        
        return {
            'trade_id': trade.get('trade_id'),
            'symbol': symbol,
            'loss_amount': abs(pnl),
            'hour_opened': hour_opened,
            'day_of_week': day_of_week,
            'atr_at_entry': atr_percent,
            'trailing_distance_used': trailing_distance,
            'analysis': analysis_text,
            'analysis_points': analysis_points,
            'recommendations': recommendations,
            'created_at': get_gmt2_time().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def apply_post_mortem_action(self, post_mortem_id: int, action: str, settings: Dict) -> Tuple[bool, str, Dict]:
        """
        Применяет рекомендацию из пост-мортема
        
        Args:
            post_mortem_id: ID пост-мортема
            action: Действие (apply_all, dismiss, apply_specific)
            settings: Текущие настройки
            
        Returns:
            (success, message, updated_settings)
        """
        try:
            post_mortems = db.get_pending_post_mortems()
            pm = next((p for p in post_mortems if p['id'] == post_mortem_id), None)
            
            if not pm:
                return False, "Пост-мортем не найден", settings
            
            recommendations = pm.get('recommendations', [])
            if isinstance(recommendations, str):
                import json
                recommendations = json.loads(recommendations)
            
            applied_changes = []
            
            for rec in recommendations:
                rec_type = rec.get('type', '')
                value = rec.get('value')
                
                if rec_type == 'avoid_hour':
                    current_hours = settings.get('avoid_hours', [])
                    if isinstance(current_hours, str):
                        import json
                        current_hours = json.loads(current_hours)
                    if value not in current_hours:
                        current_hours.append(value)
                        settings['avoid_hours'] = current_hours
                        applied_changes.append(f"Добавлен час {value}:00 в avoid_hours")
                
                elif rec_type == 'blacklist_symbol':
                    db.add_to_blacklist(value, f"Post-mortem: {rec.get('reason', '')}", 'POST_MORTEM')
                    applied_changes.append(f"Добавлен {value} в черный список")
                
                elif rec_type == 'increase_trailing':
                    if value and value > settings.get('trailing_distance_pct', 1.0):
                        settings['trailing_distance_pct'] = round(value, 1)
                        applied_changes.append(f"Увеличен trailing_distance до {value:.1f}%")
                
                elif rec_type == 'increase_confidence_threshold':
                    current = settings.get('confidence_threshold', 75)
                    if value > current:
                        settings['confidence_threshold'] = value
                        applied_changes.append(f"Увеличен confidence_threshold до {value}")
            
            # Обновляем статус пост-мортема
            db.update_post_mortem_action(post_mortem_id, 'APPLIED')
            
            if applied_changes:
                return True, f"Применено: {', '.join(applied_changes)}", settings
            else:
                return True, "Нет изменений для применения", settings
                
        except Exception as e:
            logger.error(f"[ANALYTICS] Post-mortem apply error: {e}")
            return False, f"Ошибка: {str(e)}", settings
    
    # =========================================================================
    # AI КОНТЕКСТ
    # =========================================================================
    
    def get_ai_context(self) -> str:
        """Генерирует контекст для AI промпта на основе статистики"""
        stats = db.get_trade_statistics()
        total = stats.get('total_trades', 0)
        
        if total < 10:
            return "Недостаточно данных для статистики (менее 10 сделок)."
        
        hourly = db.get_hourly_statistics()
        market = db.get_market_statistics()
        trailing_stats = self._get_trailing_statistics()
        
        lines = [
            f"ТВОЯ СТАТИСТИКА ({total} сделок):",
            f"• Win Rate: {stats.get('win_rate', 0):.0f}%",
            f"• Средний профит: ${stats.get('avg_win', 0):.2f}",
            f"• Средний убыток: ${stats.get('avg_loss', 0):.2f}",
        ]
        
        # Лучшие/худшие часы
        if hourly:
            good_hours = [h for h in hourly if h['win_rate'] and h['win_rate'] >= 60 and h['total'] >= 3]
            bad_hours = [h for h in hourly if h['win_rate'] and h['win_rate'] < 40 and h['total'] >= 3]
            
            if good_hours:
                hours_str = ', '.join([f"{h['hour']}:00" for h in good_hours[:3]])
                lines.append(f"• Лучшие часы: {hours_str}")
            
            if bad_hours:
                hours_str = ', '.join([f"{h['hour']}:00" for h in bad_hours[:3]])
                lines.append(f"• Худшие часы: {hours_str}")
        
        # Статистика трейлинга
        if trailing_stats:
            activated = trailing_stats.get('trailing_activated', {})
            if activated.get('total', 0) >= 5:
                lines.append(f"• Трейлинг WR: {activated.get('win_rate', 0):.0f}% ({activated['total']} сделок)")
        
        # Рыночная статистика
        if market and market.get('total_pumps', 0) > 50:
            lines.append("")
            lines.append(f"СТАТИСТИКА РЫНКА ({market['total_pumps']} пампов):")
            if market.get('reversal_3pct_rate'):
                lines.append(f"• Откат >3% за 4ч: {market['reversal_3pct_rate']*100:.0f}% случаев")
            if market.get('avg_reversal_4h'):
                lines.append(f"• Средний откат за 4ч: {market['avg_reversal_4h']:.1f}%")
        
        # Текущее время
        now = get_gmt2_time()
        lines.append("")
        lines.append(f"ТЕКУЩЕЕ ВРЕМЯ: {now.strftime('%H:%M')} GMT+2 ({['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][now.weekday()]})")
        
        return '\n'.join(lines)
    
    # =========================================================================
    # ПРИМЕНЕНИЕ РЕКОМЕНДАЦИЙ
    # =========================================================================
    
    def apply_recommendation(self, rec_id: int, settings: Dict, source: str = 'MANUAL') -> Tuple[bool, str, Dict]:
        """Применяет рекомендацию"""
        pending = db.get_pending_recommendations()
        rec = next((r for r in pending if r['id'] == rec_id), None)
        
        if not rec:
            return False, "Рекомендация не найдена", settings
        
        param = rec['parameter']
        new_value = rec['suggested_value']
        current_value = rec['current_value']
        
        # Специальные параметры
        if param in ['avoid_hours', 'blacklist_symbols']:
            settings[param] = new_value
            db.apply_recommendation(rec_id, source)
            db.log_setting_change(param, current_value, new_value, source, rec_id)
            return True, f"Применено: {param}", settings
        
        # Проверяем лимиты
        if param in AUTO_TUNING_LIMITS:
            min_val, max_val = AUTO_TUNING_LIMITS[param]
            new_value = max(min_val, min(max_val, new_value))
        
        # Проверяем % изменения
        if current_value and current_value > 0:
            change_pct = abs(new_value - current_value) / current_value * 100
            if change_pct > AUTO_TUNING_RULES['max_change_percent']:
                direction = 1 if new_value > current_value else -1
                new_value = current_value * (1 + direction * AUTO_TUNING_RULES['max_change_percent'] / 100)
        
        settings[param] = new_value
        db.apply_recommendation(rec_id, source)
        db.log_setting_change(param, current_value, new_value, source, rec_id)
        
        return True, f"Применено: {param} = {new_value}", settings


# Глобальный экземпляр
analytics = AnalyticsEngine()
