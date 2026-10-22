"""ORM-модели (SQLAlchemy 2.x), диалектно-нейтральные: SQLite ↔ PostgreSQL.

Типы выбраны так, чтобы переезд на PostgreSQL не требовал миграций схемы:
BigInteger для Telegram id, Float для денег (как и было в JSON — точность
рублей/копеек), Date/DateTime для сроков и меток времени, Text для текстов.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс всех таблиц (create_all в db.init_db)."""


# Категории позиций склада «🛠 Конструктора дронов» (ЭТАП 2; слоты сборки —
# data/builder/categories.py, классификатор — data/builder/keywords.py).
# Проставляется позициям классификатором Этапа 3; "other" — его фолбэк.
PART_CATEGORIES = (
    # --- слоты сборки борта ---
    "frame",            # рама
    "motor",            # моторы
    "prop",             # пропеллеры
    "fc_esc_stack",     # стек FC+ESC (одной связкой)
    "fc",               # полётный контроллер отдельно
    "esc",              # регулятор оборотов отдельно
    "vtx",              # видеопередатчик
    "camera",           # FPV-камера
    "antenna_air",      # бортовая антенна
    "antenna_ground",   # наземная антенна (патч/направленная)
    "rx",               # приёмник управления
    "tx",               # передатчик управления
    "vrx",              # видеоприёмник наземки
    # --- прочие категории склада ---
    "battery",          # аккумуляторы
    "fiber",            # оптоволокно и оптические модули связи
    "payload",          # сбросы и сервоприводы
    "spare",            # запчасти планера и мелкая обвязка (рама-части,
                        # маунты, резина, переходники, кабели, удлинители)
    "consumable",       # расходники (пластик, термоусадка, флюс, пины)
    "finished",         # готовые БАС (БАС/НСУ/образцы)
    "other",            # нераспознанное (фолбэк классификатора)
)


class BotSetting(Base):
    """KV-настройки бота (сюда переехал служебный ключ last_daily из followups.json)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class FollowUp(Base):
    """«⏰ Напоминания» менеджера (ex data/followups.json → items)."""

    __tablename__ = "follow_ups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text)
    due: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FollowUpReminder(Base):
    """Журнал отправленных предварительных напоминаний «за N минут» (ЭТАП 3).

    Уникальность (follow_up_id, minutes) делает отправку идемпотентной:
    рестарт бота или повторный скан планировщика дубль не пришлют.
    """

    __tablename__ = "followup_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    follow_up_id: Mapped[int] = mapped_column(
        ForeignKey("follow_ups.id", ondelete="CASCADE"), index=True
    )
    minutes: Mapped[int] = mapped_column(Integer)   # за сколько минут: 10 / 5 / 2
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("follow_up_id", "minutes", name="uq_followup_reminder"),
    )


class CalcEntry(Base):
    """Завершённый расчёт «💰 Калькулятор» (ex config/calc_history.json)."""

    __tablename__ = "calc_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    model: Mapped[str] = mapped_column(String(64))
    cost: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float)
    margin_pct: Mapped[float] = mapped_column(Float)


class Price(Base):
    """Прайс-лист калькулятора (ex config/prices.json)."""

    __tablename__ = "prices"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    cost: Mapped[float] = mapped_column(Float)            # закупочная — обязательна
    cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    wholesale: Mapped[float | None] = mapped_column(Float, nullable=True)
    vat5: Mapped[float | None] = mapped_column(Float, nullable=True)
    vat22: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit: Mapped[float | None] = mapped_column(Float, nullable=True)


class FaqCategory(Base):
    """Категория «❓ FAQ» (ex data/faq.json → categories)."""

    __tablename__ = "faq_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position: Mapped[int] = mapped_column(Integer, default=0)   # порядок показа
    name: Mapped[str] = mapped_column(String(256))


class FaqQuestion(Base):
    """Вопрос/ответ «❓ FAQ» (ex data/faq.json → questions)."""

    __tablename__ = "faq_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("faq_categories.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)   # порядок показа
    q: Mapped[str] = mapped_column(Text)
    a: Mapped[str] = mapped_column(Text)


class User(Base):
    """Пользователь бота (аудит/развитие п. 3.1): кто пользуется и когда был активен.

    Запись заводит middleware UserTrackingMiddleware (middleware.py) при первом
    сообщении/нажатии нового Telegram-id (аналог INSERT OR IGNORE), далее при
    каждом касании обновляет last_seen_at (и освежает имя/ник). is_active
    остаётся флагом для будущих рассылок/блокировок: касание поднимает его в True.
    """

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DailyCounter(Base):
    """Дневной счётчик метрик (аудит/развитие п. 3.1): обращения к AI для /stats.

    Первичный ключ (day, metric): метрики 'ai_ask' (текстовые вопросы через
    ai.ask_ai) и 'ai_voice' (распознавание голоса). Датой — локальный день
    (datetime.now), как у ts в calc_entries: статистика «за сегодня» должна
    совпадать с рабочим днём менеджеров.
    """

    __tablename__ = "daily_counters"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class UserState(Base):
    """FSM-состояния aiogram: кастомный SQLAlchemy-storage вместо MemoryStorage.

    Строка = ключ StorageKey (bot_id, chat_id, user_id, thread_id,
    business_connection_id, destiny). Для отсутствующих thread_id/
    business_connection_id — сентинелы 0/"" (PK не должен содержать NULL:
    SQLite это прощает, PostgreSQL — нет). state — имя состояния (или NULL),
    data — JSON-словарь FSMContext (или NULL). Запись ведёт db/fsm_storage.py;
    состояние переживает рестарт бота (watchdog), в отличие от MemoryStorage.
    """

    __tablename__ = "user_states"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=0)
    business_connection_id: Mapped[str] = mapped_column(
        String(128), primary_key=True, default=""
    )
    destiny: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON


# ============================================================
# «🛠 Конструктор дронов»: справочник комплектующих и остатки 1С (ЭТАП 1)
# ============================================================

class Part(Base):
    """Комплектующая «🛠 Конструктора дронов»: позиция справочника из CSV 1С.

    key — канонический ключ, детерминированно нормализованный из названия
    (db.stock_import.part_key): повторная загрузка файла с той же номенклатурой
    обновляет запись, а не плодит дубли. category — этап категоризации
    (из CSV 1С не приходит), params — распознанные атрибуты (JSON-совместимо
    с SQLite и PostgreSQL), наполняются поздними этапами.
    """

    __tablename__ = "parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class Stock(Base):
    """Остаток по комплектующей (1:1 с parts), обновляется каждой загрузкой CSV 1С.

    quantity — Float: 1С отдаёт штуки/килограммы/метры с тремя знаками после
    запятой («11,000»). Позиции, отсутствующие в новом файле, не удаляются —
    их остаток обнуляется (история, алиасы и id сохраняются).
    """

    __tablename__ = "stock"

    part_id: Mapped[int] = mapped_column(
        ForeignKey("parts.id", ondelete="CASCADE"), primary_key=True
    )
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class StockSnapshot(Base):
    """Загруженный файл остатков 1С: аудит импорта (когда/какой файл/статистика).

    total_parts — позиций в файле, new_parts — создано, removed_parts —
    обнулено из-за отсутствия в файле.
    """

    __tablename__ = "stock_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256))
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    total_parts: Mapped[int] = mapped_column(Integer, default=0)
    new_parts: Mapped[int] = mapped_column(Integer, default=0)
    removed_parts: Mapped[int] = mapped_column(Integer, default=0)


class StockSnapshotRow(Base):
    """Строка загруженного файла: ключ позиции и её остаток на момент снапшота.

    part_key дублируется строкой (а не FK на parts) — снапшот должен остаться
    читаемым даже при переименованиях/удалениях справочника в будущем.
    """

    __tablename__ = "stock_snapshot_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("stock_snapshots.id", ondelete="CASCADE"), index=True
    )
    part_key: Mapped[str] = mapped_column(String(256), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)


class PartAlias(Base):
    """Синоним названия: «сырая» строка из 1С → канонический parts.key.

    Нужна, когда одна позиция в разных файлах называется по-разному
    (опечатки, переименования): raw_name — точное исходное имя, part_key —
    куда мапим (None, пока алиас не привязан к детали — Этап 4),
    category_override — принудительная категория (этап 2).
    created_by — Telegram id админа, добавившего алиас (Этап 4, фасад
    add_alias; для алиасов из автоматических сценариев остаётся None).
    """

    __tablename__ = "part_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_name: Mapped[str] = mapped_column(String(512), unique=True)
    part_key: Mapped[str | None] = mapped_column(String(256), index=True)
    category_override: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class CompatRule(Base):
    """Правило совместимости комплектующих — движок «Конструктора» (этап 2+).

    Состав params зависит от rule_type и хранится JSON-ом (например
    «мотор×проп»: посадочный диаметр вала; «рама×мотор»: межлучевой размер).
    Сид стандартных правил — data/builder/seed_compat.py (идемпотентно по
    rule_type). is_active=False — правило отключено без удаления (проверки
    Этапа 5 его пропускают).
    """

    __tablename__ = "compat_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Recipe(Base):
    """Производственный рецепт сборки — ground truth совместимости (Этап 10).

    slots — JSON-список слотов [{category, part_key|null, name, qty}];
    part_key=None означает «имя из 1С не найдено в parts на момент сида»
    (неразрешённые имена не выдумываются — см. seed_recipes в
    db/repositories_builder.py). Из активных рецептов строится whitelist
    слотов (слой защиты L1) и расход остатков (слой L4).
    """

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    size: Mapped[int] = mapped_column(Integer)                     # дюймы борта
    title: Mapped[str] = mapped_column(String(255))                # имя конфигурации
    slots: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (UniqueConstraint("size", "title", name="uq_recipes_size_title"),)


class BuilderDraft(Base):
    """Черновик сборки «🛠 Конструктора» (Этап 4): у пользователя их несколько.

    config — JSON текущего выбора (рама, моторы, пропы, ...), name — подпись
    из UI (опциональна). Сохранение с draft_id обновляет запись (updated_at
    освежается), без draft_id — создаёт новый черновик (фасады
    db.repositories_builder: save_draft / list_drafts / get_draft /
    delete_draft). До Этапа 4 был «один черновик на пользователя»
    (user_id UNIQUE, без name) — старая схема мигрирует в db.init_db.
    """

    __tablename__ = "builder_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)  # Telegram id
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
