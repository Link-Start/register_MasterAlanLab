# OpenAI 自动注册

通过比特浏览器管理可复用的指纹窗口，使用 Playwright CDP 控制 ChatGPT 注册页面，并从临时邮箱读取验证码。进入 ChatGPT 页面并确认登录成功后，本轮流程结束。

## 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- 已安装、登录并正在运行的比特浏览器
- 比特浏览器已开启 Local API

## 安装

```bash
cd openai-register
uv sync
```

## Bit Browser 配置

可在 shell 中设置环境变量，也可以参考 `.env.example`：

```bash
export BIT_BROWSER_API_URL="http://127.0.0.1:54346"
export BIT_BROWSER_NAME="openai-register"
export BIT_BROWSER_ID=""
export BIT_BROWSER_HEADLESS="false"
export BIT_BROWSER_CLOSE_WAIT="5"
export BROWSER_TIMEOUT="30000"
```

代理请直接在比特浏览器的窗口配置中设置。程序不提供 `--proxy` 参数，创建或复用窗口时也不会写入或覆盖代理字段。

窗口管理规则：

- `BIT_BROWSER_ID` 非空时直接复用指定窗口。
- 未指定 ID 时按 `BIT_BROWSER_NAME` 精确查找；没有则创建，存在则复用。
- 发现多个同名窗口时停止运行，避免使用错误的浏览器环境。
- 每轮启动前重新生成浏览器指纹。
- 每轮结束后关闭窗口并清除 Cookie 和缓存，但保留窗口 ID 供下一轮复用。

## 邮箱 Provider

支持：

- `auto`
- `luckmail`
- `tempmail`
- `gptmail`
- `outlook_tw`

### outlook.tw

`outlook_tw` 使用匿名邮箱 API，无需 API Key：

```bash
export OUTLOOK_TW_BASE_URL="https://outlook.tw"
export OUTLOOK_TW_USERNAME_LENGTH="8"
export OUTLOOK_TW_DOMAIN_INDEX="0"
export OUTLOOK_TW_POLL_INTERVAL="3"
export OUTLOOK_TW_REQUEST_TIMEOUT="30"
export OUTLOOK_TW_MAX_WAIT="300"
```

### LuckMail

```bash
export LUCKMAIL_BASE_URL="https://mails.luckyous.com"
export LUCKMAIL_API_KEY="YOUR_LUCKMAIL_API_KEY"
export LUCKMAIL_API_SECRET=""
export LUCKMAIL_USE_HMAC="false"
export LUCKMAIL_PROJECT_CODE="openai"
export LUCKMAIL_EMAIL_TYPE="ms_graph"
export LUCKMAIL_DOMAIN="outlook.com"
export LUCKMAIL_ORDER_TIMEOUT="180"
export LUCKMAIL_POLL_INTERVAL="6"
```

## 运行

注册一次：

```bash
uv run python openai_register.py --mail-provider outlook_tw --once
```

持续运行：

```bash
uv run python openai_register.py --mail-provider outlook_tw
```

## 注册流程

1. 临时邮箱 provider 创建或分配邮箱。
2. 按固定名称查找或创建比特浏览器窗口。
3. 配置窗口复用规则并随机生成本轮指纹。
4. Playwright 通过 CDP 接管比特浏览器窗口。
5. 在 ChatGPT 页面提交邮箱并轮询 6 位验证码。
6. 填写姓名和年龄，完成账户创建。
7. 等待 ChatGPT 输入框出现，确认账户创建和登录成功。
8. 关闭浏览器窗口，清除 Cookie 和缓存。

当前页面流程使用邮箱验证码注册，不设置密码，也不会保存账号或 token 文件。成功结果只输出到终端。

## 参数

- `--mail-provider`：邮箱 provider。
- `--once`：只运行一轮。
- `--sleep-min`：失败后最小等待秒数。
- `--sleep-max`：失败后最大等待秒数。
- `--luckmail-*`：LuckMail 配置。
- `--outlook-tw-*`：outlook.tw 配置。

## 常见问题

### 无法连接比特浏览器

确认比特浏览器正在运行、已开启 Local API，并检查 `BIT_BROWSER_API_URL` 的端口。

### 出现多个同名窗口

删除或重命名重复窗口，或者通过 `BIT_BROWSER_ID` 明确指定需要复用的窗口。

### 收不到验证码

检查邮箱 provider 服务状态，并适当增加轮询间隔或最大等待时间。

### 登录成功判断失败

使用前台模式观察页面，确认账户创建完成后是否已经进入 ChatGPT，并检查 `#prompt-textarea` 是否正常显示。

## 资源推荐
- [Captcha.run](https://captcha.run/sso?inviter=542f4f4f-31b6-4b70-b485-c4762c45d1e8)（打码平台，强烈推荐）
- [YesCaptcha](https://cutt.ly/Mywt39r0)（自动验证码识别工具，便宜，好用）
- [订阅合租拼车](https://cutt.ly/5ywt8vb4)（国外合租平台，可以合租各种影视会员、AI订阅）
- [海外账号、电话卡](https://cutt.ly/dywt86NC)（TG账号、TikTok账号等等海外平台账号）
- [满血CC、GPT中转站](https://cutt.ly/JywJG3G5)（可以确认不掺水，缺点是价格偏高）
- [Telegram 搜索机器人](https://cutt.ly/2yeh3GOE)(TG 最强搜素引擎，试试看吧)
- [比特指纹浏览器](https://client.bitbrowser.cn/register?lang=zh&code=Alan123)（艾伦日常使用的指纹浏览器，挺好用的，没什么硬伤）

## 🚀 GPT 代充值平台

[艾伦の代充](https://ai.corouter.cc) 支持使用卡密自动完成 ChatGPT 等 AI 订阅代充，客户无需注册登录。

### 合伙人机制

- **邀请制加入**：使用平台签发的一次性邀请码注册，获得独立的合伙人工作空间。
- **自主开展业务**：充值业务积分并配置支付资源，自主生成、分发卡密，客户凭卡密完成充值。
- **灵活接入渠道**：既可直接销售卡密，也可通过 API 接入自己的站点。
- **数据独立管理**：每位合伙人的卡片、卡密、订单、积分和 API Token 相互隔离。
- [查看合伙人机制与参与指南](https://ai.corouter.cc/partner-guide)