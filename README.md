# ql-scripts

自用的青龙面板签到脚本。

| 脚本 | 任务 | 默认定时 | 环境变量 |
|---|---|---|---|
| `checkin_baiduwp.py` | 百度网盘会员签到 + 每日答题 | 每天 08:15 | `BAIDUWP_COOKIE` |
| `checkin_workbuddy.py` | WorkBuddy「Buddy 加油站」每日签到领积分 | 每天 09:10 | `WB_TOKEN` |
| `checkin_bilibili.py` | B站漫画签到、直播签到、观看、分享，可选投币 | 每天 08:30 | `BILI_COOKIE` 等 |
| `checkin_xiaoheihe.py` | 小黑盒签到，汇报每日任务和 H 币（本地签名，无需第三方服务） | 每天 08:45 | `XHH_COOKIE` |
| `checkin_msrewards.py` | Microsoft Rewards 国区：搜索、每日活动、App 签到、阅读 | 每天 09:20 | `MSR_COOKIE` 等 |

`qlutils.py` 是公共模块，不是任务。

## 拉取到青龙

在青龙「订阅管理」里新建订阅，或者在任务里执行：

```bash
ql repo https://github.com/f1shQAQ/ql-scripts.git "checkin_" "" "qlutils" "main" "py"
```

参数依次是：仓库地址、白名单（只把 `checkin_` 开头的文件建成任务）、黑名单、依赖文件、分支、文件后缀。

依赖：在「依赖管理 → Python3」里添加 `requests`（青龙一般自带）。

推送：默认走面板「系统设置 → 通知设置」（需要青龙 2.18+）。这条推送失败时，改用青龙自带的 `notify.py`，它读「配置文件」里的 `TG_BOT_TOKEN`、`PUSH_KEY` 等变量。用环境变量 `QL_NOTIFY_MODE` 可以改推送方式：

| 值 | 推送方式 |
|---|---|
| `auto`（默认） | 先走面板通知设置，失败再走 notify.py |
| `system` | 只走面板通知设置 |
| `script` | 只走 notify.py |
| `off` | 不推送，只打印到日志 |

## 环境变量

多个账号用换行或 `&&` 分隔。

### 百度网盘

| 变量 | 说明 |
|---|---|
| `BAIDUWP_COOKIE` | 浏览器登录 pan.baidu.com，按 F12 → 网络，复制任一请求的 `Cookie` 请求头。至少要有 `BDUSS` 和 `STOKEN`。也可以只填 `BDUSS` 的值。 |

### WorkBuddy

| 变量 | 说明 |
|---|---|
| `WB_TOKEN` | 每个账号一行 `AT:RT`，前面可加 `备注:`。在本地运行 `python tools/wb_token.py`，用短信验证码登录后获取。 |

- 脚本每次先用 RT 续期，新的 AT / RT 自动写回 `WB_TOKEN`（需要青龙 2.18+）。写回失败时，日志里会打印新值，请手动更新。
- 用短信登录拿凭据，可能会让其他设备上的 WorkBuddy 退出登录。
- 5.6.2 以后的客户端会加密本地登录文件，所以不要从客户端文件里复制 token，用 `tools/wb_token.py`。

### B站

| 变量 | 默认 | 说明 |
|---|---|---|
| `BILI_COOKIE` | 无 | 浏览器登录 bilibili.com，复制 `Cookie` 请求头。必须有 `SESSDATA` 和 `bili_jct`。 |
| `BILI_COIN_NUM` | `0` | 每天投币总数，0～5。默认不投币。 |
| `BILI_SILVER2COIN` | `false` | `true` 时用银瓜子换硬币。 |
| `BILI_VIP_PRIVILEGE` | `true` | 大会员时自动领取每月权益；`false` 关闭。 |

### 小黑盒

| 变量 | 默认 | 说明 |
|---|---|---|
| `XHH_COOKIE` | 无 | 登录凭据 + 设备字段，见下面「获取 XHH_COOKIE」。多个账号用换行或 `&&` 分隔。 |
| `XHH_HKEY_API` | `https://hkey.qcciii.com/hkey` | 计算 App 签名的服务地址，见下面说明。 |

两条签名通道，脚本自动选：

- **App 通道**：签到真正能用的通道。Cookie 里有 `x_xhh_tokenid` + 设备字段时优先走它。
- **Web 通道**：签名在本地算，不需要第三方服务；读任务列表、查 H 币没问题，但**签到会被拒**（返回「非法的请求」）——小黑盒的签到只认 App 签名。

所以关键结论：**要有 App 登录态才能签到**。网页扫码拿到的 pkey 只在 Web 通道有效，拿去 App 通道会提示「请重新登录」。

#### 获取 XHH_COOKIE

**推荐：手机号登录（直接拿 App 登录态，不用抓包）**

```bash
python tools/xhh_login.py --phone 138xxxxxxxx
```

输入手机收到的验证码，脚本会输出一整行 `XHH_COOKIE`，直接填进青龙即可。手机号和验证码只发往小黑盒官方接口，不经过第三方，也不会被保存。

**备选：扫码登录（只能读数据，签不了到）**

```bash
pip install qrcode          # 只在这台电脑用
python tools/xhh_cookie.py  # 终端显示二维码，用小黑盒 App 扫
```

适合先验证账号、看任务和 H 币。签到仍然需要上面那种 App 登录态。

**备选：手动粘贴 / 抓包**

网页 Cookie 同样只能读，`python tools/xhh_cookie.py --cookie "..."` 支持的格式有 cURL、Cookie 请求头、Cookie-Editor 导出的 JSON、cookies.txt。

抓 App 包（Reqable / HttpCanary / 安卓模拟器）能拿到 App 登录态，从请求头 Cookie 里复制 `pkey` 和 `x_xhh_tokenid`，再从查询参数里抄设备字段：

```
pkey=xxx;x_xhh_tokenid=xxx;imei=xxx;device_info=xxx;os_version=13;dw=411;channel=heybox_xiaomi
```

- `pkey`：身份凭据，`heybox_id` 会从中自动解析，不用单独填。
- `x_xhh_tokenid`：数美设备指纹，App 写操作会校验。
- `imei` / `device_info` / `os_version` / `dw`：设备字段，照抄请求参数；`channel` 可选。

**排查工具**

签到仍然失败时，用诊断脚本把真实接口返回打出来（会真的调签到接口）：

```bash
python tools/xhh_probe.py --cookie "你的 XHH_COOKIE"
```

#### 关于 hkey 签名服务

App 的 `hkey` 由客户端 native 代码计算，纯脚本算不了，所以脚本把 `heybox_id`、`imei`、接口路径、时间戳发给 hkey 服务（默认社区公共的 `hkey.qcciii.com`，可用 `XHH_HKEY_API` 替换）代算签名，**不发送 Cookie**。

- 公共服务的可用性没有保障，挂了会报「hkey 服务请求失败」。
- 对隐私敏感的话可以自己部署同协议的服务，或者接受「只能读、不能签到」。

### Microsoft Rewards（国区，cn.bing.com）

> Microsoft Rewards 条款禁止自动化，有封号风险，没兑换的积分会作废。脚本不含防检测代码。

| 变量 | 默认 | 说明 |
|---|---|---|
| `MSR_COOKIE` | 无 | 用插件导出，见下面「获取 MSR_COOKIE」。多个账号用 `&&` 分隔。 |
| `MSR_REFRESH_TOKEN` | 无 | 可选，用于 App 签到和阅读。在本地运行 `python tools/msr_token.py` 获取。多账号按 `MSR_COOKIE` 的顺序排。微软返回新 token 时，脚本自动写回这个变量（需要青龙 2.18+）。 |
| `MSR_SEARCH` | `true` | `false` 时不做搜索。 |
| `MSR_SEARCH_DELAY` | `15` | 两次搜索之间的秒数，最小 5。 |

#### 获取 MSR_COOKIE

rewards.bing.com 的会话 Cookie 很快过期，只复制请求头会一直报 `HTTP 401`。脚本需要 login.live.com 的登录态，过期后自动续登（和浏览器自动跳转一样）。

1. 安装浏览器插件 [Cookie-Editor](https://cookie-editor.com/)。
2. 登录微软账号，勾选「保持登录」。
3. 打开 https://rewards.bing.com ，点 Cookie-Editor → Export → JSON。
4. 打开 https://login.live.com/oauth20_desktop.srf （空白页是正常的），再导出一次 JSON。
5. 把两段 JSON 前后拼在一起，整段填进 `MSR_COOKIE`。

也支持 cookies.txt 格式（Get cookies.txt LOCALLY 等插件）和普通 `Cookie` 请求头。请求头没有 login.live.com 的部分，过期后不能自动续登。

任务：

| 任务 | 说明 |
|---|---|
| 电脑 / 手机搜索 | 在 cn.bing.com 搜百度、微博热搜词，按面板计数器搜到满分为止 |
| 每日活动 | 点击类活动直接完成；测验类可能完成不了 |
| App 签到、阅读赚积分 | 需要 `MSR_REFRESH_TOKEN` |

## 安全

- Cookie 等于账号登录态。只放在青龙的环境变量里，不要提交到这个仓库。
- 脚本调用的都是非官方接口。接口改动后脚本会报错，看青龙日志里的返回内容排查。

## 本地测试

```bash
pip install -r requirements.txt
BAIDUWP_COOKIE="..." python checkin_baiduwp.py
```

## 致谢

接口参考 [Sitoi/dailycheckin](https://github.com/Sitoi/dailycheckin)（MIT）、[SocialSisterYi/bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) 和 [yowiv08/heybox](https://github.com/yowiv08/heybox)。小黑盒 Web 端签名算法来自 [foxlesbiao/xiaoheihe-mcp](https://github.com/foxlesbiao/xiaoheihe-mcp) 和公开的逆向分析。
