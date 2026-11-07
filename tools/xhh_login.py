#!/usr/bin/env python3
"""小黑盒手机号登录，直接换出青龙能用的 XHH_COOKIE（App 登录态）。

背景：签到接口只认 App 签名，网页扫码拿到的 pkey 在 App 通道会返回「请重新登录」。
这里走 App 的短信登录接口，拿到的就是 App 登录态，签到能直接过。

用法：
    python tools/xhh_login.py --phone 138xxxxxxxx
    python tools/xhh_login.py --phone 138xxxxxxxx --tokenid "浏览器里的 x_xhh_tokenid"

流程：发验证码 → 输入手机收到的 6 位验证码 → 输出 XHH_COOKIE。

说明：
- 手机号和验证码只发往小黑盒官方接口，不经过任何第三方；本工具也不会保存它们。
- 短信有频率限制，别反复点。
- 需要的 hkey 签名由 XHH_HKEY_API 指定的服务计算（默认社区公共服务，收不到 Cookie）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import string
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checkin_xiaoheihe import APP_PROFILE, APP_UA, API, DEFAULT_HKEY_API, XiaoHeiHe  # noqa: E402

# 黑盒 App 的短信登录接口用 RSA 加密手机号，公钥来自公开的协议分析
RSA_PUBLIC_DER = base64.b64decode(
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDZgjVwAiKTjZ55nG+mW6r3TSU4ECvNYqDMIS/bhCj"
    "2QaH5GI/KZb2TBp+CBvUj9SLFnmJQ0kzHzHoGZCQ88VevCffF7JePGF9cmKQqotlfTKbV4oxV5iLz"
    "7JSG6b/Vg7AXtrTolNtWsa8HiB0tI0YClYaQlOXm4UxLeSxQwSFETwIDAQAB"
)
_MODULUS_PREFIX = b"\x02\x81\x81"
_EXPONENT = b"\x02\x03\x01\x00\x01"
_PAD_BYTES = 128

DEFAULT_TOKEN_ID = "abc"


def encrypt_phone(text: str) -> str:
    """RSA PKCS#1 v1.5 加密手机号（和 App 一致）。"""
    message = text.encode("utf-8")
    if not message or len(message) > _PAD_BYTES - 11:
        raise ValueError("手机号无法加密")
    match = re.search(re.escape(_MODULUS_PREFIX) + b".{129}", RSA_PUBLIC_DER, re.S)
    if not match or _EXPONENT not in RSA_PUBLIC_DER:
        raise ValueError("内置公钥解析失败")
    modulus = int.from_bytes(match.group(0)[3:], "big")
    padding = bytearray()
    while len(padding) < _PAD_BYTES - 3 - len(message):
        byte = os.urandom(1)[0]
        if byte:
            padding.append(byte)
    block = b"\x00\x02" + bytes(padding) + b"\x00" + message
    cipher = pow(int.from_bytes(block, "big"), 65537, modulus)
    encoded = base64.b64encode(cipher.to_bytes(_PAD_BYTES, "big")).decode("ascii")
    return "\n".join(encoded[i:i + 76] for i in range(0, len(encoded), 76)) + "\n"


def default_imei(seed: str) -> str:
    digest = hashlib.sha1(seed.encode()).hexdigest()
    return "86" + "".join(str(int(char, 16) % 10) for char in digest[:13])


def find_key(data: object, names: tuple[str, ...]) -> str:
    """在返回 JSON 里递归找指定字段。"""
    if isinstance(data, dict):
        for name in names:
            value = data.get(name)
            if isinstance(value, (str, int)) and str(value):
                return str(value)
        for value in data.values():
            found = find_key(value, names)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_key(item, names)
            if found:
                return found
    return ""


class AppLogin:
    def __init__(self, hkey_api: str, token_id: str, imei: str):
        self.hkey_api = hkey_api
        self.token_id = token_id
        self.imei = imei
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": APP_UA, "Accept": "application/json"})

    def sign(self, path: str) -> dict:
        r = self.s.get(self.hkey_api, params={
            "mode": "request", "path": path, "time": int(time.time()),
            "imei": self.imei, "heybox_id": "-1", "os_version": "13",
        }, timeout=20)
        data = r.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"hkey 服务请求失败：{data.get('msg') or data}")
        result = data.get("result") or {}
        query = {
            **APP_PROFILE,
            "imei": self.imei,
            "device_info": "M2012K11AC",
            "os_version": "13",
            "dw": "600",
            "channel": "heybox",
            "netmode": "wifi",
            "heybox_id": "-1",
            "nonce": "".join(random.choices(string.ascii_letters + string.digits, k=32)),
            "hkey": result.get("hkey"),
            "_rnd": result.get("_rnd"),
            "version": result.get("version"),
            "build": result.get("build"),
            "_time": str(result.get("time")),
            "x_xhh_tokenid": self.token_id,
        }
        return {k: v for k, v in query.items() if v}

    def post(self, path: str, data: dict) -> dict:
        r = self.s.post(API + path, params=self.sign(path), data=data, timeout=20)
        try:
            payload = r.json()
        except ValueError:
            raise RuntimeError(f"接口返回异常（HTTP {r.status_code}）：{r.text[:120]}") from None
        return payload if isinstance(payload, dict) else {"status": "bad_json", "result": payload}

    def send_code(self, phone: str) -> dict:
        return self.post("/account/get_login_code/", {"phone_num": encrypt_phone(phone)})

    def login(self, phone: str, code: str) -> dict:
        payload = self.sign("/account/login_code/")
        payload.update({"code": code, "is_new_device": "0", "referrer": ""})
        payload = {k: v for k, v in payload.items() if v}
        r = self.s.post(API + "/account/login_code/",
                        params=payload, data={"phone_num": encrypt_phone(phone)}, timeout=20)
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"接口返回异常（HTTP {r.status_code}）：{r.text[:120]}") from None
        return data if isinstance(data, dict) else {"status": "bad_json", "result": data}


def main() -> int:
    parser = argparse.ArgumentParser(description="小黑盒手机号登录，生成 XHH_COOKIE")
    parser.add_argument("--phone", help="手机号")
    parser.add_argument("--code", help="验证码（不填则交互输入）")
    parser.add_argument("--tokenid", default=os.getenv("XHH_TOKENID", DEFAULT_TOKEN_ID),
                        help="x_xhh_tokenid（默认读环境变量 XHH_TOKENID，没有就用占位值）")
    parser.add_argument("--hkey-api", default=DEFAULT_HKEY_API, help="签名服务地址")
    parser.add_argument("--imei", help="指定 imei（默认按手机号固定生成）")
    parser.add_argument("--debug", action="store_true", help="打印接口原始返回")
    args = parser.parse_args()

    phone = (args.phone or input("手机号：")).strip()
    if not re.fullmatch(r"1\d{10}", phone):
        print("手机号格式不对")
        return 1
    imei = args.imei or default_imei(phone)
    login = AppLogin(args.hkey_api, args.tokenid, imei)

    print(f"\n发送验证码到 {phone[:3]}****{phone[-4:]} …")
    result = login.send_code(phone)
    if args.debug:
        print("返回：" + json.dumps(result, ensure_ascii=False)[:400])
    if result.get("status") != "ok":
        message = str(result.get("msg") or result)
        print(f"发送失败：{message}")
        if "状态异常" in message:
            print("""
  服务端不放行发码，常见原因：
    1. 这个号码不是该账号绑定的登录手机号（去 App「设置 → 账号与安全」确认）
    2. 短时间发码次数太多被限流（等 10 分钟再试）
    3. 设备指纹是占位值，被风控拦了（换浏览器里的真实 x_xhh_tokenid）：
         python tools/xhh_login.py --phone %s --tokenid "B开头的那一长串"
""" % phone)
        return 1
    print("已发送，等短信到达。")

    code = (args.code or input("\n输入短信里的验证码：")).strip()
    if not re.fullmatch(r"\d{4,6}", code):
        print("验证码格式不对")
        return 1

    result = login.login(phone, code)
    if args.debug:
        print("返回：" + json.dumps(result, ensure_ascii=False)[:600])
    if result.get("status") != "ok":
        print(f"\n登录失败：{result.get('msg') or json.dumps(result, ensure_ascii=False)[:200]}")
        return 1

    body = result.get("result") or {}
    pkey = find_key(body, ("pkey", "user_pkey"))
    heybox_id = find_key(body, ("heybox_id", "userid", "user_id"))
    if not pkey:
        # 有些版本把 pkey 放在 Set-Cookie 里
        pkey = login.s.cookies.get("pkey", "") or login.s.cookies.get("user_pkey", "")
    if not pkey:
        print("\n登录成功但没找到 pkey，把下面这段发给作者（不含账号密码）：")
        print(json.dumps(body, ensure_ascii=False)[:800])
        return 1

    cookie = ";".join([
        f"pkey={pkey}",
        f"x_xhh_tokenid={args.tokenid}",
        f"imei={imei}",
        "device_info=M2012K11AC",
        "os_version=13",
        "dw=411",
        "channel=heybox",
    ])
    print(f"\n登录成功：heybox_id={heybox_id or '未知'}")

    try:
        account = XiaoHeiHe(cookie, args.hkey_api, channel="app")
        snapshot = account.task_list()
        print(f"验证通过：{snapshot['username'] or '未知用户'}，H 币 {snapshot['coin']}")
    except Exception as error:  # noqa: BLE001
        print(f"验证没通过（{error}），Cookie 可能仍可用，可以先填进青龙试一次")

    print("\n把下面这一整行填到青龙环境变量 XHH_COOKIE：\n")
    print(cookie)
    return 0


if __name__ == "__main__":
    sys.exit(main())
