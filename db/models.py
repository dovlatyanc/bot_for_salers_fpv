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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс всех таблиц (create_all в db.init_db)."""


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
