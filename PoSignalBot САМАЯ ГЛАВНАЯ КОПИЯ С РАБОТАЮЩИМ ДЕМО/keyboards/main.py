from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ALLOW_REAL_TRADING


# ============================================================
# MAIN MENU
# ============================================================

def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📊 Найти сигнал",
        callback_data="signal:start",
    )

    builder.button(
        text="📈 Статистика",
        callback_data="stats",
    )

    builder.button(
        text="⚙️ Настройки",
        callback_data="settings",
    )

    builder.button(
        text="ℹ️ Помощь",
        callback_data="help",
    )

    builder.adjust(1, 2, 1)

    return builder.as_markup()


# ============================================================
# ACCOUNT MODE
# ============================================================

def account_mode_menu() -> InlineKeyboardMarkup:
    """
    Выбор DEMO / REAL.
    """

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🟢 DEMO",
        callback_data="signal:mode:DEMO",
    )

    # Кнопка REAL показывается только если реальная торговля
    # явно разрешена в .env (ALLOW_REAL_TRADING=true).
    if ALLOW_REAL_TRADING:
        builder.button(
            text="🔴 REAL",
            callback_data="signal:mode:REAL",
        )

    builder.button(
        text="⬅️ Назад",
        callback_data="main",
    )

    if ALLOW_REAL_TRADING:
        builder.adjust(2, 1)
    else:
        builder.adjust(1, 1)

    return builder.as_markup()


# ============================================================
# EXPIRATION
# ============================================================

def expiration_menu(
    account_mode: str,
) -> InlineKeyboardMarkup:
    """
    Меню времени экспирации.

    account_mode передаём в callback, чтобы FSM для этого
    этапа не требовался.
    """

    mode = str(account_mode).upper()

    if mode not in {"DEMO", "REAL"}:
        mode = "DEMO"

    builder = InlineKeyboardBuilder()

    expirations = (
        (1, "1 минута"),
        (3, "3 минуты"),
        (5, "5 минут"),
        (10, "10 минут"),
        (20, "20 минут"),
        (60, "60 минут"),
    )

    for minutes, title in expirations:
        builder.button(
            text=f"⏱ {title}",
            callback_data=f"signal:exp:{mode}:{minutes}",
        )

    builder.button(
        text="⬅️ Назад",
        callback_data="signal:start",
    )

    builder.adjust(2, 2, 2, 1)

    return builder.as_markup()


# ============================================================
# SIGNAL ACTIONS
# ============================================================

def signal_actions(
    signal_id: int,
    account_mode: str,
    recommended_direction: str,
) -> InlineKeyboardMarkup:
    """
    Кнопки после найденного сигнала.

    recommended_direction — направление,
    которое рекомендует Signal Engine.
    """

    mode = str(account_mode).upper().strip()
    recommended = (
        str(recommended_direction)
        .upper()
        .strip()
    )

    if mode not in {"DEMO", "REAL"}:
        mode = "DEMO"

    if recommended not in {"CALL", "PUT"}:
        recommended = "CALL"

    builder = InlineKeyboardBuilder()

    # --------------------------------------------------------
    # Pocket Option chart
    # --------------------------------------------------------

    if mode == "REAL":
        chart_url = (
            "https://m.pocketoption.com/"
            "ru/cabinet/quick-high-low/"
        )
    else:
        chart_url = (
            "https://m.pocketoption.com/"
            "ru/cabinet/demo-quick-high-low/"
        )

    builder.button(
        text="📈 График (Pocket Option)",
        url=chart_url,
    )

    # --------------------------------------------------------
    # Direction buttons
    # --------------------------------------------------------

    call_text = "🟢 CALL"
    put_text = "🔴 PUT"

    if recommended == "CALL":
        call_text += " ⭐"
    else:
        put_text += " ⭐"

    builder.button(
        text=call_text,
        callback_data=(
            f"trade:{mode}:CALL:{signal_id}"
        ),
    )

    builder.button(
        text=put_text,
        callback_data=(
            f"trade:{mode}:PUT:{signal_id}"
        ),
    )

    # --------------------------------------------------------
    # Other actions
    # --------------------------------------------------------

    builder.button(
        text="🔄 Найти другой сигнал",
        callback_data=f"signal:mode:{mode}",
    )

    builder.button(
        text="🏠 Главное меню",
        callback_data="main",
    )

    builder.adjust(
        1,
        2,
        1,
        1,
    )

    return builder.as_markup()


# ============================================================
# REAL CONFIRMATION
# ============================================================

def real_confirmation(
    signal_id: int,
    direction: str,
) -> InlineKeyboardMarkup:
    """
    Второй экран подтверждения REAL.

    Сохраняем выбранное пользователем направление,
    чтобы после подтверждения TradeManager открыл
    именно CALL или PUT.
    """

    direction = str(
        direction
    ).upper().strip()

    if direction not in {"CALL", "PUT"}:
        direction = "CALL"

    builder = InlineKeyboardBuilder()

    builder.button(
        text="⚠️ ДА, ОТКРЫТЬ REAL",
        callback_data=(
            f"trade:real:confirm:"
            f"{direction}:{signal_id}"
        ),
    )

    builder.button(
        text="❌ Отмена",
        callback_data=f"trade:cancel:{signal_id}",
    )

    builder.adjust(1, 1)

    return builder.as_markup()


# ============================================================
# AFTER TRADE
# ============================================================

def trade_result_menu(
    account_mode: str = "DEMO",
) -> InlineKeyboardMarkup:
    """
    Кнопки после открытия сделки / результата.

    ИСПРАВЛЕНИЕ: раньше в файле было ДВЕ функции с этим именем.
    Вторая (без аргументов) перекрывала первую и отправляла
    callback_data "find_signal" и "statistics", для которых нет
    обработчиков, поэтому кнопки не работали. Теперь функция одна,
    account_mode необязателен, а callback_data рабочие
    ("signal:mode:<режим>", "stats", "main").
    """

    mode = str(account_mode).upper().strip()

    if mode not in {"DEMO", "REAL"}:
        mode = "DEMO"

    if mode == "REAL" and not ALLOW_REAL_TRADING:
        mode = "DEMO"

    builder = InlineKeyboardBuilder()

    # ВАЖНО: ведём на "signal:start", а не на "signal:mode:...".
    # Сообщение с результатом сделки содержит КАРТИНКУ (win/loss),
    # а у такого сообщения нельзя вызвать edit_text (Telegram:
    # "there is no text in the message to edit"). Обработчик
    # "signal:start" сам удаляет старое сообщение и отправляет новое,
    # поэтому работает и для картинок, и для текстовых сообщений.
    builder.button(
        text="📊 Найти следующий сигнал",
        callback_data="signal:start",
    )

    builder.button(
        text="📈 Статистика",
        callback_data="stats",
    )

    builder.button(
        text="🏠 Главное меню",
        callback_data="main",
    )

    builder.adjust(1, 1, 1)

    return builder.as_markup()


# ============================================================
# BACK
# ============================================================

def back_to_main() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🏠 Главное меню",
        callback_data="main",
    )

    return builder.as_markup()


def back_to_account_mode() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="⬅️ Назад",
        callback_data="signal:start",
    )

    return builder.as_markup()



# ============================================================
# REGISTRATION / ONBOARDING
# ============================================================

def registration_menu():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🔗 Зарегистрироваться в Pocket Option",
        url="https://pocket-friends.co/r/skqdpioy3j",
    )

    builder.adjust(1)

    return builder.as_markup()


def registration_continue_menu():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Я зарегистрировался",
        callback_data="onboarding:done",
    )

    builder.adjust(1)

    return builder.as_markup()

def signal_amount_menu(mode: str, expiration: int) -> InlineKeyboardMarkup:
    amounts = [1, 2, 5, 10]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💰 {amount}",
                    callback_data=(
                        f"signal:amount:{mode}:{expiration}:{amount}"
                    ),
                )
                for amount in amounts[:2]
            ],
            [
                InlineKeyboardButton(
                    text=f"💰 {amount}",
                    callback_data=(
                        f"signal:amount:{mode}:{expiration}:{amount}"
                    ),
                )
                for amount in amounts[2:]
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="main",
                )
            ],
        ]
    )