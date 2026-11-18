from aiogram.fsm.state import State, StatesGroup


class SignalStates(StatesGroup):
    choosing_expiration = State()
    searching_signal = State()