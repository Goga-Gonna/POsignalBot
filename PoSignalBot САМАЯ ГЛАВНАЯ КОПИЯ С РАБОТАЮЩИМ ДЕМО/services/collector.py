"""
Хранилище свечей и фоновый сборщик (Этап 3.1).

Зачем: Pocket Option отдаёт только короткое окно истории
(1 мин: ~5,5 часов; 5/15/60 мин: ~149 свечей). Для EMA 200, старшего
таймфрейма и бэктеста этого мало. Сборщик периодически дописывает
свежие ЗАКРЫТЫЕ свечи в локальную базу market_data.db, и история
растёт сама, пока бот работает.

Правила безопасности:
  - сборщик только читает данные и пишет в market_data.db;
  - он ничего не торгует и не трогает database.db;
  - любая его ошибка записывается в лог и НЕ останавливает бота;
  - отключается строкой COLLECTOR_ENABLED=false в .env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from market.data import Candle
from market.pocket_option import get_pocket_option
from signals.engine import is_currency_pair

logger = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
STORE_PATH = BASE_DIR / "market_data.db"

COLLECTOR_ENABLED = os.getenv(
    "COLLECTOR_ENABLED",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}

# Таймфреймы в минутах и сколько свечей просить у источника.
TIMEFRAME_LIMITS: dict[int, int] = {
    1: 400,
    5: 200,
    15: 200,
    60: 200,
}

# Окно 1-минутной истории ~5,5 часов, поэтому обход раз в 20 минут
# ничего не теряет, а нагрузка на соединение небольшая.
SWEEP_INTERVAL_SECONDS = 20 * 60

# Первая проверка через минуту после старта, чтобы не мешать запуску.
FIRST_SWEEP_DELAY_SECONDS = 60

# Пауза между запросами к Pocket Option.
PAUSE_BETWEEN_CALLS_SECONDS = 1.0


# ============================================================
# STORE
# ============================================================

def _connect() -> sqlite3.Connection:

    connection = sqlite3.connect(STORE_PATH, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")

    return connection


def init_store() -> None:

    with closing(_connect()) as connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol    TEXT    NOT NULL,
                timeframe INTEGER NOT NULL,
                ts        INTEGER NOT NULL,
                open      REAL    NOT NULL,
                high      REAL    NOT NULL,
                low       REAL    NOT NULL,
                close     REAL    NOT NULL,
                PRIMARY KEY (symbol, timeframe, ts)
            ) WITHOUT ROWID
            """
        )

        connection.commit()


def save_candles(
    symbol: str,
    timeframe: int,
    candles: list[Candle],
) -> int:
    """
    Сохраняет закрытые свечи (дубликаты перезаписываются).
    Возвращает количество свечей в запросе.
    """

    if not candles:
        return 0

    rows = [
        (
            symbol,
            int(timeframe),
            int(c.timestamp),
            float(c.open),
            float(c.high),
            float(c.low),
            float(c.close),
        )
        for c in candles
    ]

    with closing(_connect()) as connection:

        connection.executemany(
            """
            INSERT OR REPLACE INTO candles
                (symbol, timeframe, ts, open, high, low, close)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

        connection.commit()

    return len(rows)


def load_candles(
    symbol: str,
    timeframe: int,
    limit: int | None = None,
    since_ts: int | None = None,
) -> list[Candle]:
    """
    Читает свечи из хранилища, старые сначала.

    limit    – вернуть только последние N свечей;
    since_ts – только свечи с ts >= since_ts.
    """

    query = (
        "SELECT ts, open, high, low, close FROM candles "
        "WHERE symbol = ? AND timeframe = ?"
    )

    params: list = [symbol, int(timeframe)]

    if since_ts is not None:
        query += " AND ts >= ?"
        params.append(int(since_ts))

    if limit is not None:
        query = (
            f"SELECT * FROM ({query} ORDER BY ts DESC LIMIT ?) "
            "ORDER BY ts ASC"
        )
        params.append(int(limit))
    else:
        query += " ORDER BY ts ASC"

    with closing(_connect()) as connection:
        rows = connection.execute(query, params).fetchall()

    return [
        Candle(
            timestamp=float(ts),
            open=o,
            high=h,
            low=l,
            close=c,
        )
        for ts, o, h, l, c in rows
    ]


def get_store_stats() -> list[dict]:
    """Сводка по хранилищу: пара, таймфрейм, количество, период, пропуски."""

    with closing(_connect()) as connection:

        groups = connection.execute(
            """
            SELECT symbol, timeframe, COUNT(*), MIN(ts), MAX(ts)
            FROM candles
            GROUP BY symbol, timeframe
            ORDER BY symbol, timeframe
            """
        ).fetchall()

        result = []

        for symbol, timeframe, count, first_ts, last_ts in groups:

            step = timeframe * 60

            stamps = [
                row[0]
                for row in connection.execute(
                    "SELECT ts FROM candles WHERE symbol = ? "
                    "AND timeframe = ? ORDER BY ts",
                    (symbol, timeframe),
                )
            ]

            gaps = sum(
                1
                for a, b in zip(stamps, stamps[1:])
                if b - a != step
            )

            result.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "count": count,
                    "first_ts": first_ts,
                    "last_ts": last_ts,
                    "gaps": gaps,
                }
            )

    return result


# ============================================================
# COLLECTOR
# ============================================================

class CandleCollector:

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:

        if not COLLECTOR_ENABLED:
            logger.info(
                "Сборщик свечей отключён (COLLECTOR_ENABLED=false)."
            )
            return

        if self._running:
            return

        init_store()

        self._running = True

        self._task = asyncio.create_task(
            self._run(),
            name="candle-collector",
        )

        logger.info(
            "Сборщик свечей запущен. Файл: %s; обход раз в %s мин.",
            STORE_PATH,
            SWEEP_INTERVAL_SECONDS // 60,
        )

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

        logger.info("Сборщик свечей остановлен.")

    async def _run(self) -> None:

        try:
            await asyncio.sleep(FIRST_SWEEP_DELAY_SECONDS)

            while self._running:

                try:
                    await self.sweep_once()

                except asyncio.CancelledError:
                    raise

                except Exception:
                    logger.exception(
                        "Ошибка обхода сборщика свечей "
                        "(бот продолжает работать)."
                    )

                await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            raise

    async def sweep_once(self) -> dict:
        """Один обход всех подходящих пар и таймфреймов."""

        po = get_pocket_option("DEMO")

        assets = await po.get_available_assets()

        symbols = sorted(
            symbol
            for symbol, info in assets.items()
            if is_currency_pair(symbol, info)
        )

        started = datetime.now(timezone.utc)

        saved_total = 0
        errors = 0

        for symbol in symbols:

            for timeframe, limit in TIMEFRAME_LIMITS.items():

                try:
                    candles = await po.get_candles(
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=limit,
                    )

                    saved = await asyncio.to_thread(
                        save_candles,
                        symbol,
                        timeframe,
                        candles,
                    )

                    saved_total += saved

                except asyncio.CancelledError:
                    raise

                except Exception as error:
                    errors += 1

                    logger.warning(
                        "Сборщик: не удалось получить %s %s мин: %r",
                        symbol,
                        timeframe,
                        error,
                    )

                await asyncio.sleep(PAUSE_BETWEEN_CALLS_SECONDS)

        seconds = (
            datetime.now(timezone.utc) - started
        ).total_seconds()

        logger.info(
            "Сборщик: обход завершён за %.0f сек.; пар: %s; "
            "свечей записано/обновлено: %s; ошибок: %s",
            seconds,
            len(symbols),
            saved_total,
            errors,
        )

        return {
            "symbols": len(symbols),
            "saved": saved_total,
            "errors": errors,
        }


# ============================================================
# SINGLETON / CONVENIENCE
# ============================================================

_collector: CandleCollector | None = None


def get_collector() -> CandleCollector:

    global _collector

    if _collector is None:
        _collector = CandleCollector()

    return _collector


async def start_collector() -> None:
    await get_collector().start()


async def stop_collector() -> None:

    if _collector is not None:
        await _collector.stop()