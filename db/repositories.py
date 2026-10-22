"""Репозитории: доступ к таблицам через dict-интерфейс.

Форматы dict — те же, что раньше лежали в JSON (хендлеры рендерят их напрямую):
  follow_ups    → {"id", "user_id", "text", "due": "ГГГГ-ММ-ДД"|None,
                   "due_at": ISO-строка|None, "created_at": ISO-строка,
                   "done": bool, "done_at": ISO|None}
  calc_entries  → {"ts": ISO-строка, "model", "cost", "price", "margin", "margin_pct"}
  prices        → {код: {"cost", "cash", "wholesale", "vat5", "vat22", "profit"}}
  faq_*         → [{"name", "questions": [{"q", "a"}]}]
  settings      → ключ → значение (str)
  users / daily_counters — аналитика /stats (п. 3.1): touch-учёт менеджеров
                   и дневные счётчики обращений к AI; dict-формат не нужен.

Все методы async и берут сессию из db.engine.get_sessionmaker().
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from db.engine import get_sessionmaker
from db.retry import retry_on_db_lock
from db.models import (
    BotSetting,
    CalcEntry,
    DailyCounter,
    FaqCategory,
    FaqQuestion,
    FollowUp,
    FollowUpReminder,
    Price,
    User,
)

system_logger = logging.getLogger("system")

# Колонки прайса (cost обязателен, остальные ступени — опциональны)
PRICE_COLUMNS: tuple[str, ...] = ("cost", "cash", "wholesale", "vat5", "vat22", "profit")


def _iso_dt(value: datetime | None) -> str | None:
    """datetime → ISO-строка (как в старом JSON: секунды, без таймзоны)."""
    return value.isoformat(timespec="seconds") if value else None


def _parse_dt(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _followup_dict(it: FollowUp) -> dict:
    """Строка follow_ups → dict в формате бывшего JSON (items)."""
    return {
        "id": it.id,
        "user_id": int(it.user_id),
        "text": it.text,
        "due": it.due.isoformat() if it.due else None,
        "due_at": _iso_dt(it.due_at),
        "created_at": _iso_dt(it.created_at),
        "done": bool(it.done),
        "done_at": _iso_dt(it.done_at),
    }


# ============================================================
# «⏰ Напоминания»: follow_ups
# ============================================================
class FollowUpRepository:
    """Таблица follow_ups + адресаты/сроки для утренней сводки."""

    @retry_on_db_lock()
    async def add(self, user_id: int, text: str, due: date | None,
                  due_at: datetime | None = None) -> dict:
        """Создаёт напоминание и возвращает его dict (id — автоинкремент).

        due_at — точный момент события (если время известно из текста);
        по нему upcoming_loop шлёт предварительные напоминания «за N минут».
        """
        async with get_sessionmaker()() as session:
            item = FollowUp(
                user_id=user_id, text=text, due=due, due_at=due_at,
                created_at=datetime.now(), done=False, done_at=None,
            )
            session.add(item)
            await session.commit()
            return _followup_dict(item)

    async def mark_done(self, user_id: int, item_id: int) -> bool:
        """Отмечает выполненным; False — не найдено или уже отмечено."""
        return await self._set_done(user_id, item_id, True)

    async def reopen(self, user_id: int, item_id: int) -> bool:
        """Возвращает в активные; False — не найдено или уже активно."""
        return await self._set_done(user_id, item_id, False)

    @retry_on_db_lock()
    async def _set_done(self, user_id: int, item_id: int, done: bool) -> bool:
        async with get_sessionmaker()() as session:
            item = await session.get(FollowUp, item_id)
            if item is None or item.user_id != user_id or bool(item.done) == done:
                return False
            item.done = done
            item.done_at = datetime.now() if done else None
            await session.commit()
            return True

    @retry_on_db_lock()
    async def delete(self, user_id: int, item_id: int) -> bool:
        """Удаляет напоминание менеджера; False — чужое или не найдено."""
        async with get_sessionmaker()() as session:
            item = await session.get(FollowUp, item_id)
            if item is None or item.user_id != user_id:
                return False
            await session.delete(item)
            await session.commit()
            return True

    async def get_active(self, user_id: int) -> list[dict]:
        """Активные (не выполненные): по возрастанию срока, «без срока» — в конце."""
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(FollowUp)
                .where(FollowUp.user_id == user_id, FollowUp.done.is_(False))
                .order_by(FollowUp.due.is_(None).asc(), FollowUp.due.asc(), FollowUp.id.asc())
            )
            return [_followup_dict(r) for r in rows]

    async def get_done(self, user_id: int, limit: int = 10) -> list[dict]:
        """Выполненные — свежие отметки первыми (без done_at — в конце)."""
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(FollowUp)
                .where(FollowUp.user_id == user_id, FollowUp.done.is_(True))
                .order_by(
                    FollowUp.done_at.is_(None).asc(),
                    FollowUp.done_at.desc(),
                    FollowUp.id.desc(),
                )
                .limit(limit)
            )
            return [_followup_dict(r) for r in rows]

    async def collect_due(self, user_id: int, on_date: date | None = None) -> list[dict]:
        """Активные напоминания со сроком ровно на дату (хук утренней сводки)."""
        day = on_date or date.today()
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(FollowUp)
                .where(
                    FollowUp.user_id == user_id,
                    FollowUp.done.is_(False),
                    FollowUp.due == day,
                )
                .order_by(FollowUp.id.asc())
            )
            return [_followup_dict(r) for r in rows]

    async def daily_recipients(self, on_date: date | None = None) -> list[int]:
        """user_id менеджеров с активными напоминаниями на дату или ранее.

        Выполненные, будущие и «без срока» в рассылку не попадают.
        """
        day = on_date or date.today()
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(FollowUp.user_id).distinct()
                .where(
                    FollowUp.done.is_(False),
                    FollowUp.due.is_not(None),
                    FollowUp.due <= day,
                )
                .order_by(FollowUp.user_id)
            )
            return [int(uid) for (uid,) in result.all()]

    async def upcoming(self, now: datetime | None = None,
                       within_seconds: int = 600) -> list[dict]:
        """Активные напоминания с точным временем (due_at) в горизонте окна.

        Хук сканера предварительных напоминаний (ЭТАП 3): строго будущее —
        уже наступившие события не напоминаем, выполненные пропускаем.
        """
        now = now or datetime.now()
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(FollowUp)
                .where(
                    FollowUp.done.is_(False),
                    FollowUp.due_at.is_not(None),
                    FollowUp.due_at >= now,
                    FollowUp.due_at <= now + timedelta(seconds=within_seconds),
                )
                .order_by(FollowUp.due_at.asc(), FollowUp.id.asc())
            )
            return [_followup_dict(r) for r in rows]

    async def reminder_sent(self, follow_up_id: int, minutes: int) -> bool:
        """Предварительное напоминание «за minutes» уже отправлялось?"""
        async with get_sessionmaker()() as session:
            row = await session.scalar(
                select(FollowUpReminder.id).where(
                    FollowUpReminder.follow_up_id == follow_up_id,
                    FollowUpReminder.minutes == minutes,
                )
            )
            return row is not None

    @retry_on_db_lock()
    async def mark_reminder_sent(self, follow_up_id: int, minutes: int) -> None:
        """Фиксирует отправку «за minutes» (повтор — no-op по уникальному ключу)."""
        async with get_sessionmaker()() as session:
            session.add(FollowUpReminder(
                follow_up_id=follow_up_id, minutes=minutes, sent_at=datetime.now(),
            ))
            try:
                await session.commit()
            except IntegrityError:            # гонка/повтор — запись уже есть
                await session.rollback()


# ============================================================
# «💰 Калькулятор»: история расчётов и прайс-лист
# ============================================================
class CalcHistoryRepository:
    """Таблица calc_entries: завершённые расчёты менеджеров."""

    @retry_on_db_lock()
    async def add(self, user_id: int, entry: dict, keep_last: int | None = None) -> None:
        """Сохраняет расчёт; keep_last — оставить только N свежих на менеджера."""
        async with get_sessionmaker()() as session:
            session.add(CalcEntry(
                user_id=user_id,
                ts=_parse_dt(entry.get("ts")) or datetime.now(),
                model=str(entry.get("model", "—")),
                cost=float(entry.get("cost", 0)),
                price=float(entry.get("price", 0)),
                margin=float(entry.get("margin", 0)),
                margin_pct=float(entry.get("margin_pct", 0)),
            ))
            await session.commit()
        if keep_last is not None:
            await self._prune(user_id, keep_last)

    @retry_on_db_lock()
    async def _prune(self, user_id: int, keep_last: int) -> None:
        """Удаляет у менеджера всё, кроме keep_last последних расчётов."""
        async with get_sessionmaker()() as session:
            keep = (
                select(CalcEntry.id)
                .where(CalcEntry.user_id == user_id)
                .order_by(CalcEntry.id.desc())
                .limit(keep_last)
            )
            await session.execute(
                delete(CalcEntry).where(
                    CalcEntry.user_id == user_id, CalcEntry.id.not_in(keep)
                )
            )
            await session.commit()

    async def get(self, user_id: int, limit: int = 5) -> list[dict]:
        """Последние расчёты менеджера (новые — в начале списка)."""
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(CalcEntry)
                .where(CalcEntry.user_id == user_id)
                .order_by(CalcEntry.id.desc())
                .limit(limit)
            )
            return [
                {
                    "ts": _iso_dt(r.ts),
                    "model": r.model,
                    "cost": r.cost,
                    "price": r.price,
                    "margin": r.margin,
                    "margin_pct": r.margin_pct,
                }
                for r in rows
            ]

    async def count_since(self, since: datetime) -> int:
        """Расчёты, завершённые с момента since (для /stats: «за сегодня»)."""
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(func.count()).select_from(CalcEntry).where(CalcEntry.ts >= since)
            )).scalar_one()


class PriceRepository:
    """Таблица prices: {код модели: ступени прайса}."""

    @staticmethod
    def _entry(r: Price) -> dict:
        """Строка prices → dict без None-полей (формат прежнего prices.json)."""
        return {
            key: float(getattr(r, key))
            for key in PRICE_COLUMNS
            if getattr(r, key) is not None
        }

    async def load(self) -> dict[str, dict]:
        """Весь прайс; записи без закупочной цены (<= 0) пропускаются."""
        async with get_sessionmaker()() as session:
            rows = await session.scalars(select(Price).order_by(Price.code))
            out: dict[str, dict] = {}
            for r in rows:
                entry = self._entry(r)
                if entry.get("cost", 0) > 0:
                    out[r.code] = entry
            return out

    async def get(self, code: str) -> dict | None:
        """Ступени прайса одной модели (нет/без корректного cost — None)."""
        async with get_sessionmaker()() as session:
            r = await session.get(Price, code)
            if r is None:
                return None
            entry = self._entry(r)
            return entry if entry.get("cost", 0) > 0 else None

    @retry_on_db_lock()
    async def upsert(self, code: str, values: dict) -> None:
        """Создаёт/обновляет запись прайса (используется сидом)."""
        async with get_sessionmaker()() as session:
            row = await session.get(Price, code)
            if row is None:
                row = Price(code=code, cost=0)
                session.add(row)
            for key in PRICE_COLUMNS:
                value = values.get(key)
                setattr(row, key, float(value) if value is not None else None)
            await session.commit()

    @retry_on_db_lock()
    async def set_field(self, code: str, field: str, value: float) -> bool:
        """Обновляет одну ступень прайса модели (/update_price).

        False — модели нет в таблице. field проверяется по PRICE_COLUMNS
        (cost/cash/wholesale/vat5/vat22/profit), прочие поля — ValueError.
        """
        if field not in PRICE_COLUMNS:
            raise ValueError(f"Неизвестная ступень прайса: {field}")
        async with get_sessionmaker()() as session:
            row = await session.get(Price, code)
            if row is None:
                return False
            setattr(row, field, float(value))
            await session.commit()
            return True


# ============================================================
# «❓ FAQ»: faq_categories + faq_questions
# ============================================================
class FaqRepository:
    """База знаний «❓ FAQ»: вложенный список категорий с вопросами."""

    async def load(self) -> list[dict]:
        """[{"name", "questions": [{"q", "a"}]}] — в порядке position."""
        async with get_sessionmaker()() as session:
            cats = await session.scalars(
                select(FaqCategory)
                .order_by(FaqCategory.position.asc(), FaqCategory.id.asc())
            )
            questions = await session.scalars(
                select(FaqQuestion)
                .order_by(FaqQuestion.position.asc(), FaqQuestion.id.asc())
            )
            by_cat: dict[int, list[dict]] = {}
            for q in questions:
                by_cat.setdefault(q.category_id, []).append({"q": q.q, "a": q.a})
            return [
                {"name": c.name, "questions": by_cat.get(c.id, [])}
                for c in cats
            ]

    async def counts(self) -> tuple[int, int]:
        """(категории, вопросы) — для тестов и диагностики."""
        async with get_sessionmaker()() as session:
            cats = (await session.execute(select(FaqCategory))).scalars().all()
            questions = (await session.execute(select(FaqQuestion))).scalars().all()
            return len(cats), len(questions)

    @retry_on_db_lock()
    async def add_question(self, category_name: str, q: str, a: str) -> tuple[bool, int]:
        """Добавляет вопрос в категорию FAQ (/add_faq); категория создаётся при отсутствии.

        Категория ищется без учёта регистра («общие» == «Общие»), позиция новой
        категории/вопроса — в конец списка (max(position) + 1), как у сида.
        Возвращает (создана_ли_новая_категория, всего_вопросов_в_категории).
        """
        name = (category_name or "").strip()
        if not name:
            raise ValueError("Название категории не может быть пустым")
        if not (q or "").strip() or not (a or "").strip():
            raise ValueError("Вопрос и ответ не могут быть пустыми")
        async with get_sessionmaker()() as session:
            cats = (await session.scalars(select(FaqCategory))).all()
            cat = next(
                (c for c in cats if c.name.strip().casefold() == name.casefold()),
                None,
            )
            created = False
            if cat is None:
                cat = FaqCategory(name=name, position=max(
                    (c.position for c in cats), default=-1) + 1)
                session.add(cat)
                await session.flush()          # нужен id до записи вопроса
                created = True
            questions = (await session.scalars(
                select(FaqQuestion).where(FaqQuestion.category_id == cat.id)
            )).all()
            session.add(FaqQuestion(
                category_id=cat.id,
                position=max((x.position for x in questions), default=-1) + 1,
                q=q.strip(),
                a=a.strip(),
            ))
            await session.commit()
            return created, len(questions) + 1


# ============================================================
# KV-настройки: settings
# ============================================================
class SettingsRepository:
    """KV-настройки бота (ключ last_daily и будущие служебные флаги)."""

    async def get(self, key: str) -> str | None:
        async with get_sessionmaker()() as session:
            row = await session.get(BotSetting, key)
            return row.value if row else None

    @retry_on_db_lock()
    async def set(self, key: str, value: str | None) -> None:
        async with get_sessionmaker()() as session:
            row = await session.get(BotSetting, key)
            if row is None:
                row = BotSetting(key=key)
                session.add(row)
            row.value = value
            await session.commit()


# ============================================================
# Пользователи бота: users (аналитика /stats)
# ============================================================
class UserRepository:
    """Таблица users: кто пользуется ботом и когда был активен последний раз."""

    @retry_on_db_lock()
    async def touch(self, user_id: int, username: str | None = None,
                    first_name: str | None = None) -> bool:
        """INSERT OR IGNORE + обновление last_seen_at; True — пользователь новый.

        Вызывается middleware UserTrackingMiddleware при каждом сообщении/нажатии.
        Имя и ник освежаются (сменил — видно в аналитике), is_active поднимается
        обратно в True при любом касании.
        """
        async with get_sessionmaker()() as session:
            row = await session.get(User, user_id)
            now = datetime.now()
            if row is None:
                session.add(User(
                    user_id=user_id, username=username, first_name=first_name,
                    created_at=now, last_seen_at=now, is_active=True,
                ))
                await session.commit()
                return True
            row.last_seen_at = now
            row.is_active = True
            if username:
                row.username = username
            if first_name:
                row.first_name = first_name
            await session.commit()
            return False

    async def count_all(self) -> int:
        """Всего пользователей, заходивших в бота хоть раз."""
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(func.count()).select_from(User)
            )).scalar_one()

    async def count_active_since(self, since: datetime) -> int:
        """Пользователей с last_seen_at >= since (для /stats: «за 7 дней»)."""
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(func.count()).select_from(User)
                .where(User.last_seen_at >= since)
            )).scalar_one()


# ============================================================
# Дневные счётчики метрик: daily_counters (аналитика /stats)
# ============================================================
class DailyCounterRepository:
    """Таблица daily_counters: суточные счётчики ('ai_ask', 'ai_voice')."""

    @retry_on_db_lock()
    async def incr(self, metric: str, amount: int = 1, day: date | None = None) -> None:
        """Атомарно увеличивает счётчик метрики за день (строка создаётся при отсутствии).

        Прибавление идёт одним UPDATE value = value + amount — конкурентные
        обработчики не теряют приращения. Гонка «строки ещё нет у обоих» гасится
        повторным UPDATE после IntegrityError (PK day+metric).
        """
        day = day or date.today()
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(DailyCounter)
                .where(DailyCounter.day == day, DailyCounter.metric == metric)
                .values(value=DailyCounter.value + amount)
            )
            if result.rowcount:
                await session.commit()
                return
            session.add(DailyCounter(day=day, metric=metric, value=amount))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                await session.execute(
                    update(DailyCounter)
                    .where(DailyCounter.day == day, DailyCounter.metric == metric)
                    .values(value=DailyCounter.value + amount)
                )
                await session.commit()

    async def value(self, metric: str, day: date | None = None) -> int:
        """Значение счётчика за день (по умолчанию — сегодня); нет строки — 0."""
        day = day or date.today()
        async with get_sessionmaker()() as session:
            row = await session.get(DailyCounter, (day, metric))
            return int(row.value) if row else 0


# ============================================================
# «🛠 Конструктор дронов» (ЭТАП 4): фасады вынесены в db.repositories_builder —
# с ними этот файл превысил бы ~700 строк. Реэкспорт, чтобы хендлеры могли
# импортировать всё из db.repositories:
#     from db.repositories import list_parts, add_alias, save_draft, ...
# ============================================================
from db.repositories_builder import (  # noqa: E402,F401
    add_alias,
    add_compat_rule,
    count_by_category,
    delete_alias,
    delete_draft,
    diff_snapshots,
    get_available_qty,
    get_draft,
    get_part,
    get_part_by_id,
    get_recipe,
    get_snapshot_rows,
    list_aliases,
    list_compat_rules,
    list_drafts,
    list_parts,
    list_recipes,
    list_snapshots,
    save_draft,
    seed_recipes,
    set_compat_rule_active,
)




