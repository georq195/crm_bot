from aiogram.filters.callback_data import CallbackData


class RequestCB(CallbackData, prefix="req"):
    action: str
    id: int = 0
    page: int = 0
    status: str = ""


class PeriodCB(CallbackData, prefix="per"):
    kind: str
    period: str


class ExportCB(CallbackData, prefix="exp"):
    fmt: str
    period: str
    status: str
