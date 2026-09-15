import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Africa/Algiers")


class BindService:
    def __init__(self, garena, db, daily_limit: int):
        self.garena = garena
        self.db = db
        self.daily_limit = daily_limit

    async def search(self, token: str, user_id: int) -> dict:
        if self.db.count_searches_today(user_id) >= self.daily_limit:
            return {"success": False, "error": "daily_limit"}

        data = await asyncio.to_thread(self.garena.get_bind_info, token)

        if not data:
            self.db.log_search(user_id, token, False)
            return {"success": False, "error": "invalid_token"}

        email = (data.get("email") or "").strip()
        mobile = (data.get("mobile") or "").strip()

        if not email and not mobile:
            self.db.log_search(user_id, token, False)
            return {"success": False, "error": "no_binding"}

        self.db.save_bind_result(token, user_id, email, mobile)
        self.db.log_search(user_id, token, True)

        return {
            "success": True,
            "email": email,
            "mobile": mobile,
            "token": token,
        }

    def get_user_accounts(self, user_id: int) -> list:
        return self.db.get_user_accounts(user_id)

    def get_account_by_token(self, token: str):
        return self.db.get_account_by_token(token)

    def remove_account(self, token: str) -> None:
        self.db.remove_account(token)

    def remaining_today(self, user_id: int) -> int:
        used = self.db.count_searches_today(user_id)
        return max(self.daily_limit - used, 0)


class OtpService:
    def __init__(self, garena, db, interval: float, max_daily: int, max_emails: int):
        self.garena = garena
        self.db = db
        self.interval = interval
        self.max_daily = max_daily
        self.max_emails = max_emails

    def send_one(self, email: str):
        return self.garena.send_otp(email)

    async def send_batch(self, emails: list, callback=None) -> list:
        results = []
        for email in emails:
            success, details = await asyncio.to_thread(self.send_one, email)
            results.append({"email": email, "success": success, "details": details})
            if callback:
                await callback(email, success, details)
            await asyncio.sleep(self.interval)
        return results

    def load_emails(self, user_id: int) -> list:
        return self.db.get_emails(user_id)

    def save_emails(self, user_id: int, emails: list) -> None:
        self.db.save_emails(user_id, emails)

    def add_email(self, user_id: int, email: str):
        current = self.db.get_emails(user_id)
        if email in current:
            return False, "exists"
        if len(current) >= self.max_emails:
            return False, "limit"
        self.db.add_email(user_id, email)
        return True, "ok"

    def remove_email(self, user_id: int, index: int):
        return self.db.remove_email_by_index(user_id, index)

    def get_count(self, user_id: int, email: str) -> int:
        return self.db.get_count(user_id, email)

    def get_all_counts(self, user_id: int) -> dict:
        return self.db.get_all_counts(user_id)

    def set_count(self, user_id: int, email: str, count: int) -> None:
        self.db.set_count(user_id, email, count)

    def reset_counts(self, user_id: int) -> None:
        self.db.reset_counts(user_id)

    def all_reached_limit(self, user_id: int) -> bool:
        emails = self.load_emails(user_id)
        if not emails:
            return False
        counts = self.get_all_counts(user_id)
        for e in emails:
            if counts.get(e, 0) < self.max_daily:
                return False
        return True


class ReferralService:
    def __init__(self, db, required: int):
        self.db = db
        self.required = required

    def register(self, new_user_id: int, referrer_id: int) -> dict:
        if new_user_id == referrer_id:
            return {"success": False, "credit": False}
        if self.db.is_referred(new_user_id):
            return {"success": False, "credit": False}

        self.db.set_referred_by(new_user_id, referrer_id)
        current = self.db.get_referral_count(referrer_id)
        new_count = current + 1
        self.db.set_referral_count(referrer_id, new_count)

        earned = False
        if new_count % self.required == 0:
            credits = self.db.get_credits(referrer_id)
            self.db.set_credits(referrer_id, credits + 1)
            earned = True

        return {"success": True, "credit": earned}

    def stats(self, user_id: int) -> dict:
        refs = self.db.get_referral_count(user_id)
        credits = self.db.get_credits(user_id)
        return {
            "referrals": refs,
            "credits": credits,
            "progress": refs % self.required,
            "required": self.required,
        }

    def use_credit(self, user_id: int) -> bool:
        credits = self.db.get_credits(user_id)
        if credits <= 0:
            return False
        self.db.set_credits(user_id, credits - 1)
        return True

    def add_credits(self, user_id: int, amount: int) -> int:
        current = self.db.get_credits(user_id)
        total = current + amount
        self.db.set_credits(user_id, total)
        return total