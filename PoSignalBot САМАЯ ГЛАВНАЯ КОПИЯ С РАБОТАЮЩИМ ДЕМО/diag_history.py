"""
Проверка нового источника свечей (history) на всех таймфреймах.

Идёт ТЕМ ЖЕ путём, что и бот: PocketOptionMarket.get_candles().
Ничего не торгует и не меняет в базе.

Показывает по каждой паре и таймфрейму:
  - сколько свечей и какая глубина (для EMA 200 нужно 250+),
  - свежесть последней закрытой свечи,
  - пропуски,
  - пройдёт ли выборка проверку качества бота (check_candles_quality),
и сверяет минутные свечи из history с собранными из тиков.

Запуск из папки проекта (около минуты):
    python diag_history.py

Результат: файл history_report.txt
"""

import asyncio
from datetime import datetime, timezone

from market.pocket_option import (
    close_pocket_options,
    get_pocket_option,
)
from signals.engine import check_candles_quality

REPORT_FILE = "history_report.txt"

CASES = [
    ("EURUSD", 1),
    ("AUDCAD", 1),
    ("GBPJPY", 1),
    ("EURUSD", 5),
    ("EURUSD", 15),
    ("EURUSD", 60),
]

LIMIT = 1000

lines: list[str] = []


def add(text: str = "") -> None:
    lines.append(text)
    print(text)


def fmt(ts) -> str:
    if ts is None:
        return "—"

    return datetime.fromtimestamp(
        ts, timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


async def check_case(po, symbol: str, timeframe: int) -> None:

    step = timeframe * 60

    add(f"--- {symbol} | {timeframe} мин ---")

    try:
        candles = await po.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=LIMIT,
        )

    except Exception as error:
        add(f"ОШИБКА: {error!r}")
        add()
        return

    current = now_ts()

    first = candles[0].timestamp
    last = candles[-1].timestamp

    age = current - (last + step)

    gaps = [
        (a.timestamp, b.timestamp)
        for a, b in zip(candles, candles[1:])
        if b.timestamp - a.timestamp != step
    ]

    depth_hours = (last - first + step) / 3600

    add(
        f"Свечей: {len(candles)}; глубина {depth_hours:.1f} ч; "
        f"от {fmt(first)} до {fmt(last)} (UTC)"
    )
    add(
        f"Сейчас {fmt(current)}; возраст конца последней закрытой "
        f"свечи {age:+.0f} сек. (норма 0..{step})"
    )
    add(f"Разрывов: {len(gaps)}")

    for start, end in gaps[:5]:
        add(f"  {fmt(start)} -> {fmt(end)}")

    add(
        "Хватает для EMA 200 (250+ свечей): "
        f"{'да' if len(candles) >= 250 else 'НЕТ'}"
    )

    ok, reason = check_candles_quality(candles, timeframe)

    add(f"Проверка качества бота: {'ПРОЙДЕНА' if ok else 'НЕ пройдена'}"
        f" ({reason})")
    add()


async def compare_with_ticks(po, api) -> None:

    add("=== СВЕРКА history И СВЕЧЕЙ ИЗ ТИКОВ: EURUSD, 1 мин ===")

    try:
        candles = await po.get_candles("EURUSD", 1, LIMIT)

        ticks = await asyncio.wait_for(
            api.get_ticks("EURUSD", 1800),
            timeout=60,
        )

    except Exception as error:
        add(f"ОШИБКА: {error!r}")
        add()
        return

    buckets: dict[int, list[float]] = {}

    for tick in ticks:
        try:
            ts = float(tick[0])
            price = float(tick[1])
        except (TypeError, ValueError, IndexError):
            continue

        if ts > 1e11:
            ts /= 1000

        buckets.setdefault(int(ts // 60 * 60), []).append(price)

    keys = sorted(buckets)[1:-1]

    differences = []
    ranges = []

    for candle in candles:

        key = int(candle.timestamp)

        if key not in keys:
            continue

        prices = buckets[key]

        differences.append(abs(candle.close - prices[-1]))
        ranges.append(candle.high - candle.low)

    if not differences:
        add("Пересечений нет, сравнить нечего.")
        add()
        return

    average_difference = sum(differences) / len(differences)
    average_range = sum(ranges) / len(ranges)

    add(
        f"Сравнено свечей: {len(differences)}; "
        f"средняя разница цены закрытия: {average_difference:.6f}; "
        f"максимальная: {max(differences):.6f}; "
        f"средний размах свечи: {average_range:.6f}"
    )

    if average_range > 0:
        add(
            "Разница относительно размаха свечи: "
            f"{average_difference / average_range * 100:.1f}% "
            "(чем ближе к 0, тем лучше совпадение)"
        )

    add()


async def main() -> None:

    add("ПРОВЕРКА ИСТОЧНИКА СВЕЧЕЙ history")
    add(
        f"Запуск (UTC, часы компьютера): "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}"
    )
    add()

    po = get_pocket_option("DEMO")
    api = await po.ensure_connected()

    for symbol, timeframe in CASES:
        await check_case(po, symbol, timeframe)

    await compare_with_ticks(po, api)

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    print(f"Отчёт сохранён: {REPORT_FILE}")

    await close_pocket_options()


if __name__ == "__main__":
    asyncio.run(main())