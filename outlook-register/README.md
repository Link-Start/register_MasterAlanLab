# Microsoft / Outlook 注册协议重建

这个项目把当前目录中 `微软综合工具1.8.4.zip` 逆向得到的注册链路整理成一套分层 Python 实现，包管理和启动方式参考上一级 `register` 项目，统一使用 `uv`。逆向记录见 [`逆向分析报告`](../../test/extracted2/ANALYSIS.md)。

实现目标不是重新封装一个通用 SDK，而是保留原工具 `Task.ou_reg.oureg` 的请求顺序、字段名称和服务端地址：

```text
GET  https://signup.live.com/?lic=1
POST https://signup.live.com/API/EvaluateExperimentAssignments
GET  https://df.cfp.microsoft.com/Clear.HTML
POST https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/api/v1.0/risk/initialize
POST https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/api/v1.0/risk/verify
POST https://signup.live.com/API/CheckAvailableSigninNames?lic=1
POST https://signup.live.com/API/CreateAccount?lic=1
```

## 环境要求

- Python `>=3.10`
- [uv](https://docs.astral.sh/uv/)
- 可访问 Microsoft 注册服务的网络
- `curl-cffi` 用于复现原程序的 Chrome TLS/HTTP 指纹
- PxCaptcha2 token，或 Captcha.run API Key

## 安装

```bash
cd microsoft-register
uv sync
cp .env.example .env
```

运行测试：

```bash
uv run pytest
```

## 配置

`.env` 中的主要字段：

```dotenv
MS_REGISTER_PROXY=http://user:pass@host:port
MS_REGISTER_COUNTRY=JP
MS_REGISTER_MARKET=ja-JP
MS_REGISTER_TIMEZONE=540
MS_REGISTER_TIMEOUT=30

CAPTCHA_RUN_KEY=YOUR_CAPTCHA_RUN_KEY
CAPTCHA_RUN_BASE_URL=https://api.captcha-run.com/v2/tasks
CAPTCHA_RUN_POLL_INTERVAL=3
CAPTCHA_RUN_MAX_WAIT=60
MS_REGISTER_OUTPUT=accounts.txt
```

原程序使用 `PxCaptcha2`，创建任务的字段为：

```json
{
  "captchaType": "PxCaptcha2",
  "uaid": "随机 32 位十六进制",
  "timezone": 480,
  "country": "US",
  "host": "代理主机",
  "port": "代理端口",
  "login": "代理用户名",
  "password": "代理密码"
}
```

## 启动

先只抓取注册页、实验分配和用户名检测，查看请求轨迹：

```bash
uv run python cli.py \
  --member-name example@outlook.com \
  --password 'Password123!' \
  --first-name Alan \
  --last-name User \
  --json
```

提供已经取得的 `PxCaptcha2` token，并执行风险验证和创建请求：

```bash
uv run python cli.py \
  --member-name example@outlook.com \
  --password 'Password123!' \
  --captcha-token 'CAPTCHA_TOKEN' \
  --execute \
  --json
```

配置 `CAPTCHA_RUN_KEY` 后，程序会自动调用：

```text
POST https://api.captcha-run.com/v2/tasks
GET  https://api.captcha-run.com/v2/tasks/{taskId}?captchaType=PxCaptcha2
```

## 目录结构

```text
outlook-register/
├── README.md
├── pyproject.toml
├── .env.example
├── cli.py              # uv 启动入口和参数
├── config.py           # 环境变量配置
├── models.py           # 注册字段、页面上下文、结果
├── http_client.py      # curl_cffi Chrome 会话
├── fingerprint.py      # uaid、Canvas PNG、WebGL 和 urlDfp
├── captcha.py          # cap.cap.cappx2 的 PxCaptcha2 客户端
├── risk.py             # Clear.HTML、risk initialize/verify
├── signup.py           # oureg 主注册协议
├── output.py           # TXT 结果输出
└── tests/
    ├── test_protocol.py
    └── test_signup_sequence.py
```

分层只对应原始模块边界，没有再引入额外的 repository、service container 或插件体系：

- `signup.py` 保持原 `oureg` 的注册顺序和 payload。
- `risk.py` 对应 `login_initialize`、`login_verify`。
- `captcha.py` 对应 `cappx2create`、`cappx2gettoken`。
- `fingerprint.py` 对应 `fpgen`。
- `http_client.py` 对应原程序的 `curl_cffi.requests.Session(impersonate="chrome146")`。

## 协议细节

### 1. 注册页初始化

程序随机生成 `uaid`，使用 Edge/Chrome 请求头访问 `signup.live.com/?lic=1`，从 HTML 中提取：

- `apiCanary`
- `sUnauthSessionID`
- `urlDfp`
- `sHipFid`
- `hpgid`
- `iScenarioId`
- `iUiFlavor`
- `sPrefSMSCountry`
- `txnId`、`rid`、`ticks`、`authKey`、`cid`

### 2. 实验分配

请求体保留原程序常量：

```json
{
  "clientExperiments": [
    {
      "parallax": "enablesisufeedback",
      "control": "enablesisufeedback_control",
      "treatments": ["enablesisufeedback_treatment"]
    },
    {
      "parallax": "addprivatebrowsingtexttofabricfooter",
      "control": "addprivatebrowsingtexttofabricfooter_control",
      "treatments": ["addprivatebrowsingtexttofabricfooter_treatment"]
    }
  ]
}
```

### 3. 风险和指纹

`fingerprint.py` 保留原程序的字段形状：

- `bua`、`os=Win32`、`lproc`
- 屏幕分辨率、字体列表 MD5、Canvas PNG MD5
- WebGL vendor/renderer
- `tz=480`、`dst=0`、`tzo=480`
- `data:image/png;base64,...` Canvas 数据

风险接口保留原程序字段名：`msaCreateSignature`、`memberName`、`siteId`、`uiFlavor`、`birthdate`、`firstName`、`lastName`、`countryCode`、`verificationCode`、`deviceDetails`、`challengeSolution`、`challengeType`、`px3`、`pxde`、`pxvid`。

### 4. 用户名检查

```json
{
  "includeSuggestions": true,
  "signInName": "example@outlook.com",
  "uiflvr": 1001,
  "scid": 100118,
  "uaid": "...",
  "hpgid": 200225
}
```

### 5. 创建账户

`CreateAccount` 使用原程序恢复出的字段：

- `BirthDate`
- `Country`
- `FirstName`
- `LastName`
- `MemberName`
- `Password`
- `ReturnUrl`
- `SignupReturnUrl`
- `SuggestedAccountType=EASI`
- `SiteId=00000000487A244A`
- `ContinuationToken`
- `MemberNameChangeCount`
- `MemberNameAvailableCount`
- `MemberNameUnavailableCount`
- `IsUserConsentedToChinaPIPL`
- `RiskAssessmentDetails`
- `RepMapRequestIdentifierDetails`
- `arkoseBlob`

最终成功结果写入 `accounts.txt`，每行格式为 `邮箱----密码----client_id----refresh_token`，默认 client_id 为 `9e5f94bc-e8a4-4e73-b8be-63364c29d753`。refresh token 从响应递归提取；注册接口未返回 refresh token 时最后一段为空。失败、用户名检测失败和 dry-run 结果不会写入该文件。`--json` 仍然只用于终端调试输出。

## 与逆向产物的对应关系

- `extracted2/recovered/Task/ou_reg/oureg.py` → `signup.py`
- `extracted2/recovered/Task/ou_reg/fpgen.py` → `fingerprint.py`
- `extracted2/recovered/cap/cap.py` → `captcha.py`
- `extracted2/full_targeted/module_constants/Task_ou_reg_oureg_constants.txt` → `signup.py` 的 URL、字段和请求头
- `extracted2/full_targeted/module_constants/cap_cap_constants.txt` → `captcha.py` 的 PxCaptcha2 API

## 输出和调试

默认结果文件：`accounts.txt`。该文件只追加最终注册成功的账号。

`--json` 会打印完整结构化结果；请求过程保存在内存中的 `trace` 字段，便于对照原工具的任务日志和 HTTP 顺序。

注册接口经常随 Microsoft 服务端调整，若响应出现新的 `code`、字段或跳转地址，直接查看 JSON 结果中的 `response` 和 `trace` 即可继续对照逆向常量修改。
