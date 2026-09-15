import logging
import sqlite3
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Africa/Algiers")


class DatabaseClient:
    def __init__(self, db_file: str):
        self.db_file = db_file
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_emails (
                    user_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (user_id, email)
                );
                CREATE TABLE IF NOT EXISTS send_counts (
                    user_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, email)
                );
                CREATE TABLE IF NOT EXISTS bind_results (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    email TEXT,
                    mobile TEXT,
                    timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_prefix TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_referrals (
                    user_id INTEGER PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS referred_by (
                    user_id INTEGER PRIMARY KEY,
                    referrer_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_credits (
                    user_id INTEGER PRIMARY KEY,
                    credits INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    last_reset_date TEXT
                );
                """
            )
            conn.commit()

    # -------- Emails --------
    def get_emails(self, user_id: int) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT email FROM user_emails WHERE user_id = ? ORDER BY position",
                (user_id,),
            ).fetchall()
        return [r["email"] for r in rows]

    def save_emails(self, user_id: int, emails: list) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_emails WHERE user_id = ?", (user_id,))
            for i, email in enumerate(emails):
                conn.execute(
                    "INSERT INTO user_emails (user_id, email, position) VALUES (?, ?, ?)",
                    (user_id, email, i),
                )
            conn.commit()

    def add_email(self, user_id: int, email: str) -> bool:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM user_emails WHERE user_id = ? AND email = ?",
                (user_id, email),
            ).fetchone()
            if existing:
                return False
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM user_emails WHERE user_id = ?",
                (user_id,),
            ).fetchone()["c"]
            conn.execute(
                "INSERT INTO user_emails (user_id, email, position) VALUES (?, ?, ?)",
                (user_id, email, count),
            )
            conn.commit()
        return True

    def remove_email_by_index(self, user_id: int, index: int) -> Optional[str]:
        emails = self.get_emails(user_id)
        if index < 0 or index >= len(emails):
            return None
        removed = emails.pop(index)
        self.save_emails(user_id, emails)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM send_counts WHERE user_id = ? AND email = ?",
                (user_id, removed),
            )
            conn.commit()
        return removed

    # -------- Send counts --------
    def get_count(self, user_id: int, email: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count FROM send_counts WHERE user_id = ? AND email = ?",
                (user_id, email),
            ).fetchone()
        return row["count"] if row else 0

    def set_count(self, user_id: int, email: str, count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO send_counts (user_id, email, count) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, email) DO UPDATE SET count = excluded.count",
                (user_id, email, count),
            )
            conn.commit()

    def get_all_counts(self, user_id: int) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT email, count FROM send_counts WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {r["email"]: r["count"] for r in rows}

    def reset_counts(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM send_counts WHERE user_id = ?", (user_id,))
            conn.commit()

    # -------- Bind results --------
    def save_bind_result(self, token: str, user_id: int, email: str, mobile: str) -> None:
        ts = datetime.now(TZ).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO bind_results (token, user_id, email, mobile, timestamp) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(token) DO UPDATE SET "
                "user_id = excluded.user_id, email = excluded.email, "
                "mobile = excluded.mobile, timestamp = excluded.timestamp",
                (token, user_id, email, mobile, ts),
            )
            conn.commit()

    def get_user_accounts(self, user_id: int) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT token, email, mobile, timestamp FROM bind_results "
                "WHERE user_id = ? ORDER BY timestamp DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_account_by_token(self, token: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT token, user_id, email, mobile, timestamp FROM bind_results "
                "WHERE token = ?",
                (token,),
            ).fetchone()
        return dict(row) if row else None

    def remove_account(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM bind_results WHERE token = ?", (token,))
            conn.commit()

    # -------- Search log --------
    def log_search(self, user_id: int, token: str, success: bool) -> None:
        ts = datetime.now(TZ).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_searches (user_id, token_prefix, success, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (user_id, token[:8], 1 if success else 0, ts),
            )
            conn.commit()

    def count_searches_today(self, user_id: int) -> int:
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM user_searches "
                "WHERE user_id = ? AND timestamp LIKE ?",
                (user_id, f"{today}%"),
            ).fetchone()
        return row["c"]

    # -------- Referrals --------
    def get_referral_count(self, user_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count FROM user_referrals WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["count"] if row else 0

    def set_referral_count(self, user_id: int, count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_referrals (user_id, count) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET count = excluded.count",
                (user_id, count),
            )
            conn.commit()

    def is_referred(self, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM referred_by WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row is not None

    def set_referred_by(self, user_id: int, referrer_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO referred_by (user_id, referrer_id) VALUES (?, ?)",
                (user_id, referrer_id),
            )
            conn.commit()

    # -------- Credits --------
    def get_credits(self, user_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT credits FROM user_credits WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["credits"] if row else 0

    def set_credits(self, user_id: int, credits: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_credits (user_id, credits) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET credits = excluded.credits",
                (user_id, credits),
            )
            conn.commit()

    # -------- User state --------
    def get_last_reset(self, user_id: int) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_reset_date FROM user_state WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["last_reset_date"] if row else None

    def set_last_reset(self, user_id: int, date_str: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_state (user_id, last_reset_date) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_reset_date = excluded.last_reset_date",
                (user_id, date_str),
            )
            conn.commit()


class GarenaClient:
    def __init__(self, base_url: str, app_id: str, user_agent: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.app_id = app_id
        self.timeout = timeout
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Host": "100067.connect.garena.com",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

    def get_bind_info(self, access_token: str) -> Optional[dict]:
        if not access_token or len(access_token) != 64:
            return None
        url = f"{self.base_url}/game/account_security/bind:get_bind_info"
        params = {"app_id": self.app_id, "access_token": access_token}
        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
            if r.status_code != 200:
                return None
            data = r.json()
            if not isinstance(data, dict):
                return None
            if data.get("result") != 0:
                return None
            return data
        except Exception as e:
            logger.error(f"get_bind_info: {e}")
            return None

    def send_otp(self, email: str):
        session = requests.Session()
        try:
            session.get(
                f"{self.base_url}/game/account_security/",
                headers=self.headers,
                timeout=10,
            )
            payload = {"app_id": self.app_id, "email": email, "locale": "en_DZ"}
            url = f"{self.base_url}/game/account_security/swap:send_otp"
            r = session.post(url, headers=self.headers, data=payload, timeout=self.timeout)
            if r.status_code == 200 and '"result":0' in r.text:
                return True, r.text
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)


def is_valid_token(token: str) -> bool:
    if not token or len(token) != 64:
        return False
    return all(c in "0123456789abcdef" for c in token.lower())


def truncate_token(token: str, keep: int = 8) -> str:
    if not token:
        return ""
    if len(token) <= keep * 2:
        return token
    return f"{token[:keep]}...{token[-keep:]}"


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def current_hour() -> int:
    return datetime.now(TZ).hour