# 🚀 RVV Hunter v6.0 - LIVE Trading Ready

## Описание
RVV Hunter - AI-powered криптотрейдинг бот с поддержкой PAPER и LIVE торговли на Binance USDM Futures.

## ⚠️ ВАЖНО: LIVE РЕЖИМ
**LIVE режим использует РЕАЛЬНЫЕ деньги на Binance!**
- Начните с TESTNET для тестирования
- Установите консервативные лимиты
- Никогда не рискуйте больше, чем готовы потерять

## Новое в v6.0
- ✅ **LIVE Trading** - Реальная торговля на Binance USDM Futures
- ✅ **LONG/SHORT** - Полная поддержка обоих направлений
- ✅ **Safety Limits** - Лимиты позиций, дневных и недельных убытков
- ✅ **Agent LIVE Tools** - Агент может управлять реальными позициями
- ✅ **Testnet Support** - Безопасное тестирование на тестнете
- ✅ **Binance API UI** - Настройка ключей в интерфейсе

## Установка

```bash
# Создайте виртуальное окружение (рекомендуется)
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Установите зависимости
pip install -r requirements.txt
```

## Запуск

```bash
# Windows
start.bat

# Или напрямую
python app.py
```

Откройте в браузере: **http://127.0.0.1:8083**

## Настройка LIVE Trading

### 1. Получите API ключи Binance
1. Войдите в [Binance](https://www.binance.com)
2. Перейдите в API Management
3. Создайте новый API ключ с правами:
   - ✅ Enable Futures
   - ✅ Enable Spot & Margin Trading (опционально)
   - ⛔ НЕ включайте Withdrawals
4. Сохраните API Key и Secret Key

### 2. Настройте в RVV Hunter
1. Откройте ⚙️ Настройки
2. Прокрутите до секции "🚀 LIVE ТОРГОВЛЯ (BINANCE)"
3. Введите API Key и Secret Key
4. **РЕКОМЕНДУЕТСЯ**: Включите Testnet для тестирования
5. Нажмите "🔌 Тест подключения"
6. При успехе - включите "LIVE ENABLED"

### 3. Лимиты безопасности
- **Макс. позиция**: Максимальный размер одной позиции
- **Дневной лимит убытка**: При достижении - торговля останавливается
- **Мин. ордер Binance**: $5 (рекомендуется $10+)

## Agent Tools для LIVE Trading

```
[TOOL:LIVE_STATUS]              - Статус LIVE торговли
[TOOL:LIVE_TEST]                - Тест подключения
[TOOL:LIVE_BALANCE]             - Баланс Binance
[TOOL:LIVE_ENABLE:true]         - Включить LIVE
[TOOL:LIVE_ENABLE:false]        - Выключить LIVE
[TOOL:LIVE_OPEN:BTC:LONG:20]    - Открыть LONG BTC на $20
[TOOL:LIVE_OPEN:ETH:SHORT:15]   - Открыть SHORT ETH на $15
[TOOL:LIVE_CLOSE:LIVE-123]      - Закрыть позицию
[TOOL:LIVE_CLOSE_ALL]           - Закрыть ВСЕ позиции
```

## Структура файлов

```
RVV_Hunter_v6.0/
├── app.py              # Главный Flask сервер
├── binance_live.py     # LIVE торговля Binance
├── trader.py           # PAPER торговля
├── agent_tools.py      # Инструменты агента
├── ai_engine.py        # AI провайдеры
├── database.py         # SQLite база данных
├── analytics.py        # Аналитика
├── crypto_agent.py     # Crypto Agent v3
├── smart_agent.py      # Smart Agent v5.0
├── agent_brain.py      # Память агента
├── telegram_bot.py     # Telegram уведомления
├── websocket_manager.py # WebSocket цены
├── history_loader.py   # Загрузка истории
├── config.json         # Конфигурация
├── requirements.txt    # Зависимости
├── start.bat           # Запуск (Windows)
└── templates/
    └── index.html      # Web интерфейс
```

## Торговые параметры по умолчанию

| Параметр | Значение | Описание |
|----------|----------|----------|
| Stop Loss | 3.5% | Стоп лосс |
| Take Profit | 7% | Тейк профит |
| Trailing Activation | 1% | Активация трейлинга |
| Trailing Distance | 0.25% | Дистанция трейлинга |
| Position Size (PAPER) | $50 | Размер позиции |
| Position Size (LIVE) | $10 | Размер LIVE позиции |
| Leverage | 5x | Плечо |
| Max Positions | 5 | Макс. одновременных позиций |

## Режимы AI

- **Mock** - Простые RSI сигналы (по умолчанию)
- **DeepSeek** - AI анализ через DeepSeek API
- **Groq** - AI анализ через Groq API

## Безопасность

- 🔒 API ключи хранятся локально
- 🔒 Поддержка изолированной маржи
- 🔒 Настраиваемые лимиты убытков
- 🔒 Emergency Close All функция
- 🔒 Testnet для безопасного тестирования

## Поддержка

При проблемах проверьте:
1. Python 3.9+ установлен
2. Все зависимости установлены
3. API ключи корректны
4. Права API включают Futures

---

**⚠️ ДИСКЛЕЙМЕР**: Торговля криптовалютами связана с высоким риском. Используйте только те средства, которые готовы потерять. Автор не несёт ответственности за финансовые потери.


---

> ⚠️ **Archival experiment (2024–2025).** This is one of a series of personal crypto trading-bot experiments. Active trading has stopped; the code is released as-is for reference and learning. It may not run without adaptation (exchange API changes, missing dependencies, or configuration). **No warranty — never run it against a funded exchange account without a full review.**

## License

MIT — see [LICENSE](LICENSE).
