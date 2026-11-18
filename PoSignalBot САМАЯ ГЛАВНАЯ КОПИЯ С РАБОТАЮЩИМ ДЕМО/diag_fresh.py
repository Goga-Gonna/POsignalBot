"""
Диагностика (Этап 2, часть 3): где взять СВЕЖИЕ 1-минутные свечи.

Проверяет другие методы библиотеки, которые нашлись в отчёте
api_report.txt: history, get_ticks, compile_candles и живую подписку.
Ничего не торгует и не меняет в базе, только читает.

Запуск из папки проекта (занимает около 3 минут):
    python diag_fresh.py

Результат: файл fresh_report.txt
"""

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from market.pocket_option import (
    close_pocket_options,
    get_pocket_option,
)

REPORT_FILE = "fresh_report.txt"

SYMBOL = "EURUSD"
TIMEOUT_SECONDS = 60
SUBSCRIPTION_SECONDS = 150

lines: list[str] = []


def add(text: str = "") -> None:
    lines.append(text)
    print(text)


def short(value, size: int = 220) -> str:
    return repr(value)[:size]


def to_seconds(value) -> float:
    number = float(value)

    # Миллисекунды -> секунды.
    return number / 1000 if number > 1e11 else number


def fmt(ts) -> str:
    if ts is None:
        return "—"

    return datetime.fromtimestamp(
        ts, timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def item_ts(item):
    """Время элемента: из словаря или из пары (время, цена)."""

    if isinstance(item, dict):
        for key in ("time", "timestamp", "from", "t"):
            if key in item and item[key] is not None:
                return to_seconds(item[key])

        return None

    if isinstance(item, (list, tuple)) and item:
        return to_seconds(item[0])

    return None


async def call(function, *args, timeout=TIMEOUT_SECONDS):
    """Вызывает метод библиотеки (async или обычный)."""

    result = function(*args)

    if inspect.isawaitable(result):
        result = await asyncio.wait_for(result, timeout=timeout)

    return result


def describe_series(name: str, items: list, step: int | None) -> None:
    """Печатает свежесть и непрерывность списка элементов с временем."""

    add(f"{name}: элементов {len(items)}")

    if not items:
        add()
        return

    add(f"  первый: {short(items[0])}")
    add(f"  последний: {short(items[-1])}")

    stamps = [item_ts(i) for i in items]
    stamps = [s for s in stamps if s is not None]

    if not stamps:
        add("  время элементов определить не удалось")
        add()
        return

    current = now_ts()

    add(
        f"  от {fmt(stamps[0])} до {fmt(stamps[-1])}; "
        f"сейчас {fmt(current)} (UTC)"
    )

    if step:
        age = current - (stamps[-1] + step)

        gaps = sum(
            1 for a, b in zip(stamps, stamps[1:]) if b - a != step
        )

        add(
            f"  возраст конца последней свечи: {age:+.0f} сек. "
            f"(норма 0..{step}); разрывов: {gaps}"
        )
    else:
        add(
            f"  возраст последнего элемента: "
            f"{current - stamps[-1]:+.1f} сек."
        )

    add()


def build_candles_from_ticks(ticks: list, step: int = 60) -> list[dict]:
    """Собирает свечи из тиков по границам UTC. Последняя (неполная) отбрасывается."""

    buckets: dict[int, list[float]] = {}

    for tick in ticks:
        ts = item_ts(tick)

        try:
            price = float(tick[1])
        except (TypeError, ValueError, IndexError):
            continue

        if ts is None:
            continue

        buckets.setdefault(int(ts // step * step), []).append(price)

    keys = sorted(buckets)

    candles = []

    for key in keys[:-1]:
        prices = buckets[key]

        candles.append(
            {
                "time": key,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
            }
        )

    return candles


async def part_server_time(api) -> None:

    add("=== 1. ВРЕМЯ СЕРВЕРА ===")

    try:
        server = await call(api.get_server_time)

        server = to_seconds(server)

        add(
            f"Сервер: {fmt(server)}; компьютер: {fmt(now_ts())}; "
            f"разница (сервер минус компьютер): "
            f"{server - now_ts():+.1f} сек."
        )

    except Exception as error:
        add(f"ОШИБКА {error!r}")

    add()


async def part_history(api) -> None:

    add("=== 2. history(asset, 60) ===")

    try:
        items = await call(api.history, SYMBOL, 60)
        describe_series("history", list(items), 60)

    except Exception as error:
        add(f"ОШИБКА {error!r}")
        add()


async def part_ticks(api) -> None:

    add("=== 3. get_ticks(asset, 1800) и свечи из тиков ===")

    try:
        ticks = list(await call(api.get_ticks, SYMBOL, 1800))

        describe_series("get_ticks (30 минут)", ticks, None)

        candles = build_candles_from_ticks(ticks, 60)

        describe_series("свечи 1 мин, собранные из тиков", candles, 60)

    except Exception as error:
        add(f"ОШИБКА {error!r}")
        add()


async def part_compile(api) -> None:

    add("=== 4. compile_candles(asset, 60, 1800) ===")

    try:
        items = await call(api.compile_candles, SYMBOL, 60, 1800)
        describe_series("compile_candles", list(items), 60)

    except Exception as error:
        add(f"ОШИБКА {error!r}")
        add()


async def part_subscription(api) -> None:

    add(
        f"=== 5. subscribe_symbol_time_aligned({SYMBOL}, 60 сек), "
        f"{SUBSCRIPTION_SECONDS} сек. ==="
    )

    subscription = None

    try:
        subscription = await call(
            api.subscribe_symbol_time_aligned,
            SYMBOL,
            timedelta(seconds=60),
        )

        add(f"Тип подписки: {type(subscription).__name__}")

        public = [
            name for name in dir(subscription)
            if not name.startswith("_")
        ]

        add(f"Публичные методы подписки: {public}")

        deadline = now_ts() + SUBSCRIPTION_SECONDS
        count = 0

        while now_ts() < deadline:

            remaining = max(1.0, deadline - now_ts())

            try:
                item = await asyncio.wait_for(
                    subscription.__anext__(),
                    timeout=min(remaining, 75),
                )

            except asyncio.TimeoutError:
                add("нет новых данных 75 сек.")
                break

            except StopAsyncIteration:
                add("подписка завершилась")
                break

            count += 1

            stamp = item_ts(item)

            add(
                f"получено {count}: сейчас {fmt(now_ts())} | "
                f"время свечи {fmt(stamp)} | {short(item, 180)}"
            )

        add(f"Всего получено: {count}")

    except Exception as error:
        add(f"ОШИБКА {error!r}")

    finally:
        try:
            await call(api.unsubscribe, SYMBOL)
        except Exception:
            pass

    add()


async def main() -> None:

    add("ДИАГНОСТИКА СВЕЖИХ СВЕЧЕЙ")
    add(
        f"Запуск (UTC, часы компьютера): "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}"
    )
    add()

    po = get_pocket_option("DEMO")
    api = await po.ensure_connected()

    for part in (
        part_server_time,
        part_history,
        part_ticks,
        part_compile,
        part_subscription,
    ):
        try:
            await part(api)

        except Exception as error:
            add(f"ОШИБКА в {part.__name__}: {error!r}")
            add()

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    print()
    print(f"Отчёт сохранён: {REPORT_FILE}")

    await close_pocket_options()


if __name__ == "__main__":
    asyncio.run(main())