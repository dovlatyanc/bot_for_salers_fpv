# QUICKSTART: запуск FPV Sales Bot с нуля (сервер Ubuntu/Debian)

Линейный чек-лист «чистый сервер → работающий бот + watchdog», ~15 минут.
Детали и пояснения «почему так» — в deploy/README.md (разделы 1–5).

## 0. Что подготовить

- сервер Ubuntu/Debian с root-доступом по SSH;
- `BOT_TOKEN` от @BotFather;
- ваш Telegram ID (узнать у @userinfobot) — для `ADMIN_ID` (команда /health
  и уведомления watchdog приходят только ему);
- ключи AI-провайдера:
  * `cloudflare` → `AI_PROXY_URL` + `AI_PROXY_TOKEN` (Worker из fpv-ai-proxy);
  * `yandex` → `YANDEX_API_KEY` (или IAM-токен) + `YANDEX_FOLDER_ID`;
- Python **3.11+** на сервере (`python3 --version`; для Ubuntu 22.04 —
  python3.11 из deadsnakes) — по нему собран requirements.txt;
- SSH-ключ на вашем компьютере (`ssh-keygen -t ed25519`), публичная половина —
  в `/root/.ssh/authorized_keys` на сервере. Парольный вход для деплоя
  не годится: команды должны идти без ручного ввода пароля.

## 1. Часовой пояс сервера

Утренняя сводка follow-up (9:00) и напоминания считаются по времени сервера:

```bash
timedatectl set-timezone Europe/Moscow    # или свой регион
```

## 2. На машине разработки: сборка и заливка релиза

```bash
python deploy/make_release.py      # → dist/fpvbot_<штамп>.zip + SHA-256
scp dist/fpvbot_*.zip root@IP_СЕРВЕРА:/root/
```

Архив гарантированно без .env, БД и логов (проверки внутри make_release.py).

## 3. На сервере: пакеты, распаковка, зависимости

```bash
apt update && apt install -y python3-venv python3-pip unzip
mkdir -p /root/fpvbot && unzip -o /root/fpvbot_*.zip -d /root/fpvbot
cd /root/fpvbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 4. .env и каталог данных (вне папки кода!)

```bash
mkdir -p /root/fpvbot_data
cat > /root/fpvbot/.env <<'EOF'
BOT_TOKEN=
ADMIN_ID=
AI_PROVIDER=cloudflare
AI_PROXY_URL=
AI_PROXY_TOKEN=
DB_URL=sqlite:////root/fpvbot_data/bot.db
HEARTBEAT_FILE=/root/fpvbot_data/heartbeat.txt
EOF
nano /root/fpvbot/.env     # вписать реальные значения
chmod 600 /root/fpvbot/.env
```

БД в `/root/fpvbot_data/` — чтобы `unzip -o` нового релиза физически не мог
её перезаписать; heartbeat там же (см. раздел 5 README).

## 5. Пробный запуск руками (до systemd)

```bash
.venv/bin/python bot.py
# в логе: «Bot starting...», «DB file: /root/fpvbot_data/bot.db»,
# «AI provider: …, configured: True», строка «Heartbeat: метка каждые 60 с …»
# в Telegram: /start → меню отвечает. Остановить: Ctrl+C
```

## 6. systemd: автозапуск и работа независимо от SSH

```bash
cp deploy/fpvbot.service /etc/systemd/system/
# (проверьте в юните User/WorkingDirectory/ExecStart под свои пути)
systemctl daemon-reload
systemctl enable --now fpvbot
systemctl status fpvbot            # active (running)
journalctl -u fpvbot -f            # живой лог
```

Теперь бот переживает закрытие SSH, падения (Restart=always) и ребут сервера.

## 7. Watchdog: проверка живости каждые 4 часа + рестарт при зависании

```bash
/usr/bin/python3 /root/fpvbot/deploy/watchdog.py --dry-run
# ожидаем: «Telegram: getMe: @<бот>» и «HEALTHY: сервис активен, heartbeat свежий»

cp /root/fpvbot/deploy/fpvbot-watchdog.service \
   /root/fpvbot/deploy/fpvbot-watchdog.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fpvbot-watchdog.timer
systemctl list-timers fpvbot-watchdog*     # когда следующий прогон
```

Watchdog ловит то, что systemd не видит: процесс жив, но не отвечает
(heartbeat просрочен) → сам перезапускает и присылает уведомление в Telegram.

## 8. Ежедневный бэкап БД по cron

```bash
crontab -e    # добавить строку (бэкап до утренней сводки в 9:00):
15 8 * * * /usr/bin/python3 /root/fpvbot/deploy/backup_db.py --db /root/fpvbot_data/bot.db --out /root/fpvbot_backups --keep 14
```

## 9. Финальный чек-лист

- [ ] `systemctl is-active fpvbot` → active
- [ ] в Telegram `/start` открывает меню
- [ ] `/health` → «сконфигурирован: True … ✅» (видит только ADMIN_ID)
- [ ] `ls -l /root/fpvbot_data/heartbeat.txt` — метка свежая (обновляется ежеминутно)
- [ ] `systemctl list-timers | grep watchdog` — есть будущий прогон
- [ ] закрыли SSH, переподключились — `systemctl is-active fpvbot` всё ещё active

## Дальше

- новая версия кода: раздел 2 deploy/README.md (бэкап → unzip → pip → restart);
- деградация AI: диагностика через `/health` и
  `journalctl -u fpvbot -n 100 --no-pager | grep -i error`;
- откат: раздел 4 deploy/README.md.

---

## Приложение: как дать агенту (Cline) деплоить с вашего компьютера

Приватный ключ **никогда не передаётся в чат** — он остаётся на вашем
компьютере, агент лишь пользуется ssh-клиентом машины:

1. `ssh-keygen -t ed25519` (если ключа ещё нет; passphrase можно пустым для
   неинтерактивной работы);
2. публичный ключ на сервер — добавить строку из `~/.ssh/id_ed25519.pub`
   в `/root/.ssh/authorized_keys` (одноразово, любым доступным способом:
   панель хостинга, парольный вход, консоль провайдера);
3. создать файл `C:\Users\<вы>\.ssh\config`:

   ```
   Host fpvbot
       HostName <IP сервера>
       User root
       IdentityFile ~/.ssh/id_ed25519
       StrictHostKeyChecking accept-new
   ```

4. проверить локально: `ssh fpvbot "echo ok"` → `ok` (пароль не спрашивается);
5. сообщить агенту алиас (`fpvbot`) — дальше деплой пойдёт командами
   `ssh fpvbot "…"` и `scp dist/fpvbot_*.zip fpvbot:/root/`, каждый шаг
   с показом вывода и подтверждением поте��циально опасных действий.
