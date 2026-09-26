# tavily-register

## 目录结构

- `main.py`：批量任务入口（CLI），负责邮箱生成、调用注册流程、验证与保存结果
- `signup.py`：核心注册/登录/取 Key 逻辑（`requests.Session` 驱动）
- `mail_provider.py`：统一邮箱提供商接口（支持 `corouter` 和 `luckmail`）
- `corouter_mail_provider.py`：`mail.corouter.cc` Emailbox 邮箱读取客户端

## 环境要求

- Python `>= 3.12`
- 推荐使用 `uv` 管理依赖与虚拟环境

## 安装

```bash
uv sync
```

## 配置

### `config.yaml`

程序统一从 `config.yaml` 读取全部配置（验证码、代理、邮箱服务、浏览器参数等）。
该文件已加入 `.gitignore`，首次使用请复制示例：

```bash
cp config.yaml.example config.yaml
```

所有值（包括敏感凭据）都填写到 `config.yaml`。

也可以只在本次运行中指定分组：

```bash
uv run python main.py --count 3 --mail-group "业务注册邮箱"
```

程序识别到 `email-in-use`、邮箱已注册或注册成功后，会将邮箱写入
`registered_emails.txt`。后续自动选邮箱会跳过记录中的地址；也可以用
`--registered-emails PATH` 指定记录文件位置。

Emailbox 接口是只读的：脚本从指定租户的 active 邮箱账号中轮换选择邮箱，
然后查询 `inbox` 与 `junk` 中的最近邮件并提取 Tavily 验证链接。列表和正文
请求均遵循 [Emailbox API 说明](https://mail.corouter.cc/llms.txt)。
如果收件箱暂时返回 502 或连接超时，程序会按 1、2、4 … 60 秒退避重试，
总等待时间最多 5 分钟；超时后记录当前邮箱并继续下一个。收件箱恢复后若
确认没有 Tavily 验证链接，也会立即跳过当前邮箱，不再空等下一轮轮询。

API key 以及租户 ID 都不要提交到远端仓库；`config.yaml` 已被 `.gitignore` 忽略。

## 运行

查看参数：

```bash
uv run python main.py --help
```

批量注册：

```bash
uv run python main.py
```

脚本默认带 Tavily 单 IP 限速保护：每完成 10 个注册，会自动等待 60 分钟再继续，避免触发平台限制。也可以手动调整：

```bash
uv run python main.py -n 20 --max-per-window 10 --window-seconds 3600
```

### 网络重试与 IP 轮换

- Tavily/Auth0 注册链路的每个 HTTP 节点最多执行 3 次（首次请求 + 2 次重试）。
- 同一出口 IP 的传输层网络异常跨节点累计；达到 3 次后立即废弃该 IP。
- 轮换 IP 时保留当前邮箱、密码和已取得的验证链接，并从当前账号的最近状态继续，不会直接跳到下一个邮箱。
- 同一 IP 仍保留最多 10 次注册尝试限制；任一阈值先达到都会触发轮换。
- Emailbox、LuckMail、YesCaptcha、代理提取和出口 IP 检测使用独立的网络重试，不计入 Tavily 出口 IP 的失败额度。
- YesCaptcha 等打码服务使用专用直连 Session，不使用 Tavily 注册代理，并忽略系统中的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 等环境代理。



## 输出文件

- `api_keys.txt`：成功记录（API Key 列表）
- `failed.txt`：失败记录（邮箱与错误信息）
- `registered_emails.txt`：已确认注册过的邮箱（本地忽略文件，自动去重）
- `run.log`：运行日志（开始处理、成功、失败、进入 90 分钟等待、恢复时间等）

## 常见问题

- `ip-signup-blocked`：表示当前出口 IP 被禁止注册。配置代理 API 时脚本会轮换 IP 并继续当前账号；未配置代理时记录失败。
- `invalid-captcha`：验证码识别结果不正确。可更换 YesCaptcha key、降低并发、增加重试间隔
- `tavily`调整了策略，一个ip一段时间内只能注册5个，请勿滥用

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
