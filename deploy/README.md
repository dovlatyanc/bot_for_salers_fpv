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
