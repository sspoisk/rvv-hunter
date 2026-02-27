#!/usr/bin/env bash
# backup_config.sh — создаёт timestamped бэкап config.json с diff изменений
# Использование:
#   ./scripts/backup_config.sh                  # бэкап с автоматическим diff
#   ./scripts/backup_config.sh "причина"        # бэкап с описанием

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$PROJECT_DIR/config.json"
BACKUP_DIR="$PROJECT_DIR/backups"
CHANGELOG="$PROJECT_DIR/CHANGELOG.md"

if [[ ! -f "$CONFIG" ]]; then
    echo "ERROR: $CONFIG не найден" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
REASON="${1:-auto: config.json changed}"
BACKUP_FILE="$BACKUP_DIR/config_${TIMESTAMP}.json"

# Создаём бэкап
cp "$CONFIG" "$BACKUP_FILE"

# Находим предыдущий бэкап с ДРУГИМ содержимым (пропускаем дубликаты)
CURRENT_MD5=$(md5sum "$BACKUP_FILE" | awk '{print $1}')
PREV_BACKUP=""
while IFS= read -r candidate; do
    [[ "$candidate" == "$BACKUP_FILE" ]] && continue
    CAND_MD5=$(md5sum "$candidate" | awk '{print $1}')
    if [[ "$CAND_MD5" != "$CURRENT_MD5" ]]; then
        PREV_BACKUP="$candidate"
        break
    fi
done < <(ls -t "$BACKUP_DIR"/config_*.json 2>/dev/null)
echo "✅ Бэкап создан: $BACKUP_FILE"

# Удаляем бэкапы старше 30 дней (оставляем последние 50)
cd "$BACKUP_DIR"
ls -t config_*.json 2>/dev/null | tail -n +51 | xargs -r rm --
find "$BACKUP_DIR" -name "config_*.json" -mtime +30 -delete 2>/dev/null || true

# Генерируем diff изменений
DIFF_TEXT=""
if [[ -n "$PREV_BACKUP" && -f "$PREV_BACKUP" ]]; then
    # Используем python для красивого сравнения JSON
    DIFF_TEXT=$("$PROJECT_DIR/venv/bin/python3" -c "
import json, sys

try:
    with open('$PREV_BACKUP') as f:
        old = json.load(f)
    with open('$CONFIG') as f:
        new = json.load(f)
except Exception as e:
    print(f'Ошибка чтения: {e}')
    sys.exit(0)

def flatten(d, prefix=''):
    items = {}
    for k, v in d.items():
        key = f'{prefix}{k}' if not prefix else f'{prefix}.{k}'
        if isinstance(v, dict):
            items.update(flatten(v, key))
        elif isinstance(v, list) and len(v) > 10:
            items[key] = f'[{len(v)} items]'
        else:
            items[key] = v
    return items

old_flat = flatten(old)
new_flat = flatten(new)
all_keys = sorted(set(list(old_flat.keys()) + list(new_flat.keys())))

changes = []
for k in all_keys:
    old_v = old_flat.get(k)
    new_v = new_flat.get(k)
    if old_v != new_v:
        if old_v is None:
            changes.append(f'  + {k} = {new_v}')
        elif new_v is None:
            changes.append(f'  - {k} (удалён)')
        else:
            changes.append(f'  ~ {k}: {old_v} → {new_v}')

if changes:
    print('\n'.join(changes))
else:
    print('  (без изменений)')
" 2>/dev/null || echo "  (diff недоступен)")
else
    DIFF_TEXT="  (первый бэкап, нет предыдущего для сравнения)"
fi

# Записываем в CHANGELOG
cat >> "$CHANGELOG" << EOF

## [$(date '+%Y-%m-%d %H:%M')] Backup config.json

- **Причина:** $REASON
- **Бэкап:** \`backups/config_${TIMESTAMP}.json\`
- **Изменения:**
\`\`\`
$DIFF_TEXT
\`\`\`
EOF

echo "📝 Записано в CHANGELOG.md"
echo "$DIFF_TEXT"
