import os
import sqlite3
import time

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import requests

from user_subscriptions import (
    get_effective_access,
)


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

VIP_CHANNEL_ID = os.getenv(
    "TELEGRAM_VIP_CHANNEL_ID",
    "",
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)

API_BASE_URL = (
    f"https://api.telegram.org/"
    f"bot{BOT_TOKEN}"
)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

INVITE_VALID_MINUTES = 15

STATUS_PENDING = "PENDING_JOIN"
STATUS_JOINED = "JOINED"
STATUS_REVOKED = "REVOKED"
STATUS_EXPIRED = "EXPIRED"
STATUS_DECLINED = "DECLINED"


def utc_now():
    return datetime.now(
        timezone.utc
    )


def utc_text(
    value=None
):
    if value is None:
        value = utc_now()

    if value.tzinfo is not None:
        value = value.astimezone(
            timezone.utc
        ).replace(
            tzinfo=None
        )

    return value.strftime(
        TIME_FORMAT
    )


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_vip_access_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            vip_access (
                id INTEGER
                PRIMARY KEY
                AUTOINCREMENT,

                telegram_user_id INTEGER
                NOT NULL,

                subscription_id INTEGER,

                invite_link TEXT
                NOT NULL,

                status TEXT
                NOT NULL,

                created_at TEXT
                NOT NULL,

                expires_at TEXT
                NOT NULL,

                joined_at TEXT,

                revoked_at TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_vip_access_user
            ON vip_access (
                telegram_user_id
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_vip_access_status
            ON vip_access (
                status
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_vip_access_invite
            ON vip_access (
                invite_link
            )
            """
        )

        connection.commit()


def telegram_request(
    method,
    payload=None,
    timeout=15,
):
    if not BOT_TOKEN:
        print(
            "VIP ACCESS ERROR | "
            "Telegram token missing",
            flush=True,
        )

        return None

    try:
        response = requests.post(
            f"{API_BASE_URL}/{method}",
            json=payload or {},
            timeout=timeout,
        )

    except Exception as error:
        print(
            "VIP ACCESS TELEGRAM ERROR | "
            f"{method} | "
            f"{type(error).__name__}",
            flush=True,
        )

        return None

    if response.status_code >= 400:
        print(
            "VIP ACCESS TELEGRAM ERROR | "
            f"{method} | "
            f"HTTP "
            f"{response.status_code} "
            f"{response.reason}",
            flush=True,
        )

        return None

    try:
        data = response.json()

    except Exception:
        print(
            "VIP ACCESS TELEGRAM ERROR | "
            f"{method} | "
            "Invalid JSON",
            flush=True,
        )

        return None

    if not data.get(
        "ok"
    ):
        print(
            "VIP ACCESS TELEGRAM ERROR | "
            f"{method} | "
            "Telegram API rejected request",
            flush=True,
        )

        return None

    return data.get(
        "result"
    )


def has_active_vip(
    telegram_user_id
):
    access = get_effective_access(
        telegram_user_id
    )

    if access is None:
        return False

    return (
        access.get(
            "plan"
        ) == "VIP"
        and access.get(
            "status"
        ) == "ACTIVE"
    )


def expire_old_invites(
    telegram_user_id=None
):
    now = utc_text()

    with get_connection() as connection:
        if telegram_user_id is None:
            cursor = connection.execute(
                """
                UPDATE vip_access

                SET status = ?

                WHERE
                    status = ?
                    AND expires_at <= ?
                """,
                (
                    STATUS_EXPIRED,
                    STATUS_PENDING,
                    now,
                ),
            )

        else:
            cursor = connection.execute(
                """
                UPDATE vip_access

                SET status = ?

                WHERE
                    telegram_user_id = ?
                    AND status = ?
                    AND expires_at <= ?
                """,
                (
                    STATUS_EXPIRED,
                    int(
                        telegram_user_id
                    ),
                    STATUS_PENDING,
                    now,
                ),
            )

        connection.commit()

    return cursor.rowcount


def get_pending_invite(
    telegram_user_id
):
    expire_old_invites(
        telegram_user_id
    )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *

            FROM vip_access

            WHERE
                telegram_user_id = ?
                AND status = ?

            ORDER BY
                id DESC

            LIMIT 1
            """,
            (
                int(
                    telegram_user_id
                ),
                STATUS_PENDING,
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(
        row
    )


def save_invite(
    telegram_user_id,
    subscription_id,
    invite_link,
    expires_at,
):
    created_at = utc_text()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO vip_access (
                telegram_user_id,
                subscription_id,
                invite_link,
                status,
                created_at,
                expires_at
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                int(
                    telegram_user_id
                ),
                subscription_id,
                invite_link,
                STATUS_PENDING,
                created_at,
                expires_at,
            ),
        )

        access_id = (
            cursor.lastrowid
        )

        connection.commit()

    return access_id


def create_vip_invite(
    telegram_user_id
):
    telegram_user_id = int(
        telegram_user_id
    )

    access = get_effective_access(
        telegram_user_id
    )

    if (
        access is None
        or access.get(
            "plan"
        ) != "VIP"
        or access.get(
            "status"
        ) != "ACTIVE"
    ):
        return {
            "ok":
                False,

            "reason":
                "NO_ACTIVE_VIP",
        }

    existing = get_pending_invite(
        telegram_user_id
    )

    if existing is not None:
        return {
            "ok":
                True,

            "invite_link":
                existing[
                    "invite_link"
                ],

            "expires_at":
                existing[
                    "expires_at"
                ],

            "reused":
                True,
        }

    expires_datetime = (
        utc_now()
        + timedelta(
            minutes=(
                INVITE_VALID_MINUTES
            )
        )
    )

    expires_timestamp = int(
        expires_datetime.timestamp()
    )

    invite_name = (
        f"AS VIP {telegram_user_id}"
    )

    if len(
        invite_name
    ) > 32:
        invite_name = (
            invite_name[:32]
        )

    result = telegram_request(
        "createChatInviteLink",
        {
            "chat_id":
                VIP_CHANNEL_ID,

            "name":
                invite_name,

            "expire_date":
                expires_timestamp,

            "creates_join_request":
                True,
        },
    )

    if result is None:
        return {
            "ok":
                False,

            "reason":
                "TELEGRAM_ERROR",
        }

    invite_link = result.get(
        "invite_link"
    )

    if not invite_link:
        return {
            "ok":
                False,

            "reason":
                "NO_INVITE_LINK",
        }

    expires_at = utc_text(
        expires_datetime
    )

    save_invite(
        telegram_user_id=(
            telegram_user_id
        ),

        subscription_id=(
            access.get(
                "subscription_id"
            )
        ),

        invite_link=(
            invite_link
        ),

        expires_at=(
            expires_at
        ),
    )

    print(
        "VIP ACCESS | "
        f"Invite created | "
        f"User={telegram_user_id} | "
        f"Expires="
        f"{expires_at} UTC",
        flush=True,
    )

    return {
        "ok":
            True,

        "invite_link":
            invite_link,

        "expires_at":
            expires_at,

        "reused":
            False,
    }


def get_access_record_by_link(
    invite_link
):
    if not invite_link:
        return None

    expire_old_invites()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *

            FROM vip_access

            WHERE
                invite_link = ?
                AND status = ?

            ORDER BY
                id DESC

            LIMIT 1
            """,
            (
                invite_link,
                STATUS_PENDING,
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(
        row
    )


def mark_joined(
    access_id
):
    now = utc_text()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE vip_access

            SET
                status = ?,
                joined_at = ?

            WHERE
                id = ?
                AND status = ?
            """,
            (
                STATUS_JOINED,
                now,
                int(
                    access_id
                ),
                STATUS_PENDING,
            ),
        )

        connection.commit()

    return (
        cursor.rowcount
        > 0
    )


def mark_declined(
    access_id
):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE vip_access

            SET status = ?

            WHERE
                id = ?
                AND status = ?
            """,
            (
                STATUS_DECLINED,
                int(
                    access_id
                ),
                STATUS_PENDING,
            ),
        )

        connection.commit()

    return (
        cursor.rowcount
        > 0
    )


def approve_join_request(
    telegram_user_id
):
    result = telegram_request(
        "approveChatJoinRequest",
        {
            "chat_id":
                VIP_CHANNEL_ID,

            "user_id":
                int(
                    telegram_user_id
                ),
        },
    )

    return (
        result is not None
    )


def decline_join_request(
    telegram_user_id
):
    result = telegram_request(
        "declineChatJoinRequest",
        {
            "chat_id":
                VIP_CHANNEL_ID,

            "user_id":
                int(
                    telegram_user_id
                ),
        },
    )

    return (
        result is not None
    )


def process_vip_join_request(
    join_request
):
    if not join_request:
        return False

    chat = (
        join_request.get(
            "chat"
        )
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    if str(
        chat_id
    ) != str(
        VIP_CHANNEL_ID
    ):
        return False

    telegram_user = (
        join_request.get(
            "from"
        )
        or {}
    )

    telegram_user_id = (
        telegram_user.get(
            "id"
        )
    )

    if telegram_user_id is None:
        return False

    invite = (
        join_request.get(
            "invite_link"
        )
        or {}
    )

    invite_link = invite.get(
        "invite_link"
    )

    access_record = (
        get_access_record_by_link(
            invite_link
        )
    )

    # The link does not belong to
    # our active VIP access record.
    if access_record is None:
        decline_join_request(
            telegram_user_id
        )

        print(
            "VIP ACCESS DENIED | "
            f"User={telegram_user_id} | "
            "Unknown or expired invite",
            flush=True,
        )

        return False

    # The invite was created for
    # another Telegram user.
    if int(
        access_record[
            "telegram_user_id"
        ]
    ) != int(
        telegram_user_id
    ):
        decline_join_request(
            telegram_user_id
        )

        print(
            "VIP ACCESS DENIED | "
            f"User={telegram_user_id} | "
            "Invite belongs to another user",
            flush=True,
        )

        return False

    # The correct person is using
    # the correct link, but VIP may
    # already have expired.
    if not has_active_vip(
        telegram_user_id
    ):
        decline_join_request(
            telegram_user_id
        )

        mark_declined(
            access_record[
                "id"
            ]
        )

        print(
            "VIP ACCESS DENIED | "
            f"User={telegram_user_id} | "
            "VIP subscription inactive",
            flush=True,
        )

        return False

    approved = (
        approve_join_request(
            telegram_user_id
        )
    )

    if not approved:
        print(
            "VIP ACCESS ERROR | "
            f"User={telegram_user_id} | "
            "Approval failed",
            flush=True,
        )

        return False

    mark_joined(
        access_record[
            "id"
        ]
    )

    print(
        "VIP ACCESS APPROVED | "
        f"User={telegram_user_id}",
        flush=True,
    )

    return True


def remove_user_from_vip(
    telegram_user_id
):
    telegram_user_id = int(
        telegram_user_id
    )

    # First remove the user.
    banned = telegram_request(
        "banChatMember",
        {
            "chat_id":
                VIP_CHANNEL_ID,

            "user_id":
                telegram_user_id,
        },
    )

    if banned is None:
        return False

    # Immediately unban.
    # The user remains outside the
    # channel but can join again after
    # a future VIP renewal.
    telegram_request(
        "unbanChatMember",
        {
            "chat_id":
                VIP_CHANNEL_ID,

            "user_id":
                telegram_user_id,

            "only_if_banned":
                True,
        },
    )

    now = utc_text()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE vip_access

            SET
                status = ?,
                revoked_at = ?

            WHERE
                telegram_user_id = ?
                AND status IN (?, ?)
            """,
            (
                STATUS_REVOKED,
                now,
                telegram_user_id,
                STATUS_PENDING,
                STATUS_JOINED,
            ),
        )

        connection.commit()

    print(
        "VIP ACCESS REVOKED | "
        f"User={telegram_user_id}",
        flush=True,
    )

    return True
