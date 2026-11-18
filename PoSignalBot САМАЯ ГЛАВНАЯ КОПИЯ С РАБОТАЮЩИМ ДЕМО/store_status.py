"""
Показывает, что накопилось в хранилище свечей (market_data.db).
Только читает. Запуск из папки проекта:
    python store_status.py
"""

from datetime import datetime, timezone

from services.collector import STORE_PATH, get_store_stats, init_store


def fmt(ts) -> str:
    return datetime.fromtimestamp(
        ts, timezone.utc
    ).strftime("%Y-%m-%d %H:%M")


def main() -> None:

    init_store()

    stats = get_store_stats()

    print(f"Файл: {STORE_PATH}")

    if not stats:
        print("Хранилище пусто. Сборщик запишет первые данные "
              "через минуту после запуска бота.")
        return

    print()
    print("пара    | ТФ,мин | свечей | период (UTC)                | разрывов")

    for row in stats:
        print(
            f"{row['symbol']:<7} | {row['timeframe']:>6} | "
            f"{row['count']:>6} | "
            f"{fmt(row['first_ts'])} - {fmt(row['last_ts'])} | "
            f"{row['gaps']}"
        )


if __name__ == "__main__":
    main()