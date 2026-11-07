#!/usr/bin/env python3
"""
cron: 25 8 * * *
new Env('夸克网盘签到')

夸克网盘每日签到领容量，顺便报告连签进度和总容量。

签到只能走移动端接口，要用手机抓包拿到 kps、sign、vcode 三个参数，网页 Cookie 签不了。

环境变量：
    QUARK_PARAM   手机打开夸克 App 的「签到领空间」页，抓包任意一个 drive-m.quark.cn 的请求，
                  复制整段 URL 填进来（URL 里要有 kps、sign、vcode）。
                  也可以写成 "kps=...; sign=...; vcode=..."，前面可加 "user=备注;" 区分账号。
                  多个账号用换行或 "&&" 分隔。

接口参考 Cp0204/quark-auto-save 和 BNDou/Auto_Check_In。
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from qlutils import lines, new_session, run_accounts, safe_json

API = "https://drive-m.quark.cn/1/clouddrive/capacity/growth"
MEMBER = {"NORMAL": "普通用户", "EXP_SVIP": "88VIP", "SUPER_VIP": "SVIP", "Z_VIP": "SVIP+"}


def parse_param(text: str) -> tuple[str, dict[str, str]]:
    """从 URL 或 "k=v; k=v" 文本里取出 user 备注和 kps / sign / vcode。"""
    user = ""
    m = re.search(r"(?<![\w])user=([^;&\n]+)", text)
    if m:
        user = m.group(1).strip()
    params: dict[str, str] = {}
    for key in ("kps", "sign", "vcode"):
        m = re.search(rf"(?<![\w]){key}=([^;&\s]+)", text)
        if m:
            params[key] = unquote(m.group(1).strip())
    missing = [k for k in ("kps", "sign", "vcode") if k not in params]
    if missing:
        raise ValueError(f"QUARK_PARAM 里缺少 {'、'.join(missing)}，请重新抓包复制完整 URL")
    return user, params


def size(b: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{b:.2f} {unit}"
        b /= 1024
    return str(b)


def handle(text: str, _index: int) -> str:
    user, params = parse_param(text)
    s = new_session(
        {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
            ),
            "Content-Type": "application/json",
        }
    )
    query = {"pr": "ucpro", "fr": "android", **params}

    info = safe_json(s.get(f"{API}/info", params=query, timeout=15))
    data = info.get("data")
    if not isinstance(data, dict) or "cap_sign" not in data:
        msg = info.get("message") or info.get("_raw") or info
        raise ValueError(f"读取签到信息失败，参数可能已失效，请重新抓包（{msg}）")

    cap = data.get("cap_sign") or {}
    progress, target = int(cap.get("sign_progress") or 0), int(cap.get("sign_target") or 0)
    if cap.get("sign_daily"):
        sign_msg = f"今天已签到，+{size(cap.get('sign_daily_reward') or 0)}"
    else:
        r = safe_json(s.post(f"{API}/sign", params=query, json={"sign_cyclic": True}, timeout=15))
        reward = (r.get("data") or {}).get("sign_daily_reward")
        if reward is None:
            raise ValueError(f"签到失败：{r.get('message') or r}")
        progress += 1
        sign_msg = f"成功，+{size(reward)}"

    pairs = []
    if user:
        pairs.append(("账号", user))
    pairs += [
        ("签到", sign_msg),
        ("连签进度", f"{progress}/{target}" if target else progress),
        ("会员", MEMBER.get(data.get("member_type"), data.get("member_type") or "未知")),
        ("总容量", size(data.get("total_capacity") or 0)),
        ("签到累计", size((data.get("cap_composition") or {}).get("sign_reward") or 0)),
    ]
    return lines(pairs)


if __name__ == "__main__":
    run_accounts("夸克网盘签到", "QUARK_PARAM", handle)
