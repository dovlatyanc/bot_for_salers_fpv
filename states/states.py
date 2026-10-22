from aiogram.fsm.state import State, StatesGroup


class FeedbackState(StatesGroup):
    waiting_for_feedback = State()


class BuildState(StatesGroup):
    waiting_for_config = State()
    waiting_for_connector = State()


class SelectionState(StatesGroup):
    step_a = State()
    step_b = State()
    step_c = State()
    step_drone = State()
    step_frame = State()


class BuilderState(StatesGroup):
    """FSM «🛠 Конструктора дронов» (Этапы 6, 10).

    Поток и контракт FSM-данных — в docstring handlers/builder.py:
    start → choosing_size (Этап 10) → slot_menu → part_list/part_input →
    (review →) drafts; серийный режим: start → choosing_size → recipe_list →
    recipe_card → (черновик → slot_menu).
    """

    start = State()
    choosing_size = State()
    recipe_list = State()
    recipe_card = State()
    slot_menu = State()
    part_list = State()
    part_input = State()
    review = State()
    drafts = State()