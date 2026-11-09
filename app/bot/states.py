from aiogram.fsm.state import State, StatesGroup


class CreateRequest(StatesGroup):
    name = State()
    contact = State()
    description = State()
    date = State()
    confirm = State()


class EditRequest(StatesGroup):
    field = State()
    value = State()


class AdminInput(StatesGroup):
    search = State()
    comment = State()
