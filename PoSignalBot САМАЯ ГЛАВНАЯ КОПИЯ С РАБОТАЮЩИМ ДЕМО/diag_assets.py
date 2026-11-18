"""
Диагностика: какие активы видит бот и почему фильтр их отсеивает.

Ничего не торгует и ничего не меняет в базе. Только читает список
активов Pocket Option (DEMO) и записывает отчёт в assets_report.txt.

Запуск из папки проекта:
    python diag_assets.py
"""

import asyncio
from collections import Counter
from datetime import datetime, timezone

from market.pocket_option import (
    close_pocket_options,
    get_pocket_option,
)
from signals.engine import (
    MIN_PAYOUT,
    get_payout,
    get_timeframes,
    is_currency_pair,
)

REPORT_FILE = "assets_report.txt"


async def main() -> None:

    po = get_pocket_option("DEMO")

    try:
        assets = await po.get_available_assets()
    finally:
        pass

    lines: list[str] = []

    def add(text: str = "") -> None:
        lines.append(text)
        print(text)

    now = datetime.now(timezone.utc)

    add(f"Время (UTC): {now:%Y-%m-%d %H:%M:%S}, день недели: {now:%A}")
    add(f"Всего активов: {len(assets)}")
    add(f"MIN_PAYOUT в engine.py: {MIN_PAYOUT}")
    add()

    categories = Counter(
        str(info.get("category")) for info in assets.values()
    )
    add("Категории (как их вернула библиотека):")
    for name, count in categories.most_common():
        add(f"  {name}: {count}")
    add()

    otc = [s for s in assets if s.lower().endswith("_otc")]
    regular = [s for s in assets if not s.lower().endswith("_otc")]

    add(f"OTC-активов: {len(otc)}")
    add(f"Обычных (не OTC) активов: {len(regular)}")
    add()

    add("=== ОБЫЧНЫЕ (не OTC) АКТИВЫ ===")
    add("символ | категория | payout | подходит как валютная пара | "
        "таймфреймы (сек)")

    for symbol in sorted(regular):
        info = assets[symbol]

        add(
            f"{symbol} | {info.get('category')} | "
            f"{get_payout(info):g} | "
            f"{is_currency_pair(symbol, info)} | "
            f"{sorted(get_timeframes(info))}"
        )

    add()
    add("=== ПЕРВЫЕ 15 OTC-АКТИВОВ (для примера формата) ===")

    for symbol in sorted(otc)[:15]:
        info = assets[symbol]

        add(
            f"{symbol} | {info.get('category')} | "
            f"{get_payout(info):g} | "
            f"{sorted(get_timeframes(info))}"
        )

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    print()
    print(f"Отчёт сохранён: {REPORT_FILE}")

    await close_pocket_options()


if __name__ == "__main__":
    asyncio.run(main())