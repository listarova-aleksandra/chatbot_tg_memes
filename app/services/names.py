"""Как показывать пользователя в списках: @username, а если ника нет, то имя."""


def display_name(username: str | None, first_name: str) -> str:
    return f"@{username}" if username else first_name
