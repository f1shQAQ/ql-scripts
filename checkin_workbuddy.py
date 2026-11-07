#!/usr/bin/env python3
"""
cron: 10 9 * * *
new Env('WorkBuddy签到')

WorkBuddy（腾讯）「Buddy 加油站」每日签到领积分。

环境变量：
    WB_TOKEN   每个账号一行 "AT:RT"，前面可加 "备注:"，例如 "主号:eyJ...:eyJ..."。
               多个账号用换行或 "&&" 分隔。
               在本地运行 python tools/wb_token.py（短信验证码登录）获取。
               脚本每次运行先用 RT 续期，轮换后的新 AT / RT 会自动写回这个变量（需要青龙 2.18+）。

接口参考 L0NE-6/WorkBuddy-Daily 和 XYW110/WorkBuddy2Api 的抓包分析。
"""

from __future__ import annotations

import base64
import json
import uuid

from qlutils import lines, new_session, replace_env_value, run_accounts, safe_json

BASE = "https://www.workbuddy.cn"
REFRESH_URL = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"
UA = "WorkBuddy/5.9.3 WorkBuddy/5.9.3 CLI/2.165.0"


def jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:  # noqa: BLE001
        return {}


def parse_account(text: str) -> tuple[str, str, str]:
    """返回 (备注, AT, RT)。JWT 里没有冒号，所以按冒号拆分后挑出 eyJ 开头的两段。"""
    parts = [p.strip() for p in text.split(":") if p.strip()]
    tokens = [p for p in parts if p.startswith("eyJ") and p.count(".") == 2]
    label = next((p for p in parts if p not in tokens), "")
    if len(tokens) < 2:
        raise ValueError("WB_TOKEN 格式应为 \"AT:RT\" 或 \"备注:AT:RT\"，请用 tools/wb_token.py 重新获取")
    at, rt = tokens[0], tokens[1]
    if jwt_payload(at).get("typ") == "Offline":  # 写反了就换回来
        at, rt = rt, at
    return label, at, rt


def refresh(at: str, rt: str) -> tuple[str, str]:
    headers = {
        "X-Refresh-Token": rt,
        "X-Auth-Refresh-Source": "plugin",
        "Content-Type": "application/json",
        "X-Request-ID": uuid.uuid4().hex,
        "X-Domain": "www.workbuddy.cn",
        "Authorization": f"Bearer {at}",
    }
    uid = jwt_payload(at).get("sub")
    if uid:
        headers["X-User-Id"] = uid
    data = safe_json(new_session().post(REFRESH_URL, json={}, headers=headers, timeout=20))
    inner = data.get("data") or {}
    if data.get("code") == 0 and inner.get("accessToken"):
        return inner["accessToken"], inner.get("refreshToken") or rt
    raise ValueError(f"续期失败：{data.get('msg') or data}")


def api(at: str):
    return new_session(
        {
            "Authorization": f"Bearer {at}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": BASE,
            "Referer": f"{BASE}/profile/growth-center",
            "User-Agent": UA,
            "X-User-Id": jwt_payload(at).get("sub", ""),
        }
    )


def status(s) -> dict:
    data = safe_json(s.post(f"{BASE}/v2/billing/meter/checkin-activity-status", json={}, timeout=20))
    if data.get("code") not in (0, 200, "0", "200"):
        raise ValueError(f"查询签到状态失败：{data.get('msg') or data}")
    return data.get("data") or {}


def handle(text: str, _index: int) -> str:
    label, at, rt = parse_account(text)

    note = ""
    try:
        new_at, new_rt = refresh(at, rt)
        if new_rt != rt or new_at != at:
            ok = replace_env_value("WB_TOKEN", rt, new_rt) if new_rt != rt else True
            ok = (replace_env_value("WB_TOKEN", at, new_at) if new_at != at else True) and ok
            if not ok:
                print(f"新的 AT:RT（请手动更新到 WB_TOKEN）：\n{new_at}:{new_rt}")
        at = new_at
    except ValueError as e:
        note = f"{e}，用旧 AT 继续"  # AT 可能还没过期

    s = api(at)
    try:
        st = status(s)
    except ValueError as e:
        extra = f"（{note}）" if note else ""
        raise ValueError(f"{e}{extra}。凭据可能已失效，请重新运行 tools/wb_token.py") from None

    if st.get("active") is False:
        sign_msg = f"签到活动未开启（{st.get('activity_name') or ''}）"
    elif st.get("today_checked_in"):
        sign_msg = f"今天已签到，+{st.get('today_credit', '?')}"
    else:
        r = safe_json(s.post(f"{BASE}/v2/billing/meter/daily-checkin", json={}, timeout=20))
        d = r.get("data") or {}
        msg = str(r.get("msg") or "")
        if r.get("code") in (0, 200, "0", "200") and (d.get("credit") is not None or d.get("credit_delayed")):
            sign_msg = f"成功，+{d['credit']}" if d.get("credit") is not None else "成功，积分待入账"
        elif "已签到" in msg or "already" in msg.lower():
            sign_msg = msg
        else:
            raise ValueError(f"签到失败：{msg or r}")
        st = status(s)

    pairs = []
    if label:
        pairs.append(("账号", label))
    pairs += [
        ("签到", sign_msg),
        ("连续签到", f"{st.get('streak_days', '?')} 天"),
        ("本周", f"{st.get('week_checkin_days', '?')}/7"),
        ("本期累计", f"{st.get('total_credits', '?')} 积分"),
    ]
    if note:
        pairs.append(("续期", note))
    return lines(pairs)


if __name__ == "__main__":
    run_accounts("WorkBuddy签到", "WB_TOKEN", handle)
