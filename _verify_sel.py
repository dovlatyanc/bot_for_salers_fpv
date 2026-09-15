"""Временный скрипт проверки ветки «Не знаю» (дрон → рама → АКБ). Удаляется после проверки."""
import ast

# 1) Синтаксическая проверка
ast.parse(open("bot.py", encoding="utf-8").read())
print("ast.parse: OK")

# 2) Импорт реального модуля и проверка новой логики на актуальных данных MODELS
import bot

print("\nДроны в списке выбора:", bot._sel_drone_codes())
print("\nРамы по дронам (генерируются из MODELS):")
for d in bot._sel_drone_codes():
    print(f'  {bot.FILTER_LABELS["drone"][d]:<12} -> {bot._sel_drone_frames(d)}')

print('\n--- Дрон 10" по каждой раме ---')
for frame in bot._sel_drone_frames("10"):
    models = bot._apply_filters(drone="10", voltage=frame)
    print(f'  Рама {bot.FILTER_LABELS["voltage"][frame]}: {len(models)} модел(ь/и) -> {list(models)}')

print('\n--- Шаг выбора рамы показывается только там, где рам > 1 ---')
skip, show = [], []
for d in bot._sel_drone_codes():
    (skip if len(bot._sel_drone_frames(d)) == 1 else show).append(bot.FILTER_LABELS["drone"][d])
print("  Пропуск шага рамы (сразу АКБ):", skip)
print("  Показывают шаг рамы:", show)

print('\n--- Кнопки экранов ветки «Не знаю» ---')
print("  Экран выбора дрона:", [b.callback_data for r in bot._sel_step_drone_kb().inline_keyboard for b in r])
print("  Экран выбора рамы (10\"):", [b.callback_data for r in bot._sel_step_frame_kb('10').inline_keyboard for b in r])
print("  Пустой результат:", [b.callback_data for r in bot._sel_unk_none_kb().inline_keyboard for b in r])

print('\n--- Проверка пустой комбинации дрон+рама (данные) ---')
for d in bot._sel_drone_codes():
    for f in bot._sel_drone_frames(d):
        if not bot._apply_filters(drone=d, voltage=f):
            print("  ПУСТО:", d, f)
print("  пустых комбинаций в актуальных данных не найдено" if all(
    bot._apply_filters(drone=d, voltage=f) for d in bot._sel_drone_codes() for f in bot._sel_drone_frames(d)
) else "  есть пустые комбинации")
