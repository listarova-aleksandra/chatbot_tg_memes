"""Конструкторы «сырых» апдейтов Telegram для тестов."""

from typing import Any

FROM = {"id": 42, "is_bot": False, "first_name": "Аня", "username": "anya"}
CHAT = {"id": 42, "type": "private"}


def message_update(text: str, update_id: int = 1) -> dict[str, Any]:
    update: dict[str, Any] = {
        "update_id": update_id,
        "message": {"message_id": 1, "date": 0, "chat": CHAT, "from": FROM, "text": text},
    }
    if text.startswith("/"):
        length = len(text.split()[0])
        update["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": length}]
    return update


def callback_update(data: str, update_id: int = 2, *, photo: bool = False) -> dict[str, Any]:
    """photo=True: кнопка нажата под сообщением-фото (вопрос с картинкой)."""
    message: dict[str, Any] = {
        "message_id": 7,
        "date": 0,
        "chat": CHAT,
        "from": {**FROM, "is_bot": True},
    }
    if photo:
        message["photo"] = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
        message["caption"] = "caption"
    else:
        message["text"] = "menu"
    return {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "from": FROM,
            "chat_instance": "ci",
            "data": data,
            "message": message,
        },
    }


def photo_update(update_id: int = 1, file_id: str = "user-photo", file_size: int = 1000) -> dict[str, Any]:
    """Пользователь прислал фото."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1, "date": 0, "chat": CHAT, "from": FROM,
            "photo": [{"file_id": file_id, "file_unique_id": "u", "width": 100, "height": 100, "file_size": file_size}],
        },
    }


def sticker_update(update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1, "date": 0, "chat": CHAT, "from": FROM,
            "sticker": {"file_id": "s", "file_unique_id": "su", "type": "regular", "width": 1, "height": 1,
                        "is_animated": False, "is_video": False},
        },
    }
