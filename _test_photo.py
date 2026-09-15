"""Проверка фото-карточек: utils.send_model_card + хендлеры карточек (menu/search)."""
import asyncio
import os
import sys

ROOT = r'c:\Users\1\Desktop\Developments\Tg_Bot_for_salers'
sys.path.insert(0, ROOT)

import utils                       # noqa: E402
import handlers.menu as menu       # noqa: E402
import handlers.search as search   # noqa: E402
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: E402
from data import MODELS            # noqa: E402


class Chat:
    def __init__(self):
        self.id = 42


class Msg:
    def __init__(self):
        self.chat = Chat()
        self.edits = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append((text, reply_markup, parse_mode))

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup, parse_mode))


class C:
    def __init__(self, data):
        self.data = data
        self.message = Msg()

    async def answer(self, text=None, show_alert=False):
        pass


# Патчим общий модуль aiogram.types: isinstance-проверки send_model_card во всех
# модулях ссылаются на один и тот же объект-модуль (в проде — настоящие aiogram-объекты)
from aiogram import types as _aiotypes

_aiotypes.CallbackQuery = C
_aiotypes.Message = Msg


class FakeBot:
    def __init__(self):
        self.photos = []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.photos.append((chat_id, photo, caption, parse_mode, reply_markup))


fake = FakeBot()
menu.bot = fake    # хендлеры карточек берут бота из config — подменяем на fake
search.bot = fake


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t, d in rows])


async def main():
    # 1. format_model -> кортеж (все 13 моделей сейчас с фото; None-ветка проверяется в п.3 подменой)
    text, photo = utils.format_model('6S2P')
    assert isinstance(text, str) and '<b>6S2P</b>' in text and photo.endswith('6s2p_8000.png'), (text[:30], photo)
    print('1. format_model tuple OK:', photo)

    # 2. карточка с фото через send_model_card (путь от BASE_DIR, не от utils/)
    c = C('model:6S2P')
    await utils.send_model_card(c, '6S2P', kb([('x', 'y')]), fake)
    (chat_id, photo_obj, caption, pm, kbm) = fake.photos[-1]
    assert chat_id == 42 and caption == text and pm == 'HTML'
    assert not c.message.edits and not c.message.answers, 'fallback не должен срабатывать при наличии фото'
    print('2. PHOTO CARD OK: chat_id=42, FSInputFile path exists:', os.path.isfile(photo_obj.path))

    # 3. карточка без фото -> edit_text (подменяем photo на несуществующий файл)
    saved = MODELS['8S2P XT-60']['photo']
    MODELS['8S2P XT-60']['photo'] = 'no/such/file.png'
    c2 = C('srchm:8S2P XT-60:d=all:v=all:c=all')
    await utils.send_model_card(c2, '8S2P XT-60', kb([('x', 'y')]), fake)
    MODELS['8S2P XT-60']['photo'] = saved
    assert len(fake.photos) == 1 and c2.message.edits, 'должен уйти в edit_text'
    print('3. NO-PHOTO FALLBACK OK (edit_text)')

    # 4. Message-таргет + явное отсутствие фото (photo=None) -> answer
    m = Msg()
    saved2 = MODELS['8S2P XT-60']['photo']
    MODELS['8S2P XT-60']['photo'] = None
    await utils.send_model_card(m, '8S2P XT-60', kb([('x', 'y')]), fake)
    MODELS['8S2P XT-60']['photo'] = saved2
    assert len(fake.photos) == 1 and m.answers, 'Message -> answer'
    print('4. MESSAGE TARGET OK (answer, photo=None)')

    # 5. оба хендлера карточек идут через send_model_card (фото-путь)
    c3 = C('model:6S2P')
    await menu.callback_model(c3)
    assert len(fake.photos) == 2 and fake.photos[-1][4].inline_keyboard[0][0].text == '⬅️ К списку 6S'
    c5 = C('srchm:8S4P:d=hex:v=8S:c=all')
    await search.callback_search_model(c5)
    assert len(fake.photos) == 3
    print('5. HANDLERS OK: model:, srchm: все через фото-путь')

    # 6. model: для XT-90-S (общее фото с XT-60) и длина подписи в лимите
    assert len(fake.photos[-1][2]) <= 1024
    c6 = C('model:8S2P XT-90-S')
    await menu.callback_model(c6)
    assert len(fake.photos) == 4 and str(fake.photos[-1][1].path).endswith('8s2p_8000.png')
    print('6. SHARED PHOTO OK: XT-90-S -> 8s2p_8000.png, caption', len(fake.photos[-1][2]), 'chars')


asyncio.run(main())

# вне сети: реальный bot.send_photo не вызывался (fake перехватил всё)
print('DONE')
