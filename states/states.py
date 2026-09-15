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