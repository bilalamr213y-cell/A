import logging
import requests
from typing import Any

logger = logging.getLogger(__name__)


class FirebaseClient:
    def __init__(self, database_url: str, auth_token: str = "", timeout: int = 10):
        self.database_url = database_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

    def _build_url(self, path: str) -> str:
        url = f"{self.database_url}/{path.lstrip('/')}.json"
        if self.auth_token:
            url += f"?auth={self.auth_token}"
        return url

    def get(self, path: str) -> Any | None:
        try:
            r = requests.get(self._build_url(path), timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logger.error(f"FB GET '{path}': {e}")
            return None

    def set(self, path: str, data: Any) -> bool:
        try:
            r = requests.put(self._build_url(path), json=data, timeout=self.timeout)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"FB SET '{path}': {e}")
            return False

    def update(self, path: str, data: dict) -> bool:
        try:
            r = requests.patch(self._build_url(path), json=data, timeout=self.timeout)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"FB UPDATE '{path}': {e}")
            return False

    def push(self, path: str, data: Any) -> str | None:
        try:
            r = requests.post(self._build_url(path), json=data, timeout=self.timeout)
            r.raise_for_status()
            result = r.json()
            if isinstance(result, dict):
                return result.get("name")
            return None
        except requests.RequestException as e:
            logger.error(f"FB PUSH '{path}': {e}")
            return None

    def delete(self, path: str) -> bool:
        try:
            r = requests.delete(self._build_url(path), timeout=self.timeout)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"FB DELETE '{path}': {e}")
            return False


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

    def get_bind_info(self, access_token: str) -> dict | None:
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

    def send_otp(self, email: str) -> tuple[bool, str]:
        session = requests.Session()
        try:
            session.get(f"{self.base_url}/game/account_security/", headers=self.headers, timeout=10)
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


def safe_key(email: str) -> str:
    result = email
    for ch in [".", "@", "#", "$", "[", "]", "/"]:
        result = result.replace(ch, "_")
    return result