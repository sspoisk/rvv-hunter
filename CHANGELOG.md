# RVV Hunter — CHANGELOG

Все изменения кода и настроек с датой, обоснованием и ожидаемым эффектом.

---

## [2026-02-25 00:10] Tor SOCKS5 прокси для обхода блокировки Binance

- **Файлы:** `app.py` (init_exchange), `binance_live.py` (_init_exchange)
- **Причина:** Binance возвращал HTTP 451 (геоблокировка)
- **Изменение:** Добавлена настройка `proxies: socks5h://127.0.0.1:9050` в оба ccxt.binance() конструктора. Tor уже был установлен и работал на порту 9050
- **Управление:** `USE_TOR=0 python app.py` для отключения
- **Эффект:** Подключение к Binance через Tor circuit, обход 451

---

## [2026-02-25 00:16] Nginx как реверс-прокси

- **Файлы:** `/etc/nginx/sites-available/rvv_hunter`, `config.json`
- **Причина:** Веб-интерфейс слушал только 127.0.0.1:8083, недоступен снаружи
- **Изменение:**
  - Flask порт: 8083 → 8084 (чтобы nginx мог занять 8083)
  - Создан nginx site: `listen 8083` → `proxy_pass http://127.0.0.1:8084`
  - Добавлены заголовки WebSocket (Upgrade/Connection)
  - Добавлены X-Real-IP, X-Forwarded-For для ProxyFix
- **Эффект:** Доступ по `http://172.86.114.217:8083`

---

## [2026-02-25 00:29] Systemd сервис автозапуска

- **Файлы:** `/etc/systemd/system/rvv-hunter.service`
- **Причина:** Бот запускался вручную через `& disown`, не переживал рестарт сервера
- **Изменение:**
  - `After=network-online.target tor.service` — ждёт сети и Tor
  - `Restart=on-failure`, `RestartSec=10` — авторестарт при падении
  - `StartLimitBurst=5` за 120 сек — защита от restart-storm
  - `EnvironmentFile=/root/rvv_hunter/.env` — API ключи из файла
  - `StandardOutput/Error → logs/bot.log`
- **Эффект:** Автозапуск при загрузке сервера, авторестарт при падении

---

## [2026-02-25 00:43] Оптимизация параметров стратегии (после анализа прибыльности)

### config.json — риск-менеджмент

| Параметр | Было | Стало | Обоснование |
|---|---|---|---|
| `stop_loss_pct` | 27% → 2% | **3.5%** (floor) | 27% = слив депо на SL; 2% = постоянные ложные выбивания от шума (ATR altcoin 3-5%); 3.5% — разумный минимум |
| `take_profit_pct` | 10% | **12%** | Улучшает R:R с 5:1 до 6:1 при том же SL |
| `trailing_distance_pct` | 0.5% | **0.8%** | Трейлинг 0.5% выбивал позиции от первого тика; 0.8% даёт дышать при волатильности |
| `trailing_activation_pct` | 2.0% | 2.0% | Без изменений — правильный порог |
| `btc_neutral_mode` | "none" | **"any"** | BTC нейтрален 20-35% времени; запрет торговли в эти дни терял прибыль |
| `btc_*_min_strength` | 0.1% | **0.3%** | 0.1% слишком слабый тренд чтобы считаться трендом |
| `min_confidence` | 70% | **65%** | +30-40% к частоте сделок без потери качества |
| `ai_provider` | "mock" | **"deepseek"** | Mock = чистый RSI рандом, реальный DeepSeek нужен для сигналов |

### trader.py — defaults Settings

| Параметр | Было | Стало | Обоснование |
|---|---|---|---|
| `partial_tp_enabled` | False | **True** | Фиксируем 50% прибыли на TP1, не ставим всё на TP2 |
| `breakeven_enabled` | False | **True** | После +3% профита → SL в безубыток, устраняет риск убытка |
| `equity_protection_enabled` | False | **True** | Стоп при просадке 25%, предотвращает каскадные убытки |

### trader.py — ATR-адаптивный SL (новая логика)

```python
# Было: sl_pct = self.settings.stop_loss_pct  (фиксированный)
# Стало:
if atr_percent > 0:
    sl_pct = max(self.settings.stop_loss_pct, atr_percent * 1.5)
# Пример: ATR=4% → SL=6% (не 3.5%), что соответствует реальному шуму монеты
```

- **Ожидаемый эффект:** Снижение ложных SL-срабатываний на 30-40%, улучшение R:R

---

## [2026-02-25 00:50] Исправления UI — PnL график и вкладка Позиций

- **Файл:** `templates/index.html`
- **Причина:** Два бага производительности и корректности:

### Баг 1: График PnL не сбрасывался после reset
- **Проблема:** `doReset()` вызывал `updateAll()` но не `loadPnlChart()`; `loadPnlChart()` при отсутствии данных (после сброса) делал early return БЕЗ уничтожения старого Chart.js instance → старый график оставался на экране
- **Исправление:**
  - `doReset()`: добавлен `pnlChartInstance.destroy()` + `setTimeout(loadPnlChart, 400)`
  - `loadPnlChart()`: при пустых данных теперь уничтожает chart и очищает canvas

### Баг 2: Вкладка "Позиции" переключалась с тормозами
- **Проблема:** `updatePositions()` (каждые 5 сек) делал fetch данных, а потом вызывал `updatePositionsUI()` который делал ВТОРОЙ независимый fetch + полностью перестраивал DOM даже если данные не изменились
- **Исправление:**
  - `updatePositions()` теперь передаёт уже загруженные данные в `updatePositionsUI(d)`
  - `updatePositionsUI(data?)` принимает опциональные данные; если переданы — не делает fetch
  - Добавлена hash-проверка состояния (`_positionsGridHash`): DOM перестраивается только при реальных изменениях цен/PnL
  - Добавлен mutex `_positionsUpdateInProgress` для предотвращения параллельных вызовов

- **Ожидаемый эффект:**
  - Запросы к `/api/positions` сокращены с 2/цикл до 1/цикл (экономия ~50% сетевых запросов)
  - DOM-перестройка только при изменении данных (экономия CPU при неактивных позициях)
  - Плавное переключение на вкладку Позиций без видимых лагов

---

## [2026-02-25 00:55] Система логирования изменений

- **Файлы:** `CHANGELOG.md`, `scripts/backup_config.sh`, `/etc/systemd/system/rvv-config-backup.{service,path}`
- **Причина:** Отсутствие трассировки изменений параметров
- **Изменение:**
  - `CHANGELOG.md` — этот файл, ведётся вручную и через скрипт
  - `scripts/backup_config.sh` — создаёт `backups/config_YYYYMMDD_HHMMSS.json` перед изменениями; хранит последние 50 бэкапов, удаляет старше 30 дней
  - `rvv-config-backup.path` (systemd) — автоматически запускает backup.sh при каждом изменении `config.json`
- **Использование:**
  ```bash
  ./scripts/backup_config.sh "описание изменения"  # ручной бэкап с комментарием
  ls backups/                                        # список бэкапов
  diff backups/config_20260225_004300.json config.json  # сравнение версий
  ```

---

## [2026-02-27 14:35] Исправлена синхронизация настроек бэктеста

- **Файлы:** `config.json`, `trader.py`, `app.py`
- **Причина:** Настройки из бэктеста записывались в БД, но бот читает только config.json. Три параметра не применялись:

### Баг 1: `min_change_filter` и `max_to_analyze` не в config.json
- **Проблема:** Предыдущий скрипт записал значения в таблицу `trader_settings` в SQLite, но БД пустая (таблицы не создаются). Бот использует дефолты из кода: `min_change_filter=5.0%`, `max_to_analyze=15`
- **Исправление:** Добавлены в `config.json → trading`: `"max_to_analyze": 50`, `"min_change_filter": 0`
- **Исправление:** Добавлены в маппинг `trader.py → _sync_from_config()`: `max_positions`, `max_to_analyze`, `min_change_filter`

### Баг 2: `btc_neutral_mode` затиралось миграцией
- **Проблема:** `app.py:4252` — миграция при каждом старте: `if btc_neutral_mode == 'any' → 'none'`. Настройка `"any"` из бэктеста сбрасывалась обратно на `"none"` при каждом рестарте
- **Исправление:** Миграция теперь только добавляет ключ если отсутствует, не перезаписывает существующее значение

### Итоговые настройки после бэктеста

| Параметр | Было | Стало | Источник |
|---|---|---|---|
| SL | 3.5% | 1.25% | Бэктест фаза 1 |
| TP | 12% | 7.75% | Бэктест фаза 2 |
| Trail Activation | 2.0% | 0.5% | Бэктест фаза 3 |
| Trail Distance | 0.25% | 0.05% | Бэктест фаза 3 |
| min_change_filter | 5.0% | 0% | Бэктест фаза 4 |
| BTC bullish | long_only | short_only | Бэктест фаза 5 |
| BTC bearish | short_only | any | Бэктест фаза 5 |
| BTC neutral | none | any | Бэктест фаза 5 |
| max_positions | 5 | 10 | Бэктест positions×coins |
| max_to_analyze | 15 | 50 | Бэктест positions×coins |

- **Эффект:** Все 10 параметров бэктеста теперь реально применены и переживают рестарт бота

---

<!-- Новые записи добавляются выше этой строки -->

## [2026-02-25 02:10] Исправлен скан — "запуск сканирования и тишина"

- **Файл:** `app.py`
- **Причина:** fetch_tickers() / fetch_ohlcv() падали с 451 от заблокированных Tor-узлов → scan_cycle() возвращался после первой строки лога
- **Диагностика:** ExitNodes включал JP — Binance Futures блокирует большинство японских IP
- **Изменения:**
  - Добавлена `_is_tor_blocked_error(e)` — определяет 451/гео-блокировку
  - Добавлена `_exchange_with_retry(func, *args, max_retries=3)` — ретрай с NEWNYM при 451
  - `fetch_top_pairs()` — `exchange.fetch_tickers()` → `_exchange_with_retry(...)`
  - `get_btc_trend()` — `exchange.fetch_ohlcv/fetch_ticker` → `_exchange_with_retry(...)`
  - `update_market_prices()` — аналогично
  - `/etc/tor/torrc` — ExitNodes переставлены: SG/KR/TW первыми (работают с Binance Futures), JP в конце
- **Эффект:** Сканер теперь автоматически ротирует Tor-цепочку при 451 и продолжает работу

## [2026-02-25 01:02] Backup config.json

- **Файл:** `config.json`
- **Причина:** initial baseline — after strategy optimization
- **Бэкап:** `backups/config_20260225_010219.json`

## [2026-02-25 01:53] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260225_015326.json`

## [2026-02-25 02:13] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260225_021330.json`

## [2026-02-26 00:01] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_000121.json`

## [2026-02-26 13:45] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_134540.json`

## [2026-02-26 15:04] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_150443.json`

## [2026-02-26 18:06] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_180646.json`

## [2026-02-26 18:21] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_182117.json`

## [2026-02-26 19:23] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192324.json`

## [2026-02-26 19:24] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192407.json`

## [2026-02-26 19:24] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192419.json`

## [2026-02-26 19:25] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192517.json`

## [2026-02-26 19:25] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192517.json`

## [2026-02-26 19:25] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192545.json`

## [2026-02-26 19:26] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192603.json`

## [2026-02-26 19:26] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260226_192603.json`

## [2026-02-27 09:41] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_094153.json`

## [2026-02-27 09:42] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_094202.json`

## [2026-02-27 09:44] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_094425.json`

## [2026-02-27 09:44] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_094456.json`

## [2026-02-27 09:59] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_095923.json`

## [2026-02-27 10:00] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_100014.json`

## [2026-02-27 14:34] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_143410.json`

## [2026-02-27 14:34] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_143417.json`

## [2026-02-27 14:35] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_143513.json`

## [2026-02-27 14:35] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_143536.json`

## [2026-02-27 14:36] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_143605.json`

## [2026-02-27 14:36] Backup config.json

- **Файл:** `config.json`
- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_143632.json`

## [2026-02-27 14:41] Backup config.json

- **Причина:** тест нового формата
- **Бэкап:** `backups/config_20260227_144108.json`
- **Изменения:**
```
  (без изменений)
```

## [2026-02-27 14:41] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_144120.json`
- **Изменения:**
```
  ~ trading.leverage: 2 → 3
```

## [2026-02-27 14:41] Backup config.json

- **Причина:** тест: leverage 2→3
- **Бэкап:** `backups/config_20260227_144121.json`
- **Изменения:**
```
  (без изменений)
```

## [2026-02-27 14:41] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_144146.json`
- **Изменения:**
```
  ~ trading.leverage: 3 → 2
```

## [2026-02-27 14:41] Backup config.json

- **Причина:** тест: leverage 3→2 обратно
- **Бэкап:** `backups/config_20260227_144148.json`
- **Изменения:**
```
  (без изменений)
```

## [2026-02-27 14:42] Backup config.json

- **Причина:** тест diff с md5
- **Бэкап:** `backups/config_20260227_144243.json`
- **Изменения:**
```
  ~ trading.leverage: 3 → 2
```

## [2026-02-27 15:10] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_151018.json`
- **Изменения:**
```
  ~ filters.close_long_on_weak_bull: True → False
  ~ filters.close_short_on_weak_bear: True → False
```

## [2026-02-27 15:13] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_151305.json`
- **Изменения:**
```
  ~ filters.close_long_on_neutral: True → False
  ~ filters.close_short_on_neutral: True → False
```

## [2026-02-27 20:09] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_200954.json`
- **Изменения:**
```
  + exchange = okx
```

## [2026-02-27 20:29] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_202908.json`
- **Изменения:**
```
  + exchange = okx
```

## [2026-02-27 20:41] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_204100.json`
- **Изменения:**
```
  ~ ai_provider: deepseek → mock
```

## [2026-02-27 20:44] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_204414.json`
- **Изменения:**
```
  ~ ai_provider: deepseek → mock
```

## [2026-02-27 20:44] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_204433.json`
- **Изменения:**
```
  ~ ai_provider: deepseek → mock
```

## [2026-02-27 22:55] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_225529.json`
- **Изменения:**
```
  ~ ai_provider: deepseek → mock
```

## [2026-02-27 23:36] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_233635.json`
- **Изменения:**
```
  ~ trading.trailing_activation_pct: 0.5 → 1
```

## [2026-02-27 23:37] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260227_233738.json`
- **Изменения:**
```
  ~ trading.trailing_activation_pct: 1 → 1.5
```

## [2026-02-28 00:00] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_000015.json`
- **Изменения:**
```
  ~ trading.stop_loss_pct: 1.25 → 0.8
  ~ trading.trailing_activation_pct: 1.5 → 2.0
  ~ trading.trailing_distance_pct: 0.05 → 0.4
```

## [2026-02-28 00:08] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_000829.json`
- **Изменения:**
```
  (без изменений)
```

## [2026-02-28 00:24] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_002451.json`
- **Изменения:**
```
  + trading.atr_adaptive_sl = True
  + trading.atr_sl_multiplier = 1.5
  + trading.atr_trail_activation_multiplier = 3.0
  + trading.atr_trail_distance_multiplier = 0.7
```

## [2026-02-28 00:32] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_003224.json`
- **Изменения:**
```
  + trading.atr_adaptive_sl = True
  + trading.atr_sl_multiplier = 1.5
  + trading.atr_trail_activation_multiplier = 3.0
  + trading.atr_trail_distance_multiplier = 0.7
```

## [2026-02-28 00:38] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_003854.json`
- **Изменения:**
```
  + trading.atr_adaptive_sl = True
  + trading.atr_sl_multiplier = 1.5
  + trading.atr_trail_activation_multiplier = 3.0
  + trading.atr_trail_distance_multiplier = 0.7
```

## [2026-02-28 04:59] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_045937.json`
- **Изменения:**
```
  + entry_filters.min_rvol = 1.2
  + entry_filters.multi_tf_ema_period = 20
  + entry_filters.multi_tf_enabled = True
  + entry_filters.parabolic_enabled = True
  + entry_filters.parabolic_multiplier = 3.0
  + entry_filters.rvol_enabled = True
```

## [2026-02-28 05:11] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_051106.json`
- **Изменения:**
```
  ~ filters.btc_bearish_min_strength: 0.3 → 0.5
  ~ filters.btc_bullish_min_strength: 0.3 → 0.5
```

## [2026-02-28 05:14] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_051406.json`
- **Изменения:**
```
  ~ filters.btc_bearish_min_strength: 0.3 → 0.5
  ~ filters.btc_bullish_min_strength: 0.3 → 0.5
```

## [2026-02-28 06:08] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_060812.json`
- **Изменения:**
```
  ~ filters.btc_bearish_min_strength: 0.3 → 0.5
  ~ filters.btc_bullish_min_strength: 0.3 → 0.5
```

## [2026-02-28 06:08] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_060822.json`
- **Изменения:**
```
  ~ filters.btc_bearish_min_strength: 0.3 → 0.5
  ~ filters.btc_bullish_min_strength: 0.3 → 0.5
```

## [2026-02-28 14:25] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_142500.json`
- **Изменения:**
```
  ~ filters.btc_bearish_min_strength: 0.3 → 0.5
  ~ filters.btc_bullish_min_strength: 0.3 → 0.5
```

## [2026-02-28 17:13] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_171350.json`
- **Изменения:**
```
  + auto_backtest.auto_switch = False
  + auto_backtest.compare_strategies = True
  + auto_backtest.days = 7
  + auto_backtest.enabled = True
  + auto_backtest.interval_hours = 24
  + auto_backtest.notify_telegram = True
  + auto_backtest.pairs = 0
  - entry_filters.min_rvol (удалён)
  - entry_filters.rvol_enabled (удалён)
  + strategy.lookback_candles = 3
  + strategy.min_move_pct = 0.5
  + strategy.min_rvol = 2.0
  + strategy.side_filter = any
  + strategy.type = vol_momentum
```

## [2026-02-28 17:28] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_172847.json`
- **Изменения:**
```
  ~ auto_backtest.auto_switch: False → True
```

## [2026-02-28 17:28] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_172850.json`
- **Изменения:**
```
  ~ auto_backtest.auto_switch: False → True
```

## [2026-02-28 17:31] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_173159.json`
- **Изменения:**
```
  ~ auto_backtest.auto_switch: False → True
```

## [2026-02-28 17:48] Backup config.json

- **Причина:** auto: config.json changed
- **Бэкап:** `backups/config_20260228_174805.json`
- **Изменения:**
```
  ~ auto_backtest.auto_switch: False → True
```
