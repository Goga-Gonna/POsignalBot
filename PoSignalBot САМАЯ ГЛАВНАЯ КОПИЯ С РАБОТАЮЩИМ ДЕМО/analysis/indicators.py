from market.data import Candle


def sma(candles: list[Candle], period: int) -> float | None:
    if len(candles) < period:
        return None

    closes = [c.close for c in candles[-period:]]

    return sum(closes) / period



def momentum(
    candles: list[Candle],
    period: int = 5,
) -> float | None:
    if period <= 0 or len(candles) <= period:
        return None

    current = candles[-1].close
    previous = candles[-period - 1].close

    if previous == 0:
        return None

    # Изменение цены в процентах.
    return ((current - previous) / previous) * 100



def rsi(
    candles: list[Candle],
    period: int = 14
) -> float | None:

    if len(candles) <= period:
        return None

    closes = [c.close for c in candles]

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0.0)

        else:
            gains.append(0.0)
            losses.append(abs(change))

    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    average_gain = sum(recent_gains) / period
    average_loss = sum(recent_losses) / period

    if average_loss == 0:
        if average_gain == 0:
            return 50.0

        return 100.0

    relative_strength = average_gain / average_loss

    return 100 - (
        100 / (1 + relative_strength)
    )


def macd(
    candles: list[Candle],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> dict | None:

    closes = [c.close for c in candles]

    minimum_required = slow_period + signal_period

    if len(closes) < minimum_required:
        return None

    def ema(
        values: list[float],
        period: int
    ) -> float:

        multiplier = 2 / (period + 1)

        result = sum(values[:period]) / period

        for price in values[period:]:
            result = (
                (price - result) * multiplier
                + result
            )

        return result

    macd_values = []

    for i in range(slow_period, len(closes) + 1):

        current_closes = closes[:i]

        fast_ema = ema(
            current_closes,
            fast_period
        )

        slow_ema = ema(
            current_closes,
            slow_period
        )

        macd_value = fast_ema - slow_ema

        macd_values.append(macd_value)

    if len(macd_values) < signal_period:
        return None

    signal_value = ema(
        macd_values,
        signal_period
    )

    macd_value = macd_values[-1]

    histogram = macd_value - signal_value

    return {
        "macd": macd_value,
        "signal": signal_value,
        "histogram": histogram,
    }


def candle_confirmation(
    candles: list[Candle]
) -> dict:

    if len(candles) < 5:
        return {
            "call_score": 0,
            "put_score": 0,
            "reason_call": [],
            "reason_put": [],
        }

    last = candles[-1]
    previous = candles[-2]
    third = candles[-3]

    call_score = 0
    put_score = 0

    reason_call = []
    reason_put = []

    # ==========================================
    # 1. Направление последней свечи
    # ==========================================

    last_bullish = last.close > last.open
    last_bearish = last.close < last.open

    if last_bullish:
        call_score += 1
        reason_call.append(
            "последняя свеча бычья"
        )

    elif last_bearish:
        put_score += 1
        reason_put.append(
            "последняя свеча медвежья"
        )

    # ==========================================
    # 2. Серия последних свечей
    # ==========================================

    previous_bullish = previous.close > previous.open
    previous_bearish = previous.close < previous.open

    third_bullish = third.close > third.open
    third_bearish = third.close < third.open

    # Три бычьи свечи подряд
    if (
        last_bullish
        and previous_bullish
        and third_bullish
    ):
        call_score += 1
        reason_call.append(
            "три последние свечи бычьи"
        )

    # Три медвежьи свечи подряд
    elif (
        last_bearish
        and previous_bearish
        and third_bearish
    ):
        put_score += 1
        reason_put.append(
            "три последние свечи медвежьи"
        )

    # ==========================================
    # 3. Фильтр силы последней свечи
    # ==========================================

    candle_range = last.high - last.low
    candle_body = abs(last.close - last.open)

    if candle_range > 0:

        body_ratio = candle_body / candle_range

        # Сильная бычья свеча
        if (
            last_bullish
            and body_ratio >= 0.6
        ):
            call_score += 1
            reason_call.append(
                "сильное тело бычьей свечи"
            )

        # Сильная медвежья свеча
        elif (
            last_bearish
            and body_ratio >= 0.6
        ):
            put_score += 1
            reason_put.append(
                "сильное тело медвежьей свечи"
            )

    # ==========================================
    # Ограничиваем свечной блок максимумом 2
    # ==========================================

    call_score = min(call_score, 2)
    put_score = min(put_score, 2)

    return {
        "call_score": call_score,
        "put_score": put_score,
        "reason_call": reason_call,
        "reason_put": reason_put,
    }