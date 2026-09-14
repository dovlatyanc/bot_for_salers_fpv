import asyncio
import datetime
import os
import sys

ROOT = r'c:\Users\1\Desktop\Developments\Tg_Bot_for_salers'
sys.path.insert(0, ROOT)
import bot


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


# Подменяем aiogram-типы в модуле bot, чтобы isinstance-проверки send_model_card
# распознавали стабы (в проде используются настоящие aiogram-объекты)
bot.types.CallbackQuery = C
bot.types.Message = Msg


class FakeBot:
    def __init__(self):
        self.photos = []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.photos.append((chat_id, photo, caption, parse_mode, reply_markup))


real_bot = bot.bot
fake = FakeBot()
bot.bot = fake


def kb(rows):
    return bot.InlineKeyboardMarkup(inline_keyboard=[[bot.InlineKeyboardButton(text=t, callback_data=d)] for t, d in rows])


async def main():
    # 1. format_model -> кортеж (все 13 моделей сейчас с фото; None-ветка проверяется в п.6 подменой)
    text, photo = bot.format_model('6S2P')
    assert isinstance(text, str) and '<b>6S2P</b>' in text and photo.endswith('6s2p_8000.png'), (text[:30], photo)
    print('1. format_model tuple OK:', photo)

    # 2. карточка с фото через callback
    c = C('model:6S2P')
    await bot.send_model_card(c, '6S2P', kb([('x', 'y')]))
    (chat_id, photo_obj, caption, pm, kbm) = fake.photos[-1]
    assert chat_id == 42 and caption == text and pm == 'HTML'
    assert not c.message.edits and not c.message.answers, 'fallback не должен срабатывать при наличии фото'
    print('2. PHOTO CARD OK: chat_id=42, FSInputFile path exists:', os.path.isfile(photo_obj.path))

    # 3. карточка без фото -> edit_text (подменяем photo на несуществующий файл)
    saved = bot.MODELS['8S2P XT-60']['photo']
    bot.MODELS['8S2P XT-60']['photo'] = 'no/such/file.png'
    c2 = C('srchm:8S2P XT-60:d=all:v=all:c=all')
    await bot.send_model_card(c2, '8S2P XT-60', kb([('x', 'y')]))
    bot.MODELS['8S2P XT-60']['photo'] = saved
    assert len(fake.photos) == 1 and c2.message.edits, 'должен уйти в edit_text'
    print('3. NO-PHOTO FALLBACK OK (edit_text)')

    # 4. Message-таргет
    m = Msg()
    await bot.send_model_card(m, '8S2P XT-60', kb([('x', 'y')]))
    assert len(fake.photos) == 1 and m.answers, 'Message -> answer'
    print('4. MESSAGE TARGET OK (answer)')

    # 5. все три хендлера карточек идут через send_model_card
    c3 = C('model:6S2P')
    await bot.callback_model(c3)
    assert len(fake.photos) == 2 and fake.photos[-1][4].inline_keyboard[0][0].text == '⬅️ К списку 6S'
    # MODIFIED: ветка fltm: удалена вместе с каскадным фильтром (заменён FSM-диагностикой sel:)
    c5 = C('srchm:8S4P:d=hex:v=8S:c=all')
    await bot.callback_search_model(c5)
    assert len(fake.photos) == 3
    print('5. HANDLERS OK: model:, srchm: все через фото-путь')

    # 6. model: для XT-90-S (общее фото с XT-60) и длина подписи в лимите
    assert len(fake.photos[-1][2]) <= 1024
    c6 = C('model:8S2P XT-90-S')
    await bot.callback_model(c6)
    # MODIFIED: было 5 (с fltm:) — после удаления каскада карточек 4
    assert len(fake.photos) == 4 and fake.photos[-1][1].path.endswith('8s2p_8000.png')
    print('6. SHARED PHOTO OK: XT-90-S -> 8s2p_8000.png, caption', len(fake.photos[-1][2]), 'chars')

bot.bot = real_bot  # восстановить до запуска
asyncio.run(main())
bot.bot = real_bot

# вне сети: реальный bot.send_photo не вызывался (fake перехватил всё)
print('DONE')
