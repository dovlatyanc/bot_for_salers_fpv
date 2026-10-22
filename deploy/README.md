# Деплой FPV Sales Bot (systemd + бэкапы + релиз-архив)

Три правила прод-гигиены, которые закрывает этот каталог:

1. **Бот живёт в systemd**, а не в `nohup`: автоподъём после падения и ребута.
2. **БД не в папке кода** (`DB_URL=sqlite:////root/fpvbot_data/bot.db`) —
   `unzip -o` нового релиза физически не может её перезаписать.
3. **Бэкап перед каждым деплоем** (`deploy/backup_db.py`) — даже если БД
   ещё в `data/`, снимок делается одной командой и остаётся в `backups/`.

---

## 1. Одноразовый переезд с nohup на systemd

```bash
# 1) остановить nohup-процесс (SIGINT → bot.main() корректно закроет БД)
pkill -INT -f "python bot.py"

# 2) (рекомендуется) вынести БД из папки кода
mkdir -p /root/fpvbot_data
chown $(whoami) /root/fpvbot_data
# БД копировать ТОЛЬКО при остановленном боте (pkill выше):
cp -av /root/fpvbot/data/bot.db* /root/fpvbot_data/
# WAL-режим сам восстановит -wal/-shm; копируем их вместе с bot.db

# 3) прописать путь в .env (остальные переменные не трогаем)
echo 'DB_URL=sqlite:////root/fpvbot_data/bot.db' >> /root/fpvbot/.env
# (если строка DB_URL там уже есть — отредактируйте её, а не дублируйте)

# 4) установить юнит и запустить
cp /root/fpvbot/deploy/fpvbot.service /etc/systemd/system/fpvbot.service
#   открыть юнит и сверить User/WorkingDirectory/ExecStart со своими путями
systemctl daemon-reload
systemctl enable --now fpvbot
systemctl status fpvbot            # active (running)
journalctl -u fpvbot -f            # живой лог; в боте: «Bot starting...»
```

Проверка после старта: в Telegram `/start` → меню отвечает; лог содержит
`DB file: /root/fpvbot_data/bot.db` (движок логирует путь при старте).

## 2. Регулярный деплой новой версии

На машине разработки:
```bash
python deploy/make_release.py      # → dist/fpvbot_<дата>.zip + SHA-256
# архив НЕ содержит .env, data/bot.db*, логи — см. проверки в выводе
```
На сервере:
```bash
cd /root/fpvbot

# 1) БЭКАП ПЕРЕД ДЕПЛОЕМ (работает на живом боте, снапшот с учётом WAL)
python3 deploy/backup_db.py        # или с явными путями:
# python3 deploy/backup_db.py --db /root/fpvbot_data/bot.db --out /root/fpvbot_backups --keep 14

# 2) залить dist/fpvbot_<дата>.zip (scp) и развернуть
unzip -o ~/fpvbot_<дата>.zip -d /root/fpvbot

# 3) зависимости (если менялся requirements.txt)
/root/fpvbot/.venv/bin/pip install -r requirements.txt

# 4) перезапуск и проверка
systemctl restart fpvbot
journalctl -u fpvbot -n 50 --no-pager   # «Bot starting...», «DB file: …», без Traceback
# в Telegram: /start, 💰 Калькулятор → быстрый расчёт, ⏰ Напоминания → список
```

`.env` в архив не входит и при `unzip -o` не трогается; `data/bot.db*` туда
тоже не попадает — даже забытый бэкап не приведёт к потере данных.

## 3. Бэкапы: разово, перед деплоем, по cron

```bash
# вручную / перед деплоем (см. выше)
python3 deploy/backup_db.py --db /root/fpvbot_data/bot.db --out /root/fpvbot_backups

# cron: ежедневно в 08:15 (утренняя сводка бота в 9:00 — бэкап до неё)
crontab -e
15 8 * * * /usr/bin/python3 /root/fpvbot/deploy/backup_db.py --db /root/fpvbot_data/bot.db --out /root/fpvbot_backups --keep 14
```

Скрипт: онлайн-снапшот (`sqlite3.Connection.backup`, WAL учитывается),
`PRAGMA integrity_check` на копии, ретеншн `--keep N` (по умолчанию 14).
Восстановление (бот остановлен):
```bash
systemctl stop fpvbot
cp /root/fpvbot_backups/bot_<дата>.db /root/fpvbot_data/bot.db
rm -f /root/fpvbot_data/bot.db-wal /root/fpvbot_data/bot.db-shm
systemctl start fpvbot
```

## 4. Откат версии

Код в git — откат делается на стороне репозитория/архива (храните прошлый
`dist/fpvbot_*.zip`), БД — из бэкапа (`backup_db.py` складывает копии с
датой в имени). Данные и код откатываются независимо.

## 5. Watchdog: проверка живости и авто-перезапуск (каждые 4 часа)

`Restart=always` в юните поднимает бота после **падения** процесса. Если процесс
жив, но **завис** (polling встал, дедлок в цикле событий), systemd ничего не
заметит. Это закрывает watchdog: `deploy/watchdog.py` (только stdlib, работает
под системным python3, независимо от SSH) по таймеру `fpvbot-watchdog.timer`
каждые 4 часа проверяет:

1. юнит `fpvbot` в статусе `active` (`systemctl is-active`);
2. **heartbeat**: бот раз в 60 с пишет метку времени в `HEARTBEAT_FILE`
   (фоновая задача в bot.py); файл старше `HEARTBEAT_MAX_AGE` (900 с по
   умолчанию) или отсутствует → цикл событий завис;
3. проба `getMe` — информационная (сеть Telegram/токен): если красна ТОЛЬКО
   она, рестарт НЕ делается — перезапуск не чинит канал, пишется лог.

При проблеме: `systemctl restart fpvbot` → повторная проверка → уведомление
`ADMIN_ID` в Telegram («🔄 Watchdog: бот не отвечал… перезапущен» / «❌ … требуется
вмешательство»). Лог watchdog: `journalctl -u fpvbot-watchdog -n 50 --no-pager`.

### Установка (одноразово, после раздела 1)

```bash
# 1) включить heartbeat в .env бота (файл вне папки кода — переживёт unzip -o)
echo 'HEARTBEAT_FILE=/root/fpvbot_data/heartbeat.txt' >> /root/fpvbot/.env
systemctl restart fpvbot
sleep 5; ls -l /root/fpvbot_data/heartbeat.txt   # метка появляется сразу после старта

# 2) проверка без рестарта (перед включением таймера)
/usr/bin/python3 /root/fpvbot/deploy/watchdog.py --dry-run
# ожидается: HEALTHY: сервис активен, heartbeat свежий

# 3) поставить таймер
cp /root/fpvbot/deploy/fpvbot-watchdog.service \
   /root/fpvbot/deploy/fpvbot-watchdog.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fpvbot-watchdog.timer
systemctl list-timers fpvbot-watchdog*    # когда будет следующий прогон
```

### Самопроверка (безопасна: watchdog сам перезапустит бота)

```bash
systemctl stop fpvbot && /usr/bin/python3 /root/fpvbot/deploy/watchdog.py
# в логе: UNHEALTHY → Перезапускаю → RECOVERED; в Telegram придёт уведомление
```

Имитация зависания без остановки юнита — состарить файл heartbeat:
```bash
touch -d '30 minutes ago' /root/fpvbot_data/heartbeat.txt
/usr/bin/python3 /root/fpvbot/deploy/watchdog.py --dry-run   # UNHEALTHY (код 1)
```

### Альтернатива без systemd-таймера — cron

```cron
20 */4 * * * /usr/bin/flock -n /tmp/fpvbot_watchdog.lock /usr/bin/python3 /root/fpvbot/deploy/watchdog.py >> /root/fpvbot/watchdog.log 2>&1
```
(таймер И cron одновременно включать не нужно)

### Переменные .env watchdog (все необязательные)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `HEARTBEAT_FILE` | пусто = проверка выключена | файл пульса; пишется bot.py, путь указывается в .env бота |
| `HEARTBEAT_INTERVAL` | `60` | период записи метки ботом, с |
| `HEARTBEAT_MAX_AGE` | `900` | «просрочка» метки, после которой watchdog считает бота зависшим, с |
| `WATCHDOG_UNIT` | `fpvbot` | какой юнит проверять/перезапускать |
| `WATCHDOG_RESTART_CMD` | `systemctl restart <UNIT>` | своя команда рестарта (сложную — как `bash -c '…'`) |
| `WATCHDOG_NOTIFY` | `1` | `0` — отключить Telegram-уведомления о рестартах |

## 6. Боевой деплой с локальной машины (переезд на VPS)

Бот = polling (не webhook). Один токен = один poller: перед стартом
серверного бота локальная копия ОБЯЗАНА быть остановлена, иначе poller'ы
ломают друг друга через общий offset. Если на токене раньше висел webhook,
polling упадёт с TelegramConflictError — перед первым стартом снимать его
скриптом `deploy/delete_webhook.py` (шаг 7).

### Локально (до включения VPN)

```bash
# 1) зелёная тестовая база
pytest -q                                   # 0 failed
python _verify_all.py                       # или все _verify_*.py по одному

# 2) свежий бэкап живой БД
python deploy/backup_db.py                  # → data/backups/bot_<дата>.db

# 3) релизный архив (без .env и data/bot.db* — см. вывод скрипта)
python deploy/make_release.py               # → dist/fpvbot_<дата>.zip + SHA-256
```

### VPN-сессия

```bash
# 4) залить архив и бэкап БД на VPS (БД — вне папки кода)
ssh user@vps 'mkdir -p /root/fpvbot_data'
scp dist/fpvbot_<дата>.zip user@vps:/opt/fpvbot.zip
scp data/backups/bot_<дата>.db user@vps:/root/fpvbot_data/bot.db

# 5) на VPS: python и зависимости (нужен python3 ≥ 3.11)
ssh user@vps
python3 --version
cd /opt && unzip -o fpvbot.zip -d fpvbot && cd fpvbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 6) .env из примера и заполнить
cp .env.example .env && nano .env
#   BOT_TOKEN=...            токен бота
#   ADMIN_ID=...             ваш Telegram id
#   DB_URL=sqlite:////root/fpvbot_data/bot.db
#   HEARTBEAT_FILE=/root/fpvbot_data/heartbeat
#   HEARTBEAT_INTERVAL=60
#   PROXY_URL=               ПУСТО: VPS ходит в Telegram напрямую
#   AI_PROVIDER / ключи AI   опционально

# 7) снять webhook с токена (если когда-то ставился) — ожидаем {"ok": true, ...}
.venv/bin/python deploy/delete_webhook.py

# 8) ОСТАНОВИТЬ локального бота ДО этого шага (Ctrl+C / pkill -INT -f "python bot.py").
#    Один токен = один poller: два poller'а на одном токене ломают друг друга.

# 9) systemd: юнит бота + watchdog-таймер
cp deploy/fpvbot.service /etc/systemd/system/
cp deploy/fpvbot-watchdog.service deploy/fpvbot-watchdog.timer /etc/systemd/system/
#    открыть юниты и сверить User/WorkingDirectory/ExecStart со своими путями
systemctl daemon-reload
systemctl enable --now fpvbot
systemctl enable --now fpvbot-watchdog.timer
```

```bash
# 10) проверки
systemctl status fpvbot                     # active (running)
journalctl -u fpvbot -n 50 --no-pager
#   ожидать: «DB file: /root/fpvbot_data/bot.db», «Start polling»,
#            «Compat rules seeded: …», «Recipes seeded: …», без Traceback
ls -l /root/fpvbot_data/heartbeat           # mtime обновляется (~раз в 60 с)
#   в Telegram: /start (меню), /stats, /recipes — всё отвечает

# 11) выключить VPN и убедиться, что бот отвечает (Telegram-клиенту VPN не нужен).
#     Локальная копия бота больше НЕ запускается — бот теперь живёт на VPS.
```

### Откат

```bash
# на VPS: остановить серверного бота
systemctl stop fpvbot
# вариант А: поднять прошлую версию кода — распаковать прошлый
#   dist/fpvbot_*.zip в /opt/fpvbot и systemctl start fpvbot;
# вариант Б: откат данных — восстановить bot.db из бэкапа (бот остановлен):
cp /root/fpvbot_backups/bot_<дата>.db /root/fpvbot_data/bot.db
rm -f /root/fpvbot_data/bot.db-wal /root/fpvbot_data/bot.db-shm
systemctl start fpvbot
# после отката локальный запуск: только пока серверный бот остановлен.
```

### Еженедельно

Менеджер шлёт CSV-выгрузку остатков прямо в чат бота командой
`/load_stock` (прикрепить файл CSV к сообщению) — бот на VPS примет и
загрузит остатки. Расход за период — `/stock_stats`. Локальная машина для
этого не нужна.

