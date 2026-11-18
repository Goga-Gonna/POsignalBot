from __future__ import annotations
from pathlib import Path
from aiogram.types import FSInputFile
import inspect
import logging
import os
from typing import Any
import asyncio

from aiogram.exceptions import TelegramBadRequest
from aiogram import BaseMiddleware, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from access import AccessMiddleware, is_admin
from config import ALLOW_REAL_TRADING
from database import (
    get_signal,
    get_statistics,
    save_signal,
    is_user_onboarded,
    complete_user_onboarding,
    get_user_settings,
    update_user_settings,
)
from keyboards.main import (
    account_mode_menu,
    back_to_main,
    expiration_menu,
    main_menu,
    real_confirmation,
    signal_actions,
    trade_result_menu,
    registration_menu,
    registration_continue_menu,
    signal_amount_menu,
)
from services.trade_manager import (
    TradeExecutionResult,
    open_trade as _raw_open_trade,
)
from signals.engine import find_best_signal
from market.pocket_option import get_pocket_option

logger = logging.getLogger(__name__)

router = Router()

# ============================================================
# IMAGES
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "images"

MAIN_IMAGE = IMAGES_DIR / "main.jpg"
WIN_IMAGE = IMAGES_DIR / "win.jpg"
LOSS_IMAGE = IMAGES_DIR / "loss.jpg"

# ID главной фотографии для каждого чата.
_main_photo_messages: dict[int, int] = {}


async def _delete_main_photo(message: Message) -> None:
    """Удаляет фотографию главного меню, если она ещё существует."""
    photo_message_id = _main_photo_messages.pop(message.chat.id, None)

    if photo_message_id is None:
        return

    try:
        await message.bot.delete_message(
            chat_id=message.chat.id,
            message_id=photo_message_id,
        )
    except TelegramBadRequest:
        logger.debug(
            "Главная фотография уже удалена или недоступна."
        )


async def _send_main_photo(message: Message) -> None:
    """Отправляет фотографию главного меню."""
    if not MAIN_IMAGE.is_file():
        logger.error("Не найден файл главного меню: %s", MAIN_IMAGE)
        return

    photo_message = await message.answer_photo(
        photo=FSInputFile(MAIN_IMAGE),
    )

    _main_photo_messages[message.chat.id] = photo_message.message_id

# ============================================================
# ONBOARDING ACCESS GUARD
# ============================================================

class OnboardingCallbackGuard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Администратор проходит без регистрации.
        if is_admin(event.from_user.id):
            return await handler(event, data)

        # Разрешаем обработку кнопки подтверждения регистрации.
        if event.data == "onboarding:done":
            return await handler(event, data)

        # Остальные callback-кнопки доступны только после регистрации.
        if not is_user_onboarded(event.from_user.id):
            await event.answer(
                "🔒 Сначала завершите регистрацию и подтвердите её.",
                show_alert=True,
            )
            return

        return await handler(event, data)


# ВАЖНО: AccessMiddleware регистрируется ПЕРВЫМ. Пока пользователь
# не прошёл проверку доступа, ни один обработчик не выполняется.
router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())

router.callback_query.outer_middleware(OnboardingCallbackGuard())


# ============================================================
# REAL TRADING GUARD
# ============================================================

_REAL_DISABLED_TEXT = (
    "🔒 <b>REAL-торговля отключена.</b>\n\n"
    "Реальные сделки заблокированы администратором "
    "(ALLOW_REAL_TRADING=false). Доступен режим DEMO."
)


async def _reject_real(callback: CallbackQuery) -> None:
    """Показывает сообщение о блокировке REAL (callback уже отвечен)."""

    logger.warning(
        "REAL заблокирован: user_id=%s data=%s",
        callback.from_user.id,
        callback.data,
    )

    if callback.message:
        await callback.message.edit_text(
            _REAL_DISABLED_TEXT,
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )


async def open_trade(
    signal_id: int,
    *,
    direction: str | None = None,
    confirmed_real: bool = False,
) -> TradeExecutionResult:
    """
    Обёртка над TradeManager.open_trade().

    Последний рубеж защиты: если реальная торговля не разрешена,
    сделка по сигналу с account_mode=REAL не отправляется в брокер,
    даже если кто-то вызвал эту функцию в обход меню.
    """

    if not ALLOW_REAL_TRADING:
        signal = get_signal(signal_id)

        if (
            signal is not None
            and str(signal.get("account_mode") or "DEMO").upper() == "REAL"
        ):
            logger.error(
                "REAL-сделка заблокирована обёрткой: signal_id=%s",
                signal_id,
            )

            return TradeExecutionResult.failed(
                signal_id,
                "REAL-торговля отключена (ALLOW_REAL_TRADING=false).",
                account_mode="REAL",
            )

    return await _raw_open_trade(
        signal_id,
        direction=direction,
        confirmed_real=confirmed_real,
    )

# ============================================================
# CONFIG
# ============================================================

try:
    DEFAULT_TRADE_AMOUNT = float(
        os.getenv("TRADE_AMOUNT", "1")
    )
except (TypeError, ValueError):
    DEFAULT_TRADE_AMOUNT = 1.0


# ============================================================
# REGISTRATION CONFIRMATION
# ============================================================

@router.callback_query(F.data == "onboarding:done")
async def onboarding_done(callback: CallbackQuery):
    user_id = callback.from_user.id

    # Проверяем, что с момента /start прошло не менее 15 секунд.

    # Проверяем реальное время ожидания.
    started_at = _onboarding_started_at.get(user_id)

    if (
        started_at is None
        or asyncio.get_running_loop().time() - started_at < 15
    ):
        await callback.answer(
            "⏳ Сначала дождитесь окончания 15 секунд.",
            show_alert=True,
        )
        return

    # Сохраняем завершение регистрации в SQLite.
    complete_user_onboarding(user_id)

    # Подтверждаем нажатие кнопки.
    await callback.answer("Регистрация подтверждена!")

    if callback.message:
        # Удаляем сообщение регистрации вместе с кнопкой.
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            logger.warning(
                "Не удалось удалить сообщение регистрации пользователя %s",
                user_id,
            )

        # Сразу отправляем отдельное приветствие.
        await callback.message.answer("👋")

        # Ждём 1,5 секунды.
        await asyncio.sleep(1.5)

        # Отправляем фото вместе с текстом и inline-кнопками.
        if MAIN_IMAGE.is_file():
            await callback.message.answer_photo(
                photo=FSInputFile(MAIN_IMAGE),
                caption=(
                    "🚀 <b>PO SIGNAL BOT</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    "👋 <b>Добро пожаловать!</b>\n\n"
                    "📊 Твой помощник в мире "
                    '<b><a href="https://pocket-friends.co/r/skqdpioy3j">'
                    "Pocket Option</a></b>.\n"
                    "🎯 Получай торговые сигналы и следи за результатами сделок.\n\n"
                    "⚡ <b>Всё необходимое — в одном месте:</b>\n"
                    "▫️ 🔍 Поиск торговых сигналов\n"
                    "▫️ 💹 Работа с DEMO и REAL счетами\n"
                    "▫️ 📈 Статистика и результаты сделок\n"
                    "▫️ ⚙️ Настройки бота\n\n"
                    "━━━━━━━━━━━━━━\n"
                    "👇 <b>Выбери нужное действие:</b>"
                ),
                reply_markup=main_menu(),
                parse_mode="HTML",
            )
        else:
            logger.error("Не найден файл главного меню: %s", MAIN_IMAGE)


# ============================================================
# HELPERS
# ============================================================

async def _maybe_await(value: Any) -> Any:
    """
    Позволяет работать как с обычной функцией,
    так и с async find_best_signal().
    """

    if inspect.isawaitable(value):
        return await value

    return value


def _get_value(
    data: Any,
    *keys: str,
    default: Any = None,
) -> Any:
    """
    Получение значения из dict/object.
    """

    if data is None:
        return default

    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]

        return default

    for key in keys:
        try:
            value = getattr(data, key, None)

            if value is not None:
                return value

        except Exception:
            pass

    return default


def _format_number(value: Any, digits: int = 5) -> str:
    try:
        number = float(value)

        text = f"{number:.{digits}f}"

        text = text.rstrip("0").rstrip(".")

        return text

    except (TypeError, ValueError):
        return str(value)


def _normalize_signal(
    signal: Any,
    expiration: int,
) -> dict[str, Any] | None:
    """
    Приводит результат Signal Engine к единому формату.

    Ожидаемые поля:
        symbol
        direction
        score
        price
        payout
    """

    if signal is None:
        return None

    symbol = _get_value(
        signal,
        "symbol",
        "asset",
        "pair",
    )

    direction = _get_value(
        signal,
        "direction",
        "signal",
        "action",
    )

    score = _get_value(
        signal,
        "score",
        "confidence",
        default=0,
    )

    price = _get_value(
        signal,
        "price",
        "entry_price",
        "entryPrice",
    )

    payout = _get_value(
        signal,
        "payout",
        "profitability",
        default=None,
    )

    # «Сигнала нет» это штатный ответ движка, а не ошибка.
    if (
        isinstance(signal, dict)
        and signal.get("status") in {"no_signal", "api_error"}
    ):
        logger.info(
            "Signal Engine: сигнала нет (status=%s, %s)",
            signal.get("status"),
            signal.get("reason") or signal.get("error") or "",
        )

        return None

    if not symbol or not direction:
        logger.error(
            "Signal Engine вернул некорректный сигнал: %r",
            signal,
        )

        return None

    direction = str(direction).upper().strip()

    if direction not in {"CALL", "PUT"}:
        logger.error(
            "Некорректное направление сигнала: %r",
            direction,
        )

        return None

    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0

    try:
        price = float(price)
    except (TypeError, ValueError):
        price = 0.0

    if payout is not None:
        try:
            payout = int(float(payout))
        except (TypeError, ValueError):
            pass

    return {
        "symbol": str(symbol),
        "direction": direction,
        "score": score,
        "price": price,
        "payout": payout,
        "expiration": expiration,
    }


async def _find_signal(
    expiration: int,
) -> dict[str, Any] | None:
    """
    Вызывает существующий Signal Engine.

    Поддерживает как:
        find_best_signal(expiration)

    так и:
        find_best_signal(expiration=expiration)
    """

    try:
        try:
            result = find_best_signal(
                expiration,
            )

        except TypeError:
            result = find_best_signal(
                expiration=expiration,
            )

        result = await _maybe_await(result)

        return _normalize_signal(
            result,
            expiration,
        )

    except Exception:
        logger.exception(
            "Ошибка поиска сигнала. expiration=%s",
            expiration,
        )

        return None


def _mode_title(mode: str) -> str:
    return "🟢 DEMO" if mode == "DEMO" else "🔴 REAL"


# ============================================================
# REGISTRATION TIMER
# ============================================================

_onboarding_tasks: dict[int, asyncio.Task] = {}
_onboarding_started_at: dict[int, float] = {}

async def _enable_onboarding_continue(
    bot,
    chat_id: int,
    message_id: int,
    user_id: int,
) -> None:
    """
    Через 15 секунд показывает кнопку подтверждения регистрации.
    Таймер выполняется в фоновой asyncio-задаче.
    """

    try:
        await asyncio.sleep(15)

        # Если пользователь уже прошёл регистрацию,
        # дополнительная кнопка ему не нужна.
        if is_user_onboarded(user_id):
            return

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                "🔐 <b>Регистрация в PoSignalBot</b>\n\n"
                "Если вы зарегистрировались в Pocket Option "
                "по нашей ссылке, нажмите кнопку ниже.\n\n"
                "✅ Теперь можно продолжить."
            ),
            reply_markup=registration_continue_menu(),
            parse_mode="HTML",
        )

    except Exception:
        logging.exception(
            "Не удалось показать кнопку регистрации "
            "пользователю %s",
            user_id,
        )

    finally:
        _onboarding_tasks.pop(user_id, None)


# ============================================================
# START
# ============================================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id


    # Администратор или пользователь, прошедший регистрацию,
    # сразу видит главное меню с фото, подписью и кнопками.
    if is_admin(user_id) or is_user_onboarded(user_id):
        if MAIN_IMAGE.is_file():
            await message.answer_photo(
                photo=FSInputFile(MAIN_IMAGE),
                caption=(
                    "🚀 <b>PO SIGNAL BOT</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    "👋 <b>Добро пожаловать!</b>\n\n"
                    "📊 Твой помощник в мире "
                    '<b><a href="https://pocket-friends.co/r/skqdpioy3j">'
                    "Pocket Option</a></b>.\n"
                    "🎯 Получай торговые сигналы и следи за результатами сделок.\n\n"
                    "⚡ <b>Всё необходимое — в одном месте:</b>\n"
                    "▫️ 🔍 Поиск торговых сигналов\n"
                    "▫️ 💹 Работа с DEMO и REAL счетами\n"
                    "▫️ 📈 Статистика и результаты сделок\n"
                    "▫️ ⚙️ Настройки бота\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "👇 <b>Выбери нужное действие:</b>"
                ),
                reply_markup=main_menu(),
                parse_mode="HTML",
            )
        else:
            logger.error(
                "Не найден файл главного меню: %s",
                MAIN_IMAGE,
            )
            await message.answer(
                "❌ Не найдено изображение главного меню."
            )

        return


    # Если пользователь повторно запускает /start,
    # отменяем предыдущий таймер.
    previous_task = _onboarding_tasks.get(user_id)

    if previous_task and not previous_task.done():
        previous_task.cancel()

    # Показываем экран регистрации.
    onboarding_message = await message.answer(
        "👋 <b>Добро пожаловать в PoSignalBot!</b>\n\n"
        "Для продолжения зарегистрируйтесь в Pocket Option "
        "по нашей партнёрской ссылке:\n\n"
        "🔗 Нажмите кнопку ниже и завершите регистрацию.\n\n"
        "⏳ Через 15 секунд появится кнопка продолжения.\n\n"
        "<i>Важно: бот не может автоматически проверить факт "
        "регистрации на сайте. Подтверждение выполняется вами.</i>",
        reply_markup=registration_menu(),
        parse_mode="HTML",
    )

    _onboarding_started_at[user_id] = (
        asyncio.get_running_loop().time()
    )

    # Запускаем таймер в фоновой задаче.
    task = asyncio.create_task(
        _enable_onboarding_continue(
            bot=message.bot,
            chat_id=onboarding_message.chat.id,
            message_id=onboarding_message.message_id,
            user_id=user_id,
        )
    )

    _onboarding_tasks[user_id] = task



# ============================================================
# MAIN MENU
# ============================================================


@router.callback_query(F.data == "main")
async def callback_main(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    if callback.message is None:
        return

    # Удаляем текущее сообщение раздела или меню.
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        logger.warning(
            "Не удалось удалить предыдущее сообщение главного меню."
        )

    # Проверяем наличие изображения.
    if not MAIN_IMAGE.is_file():
        logger.error(
            "Не найден файл главного меню: %s",
            MAIN_IMAGE,
        )
        await callback.message.answer(
            "❌ Не найдено изображение главного меню."
        )
        return

    # Возвращаем главное меню: фото + подпись + кнопки.
    photo_message = await callback.message.answer_photo(
        photo=FSInputFile(MAIN_IMAGE),
        caption=(
            "🚀 <b>PO SIGNAL BOT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "👋 <b>Добро пожаловать!</b>\n\n"
            "📊 Твой помощник в мире "
            '<b><a href="https://pocket-friends.co/r/skqdpioy3j">'
            "Pocket Option</a></b>.\n"
            "🎯 Получай торговые сигналы и следи за результатами сделок.\n\n"
            "⚡ <b>Всё необходимое — в одном месте:</b>\n"
            "▫️ 🔍 Поиск торговых сигналов\n"
            "▫️ 💹 Работа с DEMO и REAL счетами\n"
            "▫️ 📈 Статистика и результаты сделок\n"
            "▫️ ⚙️ Настройки бота\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👇 <b>Выбери нужное действие:</b>"
        ),
        reply_markup=main_menu(),
        parse_mode="HTML",
    )

    # Запоминаем фотографию главного меню.
    _main_photo_messages[callback.message.chat.id] = (
        photo_message.message_id
    )




# ============================================================
# FIND SIGNAL — STEP 1
# ============================================================

@router.callback_query(F.data == "signal:start")
async def signal_start(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    if callback.message:
        # Удаляем главное меню с фотографией.
        await callback.message.delete()

        # Отправляем новый текстовый экран выбора счёта.
        await callback.message.answer(
            "📊 <b>Поиск торгового сигнала</b>\n\n"
            "Сначала выберите счёт, на котором "
            "будет открыта сделка:",
            reply_markup=account_mode_menu(),
            parse_mode="HTML",
        )



# ============================================================
# FIND SIGNAL — STEP 2: ACCOUNT
# ============================================================

@router.callback_query(
    F.data.startswith("signal:mode:")
)
async def signal_select_mode(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    mode = callback.data.split(":")[-1].upper()

    if mode not in {"DEMO", "REAL"}:
        await callback.message.edit_text(
            "❌ Некорректный режим счёта.",
            reply_markup=back_to_main(),
        )

        return

    if mode == "REAL" and not ALLOW_REAL_TRADING:
        await _reject_real(callback)
        return

    settings = get_user_settings(callback.from_user.id)
    trading_mode = str(settings.get("trading_mode", "MANUAL")).upper()

    if mode == "REAL":
        if trading_mode == "AUTO":
            text = (
                "🔴 <b>REAL-счёт — AUTO</b>\n\n"
                "После анализа бот автоматически попытается открыть "
                "сделку в направлении найденного сигнала без отдельного "
                "подтверждения. Реальные сделки несут риск потери денег.\n\n"
                "👇 Выберите срок экспирации:"
            )
        else:
            text = (
                "🔴 <b>REAL-счёт — MANUAL</b>\n\n"
                "После нахождения сигнала вы выберете направление сделки, "
                "а перед открытием REAL бот попросит дополнительное подтверждение.\n\n"
                "👇 Выберите срок экспирации:"
            )

    else:
        text = (
            "<b>🟢 PO SIGNAL — DEMO MODE</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💼 Режим торговли: DEMO\n\n" 
            "🔹 Платформа: Pocket Option\n\n"
            "🛡️ Статус: Демо-счёт активен\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Укажите время, через которое должна завершиться сделка.\n\n"
            "Бот подберёт торговый сигнал с учётом выбранного периода.\n\n"
            "👇 Выберите срок экспирации ниже:"
        )

    if mode == "REAL":
        await callback.message.edit_text(
            text,
            reply_markup=expiration_menu(mode),
            parse_mode="HTML",
        )

    else:
        await callback.message.edit_text(
            text,
            reply_markup=expiration_menu(mode),
            parse_mode="HTML",
        )


# ============================================================
# FIND SIGNAL — STEP 3: EXPIRATION
# ============================================================

@router.callback_query(F.data.startswith("signal:exp:"))
async def signal_select_expiration(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.message.edit_text(
            "❌ Некорректные параметры поиска.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    mode = parts[2].upper()
    try:
        expiration = int(parts[3])
    except ValueError:
        await callback.message.edit_text(
            "❌ Некорректное время экспирации.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    if mode not in {"DEMO", "REAL"}:
        await callback.message.edit_text(
            "❌ Некорректный режим счёта.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    if expiration not in {1, 3, 5, 10, 20, 60}:
        await callback.message.edit_text(
            "❌ Такого времени экспирации нет в меню.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    settings = get_user_settings(callback.from_user.id)
    trading_mode = str(settings.get("trading_mode", "MANUAL")).upper()

    if trading_mode == "AUTO":
        # В AUTO сумма берётся из настроек, без отдельного выбора на каждый сигнал.
        trade_amount = float(settings.get("trade_amount", 1))
        await _run_signal_search(
            callback,
            mode=mode,
            expiration=expiration,
            trade_amount=trade_amount,
            trading_mode="AUTO",
        )
        return

    # В MANUAL сумму выбирают для конкретной сделки.
    await callback.message.edit_text(
        "💰 <b>ВЫБЕРИТЕ СУММУ СДЕЛКИ</b>\n\n"
        f"💼 Счёт: <b>{_mode_title(mode)}</b>\n"
        f"⏱️ Экспирация: <b>{expiration} мин.</b>\n\n"
        "Выберите сумму. После этого начнётся анализ рынка:",
        reply_markup=signal_amount_menu(mode, expiration),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("signal:amount:"))
async def signal_select_amount(
    callback: CallbackQuery,
) -> None:
    await callback.answer("Ищу лучший сигнал...")

    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.message.edit_text(
            "❌ Некорректные параметры поиска.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    mode = parts[2].upper()
    try:
        expiration = int(parts[3])
        trade_amount = int(parts[4])
    except ValueError:
        await callback.message.edit_text(
            "❌ Некорректная экспирация или сумма.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    if mode not in {"DEMO", "REAL"}:
        await callback.message.edit_text(
            "❌ Некорректный режим счёта.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    if expiration not in {1, 3, 5, 10, 20, 60}:
        await callback.message.edit_text(
            "❌ Некорректное время экспирации.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    if trade_amount not in {1, 2, 5, 10}:
        await callback.message.edit_text(
            "❌ Некорректная сумма сделки.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    await _run_signal_search(
        callback,
        mode=mode,
        expiration=expiration,
        trade_amount=float(trade_amount),
        trading_mode="MANUAL",
    )


async def _run_signal_search(
    callback: CallbackQuery,
    *,
    mode: str,
    expiration: int,
    trade_amount: float,
    trading_mode: str,
) -> None:
    """Ищет сигнал, сохраняет его и выполняет ручной или автоматический сценарий."""
    if mode == "REAL" and not ALLOW_REAL_TRADING:
        await _reject_real(callback)
        return

    await callback.message.edit_text(
        "🔍 <b>АНАЛИЗ РЫНКА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💼 <b>Торговый счёт:</b> {_mode_title(mode)}\n"
        f"🎮 <b>Режим:</b> {'AUTO' if trading_mode == 'AUTO' else 'MANUAL'}\n"
        f"⏱️ <b>Экспирация:</b> {expiration} мин.\n"
        f"💰 <b>Сумма сделки:</b> {trade_amount:g}\n"
        "📡 <b>Источник данных:</b> Pocket Option\n\n"
        "⏳ <i>Пожалуйста, дождитесь результатов поиска...</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔍 <i>Ищем лучший сигнал...</i>",
        parse_mode="HTML",
    )

    signal = await _find_signal(expiration)
    if signal is None:
        await callback.message.edit_text(
            "😔 <b>Подходящий сигнал не найден.</b>\n\n"
            "Попробуйте другое время экспирации.",
            reply_markup=expiration_menu(mode),
            parse_mode="HTML",
        )
        return

    user_id = callback.from_user.id
    try:
        signal_id = save_signal(
            user_id=user_id,
            symbol=signal["symbol"],
            direction=signal["direction"],
            score=signal["score"],
            expiration=signal["expiration"],
            price=signal["price"],
            account_mode=mode,
            trade_enabled=1,
            trade_amount=trade_amount,
        )
    except Exception:
        logger.exception("Не удалось сохранить сигнал.")
        await callback.message.edit_text(
            "❌ <b>Ошибка сохранения сигнала.</b>\n\n"
            "Попробуйте ещё раз.",
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )
        return

    direction_icon = "🟢 CALL" if signal["direction"] == "CALL" else "🔴 PUT"
    payout_text = (
        f"\n💰 Выплата: <b>{signal['payout']}%</b>"
        if signal.get("payout") is not None
        else ""
    )

    if trading_mode == "AUTO":
        # Пользователь заранее включил AUTO. Для REAL передаём подтверждение
        # явно в сервис торговли, чтобы не показывать отдельную кнопку.
        await callback.message.edit_text(
            "🎯 <b>СИГНАЛ НАЙДЕН — AUTO</b>\n\n"
            f"💱 Актив: <b>{signal['symbol']}</b>\n"
            f"📍 Направление алгоритма: <b>{direction_icon}</b>\n"
            f"⏱ Экспирация: <b>{expiration} мин.</b>\n"
            f"💰 Сумма: <b>{trade_amount:g}</b>\n"
            f"💳 Счёт: <b>{_mode_title(mode)}</b>\n\n"
            "⚡ <i>Пытаюсь автоматически открыть сделку...</i>",
            parse_mode="HTML",
        )

        result = await open_trade(
            signal_id,
            direction=signal["direction"],
            confirmed_real=(mode == "REAL"),
        )

        if not result.success:
            await callback.message.edit_text(
                "❌ <b>AUTO: СДЕЛКА НЕ ОТКРЫТА</b>\n\n"
                f"💱 Актив: <b>{signal['symbol']}</b>\n"
                f"📍 Направление: <b>{direction_icon}</b>\n"
                f"💳 Счёт: <b>{_mode_title(mode)}</b>\n"
                f"💰 Сумма: <b>{trade_amount:g}</b>\n\n"
                f"Причина: {result.error or 'Неизвестная ошибка.'}",
                reply_markup=trade_result_menu(),
                parse_mode="HTML",
            )
            return

        entry_price = (
            _format_number(result.entry_price)
            if result.entry_price is not None
            else "не определена"
        )
        await callback.message.edit_text(
            "✅ <b>AUTO: СДЕЛКА ОТКРЫТА</b>\n\n"
            f"💱 Актив: <b>{result.symbol}</b>\n"
            f"📍 Направление: <b>{result.direction}</b>\n"
            f"💳 Счёт: <b>{_mode_title(mode)}</b>\n"
            f"💰 Сумма: <b>{result.amount:g}</b>\n"
            f"💵 Цена входа: <b>{entry_price}</b>\n"
            f"⏱ Экспирация: <b>{result.expiration} мин.</b>\n\n"
            "⏳ Результат будет проверен автоматически после экспирации.",
            reply_markup=trade_result_menu(),
            parse_mode="HTML",
        )
        return

    text = (
        "🎯 <b>СИГНАЛ НАЙДЕН</b>\n\n"
        f"💱 Актив: <b>{signal['symbol']}</b>\n"
        f"📍 Направление: <b>{direction_icon}</b>\n"
        f"⏱ Экспирация: <b>{expiration} мин.</b>\n"
        f"💵 Цена входа: <b>{_format_number(signal['price'])}</b>\n"
        f"📊 Оценка сигнала: <b>{signal['score']}</b>"
        f"{payout_text}\n\n"
        f"💳 Счёт: <b>{_mode_title(mode)}</b>\n"
        f"💰 Сумма сделки: <b>{trade_amount:g}</b>\n\n"
        "Сделка пока <b>НЕ открыта</b>."
    )
    await callback.message.answer(
        text,
        reply_markup=signal_actions(signal_id, mode, signal["direction"]),
        parse_mode="HTML",
    )


# ============================================================
# DEMO TRADE
# ============================================================

@router.callback_query(
    F.data.startswith("trade:DEMO:")
)
async def open_demo_trade(
    callback: CallbackQuery,
) -> None:

    parts = callback.data.split(":")

    if len(parts) != 4:
        await callback.answer(
            "Некорректный запрос.",
            show_alert=True,
        )

        return

    mode = parts[1].upper()
    direction = parts[2].upper()

    try:
        signal_id = int(parts[3])

    except (TypeError, ValueError):
        await callback.answer(
            "Некорректный ID сигнала.",
            show_alert=True,
        )

        return

    if mode != "DEMO":
        await callback.answer(
            "Некорректный режим.",
            show_alert=True,
        )

        return

    if direction not in {"CALL", "PUT"}:
        await callback.answer(
            "Некорректное направление.",
            show_alert=True,
        )

        return

    await callback.answer(
        f"Открываю DEMO {direction}..."
    )

    # --------------------------------------------------------
    # DEMO-сделка открывается сразу.
    # Передаём выбранное пользователем направление.
    # --------------------------------------------------------

    result = await open_trade(
        signal_id,
        direction=direction,
        confirmed_real=False,
    )
    
    if not result.success:
        await callback.message.edit_text(
            "🔴 <b>PO SIGNAL | СДЕЛКА НЕ ОТКРЫТА</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💼 <b>Режим:</b> DEMO\n"
            "⚠️ <b>Не удалось открыть сделку</b>\n\n"
            f"📝 Причина: {result.error or 'Неизвестная ошибка.'}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Проверьте подключение и попробуйте ещё раз.",
            reply_markup=trade_result_menu(),
            parse_mode="HTML",
        )
        return

    entry_price = (
        _format_number(result.entry_price)
        if result.entry_price is not None
        else "не определена"
    )

    await callback.message.edit_text(
        "<b>🟢 PO SIGNAL | DEMO-СДЕЛКА ОТКРЫТА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💼 <b>Торговый счёт:</b> DEMO\n"
        f"💱 Актив: <b>{result.symbol}</b>\n"
        f"📍 Направление: <b>{result.direction}</b>\n"
        f"💰 Сумма: <b>{result.amount:g}</b>\n"
        f"💵 Цена входа: <b>{entry_price}</b>\n"
        f"⏱ Экспирация: <b>{result.expiration} мин.</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ <b>Сделка находится в процессе</b>\n\n"
        "Результат будет проверен автоматически "
        "после завершения экспирации.\n\n"
        "🔔 <i>Ожидайте уведомления об итогах сделки.</i>",
        reply_markup=trade_result_menu(),
        parse_mode="HTML",
    )


# ============================================================
# REAL TRADE — STEP 1
# ============================================================

@router.callback_query(
    F.data.startswith("trade:REAL:")
)
async def real_trade_request(
    callback: CallbackQuery,
) -> None:
    """
    REAL-сделка здесь НЕ открывается.

    Сначала пользователь выбирает CALL/PUT,
    после чего показываем отдельное подтверждение.
    """

    parts = callback.data.split(":")

    if len(parts) != 4:
        await callback.answer(
            "Некорректный запрос.",
            show_alert=True,
        )

        return

    mode = parts[1].upper()
    direction = parts[2].upper()

    try:
        signal_id = int(parts[3])

    except (TypeError, ValueError):
        await callback.answer(
            "Некорректный ID сигнала.",
            show_alert=True,
        )

        return

    if mode != "REAL":
        await callback.answer(
            "Некорректный режим.",
            show_alert=True,
        )

        return

    if not ALLOW_REAL_TRADING:
        await callback.answer(
            "REAL-торговля отключена.",
            show_alert=True,
        )

        return

    if direction not in {"CALL", "PUT"}:
        await callback.answer(
            "Некорректное направление.",
            show_alert=True,
        )

        return

    await callback.answer()

    direction_text = (
        "🟢 CALL"
        if direction == "CALL"
        else "🔴 PUT"
    )

    await callback.message.edit_text(
        "⚠️ <b>ВНИМАНИЕ — REAL СДЕЛКА</b>\n\n"
        f"📍 Выбрано направление: "
        f"<b>{direction_text}</b>\n\n"
        "Вы собираетесь открыть <b>реальную сделку</b> "
        "на Pocket Option.\n\n"
        "💰 Средства будут списаны с REAL-счёта "
        "в соответствии с суммой сделки.\n\n"
        "❗ Убедитесь, что вы действительно хотите "
        "открыть эту сделку.\n\n"
        "<b>Нажмите подтверждение ниже, чтобы "
        "физически отправить сделку в Pocket Option.</b>",
        reply_markup=real_confirmation(
            signal_id,
            direction,
        ),
        parse_mode="HTML",
    )


# ============================================================
# REAL TRADE — STEP 2 / ACTUAL EXECUTION
# ============================================================

@router.callback_query(
    F.data.startswith("trade:real:confirm:")
)
async def confirm_real_trade(
    callback: CallbackQuery,
) -> None:
    """
    ЕДИНСТВЕННОЕ место в Telegram flow,
    где разрешается реальное открытие сделки.

    Здесь передаётся именно выбранное пользователем
    направление CALL или PUT.
    """

    parts = callback.data.split(":")

    if len(parts) != 5:
        await callback.answer(
            "Некорректный запрос.",
            show_alert=True,
        )

        return

    mode = parts[1].upper()
    action = parts[2].lower()
    direction = parts[3].upper()

    try:
        signal_id = int(parts[4])

    except (TypeError, ValueError):
        await callback.answer(
            "Некорректный ID сигнала.",
            show_alert=True,
        )

        return

    if mode != "REAL":
        await callback.answer(
            "Некорректный режим.",
            show_alert=True,
        )

        return

    if action != "confirm":
        await callback.answer(
            "Некорректное действие.",
            show_alert=True,
        )

        return

    if not ALLOW_REAL_TRADING:
        await callback.answer(
            "REAL-торговля отключена.",
            show_alert=True,
        )

        return

    if direction not in {"CALL", "PUT"}:
        await callback.answer(
            "Некорректное направление.",
            show_alert=True,
        )

        return

    await callback.answer(
        "Подтверждение принято. Открываю REAL...",
    )

    result = await open_trade(
        signal_id,
        direction=direction,
        confirmed_real=True,
    )

    if not result.success:
        await callback.message.edit_text(
            "❌ <b>REAL-сделка НЕ открыта</b>\n\n"
            f"{result.error or 'Неизвестная ошибка.'}\n\n"
            "Деньги не должны считаться списанными "
            "через этот бот, если Pocket Option "
            "не подтвердил открытие сделки.",
            reply_markup=trade_result_menu(),
            parse_mode="HTML",
        )

        return

    entry_price = (
        _format_number(result.entry_price)
        if result.entry_price is not None
        else "не определена"
    )

    await callback.message.edit_text(
        "🔴 <b>REAL-СДЕЛКА ОТКРЫТА</b>\n\n"
        f"💱 Актив: <b>{result.symbol}</b>\n"
        f"📍 Направление: <b>{result.direction}</b>\n"
        f"💰 Сумма: <b>{result.amount:g}</b>\n"
        f"💵 Цена входа: <b>{entry_price}</b>\n"
        f"⏱ Экспирация: <b>{result.expiration} мин.</b>\n"
        f"🆔 Deal ID: <code>{result.deal_id}</code>\n\n"
        "⏳ Реальная сделка открыта в Pocket Option.\n"
        "Результат будет проверен автоматически.",
        reply_markup=trade_result_menu(),
        parse_mode="HTML",
    )


# ============================================================
# REAL TRADE — CANCEL
# ============================================================

@router.callback_query(
    F.data.startswith("trade:cancel:")
)
async def cancel_real_trade(
    callback: CallbackQuery,
) -> None:

    await callback.answer(
        "REAL-сделка отменена."
    )

    await callback.message.edit_text(
        "❌ <b>REAL-сделка отменена.</b>\n\n"
        "Сделка в Pocket Option не открывалась.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


# ============================================================
# STATISTICS
# ============================================================

@router.callback_query(F.data == "stats")
async def callback_stats(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    try:
        stats = get_statistics(
            callback.from_user.id
        )

        total = int(
            stats.get("total", 0)
        )

        traded = int(
            stats.get("traded", 0)
        )

        wins = int(
            stats.get("wins", 0)
        )

        losses = int(
            stats.get("losses", 0)
        )

        draws = int(
            stats.get("draws", 0)
        )

        profit = float(
            stats.get("profit", 0)
        )

        win_rate = float(
            stats.get("win_rate", 0)
        )

        text = (
            "📈 <b>Статистика</b>\n\n"
            f"📊 Всего сигналов: <b>{total}</b>\n"
            f"💼 Сделок открыто: <b>{traded}</b>\n\n"
            f"🟢 WIN: <b>{wins}</b>\n"
            f"🔴 LOSS: <b>{losses}</b>\n"
            f"⚪ DRAW: <b>{draws}</b>\n\n"
            f"🎯 Win Rate: <b>{win_rate:.2f}%</b>\n"
            f"💰 Profit: <b>{profit:.2f}</b>"
        )

    except Exception:
        logger.exception(
            "Ошибка получения статистики."
        )

        text = (
            "❌ Не удалось получить статистику."
        )

    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            logger.warning(
                "Не удалось удалить сообщение перед показом статистики."
            )

        await callback.message.answer(
            text,
            reply_markup=back_to_main(),
            parse_mode="HTML",
        )



# ============================================================
# SETTINGS
# ============================================================

def _settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    settings = get_user_settings(user_id)
    trading_mode = str(settings.get("trading_mode", "MANUAL")).upper()

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("✅ 🖐 Ручной" if trading_mode == "MANUAL" else "🖐 Ручной"),
                    callback_data="settings:trading:MANUAL",
                ),
                InlineKeyboardButton(
                    text=("✅ 🤖 Авто" if trading_mode == "AUTO" else "🤖 Авто"),
                    callback_data="settings:trading:AUTO",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="main",
                ),
            ],
        ]
    )


def _settings_amount_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 1", callback_data="settings:amount:1"),
                InlineKeyboardButton(text="💰 2", callback_data="settings:amount:2"),
            ],
            [
                InlineKeyboardButton(text="💰 5", callback_data="settings:amount:5"),
                InlineKeyboardButton(text="💰 10", callback_data="settings:amount:10"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="settings"),
            ],
        ]
    )


def _settings_text(user_id: int) -> str:
    settings = get_user_settings(user_id)
    trading_mode = str(settings.get("trading_mode", "MANUAL")).upper()
    amount = float(settings.get("trade_amount", 1))

    trading_title = "🖐 Ручной" if trading_mode == "MANUAL" else "🤖 Автоматический"
    text = (
        "⚙️ <b>Настройки PoSignalBot</b>\n\n"
        f"🎮 Режим торговли: <b>{trading_title}</b>\n"
    )

    if trading_mode == "AUTO":
        text += f"💰 Сумма автоматической сделки: <b>{amount:g}</b>\n"

    text += "\nВыберите режим торговли ниже."
    return text



@router.callback_query(F.data == "settings")
async def callback_settings(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message:
        # Удаляем фотографию главного меню
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            logger.warning(
                "Не удалось удалить сообщение главного меню."
            )

        # Отправляем настройки отдельным текстовым сообщением
        await callback.message.answer(
            _settings_text(callback.from_user.id),
            reply_markup=_settings_keyboard(callback.from_user.id),
            parse_mode="HTML",
        )



@router.callback_query(F.data.startswith("settings:trading:"))
async def settings_trading(callback: CallbackQuery) -> None:
    trading_mode = callback.data.rsplit(":", 1)[-1].upper()

    if trading_mode not in {"MANUAL", "AUTO"}:
        await callback.answer("Некорректный режим.", show_alert=True)
        return

    update_user_settings(
        callback.from_user.id,
        trading_mode=trading_mode,
    )

    if trading_mode == "AUTO":
        await callback.answer("Выберите сумму автоматической ставки")
        await callback.message.edit_text(
            "🤖 <b>АВТОМАТИЧЕСКИЙ РЕЖИМ</b>\n\n"
            "Выберите сумму ставки. Она будет сохранена и использована "
            "для автоматических сделок, пока вы её не измените.",
            reply_markup=_settings_amount_keyboard(),
            parse_mode="HTML",
        )
        return

    await callback.answer("Включён ручной режим")
    await callback.message.edit_text(
        _settings_text(callback.from_user.id),
        reply_markup=_settings_keyboard(callback.from_user.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("settings:amount:"))
async def settings_amount(callback: CallbackQuery) -> None:
    raw_amount = callback.data.rsplit(":", 1)[-1]

    try:
        amount = float(raw_amount)
    except ValueError:
        await callback.answer("Некорректная сумма.", show_alert=True)
        return

    if amount not in {1.0, 2.0, 5.0, 10.0}:
        await callback.answer("Недопустимая сумма.", show_alert=True)
        return

    update_user_settings(
        callback.from_user.id,
        trade_amount=amount,
        trading_mode="AUTO",
    )

    await callback.answer(f"Ставка сохранена: {amount:g}")
    await callback.message.edit_text(
        _settings_text(callback.from_user.id),
        reply_markup=_settings_keyboard(callback.from_user.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "balance")
async def callback_balance(callback: CallbackQuery) -> None:
    await callback.answer("Получаю баланс...")

    settings = get_user_settings(callback.from_user.id)
    mode = settings["account_mode"]

    try:
        po = get_pocket_option(mode)
        await po.connect()

        actual_is_demo = bool(po.api.is_demo())
        expected_is_demo = mode == "DEMO"

        if actual_is_demo != expected_is_demo:
            actual_mode = "DEMO" if actual_is_demo else "REAL"

            await callback.message.edit_text(
                "⚠️ <b>Режим счёта не совпадает</b>\n\n"
                f"В настройках выбран: <b>{mode}</b>\n"
                f"Подключённый аккаунт: <b>{actual_mode}</b>\n\n"
                "Проверьте подключение Pocket Option.",
                reply_markup=_settings_keyboard(callback.from_user.id),
                parse_mode="HTML",
            )
            return

        balance = float(await po.balance())

        if balance < 0:
            raise ValueError("Получено некорректное значение баланса")

        await callback.message.edit_text(
            "💳 <b>Баланс Pocket Option</b>\n\n"
            f"Счёт: <b>{mode}</b>\n"
            f"Доступный баланс: <b>{balance:.2f}</b>\n\n"
            "Баланс получен непосредственно от платформы.",
            reply_markup=_settings_keyboard(callback.from_user.id),
            parse_mode="HTML",
        )

    except Exception:
        logger.exception(
            "Не удалось получить баланс: user_id=%s mode=%s",
            callback.from_user.id,
            mode,
        )

        await callback.message.edit_text(
            f"❌ <b>Не удалось получить баланс {mode}</b>\n\n"
            "Проверьте подключение к Pocket Option и настройки сессии. "
            "Не открывайте сделку, если баланс не подтверждён.",
            reply_markup=_settings_keyboard(callback.from_user.id),
            parse_mode="HTML",
        )




# ============================================================
# HELP
# ============================================================

@router.callback_query(F.data == "help")
async def callback_help(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    if not callback.message:
        return

    # Удаляем фото главного меню перед показом справки
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        logger.warning(
            "Не удалось удалить сообщение главного меню перед справкой."
        )

    # Отправляем справку новым текстовым сообщением
    await callback.message.answer(
        "ℹ️ <b>Как пользоваться PoSignalBot</b>\n\n"
        "1️⃣ Нажмите «📊 Найти сигнал».\n\n"
        "2️⃣ Выберите счёт:\n"
        "🟢 DEMO — тестовая торговля.\n"
        "🔴 REAL — реальная торговля.\n\n"
        "3️⃣ Выберите время экспирации.\n\n"
        "4️⃣ В MANUAL вы выбираете сумму для сигнала и сами нажимаете CALL/PUT. "
        "Для REAL потребуется подтверждение.\n\n"
        "5️⃣ В AUTO сумма заранее задаётся в настройках, а после анализа "
        "бот автоматически пытается открыть сделку в направлении сигнала. "
        "На REAL это происходит без отдельного подтверждения и несёт риск потери денег.\n\n"
        "После открытия сделки бот автоматически "
        "получит результат из Pocket Option.",
        reply_markup=back_to_main(),
        parse_mode="HTML",
    )