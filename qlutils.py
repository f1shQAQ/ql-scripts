"""青龙脚本公共工具：读取账号、发送通知、整理输出。

本文件不是任务，拉库时要放进依赖文件参数，例如：
    ql repo https://github.com/f1shQAQ/ql-scripts.git "checkin_" "" "qlutils"
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Any, Callable, Iterable

import requests

# 青龙会把它自带的 notify.py 放在脚本目录；本地运行时没有它，就只打印。
try:
    from notify import send as _ql_send  # type: ignore
except Exception:  # noqa: BLE001
    _ql_send = None

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def get_accounts(env_name: str) -> list[str]:
    """读取环境变量里的账号。多个账号用换行或 "&&" 分隔。"""
    raw = os.getenv(env_name, "")
    parts = re.split(r"\n|&&", raw)
    return [p.strip() for p in parts if p.strip()]


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "y"}


def parse_cookie(cookie: str) -> dict[str, str]:
    """把 "a=1; b=2" 形式的 Cookie 字符串拆成字典。值里可以有 "="。"""
    result: dict[str, str] = {}
    for item in cookie.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key.strip():
            result[key.strip()] = value.strip()
    return result


def mask(text: str, keep: int = 3) -> str:
    """日志里只露出首尾几个字符。"""
    if not text:
        return ""
    if len(text) <= keep * 2:
        return text[0] + "***"
    return f"{text[:keep]}***{text[-keep:]}"


def new_session(headers: dict[str, str] | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    if headers:
        session.headers.update(headers)
    return session


def safe_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {"_raw": resp.text[:300], "_status": resp.status_code}


def find_key(obj: Any, key: str) -> Any:
    """在嵌套的 dict / list 里找第一个名为 key 的值，找不到返回 None。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_key(value, key)
            if found is not None:
                return found
    return None


def notify(title: str, content: str) -> None:
    print(f"\n===== {title} =====\n{content}")
    if _ql_send is None:
        print("（没有找到青龙的 notify.py，只打印不推送）")
        return
    try:
        _ql_send(title, content)
    except Exception as e:  # noqa: BLE001
        print(f"推送失败：{e}")


def run_accounts(
    title: str,
    env_name: str,
    handler: Callable[[str, int], str],
    gap_seconds: int = 3,
) -> None:
    """依次处理每个账号，把结果汇总成一条通知。

    handler(cookie, index) 返回这个账号的结果文本；抛异常时记为失败，不影响后面的账号。
    全部账号都失败时，进程以退出码 1 结束，方便在青龙日志里看出来。
    """
    accounts = get_accounts(env_name)
    if not accounts:
        print(f"没有设置环境变量 {env_name}，跳过。")
        return

    blocks: list[str] = []
    failed = 0
    for i, cookie in enumerate(accounts, 1):
        if i > 1:
            time.sleep(gap_seconds)
        try:
            text = handler(cookie, i)
        except Exception as e:  # noqa: BLE001
            failed += 1
            text = f"运行出错：{type(e).__name__}: {e}"
        blocks.append(f"【账号 {i}】\n{text}")

    notify(title, "\n\n".join(blocks))
    if failed == len(accounts):
        sys.exit(1)


def lines(pairs: Iterable[tuple[str, Any]]) -> str:
    return "\n".join(f"{name}：{value}" for name, value in pairs)
