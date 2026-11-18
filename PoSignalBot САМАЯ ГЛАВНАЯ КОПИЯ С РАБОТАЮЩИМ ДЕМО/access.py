"""
Контроль доступа к боту.

Сейчас: пользоваться ботом могут только ADMIN_IDS.
Позже: сюда добавятся роли (например "user") и список
одобренных пользователей из БД. Остальной код должен
спрашивать get_role(), а не сравнивать ID напрямую.
"""

from __future__ import annotations

import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_NONE = "none"


def get_role(user_id: int | None) -> str:
    if user_id is not None and user_id in ADMIN_IDS:
        return ROLE_ADMIN

    return ROLE_NONE


def is_admin(user_id: int | None) -> bool:
    return get_role(user_id) == ROLE_ADMIN


class AccessMiddleware(BaseMiddleware):
    """
    Пропускает только пользователей с допустимой ролью.
    Подключается как outer-middleware на message и callback_query,
    поэтому срабатывает раньше любых обработчиков.
    """

    async def __call__(self, handler, event, data):

        user = getattr(event, "from_user", None)
        user_id = user.id if user is not None else None

        if get_role(user_id) != ROLE_NONE:
            return await handler(event, data)

        logger.warning(
            "ДОСТУП ЗАПРЕЩЁН: user_id=%s username=%s",
            user_id,
            getattr(user, "username", None),
        )

        if isinstance(event, CallbackQuery):
            await event.answer(
                "⛔ Доступ закрыт.",
                show_alert=True,
            )

        elif isinstance(event, Message):
            await event.answer("⛔ Доступ закрыт.")

        return None