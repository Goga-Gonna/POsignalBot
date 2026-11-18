"""
Диагностика качества свечей Pocket Option (Этап 2).

Ничего не торгует и не меняет в базе. Читает свечи и проверяет:
  1. Совпадает ли время свечей со временем UTC на вашем компьютере
     (или сдвинуто, например на +2 часа).
  2. Насколько свежая последняя закрытая свеча (задержка данных).
  3. Есть ли пропуски внутри истории.
  4. Сколько истории реально отдаёт источник.

Результат: файл candles_report.txt и CSV-файлы candles_<пара>_<tf>m.csv.

Запуск из папки проекта (часы Windows должны быть точными!):
    python diag_candles.py
"""

import asyncio
import csv
import math
from datetime import datetime, timezone

from market.pocket_option import (
    close_pocket_options,
    get_pocket_option,
)

REPORT_FILE = "candles_report.txt"

SYMBOLS = ["EURUSD", "AUDCAD", "GBPJPY"]
LIMIT = 100

# Повторные замеры EURUSD, чтобы увидеть, стабильна ли задержка.
REPEAT_SYMBOL = "EURUSD"
REPEAT_TIMES = 3
REPEAT_PAUSE_SECONDS = 20

# Проверка глубины истории.
DEPTH_SYMBOL = "EURUSD"
DEPTH_LIMIT = 1000

lines: list[str] = []


def add(text: str = "") -> None:
    lines.append(text)
    print(text)


def fmt(ts: float | None) -> str:
    if ts is None:
        return "—"

    return datetime.fromtimestamp(
        ts, timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


def analyze_candles(
    symbol: str,
    timeframe: int,
    closed: list,
    forming,
    now_ts: float,
) -> None:

    step = timeframe * 60

    add(f"--- {symbol} | таймфрейм {timeframe} мин ---")
    add(f"Закрытых свечей получено: {len(closed)}")

    if not closed:
        add("Свечей нет.")
        add()
        return

    first_ts = closed[0].timestamp
    last_ts = closed[-1].timestamp

    add(f"Первая свеча: {fmt(first_ts)} (метка времени как есть)")
    add(f"Последняя закрытая: {fmt(last_ts)}")
    add(f"Формируемая свеча: {fmt(forming.timestamp) if forming else '—'}")
    add(f"Сейчас (UTC, часы компьютера): {fmt(now_ts)}")

    # 1. Сдвиг времени
    expected_open = math.floor(now_ts / step) * step

    if forming is not None:
        reference_ts = forming.timestamp
        reference_name = "формируемой свечи"
    else:
        reference_ts = last_ts + step
        reference_name = "последней закрытой + шаг"

    offset_minutes = (reference_ts - expected_open) / 60

    add(
        f"Сдвиг времени по {reference_name}: "
        f"{offset_minutes:+.1f} мин "
        "(около 0 = метки совпадают с UTC; около +120 или -120 = "
        "сдвиг часового пояса; отрицательное малое число = данные "
        "отстают)"
    )

    # 2. Задержка (с поправкой на целый часовой сдвиг, если он есть)
    shift_minutes = 0

    if abs(offset_minutes) >= 30:
        shift_minutes = round(offset_minutes / 30) * 30

    if shift_minutes:
        add(
            f"Обнаружен сдвиг меток времени около {shift_minutes:+d} мин; "
            "ниже задержка считается уже с поправкой на него."
        )

    last_end = last_ts + step - shift_minutes * 60
    lag_seconds = now_ts - last_end

    candles_behind = max(0, int(lag_seconds // step))

    add(
        f"Возраст конца последней закрытой свечи: "
        f"{lag_seconds:+.0f} сек. "
        f"(норма: от 0 до {step}); "
        f"пропущено свежих свечей: {candles_behind}"
    )

    # 3. Пропуски внутри истории
    gaps = []

    for previous, current in zip(closed, closed[1:]):
        diff = current.timestamp - previous.timestamp

        if diff != step:
            gaps.append((previous.timestamp, current.timestamp, diff))

    missing_total = sum(
        max(0, int(round(diff / step)) - 1)
        for _, _, diff in gaps
        if diff > step
    )

    duplicates = sum(1 for _, _, diff in gaps if diff <= 0)

    span_minutes = (last_ts - first_ts) / 60
    expected_count = int(round((last_ts - first_ts) / step)) + 1

    add(
        f"Период: {span_minutes:.0f} мин; "
        f"ожидалось свечей: {expected_count}; "
        f"получено: {len(closed)}"
    )
    add(
        f"Пропусков (мест с разрывом): {len(gaps)}; "
        f"не хватает свечей: {missing_total}; "
        f"дубликатов/неупорядоченных: {duplicates}"
    )

    for start, end, diff in gaps[:8]:
        add(
            f"  разрыв: {fmt(start)} -> {fmt(end)} "
            f"({diff / 60:.0f} мин)"
        )

    if len(gaps) > 8:
        add(f"  ... и ещё разрывов: {len(gaps) - 8}")

    # 4. Подозрительные свечи
    broken = sum(
        1
        for c in closed
        if not (c.low <= min(c.open, c.close)
                and c.high >= max(c.open, c.close))
    )

    flat = sum(1 for c in closed if c.high == c.low)

    add(
        f"Некорректных свечей (high/low не охватывают open/close): "
        f"{broken}; плоских (high=low): {flat}"
    )

    # CSV
    file_name = f"candles_{symbol}_{timeframe}m.csv"

    with open(file_name, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["timestamp", "time_as_utc", "open", "high", "low", "close"]
        )

        for c in closed:
            writer.writerow(
                [c.timestamp, fmt(c.timestamp),
                 c.open, c.high, c.low, c.close]
            )

    add(f"Свечи сохранены: {file_name}")
    add()


async def fetch(po, symbol: str, timeframe: int, limit: int):

    closed, forming = await po.get_candles_live(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
    )

    return closed, forming


async def main() -> None:

    po = get_pocket_option("DEMO")

    add("ДИАГНОСТИКА СВЕЧЕЙ POCKET OPTION")
    add(
        f"Время запуска (UTC, часы компьютера): "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}"
    )
    add()

    # Основные замеры
    for symbol in SYMBOLS:
        try:
            closed, forming = await fetch(po, symbol, 1, LIMIT)

            analyze_candles(
                symbol, 1, closed, forming,
                datetime.now(timezone.utc).timestamp(),
            )

        except Exception as error:
            add(f"--- {symbol}: ОШИБКА {error!r} ---")
            add()

    # Другой таймфрейм
    try:
        closed, forming = await fetch(po, REPEAT_SYMBOL, 5, LIMIT)

        analyze_candles(
            REPEAT_SYMBOL, 5, closed, forming,
            datetime.now(timezone.utc).timestamp(),
        )

    except Exception as error:
        add(f"--- {REPEAT_SYMBOL} 5 мин: ОШИБКА {error!r} ---")
        add()

    # Повторные замеры задержки
    add(f"=== ПОВТОРНЫЕ ЗАМЕРЫ ЗАДЕРЖКИ: {REPEAT_SYMBOL}, 1 мин ===")

    for index in range(REPEAT_TIMES):

        try:
            closed, forming = await fetch(po, REPEAT_SYMBOL, 1, 30)

            now_ts = datetime.now(timezone.utc).timestamp()

            if closed:
                last_end = closed[-1].timestamp + 60

                reference = (
                    forming.timestamp if forming is not None
                    else last_end
                )
                offset_min = (
                    reference - math.floor(now_ts / 60) * 60
                ) / 60

                shift_min = (
                    round(offset_min / 30) * 30
                    if abs(offset_min) >= 30 else 0
                )

                add(
                    f"замер {index + 1}: сейчас {fmt(now_ts)}, "
                    f"последняя закрытая {fmt(closed[-1].timestamp)}, "
                    f"сдвиг времени {shift_min:+d} мин, "
                    f"возраст конца с поправкой "
                    f"{now_ts - (last_end - shift_min * 60):+.0f} сек. "
                    f"(норма 0..60)"
                )
            else:
                add(f"замер {index + 1}: свечей нет")

        except Exception as error:
            add(f"замер {index + 1}: ОШИБКА {error!r}")

        if index < REPEAT_TIMES - 1:
            await asyncio.sleep(REPEAT_PAUSE_SECONDS)

    add()

    # Глубина истории
    add(
        f"=== ГЛУБИНА ИСТОРИИ: {DEPTH_SYMBOL}, 1 мин, "
        f"запрошено {DEPTH_LIMIT} ==="
    )

    try:
        closed, _forming = await fetch(
            po, DEPTH_SYMBOL, 1, DEPTH_LIMIT
        )

        add(f"Получено свечей: {len(closed)}")

        if closed:
            add(
                f"От {fmt(closed[0].timestamp)} "
                f"до {fmt(closed[-1].timestamp)}"
            )

    except Exception as error:
        add(f"ОШИБКА {error!r}")

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    print()
    print(f"Отчёт сохранён: {REPORT_FILE}")

    await close_pocket_options()


if __name__ == "__main__":
    asyncio.run(main())