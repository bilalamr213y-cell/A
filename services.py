import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class BindService:
    def __init__(self, garena, firebase, tz, daily_limit: int):
        self.garena = garena
        self.firebase = firebase
        self.tz = tz
        self.daily_limit = daily_limit

    async def search(self, token: str, user_id: int) -> dict:
        if not self._check_daily_limit(user_id):
            return {"success": False, "error": "daily_limit"}

        data = await asyncio.to_thread(self.garena.get_bind_info, token)

        if not data:
            self._log(user_id, token, False)
            return {"success": False, "error": "invalid_token"}

        email = (data.get("email") or "").strip()
        mobile = (data.get("mobile") or "").strip()

        if not email and not mobile:
            self._log(user_id, token, False)
            return {"success": False, "error": "no_binding"}

        self.firebase.set(f"bind_results/{token}", {
            "email": email,
            "mobile": mobile,
            "user_id": user_id,
            "timestamp": datetime.now(self.tz).isoformat(),
        })
        self._log(user_id, token, True)

        return {"success": True, "email": email, "mobile": mobile, "token": token}

    def _log(self, user_id: int, token: str, success: bool):
        self.firebase.push(f"user_searches/{user_id}", {
            "token_prefix": token[:8],
            "success": success,
            "timestamp": datetime.now(self.tz).isoformat(),
        })

    def _check_daily_limit(self, user_id: int) -> bool:
        today = datetime.now(self.tz).strftime("%Y-%m-%d")
        data = self.firebase.get(f"user_searches/{user_id}")
        if not isinstance(data, dict):
            return True
        count = 0
        for v in data.values():
            if isinstance(v, dict) and v.get("timestamp", "").startswith(today):
                count += 1
        return count < self.daily_limit

    def get_user_accounts(self, user_id: int) -> list:
        data = self.firebase.get("bind_results")
        if not isinstance(data, dict):
            return []
        accounts = []
        for token, info in data.items():
            if isinstance(info, dict) and info.get("user_id") == user_id:
                acc = {"token": token}
                acc.update(info)
                accounts.append(acc)
        accounts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return accounts

    def remove_account(self, token: str) -> bool:
        return self.firebase.delete(f"bind_results/{token}")


class OtpService:
    def __init__(self, garena, firebase, interval: float, max_daily: int, max_emails: int):
        self.garena = garena
        self.firebase = firebase
        self.interval = interval
        self.max_daily = max_daily
        self.max_emails = max_emails

    def send_one(self, email: str) -> tuple[bool, str]:
        return self.garena.send_otp(email)

    async def send_batch(self, emails: list[str], callback=None) -> list:
        results = []
        for email in emails:
            success, details = await asyncio.to_thread(self.send_one, email)
            results.append({"email": email, "success": success, "details": details})
            if callback:
                await callback(email, success, details)
            await asyncio.sleep(self.interval)
        return results

    def load_emails(self, user_id: int) -> list[str]:
        data = self.firebase.get(f"user_emails/{user_id}")
        if isinstance(data, list):
            return [e for e in data if e]
        if isinstance(data, dict):
            return [e for e in data.values() if e]
        return []

    def save_emails(self, user_id: int, emails: list[str]) -> bool:
        return self.firebase.set(f"user_emails/{user_id}", emails)

    def add_email(self, user_id: int, email: str) -> tuple[bool, str]:
        emails = self.load_emails(user_id)
        if email in emails:
            return False, "exists"
        if len(emails) >= self.max_emails:
            return False, "limit"
        emails.append(email)
        self.save_emails(user_id, emails)
        return True, "ok"

    def remove_email(self, user_id: int, index: int) -> str | None:
        emails = self.load_emails(user_id)
        if index < 0 or index >= len(emails):
            return None
        removed = emails.pop(index)
        self.save_emails(user_id, emails)
        return removed

    def get_counts(self, user_id: int) -> dict:
        data = self.firebase.get(f"send_counts/{user_id}")
        return data if isinstance(data, dict) else {}

    def set_count(self, user_id: int, email: str, count: int) -> bool:
        key = self._key(email)
        return self.firebase.update(f"send_counts/{user_id}", {key: count})

    def get_count(self, user_id: int, email: str) -> int:
        counts = self.get_counts(user_id)
        return counts.get(self._key(email), 0)

    def reset_counts(self, user_id: int) -> bool:
        return self.firebase.set(f"send_counts/{user_id}", {})

    def _key(self, email: str) -> str:
        result = email
        for ch in [".", "@", "#", "$", "[", "]", "/"]:
            result = result.replace(ch, "_")
        return result


class ReferralService:
    def __init__(self, firebase, required: int):
        self.firebase = firebase
        self.required = required

    def register(self, new_user_id: int, referrer_id: int) -> dict:
        if new_user_id == referrer_id:
            return {"success": False, "credit": False}
        if self.firebase.get(f"referred_by/{new_user_id}"):
            return {"success": False, "credit": False}

        self.firebase.set(f"referred_by/{new_user_id}", referrer_id)
        current = self.firebase.get(f"user_referrals/{referrer_id}") or 0
        new_count = current + 1
        self.firebase.set(f"user_referrals/{referrer_id}", new_count)

        earned = False
        if new_count % self.required == 0:
            credits = self.firebase.get(f"user_credits/{referrer_id}") or 0
            self.firebase.set(f"user_credits/{referrer_id}", credits + 1)
            earned = True

        return {"success": True, "credit": earned}

    def stats(self, user_id: int) -> dict:
        refs = self.firebase.get(f"user_referrals/{user_id}") or 0
        credits = self.firebase.get(f"user_credits/{user_id}") or 0
        return {
            "referrals": refs,
            "credits": credits,
            "progress": refs % self.required,
        }

    def use_credit(self, user_id: int) -> bool:
        credits = self.firebase.get(f"user_credits/{user_id}") or 0
        if credits <= 0:
            return False
        self.firebase.set(f"user_credits/{user_id}", credits - 1)
        return True

    def add_credits(self, user_id: int, amount: int) -> int:
        current = self.firebase.get(f"user_credits/{user_id}") or 0
        total = current + amount
        self.firebase.set(f"user_credits/{user_id}", total)
        return total