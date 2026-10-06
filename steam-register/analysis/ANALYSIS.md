# Steam 注册机静态逆向与协议重建

分析日期：2026-10-06。附件没有被运行；本次采用 PE 资源解析、Zstandard 解包、Nuitka 常量反序列化，以及 Steam 当前官方页面和 JavaScript 对照。使用方式见 [README](../README.md)，带偏移的脱敏证据见 [sample_report.json](sample_report.json)。

## 样本与封装

| 项目 | 结果 |
| --- | --- |
| 样本 | `8.23steam注册机（辅助格式导入兼容）.exe.bin` |
| SHA-256 | `c1724644ccfe6ce5598df9c6cbb3aa537727bee916da409962aba091119cda4c` |
| 文件长度 | 29,343,744 字节 |
| 外层格式 | PE32+，Windows GUI，x86-64 |
| 封装 | Nuitka onefile，RCDATA `10/27/0`，头部 `KAY` |
| 压缩载荷 | 文件偏移 `0x26bd8`，29,183,056 字节 |
| 解压后 | 103,235,661 字节，683 个文件 |
| 主程序 | `main.dll`，18,105,344 字节，原生机器码 |
| Python / GUI | `python310.dll`、PySide2、Qt5 |
| 网络库 | `curl_cffi`、`requests`，含 curl-impersonate DLL |

外层资源去掉 `KAY` 后读取完整 Zstandard 帧；帧后还有 9 字节尾部数据，不能继续作为下一帧解压。Windows 文件条目是 UTF-16LE 零结尾文件名、8 字节小端长度、文件内容，最后以空文件名结束。这与 [Nuitka onefile 引导代码](https://github.com/Nuitka/Nuitka/blob/2.7.16/nuitka/build/static_src/OnefileBootstrap.c) 的 Windows 分支对应。

`main.dll` 中 RCDATA `10/3/0` 位于 `0xbd7fd8`，长度 5,670,577 字节。其头部包含 CRC32 和长度，随后按“模块名 + 长度 + 常量数据”保存。本次完整解析出 172 个模块条目，并通过长度和 CRC 校验。

| 模块 | 常量数据偏移（main.dll 文件偏移） | 说明 |
| --- | --- | --- |
| `.bytecode` | `0xbd7fee` | 4,697,388 字节，其他打包 Python 字节码 |
| `__main__` | `0x10580af` | 7,432 字节，424 项常量 |
| `getmail` | `0x10b582e` | 2,671 字节，140 项常量 |
| `ui` | `0x10fd57a` | 2,362 字节，界面模块常量 |

主业务已编译为机器码，无法按 PyInstaller `.pyc` 直接完整反编译。本次恢复的是接口地址、字段、字面量请求头、函数/局部变量名称和模块边界；完整源码和所有运行分支没有恢复。常量解析参考 [Nuitka 常量加载实现](https://github.com/Nuitka/Nuitka/blob/2.7.16/nuitka/build/static_src/HelpersConstantsBlob.c)。

## 原程序接口证据

以下偏移是序列化常量记录起点，可能包含 tuple/string 标签，不能直接视作 URL 正文地址。

| 原程序符号 | 接口 | 索引 | main.dll 偏移 |
| --- | --- | --- | --- |
| `getrefreshcaptcha` | `POST https://store.steampowered.com/join/refreshcaptcha` | `__main__[64]` | `0x1058628` |
| `sendmail` | `POST https://store.steampowered.com/join/ajaxverifyemail` | `__main__[79]` | `0x1058977` |
| `streg` | `POST https://store.steampowered.com/join/createaccount/` | `__main__[90]` | `0x1058a2b` |
| `rec.gettoken` | `POST https://api.captcha-run.com/v2/tasks` | `__main__[132]` | `0x1058bec` |
| 邮箱 OAuth | `POST https://login.microsoftonline.com/consumers/oauth2/v2.0/token` | `getmail[10]` | `0x10b58ad` |
| Graph 邮件 | `GET https://graph.microsoft.com/v1.0/me/messages?$top=1&$orderby=receivedDateTime desc` | `getmail[78]` | `0x10b5c98` |

Steam 字面量请求头包含 `Origin`、`Referer`、`X-Requested-With=XMLHttpRequest`、`X-Prototype-Version=1.7`，以及表单 `Content-Type`。字面量 User-Agent 为 Windows Chrome 146，会话参数含 `impersonate=chrome`。新实现继续使用 `curl_cffi` Chrome 会话，由库维护浏览器请求头与 TLS 指纹。

### Steam 表单

| 阶段 | 恢复的字段与字面量 |
| --- | --- |
| 刷新验证码 | `__main__[61]` 字典 `{"count":1,"hcaptcha":1}`；响应字段 `gid`、`sitekey` |
| 发送邮件 | `email`、`captchagid`、`captcha_text`、`elang`（相邻整数为 6）、`init_id`、`guest` |
| 创建账户 | `accountname`、`password`、`count`、`lt`、`creation_sessionid`、`embedded_appid`；成功标志 `bSuccess` |

`sendmail` 局部变量中可见 `s`、`gid`、`token`、`email`、`headers`、`data`、`response`。`init_id` 附近有 `round` 和大整数常量，但不足以完整确认原始计算式。新实现采用官方页面实际下发的 `init_id`。

邮箱模块包含 `https://store.steampowered.com/account/newaccountverification?stoken=<十六进制>&creationid=<数字>` 匹配模式，也有 `&amp;creationid=` 版本。主注册函数局部变量含 `href`、`creationid`、`uanme`、`upwd`。新实现先解码邮件，再严格匹配当前创建会话；不接受其他域名、重复 creationid 或其他会话的链接。

### Captcha.run

原模块包含 `HCaptchaSteam`、`captchaType`、`siteReferer`、`siteKey`、`taskId`、`Success`、`Fail`、`response`、`captcha_key`，对应：

```text
POST https://api.captcha-run.com/v2/tasks
Authorization: Bearer <用户自己的 Key>
Content-Type: application/json
{"captchaType":"HCaptchaSteam","siteReferer":"https://store.steampowered.com/join/","siteKey":"当前 sitekey"}

GET https://api.captcha-run.com/v2/tasks/{taskId}
status=Success → response.captcha_key
```

样本还包含另一个 HTTP 任务服务、Captchaly 地址及内置第三方凭据。实现只接入 Captcha.run 和手动 token，没有复制内置 Key 或访问其他打码服务；证据工具也不导出全量常量或内置凭据。

当前 [Captcha.run 官网](https://captcha.run/) 公布 `/v2/tasks`、Bearer 认证、taskId 轮询和 `Working/Success/Fail` 状态，使用 `api.captcha.run` 域名。新实现默认采用该域名，旧域名可配置。Steam 专用结果字段 `response.captcha_key` 来自附件，尚未用真实 Key 验证付费任务。

### 邮箱读取

`getmail` 中可见 `pyimap`、`pypop`、`wrgraph`：IMAP 有密码与 XOAUTH2、INBOX/Junk、RFC822 和正文解析；POP3 有用户名密码登录；Graph 先用 refresh token 换 access token，再读取最新邮件（附件 `$top=1`）。

新实现只提供 TLS 连接，Graph 扫描最近 20 封；IMAP 用 `BODY.PEEK[]` 保持未读。Graph 参数依据 [Microsoft Graph 邮件查询说明](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)。IMAP OAuth scope 按 [Microsoft 官方说明](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth) 使用 `https://outlook.office.com/IMAP.AccessAsUser.All`，不把 Graph token 用于 IMAP。

辅助导入是新实现明确提供的兼容功能：接受标准四段和后两段反序格式，通过 UUID 判断 client_id，支持 `----`、`|`、Tab。它不是原 GUI 全部导入分支的完整还原。

## 与当前官方前端对照

来源：[Steam 注册页](https://store.steampowered.com/join/)、实际引用的 [joinsteam.js](https://store.fastly.steamstatic.com/public/shared/javascript/joinsteam.js?v=JUEX1SrWVPHM&l=english&_cdn=fastly)。本次解压后脚本 SHA-256：`854315701d704a065b7afcdae56d9a5ebe99bc35fbc330ed9cc96ed24aa23a20`。

| 当前前端行为 | 新实现 |
| --- | --- |
| 页面 `init_id`、`lt`、`g_embeddedAppID`、`g_bGuest` | GET 页面并读取实际值 |
| 发信成功响应的 `sessionid` | 后续 creationid / creation_sessionid；用字符串保留精度 |
| `ajaxcheckemailverified` | 点击链接后等待确认；27/29/42 立即失败 |
| `checkavail/`、`checkpasswordavail/` | 最终创建前检查账户名和密码 |
| `createaccount/` | 原字段 + 页面 guest；仅明确 bSuccess 才记录成功 |
| 刷新验证码 type=1/2/3 | 图片、reCAPTCHA 手动输入；hCaptcha 可调用 HCaptchaSteam |

邮箱确认轮询和两个可用性检查接口来自当前官方 JavaScript，在主模块已提取的端点常量中未找到。创建后 JWT 自动登录、消费账户设置、监护人同意和其他客户端分支未纳入本次实现。

## 验证与复现

2026-10-06 在线 `--probe`：`GET /join/` 和 `POST /join/refreshcaptcha` 均为 HTTP 200，取得实际 init_id/gid；验证码 type=3，sitekey 为 `e18a349a-46c2-46a0-87a8-74be79345c92`，与附件常量一致，`s` 为空。未发送注册邮件、创建打码任务或创建账户。

离线测试覆盖表单顺序、验证码刷新、验证过期/超时、会话匹配、跳转限制、创建结果未知、导入、MIME、Graph/IMAP/POP3 和凭据输出。实账号注册仍需配置邮箱权限与用户自己的打码 Key，并检查实际服务返回。

重新生成静态证据：

```bash
cd steam-register
uv run --group analysis python tools/analyze_sample.py '/path/to/8.23steam注册机（辅助格式导入兼容）.exe.bin'
```

工具在内存中解析载荷和常量，不将 DLL 写入项目，不加载字节码或运行附件。解包器针对本附件格式，并非所有 Nuitka 版本的通用反编译器。
