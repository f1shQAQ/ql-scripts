#!/usr/bin/env python3
"""
cron: 15 8 * * *
new Env('百度网盘签到')

百度网盘会员成长值：每日签到 + 每日答题，最后查询等级和成长值。

环境变量：
    BAIDUWP_COOKIE   pan.baidu.com 的完整 Cookie（至少要有 BDUSS 和 STOKEN），
                     也可以只填 BDUSS 的值。多个账号用换行或 "&&" 分隔。

接口参考 Sitoi/dailycheckin 的 baiduwp 模块（MIT）。
"""

from __future__ import annotations

import time

from qlutils import find_key, lines, new_session, parse_cookie, run_accounts, safe_json

API = "https://pan.baidu.com"
COMMON = {"app_id": "250528", "web": "5"}


def normalize_cookie(cookie: str) -> str:
    if "=" not in cookie:  # 只填了 BDUSS 的值
        return f"BDUSS={cookie}"
    return cookie


def signin(session) -> str:
    data = safe_json(session.get(f"{API}/rest/2.0/membership/level", params={**COMMON, "method": "signin"}, timeout=15))
    points = find_key(data, "points")
    if points is not None:
        return f"成功，成长值 +{points}"
    msg = find_key(data, "error_msg") or find_key(data, "show_msg") or data.get("_raw") or data
    return f"未获得成长值（{msg}）"


def answer_question(session) -> str:
    q = safe_json(session.get(f"{API}/act/v2/membergrowv2/getdailyquestion", params=COMMON, timeout=15))
    ask_id = find_key(q, "ask_id")
    answer = find_key(q, "answer")
    if ask_id is None or answer is None:
        msg = find_key(q, "show_msg") or find_key(q, "error_msg") or "今天没有题目，或已经答过"
        return str(msg)
    time.sleep(2)
    r = safe_json(
        session.get(
            f"{API}/act/v2/membergrowv2/answerquestion",
            params={**COMMON, "ask_id": ask_id, "answer": answer},
            timeout=15,
        )
    )
    score = find_key(r, "score")
    msg = find_key(r, "show_msg") or ""
    return f"成长值 +{score} {msg}".strip() if score is not None else str(msg or r)


def user_info(session) -> tuple[str, str]:
    data = safe_json(session.get(f"{API}/rest/2.0/membership/user", params={**COMMON, "method": "query"}, timeout=15))
    level = find_key(data, "current_level")
    value = find_key(data, "current_value")
    return (str(level) if level is not None else "未知", str(value) if value is not None else "未知")


def handle(cookie: str, _index: int) -> str:
    cookie = normalize_cookie(cookie)
    if "BDUSS" not in parse_cookie(cookie):
        raise ValueError("Cookie 里没有 BDUSS")
    session = new_session(
        {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
            ),
            "Referer": "https://pan.baidu.com/wap/svip/growth/task",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": cookie,
        }
    )
    sign_msg = signin(session)
    time.sleep(3)
    answer_msg = answer_question(session)
    level, value = user_info(session)
    text = lines([("签到", sign_msg), ("答题", answer_msg), ("会员等级", level), ("成长值", value)])
    if level == "未知" and value == "未知":  # 查不到会员信息，基本就是 Cookie 失效
        raise ValueError(f"Cookie 可能已失效，请重新获取\n{text}")
    return text


if __name__ == "__main__":
    run_accounts("百度网盘签到", "BAIDUWP_COOKIE", handle)
