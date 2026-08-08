#!/usr/bin/env python3
"""
GoRouter 多账号每日签到（Playwright + CDP 绕过 Cloudflare Turnstile）

从环境变量 GOROUTER_ACCOUNTS_JSON 读取账号列表。

核心流程：
  1. 先尝试纯 requests 签到（快速路径）
  2. 如果 API 返回 Turnstile token 为空，则启动 Playwright 浏览器
  3. 浏览器访问 gorouter.app，自动点击 Turnstile 复选框（CDP 模拟）
  4. 从 DOM 提取 cf-turnstile-response token
  5. 携带 token 再次调用签到 API
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
from playwright.sync_api import sync_playwright

BASE_URL = "https://gorouter.app"

# GoRouter / New-API 配额换算
QUOTA_PER_USD = 500_000

BJT = timezone(timedelta(hours=8))

REQUEST_TIMEOUT = 20

# --- Chrome / CDP 配置 ---
DEBUG_PORT = 9222
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("gorouter-checkin-ts")


# ============================================================
# 1. 注入脚本：Hook Shadow DOM，获取 Turnstile 复选框坐标
#    来自 katabump-renew 的核心技术
# ============================================================

INJECTED_SCRIPT = """
(function() {
    if (window.self === window.top) return;
    try {
        function getRandomInt(min, max) {
            return Math.floor(Math.random() * (max - min + 1)) + min;
        }
        var screenX = getRandomInt(800, 1200);
        var screenY = getRandomInt(400, 600);
        Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
        Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });
    } catch (e) { }

    try {
        var originalAttachShadow = Element.prototype.attachShadow;
        Element.prototype.attachShadow = function(init) {
            var shadowRoot = originalAttachShadow.call(this, init);
            if (shadowRoot) {
                var checkAndReport = function() {
                    var checkbox = shadowRoot.querySelector('input[type="checkbox"]');
                    if (checkbox) {
                        var rect = checkbox.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && window.innerWidth > 0 && window.innerHeight > 0) {
                            var xRatio = (rect.left + rect.width / 2) / window.innerWidth;
                            var yRatio = (rect.top + rect.height / 2) / window.innerHeight;
                            window.__turnstile_data = { xRatio: xRatio, yRatio: yRatio };
                            return true;
                        }
                    }
                    return false;
                };
                if (!checkAndReport()) {
                    var observer = new MutationObserver(function() {
                        if (checkAndReport()) observer.disconnect();
                    });
                    observer.observe(shadowRoot, { childList: true, subtree: true });
                }
            }
            return shadowRoot;
        };
    } catch (e) {
        console.error('[注入] Hook attachShadow 失败:', e);
    }
})();
"""


# ============================================================
# 2. 辅助函数
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
    return "turnstile" in msg_lower and ("为空" in message or "empty" in msg_lower or "token" in msg_lower)


# ============================================================
# 3. CDP Turnstile 解法（来自 katabump-renew）
# ============================================================

def _create_session_for_account(account: dict[str, Any]) -> requests.Session:
    """创建带 cookie 和 header 的 requests.Session。"""
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
            "session",
            account["session"],
            domain="gorouter.app",
            path="/",
            secure=True,
        )

    return session


def dispatch_cdp_click(page, x: float, y: float) -> bool:
    """通过 CDP 发送鼠标点击事件。"""
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1,
        })
        time.sleep(0.05 + 0.1 * (time.time() % 1))
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1,
        })
        log.info(">> CDP 坐标 (%.2f, %.2f) 点击已发送", x, y)
        return True
    except Exception as e:
        log.warning(">> CDP 点击失败: %s", e)
        return False


def _attempt_turnstile_cdp(page) -> bool:
    """遍历所有 frame 查找 __turnstile_data 并点击。"""
    for frame in page.frames:
        try:
            data = frame.evaluate("() => window.__turnstile_data")
            if data:
                log.info(">> 发现 Turnstile 数据: %s", data)
                frame.evaluate("() => { window.__turnstile_data = null; }")
                iframe_el = frame.frame_element()
                if not iframe_el:
                    continue
                box = iframe_el.bounding_box()
                if not box:
                    continue
                click_x = box["x"] + box["width"] * data["xRatio"]
                click_y = box["y"] + box["height"] * data["yRatio"]
                return dispatch_cdp_click(page, click_x, click_y)
        except Exception:
            pass
    return False


def _check_turnstile_success(page) -> bool:
    """检查 Turnstile 是否已通过。"""
    try:
        return page.evaluate("""
            () => {
                var els = document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
                return Array.from(els).some(function(el) { return el.value && el.value.trim().length > 0; });
            }
        """)
    except Exception:
        return False


def _get_turnstile_token(page) -> str:
    """从 DOM 提取 cf-turnstile-response 令牌。"""
    try:
        return page.evaluate("""
            () => {
                var els = document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
                for (var i = 0; i < els.length; i++) {
                    if (els[i].value && els[i].value.trim().length > 0) {
                        return els[i].value;
                    }
                }
                return '';
            }
        """)
    except Exception:
        return ""


def _has_turnstile_frame(page) -> bool:
    """检测页面是否有 Turnstile iframe。"""
    try:
        return page.evaluate("""
            () => document.querySelectorAll('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]').length > 0
        """)
    except Exception:
        return False


def solve_turnstile(page, stage_name: str = "签到", max_attempts: int = 10,
                    wait_after_click: int = 5000) -> bool:
    """
    解决 Cloudflare Turnstile 验证。
    逻辑完全对应 katabump-renew 的 solveTurnstileIfPresent。
    """
    log.info("[%s] 开始检测 Cloudflare Turnstile...", stage_name)
    saw_turnstile = False
    for i in range(max_attempts):
        if _has_turnstile_frame(page):
            saw_turnstile = True
        if _check_turnstile_success(page):
            log.info("[%s] ✅ Turnstile 已通过验证。", stage_name)
            return True
        clicked = _attempt_turnstile_cdp(page)
        if clicked:
            saw_turnstile = True
            log.info("[%s] 已点击 Turnstile，等待验证结果 (%dms)...",
                     stage_name, wait_after_click)
            page.wait_for_timeout(wait_after_click)
            if _check_turnstile_success(page):
                log.info("[%s] ✅ Turnstile 验证通过！", stage_name)
                return True
            log.info("[%s] ⚠️ 点击后验证未通过，继续重试...", stage_name)
        if i < max_attempts - 1:
            page.wait_for_timeout(1000)
    if not saw_turnstile:
        log.info("[%s] 未检测到 Turnstile。", stage_name)
        return True
    log.info("[%s] 检测到 Turnstile，但未能通过验证。", stage_name)
    return False


# ============================================================
# 4. 浏览器获取 Turnstile token
# ============================================================

def _get_turnstile_token_via_browser(account: dict[str, Any], name: str) -> str:
    """
    启动 Playwright 浏览器，访问 gorouter.app，解决 Turnstile，
    返回 cf-turnstile-response 令牌。
    """
    log.info("[%s] 启动浏览器获取 Turnstile token...", name)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # CI 中需要 xvfb-run
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
            ],
        )

        try:
            context = browser.new_context(
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/150.0.0.0 Safari/537.36"
                ),
            )

            # 设置 session cookie
            session_val = account.get("session", "")
            if session_val:
                context.add_cookies([{
                    "name": "session",
                    "value": session_val,
                    "domain": "gorouter.app",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                }])

            page = context.new_page()

            # 注入 Turnstile 检测脚本（所有 frame 生效）
            page.add_init_script(INJECTED_SCRIPT)

            # 访问首页
            log.info("[%s] 正在访问 %s ...", name, BASE_URL)
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 访问 overview 触发完整页面加载
            overview_url = f"{BASE_URL}/dashboard/overview"
            log.info("[%s] 正在访问 %s ...", name, overview_url)
            page.goto(overview_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 检测并解决 Turnstile
            ts_ok = solve_turnstile(page, "登录验证", 10, 5000)

            if not ts_ok:
                log.warning("[%s] Turnstile 验证失败", name)
                return ""

            page.wait_for_timeout(2000)

            # 提取 token
            token = _get_turnstile_token(page)
            if token:
                log.info("[%s] 成功提取 Turnstile token (长度: %d)", name, len(token))
            else:
                log.warning("[%s] 未找到 Turnstile token", name)

            return token

        except Exception as e:
            log.error("[%s] 浏览器获取 token 异常: %s", name, e)
            return ""
        finally:
            browser.close()


# ============================================================
# 5. 签到 API（requests）
# ============================================================

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
            "need_turnstile": bool,   # 标识是否需要 Turnstile token
        }
    """
    session = _create_session_for_account(account)

    # 携带 Turnstile token（New-API 典型 header）
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
        resp = session.get(
            f"{BASE_URL}/api/user/self",
            timeout=REQUEST_TIMEOUT,
        )
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
        resp = session.get(
            f"{BASE_URL}/api/user/checkin",
            timeout=REQUEST_TIMEOUT,
        )
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
        resp = session.post(
            f"{BASE_URL}/api/user/checkin",
            json={},
            timeout=REQUEST_TIMEOUT,
        )
        payload = resp.json()
        msg = str(payload.get("message") or f"HTTP {resp.status_code}")

        # 检查是否 Turnstile token 为空
        if not turnstile_token and _turnstile_blocked(msg):
            log.warning("签到被 Turnstile 拦截: %s", msg)
            result["message"] = msg
            result["need_turnstile"] = True
            return result

        # 检查失败原因
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
            resp2 = session.get(
                f"{BASE_URL}/api/user/self",
                timeout=REQUEST_TIMEOUT,
            )
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
# 6. 单账号处理（快速路径 + Turnstile 回退）
# ============================================================

def process_account(account: dict[str, Any], index: int, total: int) -> dict[str, Any]:
    """
    处理单个账号签到。

    策略：
      1. 快速路径：纯 requests 调用 API
      2. 如果 API 返回需要 Turnstile → 启动浏览器获取 token → 重试
    """
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

    # 如果不是 Turnstile 问题，直接返回错误
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

    # 携带 token 重试 API
    log.info("[%s] 携带 Turnstile token 重试签到 API...", name)
    api_result = call_checkin_api(account, token)
    result.update(api_result)

    if result.get("success"):
        log.info("[%s] ✅ Turnstile 绕过成功，签到完成", name)
    else:
        log.warning("[%s] Turnstile 绕过成功但签到失败: %s", name, result.get("message", "未知"))

    return result


# ============================================================
# 7. 通知
# ============================================================

def send_notification(results: list[dict[str, Any]]) -> None:
    try:
        from notify import send_tg_notification
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
# 8. 主入口
# ============================================================

def main() -> int:
    log.info("=" * 48)
    log.info("GoRouter 多账号签到（纯 API + Turnstile 自动绕过回退）")
    log.info("目标站点：%s", BASE_URL)

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