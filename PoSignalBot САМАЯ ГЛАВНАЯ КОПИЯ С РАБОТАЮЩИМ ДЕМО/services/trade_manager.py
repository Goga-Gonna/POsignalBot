from __future__ import annotations

import logging
import os
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from database import (
    get_signal,
    mark_trade_awaiting_execution,
    mark_trade_error,
    mark_trade_open,
)
from market.pocket_option import get_pocket_option


logger = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

TRADING_ENABLED = os.getenv(
    "TRADING_ENABLED",
    "false",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

try:
    DEFAULT_TRADE_AMOUNT = float(
        os.getenv("TRADE_AMOUNT", "1")
    )
except (TypeError, ValueError):
    DEFAULT_TRADE_AMOUNT = 1.0


# ============================================================
# RESULT
# ============================================================

@dataclass(slots=True)
class TradeExecutionResult:
    success: bool
    signal_id: int

    symbol: str | None = None
    direction: str | None = None
    account_mode: str | None = None

    amount: float | None = None
    expiration: int | None = None

    deal_id: str | None = None
    entry_price: float | None = None
    trade_expires_at: str | None = None

    balance_before: float | None = None

    trade_data: Any = None
    error: str | None = None

    @classmethod
    def failed(
        cls,
        signal_id: int,
        error: str,
        *,
        symbol: str | None = None,
        direction: str | None = None,
        account_mode: str | None = None,
        amount: float | None = None,
        expiration: int | None = None,
        balance_before: float | None = None,
    ) -> "TradeExecutionResult":

        return cls(
            success=False,
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            account_mode=account_mode,
            amount=amount,
            expiration=expiration,
            balance_before=balance_before,
            error=error,
        )


# ============================================================
# HELPERS
# ============================================================

def _normalize_mode(value: Any) -> str:
    mode = str(value or "DEMO").strip().upper()

    if mode not in {"DEMO", "REAL"}:
        raise ValueError(
            f"Недопустимый account_mode={value!r}. "
            f"Разрешено только DEMO или REAL."
        )

    return mode


def _normalize_direction(value: Any) -> str:
    direction = str(value or "").strip().upper()

    if direction not in {"CALL", "PUT"}:
        raise ValueError(
            f"Недопустимое направление сделки: {value!r}. "
            f"Разрешено только CALL или PUT."
        )

    return direction


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
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
            if key in data and data[key] is not None:
                return data[key]

        for nested_key in (
            "data",
            "trade",
            "result",
            "deal",
        ):
            nested = data.get(nested_key)

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


def _extract_deal_id(
    deal_id: Any,
    trade_data: Any = None,
) -> str | None:

    if deal_id is not None:

        value = str(
            deal_id
        ).strip()

        if value:
            return value

    value = _extract_value(
        trade_data,
        "deal_id",
        "dealId",
        "id",
        "trade_id",
        "tradeId",
        "position_id",
        "positionId",
    )

    if value is None:
        return None

    value = str(value).strip()

    return value or None


def _extract_entry_price(
    trade_data: Any,
) -> float | None:

    value = _extract_value(
        trade_data,
        "openPrice",
        "open_price",
        "entry_price",
        "entryPrice",
        "open",
        "price",
        "rate",
        "open_rate",
        "openRate",
    )

    return _safe_float(value)


def _to_iso_utc(
    value: Any,
) -> str | None:

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
        ).isoformat()

    if isinstance(value, (int, float)):

        try:
            timestamp = float(value)

            if timestamp > 10_000_000_000:
                timestamp /= 1000.0

            dt = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

            return dt.isoformat()

        except (
            ValueError,
            OSError,
            OverflowError,
        ):
            return None

    if isinstance(value, str):

        text = value.strip()

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
            ).isoformat()

        except ValueError:
            pass

        try:
            return _to_iso_utc(
                float(text)
            )
        except (
            TypeError,
            ValueError,
        ):
            return text

    return None


def _fallback_expiration(
    expiration_minutes: int,
) -> str:

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(
            minutes=expiration_minutes
        )
    )

    return expires_at.isoformat()


# ============================================================
# TRADE MANAGER
# ============================================================

class TradeManager:

    def __init__(self) -> None:
        self.logger = logger

    async def open_trade(
        self,
        signal_id: int,
        *,
        direction: str | None = None,
        confirmed_real: bool = False,
    ) -> TradeExecutionResult:

        # ----------------------------------------------------
        # 1. Global trading switch
        # ----------------------------------------------------

        if not TRADING_ENABLED:

            error = (
                "Торговля отключена. "
                "Установите TRADING_ENABLED=true в .env."
            )

            self.logger.warning(error)

            return TradeExecutionResult.failed(
                signal_id,
                error,
            )

        # ----------------------------------------------------
        # 2. Signal
        # ----------------------------------------------------

        signal = get_signal(signal_id)

        if signal is None:

            error = (
                f"Сигнал #{signal_id} "
                f"не найден в базе данных."
            )

            self.logger.error(error)

            return TradeExecutionResult.failed(
                signal_id,
                error,
            )

        symbol = str(
            signal["symbol"]
        )

        # ----------------------------------------------------
        # 3. Parameters
        # ----------------------------------------------------

        try:

        # Если направление передано явно —
        # используем выбор пользователя.
        #
        # Если direction=None —
        # используем рекомендацию Signal Engine,
        # сохранённую в БД.
            if direction is None:
                direction = _normalize_direction(
                    signal["direction"]
                )
            else:
                direction = _normalize_direction(
                    direction
                )

            account_mode = _normalize_mode(
                signal["account_mode"]
            )

        except ValueError as exc:

            error = str(exc)

            self.logger.error(
                "Сигнал #%s: %s",
                signal_id,
                error,
            )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=str(
                    signal["direction"]
                ),
                account_mode=str(
                    signal["account_mode"]
                ),
            )

        # ----------------------------------------------------
        # 4. Проверяем, разрешена ли конкретная сделка
        # ----------------------------------------------------

        if not bool(
            signal["trade_enabled"]
        ):

            error = (
                f"Для сигнала #{signal_id} "
                f"торговля не разрешена."
            )

            self.logger.warning(
                error
            )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
            )

        # ----------------------------------------------------
        # 5. Expiration
        # ----------------------------------------------------

        expiration = _safe_int(
            signal["expiration"]
        )

        if expiration is None or expiration <= 0:

            error = (
                "Некорректная длительность сделки "
                f"для сигнала #{signal_id}: "
                f"{signal['expiration']!r}"
            )

            self.logger.error(error)

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
            )

        # ----------------------------------------------------
        # 6. Amount
        # ----------------------------------------------------

        amount = _safe_float(
            signal["trade_amount"]
        )

        if amount is None:
            amount = DEFAULT_TRADE_AMOUNT

        if not math.isfinite(amount) or amount <= 0:

            error = (
                f"Некорректная сумма сделки: {amount}. "
                f"Сумма должна быть больше 0."
            )

            self.logger.error(error)

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
            )

        # ----------------------------------------------------
        # 7. REAL confirmation
        # ----------------------------------------------------

        if (
            account_mode == "REAL"
            and not confirmed_real
        ):

            error = (
                "Для открытия REAL-сделки "
                "требуется явное подтверждение."
            )

            self.logger.warning(
                "REAL без подтверждения: "
                "signal_id=%s",
                signal_id,
            )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
            )

        # ----------------------------------------------------
        # 8. Atomic claim
        # ----------------------------------------------------

        claimed = mark_trade_awaiting_execution(
            signal_id
        )

        if not claimed:

            error = (
                f"Сигнал #{signal_id} уже обрабатывается, "
                f"исполнен или имеет другой статус."
            )

            self.logger.warning(error)

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
            )


        # ----------------------------------------------------
        # 9. Pocket Option — безопасное получение подключения
        # ----------------------------------------------------

        try:
            po = get_pocket_option(account_mode)
            await po.connect()

        except Exception as exc:
            error = (
                f"Не удалось подключиться "
                f"к Pocket Option {account_mode}: {exc}"
            )

            self.logger.exception(
                "Ошибка подключения: signal_id=%s",
                signal_id,
            )

            try:
                mark_trade_error(signal_id, error)
            except Exception:
                self.logger.exception(
                    "Не удалось сохранить ошибку подключения: "
                    "signal_id=%s",
                    signal_id,
                )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
            )


        # ----------------------------------------------------
        # 10. Mode verification
        # ----------------------------------------------------

        try:
            actual_is_demo = bool(
                po.api.is_demo()
            )

        except Exception as exc:

            error = (
                "Не удалось определить режим "
                f"аккаунта Pocket Option: {exc}"
            )

            self.logger.exception(
                "Ошибка проверки режима: "
                "signal_id=%s",
                signal_id,
            )

            mark_trade_error(
                signal_id,
                error,
            )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
            )

        expected_is_demo = (
            account_mode == "DEMO"
        )

        if actual_is_demo != expected_is_demo:

            actual_mode = (
                "DEMO"
                if actual_is_demo
                else "REAL"
            )

            error = (
                f"Несовпадение режима аккаунта. "
                f"Ожидался {account_mode}, "
                f"но Pocket Option подключён "
                f"как {actual_mode}."
            )

            self.logger.error(
                "MODE MISMATCH: "
                "signal_id=%s expected=%s actual=%s",
                signal_id,
                account_mode,
                actual_mode,
            )

            mark_trade_error(
                signal_id,
                error,
            )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
            )


        # ----------------------------------------------------
        # 11. Balance before — STRICT SAFETY CHECK
        # ----------------------------------------------------

        balance_before: float | None = None

        try:
            balance_before = _safe_float(
                await po.balance()
            )
        except Exception:
            self.logger.exception(
                "Не удалось получить баланс перед сделкой: "
                "signal_id=%s mode=%s",
                signal_id,
                account_mode,
            )

        # Не открываем сделку, если баланс неизвестен,
        # отрицательный, NaN или бесконечный.
        if (
            balance_before is None
            or not math.isfinite(balance_before)
            or balance_before < 0
        ):
            error = (
                f"Не удалось достоверно проверить баланс "
                f"{account_mode}. Сделка не отправлена. "
                f"Проверьте подключение и попробуйте позже."
            )

            self.logger.error(
                "TRADE BLOCKED: invalid balance; "
                "signal_id=%s mode=%s balance=%r",
                signal_id,
                account_mode,
                balance_before,
            )

            mark_trade_error(signal_id, error)

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
                balance_before=balance_before,
            )

        self.logger.info(
            "BALANCE BEFORE: signal_id=%s mode=%s "
            "balance=%.2f amount=%.2f",
            signal_id,
            account_mode,
            balance_before,
            amount,
        )

        # Строгая проверка достаточности средств.
        if balance_before < amount:
            error = (
                f"Недостаточно средств на счёте {account_mode}. "
                f"Баланс: {balance_before:.2f}; "
                f"сумма сделки: {amount:.2f}. "
                f"Сделка не отправлена."
            )

            self.logger.warning(
                "TRADE BLOCKED: insufficient balance; "
                "signal_id=%s mode=%s balance=%.2f amount=%.2f",
                signal_id,
                account_mode,
                balance_before,
                amount,
            )

            mark_trade_error(signal_id, error)

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
                balance_before=balance_before,
            )


        # ----------------------------------------------------
        # 12. Fallback entry price
        # ----------------------------------------------------

        fallback_entry_price: float | None = None

        try:

            fallback_entry_price = (
                await po.get_current_price(
                    symbol
                )
            )

        except Exception:

            self.logger.warning(
                "Не удалось получить цену "
                "перед сделкой: "
                "signal_id=%s symbol=%s",
                signal_id,
                symbol,
                exc_info=True,
            )

        # ----------------------------------------------------
        # 13. BUY / SELL
        # ----------------------------------------------------

        trade_data: Any = None
        deal_id: str | None = None

        try:

            if direction == "CALL":

                deal_id_raw, trade_data = (
                    await po.buy(
                        symbol=symbol,
                        amount=amount,
                        expiration_minutes=expiration,
                    )
                )

            else:

                deal_id_raw, trade_data = (
                    await po.sell(
                        symbol=symbol,
                        amount=amount,
                        expiration_minutes=expiration,
                    )
                )

            deal_id = _extract_deal_id(
                deal_id_raw,
                trade_data,
            )

        except Exception as exc:

            error = (
                f"Ошибка открытия сделки "
                f"{direction} {symbol}: {exc}"
            )

            self.logger.exception(
                "Ошибка buy/sell: signal_id=%s",
                signal_id,
            )

            mark_trade_error(
                signal_id,
                error,
            )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
                balance_before=balance_before,
            )

        # ----------------------------------------------------
        # 14. deal_id
        # ----------------------------------------------------

        if not deal_id:

            error = (
                "Pocket Option вернул ответ "
                "без deal_id. "
                "Повторная сделка НЕ открывается."
            )

            self.logger.critical(
                "NO DEAL ID AFTER BUY/SELL: "
                "signal_id=%s trade_data=%r",
                signal_id,
                trade_data,
            )

            mark_trade_error(
                signal_id,
                error,
            )

            return TradeExecutionResult.failed(
                signal_id,
                error,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
                balance_before=balance_before,
            )

        # ----------------------------------------------------
        # 15. Entry price
        # ----------------------------------------------------

        entry_price = _extract_entry_price(
            trade_data
        )

        if entry_price is None:
            entry_price = fallback_entry_price

        # ----------------------------------------------------
        # 16. Expiration time
        # ----------------------------------------------------
        #
        # НЕ используем po.get_deal_end_time().
        #
        # BinaryOptionsToolsV2 может вернуть время
        # без timezone-информации / в локальном времени.
        # Если такое значение трактовать как UTC,
        # получаем смещение + несколько часов.
        #
        # Для ResultChecker используем собственную
        # UTC-временную шкалу:
        #
        # trade_created_at + expiration минут.
        #
        # ----------------------------------------------------

        trade_created_at = datetime.now(
            timezone.utc
        )

        trade_expires_at = (
            trade_created_at
            + timedelta(minutes=expiration)
        ).isoformat()

        self.logger.info(
            "TRADE EXPIRATION: "
            "signal_id=%s "
            "deal_id=%s "
            "created_at=%s "
            "expires_at=%s "
            "expiration=%sm",
            signal_id,
            deal_id,
            trade_created_at.isoformat(),
            trade_expires_at,
            expiration,
        )

        # ----------------------------------------------------
        # 17. SAVE OPEN TRADE
        # ----------------------------------------------------
        #
        # КРИТИЧЕСКИ ВАЖНО:
        #
        # buy/sell уже выполнен.
        #
        # Поэтому здесь НИКОГДА не вызываем
        # buy()/sell() повторно.
        # ----------------------------------------------------

        try:

            saved = mark_trade_open(
                signal_id=signal_id,
                deal_id=deal_id,
                entry_price=entry_price,
                balance_before=balance_before,
                trade_created_at=trade_created_at.isoformat(),
                trade_expires_at=trade_expires_at,
                direction=direction,
            )

        except Exception as exc:

            self.logger.critical(
                "DB ERROR AFTER TRADE OPEN: "
                "signal_id=%s deal_id=%s",
                signal_id,
                deal_id,
                exc_info=True,
            )

            saved = False

        # ----------------------------------------------------
        # 18. Проверяем bool от mark_trade_open()
        # ----------------------------------------------------

        if not saved:

            self.logger.critical(
                "Первичная запись сделки в БД "
                "не подтверждена. "
                "Повторяем ТОЛЬКО запись, "
                "НЕ торговую операцию. "
                "signal_id=%s deal_id=%s",
                signal_id,
                deal_id,
            )

            try:

                saved = mark_trade_open(
                    signal_id=signal_id,
                    deal_id=deal_id,
                    entry_price=entry_price,
                    balance_before=balance_before,
                    trade_created_at=trade_created_at.isoformat(),
                    trade_expires_at=trade_expires_at,
                    direction=direction,
                )

            except Exception:

                saved = False

                self.logger.critical(
                    "RETRY DB SAVE FAILED: "
                    "signal_id=%s deal_id=%s",
                    signal_id,
                    deal_id,
                    exc_info=True,
                )

        # ----------------------------------------------------
        # 19. Если БД всё ещё не приняла сделку
        # ----------------------------------------------------

        if not saved:

            error = (
                f"Сделка {deal_id} открыта в "
                f"Pocket Option, но её не удалось "
                f"сохранить в БД. "
                f"Повторный buy/sell НЕ выполнялся."
            )

            self.logger.critical(
                "ORPHANED TRADE: "
                "signal_id=%s deal_id=%s",
                signal_id,
                deal_id,
            )

            return TradeExecutionResult(
                success=False,
                signal_id=signal_id,
                symbol=symbol,
                direction=direction,
                account_mode=account_mode,
                amount=amount,
                expiration=expiration,
                deal_id=deal_id,
                entry_price=entry_price,
                trade_expires_at=trade_expires_at,
                balance_before=balance_before,
                trade_data=trade_data,
                error=error,
            )

        # ----------------------------------------------------
        # 20. SUCCESS
        # ----------------------------------------------------

        self.logger.info(
            "TRADE OPENED: "
            "signal_id=%s deal_id=%s "
            "mode=%s symbol=%s direction=%s "
            "amount=%s entry=%s "
            "balance_before=%s expiration=%sm",
            signal_id,
            deal_id,
            account_mode,
            symbol,
            direction,
            amount,
            entry_price,
            balance_before,
            expiration,
        )

        return TradeExecutionResult(
            success=True,
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            account_mode=account_mode,
            amount=amount,
            expiration=expiration,
            deal_id=deal_id,
            entry_price=entry_price,
            trade_expires_at=trade_expires_at,
            balance_before=balance_before,
            trade_data=trade_data,
        )


# ============================================================
# SINGLETON
# ============================================================

_trade_manager: TradeManager | None = None


def get_trade_manager() -> TradeManager:

    global _trade_manager

    if _trade_manager is None:
        _trade_manager = TradeManager()

    return _trade_manager


# ============================================================
# CONVENIENCE
# ============================================================

async def open_trade(
    signal_id: int,
    *,
    direction: str | None = None,
    confirmed_real: bool = False,
) -> TradeExecutionResult:

    manager = get_trade_manager()

    return await manager.open_trade(
        signal_id,
        direction=direction,
        confirmed_real=confirmed_real,
    )