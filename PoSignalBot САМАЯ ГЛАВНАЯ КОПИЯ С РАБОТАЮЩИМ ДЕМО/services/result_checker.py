from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import FSInputFile

from database import (
    get_open_trades,
    update_trade_result,
)
from market.pocket_option import get_pocket_option
from keyboards.main import trade_result_menu

logger = logging.getLogger(__name__)


# Пути к изображениям результата сделки
BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "images"

WIN_IMAGE = IMAGES_DIR / "win.jpg"
LOSS_IMAGE = IMAGES_DIR / "loss.jpg"

# ============================================================
# CONFIG
# ============================================================

CHECK_INTERVAL_SECONDS = 10

RESULT_TIMEOUT_EXTRA_SECONDS = 30

BALANCE_RETRY_COUNT = 3
BALANCE_RETRY_DELAY_SECONDS = 1


# ============================================================
# HELPERS
# ============================================================

def _safe_float(value: Any) -> float | None:

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_value(
    data: Any,
    *keys: str,
) -> Any:

    if data is None:
        return None

    if isinstance(data, dict):

        for key in keys:

            if (
                key in data
                and data[key] is not None
            ):
                return data[key]

        for nested_key in (
            "data",
            "trade",
            "result",
            "deal",
        ):

            nested = data.get(
                nested_key
            )

            if nested is not None:

                value = _extract_value(
                    nested,
                    *keys,
                )

                if value is not None:
                    return value

        return None

    for key in keys:

        try:

            value = getattr(
                data,
                key,
                None,
            )

            if value is not None:
                return value

        except Exception:
            continue

    return None


def _normalize_result(
    result_data: Any,
) -> str | None:

    raw_result = _extract_value(
        result_data,
        "result",
        "status",
        "outcome",
        "trade_result",
        "tradeResult",
    )

    if raw_result is not None:

        value = str(
            raw_result
        ).strip().lower()

        if value in {
            "win",
            "won",
            "profit",
            "profitable",
            "success",
            "successfully",
        }:
            return "WIN"

        if value in {
            "loss",
            "lost",
            "lose",
            "losing",
            "failed",
        }:
            return "LOSS"

        if value in {
            "draw",
            "tie",
            "equal",
            "refund",
            "returned",
        }:
            return "DRAW"

        if value == "true":
            return "WIN"

        if value == "false":
            return "LOSS"

    profit = _extract_value(
        result_data,
        "profit",
        "profit_amount",
        "profitAmount",
        "pnl",
        "pnl_amount",
        "pnlAmount",
    )

    profit_value = _safe_float(
        profit
    )

    if profit_value is not None:

        if profit_value > 0:
            return "WIN"

        if profit_value < 0:
            return "LOSS"

        return "DRAW"

    return None


def _extract_profit(
    result_data: Any,
) -> float:

    value = _extract_value(
        result_data,
        "profit",
        "profit_amount",
        "profitAmount",
        "pnl",
        "pnl_amount",
        "pnlAmount",
    )

    profit = _safe_float(
        value
    )

    if profit is None:
        return 0.0

    return profit


def _extract_exit_price(
    result_data: Any,
) -> float | None:

    value = _extract_value(
        result_data,
        "closePrice",
        "close_price",
        "exit_price",
        "exitPrice",
        "close",
        "price",
        "rate",
        "close_rate",
        "closeRate",
    )

    return _safe_float(value)


def _parse_datetime(
    value: Any,
) -> datetime | None:

    if value is None:
        return None

    if isinstance(value, datetime):

        dt = value

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    text = str(
        value
    ).strip()

    if not text:
        return None

    try:

        dt = datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00",
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except ValueError:
        return None


def _trade_is_due(
    trade: Any,
) -> bool:

    expires_at = _parse_datetime(
        trade["trade_expires_at"]
    )

    if expires_at is None:
        return True

    now = datetime.now(
        timezone.utc
    )

    return now >= expires_at


def _get_signal_id(
    trade: Any,
) -> int:

    return int(
        trade["id"]
    )


def _format_price(
    value: Any,
) -> str:

    number = _safe_float(
        value
    )

    if number is None:
        return "—"

    return (
        f"{number:.10f}"
        .rstrip("0")
        .rstrip(".")
    )


def _format_money(
    value: Any,
) -> str:

    number = _safe_float(
        value
    )

    if number is None:
        return "—"

    return f"${number:.2f}"


def _format_profit(
    value: Any,
) -> str:

    number = _safe_float(
        value
    )

    if number is None:
        number = 0.0

    if number > 0:
        return f"+${number:.2f}"

    if number < 0:
        return f"-${abs(number):.2f}"

    return "$0.00"


def _format_balance_change(
    balance_before: Any,
    balance_after: Any,
) -> str:

    before = _safe_float(
        balance_before
    )

    after = _safe_float(
        balance_after
    )

    if before is None or after is None:
        return "—"

    change = after - before

    if change > 0:
        return f"+${change:.2f}"

    if change < 0:
        return f"-${abs(change):.2f}"

    return "$0.00"


def _result_emoji(
    result: str,
) -> str:

    if result == "WIN":
        return "🟢"

    if result == "LOSS":
        return "🔴"

    return "⚪"


def _direction_text(
    direction: str,
) -> str:

    direction = str(
        direction
    ).upper()

    if direction == "CALL":
        return "CALL 🟢"

    if direction == "PUT":
        return "PUT 🔴"

    return direction


# ============================================================
# RESULT MESSAGE
# ============================================================


def _build_result_message(
    *,
    trade: Any,
    result: str,
    profit: float,
    exit_price: float | None,
    balance_after: float | None,
) -> str:
    """Формирует короткое сообщение о результате сделки."""

    symbol = str(trade["symbol"])
    direction = _direction_text(str(trade["direction"]))
    expiration = int(trade["expiration"])

    if result == "WIN":
        title = "🟢 <b>СДЕЛКА ЗАКРЫТА В ПЛЮС</b>"
    elif result == "LOSS":
        title = "🔴 <b>СДЕЛКА ЗАКРЫТА В МИНУС</b>"
    else:
        title = "⚪ <b>СДЕЛКА ЗАКРЫТА В НИЧЬЮ</b>"

    return (
        f"{title}\n\n"
        f"💰 Результат: <b>{_format_profit(profit)}</b>\n"
        f"💱 Актив: <code>{symbol}</code>\n"
        f"📈 Направление: {direction}\n"
        f"⏱ Экспирация: {expiration} мин\n"
        f"🎯 Вход: {_format_price(trade['entry_price'])}\n"
        f"🏁 Выход: {_format_price(exit_price)}"
    )



# ============================================================
# RESULT CHECKER
# ============================================================

class ResultChecker:

    def __init__(
        self,
        bot: Bot,
        *,
        check_interval: int = CHECK_INTERVAL_SECONDS,
    ) -> None:

        self.bot = bot

        self.check_interval = max(
            1,
            int(check_interval),
        )

        self._running = False
        self._task: asyncio.Task | None = None

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    async def start(self) -> None:

        if self._running:

            logger.warning(
                "ResultChecker уже запущен."
            )

            return

        self._running = True

        self._task = asyncio.create_task(
            self._run(),
            name=(
                "pocket-option-result-checker"
            ),
        )

        logger.info(
            "ResultChecker запущен. "
            "Интервал: %s сек.",
            self.check_interval,
        )

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    async def stop(self) -> None:

        self._running = False

        if self._task is None:
            return

        self._task.cancel()

        try:
            await self._task

        except asyncio.CancelledError:
            pass

        finally:
            self._task = None

        logger.info(
            "ResultChecker остановлен."
        )

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    async def _run(self) -> None:

        while self._running:

            try:
                await self.check_open_trades()

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Критическая ошибка "
                    "в ResultChecker."
                )

            try:
                await asyncio.sleep(
                    self.check_interval
                )

            except asyncio.CancelledError:
                raise

    # --------------------------------------------------------
    # CHECK ALL
    # --------------------------------------------------------

    async def check_open_trades(
        self,
    ) -> None:

        trades = get_open_trades()

        if not trades:
            return

        logger.debug(
            "Открытых сделок "
            "для проверки: %s",
            len(trades),
        )

        for trade in trades:

            try:

                await self._check_trade(
                    trade
                )

            except asyncio.CancelledError:
                raise

            except Exception:

                logger.exception(
                    "Ошибка проверки сделки "
                    "#%s deal_id=%s",
                    trade["id"],
                    trade["deal_id"],
                )

    # --------------------------------------------------------
    # BALANCE AFTER
    # --------------------------------------------------------

    async def _get_balance_after(
        self,
        po: Any,
        *,
        signal_id: int,
        deal_id: str,
        account_mode: str,
    ) -> float | None:

        for attempt in range(
            1,
            BALANCE_RETRY_COUNT + 1,
        ):

            try:

                balance = _safe_float(
                    await po.balance()
                )

                if balance is not None:

                    logger.info(
                        "BALANCE AFTER TRADE: "
                        "signal_id=%s "
                        "deal_id=%s "
                        "mode=%s "
                        "balance_after=%s "
                        "attempt=%s",
                        signal_id,
                        deal_id,
                        account_mode,
                        balance,
                        attempt,
                    )

                    return balance

                logger.warning(
                    "Пустой баланс: "
                    "signal_id=%s "
                    "deal_id=%s "
                    "attempt=%s",
                    signal_id,
                    deal_id,
                    attempt,
                )

            except Exception:

                logger.exception(
                    "Ошибка получения "
                    "balance_after: "
                    "signal_id=%s "
                    "deal_id=%s "
                    "attempt=%s",
                    signal_id,
                    deal_id,
                    attempt,
                )

            if attempt < BALANCE_RETRY_COUNT:

                try:

                    await asyncio.sleep(
                        BALANCE_RETRY_DELAY_SECONDS
                    )

                except asyncio.CancelledError:
                    raise

        logger.error(
            "Не удалось получить "
            "balance_after: "
            "signal_id=%s "
            "deal_id=%s "
            "mode=%s",
            signal_id,
            deal_id,
            account_mode,
        )

        return None

    # --------------------------------------------------------
    # SEND MESSAGE
    # --------------------------------------------------------




    async def _send_result_message(
        self,
        *,
        trade: Any,
        result: str,
        profit: float,
        exit_price: float | None,
        balance_after: float | None,
    ) -> None:

        user_id = trade["user_id"]

        if user_id is None:
            logger.error(
                "Невозможно отправить результат: "
                "у сделки #%s отсутствует user_id.",
                trade["id"],
            )
            return

        message = _build_result_message(
            trade=trade,
            result=result,
            profit=profit,
            exit_price=exit_price,
            balance_after=balance_after,
        )

        # Выбираем изображение по результату сделки
        if result == "WIN":
            image_path = WIN_IMAGE
        elif result == "LOSS":
            image_path = LOSS_IMAGE
        else:
            image_path = None

        try:
            chat_id = int(user_id)

            # Отправляем изображение и результат
            # в одном сообщении Telegram
            if image_path is not None and image_path.is_file():
                await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=FSInputFile(str(image_path)),
                    caption=message,
                    parse_mode="HTML",
                    reply_markup=trade_result_menu(),
                )
            else:
                if image_path is not None:
                    logger.warning(
                        "Изображение результата не найдено: %s",
                        image_path,
                    )

                # Если файла нет, отправляем текстовый результат
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="HTML",
                    reply_markup=trade_result_menu(),
                )

            logger.info(
                "RESULT MESSAGE SENT: "
                "signal_id=%s user_id=%s mode=%s result=%s",
                trade["id"],
                user_id,
                str(trade["account_mode"] or "DEMO").upper(),
                result,
            )

        except Exception:
            logger.exception(
                "Не удалось отправить Telegram-результат: "
                "signal_id=%s user_id=%s",
                trade["id"],
                user_id,
            )




    # --------------------------------------------------------
    # CHECK SINGLE TRADE
    # --------------------------------------------------------

    async def _check_trade(
        self,
        trade: Any,
    ) -> None:

        signal_id = _get_signal_id(
            trade
        )

        deal_id = str(
            trade["deal_id"]
        ).strip()

        account_mode = str(
            trade["account_mode"]
            or "DEMO"
        ).strip().upper()

        symbol = str(
            trade["symbol"]
        )

        direction = str(
            trade["direction"]
        ).upper()

        expiration = int(
            trade["expiration"]
        )

        # ----------------------------------------------------
        # Protection
        # ----------------------------------------------------

        if not deal_id:

            logger.error(
                "Сделка #%s имеет пустой "
                "deal_id. Оставляем открытой.",
                signal_id,
            )

            return

        if account_mode not in {
            "DEMO",
            "REAL",
        }:

            logger.error(
                "Сделка #%s имеет неизвестный "
                "account_mode=%r.",
                signal_id,
                account_mode,
            )

            return

        # ----------------------------------------------------
        # Expiration
        # ----------------------------------------------------

        if not _trade_is_due(
            trade
        ):

            logger.debug(
                "Сделка ещё не завершилась: "
                "signal_id=%s deal_id=%s",
                signal_id,
                deal_id,
            )

            return

        # ----------------------------------------------------
        # Pocket Option
        # ----------------------------------------------------

        po = get_pocket_option(
            account_mode
        )

        try:

            await po.connect()

        except Exception:

            logger.exception(
                "Не удалось подключиться "
                "к Pocket Option %s "
                "для проверки deal_id=%s",
                account_mode,
                deal_id,
            )

            return

        # ----------------------------------------------------
        # Mode
        # ----------------------------------------------------

        try:

            actual_is_demo = bool(
                po.api.is_demo()
            )

        except Exception:

            logger.exception(
                "Не удалось проверить режим "
                "Pocket Option для deal_id=%s",
                deal_id,
            )

            return

        expected_is_demo = (
            account_mode == "DEMO"
        )

        if actual_is_demo != expected_is_demo:

            actual_mode = (
                "DEMO"
                if actual_is_demo
                else "REAL"
            )

            logger.error(
                "MODE MISMATCH: "
                "deal_id=%s expected=%s actual=%s",
                deal_id,
                account_mode,
                actual_mode,
            )

            return

        # ----------------------------------------------------
        # check_win
        # ----------------------------------------------------

        timeout_seconds = (
            max(
                expiration * 60,
                30,
            )
            + RESULT_TIMEOUT_EXTRA_SECONDS
        )

        try:

            result_data = (
                await po.check_win(
                    deal_id,
                    timeout_seconds=(
                        timeout_seconds
                    ),
                )
            )

        except Exception:

            logger.exception(
                "check_win() ошибка: "
                "signal_id=%s "
                "deal_id=%s",
                signal_id,
                deal_id,
            )

            return

        logger.debug(
            "check_win response: "
            "signal_id=%s "
            "deal_id=%s "
            "result=%r",
            signal_id,
            deal_id,
            result_data,
        )

        # ----------------------------------------------------
        # Normalize
        # ----------------------------------------------------

        result = _normalize_result(
            result_data
        )

        if result is None:

            logger.info(
                "Результат сделки пока "
                "не готов: "
                "signal_id=%s "
                "deal_id=%s "
                "response=%r",
                signal_id,
                deal_id,
                result_data,
            )

            return

        # ----------------------------------------------------
        # Profit
        # ----------------------------------------------------

        profit = _extract_profit(
            result_data
        )

        # ----------------------------------------------------
        # Exit price
        # ----------------------------------------------------

        exit_price = _extract_exit_price(
            result_data
        )

        # ----------------------------------------------------
        # Balance after
        # ----------------------------------------------------

        balance_after = (
            await self._get_balance_after(
                po,
                signal_id=signal_id,
                deal_id=deal_id,
                account_mode=account_mode,
            )
        )

        balance_before = _safe_float(
            trade["balance_before"]
        )

        if (
            balance_before is not None
            and balance_after is not None
        ):

            balance_change = (
                balance_after
                - balance_before
            )

            logger.info(
                "BALANCE CHANGE: "
                "signal_id=%s "
                "mode=%s "
                "before=%s "
                "after=%s "
                "change=%s",
                signal_id,
                account_mode,
                balance_before,
                balance_after,
                balance_change,
            )

        # ----------------------------------------------------
        # Atomic completion
        # ----------------------------------------------------

        checked_at = datetime.now(
            timezone.utc
        ).isoformat()

        try:

            completed = (
                update_trade_result(
                    signal_id=signal_id,
                    result=result,
                    profit=profit,
                    exit_price=exit_price,
                    checked_at=checked_at,
                    balance_after=balance_after,
                )
            )

        except Exception:

            logger.exception(
                "НЕ УДАЛОСЬ СОХРАНИТЬ "
                "РЕЗУЛЬТАТ: "
                "signal_id=%s "
                "deal_id=%s "
                "result=%s",
                signal_id,
                deal_id,
                result,
            )

            return

        # ----------------------------------------------------
        # Уже закрыта другим процессом
        # ----------------------------------------------------

        if not completed:

            logger.info(
                "Результат сделки #%s "
                "уже был сохранён ранее. "
                "Повторное Telegram-уведомление "
                "не отправляется. "
                "deal_id=%s",
                signal_id,
                deal_id,
            )

            return

        # ----------------------------------------------------
        # Telegram
        # ----------------------------------------------------

        await self._send_result_message(
            trade=trade,
            result=result,
            profit=profit,
            exit_price=exit_price,
            balance_after=balance_after,
        )

        # ----------------------------------------------------
        # Done
        # ----------------------------------------------------

        logger.info(
            "TRADE RESULT: "
            "signal_id=%s "
            "deal_id=%s "
            "mode=%s "
            "symbol=%s "
            "direction=%s "
            "result=%s "
            "profit=%s "
            "exit_price=%s "
            "balance_before=%s "
            "balance_after=%s",
            signal_id,
            deal_id,
            account_mode,
            symbol,
            direction,
            result,
            profit,
            exit_price,
            balance_before,
            balance_after,
        )


# ============================================================
# SINGLETON
# ============================================================

_result_checker: ResultChecker | None = None


def get_result_checker(
    bot: Bot,
) -> ResultChecker:

    global _result_checker

    if _result_checker is None:

        _result_checker = ResultChecker(
            bot=bot
        )

    return _result_checker


# ============================================================
# CONVENIENCE
# ============================================================

async def start_result_checker(
    bot: Bot,
) -> None:

    checker = get_result_checker(
        bot
    )

    await checker.start()


async def stop_result_checker() -> None:

    if _result_checker is None:
        return

    await _result_checker.stop()


async def check_open_trades() -> None:

    if _result_checker is None:
        return

    await _result_checker.check_open_trades()