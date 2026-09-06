"""
Tavily 批量注册
使用 mail_provider 自动生成/接码临时邮箱，批量注册并保存 API Key
"""
import argparse
import os
import sys
import time
from datetime import datetime
from typing import Iterable

from mail_provider import create_mail_provider
from proxy_manager import ProxyManager
from retry_policy import NetworkNodeFailed, ProxyRotationRequired
from signup import (
    create_session,
    create_api_key,
    get_api_keys,
    load_config,
    login_after_verification,
    signup,
    verify_email,
)
from utils import generate_password

# 配置
OUTPUT_FILE = "api_keys.txt"
FAILED_FILE = "failed.txt"
RUN_LOG_FILE = "run.log"

# 注册间隔（秒），避免被限制
REGISTER_INTERVAL = 5
VERIFY_TIMEOUT = 180
VERIFY_POLL_INTERVAL = 5.0
MAX_EMAIL_GENERATE_ATTEMPTS = 30
MAX_DOMAIN_BLOCKED_RETRIES = 10
MAX_REGISTRATIONS_PER_WINDOW = 10
REGISTRATION_WINDOW_SECONDS = 60 * 60


def _extract_key_value(item) -> str:
    if isinstance(item, dict):
        val = (
            item.get("api_key")
            or item.get("key")
            or item.get("apiKey")
            or item.get("token")
            or item.get("secret")
            or ""
        )
        return str(val).strip()
    if isinstance(item, str):
        return item.strip()
    return ""


def _extract_first_api_key(raw_keys) -> str:
    if not raw_keys:
        return ""
    if isinstance(raw_keys, list) and len(raw_keys) > 0:
        return _extract_key_value(raw_keys[0])
    if isinstance(raw_keys, dict):
        return _extract_key_value(raw_keys)
    return ""


def append_run_log(file_path: str, message: str):
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"[{time_str}] {message}\n")


def save_result(file_path: str, email: str, api_key: str, mode: str = "a"):
    key = (api_key or "").strip()
    if key:
        with open(file_path, mode, encoding="utf-8") as f:
            f.write(f"{key}\n")


def save_failed(file_path: str, email: str, error: str, mode: str = "a"):
    with open(file_path, mode, encoding="utf-8") as f:
        f.write(f"{email}----{error}\n")


def load_email_list(file_path: str) -> list[str]:
    """
    支持每行格式：
      - email
      - email----password
      - email----... (只取第一段邮箱，兼容旧 failed.txt / email.txt)
    """
    if not os.path.exists(file_path):
        return []
    out = []
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            email = line.split("----", 1)[0].strip()
            if "@" not in email:
                continue
            out.append(email)
    return out


def try_login_get_key(
    email: str,
    password: str,
    config: dict,
    *,
    proxy: str = None,
    proxy_manager=None,
    debug_init: bool = False,
) -> str:
    """
    尝试登录已注册账户并获取API Key

    Returns:
        API Key 或 None
    """
    print("    尝试登录获取API Key...")
    max_login_attempts = 5
    session = None
    for attempt in range(max_login_attempts):
        session = create_session(proxy=proxy, proxy_manager=proxy_manager)
        try:
            login_result = login_after_verification(
                session, email, password, config
            )
            if login_result.get("success"):
                keys_result = get_api_keys(
                    session, max_retries=10, retry_delay=2, debug_init=debug_init
                )
                if keys_result.get("success") and keys_result.get("keys"):
                    keys = keys_result["keys"]
                    if isinstance(keys, list) and len(keys) > 0:
                        api_key = _extract_key_value(keys[0])
                        if api_key:
                            return api_key
                    elif isinstance(keys, dict):
                        api_key = _extract_key_value(keys)
                        if api_key:
                            return api_key

                print("    登录成功但没有 key，尝试创建...")
                create_result = create_api_key(session, key_name="default")
                if create_result.get("success") and create_result.get("key"):
                    api_key = _extract_key_value(create_result["key"])
                    if api_key:
                        return api_key

                print(f"    第 {attempt+1} 次未获取到 key，重试...")
            else:
                print(
                    f"    登录失败 (attempt {attempt+1}/{max_login_attempts}): {login_result.get('error')}"
                )
        except ProxyRotationRequired:
            raise
        except NetworkNodeFailed:
            raise
        except Exception as e:
            print(f"    登录尝试 {attempt+1} 异常: {e}")
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
                session = None

        time.sleep(2)

    return None


def _verify_email_and_get_key(
    provider,
    email: str,
    password: str,
    config: dict,
    *,
    session=None,
    proxy: str = None,
    proxy_manager=None,
    workflow_state: dict | None = None,
    debug_init: bool = False,
) -> str | None:
    workflow_state = workflow_state if workflow_state is not None else {}
    print("    等待验证邮件...")
    if getattr(provider, "email", None) != email:
        provider.email = email
    link = workflow_state.get("verification_link") or provider.wait_for_verification_link()
    if not link:
        print("    超时: 未收到验证邮件")
        return None

    print(f"    获取到验证链接: {link[:60]}...")
    workflow_state["verification_link"] = link

    close_session = False
    if session is None:
        session = create_session(proxy=proxy, proxy_manager=proxy_manager)
        close_session = True
    try:
        if not workflow_state.get("email_verified"):
            verify_result = verify_email(session, link)
            if not verify_result.get("success"):
                print(f"    邮箱验证失败: {verify_result.get('error')}")
                return None
            workflow_state["email_verified"] = True

        try:
            resp = session.get(
                "https://app.tavily.com/api/auth/login?returnTo=/home",
                allow_redirects=True,
                timeout=30,
            )
            if "app.tavily.com" in (resp.url or ""):
                print("    已进入应用(登录态已建立)")
        except ProxyRotationRequired:
            raise
        except NetworkNodeFailed:
            raise
        except Exception:
            pass

        session_valid = False
        try:
            resp = session.get("https://app.tavily.com/api/auth/me", timeout=30)
            if resp.status_code == 200:
                print(f"    获取用户资料成功：{resp.json()}")
                session_valid = True
            else:
                print(
                    f"    Session 无效 (status={resp.status_code})，需要重新登录"
                )
        except ProxyRotationRequired:
            raise
        except NetworkNodeFailed:
            raise
        except Exception as e:
            print(f"    检查 session 失败: {e}")

        if not session_valid:
            print("    验证完成但未建立登录态，尝试登录...")
            return try_login_get_key(
                email,
                password,
                config,
                proxy=proxy,
                proxy_manager=proxy_manager,
                debug_init=debug_init,
            )

        keys_result = get_api_keys(
            session, max_retries=10, retry_delay=2, debug_init=debug_init
        )
        if keys_result.get("success") and keys_result.get("keys"):
            api_key = _extract_first_api_key(keys_result["keys"])
            if api_key:
                return api_key

        try:
            create_result = create_api_key(session, key_name="default")
            if create_result.get("success") and create_result.get("key"):
                api_key = _extract_first_api_key(create_result["key"])
                if api_key:
                    return api_key
        except ProxyRotationRequired:
            raise
        except NetworkNodeFailed:
            raise
        except Exception:
            pass

        print("    已登录但未获取到 key，尝试重新登录...")
        return try_login_get_key(
            email,
            password,
            config,
            proxy=proxy,
            proxy_manager=proxy_manager,
            debug_init=debug_init,
        )
    finally:
        if close_session:
            try:
                session.close()
            except Exception:
                pass


def batch_signup(
    *,
    count: int = 1,
    emails: Iterable[str] | None = None,
    output_file: str = OUTPUT_FILE,
    failed_file: str = FAILED_FILE,
    run_log_file: str = RUN_LOG_FILE,
    password: str | None = None,
    interval: int = REGISTER_INTERVAL,
    verify_timeout: int = VERIFY_TIMEOUT,
    verify_poll_interval: float = VERIFY_POLL_INTERVAL,
    max_registrations_per_window: int = MAX_REGISTRATIONS_PER_WINDOW,
    registration_window_seconds: int = REGISTRATION_WINDOW_SECONDS,
    proxy_api_url: str | None = None,
    debug_init: bool = False,
):
    """
    批量注册 Tavily 账号
    """
    config = load_config()

    if not proxy_api_url:
        proxy_api_url = config.get("PROXY_API_URL") or os.getenv("PROXY_API_URL")

    proxy_mgr = ProxyManager(
        proxy_api_url=proxy_api_url,
        max_attempts_per_ip=10,
        max_network_failures_per_ip=3,
        poll_interval=30.0,
    )

    email_list = list(emails) if emails is not None else []
    if emails is None:
        print("模式: 自动生成邮箱")
        print(f"目标数量: {count}")
    else:
        if not email_list:
            print("邮箱列表为空")
            return
        print("模式: 输入邮箱列表")
        print(f"共加载 {len(email_list)} 个邮箱")

    if password:
        print(f"固定密码: {password}")
    else:
        print("密码模式: 为每个账号随机生成高强度合规密码")
    print(f"注册间隔: {interval} 秒")
    if proxy_api_url:
        print(f"代理 API: {proxy_api_url}")
        print(
            "代理模式: 单 IP 最多 10 次注册尝试，累计 3 次网络失败立即轮换；"
            "达到任一阈值后检测新 IP，并继续当前账号"
        )
    else:
        print(
            f"时间窗口限制: 单 IP 每 {registration_window_seconds/60:.1f} 分钟最多注册 {max_registrations_per_window} 个"
        )
    print()

    success_count = 0
    failed_count = 0
    skipped_count = 0
    window_completed = 0

    registered_emails = set()
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if "----" in line_str:
                    email = line_str.split("----")[0].strip()
                    registered_emails.add(email)
                elif "@" in line_str:
                    registered_emails.add(line_str)
        if registered_emails:
            print(f"已有 {len(registered_emails)} 个邮箱注册成功，将跳过")
            print()

    start_time = datetime.now()
    total = len(email_list) if emails is not None else count

    def maybe_wait_for_rate_limit(next_index: int):
        nonlocal window_completed
        if proxy_mgr.proxy_api_url:
            return  # 代理模式由 ProxyManager 统一调度 IP 轮换与 8 分钟等待
        if max_registrations_per_window <= 0 or registration_window_seconds <= 0:
            return
        if next_index <= 0:
            return
        if (
            window_completed > 0
            and window_completed % max_registrations_per_window == 0
        ):
            wait_minutes = registration_window_seconds / 60
            wake_at = datetime.now().timestamp() + registration_window_seconds
            wake_at_str = datetime.fromtimestamp(wake_at).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            print()
            print("=" * 60)
            print(f"达到 Tavily 单 IP 限制：已完成 {window_completed} 个注册")
            print(f"等待 {wait_minutes:.1f} 分钟后继续...")
            print(f"预计恢复时间: {wake_at_str}")
            print("=" * 60)
            append_run_log(
                run_log_file,
                f"达到限速阈值，已完成 {window_completed} 个注册，暂停 {registration_window_seconds} 秒，预计 {wake_at_str} 恢复",
            )
            time.sleep(registration_window_seconds)
            append_run_log(run_log_file, "限速等待结束，继续注册")

    for i in range(total):
        maybe_wait_for_rate_limit(i)
        current_proxy = proxy_mgr.get_proxy()
        item_password = password if password else generate_password(14)
        completed_this_item = False

        # 邮箱、密码和验证链接属于当前账号。换 IP 时只重建 Tavily Session。
        provider = None
        email = None
        if emails is None:
            while True:
                try:
                    provider = create_mail_provider()
                    email = provider.acquire_email()
                except (ValueError, RuntimeError) as e:
                    err = f"email_generate_failed: {e}"
                    print(f"\n{'='*60}")
                    print(f"[{i+1}/{total}] (生成邮箱失败)")
                    print(f"{'='*60}")
                    print(err)
                    save_failed(failed_file, "N/A", err)
                    failed_count += 1
                    proxy_mgr.record_attempt()
                    provider = None
                    break
                if email not in registered_emails:
                    break
                print(f"跳过: 已注册邮箱 {email}，重新生成")
                skipped_count += 1
                proxy_mgr.record_attempt()
                try:
                    provider.close()
                except Exception:
                    pass
                provider = None
        else:
            try:
                provider = create_mail_provider()
                email = email_list[i]
                provider.email = email
            except Exception as e:
                save_failed(failed_file, "N/A", f"email_provider_failed: {e}")
                failed_count += 1
                proxy_mgr.record_attempt()

        if provider is not None and email in registered_emails:
            print("跳过: 已注册")
            skipped_count += 1
            proxy_mgr.record_attempt()
            try:
                provider.close()
            except Exception:
                pass
            provider = None

        if provider is not None:
            print(f"\n{'='*60}")
            print(f"[{i+1}/{total}] {email}")
            print(f"密码: {item_password}")
            print(f"{'='*60}")
            append_run_log(run_log_file, f"开始处理 [{i+1}/{total}] {email}")

            workflow_state = {
                "verification_link": None,
                "email_verified": False,
            }
            signup_completed = False
            signup_session = None
            terminal_error = None

            while True:
                try:
                    if not signup_completed:
                        result = signup(
                            email=email,
                            password=item_password,
                            config=config,
                            max_retries=3,
                            mail_api_base=None,
                            mail_jwt=None,
                            keep_session=True,
                            proxy=current_proxy,
                            proxy_manager=proxy_mgr,
                            debug_init=debug_init,
                        )
                        signup_session = result.get("session")

                        if result.get("success"):
                            signup_completed = True
                        else:
                            error = result.get("error", "unknown")
                            print(f"\n注册失败: {error}")
                            # 请求已成功但邮箱已存在时，转入验证/登录恢复流程。
                            if isinstance(error, str) and "邮箱已注册" in error:
                                signup_completed = True
                            elif isinstance(error, str) and "ip-signup-blocked" in error:
                                if not proxy_mgr.proxy_api_url:
                                    terminal_error = error
                                    break
                                proxy_mgr.record_attempt()
                                proxy_mgr.force_rotate("Tavily 返回 ip-signup-blocked")
                                current_proxy = proxy_mgr.get_proxy()
                                continue
                            else:
                                api_key = try_login_get_key(
                                    email,
                                    item_password,
                                    config,
                                    proxy=current_proxy,
                                    proxy_manager=proxy_mgr,
                                    debug_init=debug_init,
                                )
                                if api_key:
                                    save_result(output_file, email, api_key)
                                    print(f"\n通过登录获取成功! API Key: {api_key[:15]}...{api_key[-4:]}")
                                    append_run_log(run_log_file, f"登录补救成功 {email}")
                                    success_count += 1
                                    completed_this_item = True
                                else:
                                    terminal_error = error
                                break

                    if signup_completed and result.get("api_keys"):
                        api_key = _extract_first_api_key(result.get("api_keys"))
                        if api_key:
                            save_result(output_file, email, api_key)
                            print(f"\n成功! API Key: {api_key[:15]}...{api_key[-4:]}")
                            append_run_log(run_log_file, f"注册成功 {email}")
                            success_count += 1
                            completed_this_item = True
                            break

                    if signup_completed:
                        api_key = _verify_email_and_get_key(
                            provider,
                            email,
                            item_password,
                            config,
                            session=signup_session,
                            proxy=current_proxy,
                            proxy_manager=proxy_mgr,
                            workflow_state=workflow_state,
                            debug_init=debug_init,
                        )
                        if api_key:
                            save_result(output_file, email, api_key)
                            print(f"\n成功! API Key: {api_key[:15]}...{api_key[-4:]}")
                            append_run_log(run_log_file, f"注册成功 {email}")
                            success_count += 1
                            completed_this_item = True
                        else:
                            terminal_error = "no_api_key_after_verify"
                        break

                except ProxyRotationRequired as e:
                    # 只关闭受影响的 Session；邮箱、密码、验证链接和流程状态保留。
                    if signup_session is not None:
                        try:
                            signup_session.close()
                        except Exception:
                            pass
                        signup_session = None
                    proxy_mgr.record_attempt()
                    print(f"\n当前 IP 需要轮换: {e.reason}")
                    current_proxy = proxy_mgr.get_proxy()
                    print(f"使用新 IP 继续当前账号: {email}")
                    continue
                except NetworkNodeFailed as e:
                    terminal_error = str(e)
                    break
                except Exception as e:
                    terminal_error = str(e)
                    print(f"\n异常: {e}")
                    break
                finally:
                    if signup_session is not None and (completed_this_item or terminal_error):
                        try:
                            signup_session.close()
                        except Exception:
                            pass
                        signup_session = None

            if terminal_error and not completed_this_item:
                save_failed(failed_file, email, terminal_error)
                print(f"\n最终失败: {terminal_error}")
                append_run_log(run_log_file, f"注册失败 {email} - {terminal_error}")
                failed_count += 1
            proxy_mgr.record_attempt()

        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass

        if completed_this_item:
            window_completed += 1

        if i < total - 1:
            print(f"\n等待 {interval} 秒...")
            time.sleep(interval)

    end_time = datetime.now()
    duration = end_time - start_time

    print()
    print("=" * 60)
    print("批量注册完成")
    print("=" * 60)
    print(f"总数: {total}")
    print(f"成功: {success_count}")
    print(f"失败: {failed_count}")
    print(f"跳过: {skipped_count}")
    print(f"耗时: {duration}")
    print()
    print(f"API Keys 已保存到: {output_file}")
    append_run_log(
        run_log_file,
        f"批量注册结束: 总数={total}, 成功={success_count}, 失败={failed_count}, 跳过={skipped_count}, 耗时={duration}",
    )
    if failed_count > 0:
        print(f"失败记录已保存到: {failed_file}")


def retry_failed(
    *,
    failed_file: str = FAILED_FILE,
    output_file: str = OUTPUT_FILE,
    run_log_file: str = RUN_LOG_FILE,
    password: str | None = None,
    interval: int = REGISTER_INTERVAL,
    verify_timeout: int = VERIFY_TIMEOUT,
    verify_poll_interval: float = VERIFY_POLL_INTERVAL,
    proxy_api_url: str | None = None,
    debug_init: bool = False,
):
    """
    重试失败的注册
    """
    print("=" * 60)
    print("重试失败的注册")
    print("=" * 60)

    if not os.path.exists(failed_file):
        print(f"没有失败记录: {failed_file}")
        return

    emails = load_email_list(failed_file)
    if not emails:
        print("没有需要重试的记录")
        return

    print(f"找到 {len(emails)} 条失败记录")

    open(failed_file, "w").close()

    batch_signup(
        emails=emails,
        output_file=output_file,
        failed_file=failed_file,
        run_log_file=run_log_file,
        password=password,
        interval=interval,
        verify_timeout=verify_timeout,
        verify_poll_interval=verify_poll_interval,
        proxy_api_url=proxy_api_url,
        debug_init=debug_init,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tavily 批量注册 (mail_provider)")
    parser.add_argument("--retry", action="store_true", help="重试失败的注册")
    parser.add_argument(
        "--count",
        "-n",
        type=int,
        default=None,
        help="注册数量 (未指定时交互提示，默认 10)",
    )
    parser.add_argument(
        "--input",
        "-i",
        default=None,
        help="邮箱列表文件 (每行一个邮箱，或 email----... 只取邮箱)",
    )
    parser.add_argument("--output", "-o", default=OUTPUT_FILE, help="输出文件路径")
    parser.add_argument("--failed", default=FAILED_FILE, help="失败记录文件路径")
    parser.add_argument("--run-log", default=RUN_LOG_FILE, help="运行日志文件路径")
    parser.add_argument("--password", default=None, help="注册/登录密码 (未指定时为每个账号随机生成符合合规要求的密码)")
    parser.add_argument(
        "--interval", type=int, default=REGISTER_INTERVAL, help="注册间隔 (秒)"
    )
    parser.add_argument(
        "--proxy-api-url",
        default=None,
        help="代理提取 API 链接 (单 IP 累计 3 次网络失败或 10 次注册尝试后轮换)",
    )

    parser.add_argument(
        "--max-per-window",
        type=int,
        default=MAX_REGISTRATIONS_PER_WINDOW,
        help="单个时间窗口内最多注册多少个账号 (未启用代理 API 时使用)",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=REGISTRATION_WINDOW_SECONDS,
        help="达到窗口上限后等待多久再继续（秒）",
    )
    parser.add_argument("--verify-timeout", type=int, default=VERIFY_TIMEOUT)
    parser.add_argument("--verify-interval", type=float, default=VERIFY_POLL_INTERVAL)
    parser.add_argument(
        "--debug-init",
        action="store_true",
        help="打印首次登录初始化接口调用信息（/api/account 等）",
    )

    args = parser.parse_args()

    if args.retry:
        retry_failed(
            failed_file=args.failed,
            output_file=args.output,
            run_log_file=args.run_log,
            password=args.password,
            interval=args.interval,
            verify_timeout=args.verify_timeout,
            verify_poll_interval=args.verify_interval,
            proxy_api_url=args.proxy_api_url,
            debug_init=args.debug_init,
        )
    else:
        emails = load_email_list(args.input) if args.input else None
        count = args.count
        if emails is None and count is None:
            if sys.stdin.isatty():
                try:
                    user_input = input("👉 请输入需要注册的数量 [默认 10]: ").strip()
                    count = int(user_input) if user_input else 10
                except Exception:
                    count = 10
            else:
                count = 10
        elif count is None:
            count = len(emails)

        batch_signup(
            count=count,
            emails=emails,
            output_file=args.output,
            failed_file=args.failed,
            run_log_file=args.run_log,
            password=args.password,
            interval=args.interval,
            verify_timeout=args.verify_timeout,
            verify_poll_interval=args.verify_interval,
            max_registrations_per_window=args.max_per_window,
            registration_window_seconds=args.window_seconds,
            proxy_api_url=args.proxy_api_url,
            debug_init=args.debug_init,
        )
