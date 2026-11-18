from market.pocket_option import get_pocket_option
from analysis.strategy import analyze


MIN_PAYOUT = 80


EXPIRATION_CONFIG = {
    1: {
        "timeframe": 1,
        "limit": 100,
    },
    3: {
        "timeframe": 1,
        "limit": 100,
    },
    5: {
        "timeframe": 5,
        "limit": 100,
    },
    10: {
        "timeframe": 5,
        "limit": 100,
    },
    20: {
        "timeframe": 15,
        "limit": 100,
    },
    60: {
        "timeframe": 60,
        "limit": 100,
    },
}


def is_currency_pair(
    symbol: str,
    info: dict,
) -> bool:

    category = str(
        info.get("category", "")
    ).lower()

    return category == "currency"


def get_timeframes(
    info: dict,
) -> list[int]:

    raw_timeframes = info.get(
        "timeframes",
        [],
    )

    result = []

    for item in raw_timeframes:

        if not isinstance(item, dict):
            continue

        value = item.get("time")

        if value is None:
            continue

        try:
            value = int(value)

        except (
            TypeError,
            ValueError,
        ):
            continue

        result.append(value)

    return result


def supports_timeframe(
    info: dict,
    timeframe: int,
) -> bool:

    supported = get_timeframes(info)

    required_seconds = timeframe * 60

    return required_seconds in supported


def get_payout(
    info: dict,
) -> float:

    payout = info.get(
        "payout",
        0,
    )

    try:
        return float(payout)

    except (
        TypeError,
        ValueError,
    ):
        return 0.0


async def find_best_signal(
    expiration: int,
) -> dict:

    config = EXPIRATION_CONFIG.get(
        expiration
    )

    if config is None:

        raise ValueError(
            f"Неподдерживаемая экспирация: "
            f"{expiration}"
        )

    timeframe = config["timeframe"]
    limit = config["limit"]

    print()
    print("================================")
    print("Поиск сигнала")
    print(
        f"Экспирация: {expiration} мин"
    )
    print(
        f"Timeframe: {timeframe} мин"
    )
    print(
        f"Минимальный payout: "
        f"{MIN_PAYOUT}%"
    )
    print("================================")

    po = get_pocket_option()

    try:

        assets = (
            await po.get_available_assets()
        )

    except Exception as error:

        print(
            "Ошибка получения активов:",
            error,
        )

        return {
            "status": "api_error",
            "error": str(error),
        }

    if not assets:

        return {
            "status": "api_error",
            "error": (
                "Список активов пуст"
            ),
        }

    print(
        f"Всего доступных активов: "
        f"{len(assets)}"
    )

    candidates = []

    for symbol, info in assets.items():

        if not is_currency_pair(
            symbol,
            info,
        ):
            continue

        if not supports_timeframe(
            info,
            timeframe,
        ):
            continue

        payout = get_payout(info)

        if payout < MIN_PAYOUT:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "info": info,
                "payout": payout,
            }
        )

    print(
        f"Подходящих валютных пар: "
        f"{len(candidates)}"
    )

    if not candidates:

        return {
            "status": "no_signal",
            "reason": (
                "Нет валютных пар, "
                "соответствующих условиям"
            ),
        }

    candidates.sort(
        key=lambda item: item["payout"],
        reverse=True,
    )

    best_signal = None

    successful_symbols = 0
    failed_symbols = 0

    for candidate in candidates:

        symbol = candidate["symbol"]
        payout = candidate["payout"]

        print()
        print(
            f"Анализируем {symbol} "
            f"| payout={payout:.0f}%"
        )

        try:

            candles = await po.get_candles(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
            )

            successful_symbols += 1

        except Exception as error:

            failed_symbols += 1

            print(
                f"Ошибка получения свечей "
                f"{symbol}: {error}"
            )

            continue

        try:

            analysis = analyze(
                candles
            )

        except Exception as error:

            failed_symbols += 1

            print(
                f"Ошибка анализа "
                f"{symbol}: {error}"
            )

            continue

        direction = analysis.get(
            "direction"
        )

        score = analysis.get(
            "score",
            0,
        )

        price = analysis.get(
            "price"
        )

        reason = analysis.get(
            "reason",
            [],
        )

        print(
            f"Результат {symbol}: "
            f"{direction} "
            f"| score={score}/10"
        )

        if direction is None:
            continue

        if score <= 0:
            continue

        signal = {
            "status": "signal",
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "price": price,
            "expiration": expiration,
            "timeframe": timeframe,
            "payout": payout,
            "reason": reason,
        }

        if best_signal is None:

            best_signal = signal

        else:

            if (
                signal["score"]
                > best_signal["score"]
            ):

                best_signal = signal

            elif (
                signal["score"]
                == best_signal["score"]
                and signal["payout"]
                > best_signal["payout"]
            ):

                best_signal = signal

    print()
    print("================================")

    print(
        "Успешно проанализировано: "
        f"{successful_symbols}"
    )

    print(
        f"Ошибок: {failed_symbols}"
    )

    if best_signal is None:

        print(
            "Сильного сигнала не найдено"
        )

        print("================================")

        return {
            "status": "no_signal",
            "successful_symbols":
                successful_symbols,
            "failed_symbols":
                failed_symbols,
        }

    print("Лучший сигнал:")

    print(
        f"{best_signal['symbol']} "
        f"| {best_signal['direction']} "
        f"| score="
        f"{best_signal['score']}/10 "
        f"| payout="
        f"{best_signal['payout']:.0f}%"
    )

    print("================================")

    return best_signal