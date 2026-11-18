from market.data import Candle
from analysis.indicators import (
    sma,
    momentum,
    rsi,
    macd,
    candle_confirmation,
)


def analyze(candles: list[Candle]) -> dict:

    if len(candles) < 40:
        return {
            "direction": None,
            "score": 0,
            "reason": ["Недостаточно данных"],
            "price": None,
        }

    current_price = candles[-1].close

    # ==========================================
    # ИНДИКАТОРЫ
    # ==========================================

    sma_10 = sma(candles, 10)
    sma_20 = sma(candles, 20)

    mom = momentum(candles, 5)

    rsi_value = rsi(candles, 14)

    macd_data = macd(candles)

    candle_data = candle_confirmation(candles)

    if (
        sma_10 is None
        or sma_20 is None
        or mom is None
        or rsi_value is None
        or macd_data is None
    ):
        return {
            "direction": None,
            "score": 0,
            "reason": [
                "Недостаточно данных для анализа"
            ],
            "price": current_price,
        }

    macd_value = macd_data["macd"]
    macd_signal = macd_data["signal"]
    macd_histogram = macd_data["histogram"]

    # ==========================================
    # SCORE
    # ==========================================

    score_call = 0
    score_put = 0

    reasons_call = []
    reasons_put = []

    # ==========================================
    # 1. ТРЕНД — 0–2
    # ==========================================

    if current_price > sma_10:
        score_call += 1
        reasons_call.append(
            "цена выше SMA 10"
        )

    elif current_price < sma_10:
        score_put += 1
        reasons_put.append(
            "цена ниже SMA 10"
        )

    if sma_10 > sma_20:
        score_call += 1
        reasons_call.append(
            "SMA 10 выше SMA 20"
        )

    elif sma_10 < sma_20:
        score_put += 1
        reasons_put.append(
            "SMA 10 ниже SMA 20"
        )

    # ==========================================
    # 2. MOMENTUM — 0–2
    # ==========================================

    if mom > 0:
        score_call += 2
        reasons_call.append(
            "положительный momentum"
        )

    elif mom < 0:
        score_put += 2
        reasons_put.append(
            "отрицательный momentum"
        )

    # ==========================================
    # 3. RSI — 0–2
    # ==========================================

    if 50 <= rsi_value < 70:

        score_call += 2

        reasons_call.append(
            f"RSI подтверждает рост ({rsi_value:.1f})"
        )

    elif 30 < rsi_value < 50:

        score_put += 2

        reasons_put.append(
            f"RSI подтверждает снижение ({rsi_value:.1f})"
        )

    elif rsi_value >= 70:

        reasons_call.append(
            f"RSI показывает перекупленность ({rsi_value:.1f})"
        )

    elif rsi_value <= 30:

        reasons_put.append(
            f"RSI показывает перепроданность ({rsi_value:.1f})"
        )

    # ==========================================
    # 4. MACD — 0–2
    # ==========================================

    if (
        macd_value > macd_signal
        and macd_histogram > 0
    ):

        score_call += 2

        reasons_call.append(
            "MACD подтверждает рост"
        )

    elif (
        macd_value < macd_signal
        and macd_histogram < 0
    ):

        score_put += 2

        reasons_put.append(
            "MACD подтверждает снижение"
        )

    # ==========================================
    # 5. СВЕЧИ — 0–2
    # ==========================================

    score_call += candle_data["call_score"]
    score_put += candle_data["put_score"]

    reasons_call.extend(
        candle_data["reason_call"]
    )

    reasons_put.extend(
        candle_data["reason_put"]
    )


    # ==========================================
    # ИТОГОВОЕ НАПРАВЛЕНИЕ
    # ==========================================

    score_margin = abs(score_call - score_put)

    # Экспериментальные фильтры.
    # Их эффективность нужно проверить на истории.
    MIN_SCORE = 5
    MIN_SCORE_MARGIN = 2

    if score_call > score_put:
        direction = "CALL"
        score = score_call
        reasons = reasons_call

    elif score_put > score_call:
        direction = "PUT"
        score = score_put
        reasons = reasons_put

    else:
        direction = None
        score = 0
        reasons = ["Нет явного преимущества"]

    # Не выдаём сигнал, если оценка слишком низкая
    # или разница между сторонами слишком мала.
    if direction is not None:
        if score < MIN_SCORE:
            direction = None
            reasons = [
                f"Слабый сигнал: {score}/10, "
                f"минимум {MIN_SCORE}/10"
            ]

        elif score_margin < MIN_SCORE_MARGIN:
            direction = None
            reasons = [
                "Сигналы CALL и PUT недостаточно "
                "различаются"
            ]

    return {
        "direction": direction,
        "score": score if direction is not None else 0,
        "score_call": score_call,
        "score_put": score_put,
        "score_margin": score_margin,
        "reason": reasons,
        "price": current_price,
        "rsi": rsi_value,
        "macd": macd_data,
        "candle_score": {
            "call": candle_data["call_score"],
            "put": candle_data["put_score"],
        },
    }
