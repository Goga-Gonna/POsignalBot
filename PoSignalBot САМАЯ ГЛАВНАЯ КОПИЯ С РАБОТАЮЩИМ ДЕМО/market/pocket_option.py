# market/pocket_option.py

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync

from market.data import Candle

load_dotenv()

logger = logging.getLogger(__name__)


class PocketOptionError(Exception):
    """Ошибка работы с Pocket Option."""


class PocketOptionMarket:
    """
    Унифицированный адаптер Pocket Option.

    Остальной проект не должен напрямую работать
    с BinaryOptionsToolsV2.
    """

    def __init__(self, mode: str):
        mode = mode.upper().strip()

        if mode not in ("DEMO", "REAL"):
            raise ValueError("mode должен быть DEMO или REAL")

        self.mode = mode

        if mode == "DEMO":
            self.ssid = os.getenv("POCKET_OPTION_DEMO_SSID")
        else:
            self.ssid = os.getenv("POCKET_OPTION_REAL_SSID")

        if not self.ssid:
            raise PocketOptionError(
                f"POCKET_OPTION_{mode}_SSID не найден в .env"
            )

        self.api: PocketOptionAsync | None = None

        self._connect_lock = asyncio.Lock()
        self._connected = False

    # =========================================================
    # CONNECTION
    # =========================================================

    async def connect(self) -> None:
        """
        Создаёт подключение и ждёт загрузки активов.
        """

        if self._connected and self.api is not None:
            return

        async with self._connect_lock:
            if self._connected and self.api is not None:
                return

            logger.info(
                "Подключение к Pocket Option [%s]...",
                self.mode,
            )

            try:
                self.api = PocketOptionAsync(self.ssid)

                await self.api.wait_for_assets()

                actual_demo = self.api.is_demo()

                expected_demo = self.mode == "DEMO"

                if actual_demo != expected_demo:
                    await self.api.shutdown()
                    self.api = None

                    raise PocketOptionError(
                        f"Несоответствие режима Pocket Option: "
                        f"ожидался {self.mode}, "
                        f"а SSID определился как "
                        f"{'DEMO' if actual_demo else 'REAL'}"
                    )

                self._connected = True

                logger.info(
                    "Pocket Option [%s] подключён",
                    self.mode,
                )

            except Exception:
                self._connected = False
                self.api = None

                logger.exception(
                    "Ошибка подключения Pocket Option [%s]",
                    self.mode,
                )

                raise

    async def ensure_connected(self) -> PocketOptionAsync:
        await self.connect()

        if self.api is None:
            raise PocketOptionError(
                "Pocket Option API не инициализирован"
            )

        return self.api

    async def close(self) -> None:
        """
        Закрывает подключение.
        """

        async with self._connect_lock:
            if self.api is None:
                self._connected = False
                return

            try:
                await self.api.shutdown()
            except Exception:
                logger.exception(
                    "Ошибка при закрытии Pocket Option [%s]",
                    self.mode,
                )
            finally:
                self.api = None
                self._connected = False

        logger.info(
            "Pocket Option [%s] отключён",
            self.mode,
        )

    # =========================================================
    # ACCOUNT
    # =========================================================

    async def is_demo(self) -> bool:
        api = await self.ensure_connected()
        return api.is_demo()

    async def balance(self) -> float:
        api = await self.ensure_connected()

        value = await api.balance()

        return float(value)

    async def payout(self, symbol: str) -> int | float:
        api = await self.ensure_connected()

        value = await api.payout(symbol)

        return value

    # =========================================================
    # ASSETS
    # =========================================================
    async def get_available_assets(self) -> dict[str, dict[str, Any]]:
        """
        Получает доступные активы Pocket Option.

        Возвращает словарь:
        {
            "EURUSD_otc": {
                "symbol": "EURUSD_otc",
                "category": "...",
                "payout": 90,
                "timeframes": [...]
            },
            ...
        }

        Такой формат ожидает Signal Engine.
        """

        api = await self.ensure_connected()

        assets: dict[str, dict[str, Any]] = {}

        # BinaryOptionsToolsV2 предоставляет active_assets()
        try:
            raw_assets = await api.active_assets()
        except AttributeError:
            # Некоторые версии библиотеки могут иметь
            # active_assets как обычный метод.
            raw_assets = api.active_assets()

            if asyncio.iscoroutine(raw_assets):
                raw_assets = await raw_assets

        if not raw_assets:
            return assets

        # ---------------------------------------------------------
        # Вариант 1: библиотека уже вернула dict
        # ---------------------------------------------------------
        if isinstance(raw_assets, dict):

            for symbol, info in raw_assets.items():

                if isinstance(info, dict):
                    item = dict(info)
                    item.setdefault("symbol", symbol)
                else:
                    item = {
                        "symbol": symbol,
                        "value": info,
                    }

                normalized = self._normalize_asset(item)

                normalized_symbol = normalized.get("symbol")

                if normalized_symbol:
                    assets[str(normalized_symbol)] = normalized

        # ---------------------------------------------------------
        # Вариант 2: библиотека вернула list / tuple / set
        # ---------------------------------------------------------
        elif isinstance(raw_assets, (list, tuple, set)):

            for item in raw_assets:

                if isinstance(item, str):
                    item = {
                        "symbol": item
                    }

                if not isinstance(item, dict):
                    continue

                normalized = self._normalize_asset(item)

                normalized_symbol = normalized.get("symbol")

                if normalized_symbol:
                    assets[str(normalized_symbol)] = normalized

        return assets

    @staticmethod
    def _normalize_asset(
        asset: dict[str, Any],
    ) -> dict[str, Any]:

        symbol = (
            asset.get("symbol")
            or asset.get("asset")
            or asset.get("name")
        )

        category = (
            asset.get("category")
            or asset.get("type")
            or "currency"
        )

        payout = asset.get("payout", 0)

        raw_timeframes = asset.get("timeframes")

        if not raw_timeframes:
            raw_timeframes = [1, 3, 5, 10, 15, 30, 60]

        timeframes = []

        for item in raw_timeframes:

            # Уже нормальный формат библиотеки:
            # {"time": 60}
            if isinstance(item, dict):

                value = (
                    item.get("time")
                    or item.get("seconds")
                    or item.get("period")
                )

                if value is None:
                    continue

                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue

                # Если библиотека уже передала секунды
                if value >= 60:
                    seconds = value

                # Если переданы минуты
                else:
                    seconds = value * 60

                timeframes.append({
                    "time": seconds
                })

            # Наш fallback: 1, 3, 5, 10...
            else:

                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue

                timeframes.append({
                    "time": value * 60
                })

        return {
            **asset,
            "symbol": symbol,
            "category": category,
            "payout": payout,
            "timeframes": timeframes,
        }

    # =========================================================
    # CANDLES
    # =========================================================

    async def get_candles(
        self,
        symbol: str,
        timeframe: int,
        limit: int = 100,
        force_refresh: bool = False,
    ) -> list[Candle]:
        """
        Возвращает ЗАКРЫТЫЕ свечи (старые сначала).

        Источник выбирается по таймфрейму на основе диагностики
        (Этап 2), без автоматического переключения при ошибке:
          - 1 минута: history(). get_candles_live() на 1 минуте
            отставал на 2-5 минут и имел пропуски.
          - 5 минут и выше: get_candles_live(). history() там отдаёт
            только окно ~6 часов (22 свечи на 15 мин, 6 на 60 мин).
        """

        if timeframe <= 1:
            return await self._get_candles_history(
                symbol, timeframe, limit
            )

        return await self._get_candles_stream(
            symbol, timeframe, limit
        )

    async def _get_candles_history(
        self,
        symbol: str,
        timeframe: int,
        limit: int = 100,
    ) -> list[Candle]:
        """
        Возвращает ЗАКРЫТЫЕ свечи (старые сначала).

        ИЗМЕНЕНО (Этап 2): раньше использовался get_candles_live(),
        у которого список закрытых свечей отставал на 2-5 минут и имел
        пропуски, а глубина была ограничена ~145 свечами.
        Теперь используется history(): по диагностике он отдаёт свежие
        свечи без пропусков и ~330 штук на 1-минутном таймфрейме.
        Формируемая (is_closed=False) свеча в результат не попадает.

        При ошибке или пустом ответе поднимается исключение. Мы НЕ
        переключаемся молча на старый источник, чтобы не вернуть
        анализ по устаревшим данным.
        """

        api = await self.ensure_connected()

        raw_items = await asyncio.wait_for(
            api.history(symbol, int(timeframe * 60)),
            timeout=30,
        )

        if not raw_items:
            raise PocketOptionError(
                f"history() вернул пустой ответ: {symbol}, "
                f"{timeframe} мин"
            )

        by_time: dict[float, Candle] = {}

        for raw in raw_items:

            # Формируемая свеча ещё не закончилась.
            if (
                isinstance(raw, dict)
                and raw.get("is_closed") is False
            ):
                continue

            candle = self._normalize_candle(raw)

            if candle is not None:
                by_time[candle.timestamp] = candle

        candles = [by_time[key] for key in sorted(by_time)]

        if not candles:
            raise PocketOptionError(
                f"Нет закрытых свечей: {symbol}, {timeframe} мин"
            )

        if len(candles) > limit:
            candles = candles[-limit:]

        logger.info(
            "Свечи %s | timeframe=%s мин | count=%s | "
            "first=%s | last=%s | источник=history",
            symbol,
            timeframe,
            len(candles),
            candles[0].timestamp,
            candles[-1].timestamp,
        )

        return candles

    async def _get_candles_stream(
        self,
        symbol: str,
        timeframe: int,
        limit: int = 100,
    ) -> list[Candle]:

        """
        Закрытые свечи через get_candles_live() (поток).
        Используется на таймфреймах от 5 минут: диагностика показала,
        что там список закрытых свечей свежий, без пропусков и
        глубже, чем у history() (до ~149 свечей).
        """

        api = await self.ensure_connected()

        generator = api.get_candles_live(
            asset=symbol,
            period=int(timeframe * 60),
            hours=max(1.0, (limit * timeframe) / 60),
            max_rows=limit,
        )

        try:
            closed, _forming = await anext(generator)

        finally:
            await generator.aclose()


        candles = []

        for raw in closed:
            candle = self._normalize_candle(raw)

            if candle is not None:
                candles.append(candle)

        # Диагностика порядка свечей.
        if candles:
            ordered = all(
                candles[i].timestamp <= candles[i + 1].timestamp
                for i in range(len(candles) - 1)
            )

            logger.info(
                "Свечи %s | timeframe=%s мин | count=%s | "
                "first=%s | last=%s | ordered=%s | источник=live",
                symbol,
                timeframe,
                len(candles),
                candles[0].timestamp,
                candles[-1].timestamp,
                ordered,
            )

        # На всякий случай ограничиваем результат.
        if len(candles) > limit:
            candles = candles[-limit:]

        return candles


    async def get_candles_live(
        self,
        symbol: str,
        timeframe: int,
        limit: int = 100,
    ) -> tuple[list[Candle], Candle | None]:

        api = await self.ensure_connected()

        generator = api.get_candles_live(
            asset=symbol,
            period=int(timeframe * 60),
            hours=max(1.0, (limit * timeframe) / 60),
            max_rows=limit,
        )

        try:
            closed_raw, forming_raw = await anext(generator)

        finally:
            await generator.aclose()

        closed = []

        for raw in closed_raw:
            candle = self._normalize_candle(raw)

            if candle is not None:
                closed.append(candle)

        forming = self._normalize_candle(forming_raw)

        if len(closed) > limit:
            closed = closed[-limit:]

        return closed, forming

    @staticmethod
    def _normalize_candle(
        raw: Any,
    ) -> Candle | None:

        if raw is None:
            return None

        if isinstance(raw, Candle):
            return raw

        if not isinstance(raw, dict):
            return None

        timestamp = (
            raw.get("time")
            or raw.get("timestamp")
            or raw.get("from")
        )

        if timestamp is None:
            return None

        try:
            timestamp = float(timestamp)

            return Candle(
                timestamp=timestamp,
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            logger.warning(
                "Не удалось преобразовать свечу: %r",
                raw,
            )

            return None

    # =========================================================
    # PRICE
    # =========================================================

    async def get_current_price(
        self,
        symbol: str,
    ) -> float:

        _closed, forming = await self.get_candles_live(
            symbol=symbol,
            timeframe=1,
            limit=10,
        )

        if forming is not None:
            return forming.close

        if _closed:
            return _closed[-1].close

        raise PocketOptionError(
            f"Не удалось получить цену {symbol}"
        )

    # =========================================================
    # TRADING
    # =========================================================

    async def buy(
        self,
        symbol: str,
        amount: float,
        expiration_minutes: int,
    ) -> tuple[str, dict[str, Any]]:

        api = await self.ensure_connected()

        seconds = int(expiration_minutes * 60)

        logger.warning(
            "BUY [%s] symbol=%s amount=%s expiration=%ss",
            self.mode,
            symbol,
            amount,
            seconds,
        )

        trade_id, trade_data = await api.buy(
            asset=symbol,
            amount=float(amount),
            time=seconds,
            check_win=False,
        )

        return str(trade_id), trade_data

    async def sell(
        self,
        symbol: str,
        amount: float,
        expiration_minutes: int,
    ) -> tuple[str, dict[str, Any]]:

        api = await self.ensure_connected()

        seconds = int(expiration_minutes * 60)

        logger.warning(
            "SELL [%s] symbol=%s amount=%s expiration=%ss",
            self.mode,
            symbol,
            amount,
            seconds,
        )

        trade_id, trade_data = await api.sell(
            asset=symbol,
            amount=float(amount),
            time=seconds,
            check_win=False,
        )

        return str(trade_id), trade_data

    async def check_win(
        self,
        deal_id: str,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:

        api = await self.ensure_connected()

        logger.info(
            "Проверяем результат сделки [%s]: %s",
            self.mode,
            deal_id,
        )

        result = await api.check_win(
            deal_id,
            timeout_seconds=timeout_seconds,
        )

        if not isinstance(result, dict):
            return {
                "result": result,
                "profit": 0,
            }

        return result

    async def get_deal_end_time(
        self,
        deal_id: str,
    ) -> Any:

        api = await self.ensure_connected()

        try:
            return await api.get_deal_end_time(deal_id)
        except Exception:
            logger.exception(
                "Не удалось получить время окончания сделки %s",
                deal_id,
            )
            return None


# =============================================================
# SINGLETONS
# =============================================================

_clients: dict[str, PocketOptionMarket] = {}


def get_pocket_option(
    mode: str = "DEMO",
) -> PocketOptionMarket:

    mode = mode.upper().strip()

    if mode not in ("DEMO", "REAL"):
        raise ValueError(
            "mode должен быть DEMO или REAL"
        )

    if mode not in _clients:
        _clients[mode] = PocketOptionMarket(mode)

    return _clients[mode]


async def close_pocket_options() -> None:
    """
    Закрывает все подключения DEMO/REAL.
    """

    clients = list(_clients.values())

    for client in clients:
        await client.close()

    _clients.clear()