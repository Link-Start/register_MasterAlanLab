# exa-register

Exa 自动注册脚本，走邮箱验证码，登录 dashboard 抓取完整 API Key 并写入 `exa_apikeys.txt`（一行一个 key）。

## 环境要求
- Python 3.10+
- 已安装 `uv`
- Chrome/Chromium 可用（Camoufox/patchright 依赖）

## 安装
```bash
cd exa-register
uv sync
```

## 配置（.env 或环境变量）
- 邮箱提供方：`EMAIL_PROVIDER` = `auto` | `cloudflare` | `duckmail` | `gptmail` | `tempmail`
- Cloudflare 自建邮件 API：`EMAIL_API_URL`, `EMAIL_API_TOKEN`, `EMAIL_DOMAIN` / `EMAIL_DOMAINS`
- DuckMail：`DUCKMAIL_API_URL` (默认 https://api.duckmail.sbs), `DUCKMAIL_API_KEY`, `DUCKMAIL_DOMAIN` / `DUCKMAIL_DOMAINS`
- GPTMail：无需额外配置
- TempMail.lol：无需额外配置
- `auto`：优先 TempMail.lol，失败自动回退 GPTMail
- 运行参数：
  - `DEFAULT_COUNT` (默认 5)、`DEFAULT_CONCURRENCY` (默认 2)、`DEFAULT_DELAY` (默认 10)
  - `REGISTER_HEADLESS` (默认 true) — 设为 false 可前台可视化
  - `EMAIL_CODE_TIMEOUT` (默认 90s)，`API_KEY_TIMEOUT` (默认 20s)

## 使用
直接跑主入口，自动生成邮箱与密码并注册：
```bash
cd exa-register
uv run python exa_core.py
```

## 输出
- 成功的 API Key 追加写入 `exa_apikeys.txt`，每行一个 key。
- 控制台会打印邮箱、标记密码（EMAIL_OTP_ONLY）和 key 供核对。

## 工作流摘要
1) 创建一次性邮箱（按 `EMAIL_PROVIDER`）
2) 打开 Exa 登录页，填邮箱 -> 收验证码 -> 登录
3) 进入 dashboard，优先调用 `/api/get-api-keys`，兜底页面提取
4) 调用 Exa API 校验 key 可用后写入 `exa_apikeys.txt`

## 常见问题
- 一直拿不到验证码：检查邮箱 API 配置、域名是否可用，或加长 `EMAIL_CODE_TIMEOUT`
- 浏览器前台闪退：把 `REGISTER_HEADLESS=false` 并确保本机有 Chrome
- 没写入 key：看日志是否校验失败，或 dashboard 没渲染完整 key

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