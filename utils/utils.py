from pathlib import Path

from aiogram import types
from aiogram.types import FSInputFile, InlineKeyboardMarkup

from config import BASE_DIR, system_logger
from data import MODELS


# ---------- фильтры ----------
def _apply_filters(drone=None, voltage=None, cap_class=None) -> dict:
    result = dict(MODELS)
    if drone and drone != "all":
        result = {c: m for c, m in result.items() if drone in m["drones"]}
    if voltage and voltage != "all":
        result = {c: m for c, m in result.items() if m["voltage"] == voltage}
    if cap_class and cap_class != "all":
        result = {c: m for c, m in result.items() if m["cap_class"] == cap_class}
    return result


# ---------- форматирование ----------
def get_row(model_code: str) -> str:
    return model_code[:2]


def models_by_row(row: str) -> list[str]:
    return [c for c in MODELS if c.startswith(row)]


def split_text(text: str, limit: int = 4000) -> list[str]:
    text = text or ""
    parts = []
    while len(text) > limit:
        cut = text[:limit]
        if "\n" in cut:
            cut = cut[:cut.rfind("\n")]
        parts.append(cut); text = text[len(cut):]
    if text:
        parts.append(text)
    return parts or [""]


def format_model(model_code: str) -> tuple[str, str | None]:
    m = MODELS[model_code]
    row = get_row(model_code)

    if row == "6S":
        nominal_v, max_charge_v, work_min_v, abs_min_v = 22.2, 25.2, 18.0, 15.0
        balance, length = "JST-XH 7-pin", 133
    else:
        nominal_v, max_charge_v, work_min_v, abs_min_v = 29.6, 33.6, 24.0, 20.0
        balance, length = "JST-XH 9-pin", 177

    connector = m.get("connector", "XT90-S" if row != "6S" else "XT60")
    wh = round(nominal_v * m["mah"] / 1000)

    text = f"""
<b>{model_code}</b>

Ёмкость: {m['mah']} мА·ч
Энергия: ~{wh} Вт·ч
Масса: {m['kg']} кг
Напряжение: {row} (Номинал: {nominal_v} В)

Ток длительно: {m['cont']} А
Ток пиково: {m['peak']} А
Ток заряда: {m['charge']} А
Сопротивление: {m['mohm']} мОм

Заряд не выше: {max_charge_v} В
В работе не ниже: {work_min_v} В
Абсолютный минимум: {abs_min_v} В

Разъём: {connector}
Баланс: {balance}
Габариты: {length} × {m['w']} × 74 мм

<b>Применение:</b>
{m['best']}
""".strip()

    if m.get("warn"):
        text += f"\n\n<i>Внимание: {m['warn']}</i>"

    return text, m.get("photo")


# ---------- отправка ----------
async def answer_long(message: types.Message, text: str):
    for part in split_text(text):
        await message.answer(part)


async def send_model_card(target, model_code: str, kb: InlineKeyboardMarkup, bot) -> None:
    """target — Message или CallbackQuery. bot передаём, чтобы не плодить циклические импорты."""
    text, photo_path = format_model(model_code)
    msg = target.message if isinstance(target, types.CallbackQuery) else target

    if photo_path:
        abs_path = Path(photo_path)
        if not abs_path.is_absolute():
            # Относительные пути из MODELS считаются от корня приложения (config.BASE_DIR),
            # а не от каталога этого модуля — запуск работает из любого cwd.
            abs_path = BASE_DIR / photo_path
        if abs_path.exists() and len(text) <= 1024:
            try:
                await bot.send_photo(
                    msg.chat.id, FSInputFile(abs_path),
                    caption=text, parse_mode="HTML", reply_markup=kb,
                )
                return
            except Exception as e:
                system_logger.warning(f"Model photo send failed: {e}")

    if isinstance(target, types.CallbackQuery):
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")