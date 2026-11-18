"""
Диагностика (Этап 2, часть 2): что умеет библиотека и можно ли
получить свежие свечи на 1-минутном таймфрейме.

Ничего не торгует и не меняет в базе. Использует только тот же вызов
get_candles_live, который уже работает в боте, и просто читает
доступные методы библиотеки.

Запуск из папки проекта (занимает 3-4 минуты):
    python diag_api.py

Результат: файл api_report.txt
"""

import asyncio
import inspect
from datetime import datetime, timezone
from importlib import metadata

from market.pocket_option import (
    close_pocket_options,
    get_pocket_option,
)

REPORT_FILE = "api_report.txt"

SYMBOL = "EURUSD"
STREAM_UPDATES = 6
STREAM_TIMEOUT_SECONDS = 90
DEPTH_HOURS = [1, 3, 6, 12, 24]

lines: list[str] = []


def add(text: str = "") -> None:
    lines.append(text)
    print(text)


def fmt(ts) -> str:
    if ts is None:
        return "—"

    return datetime.fromtimestamp(
        ts, timezone.utc
    ).strftime("%H:%M:%S")


def part_methods(api) -> None:

    add("=== 1. БИБЛИОТЕКА И ЕЁ МЕТОДЫ ===")

    try:
        add(
            "Версия BinaryOptionsToolsV2: "
            f"{metadata.version('BinaryOptionsToolsV2')}"
        )
    except Exception as error:
        add(f"Версия не определена: {error!r}")

    add(f"Класс клиента: {type(api).__name__}")
    add()

    for name in sorted(dir(api)):

        if name.startswith("_"):
            continue

        attribute = getattr(api, name, None)

        if not callable(attribute):
            continue

        try:
            signature = str(inspect.signature(attribute))
        except (TypeError, ValueError):
            signature = "(сигнатура недоступна)"

        doc = (inspect.getdoc(attribute) or "").strip().splitlines()
        first_line = doc[0][:110] if doc else ""

        add(f"{name}{signature}")

        if first_line:
            add(f"    {first_line}")

    add()


async def part_stream(po, api) -> None:

    add(
        f"=== 2. ПОТОК СВЕЧЕЙ {SYMBOL}, 1 мин: "
        "догоняет ли он реальное время ==="
    )

    generator = api.get_candles_live(
        asset=SYMBOL,
        period=60,
        hours=2.0,
        max_rows=200,
    )

    try:
        for index in range(STREAM_UPDATES):

            try:
                closed_raw, forming_raw = await asyncio.wait_for(
                    generator.__anext__(),
                    timeout=STREAM_TIMEOUT_SECONDS,
                )

            except asyncio.TimeoutError:
                add(
                    f"обновление {index + 1}: нет данных "
                    f"{STREAM_TIMEOUT_SECONDS} сек."
                )
                break

            except StopAsyncIteration:
                add(f"обновление {index + 1}: поток завершён")
                break

            closed = [
                c for c in (
                    po._normalize_candle(raw) for raw in closed_raw
                ) if c is not None
            ]

            forming = po._normalize_candle(forming_raw)

            now_ts = datetime.now(timezone.utc).timestamp()

            last = closed[-1].timestamp if closed else None

            age = (
                now_ts - (last + 60) if last is not None else None
            )

            add(
                f"обновление {index + 1}: сейчас {fmt(now_ts)} UTC | "
                f"закрытых {len(closed)} | "
                f"последняя закрытая {fmt(last)} | "
                f"формируемая {fmt(forming.timestamp) if forming else '—'}"
                f" | возраст конца закрытой "
                f"{'—' if age is None else f'{age:+.0f} сек.'}"
            )

    finally:
        try:
            await generator.aclose()
        except Exception:
            pass

    add()


async def part_depth(po, api) -> None:

    add(
        f"=== 3. ГЛУБИНА ИСТОРИИ {SYMBOL}, 1 мин, "
        "параметр hours (max_rows=2000) ==="
    )

    for hours in DEPTH_HOURS:

        generator = api.get_candles_live(
            asset=SYMBOL,
            period=60,
            hours=float(hours),
            max_rows=2000,
        )

        try:
            closed_raw, _forming = await asyncio.wait_for(
                generator.__anext__(),
                timeout=STREAM_TIMEOUT_SECONDS,
            )

            closed = [
                c for c in (
                    po._normalize_candle(raw) for raw in closed_raw
                ) if c is not None
            ]

            gaps = sum(
                1
                for a, b in zip(closed, closed[1:])
                if b.timestamp - a.timestamp != 60
            )

            span = (
                (closed[-1].timestamp - closed[0].timestamp) / 60
                if closed else 0
            )

            add(
                f"hours={hours}: получено {len(closed)} свечей, "
                f"период {span:.0f} мин "
                f"(ожидалось около {hours * 60}), "
                f"разрывов {gaps}"
            )

        except Exception as error:
            add(f"hours={hours}: ОШИБКА {error!r}")

        finally:
            try:
                await generator.aclose()
            except Exception:
                pass

    add()


async def main() -> None:

    add("ДИАГНОСТИКА БИБЛИОТЕКИ И ПОТОКА СВЕЧЕЙ")
    add(
        f"Запуск (UTC, часы компьютера): "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}"
    )
    add()

    po = get_pocket_option("DEMO")
    api = await po.ensure_connected()

    for part in (part_methods, part_stream, part_depth):

        try:
            if part is part_methods:
                part(api)
            else:
                await part(po, api)

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