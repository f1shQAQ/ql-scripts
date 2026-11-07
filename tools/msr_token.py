#!/usr/bin/env python3
"""在本地电脑运行一次，拿到 Microsoft Rewards App 任务用的 refresh token。

用法：
    python tools/msr_token.py

步骤：
    1. 脚本打印一个登录链接，用浏览器打开并登录你的微软账号。
    2. 登录完成后，浏览器会停在一个空白页，地址以
       https://login.live.com/oauth20_desktop.srf?code= 开头。
    3. 把这个完整地址粘贴回来，脚本输出 refresh token。
    4. 把 refresh token 填到青龙环境变量 MSR_REFRESH_TOKEN。

refresh token 等于账号的长期登录凭证，不要发给别人，也不要提交到仓库。
"""

from __future__ import annotations

import sys
from urllib.parse import parse_qs, urlencode, urlparse

import requests

CLIENT_ID = "0000000040170455"
SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"
REDIRECT = "https://login.live.com/oauth20_desktop.srf"
AUTHORIZE = "https://login.live.com/oauth20_authorize.srf"
TOKEN = "https://login.live.com/oauth20_token.srf"


def main() -> int:
    url = f"{AUTHORIZE}?{urlencode({'client_id': CLIENT_ID, 'scope': SCOPE, 'response_type': 'code', 'redirect_uri': REDIRECT})}"
    print("1. 用浏览器打开下面的链接并登录：\n")
    print(url)
    print("\n2. 登录后把浏览器地址栏里的完整地址粘贴到这里，然后按回车：")
    pasted = input("> ").strip()

    code = parse_qs(urlparse(pasted).query).get("code", [""])[0]
    if not code:
        print("地址里没有 code 参数。确认地址以 oauth20_desktop.srf?code= 开头。")
        return 1

    resp = requests.post(
        TOKEN,
        data={
            "client_id": CLIENT_ID,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT,
            "scope": SCOPE,
        },
        timeout=20,
    )
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    token = data.get("refresh_token")
    if not token:
        print(f"换取失败：HTTP {resp.status_code} {resp.text[:300]}")
        return 1

    print("\n3. 把下面这一整行填到青龙环境变量 MSR_REFRESH_TOKEN：\n")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
