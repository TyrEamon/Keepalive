#!/usr/bin/env python3
"""
GoRouter 多账号每日签到（seleniumbase UC 模式 + 代理 + Turnstile 自动绕过）

核心流程：
  1. 快速路径：纯 requests 签到（无 Turnstile 问题时）
  2. 如果 API 返回 Turnstile token 为空 → 启动 seleniumbase UC 浏览器
     2a. 如果配置了 NODE_LINK 代理，自动挂载 sing-box 代理出口
     2b. 浏览器自动点击 Turnstile 复选框（uc_gui_click_captcha）
     2c. 提取 cf-turnstile-response token
     2d. 携带 token 重试签到 API

环境变量：
  GOROUTER_ACCOUNTS_JSON  — 多账号配置（必填）
  IS_PROXY                — "true" 启用代理（默认 false）
  PROXY_SERVER            — 代理地址（默认 http://127.0.0.1:1080）
  CHECKIN_INTERVAL_SECONDS — 每个账号间隔（默认 1.5）
  TG_BOT_TOKEN / TG_CHAT_ID — Telegram 通知（可选）
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from seleniumbase import SB

BASE_URL = "https://gorouter.app"

# GoRouter / New-API 配额换算
QUOTA_PER_USD = 500_000

BJT = timezone(timedelta(hours=8))

REQUEST_TIMEOUT = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("gorouter-checkin-sb")


# ============================================================
# 1. 辅助函数
# ============================================================

def quota_to_usd(quota: Any) -> float:
    try:
        return round(float(quota or 0) / QUOTA_PER_USD, 2)
    except (TypeError, ValueError):
        return 0.0


def today_text() -> str:
    now = datetime.now(BJT)
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


def mask_name(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return value[0] + "***"
    return value[:4] + "*****"


def normalize_session_value(value: Any) -> str:
    session_value = str(value or "").strip()
    if session_value.lower().startswith("session="):
        session_value = session_value.split("=", 1)[1].strip()
    return session_value


def load_accounts() -> list[dict[str, Any]]:
    raw = os.getenv("GOROUTER_ACCOUNTS_JSON", "").strip()
    if not raw:
        raise ValueError("未配置 GOROUTER_ACCOUNTS_JSON")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "GOROUTER_ACCOUNTS_JSON 不是有效 JSON："
            f"第 {exc.lineno} 行第 {exc.colno} 列"
        ) from exc

    if not isinstance(data, list):
        raise ValueError("GOROUTER_ACCOUNTS_JSON 顶层必须是数组 []")

    accounts: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            log.warning("跳过第 %d 项：账号配置必须是对象", index)
            continue
        if item.get("enabled", True) is False:
            log.info("跳过已禁用账号：%s", item.get("name") or f"账号{index}")
            continue
        account = dict(item)
        account["name"] = str(item.get("name") or f"账号{index}").strip()
        account["user_id"] = str(item.get("user_id") or "").strip()
        account["session"] = normalize_session_value(item.get("session"))
        account["token"] = str(item.get("token") or "").strip()

        if not account["user_id"]:
            log.warning("跳过 %s：缺少 user_id", account["name"])
            continue
        if not account["session"] and not account["token"]:
            log.warning("跳过 %s：session 和 token 至少填写一个", account["name"])
            continue
        accounts.append(account)

    if not accounts:
        raise ValueError("没有可运行的账号，请检查 enabled、user_id 和 session")
    return accounts


def _turnstile_blocked(message: str) -> bool:
    """判断 API 返回的消息是否指示 Turnstile token 缺失。"""
    msg_lower = message.lower()
    return "turnstile" in msg_lower and (
        "为空" in message or "empty" in msg_lower or "token" in msg_lower
    )


def _proxy_config() -> tuple[bool, str]:
    """读取代理配置，并检查代理是否可达。"""
    is_proxy = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy = os.environ.get("PROXY_SERVER", "").strip() or "socks5://127.0.0.1:1080"

    if is_proxy:
        # 检查代理端口是否开放（SOCKS5 不能用 requests 直测）
        try:
            import socket
            parsed = proxy.replace("socks5://", "").replace("http://", "")
            host, _, port = parsed.partition(":")
            sock = socket.create_connection((host, int(port or 1080)), timeout=5)
            sock.close()
        except Exception:
            log.warning("代理 %s 不可达，回退到直连模式", proxy)
            return False, proxy

    return is_proxy, proxy


# ============================================================
# 2. API 签到
# ============================================================

def _create_session_for_account(account: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": f"{BASE_URL}/dashboard/overview",
        "New-Api-User": account["user_id"],
        "Origin": BASE_URL,
    }
    if account.get("token"):
        headers["Authorization"] = f"Bearer {account['token']}"
    session.headers.update(headers)
    if account.get("session"):
        session.cookies.set(
            "session", account["session"],
            domain="gorouter.app", path="/", secure=True,
        )
    return session


def call_checkin_api(account: dict[str, Any], turnstile_token: str = "") -> dict[str, Any]:
    """
    调用 GoRouter 签到 API。

    返回:
        {
            "success": bool,
            "status": "checked"|"already"|"failed",
            "message": str,
            "reward_usd": float,
            "balance_usd": float,
            "username": str,
            "need_turnstile": bool,
        }
    """
    session = _create_session_for_account(account)

    if turnstile_token:
        session.headers["Turnstile"] = turnstile_token

    result: dict[str, Any] = {
        "success": False,
        "status": "failed",
        "message": "",
        "reward_usd": 0.0,
        "balance_usd": 0.0,
        "username": "",
        "need_turnstile": False,
    }

    # 1. 查询用户信息
    try:
        resp = session.get(f"{BASE_URL}/api/user/self", timeout=REQUEST_TIMEOUT)
        payload = resp.json()
        if resp.status_code < 400 and payload.get("success"):
            info = payload.get("data") or {}
            display_name = (
                info.get("display_name")
                or info.get("username")
                or info.get("email")
                or ""
            )
            result["username"] = mask_name(str(display_name))
            result["balance_usd"] = quota_to_usd(info.get("quota"))
            log.info("登录成功，当前余额 $%.2f", result["balance_usd"])
        else:
            msg = str(payload.get("message") or f"HTTP {resp.status_code}")
            log.error("查询用户信息失败: %s", msg)
            result["message"] = f"登录验证失败：{msg}"
            return result
    except Exception as exc:
        log.error("查询用户信息网络异常: %s", exc)
        result["message"] = f"查询用户信息异常：{exc}"
        return result

    # 2. 查询签到状态
    try:
        resp = session.get(f"{BASE_URL}/api/user/checkin", timeout=REQUEST_TIMEOUT)
        payload = resp.json()
        if resp.status_code < 400 and payload.get("success"):
            data = payload.get("data") or {}
            stats = data.get("stats") or {}
            if stats.get("checked_in_today"):
                result["success"] = True
                result["status"] = "already"
                result["message"] = "今日已签到"
                records = stats.get("records")
                if isinstance(records, list) and records:
                    last = records[-1]
                    if isinstance(last, dict):
                        result["reward_usd"] = quota_to_usd(last.get("quota_awarded"))
                log.info("今日已签到")
                return result
        else:
            msg = str(payload.get("message") or f"HTTP {resp.status_code}")
            log.warning("查询签到状态: %s", msg)
    except Exception as exc:
        log.error("查询签到状态异常: %s", exc)

    # 3. 执行签到 POST
    try:
        resp = session.post(f"{BASE_URL}/api/user/checkin", json={}, timeout=REQUEST_TIMEOUT)
        payload = resp.json()
        msg = str(payload.get("message") or f"HTTP {resp.status_code}")

        # Turnstile 拦截
        if not turnstile_token and _turnstile_blocked(msg):
            log.warning("签到被 Turnstile 拦截: %s", msg)
            result["message"] = msg
            result["need_turnstile"] = True
            return result

        if resp.status_code >= 400:
            log.error("签到请求失败: %s", msg)
            result["message"] = f"签到失败：{msg}"
            return result

        if not payload.get("success", False):
            if "今日已签到" in msg:
                result["success"] = True
                result["status"] = "already"
                result["message"] = "今日已签到"
                log.info("接口返回今日已签到")
                return result
            log.error("签到失败: %s", msg)
            result["message"] = f"签到失败：{msg}"
            return result

        data = payload.get("data") or {}
        result["success"] = True
        result["status"] = "checked"
        result["message"] = "签到成功"
        result["reward_usd"] = quota_to_usd(data.get("quota_awarded"))
        log.info("签到成功，获得 $%.2f", result["reward_usd"])

        # 刷新余额
        try:
            resp2 = session.get(f"{BASE_URL}/api/user/self", timeout=REQUEST_TIMEOUT)
            payload2 = resp2.json()
            if payload2.get("success") and payload2.get("data"):
                result["balance_usd"] = quota_to_usd(payload2["data"].get("quota"))
                log.info("签到后余额 $%.2f", result["balance_usd"])
        except Exception:
            pass

    except Exception as exc:
        log.error("签到请求异常: %s", exc)
        result["message"] = f"签到异常：{exc}"

    return result


# ============================================================
# 3. 浏览器获取 Turnstile token（seleniumbase UC 模式）
# ============================================================

def _get_turnstile_token_via_browser(account: dict[str, Any], name: str) -> str:
    """
    使用 seleniumbase UC 模式启动浏览器：
      - 挂载代理（如果配置了 NODE_LINK / IS_PROXY）
      - 注入 session cookie
      - 自动点击 Turnstile 复选框（uc_gui_click_captcha）
      - 提取 cf-turnstile-response token
    """
    is_proxy, proxy_server = _proxy_config()

    sb_kwargs: dict[str, Any] = {
        "uc": True,
        "browser": "chrome",
    }

    if is_proxy:
        log.info("[%s] 🔗 挂载代理: %s", name, proxy_server)
        sb_kwargs["proxy"] = proxy_server
    else:
        log.info("[%s] 🍭 直连模式（未用代理）", name)

    # 获取当前出口 IP（仅用于日志）
    ip = _get_current_ip(proxy_server if is_proxy else "")
    if ip:
        log.info("[%s] 📍 当前出口 IP: %s", name, ip)

    with SB(**sb_kwargs) as sb:
        try:
            # Step 1: 先访问首页
            log.info("[%s] 正在访问 %s ...", name, BASE_URL)
            sb.open(BASE_URL)
            sb.wait_for_ready_state_complete()
            sb.sleep(2)

            # Step 2: 注入 session cookie
            session_val = account.get("session", "")
            if session_val:
                sb.add_cookie({
                    "name": "session",
                    "value": session_val,
                    "domain": "gorouter.app",
                })

            # Step 3: 访问 dashboard 触发 Turnstile
            overview_url = f"{BASE_URL}/dashboard/overview"
            log.info("[%s] 正在访问 %s ...", name, overview_url)
            sb.open(overview_url)
            sb.wait_for_ready_state_complete()
            sb.sleep(3)

            # Step 4: 检测并解决 Turnstile
            page_source = sb.get_page_source().lower()
            has_turnstile = "turnstile" in page_source or "challenges.cloudflare.com" in page_source

            if has_turnstile:
                log.info("[%s] 🔒 检测到 Turnstile 验证，尝试自动破解...", name)
                for attempt in range(1, 4):
                    try:
                        sb.uc_gui_click_captcha()
                        log.info("[%s] 第 %d 次点击已执行，等待验证...", name, attempt)
                        time.sleep(10)
                    except Exception as e:
                        log.warning("[%s] 点击 Turnstile 出错: %s", name, e)
                        time.sleep(3)

                    token = _extract_token_from_page(sb)
                    if token:
                        log.info("[%s] ✅ Turnstile 验证通过，token 已提取（长度: %d）", name, len(token))
                        return token
                    log.info("[%s] ⏳ 第 %d 次未通过，重试...", name, attempt)
            else:
                log.info("[%s] 未检测到 Turnstile 挑战", name)

            # Step 5: 直接提取 token（可能已自动生成）
            token = _extract_token_from_page(sb)
            if token:
                log.info("[%s] 提取到 Turnstile token（长度: %d）", name, len(token))
            else:
                log.warning("[%s] 未找到 Turnstile token", name)

            return token or ""

        except Exception as e:
            log.error("[%s] 浏览器获取 token 异常: %s", name, e)
            return ""


def _extract_token_from_page(sb) -> str:
    """从页面提取 cf-turnstile-response token。"""
    try:
        return sb.execute_script("""
            var els = document.querySelectorAll(
                'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
            );
            for (var i = 0; i < els.length; i++) {
                if (els[i].value && els[i].value.trim().length > 0) {
                    return els[i].value;
                }
            }
            return '';
        """)
    except Exception:
        return ""


def _get_current_ip(proxy_server: str = "") -> str:
    try:
        proxies = None
        if proxy_server:
            proxies = {"http": proxy_server, "https": proxy_server}
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=10)
        return resp.text.strip()
    except Exception:
        return ""


# ============================================================
# 4. 单账号处理（快速路径 + Turnstile 回退）
# ============================================================

def process_account(account: dict[str, Any], index: int, total: int) -> dict[str, Any]:
    name = account["name"]
    log.info("-" * 48)
    log.info("[%d/%d] 开始处理：%s", index, total, name)

    result: dict[str, Any] = {
        "name": name,
        "success": False,
        "status": "failed",
        "message": "",
        "reward_usd": 0.0,
        "balance_usd": 0.0,
        "username": "",
    }

    # --- 快速路径：纯 API ---
    log.info("[%s] 尝试快速路径（纯 API）...", name)
    api_result = call_checkin_api(account, "")

    if api_result.get("success"):
        result.update(api_result)
        return result

    if not api_result.get("need_turnstile", False):
        result.update(api_result)
        return result

    # --- Turnstile 回退路径 ---
    log.info("[%s] 🔄 检测到 Turnstile 拦截，切换到浏览器自动化解锁...", name)

    token = _get_turnstile_token_via_browser(account, name)

    if not token:
        result["message"] = "Turnstile 验证失败，无法获取 token"
        log.error("[%s] %s", name, result["message"])
        return result

    log.info("[%s] 携带 Turnstile token 重试签到 API...", name)
    api_result = call_checkin_api(account, token)
    result.update(api_result)

    if result.get("success"):
        log.info("[%s] ✅ Turnstile 绕过成功，签到完成", name)
    else:
        log.warning("[%s] Turnstile 绕过成功但签到失败: %s", name, result.get("message", "未知"))

    return result


# ============================================================
# 5. 通知
# ============================================================

def send_notification(results: list[dict[str, Any]]) -> None:
    try:
        from notify import send_tg_notification  # type: ignore
        send_tg_notification(results, today_text())
    except ImportError as exc:
        log.warning("无法导入 notify 模块：%s", exc)
    except Exception as exc:
        log.error("发送 Telegram 通知异常：%s", exc)


def save_updated_accounts(accounts: list[dict[str, Any]]) -> None:
    with open("accounts.updated.json", "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)
    log.info("已生成 accounts.updated.json，供工作流可选写回 Secret")


# ============================================================
# 6. 主入口
# ============================================================

def main() -> int:
    log.info("=" * 48)
    log.info("GoRouter 多账号签到（seleniumbase UC + Turnstile 自动绕过）")
    log.info("目标站点：%s", BASE_URL)

    is_proxy, proxy_server = _proxy_config()
    if is_proxy:
        log.info("代理模式：已启用（%s）", proxy_server)
    else:
        log.info("代理模式：未启用（直连）")

    try:
        accounts = load_accounts()
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    log.info("已载入 %d 个有效账号", len(accounts))

    try:
        interval = max(0.0, float(os.getenv("CHECKIN_INTERVAL_SECONDS", "1.5")))
    except ValueError:
        interval = 1.5

    results: list[dict[str, Any]] = []
    updated_accounts: list[dict[str, Any]] = []

    for index, account in enumerate(accounts, start=1):
        result = process_account(account, index, len(accounts))
        results.append(result)
        updated_accounts.append(account)

        if index < len(accounts) and interval > 0:
            time.sleep(interval)

    save_updated_accounts(updated_accounts)
    send_notification(results)

    success_count = sum(1 for item in results if item.get("success"))
    failed_count = len(results) - success_count

    log.info("-" * 48)
    log.info("签到完成：成功 %d，失败 %d，总计 %d", success_count, failed_count, len(results))
    log.info("=" * 48)

    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())