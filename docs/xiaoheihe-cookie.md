# 小黑盒 Cookie 获取指南

青龙里要填的 `XHH_COOKIE`，是**小黑盒 App 的登录态 + 设备字段**，一行搞定：

```
pkey=xxx;x_xhh_tokenid=xxx;imei=xxx;device_info=xxx;os_version=13;dw=411;channel=heybox
```

## 为什么必须用 App 登录态

小黑盒有两套接口签名：

- **Web 通道**：签名算法公开，能本地算，不需要第三方服务，但只能读任务列表、查 H 币。**签到会被拒**（返回「非法的请求」）。
- **App 通道**：签到和分享任务只能走这条。签名由 App 的 native 代码算，脚本靠第三方 hkey 服务代算。

网页扫码拿到的是 Web 登录态（`user_pkey`），拿去 App 通道会返回「请重新登录」。所以要自动签到，必须拿到 App 登录态。

## 方法一：手机号登录（推荐，不用抓包）

```bash
python tools/xhh_login.py --phone 138xxxxxxxx
```

流程：发验证码 → 输入手机收到的验证码 → 输出一整行 `XHH_COOKIE` → 填进青龙。

- 手机号和验证码只发往小黑盒官方接口，不经过第三方，也不会被保存。
- 脚本拿到的就是 App 登录态，签到能直接过。
- 短信有频率限制，**失败别连点**，等 10 分钟以上再试。

### 带上浏览器里的设备指纹（被风控时用）

登录接口会校验 `x_xhh_tokenid`（数美设备指纹）。脚本默认用一个占位值，正常情况下够用；如果发码时提示「手机号状态异常」，换成真实值再试：

1. 电脑浏览器打开 https://xiaoheihe.cn 并登录。
2. F12 → Application（应用）→ Cookies → `https://xiaoheihe.cn` → 复制 `x_xhh_tokenid`（`B` 开头，很长）。
3. 带上它重新登录：

```powershell
# PowerShell
$env:XHH_TOKENID = "粘贴到这里"
python tools/xhh_login.py --phone 138xxxxxxxx
```

或者直接 `--tokenid "B..."`。这个值有效期约 14 天。

### 常见报错

| 返回 | 含义 | 处理 |
|---|---|---|
| `手机号状态异常，无法获取验证码` | 发码被风控拦了 | 换真实 `x_xhh_tokenid`、换网络（手机热点）、等 10 分钟到 24 小时后重试 |
| `请输入正确的手机号码` | 号码格式不对 | 只填 11 位，不带 `+86` |
| `验证码错误，请重新获取` | 验证码不对或过期 | 重新发一次 |

## 方法二：扫码登录（只能读，签不了到）

```bash
pip install qrcode          # 只在这台电脑用，青龙上不需要
python tools/xhh_cookie.py  # 终端显示二维码，用小黑盒 App 扫
```

适合先确认账号能不能通、看看任务和 H 币。这条路拿到的是 Web 登录态，签到仍然需要方法一的 App 登录态。

也可以用 `--cookie` 手动粘网页 Cookie（cURL / Cookie 请求头 / Cookie-Editor 导出的 JSON / cookies.txt 都支持）：

```bash
python tools/xhh_cookie.py --cookie "把 cURL 整段粘在这里"
```

## 方法三：抓 App 包

能拿到最原始的 App 登录态，但需要 root 设备或安卓模拟器。

1. 装 MuMu / 雷电模拟器（带 root 开关）和 Reqable。
2. 把抓包证书装成**系统证书**（Android 7+ 只认系统证书，装成用户证书不生效）。
3. 模拟器里装小黑盒 App 并登录，进「我 → 我的任务」。
4. 抓 `api.xiaoheihe.cn` 的请求，右键 → Copy as cURL。
5. 粘给工具：

```bash
python tools/xhh_cookie.py --cookie "cURL"
```

工具会自动取出 `pkey`、`x_xhh_tokenid`，补上设备字段并验证一次。

## Cookie 字段说明

| 字段 | 说明 |
|---|---|
| `pkey` | 身份凭据，`heybox_id` 会从中自动解析，不用单独填 |
| `x_xhh_tokenid` | 数美设备指纹，App 的写操作会校验；扫码登录不给这个字段 |
| `imei` / `device_info` / `os_version` / `dw` | 设备字段，抓包时照抄请求参数；手机号登录工具会自动生成 |
| `channel` | 可选，抓包有就带上 |

多个账号用换行或 `&&` 分隔。

## 排查

签到失败时，用诊断脚本把服务端真实返回打出来（会真的调签到接口）：

```bash
python tools/xhh_probe.py --cookie "你的 XHH_COOKIE"
```

它会依次试官网参数组、账号中心参数组、手机 UA、`task_id`、token 放 query 等组合，打印每组的结果。把输出发出来即可定位。

只读验证（不签到）用：

```bash
python tools/xhh_probe.py --cookie "你的 XHH_COOKIE" --only-read
```
