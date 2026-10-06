# Steam 注册机使用指南

支持单个邮箱注册和邮箱文件批量导入，可自动读取验证邮件，也可手动填写验证链接。

## 环境准备

需要 Python 3.10+、`uv`，以及能够访问 Steam 和邮箱服务的网络。使用自动验证码识别时，需要准备自己的 Captcha.run API Key 和可用余额。

在仓库根目录执行：

```bash
cd steam-register
uv sync
cp config.yaml.example config.yaml
```

后续命令均在 `steam-register` 目录中运行。

## 配置

编辑 `config.yaml`，批量注册的基础配置如下：

```yaml
CAPTCHA_RUN_KEY: "你的 Captcha.run API Key"
MAIL_PROVIDER: auto
MAIL_FILE: mailboxes.txt
STEAM_PROXY: ""
```

需要代理时填写 `STEAM_PROXY`，例如 `http://user:password@host:port`。该设置用于 Steam 注册；邮箱和验证码服务仍直接连接。

常用可选配置：

| 配置项 | 用途 | 默认值 |
| --- | --- | --- |
| `MAIL_PROVIDER` | 邮箱读取模式 | `auto` |
| `MAIL_FILE` | 邮箱导入文件 | 空 |
| `MAIL_MAX_WAIT` | 等待验证邮件的最长时间，单位为秒 | `300` |
| `STEAM_TIMEOUT` | 网络请求超时时间，单位为秒 | `30` |
| `BATCH_DELAY` | 批量注册间隔，单位为秒 | `5` |
| `ACCOUNT_PREFIX` | 自动生成账号名的可选前缀 | 空 |
| `ACCOUNT_NAME_ATTEMPTS` | 重名时最多尝试的用户名数量 | `8` |
| `STEAM_PASSWORD` | 指定 Steam 密码，留空时自动生成 | 空 |
| `STEAM_OUTPUT` | 成功账号的 CSV 文件 | `accounts.csv` |
| `STEAM_TEXT_OUTPUT` | 额外保存 TXT，留空时不启用 | 空 |

邮箱文件和输出文件的相对路径，以配置文件所在目录为准。

## 账号命名

默认使用「中文拼音昵称 + 4 位数字」，例如 `QingFeng4827`、`XingHe7316`、`LiuYun2048`。昵称采用分词首字母大写，方便辨认和记忆。

注册时会检查用户名是否可用；遇到重名会自动换一个名字，最多尝试 8 个名称，继续使用本次邮箱和验证码。成功文件保存最终注册的用户名。

如需统一前缀，可设置 `ACCOUNT_PREFIX`，例如填写 `Alan_` 后会生成 `Alan_QingFeng4827`。

也可通过 `--account-name QingFeng4827` 手动指定用户名；被占用时按提示更换。

## 使用 Corouter 托管邮箱

支持通过 [Corouter 邮箱服务](https://mail.corouter.cc) 选择已有邮箱并自动读取 Steam 验证邮件，无需填写邮箱密码。请先在网页中添加并启用要使用的邮箱。

在 `config.yaml` 中填写：

```yaml
MAIL_PROVIDER: corouter
MAIL_FILE: ""
COROUTER_MAIL_TENANT_ID: "你的租户 ID"
COROUTER_MAIL_API_KEY: "你的邮箱 API Key"
COROUTER_MAIL_GROUP_ID: ""
COROUTER_MAIL_REQUEST_TIMEOUT: 90
COROUTER_MAIL_POLL_INTERVAL: 5
```

`COROUTER_MAIL_API_KEY` 支持纯 Key，也支持完整的 `Authorization: Bearer ...`。邮箱 API Key 与验证码服务的 `CAPTCHA_RUN_KEY` 分别填写。

默认从租户中选择一个未成功注册的可用邮箱：

```bash
uv run python main.py
```

批量处理 3 个邮箱，或处理全部可用邮箱：

```bash
uv run python main.py --count 3
uv run python main.py --all
```

仅使用指定分组，可填写 `COROUTER_MAIL_GROUP_ID`，或在运行时指定分组 ID / 名称：

```bash
uv run python main.py --mail-group "Steam" --count 3
```

使用租户中的指定邮箱：

```bash
uv run python main.py --mail-provider corouter --email user@example.com
```

也可配合 `--mail-file mailboxes.txt` 限定要使用的邮箱；文件中每行只需一个地址，且这些地址应已添加到该租户及所选分组中。

读取邮件会消耗服务的每日取件额度。额度用尽或 API Key 失效时，程序会停止当前批次；等待额度重置或更新配置后再运行。

## 准备邮箱文件

新建 `mailboxes.txt`，每行填写一个邮箱。支持以下格式：

```text
# 仅邮箱地址，运行时手动填写验证链接
user@example.com

# 邮箱地址和邮箱密码
user@example.com----MailboxPassword

# Outlook 邮箱四段格式
user@outlook.com----MailboxPassword----client_id----refresh_token

# 也支持后两段交换顺序
user@outlook.com----MailboxPassword----refresh_token----client_id
```

示例中的邮箱、密码、`client_id` 和 `refresh_token` 均需替换为实际值。`client_id` 应为完整 UUID，例如 `12345678-1234-1234-1234-123456789abc`。

文件使用 UTF-8 编码。分隔符可使用 `----`、`|` 或 Tab；同一行使用同一种分隔符。如果密码包含分隔符，请换用另一种。空行、`#` 注释和重复邮箱会自动忽略。

### 邮箱读取模式

| 模式 | 使用方法 |
| --- | --- |
| `auto` | 四段邮箱使用 `graph`，邮箱加密码使用 `imap`，仅邮箱地址使用 `manual` |
| `manual` | 自行打开邮箱，在终端粘贴本次收到的验证链接 |
| `corouter` | 从 Corouter 租户选择已有邮箱并自动读取验证邮件 |
| `graph` | 使用带有有效 `client_id` 和 `refresh_token` 的 Microsoft 邮箱 |
| `imap` | 使用邮箱密码，或已获得邮箱读取授权的四段邮箱 |
| `pop3` | 使用邮箱密码，不支持四段邮箱授权登录 |

使用自动读取前，请确认邮箱已开启相应读取方式，授权信息仍有效。非 Microsoft 邮箱需按邮箱服务商的说明填写服务器，例如：

```yaml
MAIL_PROVIDER: imap
IMAP_HOST: imap.example.com
IMAP_PORT: 993
```

使用 POP3 时，改为填写 `MAIL_PROVIDER: pop3`、`POP3_HOST` 和 `POP3_PORT`。

## 运行示例

### 检查连接

```bash
uv run python main.py --probe
```

此模式只检查连接状态，不注册账号。

### 使用配置中的邮箱文件

```bash
uv run python main.py
```

默认处理一个尚未成功注册的邮箱。账号名和 Steam 密码未指定时会自动生成；邮箱密码仅用于读取邮件。

### 批量注册

处理最多 3 个邮箱：

```bash
uv run python main.py --mail-file mailboxes.txt --count 3
```

处理文件中的全部邮箱：

```bash
uv run python main.py --mail-file mailboxes.txt --all
```

成功账号文件中已有的邮箱会自动跳过。失败邮箱仍可在后续运行中重新尝试。

### 手动验证单个邮箱

```bash
uv run python main.py --email user@example.com --account-name QingFeng4827
```

未配置 `CAPTCHA_RUN_KEY` 时，终端会提示填写验证码结果；收到验证邮件后，按提示粘贴本次注册的验证链接。

### 常用参数

| 参数 | 用途 |
| --- | --- |
| `--config config.yaml` | 指定配置文件 |
| `--email user@example.com` | 指定单个邮箱 |
| `--mail-file mailboxes.txt` | 指定邮箱导入文件 |
| `--mail-provider manual` | 指定邮箱读取模式 |
| `--mail-group "Steam"` | 指定 Corouter 邮箱分组 ID 或名称 |
| `--account-name QingFeng4827` | 指定单个账号的登录名 |
| `--password 'YourPassword123!'` | 指定 Steam 密码 |
| `--proxy http://host:port` | 指定注册代理 |
| `--count 3` / `--all` | 指定处理数量或处理全部邮箱 |
| `--output accounts_new.csv` | 指定成功账号 CSV 文件 |

查看完整参数：

```bash
uv run python main.py --help
```

## 输出位置

| 文件 | 内容 |
| --- | --- |
| `accounts.csv` | 注册成功的 Steam 账号名、密码、邮箱等信息 |
| `attempts.jsonl` | 本次尝试使用的账号名、密码和邮箱，便于检查注册状态 |
| `failed.jsonl` | 失败原因 |

`attempts.jsonl` 中有记录不代表注册成功，应以 `accounts.csv` 和实际登录结果为准。

如需额外保存 TXT，在配置中设置：

```yaml
STEAM_TEXT_OUTPUT: accounts.txt
```

TXT 每行格式：

```text
账号名----Steam密码----邮箱
```

启用 TXT 时，账号名、密码和邮箱不能包含 `----`。账号文件包含明文密码，请妥善保管。

## 常见问题

- **收不到验证邮件**：检查垃圾邮件文件夹、邮箱读取权限和服务器设置；等待时间不足时增大 `MAIL_MAX_WAIT`，也可使用 `manual` 模式自行填写链接。
- **Corouter 找不到可用邮箱**：确认网页中的邮箱已启用，且位于配置的租户和分组内；检查是否已在成功账号文件中记录。
- **Corouter 提示邮箱不可用**：在邮箱网页中检查停用、封禁或授权状态，处理后再运行。
- **验证码识别失败**：检查 `CAPTCHA_RUN_KEY` 和平台余额，重新运行后使用本次验证码结果。
- **连接超时**：检查网络与 `STEAM_PROXY`，必要时增大 `STEAM_TIMEOUT`。
- **提示创建状态未知**：先使用 `attempts.jsonl` 中的账号信息尝试登录，确认结果后再重新运行。
- **提示请求过于频繁**：暂停注册，等待一段时间后再运行，可适当增大 `BATCH_DELAY`。
