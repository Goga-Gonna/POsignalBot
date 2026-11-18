import os

from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parse_admin_ids(raw: str | None) -> frozenset[int]:
    """
    ADMIN_IDS=1443474084
    ADMIN_IDS=1443474084,123456789
    """

    result: set[int] = set()

    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()

        if not part:
            continue

        try:
            result.add(int(part))
        except ValueError:
            raise ValueError(
                f"ADMIN_IDS содержит не число: {part!r}"
            ) from None

    return frozenset(result)


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError(
        "BOT_TOKEN не найден в .env"
    )

# Администраторы: единственные, кто может пользоваться ботом
# на текущем этапе. Позже сюда добавятся роли для других людей
# (см. access.py).
ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS"))

if not ADMIN_IDS:
    raise ValueError(
        "ADMIN_IDS не задан в .env. "
        "Укажите свой Telegram ID, например: ADMIN_IDS=1443474084"
    )


# ============================================================
# POCKET OPTION
# ============================================================

POCKET_OPTION_DEMO_SSID = os.getenv(
    "POCKET_OPTION_DEMO_SSID"
)

# REAL SSID теперь НЕ обязателен. Он нужен только если
# явно включён ALLOW_REAL_TRADING.
POCKET_OPTION_REAL_SSID = os.getenv(
    "POCKET_OPTION_REAL_SSID"
)

if not POCKET_OPTION_DEMO_SSID:
    raise ValueError(
        "POCKET_OPTION_DEMO_SSID не найден в .env"
    )


# ============================================================
# TRADING
# ============================================================

# Общий выключатель открытия сделок (DEMO).
# По умолчанию выключен, как и в trade_manager.py.
TRADING_ENABLED = _env_bool("TRADING_ENABLED", "false")

# Реальные деньги. По умолчанию ЗАПРЕЩЕНО.
# Включается только строкой ALLOW_REAL_TRADING=true в .env.
ALLOW_REAL_TRADING = _env_bool("ALLOW_REAL_TRADING", "false")

if ALLOW_REAL_TRADING and not POCKET_OPTION_REAL_SSID:
    raise ValueError(
        "ALLOW_REAL_TRADING=true, но POCKET_OPTION_REAL_SSID "
        "не найден в .env"
    )

TRADE_AMOUNT = float(
    os.getenv(
        "TRADE_AMOUNT",
        "1",
    )
)

DEFAULT_ACCOUNT_MODE = os.getenv(
    "DEFAULT_ACCOUNT_MODE",
    "DEMO",
).upper()

if DEFAULT_ACCOUNT_MODE not in {
    "DEMO",
    "REAL",
}:
    raise ValueError(
        "DEFAULT_ACCOUNT_MODE должен быть DEMO или REAL"
    )

if DEFAULT_ACCOUNT_MODE == "REAL" and not ALLOW_REAL_TRADING:
    # Не падаем, а безопасно принудительно переключаем на DEMO.
    DEFAULT_ACCOUNT_MODE = "DEMO"

if TRADE_AMOUNT <= 0:
    raise ValueError(
        "TRADE_AMOUNT должен быть больше 0"
    )