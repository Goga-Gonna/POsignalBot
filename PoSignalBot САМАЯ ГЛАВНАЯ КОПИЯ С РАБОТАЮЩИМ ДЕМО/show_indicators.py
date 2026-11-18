"""
Показывает индикаторы по всем парам из хранилища свечей.
Только читает market_data.db, ничего не торгует.

Запуск из папки проекта:
    python show_indicators.py        (таймфрейм 5 минут)
    python show_indicators.py 1      (1 минута)
    python show_indicators.py 15     (15 минут)
"""

import sys
import time

from analysis.ta import compute_series, snapshot
from services.collector import get_store_stats, init_store, load_candles


def fmt(value, digits: int = 1) -> str:

    if value is None:
        return "—"

    return f"{value:.{digits}f}"


def main() -> None:

    timeframe = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    init_store()

    symbols = sorted(
        {
            row["symbol"]
            for row in get_store_stats()
            if row["timeframe"] == timeframe
        }
    )

    if not symbols:
        print(
            f"В хранилище нет свечей {timeframe} мин. "
            "Дождитесь обхода сборщика (первый обход идёт "
            "10-15 минут после запуска бота)."
        )
        return

    print(f"Индикаторы, таймфрейм {timeframe} мин (последняя закрытая свеча)")
    print()
    print(
        "пара    | свечей | тренд | структура | ADX  | RSI  | "
        "эффект.рынка | ширина BB % | ATR(пунктов)"
    )

    now = time.time()

    for symbol in symbols:

        candles = load_candles(symbol, timeframe, limit=400)

        if len(candles) < 60:
            print(f"{symbol:<7} | {len(candles):>6} | мало данных")
            continue

        values = snapshot(candles)

        stale = (
            now - (candles[-1].timestamp + timeframe * 60)
            > timeframe * 60 * 3
        )

        # Размер пункта: 0.01 для пар с йеной, иначе 0.0001
        pip = 0.01 if symbol.endswith("JPY") else 0.0001

        atr_pips = (
            values["atr14"] / pip if values["atr14"] is not None else None
        )

        width = (
            values["bb_width"] * 100
            if values["bb_width"] is not None else None
        )

        print(
            f"{symbol:<7} | {len(candles):>6} | "
            f"{values['trend'] or '—':<5} | "
            f"{values['structure']:<9} | "
            f"{fmt(values['adx']):>4} | {fmt(values['rsi14']):>4} | "
            f"{fmt(values['efficiency10'], 2):>12} | "
            f"{fmt(width, 3):>11} | {fmt(atr_pips):>12}"
            f"{'  (данные устарели)' if stale else ''}"
        )

    print()
    print(
        "Подсказка: эффективность рынка ближе к 1 означает ровное "
        "движение, ближе к 0 означает шум. ADX выше 20-25 это "
        "выраженный тренд. Пороги пока ориентировочные и будут "
        "откалиброваны на истории."
    )


if __name__ == "__main__":
    main()