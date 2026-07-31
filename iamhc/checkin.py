#!/usr/bin/env python3
"""
IAMHC / New-API 多账号每日签到（GitHub Actions）

配置：
  IAMHC_ACCOUNTS_JSON：JSON 数组，放在 GitHub Actions Secret 中。

每个账号支持：
  name       账号备注，可选
  base_url   站点地址，可选，默认读取 IAMHC_BASE_URL
  user_id    New-Api-User，必填
  username   登录用户名，Session 失效后自动登录时需要
  password   登录密码，Session 失效后自动登录时需要
  session    原始 session Cookie 值，可选，支持带 session= 前缀
  enabled    false 时跳过，可选
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


DEFAULT_BASE_URL = (os.getenv("IAMHC_BASE_URL") or "https://api.hcnsec.cn").rstrip("/")
QUOTA_PER_USD = 500_000
REQUEST_TIMEOUT = 20
BJT = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("iamhc-checkin")


def bjt_date_str() -> str:
    now = datetime.now(BJT)
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


def quota_to_usd(quota: Any) -> float:
    try:
        return round(float(quota or 0) / QUOTA_PER_USD, 2)
    except (TypeError, ValueError):
        return 0.0


def mask_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未命名"
    if len(text) <= 2:
        return text[0] + "***"
    if len(text) <= 4:
        return text[:2] + "***"
    return text[:4] + "*****"


def normalize_session(value: Any) -> str:
    session_value = str(value or "").strip()
    if session_value.lower().startswith("session="):
        session_value = session_value.split("=", 1)[1].strip()
    return session_value


def decode_legacy_session_b64(encoded: str) -> list[dict[str, Any]]:
    """兼容旧版 IAMHC_SESSION_COOKIE 的 Cookie 列表 Base64。"""
    raw = base64.b64decode(encoded, validate=True)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("旧版 Session Base64 解码后不是数组")
    return [item for item in data if isinstance(item, dict)]


def load_accounts() -> tuple[list[dict[str, Any]], list[Any]]:
    raw = (os.getenv("IAMHC_ACCOUNTS_JSON") or "").strip()
    if not raw:
        raise ValueError("未配置 IAMHC_ACCOUNTS_JSON")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"IAMHC_ACCOUNTS_JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列"
        ) from exc

    if isinstance(payload, dict):
        payload = payload.get("accounts")

    if not isinstance(payload, list):
        raise ValueError("IAMHC_ACCOUNTS_JSON 顶层必须是数组 []")

    # 保留原始列表，自动写回 Session 时不会丢掉 disabled 账号或自定义字段。
    original_accounts: list[Any] = [
        dict(item) if isinstance(item, dict) else item
        for item in payload
    ]

    accounts: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            log.warning("跳过第 %d 项：账号配置不是对象", index)
            continue

        if item.get("enabled", True) is False:
            log.info("跳过已禁用账号：%s", item.get("name") or f"账号{index}")
            continue

        account = dict(item)
        account["_source_index"] = index - 1
        account["name"] = str(item.get("name") or f"账号{index}").strip()
        account["base_url"] = str(item.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
        account["user_id"] = str(item.get("user_id") or "").strip()
        account["username"] = str(item.get("username") or "").strip()
        account["password"] = str(item.get("password") or "")
        account["session"] = normalize_session(item.get("session"))
        account["session_b64"] = str(item.get("session_b64") or "").strip()

        parsed = urlparse(account["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            log.warning("跳过 %s：base_url 无效", account["name"])
            continue

        if not account["user_id"]:
            log.warning("跳过 %s：缺少 user_id", account["name"])
            continue

        has_session = bool(account["session"] or account["session_b64"])
        has_login = bool(account["username"] and account["password"])
        if not has_session and not has_login:
            log.warning(
                "跳过 %s：至少需要 session，或 username + password",
                account["name"],
            )
            continue

        accounts.append(account)

    if not accounts:
        raise ValueError("没有可运行的 IAMHC 账号")

    return accounts, original_accounts

def create_http_session(account: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "Referer": f"{account['base_url']}/profile",
        }
    )

    if account.get("session_b64"):
        try:
            for cookie in decode_legacy_session_b64(account["session_b64"]):
                name = str(cookie.get("name") or "").strip()
                value = str(cookie.get("value") or "")
                if not name:
                    continue
                session.cookies.set(
                    name,
                    value,
                    domain=cookie.get("domain"),
                    path=cookie.get("path") or "/",
                    secure=bool(cookie.get("secure", True)),
                )
        except Exception as exc:
            log.warning("%s：旧版 Session Base64 解析失败：%s", account["name"], exc)

    if account.get("session"):
        host = urlparse(account["base_url"]).hostname
        session.cookies.set(
            "session",
            account["session"],
            domain=host,
            path="/",
            secure=account["base_url"].startswith("https://"),
        )

    return session


def set_user_header(session: requests.Session, user_id: str) -> None:
    session.headers["New-Api-User"] = user_id


def clear_user_header(session: requests.Session) -> None:
    session.headers.pop("New-Api-User", None)


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any], str, int]:
    try:
        response = session.request(
            method,
            url,
            json=json_body,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, {}, f"请求异常：{exc}", 0

    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip().replace("\n", " ")[:160]
        return (
            False,
            {},
            f"HTTP {response.status_code}，返回内容不是 JSON：{text}",
            response.status_code,
        )

    if not isinstance(payload, dict):
        return False, {}, f"HTTP {response.status_code}，返回格式异常", response.status_code

    message = str(payload.get("message") or "").strip()
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}

    if response.status_code >= 400 or not payload.get("success", False):
        return (
            False,
            data,
            message or f"HTTP {response.status_code}",
            response.status_code,
        )

    return True, data, message, response.status_code


def get_user_info(
    session: requests.Session,
    account: dict[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    set_user_header(session, account["user_id"])
    ok, data, message, _ = request_json(
        session,
        "GET",
        f"{account['base_url']}/api/user/self",
    )
    return ok, data, message


def login(session: requests.Session, account: dict[str, Any]) -> tuple[bool, str]:
    if not account.get("username") or not account.get("password"):
        return False, "Session 无效，且未配置 username/password"

    clear_user_header(session)
    ok, _, message, _ = request_json(
        session,
        "POST",
        f"{account['base_url']}/api/user/login",
        json_body={
            "username": account["username"],
            "password": account["password"],
        },
    )
    if not ok:
        return False, message or "登录失败"

    set_user_header(session, account["user_id"])
    return True, "登录成功"


def get_checkin_status(
    session: requests.Session,
    account: dict[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    set_user_header(session, account["user_id"])
    ok, data, message, _ = request_json(
        session,
        "GET",
        f"{account['base_url']}/api/user/checkin",
    )
    return ok, data, message


def do_checkin(
    session: requests.Session,
    account: dict[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    set_user_header(session, account["user_id"])
    ok, data, message, _ = request_json(
        session,
        "POST",
        f"{account['base_url']}/api/user/checkin",
    )
    if not ok and "今日已签到" in message:
        return True, {"already_checked_in": True}, message
    return ok, data, message


def extract_session_cookie(session: requests.Session) -> str:
    candidates = [cookie for cookie in session.cookies if cookie.name == "session"]
    if not candidates:
        return ""
    return str(candidates[-1].value or "")


def run_account(
    account: dict[str, Any],
    index: int,
    total: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    name = account["name"]
    log.info("-" * 56)
    log.info("[%d/%d] 处理账号：%s", index, total, name)
    log.info("目标站点：%s", account["base_url"])
    log.info("用户 ID：%s", account["user_id"])

    updated_account = dict(account)
    result: dict[str, Any] = {
        "name": name,
        "base_url": account["base_url"],
        "success": False,
        "status": "failed",
        "message": "",
        "username": "",
        "reward_usd": 0.0,
        "balance_usd": 0.0,
    }

    session = create_http_session(account)

    ok, user_info, message = get_user_info(session, account)
    if ok:
        log.info("%s：Session 有效，跳过登录", name)
    else:
        log.info("%s：Session 不可用，尝试账号密码登录", name)
        login_ok, login_message = login(session, account)
        if not login_ok:
            result["message"] = f"登录失败：{login_message}"
            log.error("%s：%s", name, result["message"])
            return result, updated_account

        ok, user_info, message = get_user_info(session, account)
        if not ok:
            result["message"] = f"登录后验证失败：{message}"
            log.error("%s：%s", name, result["message"])
            return result, updated_account
        log.info("%s：登录成功", name)

    display_name = (
        user_info.get("display_name")
        or user_info.get("username")
        or account.get("username")
        or name
    )
    result["username"] = mask_text(display_name)
    result["balance_usd"] = quota_to_usd(user_info.get("quota"))

    ok, checkin_data, message = get_checkin_status(session, account)
    if not ok:
        result["message"] = f"查询签到状态失败：{message}"
        log.error("%s：%s", name, result["message"])
        new_session = extract_session_cookie(session)
        if new_session:
            updated_account["session"] = new_session
            updated_account.pop("session_b64", None)
        return result, updated_account

    stats = checkin_data.get("stats")
    if not isinstance(stats, dict):
        stats = {}

    if bool(stats.get("checked_in_today")):
        result["success"] = True
        result["status"] = "already"
        result["message"] = "今日已签到"

        records = stats.get("records")
        if isinstance(records, list) and records:
            last = records[-1]
            if isinstance(last, dict):
                result["reward_usd"] = quota_to_usd(last.get("quota_awarded"))

        log.info("%s：✅ 今日已签到", name)
    else:
        ok, checkin_result, message = do_checkin(session, account)
        if not ok:
            result["message"] = f"签到失败：{message}"
            log.error("%s：%s", name, result["message"])
            new_session = extract_session_cookie(session)
            if new_session:
                updated_account["session"] = new_session
                updated_account.pop("session_b64", None)
            return result, updated_account

        if checkin_result.get("already_checked_in"):
            result["success"] = True
            result["status"] = "already"
            result["message"] = "今日已签到"
            log.info("%s：✅ 接口返回今日已签到", name)
        else:
            result["success"] = True
            result["status"] = "checked"
            result["message"] = "签到成功"
            result["reward_usd"] = quota_to_usd(checkin_result.get("quota_awarded"))
            log.info("%s：🎉 签到成功，获得 $%.2f", name, result["reward_usd"])

    refreshed, refreshed_info, _ = get_user_info(session, account)
    if refreshed:
        result["balance_usd"] = quota_to_usd(refreshed_info.get("quota"))

    log.info("%s：当前余额 $%.2f", name, result["balance_usd"])

    new_session = extract_session_cookie(session)
    if new_session:
        updated_account["session"] = new_session
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
    try:
        from notify import send_tg_notification

        send_tg_notification(results, bjt_date_str())
    except ImportError as exc:
        log.warning("无法导入 notify 模块：%s", exc)
    except Exception as exc:
        log.error("发送 Telegram 通知异常：%s", exc)


def main() -> int:
    log.info("=" * 56)
    log.info("IAMHC 多账号每日签到启动")

    try:
        accounts, original_accounts = load_accounts()
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
        result, updated = run_account(account, index, len(accounts))
        results.append(result)
        updated_accounts.append(updated)

        if index < len(accounts) and interval > 0:
            time.sleep(interval)

    save_updated_accounts(original_accounts, updated_accounts)
    send_notification(results)

    success_count = sum(1 for item in results if item.get("success"))
    failed_count = len(results) - success_count

    log.info("-" * 56)
    log.info(
        "签到完成：成功 %d，失败 %d，总计 %d",
        success_count,
        failed_count,
        len(results),
    )
    log.info("=" * 56)

    # 部分账号失败不让整次工作流变红；全部失败才返回 1。
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
