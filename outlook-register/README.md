# Outlook 注册机使用指南

用于注册 Outlook / Microsoft 账号，每次运行处理一个账号，成功后自动保存到文件。

## 环境准备

需要 Python 3.10+、`uv`，以及能够访问 Microsoft 注册服务的网络。使用自动验证码识别时，需要准备自己的 Captcha.run API Key 和可用余额。

在仓库根目录执行：

```bash
cd outlook-register
uv sync
cp .env.example .env
```

后续命令均在 `outlook-register` 目录中运行。

## 配置

编辑 `.env`，填写基础配置：

```dotenv
CAPTCHA_RUN_KEY="你的 Captcha.run API Key"
MS_REGISTER_PROXY=
MS_REGISTER_COUNTRY=US
MS_REGISTER_OUTPUT=accounts.txt
```

需要代理时，将 `MS_REGISTER_PROXY` 填为 `http://user:password@host:port`。

常用可选配置：

| 配置项 | 用途 | 默认值 |
| --- | --- | --- |
| `MS_REGISTER_COUNTRY` | 注册国家或地区，例如 `US`、`JP` | `US` |
| `MS_REGISTER_MARKET` | 页面语言，例如 `en-US`、`ja-JP` | 空 |
| `MS_REGISTER_TIMEZONE` | 时区偏移，单位为分钟 | `480` |
| `MS_REGISTER_TIMEOUT` | 网络请求超时时间，单位为秒 | `30` |
| `CAPTCHA_RUN_MAX_WAIT` | 等待验证码结果的最长时间，单位为秒 | `60` |
| `MS_REGISTER_OUTPUT` | 成功账号保存路径 | `accounts.txt` |

## 运行示例

### 注册一个账号

将示例邮箱和密码替换为自己的值：

```bash
uv run python cli.py \
  --member-name example@outlook.com \
  --password 'Password123!' \
  --first-name Alan \
  --last-name User \
  --execute
```

`--member-name` 需要填写完整邮箱地址；密码至少 8 位。配置 `CAPTCHA_RUN_KEY` 后，会自动获取验证码结果。

**实际注册必须加上 `--execute`。**

### 只检查连接和邮箱名

```bash
uv run python cli.py \
  --member-name example@outlook.com \
  --password 'Password123!'
```

不加 `--execute` 时，只检查连接和邮箱名是否可用，不创建账号，也不保存账号文件。

### 指定国家、语言和出生日期

```bash
uv run python cli.py \
  --member-name example@outlook.com \
  --password 'Password123!' \
  --country JP \
  --market ja-JP \
  --birth-date 1995-05-20 \
  --execute
```

### 手动提供验证码结果

已有有效验证码结果时，可以通过参数直接填写：

```bash
uv run python cli.py \
  --member-name example@outlook.com \
  --password 'Password123!' \
  --captcha-token '你的验证码结果' \
  --execute
```

### 常用参数

| 参数 | 用途 |
| --- | --- |
| `--member-name example@outlook.com` | 完整邮箱地址，必填 |
| `--password 'Password123!'` | 账号密码，必填 |
| `--first-name Alan` / `--last-name User` | 名和姓 |
| `--country US` | 注册国家或地区 |
| `--market en-US` | 页面语言 |
| `--birth-date 1995-05-20` | 出生日期，格式为 `YYYY-MM-DD` |
| `--proxy http://host:port` | 指定代理 |
| `--env-file .env` | 指定配置文件 |
| `--captcha-token '验证码结果'` | 手动提供验证码结果 |
| `--output accounts_new.txt` | 修改结果文件名 |
| `--execute` | 执行账号注册 |

查看完整参数：

```bash
uv run python cli.py --help
```

## 输出位置

默认将成功账号追加到当前目录的 `accounts.txt`，每行格式：

```text
邮箱----密码----client_id----refresh_token
```

最后一段为空，表示本次没有获得 `refresh_token`。

修改保存路径可设置 `.env` 中的 `MS_REGISTER_OUTPUT`。命令行 `--output` 只修改文件名，例如 `accounts_new.txt`。

账号文件包含明文密码，请妥善保管；分享终端输出前，也请检查是否包含账号信息。

## 常见问题

- **运行后没有创建账号**：检查命令是否包含 `--execute`。
- **邮箱名不可用**：更换邮箱名后重新运行。
- **验证码获取失败**：检查 `CAPTCHA_RUN_KEY` 和平台余额；等待时间不足时增大 `CAPTCHA_RUN_MAX_WAIT`，或通过 `--captcha-token` 提供有效结果。
- **连接超时**：检查网络和代理设置，必要时增大 `MS_REGISTER_TIMEOUT`。
- **没有生成结果文件**：只有注册成功才会写入文件；检查终端结果和 `MS_REGISTER_OUTPUT` 指定的位置。
