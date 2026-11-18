from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database.db"


# ============================================================
# CONNECTION
# ============================================================

def get_connection() -> sqlite3.Connection:
    """
    Создаёт соединение с SQLite.

    row_factory позволяет обращаться к колонкам
    как к обычному словарю:
        row["symbol"]
    """

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")

    return connection


# ============================================================
# HELPERS
# ============================================================

def _column_exists(
    connection: sqlite3.Connection,
    table: str,
    column: str,
) -> bool:

    rows = connection.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return any(
        row["name"] == column
        for row in rows
    )


def _add_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:

    if not _column_exists(
        connection,
        table,
        column,
    ):
        connection.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )


# ============================================================
# DATABASE INIT / MIGRATION
# ============================================================

def init_database() -> None:
    """
    Создаёт БД при первом запуске и выполняет миграцию
    существующей БД.

    Старые данные НЕ удаляются.
    """

    with closing(get_connection()) as connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                score INTEGER NOT NULL,
                expiration INTEGER NOT NULL,
                price REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                result TEXT,
                result_price REAL,
                checked_at TIMESTAMP
            )
            """
        )
        
        # ----------------------------------------------------
        # USER ONBOARDING
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_onboarding (
                user_id INTEGER PRIMARY KEY,
                completed_at TEXT NOT NULL
            )
            """
        )
        
        
        # ----------------------------------------------------
        # USER SETTINGS
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                account_mode TEXT NOT NULL DEFAULT 'DEMO',
                trading_mode TEXT NOT NULL DEFAULT 'MANUAL',
                trade_amount REAL NOT NULL DEFAULT 1.0,
                updated_at TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # MIGRATION
        # ----------------------------------------------------

        _add_column(
            connection,
            "signals",
            "account_mode",
            "TEXT DEFAULT 'DEMO'",
        )

        _add_column(
            connection,
            "signals",
            "trade_enabled",
            "INTEGER DEFAULT 0",
        )

        _add_column(
            connection,
            "signals",
            "trade_amount",
            "REAL",
        )

        _add_column(
            connection,
            "signals",
            "deal_id",
            "TEXT",
        )

        _add_column(
            connection,
            "signals",
            "trade_status",
            "TEXT DEFAULT 'not_traded'",
        )

        _add_column(
            connection,
            "signals",
            "entry_price",
            "REAL",
        )

        _add_column(
            connection,
            "signals",
            "exit_price",
            "REAL",
        )

        _add_column(
            connection,
            "signals",
            "profit",
            "REAL DEFAULT 0",
        )

        _add_column(
            connection,
            "signals",
            "trade_created_at",
            "TIMESTAMP",
        )

        _add_column(
            connection,
            "signals",
            "trade_checked_at",
            "TIMESTAMP",
        )

        _add_column(
            connection,
            "signals",
            "trade_expires_at",
            "TIMESTAMP",
        )

        _add_column(
            connection,
            "signals",
            "trade_error",
            "TEXT",
        )

        _add_column(
            connection,
            "signals",
            "balance_before",
            "REAL",
        )

        _add_column(
            connection,
            "signals",
            "balance_after",
            "REAL",
        )

        # Направление, которое пользователь реально выбрал
        # при открытии сделки. Поле direction теперь хранит
        # ТОЛЬКО рекомендацию стратегии и больше не перезаписывается.
        _add_column(
            connection,
            "signals",
            "user_direction",
            "TEXT",
        )

        # ----------------------------------------------------
        # INDEXES
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signals_user_created
            ON signals(user_id, created_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signals_status
            ON signals(status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signals_trade_status
            ON signals(trade_status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signals_deal_id
            ON signals(deal_id)
            """
        )

        connection.commit()


# ============================================================
# DATETIME
# ============================================================

def utc_now() -> str:
    """
    Возвращает UTC timestamp в ISO формате.
    """

    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# SIGNALS
# ============================================================

def has_recent_signal(
    user_id: int,
    symbol: str,
    direction: str,
    expiration: int,
    seconds: int = 60,
) -> bool:

    with closing(get_connection()) as connection:

        row = connection.execute(
            """
            SELECT id
            FROM signals
            WHERE user_id = ?
              AND symbol = ?
              AND direction = ?
              AND expiration = ?
              AND datetime(created_at) >=
                  datetime('now', ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                user_id,
                symbol,
                direction,
                expiration,
                f"-{seconds} seconds",
            ),
        ).fetchone()

        return row is not None


def save_signal(
    user_id: int,
    symbol: str,
    direction: str,
    score: int,
    expiration: int,
    price: float,
    account_mode: str = "DEMO",
    trade_enabled: bool = False,
    trade_amount: float | None = None,
    status: str = "pending",
    trade_status: str = "not_traded",
) -> int:

    account_mode = account_mode.upper()

    if account_mode not in ("DEMO", "REAL"):
        raise ValueError(
            "account_mode должен быть DEMO или REAL"
        )

    with closing(get_connection()) as connection:

        cursor = connection.execute(
            """
            INSERT INTO signals (
                user_id,
                symbol,
                direction,
                score,
                expiration,
                price,
                status,
                account_mode,
                trade_enabled,
                trade_amount,
                trade_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                symbol,
                direction,
                score,
                expiration,
                price,
                status,
                account_mode,
                int(trade_enabled),
                trade_amount,
                trade_status,
            ),
        )

        connection.commit()

        return int(cursor.lastrowid)


# ============================================================
# SIGNAL GETTERS
# ============================================================

def get_signal(
    signal_id: int,
) -> dict[str, Any] | None:

    with closing(get_connection()) as connection:

        row = connection.execute(
            """
            SELECT *
            FROM signals
            WHERE id = ?
            """,
            (signal_id,),
        ).fetchone()

        if row is None:
            return None

        return dict(row)


def get_pending_signals(
    limit: int = 100,
) -> list[dict[str, Any]]:

    with closing(get_connection()) as connection:

        rows = connection.execute(
            """
            SELECT *
            FROM signals
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


# ============================================================
# TRADE GETTERS
# ============================================================

def _trade_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """
    Для сообщений о результате сделки показываем направление,
    в котором сделка РЕАЛЬНО была открыта (user_direction).
    Поле direction в БД при этом остаётся рекомендацией стратегии,
    поэтому статистику стратегии это не портит.
    """

    data = dict(row)

    executed = data.get("user_direction")

    if executed:
        data["direction"] = executed

    return data


def get_open_trades(
    limit: int = 100,
) -> list[dict[str, Any]]:

    with closing(get_connection()) as connection:

        rows = connection.execute(
            """
            SELECT *
            FROM signals
            WHERE trade_status = 'open'
              AND deal_id IS NOT NULL
              AND deal_id != ''
            ORDER BY trade_created_at ASC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [
            _trade_row_to_dict(row)
            for row in rows
        ]


def get_trade_by_deal_id(
    deal_id: str,
) -> dict[str, Any] | None:

    with closing(get_connection()) as connection:

        row = connection.execute(
            """
            SELECT *
            FROM signals
            WHERE deal_id = ?
            LIMIT 1
            """,
            (str(deal_id),),
        ).fetchone()

        if row is None:
            return None

        return _trade_row_to_dict(row)


# ============================================================
# TRADE EXECUTION
# ============================================================

def mark_trade_awaiting_execution(
    signal_id: int,
) -> bool:
    """
    Атомарно резервирует сигнал для открытия сделки.

    not_traded -> awaiting_execution
    """

    with closing(get_connection()) as connection:

        cursor = connection.execute(
            """
            UPDATE signals
            SET trade_status = 'awaiting_execution',
                trade_error = NULL
            WHERE id = ?
              AND trade_status = 'not_traded'
              AND trade_enabled = 1
            """,
            (signal_id,),
        )

        connection.commit()

        return cursor.rowcount > 0


def mark_trade_open(
    signal_id: int,
    deal_id: str,
    entry_price: float | None = None,
    trade_created_at: str | None = None,
    trade_expires_at: str | None = None,
    balance_before: float | None = None,
    direction: str | None = None,
) -> bool:

    if trade_created_at is None:
        trade_created_at = utc_now()

    with closing(get_connection()) as connection:

        if direction is None:
            cursor = connection.execute(
                """
                UPDATE signals
                SET deal_id = ?,
                    entry_price = ?,
                    balance_before = ?,
                    trade_status = 'open',
                    trade_created_at = ?,
                    trade_expires_at = ?,
                    trade_error = NULL
                WHERE id = ?
                  AND trade_status = 'awaiting_execution'
                """,
                (
                    str(deal_id),
                    entry_price,
                    balance_before,
                    trade_created_at,
                    trade_expires_at,
                    signal_id,
                ),
            )
        else:
            direction = str(direction).strip().upper()

            if direction not in {"CALL", "PUT"}:
                raise ValueError(
                    f"Invalid trade direction: {direction!r}"
                )

            cursor = connection.execute(
                """
                UPDATE signals
                SET deal_id = ?,
                    entry_price = ?,
                    balance_before = ?,
                    trade_status = 'open',
                    trade_created_at = ?,
                    trade_expires_at = ?,
                    trade_error = NULL,
                    user_direction = ?
                WHERE id = ?
                  AND trade_status = 'awaiting_execution'
                """,
                (
                    str(deal_id),
                    entry_price,
                    balance_before,
                    trade_created_at,
                    trade_expires_at,
                    direction,
                    signal_id,
                ),
            )

        connection.commit()

        return cursor.rowcount > 0


def mark_trade_error(
    signal_id: int,
    error: str,
) -> bool:

    with closing(get_connection()) as connection:

        cursor = connection.execute(
            """
            UPDATE signals
            SET trade_status = 'error',
                trade_error = ?,
                checked_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND trade_status = 'awaiting_execution'
            """,
            (
                str(error)[:2000],
                signal_id,
            ),
        )

        connection.commit()

        return cursor.rowcount > 0


# ============================================================
# TRADE BALANCE
# ============================================================

def update_trade_balance(
    signal_id: int,
    balance_before: float | None = None,
    balance_after: float | None = None,
) -> bool:

    updates = []
    values: list[Any] = []

    if balance_before is not None:
        updates.append("balance_before = ?")
        values.append(float(balance_before))

    if balance_after is not None:
        updates.append("balance_after = ?")
        values.append(float(balance_after))

    if not updates:
        return False

    values.append(signal_id)

    with closing(get_connection()) as connection:

        cursor = connection.execute(
            f"""
            UPDATE signals
            SET {", ".join(updates)}
            WHERE id = ?
            """,
            tuple(values),
        )

        connection.commit()

        return cursor.rowcount > 0


# ============================================================
# TRADE RESULT
# ============================================================

def update_trade_result(
    signal_id: int,
    result: str,
    profit: float = 0,
    exit_price: float | None = None,
    checked_at: str | None = None,
    balance_after: float | None = None,
) -> bool:
    """
    Атомарно завершает открытую сделку.

    ВАЖНО:
        Только open -> completed.

    Если другой процесс уже завершил сделку,
    функция вернёт False.
    """

    result = result.upper()

    if result not in ("WIN", "LOSS", "DRAW"):
        raise ValueError(
            "result должен быть WIN, LOSS или DRAW"
        )

    if checked_at is None:
        checked_at = utc_now()

    with closing(get_connection()) as connection:

        if balance_after is None:

            cursor = connection.execute(
                """
                UPDATE signals
                SET status = 'completed',
                    result = ?,
                    profit = ?,
                    exit_price = ?,
                    checked_at = ?,
                    trade_checked_at = ?,
                    trade_status = 'completed',
                    trade_error = NULL
                WHERE id = ?
                  AND trade_status = 'open'
                  AND deal_id IS NOT NULL
                  AND deal_id != ''
                """,
                (
                    result,
                    float(profit or 0),
                    exit_price,
                    checked_at,
                    checked_at,
                    signal_id,
                ),
            )

        else:

            cursor = connection.execute(
                """
                UPDATE signals
                SET status = 'completed',
                    result = ?,
                    profit = ?,
                    exit_price = ?,
                    balance_after = ?,
                    checked_at = ?,
                    trade_checked_at = ?,
                    trade_status = 'completed',
                    trade_error = NULL
                WHERE id = ?
                  AND trade_status = 'open'
                  AND deal_id IS NOT NULL
                  AND deal_id != ''
                """,
                (
                    result,
                    float(profit or 0),
                    exit_price,
                    float(balance_after),
                    checked_at,
                    checked_at,
                    signal_id,
                ),
            )

        connection.commit()

        return cursor.rowcount > 0


# ============================================================
# OLD COMPATIBILITY FUNCTION
# ============================================================

def update_signal_result(
    signal_id: int,
    result: str,
    result_price: float | None = None,
) -> bool:
    """
    Совместимость со старым result_checker.

    Если это реальная торговая запись,
    также переводим trade_status в completed.
    """

    result = result.upper()

    with closing(get_connection()) as connection:

        cursor = connection.execute(
            """
            UPDATE signals
            SET status = 'completed',
                result = ?,
                result_price = ?,
                checked_at = CURRENT_TIMESTAMP,
                trade_status = CASE
                    WHEN deal_id IS NOT NULL
                         AND deal_id != ''
                    THEN 'completed'
                    ELSE trade_status
                END
            WHERE id = ?
            """,
            (
                result,
                result_price,
                signal_id,
            ),
        )

        connection.commit()

        return cursor.rowcount > 0


# ============================================================
# CANCEL / NOT TRADED
# ============================================================

def mark_signal_not_traded(
    signal_id: int,
) -> bool:

    with closing(get_connection()) as connection:

        cursor = connection.execute(
            """
            UPDATE signals
            SET trade_status = 'not_traded'
            WHERE id = ?
              AND deal_id IS NULL
              AND trade_status IN (
                  'awaiting_execution',
                  'error',
                  'not_traded'
              )
            """,
            (signal_id,),
        )

        connection.commit()

        return cursor.rowcount > 0


# ============================================================
# STATISTICS
# ============================================================

def get_statistics(
    user_id: int | None = None,
) -> dict[str, Any]:
    with closing(get_connection()) as connection:
        where = ""
        params: tuple[Any, ...] = ()

        if user_id is not None:
            where = "AND user_id = ?"
            params = (user_id,)

        total_row = connection.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM signals
            WHERE 1=1 {where}
            """,
            params,
        ).fetchone()

        completed_filter = """
            trade_enabled = 1
            AND deal_id IS NOT NULL
            AND TRIM(deal_id) != ''
            AND trade_status = 'completed'
        """

        completed_params: tuple[Any, ...] = ()
        user_filter = ""

        if user_id is not None:
            user_filter = "AND user_id = ?"
            completed_params = (user_id,)

        rows = connection.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END), 0) AS losses,
                COALESCE(SUM(CASE WHEN result = 'DRAW' THEN 1 ELSE 0 END), 0) AS draws,
                COUNT(*) AS traded,
                COALESCE(SUM(profit), 0) AS profit
            FROM signals
            WHERE {completed_filter}
            {user_filter}
            """,
            completed_params,
        ).fetchone()

        total = int(total_row["total"] or 0)
        wins = int(rows["wins"] or 0)
        losses = int(rows["losses"] or 0)
        draws = int(rows["draws"] or 0)
        traded = int(rows["traded"] or 0)
        profit = float(rows["profit"] or 0)

        decisive = wins + losses
        win_rate = (wins / decisive * 100) if decisive else 0.0

        return {
            "total": total,
            "traded": traded,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "profit": profit,
            "win_rate": win_rate,
        }


# ============================================================
# USER ONBOARDING
# ============================================================

def is_user_onboarded(user_id: int) -> bool:
    """
    Проверяет, завершил ли пользователь регистрацию в боте.
    """

    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT user_id
            FROM user_onboarding
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        return row is not None


def complete_user_onboarding(user_id: int) -> None:
    """
    Сохраняет завершение регистрации пользователя.

    Повторный вызов не создаёт дубликатов.
    """

    with closing(get_connection()) as connection:
        connection.execute(
            """
            INSERT INTO user_onboarding (user_id, completed_at)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, utc_now()),
        )

        connection.commit()


# ============================================================
# DEBUG / DATABASE INFO
# ============================================================

def get_table_columns(
    table: str = "signals",
) -> list[str]:

    with closing(get_connection()) as connection:

        rows = connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()

        return [
            row["name"]
            for row in rows
        ]



# ============================================================
# USER SETTINGS
# ============================================================

DEFAULT_USER_SETTINGS = {
    "account_mode": "DEMO",
    "trading_mode": "MANUAL",
    "trade_amount": 1.0,
}


def get_user_settings(user_id: int) -> dict[str, Any]:
    """
    Возвращает настройки пользователя.
    Если пользователь новый, создаёт настройки по умолчанию.
    """

    with closing(get_connection()) as connection:
        connection.execute(
            """
            INSERT INTO user_settings (
                user_id,
                account_mode,
                trading_mode,
                trade_amount,
                updated_at
            )
            VALUES (?, 'DEMO', 'MANUAL', 1.0, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, utc_now()),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT user_id, account_mode, trading_mode,
                   trade_amount, updated_at
            FROM user_settings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                f"Не удалось загрузить настройки пользователя {user_id}"
            )

        return dict(row)


def update_user_settings(
    user_id: int,
    *,
    account_mode: str | None = None,
    trading_mode: str | None = None,
    trade_amount: float | None = None,
) -> dict[str, Any]:
    """
    Обновляет только переданные настройки.
    Неизменённые параметры сохраняются.
    """

    current = get_user_settings(user_id)

    if account_mode is not None:
        account_mode = account_mode.strip().upper()

        if account_mode not in {"DEMO", "REAL"}:
            raise ValueError(
                "account_mode должен быть DEMO или REAL"
            )

        current["account_mode"] = account_mode

    if trading_mode is not None:
        trading_mode = trading_mode.strip().upper()

        if trading_mode not in {"MANUAL", "AUTO"}:
            raise ValueError(
                "trading_mode должен быть MANUAL или AUTO"
            )

        current["trading_mode"] = trading_mode

    if trade_amount is not None:
        try:
            trade_amount = float(trade_amount)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Сумма сделки должна быть числом"
            ) from exc

        if not 0 < trade_amount < float("inf"):
            raise ValueError(
                "Сумма должна быть положительным конечным числом"
            )

        current["trade_amount"] = trade_amount

    with closing(get_connection()) as connection:
        connection.execute(
            """
            UPDATE user_settings
            SET account_mode = ?,
                trading_mode = ?,
                trade_amount = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (
                current["account_mode"],
                current["trading_mode"],
                current["trade_amount"],
                utc_now(),
                user_id,
            ),
        )

        connection.commit()

    return get_user_settings(user_id)


# ============================================================
# AUTO INIT
# ============================================================

if __name__ == "__main__":
    init_database()

    print("================================")
    print("DATABASE INITIALIZED")
    print("================================")
    print(f"DB: {DB_PATH}")
    print()
    print("signals columns:")

    for column in get_table_columns():
        print(f"  - {column}")

    print()
    print("Migration completed successfully.")