"""
Индикаторы технического анализа (Этап 3.2).

Принципы:
  1. Без внешних библиотек (только стандартный Python).
  2. Каждая функция возвращает СЕРИЮ той же длины, что и вход.
     Пока данных мало, значение равно None.
  3. Значение в точке i использует только данные до i включительно.
     Это защита от заглядывания в будущее (look-ahead bias):
     для бэктеста можно один раз посчитать серию и читать значение
     в любой точке. Свойство проверяется отдельным тестом.
  4. Формулы стандартные: EMA с SMA-затравкой, RSI/ATR/ADX по Уайлдеру,
     Bollinger со стандартным отклонением генеральной совокупности.

Старый analysis/indicators.py остаётся без изменений: его использует
текущая strategy.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

Series = list  # list[Optional[float]]


# ============================================================
# БАЗОВЫЕ СКОЛЬЗЯЩИЕ
# ============================================================

def sma(values: Sequence[Optional[float]], period: int) -> Series:
    """Простая скользящая средняя."""

    if period <= 0:
        raise ValueError("period должен быть > 0")

    n = len(values)
    out: Series = [None] * n

    window_sum = 0.0
    count = 0

    for i, value in enumerate(values):

        if value is not None:
            window_sum += value
            count += 1

        j = i - period

        if j >= 0 and values[j] is not None:
            window_sum -= values[j]
            count -= 1

        if i >= period - 1 and count == period:
            out[i] = window_sum / period

    return out


def ema(values: Sequence[Optional[float]], period: int) -> Series:
    """
    Экспоненциальная скользящая средняя.
    Первое значение равно SMA первых `period` значений.
    Допускаются None в начале ряда (например, для сигнальной линии MACD).
    """

    if period <= 0:
        raise ValueError("period должен быть > 0")

    n = len(values)
    out: Series = [None] * n

    run = 0
    start = None

    for i, value in enumerate(values):
        run = run + 1 if value is not None else 0

        if run == period:
            start = i
            break

    if start is None:
        return out

    k = 2.0 / (period + 1)

    previous = sum(values[start - period + 1:start + 1]) / period
    out[start] = previous

    for i in range(start + 1, n):

        value = values[i]

        if value is None:
            continue

        previous = (value - previous) * k + previous
        out[i] = previous

    return out


# ============================================================
# УРОВЕНЬ 2: ИМПУЛЬС
# ============================================================

def _rsi_value(avg_gain: float, avg_loss: float) -> float:

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def rsi(closes: Sequence[float], period: int = 14) -> Series:
    """RSI по Уайлдеру."""

    n = len(closes)
    out: Series = [None] * n

    if n <= period:
        return out

    gains = 0.0
    losses = 0.0

    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)

    avg_gain = gains / period
    avg_loss = losses / period

    out[period] = _rsi_value(avg_gain, avg_loss)

    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]

        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period

        out[i] = _rsi_value(avg_gain, avg_loss)

    return out


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[Series, Series, Series]:
    """Возвращает (линия MACD, сигнальная линия, гистограмма)."""

    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)

    line: Series = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(fast_ema, slow_ema)
    ]

    signal_line = ema(line, signal)

    histogram: Series = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(line, signal_line)
    ]

    return line, signal_line, histogram


def stochastic(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    k_period: int = 14,
    smooth: int = 3,
    d_period: int = 3,
) -> tuple[Series, Series]:
    """Медленный стохастик: возвращает (%K, %D)."""

    n = len(closes)
    raw: Series = [None] * n

    for i in range(k_period - 1, n):

        highest = max(highs[i - k_period + 1:i + 1])
        lowest = min(lows[i - k_period + 1:i + 1])

        span = highest - lowest

        raw[i] = (
            50.0 if span == 0
            else 100.0 * (closes[i] - lowest) / span
        )

    k = sma(raw, smooth)
    d = sma(k, d_period)

    return k, d


def momentum(closes: Sequence[float], period: int = 10) -> Series:
    """Изменение цены за `period` свечей, в процентах (ROC)."""

    n = len(closes)
    out: Series = [None] * n

    for i in range(period, n):

        previous = closes[i - period]

        if previous != 0:
            out[i] = 100.0 * (closes[i] / previous - 1.0)

    return out


def cci(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 20,
) -> Series:
    """Commodity Channel Index (константа Ламберта 0.015)."""

    n = len(closes)
    out: Series = [None] * n

    typical = [
        (h + l + c) / 3.0
        for h, l, c in zip(highs, lows, closes)
    ]

    for i in range(period - 1, n):

        window = typical[i - period + 1:i + 1]
        mean = sum(window) / period
        deviation = sum(abs(x - mean) for x in window) / period

        out[i] = (
            0.0 if deviation == 0
            else (typical[i] - mean) / (0.015 * deviation)
        )

    return out


# ============================================================
# УРОВЕНЬ 3: ВОЛАТИЛЬНОСТЬ И ШУМ
# ============================================================

def true_range(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> list[float]:

    n = len(closes)

    if n == 0:
        return []

    result = [highs[0] - lows[0]]

    for i in range(1, n):
        result.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    return result


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> Series:
    """Average True Range по Уайлдеру."""

    n = len(closes)
    out: Series = [None] * n

    if n <= period:
        return out

    tr = true_range(highs, lows, closes)

    value = sum(tr[1:period + 1]) / period
    out[period] = value

    for i in range(period + 1, n):
        value = (value * (period - 1) + tr[i]) / period
        out[i] = value

    return out


def bollinger(
    closes: Sequence[float],
    period: int = 20,
    multiplier: float = 2.0,
) -> dict[str, Series]:
    """
    Полосы Боллинджера.
    width = (верхняя - нижняя) / средняя (для поиска сжатия),
    percent_b = положение цены внутри полос (0 = нижняя, 1 = верхняя).
    """

    n = len(closes)

    middle: Series = [None] * n
    upper: Series = [None] * n
    lower: Series = [None] * n
    width: Series = [None] * n
    percent_b: Series = [None] * n

    for i in range(period - 1, n):

        window = closes[i - period + 1:i + 1]
        mean = sum(window) / period

        variance = sum((x - mean) ** 2 for x in window) / period
        deviation = variance ** 0.5

        up = mean + multiplier * deviation
        low = mean - multiplier * deviation

        middle[i] = mean
        upper[i] = up
        lower[i] = low

        if mean != 0:
            width[i] = (up - low) / mean

        if up != low:
            percent_b[i] = (closes[i] - low) / (up - low)

    return {
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "width": width,
        "percent_b": percent_b,
    }


def efficiency_ratio(closes: Sequence[float], period: int = 10) -> Series:
    """
    Коэффициент эффективности Кауфмана, 0..1.
    Близко к 1: цена идёт почти по прямой (тренд).
    Близко к 0: цена мечется туда-сюда (рыночный шум).
    """

    n = len(closes)
    out: Series = [None] * n

    for i in range(period, n):

        net = abs(closes[i] - closes[i - period])

        path = sum(
            abs(closes[j] - closes[j - 1])
            for j in range(i - period + 1, i + 1)
        )

        out[i] = 0.0 if path == 0 else net / path

    return out


# ============================================================
# УРОВЕНЬ 1: ТРЕНД
# ============================================================

def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> tuple[Series, Series, Series]:
    """
    ADX по Уайлдеру. Возвращает (ADX, +DI, -DI).
    Первое значение ADX появляется на индексе 2*period - 1.
    """

    n = len(closes)

    adx_out: Series = [None] * n
    plus_out: Series = [None] * n
    minus_out: Series = [None] * n

    if n < 2 * period:
        return adx_out, plus_out, minus_out

    tr = true_range(highs, lows, closes)

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(1, n):

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move

        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    smooth_tr = sum(tr[1:period + 1])
    smooth_plus = sum(plus_dm[1:period + 1])
    smooth_minus = sum(minus_dm[1:period + 1])

    dx: Series = [None] * n

    for i in range(period, n):

        if i > period:
            smooth_tr = smooth_tr - smooth_tr / period + tr[i]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[i]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[i]

        if smooth_tr == 0:
            plus_di = 0.0
            minus_di = 0.0
        else:
            plus_di = 100.0 * smooth_plus / smooth_tr
            minus_di = 100.0 * smooth_minus / smooth_tr

        plus_out[i] = plus_di
        minus_out[i] = minus_di

        total = plus_di + minus_di

        dx[i] = (
            0.0 if total == 0
            else 100.0 * abs(plus_di - minus_di) / total
        )

    value = sum(dx[period:2 * period]) / period
    adx_out[2 * period - 1] = value

    for i in range(2 * period, n):
        value = (value * (period - 1) + dx[i]) / period
        adx_out[i] = value

    return adx_out, plus_out, minus_out


def classify_trend(
    ema_fast: Optional[float],
    ema_mid: Optional[float],
    ema_slow: Optional[float],
    adx_value: Optional[float],
    plus_di: Optional[float],
    minus_di: Optional[float],
    adx_min: float = 20.0,
) -> Optional[str]:
    """
    'UP', 'DOWN' или 'RANGE'; None, если данных не хватает.

    ВНИМАНИЕ: правило и порог adx_min=20 это стартовая гипотеза,
    а не доказанный факт. Их нужно калибровать на бэктесте.
    """

    values = (ema_fast, ema_mid, ema_slow, adx_value, plus_di, minus_di)

    if any(v is None for v in values):
        return None

    if adx_value >= adx_min:

        if ema_fast > ema_mid > ema_slow and plus_di > minus_di:
            return "UP"

        if ema_fast < ema_mid < ema_slow and plus_di < minus_di:
            return "DOWN"

    return "RANGE"


# ============================================================
# УРОВЕНЬ 1: СТРУКТУРА МАКСИМУМОВ И МИНИМУМОВ
# ============================================================

@dataclass(frozen=True)
class Swing:
    index: int            # где находится экстремум
    kind: str             # "H" (максимум) или "L" (минимум)
    price: float
    confirmed_index: int  # когда экстремум стал известен


def swing_points(
    highs: Sequence[float],
    lows: Sequence[float],
    left: int = 3,
    right: int = 3,
) -> list[Swing]:
    """
    Локальные экстремумы. Свеча j это максимум, если её high строго
    выше `left` предыдущих и не ниже `right` следующих high
    (для минимумов зеркально).

    Экстремум становится ИЗВЕСТЕН только на свече j + right.
    Поэтому для анализа в точке i используйте
    swings_known_at(swings, i), иначе будет заглядывание в будущее.
    """

    n = len(highs)
    swings: list[Swing] = []

    for j in range(left, n - right):

        high = highs[j]

        if (
            all(high > highs[k] for k in range(j - left, j))
            and all(high >= highs[k] for k in range(j + 1, j + right + 1))
        ):
            swings.append(Swing(j, "H", high, j + right))

        low = lows[j]

        if (
            all(low < lows[k] for k in range(j - left, j))
            and all(low <= lows[k] for k in range(j + 1, j + right + 1))
        ):
            swings.append(Swing(j, "L", low, j + right))

    return swings


def swings_known_at(swings: Sequence[Swing], index: int) -> list[Swing]:
    """Только те экстремумы, которые уже были подтверждены к свече index."""

    return [s for s in swings if s.confirmed_index <= index]


def market_structure(known_swings: Sequence[Swing]) -> str:
    """
    'UP'    – последние максимум и минимум выше предыдущих (HH + HL),
    'DOWN'  – ниже предыдущих (LH + LL),
    'MIXED' – иначе,
    'UNKNOWN' – экстремумов пока мало.
    """

    highs = [s for s in known_swings if s.kind == "H"]
    lows = [s for s in known_swings if s.kind == "L"]

    if len(highs) < 2 or len(lows) < 2:
        return "UNKNOWN"

    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price

    if higher_high and higher_low:
        return "UP"

    if lower_high and lower_low:
        return "DOWN"

    return "MIXED"


# ============================================================
# УДОБНЫЕ ОБЁРТКИ
# ============================================================

def compute_series(candles: Sequence) -> dict[str, Series]:
    """
    Считает все индикаторы по списку свечей (объекты с полями
    open/high/low/close). Все серии одной длины.
    """

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]

    adx_line, plus_di, minus_di = adx(highs, lows, closes, 14)
    macd_line, macd_signal, macd_hist = macd(closes)
    stoch_k, stoch_d = stochastic(highs, lows, closes)
    bands = bollinger(closes)

    return {
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "adx": adx_line,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "rsi14": rsi(closes, 14),
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "momentum10": momentum(closes, 10),
        "cci20": cci(highs, lows, closes, 20),
        "atr14": atr(highs, lows, closes, 14),
        "bb_middle": bands["middle"],
        "bb_upper": bands["upper"],
        "bb_lower": bands["lower"],
        "bb_width": bands["width"],
        "bb_percent_b": bands["percent_b"],
        "efficiency10": efficiency_ratio(closes, 10),
    }


def snapshot(
    candles: Sequence,
    series: dict[str, Series] | None = None,
    index: int = -1,
) -> dict:
    """Значения всех индикаторов в точке index (по умолчанию последняя)."""

    if series is None:
        series = compute_series(candles)

    n = len(candles)

    if n == 0:
        return {}

    if index < 0:
        index += n

    result = {name: values[index] for name, values in series.items()}

    result["trend"] = classify_trend(
        result["ema9"], result["ema21"], result["ema50"],
        result["adx"], result["plus_di"], result["minus_di"],
    )

    swings = swing_points(
        [c.high for c in candles],
        [c.low for c in candles],
    )

    result["structure"] = market_structure(
        swings_known_at(swings, index)
    )

    return result