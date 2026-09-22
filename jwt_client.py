# ============================================================
# jwt_client.py - Free Fire login engine (uid+password -> JWT)
# ============================================================
import base64
import json
import re
import time
import uuid
from datetime import datetime, timezone

import requests
import urllib3
import blackboxprotobuf
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV  = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])


def enc_aes(data: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(data, 16))


def dec_aes(data: bytes) -> bytes:
    return unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_IV).decrypt(data), 16)


TYPEDEF_LOGIN = {
    '3':{'type':'bytes','name':''},'4':{'type':'bytes','name':''},'5':{'type':'int','name':''},
    '7':{'type':'bytes','name':''},'8':{'type':'bytes','name':''},'9':{'type':'bytes','name':''},
    '10':{'type':'bytes','name':''},'11':{'type':'bytes','name':''},'12':{'type':'int','name':''},
    '13':{'type':'int','name':''},'14':{'type':'bytes','name':''},'15':{'type':'bytes','name':''},
    '16':{'type':'int','name':''},'17':{'type':'bytes','name':''},'18':{'type':'bytes','name':''},
    '19':{'type':'bytes','name':''},'20':{'type':'bytes','name':''},'21':{'type':'bytes','name':''},
    '22':{'type':'bytes','name':''},'23':{'type':'bytes','name':''},'24':{'type':'bytes','name':''},
    '25':{'type':'bytes','name':''},'26':{'type':'bytes','name':''},'29':{'type':'bytes','name':''},
    '30':{'type':'int','name':''},'41':{'type':'bytes','name':''},'42':{'type':'bytes','name':''},
    '57':{'type':'bytes','name':''},'60':{'type':'int','name':''},'61':{'type':'int','name':''},
    '62':{'type':'int','name':''},'64':{'type':'int','name':''},'65':{'type':'int','name':''},
    '66':{'type':'int','name':''},'67':{'type':'int','name':''},'73':{'type':'int','name':''},
    '74':{'type':'bytes','name':''},'76':{'type':'int','name':''},'77':{'type':'bytes','name':''},
    '78':{'type':'int','name':''},'79':{'type':'int','name':''},'81':{'type':'bytes','name':''},
    '83':{'type':'bytes','name':''},'85':{'type':'int','name':''},'86':{'type':'bytes','name':''},
    '87':{'type':'int','name':''},'88':{'type':'int','name':''},'92':{'type':'int','name':''},
    '93':{'type':'bytes','name':''},'94':{'type':'bytes','name':''},'95':{'type':'int','name':''},
    '96':{'type':'bytes','name':''},'97':{'type':'int','name':''},'98':{'type':'int','name':''},
    '99':{'type':'bytes','name':''},'100':{'type':'bytes','name':''},
    '102':{'type':'message','message_typedef':{},'name':''},
    '104':{'type':'int','name':''},'105':{'type':'int','name':''},
    '106':{'type':'bytes','name':''},'107':{'type':'bytes','name':''},
}

LOGIN_META = {
    "3": b"", "4": b"free fire", "5": 1, "7": b"2.133.6",
    "8": b"Android OS 12 / API-31 (SP1A.210812.003/compiler03061504)",
    "9": b"Handheld", "10": b"airtel", "11": b"CarrierDataNetwork",
    "12": 1600, "13": 720, "14": b"300",
    "15": b"ARM64 FP ASIMD AES | 2301 | 8", "16": 3806,
    "17": b"PowerVR Rogue GE8320",
    "18": b"OpenGL ES 3.2 build 1.13@5776728",
    "19": b"", "20": b"223.228.74.9", "21": b"en", "22": b"",
    "23": b"4", "24": b"Handheld", "25": b"vivo V2111", "26": b"IND",
    "29": b"", "30": 1, "41": b"airtel", "42": b"4G",
    "57": b"1ac4b80ecf0478a44203bf8fac6120f5",
    "60": 47135, "61": 1993, "62": 3152, "64": 2337, "65": 47335,
    "66": 1993, "67": 47135, "73": 2,
    "74": b"/data/app/~~p3h_eiATw1cHfPQlvxjhqg==/com.dts.freefiremax-dPHcgnpmQTrWraOdRcpnuQ==/lib/arm64",
    "76": 2,
    "77": b"38f4751a330688ab124c2c804cec90a5|/data/app/~~p3h_eiATw1cHfPQlvxjhqg==/com.dts.freefiremax-dPHcgnpmQTrWraOdRcpnuQ==/base.apk",
    "78": 2, "79": 2, "81": b"64", "83": b"2019118527",
    "85": 3, "86": b"OpenGLES3", "87": 3071, "88": 4, "92": 18693,
    "93": b"android_max",
    "94": b"KqsHT4LSqizyvxLV1tQJmqyzRHnrvP+fGB4YT+fq1TcQwy54I81dQdSnVxi1nTRKBjb3jBdf3/qUnNKzRq8ew1CnRtKXgtMyC2U3m0L86eg35ovY",
    "95": 111207,
    "96": b'{"cur_rate":null,"support_etc2":true}',
    "97": 1, "98": 1, "99": b"4", "100": b"4", "102": {},
    "104": 2824, "105": 1,
    "106": b"https://dl-tata.freefireind.in/live/ABHotUpdates/|https://core-tata.freefireind.in/live/ABHotUpdates/|211c933168f55902c7dfbfd8c4e2957d",
    "107": b"c8e41b7a93f02d56e1a94c7b8203f5d1",
}

OAUTH_BASE = "https://ffmconnect.live.gop.garenanow.com"
LOGIN_BASE = "https://loginbp.ppmainecoonghj.com"
CLIENT_ID = 100067
CLIENT_SECRET = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
UA_MSDK = "GarenaMSDK/4.0.44(ASUS_AI2501_B ;Android 12;en;US;app 2.132.1 2019118525;)"
UA_UNITY = "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"


class FreeFireLogin:
    def __init__(self, proxy=None, timeout=(5.0, 12.0)):
        self.proxy = proxy
        self.timeout = timeout

    def login(self, uid, password):
        grant = self._token_grant(uid, password)
        jwt, extra = self._major_login(grant["access_token"], grant["open_id"])
        if not jwt:
            raise RuntimeError("no JWT")

        acc = extra.get("account_id")
        if not acc:
            try:
                p = jwt.split(".")[1]
                p += "=" * (-len(p) % 4)
                payload = json.loads(base64.b64decode(p).decode("utf-8"))
                for k in ("account_id", "aid", "uid"):
                    if k in payload:
                        acc = payload[k]
                        break
            except Exception:
                pass
        if not acc:
            raise RuntimeError("no account_id")

        return {
            "uid": int(uid),
            "open_id": grant["open_id"],
            "access_token": grant["access_token"],
            "account_id": acc,
            "jwt": jwt,
        }

    def _token_grant(self, uid, password):
        body = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "client_type": 2,
            "device_id": f"02-{uuid.uuid4()}",
            "password": password,
            "response_type": "token",
            "uid": int(uid),
        }
        r = requests.post(
            f"{OAUTH_BASE}/api/v2/oauth/guest/token:grant",
            headers={
                "User-Agent": UA_MSDK,
                "Content-Type": "application/json; charset=utf-8",
                "Connection": "close",
            },
            json=body, verify=False, proxies=self.proxy, timeout=self.timeout,
        )
        if r.status_code != 200:
            try:
                j = r.json()
            except Exception:
                j = {}
            code = j.get("code")
            err = j.get("error") or j.get("message") or r.text[:200]
            if code == 1002 or "error_params" in str(err).lower():
                raise RuntimeError("error_params")
            raise RuntimeError(f"token_grant_http_{r.status_code}")
        d = (r.json() or {}).get("data") or {}
        if "access_token" not in d or "open_id" not in d:
            raise RuntimeError("token_grant_bad_payload")
        return d

    def _major_login(self, access_token, open_id):
        meta = dict(LOGIN_META)
        meta["3"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S").encode()
        meta["19"] = f"Google|{uuid.uuid4()}".encode()
        meta["22"] = open_id.encode()
        meta["29"] = access_token.encode()

        body = enc_aes(blackboxprotobuf.encode_message(meta, TYPEDEF_LOGIN))

        r = requests.post(
            f"{LOGIN_BASE}/MajorLogin",
            headers={
                "Host": "loginbp.ppmainecoonghj.com",
                "User-Agent": UA_UNITY,
                "Accept": "*/*",
                "Accept-Encoding": "deflate, gzip",
                "Authorization": "Bearer",
                "X-GA": "v1 1",
                "ReleaseVersion": "OB55",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Unity-Version": "2018.4.12f1",
                "X-GA-SV": str(int(time.time())),
            },
            data=body, verify=False, proxies=self.proxy, timeout=self.timeout,
        )
        if r.status_code != 200:
            if "INVALID_PLATFORM" in r.text:
                raise RuntimeError("invalid_platform")
            raise RuntimeError(f"majorlogin_http_{r.status_code}")
        return self._parse(r.content)

    @staticmethod
    def _parse(content):
        blobs = []
        try:
            blobs.append(dec_aes(content))
        except Exception:
            pass
        if len(content) > 64:
            try:
                blobs.append(dec_aes(content[64:]))
            except Exception:
                pass
            blobs.append(content[64:])
        blobs.append(content)

        for blob in blobs:
            try:
                obj, _ = blackboxprotobuf.decode_message(blob)
                if isinstance(obj, dict):
                    extra = {}
                    for ks, kb in (("1", b"1"), ("8", b"8"), ("9", b"9"),
                                   ("10", b"10"), ("11", b"11")):
                        v = obj.get(ks, obj.get(kb))
                        if v is None:
                            continue
                        if ks == "1" and isinstance(v, int):
                            extra["account_id"] = v
                        elif ks == "8" and isinstance(v, bytes):
                            extra["jwt"] = v.decode("utf-8", "ignore")
                        elif ks in ("9", "10", "11") and isinstance(v, bytes):
                            extra[{"9": "ttl", "10": "server_url",
                                   "11": "new_active_region"}[ks]] = v.decode("utf-8", "ignore")

                    if extra.get("jwt"):
                        return extra.pop("jwt"), extra

                    v2 = obj.get("2", obj.get(b"2"))
                    if isinstance(v2, bytes) and v2.startswith(b"eyJ"):
                        return v2.decode("utf-8", "ignore"), extra
            except Exception:
                pass

            m = re.search(rb"eyJ[\w\-]+\.[\w\-]+\.[\w\-]+", blob)
            if m:
                return m.group(0).decode(), {}

        return None, {}