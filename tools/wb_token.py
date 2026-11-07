#!/usr/bin/env python3
"""在本地电脑运行一次，用短信验证码登录 WorkBuddy，拿到签到用的 AT 和 RT。

用法：
    python tools/wb_token.py

步骤：
    1. 输入手机号，收短信验证码。
    2. 输入验证码，脚本输出一行 "AT:RT"。
    3. 把这一行填到青龙环境变量 WB_TOKEN（多账号每行一个）。

这里用的是 WorkBuddy 官网插件登录接口，和客户端登录走同一个流程。
RT 等于账号的长期登录凭证，不要发给别人，也不要提交到仓库。
注意：登录可能会让其他设备上的 WorkBuddy 退出登录。
"""

from __future__ import annotations

import sys

import requests

BASE = "https://www.workbuddy.cn"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": BASE,
    "Referer": f"{BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}


def main() -> int:
    phone = input("手机号：").strip()
    r = requests.post(f"{BASE}/v2/plugin/login/send-sms", headers=HEADERS, json={"phone": phone}, timeout=20).json()
    if r.get("code") != 0:
        print(f"发送验证码失败：{r.get('msg') or r}")
        return 1
    code = input("短信验证码：").strip()
    r = requests.post(
        f"{BASE}/v2/plugin/login/token",
        headers=HEADERS,
        json={"login_method": "phone", "phone": phone, "sms_code": code},
        timeout=30,
    ).json()
    data = r.get("data") or {}
    at = data.get("accessToken") or data.get("access_token")
    rt = data.get("refreshToken") or data.get("refresh_token")
    if r.get("code") != 0 or not at or not rt:
        print(f"登录失败：{r.get('msg') or r}")
        return 1
    print("\n把下面这一整行填到青龙环境变量 WB_TOKEN（可在前面加 \"备注:\"）：\n")
    print(f"{at}:{rt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
