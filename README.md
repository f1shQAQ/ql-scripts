# ql-scripts

自用的青龙面板签到脚本。

| 脚本 | 任务 | 默认定时 | 环境变量 |
|---|---|---|---|
| `checkin_baiduwp.py` | 百度网盘会员签到 + 每日答题 | 每天 08:15 | `BAIDUWP_COOKIE` |
| `checkin_bilibili.py` | B站漫画签到、直播签到、观看、分享，可选投币 | 每天 08:30 | `BILI_COOKIE` 等 |

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

### B站

| 变量 | 默认 | 说明 |
|---|---|---|
| `BILI_COOKIE` | 无 | 浏览器登录 bilibili.com，复制 `Cookie` 请求头。必须有 `SESSDATA` 和 `bili_jct`。 |
| `BILI_COIN_NUM` | `0` | 每天投币总数，0～5。默认不投币。 |
| `BILI_SILVER2COIN` | `false` | `true` 时用银瓜子换硬币。 |
| `BILI_VIP_PRIVILEGE` | `true` | 大会员时自动领取每月权益；`false` 关闭。 |

## 安全

- Cookie 等于账号登录态。只放在青龙的环境变量里，不要提交到这个仓库。
- 脚本调用的都是非官方接口。接口改动后脚本会报错，看青龙日志里的返回内容排查。

## 本地测试

```bash
pip install -r requirements.txt
BAIDUWP_COOKIE="..." python checkin_baiduwp.py
```

## 致谢

接口参考 [Sitoi/dailycheckin](https://github.com/Sitoi/dailycheckin)（MIT）和 [SocialSisterYi/bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)。
