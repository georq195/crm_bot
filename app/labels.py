from app.db.models import Status

STATUS_LABELS: dict[Status, str] = {
    Status.new: "Новая",
    Status.in_progress: "В работе",
    Status.done: "Завершена",
    Status.rejected: "Отклонена",
}
