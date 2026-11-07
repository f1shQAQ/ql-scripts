#!/usr/bin/env python3
"""在本地电脑运行一次，登录小黑盒并生成青龙用的 XHH_COOKIE。

用法：
    python tools/xhh_cookie.py                    # 扫码登录（推荐，手机 App 扫终端里的二维码）
    python tools/xhh_cookie.py --cookie "..."     # 手动传入 Cookie / cURL / JSON
    python tools/xhh_cookie.py --file ck.txt      # 从文件读

为什么需要这个：小黑盒 App 有证书校验（SSL Pinning），iPhone / 没 root 的安卓都抓不到包。
这里改走网页登录接口（不需要签名），拿到登录态后再按青龙脚本需要的格式拼好。

扫码登录额外需要 qrcode 库（本地用，青龙上不需要）：
    pip install qrcode

设备字段默认按脚本里的安卓请求配置生成：imei 按 pkey 固定生成（同一账号每次一致），
device_info / os_version / dw 用常见机型。想换成别的用 --device iphone 或手动覆盖。

Cookie 等于账号登录状态，不要发给别人，也不要提交到仓库。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qlutils import parse_cookie_export  # noqa: E402
from checkin_xiaoheihe import (  # noqa: E402
    DEFAULT_HKEY_API,
    heybox_id_from_pkey,
    open_account,
)

API = "https://api.xiaoheihe.cn"
QR_CODE_URL = f"{API}/account/get_qrcode_url/"
QR_STATE_URL = f"{API}/account/qr_state/"
RESTORE_URL = f"{API}/account/restore_login"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# 设备指纹预设。脚本的 App 请求配置是安卓的，所以默认也用安卓值。
DEVICE_PRESETS = {
    "android": {"device_info": "M2012K11AC", "os_version": "13", "dw": "411"},
    "iphone": {"device_info": "iPhone15,3", "os_version": "17.2", "dw": "430"},
}


def extract_cookie_blob(text: str) -> str:
    """从 cURL / fetch 代码里抠出 Cookie，其他格式原样返回。"""
    patterns = [
        r"-H\s+['\"]Cookie:\s*([^'\"]+)['\"]",
        r"--header\s+['\"]Cookie:\s*([^'\"]+)['\"]",
        r"(?:^|\s)-b\s+['\"]([^'\"]+)['\"]",
        r"cookie['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return text.strip()


def split_accounts(raw: str) -> list[str]:
    text = raw.strip()
    if "&&" in text:
        return [chunk.strip() for chunk in text.split("&&") if chunk.strip()]
    if text.lower().startswith("curl") or "fetch(" in text:
        return [text]
    if text.startswith("[") or text.startswith("{") or "\t" in text:
        return [text]
    return [line.strip() for line in text.splitlines() if line.strip()]


def default_imei(pkey: str) -> str:
    """按 pkey 生成固定的 15 位数字串当 imei，同一个账号每次一致。"""
    digest = hashlib.sha1(pkey.encode()).hexdigest()
    return "86" + "".join(str(int(char, 16) % 10) for char in digest[:13])


def render_qr(url: str, out_path: str | None) -> None:
    try:
        import qrcode
    except ImportError:
        print("扫码登录需要 qrcode 库，先运行：pip install qrcode")
        raise SystemExit(1)

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)

    if out_path:
        import qrcode.image.svg

        path = Path(out_path)
        qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(str(path))
        print(f"\n二维码也保存到了：{path.resolve()}（终端二维码扫不出来时，用浏览器打开它）")


def qr_login(qr_out: str | None, debug: bool = False) -> dict[str, str]:
    """扫码登录，返回拿到的 Cookie 字段。"""
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})

    r = session.get(QR_CODE_URL, params={"app": "web", "_notip": "true"}, timeout=15)
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"获取二维码失败：{data.get('msg') or data}")
    result = data.get("result") or {}
    qr_url = result.get("qr_url") or ""
    qr_id = parse_qs(urlparse(qr_url).query).get("qr", [""])[0]
    if not qr_id:
        raise RuntimeError(f"二维码返回异常：{result}")
    expire = int(result.get("expire") or 120)

    print("\n用小黑盒 App 扫下面的二维码，然后在手机上确认登录：\n")
    render_qr(qr_url, qr_out)

    deadline = time.time() + expire
    while time.time() < deadline:
        time.sleep(2)
        r = session.get(QR_STATE_URL, params={"qr": qr_id, "app": "web"}, timeout=15)
        data = r.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"轮询登录状态失败：{data.get('msg') or data}")
        result = data.get("result") or {}
        state = str(result.get("error") or "wait")
        if state in ("wait", "ready"):
            continue
        if state in ("cancel", "canceled", "denied"):
            raise RuntimeError("手机上取消了登录")
        if state in ("timeout", "expired"):
            raise RuntimeError("二维码过期了，重新运行一次")
        if state != "ok":
            raise RuntimeError(f"登录失败：{result.get('error_msg') or state}")

        if debug:
            print("\n[debug] qr_state 返回：", result)

        # 网页端确认登录后还会调一次 restore_login 补齐登录态，这里照着做，
        # 顺便把服务端随后补发的 Cookie 一起收进来。
        restored: dict = {}
        try:
            r = session.get(RESTORE_URL, params={}, timeout=15)
            restored = (r.json().get("result") or {}) if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001
            pass

        fields = {cookie.name: cookie.value for cookie in session.cookies}
        # 身份字段也可能直接出现在返回体里（pkey / profile / account_detail）
        identity = restored or result
        for name in ("pkey", "user_pkey"):
            value = identity.get(name) or result.get(name)
            if value:
                fields.setdefault(name, str(value))
        for name in ("heyboxid", "nickname", "userid"):
            value = result.get(name) or identity.get(name)
            if value:
                fields.setdefault(name, str(value))
        detail = result.get("account_detail") or identity.get("account_detail") or {}
        if isinstance(detail, dict) and detail.get("userid"):
            fields.setdefault("heyboxid", str(detail["userid"]))

        if debug:
            print("[debug] 拿到的 Cookie 字段：", sorted(fields))
        return fields

    raise RuntimeError("等待扫码超时，重新运行一次")


def device_fields(pkey: str, args: argparse.Namespace) -> dict[str, str]:
    preset = DEVICE_PRESETS[args.device]
    fields = {
        "imei": args.imei or default_imei(pkey),
        "device_info": args.device_info or preset["device_info"],
        "os_version": args.os_version or preset["os_version"],
        "dw": args.dw or preset["dw"],
    }
    if args.channel:
        fields["channel"] = args.channel
    return fields


def assemble(pkey: str, token_id: str, args: argparse.Namespace) -> str:
    token_id = token_id or getattr(args, "tokenid", "") or ""
    if not pkey:
        raise ValueError("没找到登录凭据 pkey，确认复制的是 api.xiaoheihe.cn 请求的 Cookie（不是网页静态资源的）")
    parts = {"pkey": pkey}
    if token_id:
        parts["x_xhh_tokenid"] = token_id
    else:
        # 网页扫码登录不会下发这个字段。Web 通道用不上它，App 通道也不校验，
        # 所以缺了照样能用，这里给个提示而不是报错。
        print("  提示：没有 x_xhh_tokenid（网页登录常见），Web 通道不需要它，继续。")
    parts.update(device_fields(pkey, args))
    return ";".join(f"{key}={value}" for key, value in parts.items())


def pick_pkey(fields: dict[str, str], expected_id: str = "") -> str:
    """pkey / user_pkey 都认，优先选和登录返回的 heybox_id 对得上、且能解析出 ID 的那个。"""
    candidates = [fields.get("pkey", ""), fields.get("user_pkey", "")]
    parsed = [(value, heybox_id_from_pkey(value)) for value in candidates if value]
    if expected_id:
        for value, user_id in parsed:
            if user_id == str(expected_id):
                return value
    for value, user_id in parsed:
        if user_id:
            return value
    return candidates[0] or candidates[1]


def from_blob(blob: str, args: argparse.Namespace) -> str:
    fields: dict[str, str] = {}
    for item in parse_cookie_export(blob, "xiaoheihe.cn"):
        fields.setdefault(item["name"], item["value"])
    # 网页登录态里身份字段叫 user_pkey，App 叫 pkey，两个都认
    return assemble(pick_pkey(fields), fields.get("x_xhh_tokenid", ""), args)


def read_input(args: argparse.Namespace) -> str:
    if args.cookie:
        return args.cookie
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    print("把下面任意一种内容粘贴进来，粘完按回车两次：")
    print("  1. F12 里右键请求 → Copy as cURL")
    print("  2. 请求头里的 Cookie 值")
    print("  3. Cookie-Editor 导出的 JSON")
    print()
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip() and lines:
            break
        lines.append(line)
    return "\n".join(lines)


def describe(cookie: str, args: argparse.Namespace) -> dict:
    xhh, snapshot = open_account(cookie, args.hkey_api)
    sign_state = next((t["state"] for t in snapshot["tasks"] if t["type"] == "sign"), "")
    print(f"  用户名：{snapshot['username'] or '未知'}")
    print(f"  H 币：{snapshot['coin']}")
    print(f"  今日签到：{'已签到' if sign_state == 'finish' else '未签到'}")
    print(f"  通道：{'Web（本地签名）' if xhh.channel == 'web' else 'App（hkey 服务）'}")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="登录小黑盒并生成 XHH_COOKIE")
    parser.add_argument("--cookie", help="直接传入 Cookie / cURL / JSON 内容")
    parser.add_argument("--file", help="从文件读取")
    parser.add_argument("--cookie-only", action="store_true", help="只输出 Cookie，不做验证")
    parser.add_argument("--hkey-api", default=DEFAULT_HKEY_API, help="签名服务地址")
    parser.add_argument("--device", choices=sorted(DEVICE_PRESETS), default="android", help="设备指纹预设")
    parser.add_argument("--imei", help="覆盖 imei（默认按 pkey 固定生成）")
    parser.add_argument("--device-info", help="覆盖 device_info")
    parser.add_argument("--os-version", help="覆盖 os_version")
    parser.add_argument("--dw", help="覆盖 dw")
    parser.add_argument("--channel", help="覆盖 channel（默认不带）")
    parser.add_argument("--qr-out", help="把二维码另存为 SVG，方便用浏览器打开")
    parser.add_argument("--tokenid", help="手动指定 x_xhh_tokenid（浏览器 Cookie 里能复制到）")
    parser.add_argument("--debug", action="store_true", help="打印登录接口的原始返回，排查问题用")
    args = parser.parse_args()

    manual = bool(args.cookie or args.file)
    if manual:
        raw = read_input(args)
        if not raw.strip():
            parser.print_help()
            return 1
        accounts = split_accounts(raw)
        cookies: list[str] = []
        for chunk in accounts:
            try:
                cookies.append(extract_cookie_blob(chunk))
            except Exception as error:  # noqa: BLE001
                print(f"解析失败：{error}")
                return 1
        blobs = cookies
    else:
        try:
            fields = qr_login(args.qr_out, args.debug)
        except Exception as error:  # noqa: BLE001
            print(f"\n登录失败：{error}")
            return 1
        print(f"\n登录成功：{fields.get('nickname') or '已获取账号'}（heybox_id={fields.get('heyboxid') or '未知'}）")
        blobs = [fields]

    results: list[str] = []
    failures = 0
    for index, blob in enumerate(blobs, 1):
        print(f"\n【账号 {index}】")
        try:
            if isinstance(blob, dict):
                cookie = assemble(
                    pick_pkey(blob, str(blob.get("heyboxid") or "")),
                    blob.get("x_xhh_tokenid", ""),
                    args,
                )
            else:
                cookie = from_blob(blob, args)
            if args.cookie_only:
                print("  已生成（跳过验证）")
            else:
                describe(cookie, args)
            results.append(cookie)
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  失败：{error}")
            print("  排查：Cookie 是否过期？重新登录再试一次（加 --debug 能看到接口原始返回）。")

    if not results:
        return 1

    print("\n把下面这一整行填到青龙环境变量 XHH_COOKIE：\n")
    print("&&".join(results))
    if failures:
        print(f"\n注意：{failures} 个账号失败，已经跳过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
