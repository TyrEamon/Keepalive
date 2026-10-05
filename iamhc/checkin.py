#!/usr/bin/env python3
"""
IAMHC（新疆幻城）多账号每日签到（GitHub Actions）

基于同仓库 gorouter/checkin.py 改写，适配 https://api.hcnsec.cn

认证方式：**只用 session Cookie**，不提供账号密码登录。
原因：该站登录接口要求 Cloudflare Turnstile token，脚本无法也不应提供；
      只要 session 有效即可完成签到，失效时通过 Telegram 提醒人工更新。

从环境变量 IAMHC_ACCOUNTS_JSON 读取账号列表。

每个账号支持：
- name         账号备注（可选）
- user_id      New-Api-User，必填
- session      session Cookie 值（可写裸值，也可写 session=xxxx）
- session_b64  旧版：Cookie 列表的 Base64（兼容保留，成功后会写回为 session）
- base_url     可选，覆盖环境变量 IAMHC_BASE_URL
- token        可选，Bearer Token
- enabled      false 时跳过
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_BASE_URL = (
    os.getenv("IAMHC_BASE_URL") or "https://api.hcnsec.cn"
).strip().rstrip("/")

# New-API 配额换算：500000 quota = 1 USD
QUOTA_PER_USD = 500_000

REQUEST_TIMEOUT = 20

# 北京时间
BJT = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("iamhc-checkin")

# 判定「需要人工重新认证」的关键词（命中即提示换 session，而不是当成普通故障）
AUTH_HINTS = (
    "未登录",
    "请登录",
    "登录已过期",
    "登录过期",
    "无权",
    "unauthorized",
    "session",
)


def today_text() -> str:
    """返回北京时间日期。"""
    now = datetime.now(BJT)
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


def quota_to_usd(quota: Any) -> float:
    """将 quota 换算为美元。"""
    try:
        return round(float(quota or 0) / QUOTA_PER_USD, 2)
    except (TypeError, ValueError):
        return 0.0


def mask_name(value: Any) -> str:
    """对站内用户名进行简单脱敏。"""
    text = str(value or "").strip()

    if not text:
        return ""

    if len(text) <= 2:
        return text[0] + "***"

    if len(text) <= 4:
        return text[:2] + "***"

    return text[:4] + "*****"


def normalize_session(value: Any) -> str:
    """标准化 Session，允许填裸值或 session=xxxx。"""
    text = str(value or "").strip()

    if text.lower().startswith("session="):
        text = text.split("=", 1)[1].strip()

    return text


def decode_session_b64(encoded: str) -> list[dict[str, Any]]:
    """旧版 IAMHC_SESSION_COOKIE：Cookie 列表的 Base64。"""
    raw = base64.b64decode(encoded, validate=True)
    data = json.loads(raw.decode("utf-8"))

    if not isinstance(data, list):
        raise ValueError("旧版 Session Base64 解码后不是数组")

    return [item for item in data if isinstance(item, dict)]


def load_accounts() -> tuple[list[dict[str, Any]], list[Any]]:
    """
    读取并验证多账号配置。

    返回 (可运行账号列表, 原始账号数组)。
    原始数组用于写回时保留 disabled 账号与自定义字段。
    """
    raw = (os.getenv("IAMHC_ACCOUNTS_JSON") or "").strip()

    if not raw:
        raise ValueError("未配置 IAMHC_ACCOUNTS_JSON")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "IAMHC_ACCOUNTS_JSON 不是有效 JSON："
            f"第 {exc.lineno} 行第 {exc.colno} 列"
        ) from exc

    if isinstance(payload, dict):
        payload = payload.get("accounts")

    if not isinstance(payload, list):
        raise ValueError("IAMHC_ACCOUNTS_JSON 顶层必须是数组 []")

    original: list[Any] = [
        dict(item) if isinstance(item, dict) else item
        for item in payload
    ]

    accounts: list[dict[str, Any]] = []

    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            log.warning("跳过第 %d 项：账号配置必须是对象", index)
            continue

        if item.get("enabled", True) is False:
            log.info(
                "跳过已禁用账号：%s",
                item.get("name") or f"账号{index}",
            )
            continue

        account = dict(item)
        account["_source_index"] = index - 1

        account["name"] = str(
            item.get("name") or f"账号{index}"
        ).strip()

        account["user_id"] = str(
            item.get("user_id") or ""
        ).strip()

        account["session"] = normalize_session(
            item.get("session")
        )

        account["session_b64"] = str(
            item.get("session_b64") or ""
        ).strip()

        account["token"] = str(
            item.get("token") or ""
        ).strip()

        account["base_url"] = str(
            item.get("base_url") or DEFAULT_BASE_URL
        ).strip().rstrip("/")

        parsed = urlparse(account["base_url"])

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            log.warning(
                "跳过 %s：base_url 无效",
                account["name"],
            )
            continue

        if not account["user_id"]:
            log.warning(
                "跳过 %s：缺少 user_id",
                account["name"],
            )
            continue

        if (
            not account["session"]
            and not account["session_b64"]
            and not account["token"]
        ):
            log.warning(
                "跳过 %s：至少需要 session 或 token",
                account["name"],
            )
            continue

        accounts.append(account)

    if not accounts:
        raise ValueError(
            "没有可运行的账号，请检查 enabled、user_id 和 session"
        )

    return accounts, original


def create_session(account: dict[str, Any]) -> requests.Session:
    """创建带有账号 Cookie 和请求头的 Session。"""
    base_url = account["base_url"]
    host = urlparse(base_url).hostname or ""

    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Referer": f"{base_url}/profile",
        "New-Api-User": account["user_id"],
    }

    # 个别账号若有 Bearer Token，也可以填写
    if account.get("token"):
        headers["Authorization"] = f"Bearer {account['token']}"

    session.headers.update(headers)

    # 旧版 session_b64（Cookie 列表）
    if account.get("session_b64"):
        try:
            for cookie in decode_session_b64(account["session_b64"]):
                name = str(cookie.get("name") or "").strip()

                if not name:
                    continue

                session.cookies.set(
                    name,
                    str(cookie.get("value") or ""),
                    domain=cookie.get("domain") or host,
                    path=cookie.get("path") or "/",
                    secure=bool(cookie.get("secure", True)),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "%s：旧版 Session Base64 解析失败：%s",
                account["name"],
                exc,
            )

    if account.get("session"):
        session.cookies.set(
            "session",
            account["session"],
            domain=host,
            path="/",
            secure=base_url.startswith("https://"),
        )

    return session


def request_json(
    session: requests.Session,
    method: str,
    base_url: str,
    path: str,
) -> tuple[bool, dict[str, Any], str, int]:
    """
    请求站点 API。

    返回：ok, data, message, status
    """
    url = f"{base_url}{path}"

    try:
        response = session.request(
            method,
            url,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, {}, f"请求异常：{exc}", 0

    status = response.status_code

    try:
        payload = response.json()
    except ValueError:
        return (
            False,
            {},
            f"HTTP {status}，返回内容不是 JSON",
            status,
        )

    if not isinstance(payload, dict):
        return (
            False,
            {},
            f"HTTP {status}，返回格式异常",
            status,
        )

    message = str(payload.get("message") or "").strip()

    if status >= 400:
        return (
            False,
            payload,
            message or f"HTTP {status}",
            status,
        )

    if not payload.get("success", False):
        return (
            False,
            payload,
            message or "接口返回失败",
            status,
        )

    data = payload.get("data")

    if not isinstance(data, dict):
        data = {}

    return True, data, message, status


def get_user_info(
    session: requests.Session,
    base_url: str,
) -> tuple[bool, dict[str, Any], str, int]:
    """获取账号信息和余额。"""
    return request_json(session, "GET", base_url, "/api/user/self")


def get_checkin_status(
    session: requests.Session,
    base_url: str,
) -> tuple[bool, dict[str, Any], str, int]:
    """查询今日签到状态。"""
    return request_json(session, "GET", base_url, "/api/user/checkin")


def do_checkin(
    session: requests.Session,
    base_url: str,
) -> tuple[bool, dict[str, Any], str, int]:
    """执行签到。"""
    ok, data, message, status = request_json(
        session, "POST", base_url, "/api/user/checkin"
    )

    # 并发或重复调用时，站点可能返回「今日已签到」
    if not ok and "今日已签到" in message:
        return True, {"already_checked_in": True}, message, status

    return ok, data, message, status


def get_current_session_cookie(
    session: requests.Session,
    host: str,
) -> str:
    """
    获取请求完成后的 Session Cookie。

    如果服务端通过 Set-Cookie 刷新 Session，
    这里会取到更新后的值。
    """
    candidates = [
        cookie
        for cookie in session.cookies
        if cookie.name == "session"
    ]

    if not candidates:
        return ""

    for cookie in candidates:
        if cookie.domain in {host, f".{host}"}:
            return str(cookie.value or "")

    return str(candidates[-1].value or "")


def looks_like_auth_failure(message: str, status: int) -> bool:
    """判断这次失败是不是「session 失效」，而不是普通故障。"""
    if status in {401, 403}:
        return True

    lowered = (message or "").lower()

    return any(hint in lowered for hint in AUTH_HINTS)


def run_account(
    account: dict[str, Any],
    index: int,
    total: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """执行单个账号签到。"""
    name = account["name"]
    base_url = account["base_url"]

    log.info("-" * 56)
    log.info("[%d/%d] 处理账号：%s", index, total, name)
    log.info("目标站点：%s", base_url)
    log.info("用户 ID：%s", account["user_id"])

    result: dict[str, Any] = {
        "name": name,
        "success": False,
        "status": "failed",
        "needs_reauth": False,
        "message": "",
        "reward_usd": 0.0,
        "balance_usd": 0.0,
        "username": "",
    }

    # 保留原账号中的其他自定义字段
    updated_account = dict(account)

    session = create_session(account)

    # 验证登录并获取当前余额
    ok, user_info, message, status = get_user_info(session, base_url)

    if not ok:
        if looks_like_auth_failure(message, status):
            result["needs_reauth"] = True
            result["message"] = (
                "Session 已失效，需要人工更新 session（登录接口需 Turnstile）"
            )
            log.warning("%s：%s", name, result["message"])
        else:
            result["message"] = f"登录验证失败：{message}"
            log.error("%s：%s", name, result["message"])

        return result, updated_account

    display_name = (
        user_info.get("display_name")
        or user_info.get("username")
        or user_info.get("email")
        or ""
    )

    result["username"] = mask_name(display_name)
    result["balance_usd"] = quota_to_usd(user_info.get("quota"))

    log.info(
        "%s：Session 有效，跳过登录，当前余额 $%.2f",
        name,
        result["balance_usd"],
    )

    # 查询签到状态
    ok, checkin_data, message, _ = get_checkin_status(session, base_url)

    if not ok:
        result["message"] = f"查询签到状态失败：{message}"
        log.error("%s：%s", name, result["message"])

        refreshed = get_current_session_cookie(session, urlparse(base_url).hostname or "")

        if refreshed:
            updated_account["session"] = refreshed
            updated_account.pop("session_b64", None)

        return result, updated_account

    stats = checkin_data.get("stats")

    if not isinstance(stats, dict):
        stats = {}

    checked_in_today = bool(stats.get("checked_in_today"))

    if checked_in_today:
        result["success"] = True
        result["status"] = "already"
        result["message"] = "今日已签到"

        # 尝试读取最近一次签到奖励
        records = stats.get("records")

        if isinstance(records, list) and records:
            last_record = records[-1]

            if isinstance(last_record, dict):
                result["reward_usd"] = quota_to_usd(
                    last_record.get("quota_awarded")
                )

        log.info("%s：✅ 今日已签到", name)

    else:
        # 今日尚未签到，执行签到
        ok, checkin_result, message, _ = do_checkin(session, base_url)

        if not ok:
            result["message"] = f"签到失败：{message}"
            log.error("%s：%s", name, result["message"])

            refreshed = get_current_session_cookie(
                session, urlparse(base_url).hostname or ""
            )

            if refreshed:
                updated_account["session"] = refreshed
                updated_account.pop("session_b64", None)

            return result, updated_account

        if checkin_result.get("already_checked_in"):
            result["success"] = True
            result["status"] = "already"
            result["message"] = "今日已签到"
            log.info("%s：接口返回今日已签到", name)

        else:
            result["success"] = True
            result["status"] = "checked"
            result["message"] = "签到成功"
            result["reward_usd"] = quota_to_usd(
                checkin_result.get("quota_awarded")
            )
            log.info(
                "%s：🎉 签到成功，获得 $%.2f",
                name,
                result["reward_usd"],
            )

        # 签到后重新查询余额
        refreshed, refreshed_info, _, _ = get_user_info(session, base_url)

        if refreshed:
            result["balance_usd"] = quota_to_usd(
                refreshed_info.get("quota")
            )
            log.info(
                "%s：签到后余额 $%.2f",
                name,
                result["balance_usd"],
            )

    # 保存服务端可能刷新的 Session
    refreshed = get_current_session_cookie(
        session, urlparse(base_url).hostname or ""
    )

    if refreshed:
        updated_account["session"] = refreshed
        updated_account.pop("session_b64", None)

    return result, updated_account


def save_updated_accounts(
    original_accounts: list[Any],
    updated_accounts: list[dict[str, Any]],
) -> None:
    """
    只把刷新后的 session 合并回原始 JSON。

    disabled 账号、账号顺序以及用户自行添加的字段都会保留。
    """
    merged: list[Any] = [
        dict(item) if isinstance(item, dict) else item
        for item in original_accounts
    ]

    for account in updated_accounts:
        source_index = account.get("_source_index")

        if not isinstance(source_index, int):
            continue

        if source_index < 0 or source_index >= len(merged):
            continue

        if not isinstance(merged[source_index], dict):
            continue

        new_session = normalize_session(account.get("session"))

        if new_session:
            merged[source_index]["session"] = new_session
            merged[source_index].pop("session_b64", None)

    with open("accounts.updated.json", "w", encoding="utf-8") as file:
        json.dump(merged, file, ensure_ascii=False, indent=2)

    log.info("已生成 accounts.updated.json，供工作流可选写回 Secret")


def send_notification(results: list[dict[str, Any]]) -> None:
    """发送 Telegram 汇总通知。"""
    try:
        from notify import send_tg_notification

        send_tg_notification(results, today_text())

    except ImportError as exc:
        log.warning("无法导入 notify 模块：%s", exc)

    except Exception as exc:  # noqa: BLE001
        log.error("发送 Telegram 通知异常：%s", exc)


def main() -> int:
    log.info("=" * 56)
    log.info("IAMHC 多账号每日签到启动")
    log.info("默认站点：%s", DEFAULT_BASE_URL)

    try:
        accounts, original_accounts = load_accounts()
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    log.info("已载入 %d 个有效账号", len(accounts))

    try:
        interval = max(
            0.0,
            float(os.getenv("CHECKIN_INTERVAL_SECONDS", "1.5")),
        )
    except ValueError:
        interval = 1.5

    results: list[dict[str, Any]] = []
    updated_accounts: list[dict[str, Any]] = []

    for index, account in enumerate(accounts, start=1):
        result, updated_account = run_account(
            account, index, len(accounts)
        )

        results.append(result)
        updated_accounts.append(updated_account)

        if index < len(accounts) and interval > 0:
            time.sleep(interval)

    # 无论部分账号是否失败，都保存其余账号状态
    save_updated_accounts(original_accounts, updated_accounts)

    # Telegram 汇总
    send_notification(results)

    success_count = sum(
        1 for item in results if item.get("success")
    )
    reauth_count = sum(
        1 for item in results if item.get("needs_reauth")
    )
    failed_count = len(results) - success_count

    log.info("-" * 56)
    log.info(
        "签到完成：成功 %d，失败 %d，需重新认证 %d，总计 %d",
        success_count,
        failed_count,
        reauth_count,
        len(results),
    )

    if reauth_count:
        log.warning(
            "有 %d 个账号需要人工更新 session，请查收 Telegram 通知",
            reauth_count,
        )

    log.info("=" * 56)

    # 「等人换 cookie」属于正常状态，不该让工作流报红；
    # 只有全部账号都以非认证原因失败时才返回 1。
    if success_count > 0 or reauth_count > 0:
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
