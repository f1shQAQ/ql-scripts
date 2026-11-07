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
| `XHH_COOKIE` | 无 | 登录凭据，网页扫码的 `pkey=xxx` 就够（也认 `user_pkey`）。多个账号用换行或 `&&` 分隔。 |
| `XHH_HKEY_API` | `https://hkey.qcciii.com/hkey` | 只在 App 通道用：计算 App 签名的服务地址，见下面说明。 |

脚本有两条通道，会按 Cookie 形态自动选，一条失败自动换另一条：

- **Web 通道（默认）**：签名在本地算，不依赖任何第三方服务，Cookie 只要 `pkey`。扫码登录拿到的就是这种。读任务列表、查 H 币都走这条。
- **App 通道**：用抓包来的 Cookie + 设备字段，签名得找外部 hkey 服务。设备字段齐全且有 `x_xhh_tokenid` 时优先走这条。

签到属于写操作，风控比读取严。实测**网页扫码拿到的 pkey 在 App 通道不可用**（会返回「请重新登录」），所以只有抓包拿到 App 登录态时才走 App 通道；Web 通道签到如果返回「非法的请求」，在本地跑一下诊断工具，把输出发出来即可定位：

```bash
python tools/xhh_probe.py --cookie "你的 XHH_COOKIE"
```

它会依次尝试官网 / 账号中心 / 手机 UA 等几组参数并打印服务端真实返回（会真的调签到接口）。也可能补充 `x_xhh_tokenid`（数美设备指纹）才有用，见下面说明。

#### 获取 XHH_COOKIE

**推荐：脚本里扫码登录，不用抓包**

小黑盒 App 有证书校验（SSL Pinning），没越狱的 iPhone、没 root 的安卓都抓不到包。所以这里走网页登录接口（不需要 App 签名）：

1. 本地安装二维码库（只在这台电脑用，青龙上不需要）：`pip install qrcode`
2. 运行：

```bash
python tools/xhh_cookie.py
```

3. 终端会显示二维码，用小黑盒 App 扫一下并在手机上确认。
4. 脚本自动生成设备指纹（`imei` 按账号固定生成，同一账号每次一致），并试读一次任务列表确认能登录，最后输出可直接填进青龙的 `XHH_COOKIE`。

设备指纹默认用一套安卓值，只对 App 通道有用，Web 通道会忽略。`--device iphone` 可以换成 iPhone 的机型参数，或者用 `--device-info`、`--os-version`、`--dw`、`--imei` 单独覆盖。扫码登录对账号只做一次，之后 Cookie 一直有效，直到你改密码或主动退出。

#### 补充 x_xhh_tokenid（签到时被风控挡了才需要）

`x_xhh_tokenid` 是小黑盒网页里的设备指纹 Cookie（数美 SDK 生成），扫码接口不会下发。签到被两条通道都拒绝时，可以自己取一个：

1. 电脑浏览器打开 https://xiaoheihe.cn ，登录（扫码即可）。
2. F12 → Application（应用）→ Cookies → `https://xiaoheihe.cn`，找到 `x_xhh_tokenid`，复制整段值（很长，`B` 开头）。
3. 生成 Cookie 时带上：

```bash
python tools/xhh_cookie.py --tokenid "粘贴到这里"
```

或者在青龙的 `XHH_COOKIE` 里直接追加一段 `;x_xhh_tokenid=值`。这个 Cookie 有效期约 14 天，过期后重新复制一次。

扫码登录拿到的 Cookie 里没有 `x_xhh_tokenid`（这是浏览器里请求过接口才会有的字段），Web 通道用不上它，脚本会跳过。加 `--debug` 可以打印登录接口的原始返回，排查问题用。

**备选：手动粘贴网页 Cookie**

如果不想扫码，也可以在电脑浏览器登录 https://xiaoheihe.cn ，F12 → Network 点一个 `api.xiaoheihe.cn` 请求，右键 → Copy as cURL，然后：

```bash
python tools/xhh_cookie.py --cookie "把 cURL 整段粘在这里"
```

也可以直接粘 Cookie 请求头、Cookie-Editor 导出的 JSON 或 cookies.txt（`--file` 从文件读）。多个账号用 `&&` 分隔。

**可选：抓 App 包**（走 App 通道，字段要真实）

用 Reqable / HttpCanary 抓 `api.xiaoheihe.cn` 的请求（登录后进「我 → 我的任务」），从请求头 Cookie 里复制 `pkey` 和 `x_xhh_tokenid`，再从查询参数里抄设备字段，拼成一行：

```
pkey=xxx;x_xhh_tokenid=xxx;imei=xxx;device_info=xxx;os_version=13;dw=411;channel=heybox_xiaomi
```

- `pkey`：身份凭据，`heybox_id` 会从中自动解析，不用单独填。
- `x_xhh_tokenid`：App 通道的身份凭据，有就带上；网页登录拿不到。
- `imei` / `device_info` / `os_version` / `dw`：设备字段，从任意一个 App 请求的查询参数里照抄即可，走 App 通道时四个都不能省；`channel` 可选。

#### 关于 hkey 签名服务

只有 App 通道需要它：App 的 `hkey` 由客户端 native 代码计算，纯脚本算不了，所以脚本把 `heybox_id`、`imei`、接口路径、时间戳发给第三方 hkey 服务（默认社区公共的 `hkey.qcciii.com`，可用 `XHH_HKEY_API` 替换）代算签名，**不发送 Cookie**。注意：

- 公共服务的可用性没有保障，挂了 App 通道会报「hkey 服务请求失败」，脚本会自动改走 Web 通道。
- 对隐私敏感的话可以只用 Web 通道，完全不需要这个服务——这也是扫码登录的默认路线。

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
