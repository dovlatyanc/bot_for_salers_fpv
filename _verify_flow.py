"""Временная E2E-проверка ветки «Не знаю» через реальный обработчик callback_selection."""
import asyncio

import bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class Msg:
    def __init__(self):
        self.text = None
        self.markup = None

    async def edit_text(self, text, reply_markup=None):
        self.text = text
        self.markup = reply_markup


class User:
    id = 42
    username = "tester"


class CB:
    def __init__(self, data):
        self.data = data
        self.message = Msg()
        self.from_user = User()

    async def answer(self, *a, **k):
        pass


def buttons(cb):
    if cb.message.markup is None:
        return "НЕТ КЛАВИАТУРЫ"
    return [[b.text for b in row] for row in cb.message.markup.inline_keyboard]


async def press(ctx, data):
    cb = CB(data)
    await bot.callback_selection(cb, ctx)
    return cb


async def main():
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)

    async def new_ctx(reset=True):
        ctx = FSMContext(storage=storage, key=key)
        if reset:
            await ctx.clear()
        return ctx

    # --- Сценарий 1: «Не знаю» -> 10" -> выбор рамы -> 6S -> результаты -> назад ---
    ctx = await new_ctx()
    await ctx.set_state(bot.SelectionState.step_a)
    cb = await press(ctx, "sel:a:unknown")
    print("=== Шаг «Не знаю» -> выбор дрона ===")
    print("state:", await ctx.get_state())
    print(cb.message.text)
    print(buttons(cb))

    cb = await press(ctx, "sel:dr:10")
    print("\n=== 10\" -> экран выбора рамы (рам 2, шаг показан) ===")
    print("state:", await ctx.get_state())
    print(cb.message.text)
    print(buttons(cb))

    cb = await press(ctx, "sel:fr:6S")
    print("\n=== 10\" / 6S -> результаты (5 моделей, шага назад к раме нет? есть) ===")
    print("state:", await ctx.get_state())
    print(cb.message.text)
    print(buttons(cb))

    cb = await press(ctx, "sel:frback")
    print("\n=== «Вернуться к выбору рамы» -> экран рамы ===")
    print("state:", await ctx.get_state())
    print(buttons(cb))

    cb = await press(ctx, "sel:fr:8S")
    print("\n=== 10\" / 8S -> результаты (1 модель + пояснение) ===")
    print("state:", await ctx.get_state())
    print(cb.message.text)
    print(buttons(cb))

    cb = await press(ctx, "sel:drback")
    print("\n=== «Выбрать другой дрон» -> экран дрона ===")
    print("state:", await ctx.get_state())
    print(buttons(cb))

    # --- Сценарий 2: дрон с одной рамой — шаг выбора рамы пропускается ---
    for drone, expect in (("7", 1), ("9", 1), ("12", 2), ("13", 4), ("15", 7), ("hex", 5)):
        ctx = await new_ctx()
        await ctx.set_state(bot.SelectionState.step_a)
        await press(ctx, "sel:a:unknown")
        cb = await press(ctx, f"sel:dr:{drone}")
        rows = buttons(cb)
        model_rows = [r for r in rows if not r[0].startswith("⬅️")]
        print(f'\n=== {bot.FILTER_LABELS["drone"][drone]}: шаг рамы пропущен, моделей {len(model_rows)} (ожидали {expect}) ===')
        print("state:", await ctx.get_state())
        print(cb.message.text)
        print(rows)

    # --- Сценарий 3: пустая комбинация дрон+рама (несуществующая рама) ---
    ctx = await new_ctx()
    await ctx.set_state(bot.SelectionState.step_frame)
    await ctx.update_data(drone="10")
    cb = await press(ctx, "sel:fr:2S")
    print("\n=== Пустая комбинация -> понятное сообщение + 2 кнопки ===")
    print("state:", await ctx.get_state())
    print(cb.message.text)
    print(buttons(cb))

    # --- Сценарий 4: потеря контекста (state=step_frame без drone) ---
    ctx = await new_ctx()
    await ctx.set_state(bot.SelectionState.step_frame)
    cb = await press(ctx, "sel:fr:6S")
    print("\n=== Потеря контекста -> перезапуск с выбора дрона ===")
    print("state:", await ctx.get_state())
    print(buttons(cb))

    # --- Сценарий 5: FSM-гварды не сломаны ---
    ctx = await new_ctx()
    await ctx.set_state(bot.SelectionState.step_a)
    cb = await press(ctx, "sel:dr:10")
    print("\n=== Гвард: sel:dr на шаге А игнорируется === черновик экрана:", cb.message.text)
    ctx = await new_ctx()
    await ctx.set_state(bot.SelectionState.step_drone)
    cb = await press(ctx, "sel:fr:6S")
    print("=== Гвард: sel:fr на шаге дрона игнорируется === черновик экрана:", cb.message.text)

    # --- Сценарий 6: «Начать заново» с экрана результатов ---
    ctx = await new_ctx()
    await ctx.set_state(bot.SelectionState.step_a)
    await press(ctx, "sel:a:unknown")
    await press(ctx, "sel:dr:12")
    cb = await press(ctx, "sel:restart")
    print("\n=== «Начать заново» с результатов ===")
    print("state:", await ctx.get_state())
    print(cb.message.text)
    print(buttons(cb))


asyncio.run(main())
