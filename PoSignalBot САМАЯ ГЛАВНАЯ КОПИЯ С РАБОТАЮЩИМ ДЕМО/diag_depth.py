"""
Глубина истории на 5, 15 и 60 минутах (Этап 2, часть 4).

history() отдаёт окно примерно в 6 часов, поэтому на 15 и 60 минутах
свечей слишком мало. Скрипт проверяет, можно ли получить больше:
  - get_candles_live (старый метод, но на 5 мин он был свежим),
  - compile_candles с разной глубиной,
  - get_ticks с разной глубиной (из тиков можно собрать любые свечи).

Ничего не торгует и не меняет в базе. Запуск (2-4 минуты):
    python diag_depth.py

Результат: файл depth_report.txt
"""

import asyncio
from datetime import datetime, timezone

from market.pocket_option import (
    close_pocket_options,
    get_pocket_option,
)

REPORT_FILE = "depth_report.txt"

SYMBOL = "EURUSD"
TIMEFRAMES = [5, 15, 60]
COMPILE_HOURS = [6, 24, 72]
TICK_HOURS = [6, 24, 48]
CALL_TIMEOUT_SECONDS = 150

lines: list[str] = []


def add(text: str = "") -> None:
    lines.append(text)
    print(text)


def fmt(ts) -> str:
    if ts is None:
        return "—"

    return datetime.fromtimestamp(
        ts, timezone.utc
    ).strftime("%Y-%m-%d %H:%M")


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def describe(label: str, candles: list, step: int) -> None:
    """candles: объекты Candle, старые сначала."""

    if not candles:
        add(f"{label}: свечей нет")
        return

    first = candles[0].timestamp
    last = candles[-1].timestamp

    age = now_ts() - (last + step)

    gaps = sum(
        1
        for a, b in zip(candles, candles[1:])
        if b.timestamp - a.timestamp != step
    )

    add(
        f"{label}: свечей {len(candles)}; "
        f"глубина {(last - first + step) / 3600:.1f} ч; "
        f"{fmt(first)} -> {fmt(last)}; "
        f"возраст конца последней {age:+.0f} сек. (норма 0..{step}); "
        f"разрывов {gaps}"
    )


def only_closed(po, raw_items) -> list:

    result = {}

    for raw in raw_items or []:

        if isinstance(raw, dict) and raw.get("is_closed") is False:
            continue

        candle = po._normalize_candle(raw)

        if candle is not None:
            result[candle.timestamp] = candle

    return [result[key] for key in sorted(result)]


async def main() -> None:

    add("ГЛУБИНА ИСТОРИИ НА 5, 15 И 60 МИНУТАХ")
    add(
        f"Запуск (UTC, часы компьютера): "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}"
    )
    add()

    po = get_pocket_option("DEMO")
    api = await po.ensure_connected()

    for timeframe in TIMEFRAMES:

        step = timeframe * 60

        add(f"=== ТАЙМФРЕЙМ {timeframe} мин ===")

        # 1. Старый метод (get_candles_live), первая выдача
        try:
            closed, _forming = await asyncio.wait_for(
                po.get_candles_live(SYMBOL, timeframe, 1000),
                timeout=CALL_TIMEOUT_SECONDS,
            )

            describe("get_candles_live", closed, step)

        except Exception as error:
            add(f"get_candles_live: ОШИБКА {error!r}")

        # 2. compile_candles с разной глубиной
        for hours in COMPILE_HOURS:

            try:
                raw = await asyncio.wait_for(
                    api.compile_candles(
                        SYMBOL, step, int(hours * 3600)
                    ),
                    timeout=CALL_TIMEOUT_SECONDS,
                )

                describe(
                    f"compile_candles (lookback {hours} ч)",
                    only_closed(po, raw),
                    step,
                )

            except Exception as error:
                add(
                    f"compile_candles (lookback {hours} ч): "
                    f"ОШИБКА {error!r}"
                )

        add()

    # 3. Глубина тиков
    add("=== ГЛУБИНА ТИКОВ ===")

    for hours in TICK_HOURS:

        try:
            ticks = await asyncio.wait_for(
                api.get_ticks(SYMBOL, int(hours * 3600)),
                timeout=CALL_TIMEOUT_SECONDS,
            )

            stamps = []

            for tick in ticks:
                try:
                    value = float(tick[0])
                except (TypeError, ValueError, IndexError):
                    continue

                stamps.append(value / 1000 if value > 1e11 else value)

            if not stamps:
                add(f"get_ticks ({hours} ч): тиков нет")
                continue

            long_pauses = sum(
                1 for a, b in zip(stamps, stamps[1:]) if b - a > 60
            )

            add(
                f"get_ticks ({hours} ч): тиков {len(stamps)}; "
                f"{fmt(stamps[0])} -> {fmt(stamps[-1])}; "
                f"глубина {(stamps[-1] - stamps[0]) / 3600:.1f} ч; "
                f"пауз больше минуты: {long_pauses}"
            )

        except Exception as error:
            add(f"get_ticks ({hours} ч): ОШИБКА {error!r}")

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    print()
    print(f"Отчёт сохранён: {REPORT_FILE}")

    await close_pocket_options()


if __name__ == "__main__":
    asyncio.run(main())