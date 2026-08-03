# tavily-register

## 目录结构

- `batch_signup.py`：批量任务入口（CLI），负责邮箱生成、调用注册流程、验证与保存结果
- `signup.py`：核心注册/登录/取 Key 逻辑（`requests.Session` 驱动）
- `gptmail_client.py`：临时邮箱（GPTMail 兼容接口）客户端

## 环境要求

- Python `>= 3.12`
- 推荐使用 `uv` 管理依赖与虚拟环境

## 安装

```bash
uv sync
```

## 配置

### 1) `config.yaml`

`signup.py` 会从仓库根目录读取 `config.yaml`（已在 `.gitignore` 中忽略）。现在验证码识别默认走 YesCaptcha。示例：

```yaml
# YesCaptcha 图片识别 key（ImageToTextTask）
YESCAPTCHA_CLIENT_KEY: "YOUR_YESCAPTCHA_KEY"
```

也支持通过环境变量提供：

- `YESCAPTCHA_CLIENT_KEY`
- `YESCAPTCHA_KEY`

### 2) 临时邮箱环境变量（可选）

`batch_signup.py` 支持通过环境变量配置邮箱服务：

- `GPTMAIL_BASE_URL`
- `GPTMAIL_API_KEY`
- `GPTMAIL_TIMEOUT`
- `GPTMAIL_PREFIX`
- `GPTMAIL_DOMAIN`

## 运行

查看参数：

```bash
uv run python batch_signup.py --help
```

脚本内置临时邮箱的共享key，批量注册

```
uv run python batch_signup.py
```

脚本默认带 Tavily 单 IP 限速保护：每完成 6 个注册，会自动等待 90 分钟再继续，避免触发平台限制。也可以手动调整：

```bash
uv run python batch_signup.py -n 20 --max-per-window 6 --window-seconds 5400
```

如共享key额度用完，可以到https://mail.chatgpt.org.uk获取

```
uv run python batch_signup.py --gptmail-api-key your_own_key
```

如果你没有把 YesCaptcha key 写进 `config.yaml`，也可以直接用环境变量运行：

```bash
YESCAPTCHA_CLIENT_KEY=your_yescaptcha_key uv run python batch_signup.py --gptmail-api-key your_own_key
```



## 输出文件

- `api_keys.txt`：成功记录（邮箱与 key）
- `failed.txt`：失败记录（邮箱与错误信息）
- `banned_domains.txt`：被判定为不可用的域名黑名单
- `run.log`：运行日志（开始处理、成功、失败、进入 90 分钟等待、恢复时间等）

## 常见问题

- `ip-signup-blocked`：表示当前出口 IP 被禁止注册。脚本会终止批量流程
- `custom-script-error-code_extensibility_error`：通常表示当前邮箱域名被禁止。脚本会将域名写入 `banned_domains.txt` 并在自动生成邮箱模式下重新获取邮箱重试。
- `invalid-captcha`：验证码识别结果不正确。可更换 YesCaptcha key、降低并发、增加重试间隔
- `tavily`调整了策略，一个ip一段时间内只能注册5个，请勿滥用
