import os
import sqlite3

from datetime import (
    datetime,
    timezone,
)


DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

PLAN_FREE = "FREE"
PLAN_VIP = "VIP"

STATUS_ACTIVE = "ACTIVE"
STATUS_EXPIRED = "EXPIRED"
STATUS_CANCELLED = "CANCELLED"


def utc_now():
    return datetime.now(
        timezone.utc
    )


def datetime_to_text(
    value
):
    if value is None:
        return None

    if isinstance(
        value,
        str,
    ):
        return value

    if value.tzinfo is not None:
        value = value.astimezone(
            timezone.utc
        ).replace(
            tzinfo=None
        )

    return value.strftime(
        TIME_FORMAT
    )


def utc_now_text():
    return datetime_to_text(
        utc_now()
    )


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


def init_user_subscription_tables():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            users (
                id INTEGER
                PRIMARY KEY
                AUTOINCREMENT,

                telegram_user_id INTEGER
                NOT NULL
                UNIQUE,

                username TEXT,

                first_name TEXT,

                last_name TEXT,

                created_at TEXT
                NOT NULL,

                updated_at TEXT
                NOT NULL,

                last_seen_at TEXT
                NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            subscriptions (
                id INTEGER
                PRIMARY KEY
                AUTOINCREMENT,

                user_id INTEGER
                NOT NULL,

                plan TEXT
                NOT NULL,

                status TEXT
                NOT NULL,

                starts_at TEXT
                NOT NULL,

                expires_at TEXT,

                source TEXT
                NOT NULL
                DEFAULT 'MANUAL',

                created_at TEXT
                NOT NULL,

                updated_at TEXT
                NOT NULL,

                FOREIGN KEY (
                    user_id
                )
                REFERENCES users (
                    id
                )
                ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_users_telegram_user_id
            ON users (
                telegram_user_id
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_subscriptions_user_id
            ON subscriptions (
                user_id
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_subscriptions_status
            ON subscriptions (
                status
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_subscriptions_expires_at
            ON subscriptions (
                expires_at
            )
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_one_active_subscription_per_user
            ON subscriptions (
                user_id
            )
            WHERE status = 'ACTIVE'
            """
        )

        connection.commit()


def register_or_update_user(
    telegram_user_id,
    username=None,
    first_name=None,
    last_name=None,
):
    now = utc_now_text()

    telegram_user_id = int(
        telegram_user_id
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (
                telegram_user_id,
                username,
                first_name,
                last_name,
                created_at,
                updated_at,
                last_seen_at
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )

            ON CONFLICT (
                telegram_user_id
            )

            DO UPDATE SET
                username =
                    COALESCE(
                        excluded.username,
                        users.username
                    ),

                first_name =
                    COALESCE(
                        excluded.first_name,
                        users.first_name
                    ),

                last_name =
                    COALESCE(
                        excluded.last_name,
                        users.last_name
                    ),

                updated_at =
                    excluded.updated_at,

                last_seen_at =
                    excluded.last_seen_at
            """,
            (
                telegram_user_id,
                username,
                first_name,
                last_name,
                now,
                now,
                now,
            ),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT *

            FROM users

            WHERE telegram_user_id = ?
            """,
            (
                telegram_user_id,
            ),
        ).fetchone()

    return dict(
        row
    )


def get_user(
    telegram_user_id
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *

            FROM users

            WHERE telegram_user_id = ?
            """,
            (
                int(
                    telegram_user_id
                ),
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(
        row
    )


def get_user_by_id(
    user_id
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *

            FROM users

            WHERE id = ?
            """,
            (
                int(
                    user_id
                ),
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(
        row
    )


def expire_due_subscriptions():
    now = utc_now_text()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE subscriptions

            SET
                status = ?,
                updated_at = ?

            WHERE
                status = ?
                AND expires_at IS NOT NULL
                AND expires_at <= ?
            """,
            (
                STATUS_EXPIRED,
                now,
                STATUS_ACTIVE,
                now,
            ),
        )

        connection.commit()

    return cursor.rowcount


def get_active_subscription(
    telegram_user_id
):
    expire_due_subscriptions()

    user = get_user(
        telegram_user_id
    )

    if user is None:
        return None

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *

            FROM subscriptions

            WHERE
                user_id = ?
                AND status = ?

            ORDER BY
                id DESC

            LIMIT 1
            """,
            (
                user[
                    "id"
                ],
                STATUS_ACTIVE,
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(
        row
    )


def get_effective_access(
    telegram_user_id
):
    user = get_user(
        telegram_user_id
    )

    if user is None:
        return None

    subscription = (
        get_active_subscription(
            telegram_user_id
        )
    )

    if (
        subscription is not None
        and subscription[
            "plan"
        ] == PLAN_VIP
    ):
        return {
            "telegram_user_id":
                user[
                    "telegram_user_id"
                ],

            "plan":
                PLAN_VIP,

            "status":
                STATUS_ACTIVE,

            "starts_at":
                subscription[
                    "starts_at"
                ],

            "expires_at":
                subscription[
                    "expires_at"
                ],

            "subscription_id":
                subscription[
                    "id"
                ],
        }

    return {
        "telegram_user_id":
            user[
                "telegram_user_id"
            ],

        "plan":
            PLAN_FREE,

        "status":
            STATUS_ACTIVE,

        "starts_at":
            user[
                "created_at"
            ],

        "expires_at":
            None,

        "subscription_id":
            None,
    }


def activate_vip(
    telegram_user_id,
    expires_at,
    source="MANUAL",
    starts_at=None,
):
    user = get_user(
        telegram_user_id
    )

    if user is None:
        raise ValueError(
            "User is not registered"
        )

    now = utc_now_text()

    if starts_at is None:
        starts_at = now
    else:
        starts_at = datetime_to_text(
            starts_at
        )

    expires_at = datetime_to_text(
        expires_at
    )

    if expires_at is None:
        raise ValueError(
            "VIP subscription requires expires_at"
        )

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE subscriptions

            SET
                status = ?,
                updated_at = ?

            WHERE
                user_id = ?
                AND status = ?
            """,
            (
                STATUS_CANCELLED,
                now,
                user[
                    "id"
                ],
                STATUS_ACTIVE,
            ),
        )

        cursor = connection.execute(
            """
            INSERT INTO subscriptions (
                user_id,
                plan,
                status,
                starts_at,
                expires_at,
                source,
                created_at,
                updated_at
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                user[
                    "id"
                ],
                PLAN_VIP,
                STATUS_ACTIVE,
                starts_at,
                expires_at,
                source,
                now,
                now,
            ),
        )

        subscription_id = (
            cursor.lastrowid
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT *

            FROM subscriptions

            WHERE id = ?
            """,
            (
                subscription_id,
            ),
        ).fetchone()

    return dict(
        row
    )


def cancel_active_subscription(
    telegram_user_id
):
    user = get_user(
        telegram_user_id
    )

    if user is None:
        return False

    now = utc_now_text()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE subscriptions

            SET
                status = ?,
                updated_at = ?

            WHERE
                user_id = ?
                AND status = ?
            """,
            (
                STATUS_CANCELLED,
                now,
                user[
                    "id"
                ],
                STATUS_ACTIVE,
            ),
        )

        connection.commit()

    return (
        cursor.rowcount
        > 0
    )


def get_subscription_history(
    telegram_user_id
):
    user = get_user(
        telegram_user_id
    )

    if user is None:
        return []

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *

            FROM subscriptions

            WHERE user_id = ?

            ORDER BY
                id DESC
            """,
            (
                user[
                    "id"
                ],
            ),
        ).fetchall()

    return [
        dict(
            row
        )
        for row in rows
    ]


def count_users():
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total

            FROM users
            """
        ).fetchone()

    return int(
        row[
            "total"
        ]
        or 0
    )


def count_active_vip():
    expire_due_subscriptions()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total

            FROM subscriptions

            WHERE
                plan = ?
                AND status = ?
            """,
            (
                PLAN_VIP,
                STATUS_ACTIVE,
            ),
        ).fetchone()

    return int(
        row[
            "total"
        ]
        or 0
    )


def get_subscription_stats():
    expire_due_subscriptions()

    with get_connection() as connection:
        users_row = connection.execute(
            """
            SELECT
                COUNT(*) AS total

            FROM users
            """
        ).fetchone()

        vip_row = connection.execute(
            """
            SELECT
                COUNT(*) AS total

            FROM subscriptions

            WHERE
                plan = ?
                AND status = ?
            """,
            (
                PLAN_VIP,
                STATUS_ACTIVE,
            ),
        ).fetchone()

    total_users = int(
        users_row[
            "total"
        ]
        or 0
    )

    vip_active = int(
        vip_row[
            "total"
        ]
        or 0
    )

    free_users = max(
        total_users
        - vip_active,
        0,
    )

    return {
        "users":
            total_users,

        "free":
            free_users,

        "vip_active":
            vip_active,
    }
