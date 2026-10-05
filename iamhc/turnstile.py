#!/usr/bin/env python3
"""
IAMHC Turnstile 辅助：用 seleniumbase UC 浏览器过 Cloudflare Turnstile，返回 token。

只在纯 API 签到被 Turnstile 拦截时才会被 checkin.py 调用。
seleniumbase 在函数内部才导入，没被拦截时不装它也能跑 checkin.py。

移植自同仓库 gorouter/checkin_turnstile.py，改为按账号 base_url 工作。
"""

from __future__ import annotations

import logging
import socket
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests


log = logging.getLogger("iamhc-turnstile")

DEFAULT_PROXY_SERVER = "socks5://127.0.0.1:1080"


def normalize_proxy(proxy: str) -> str:
    """标准化代理 URL，并丢弃服务商附带的 fragment 标签（例如 #us）。"""
    value = (proxy or "").strip()

    if not value:
        return ""

    if "://" not in value:
        value = f"socks5://{value}"

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()

    if scheme == "socks":
        scheme = "socks5"

    return urlunparse(
        (
            scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def mask_proxy(proxy: str) -> str:
    """隐藏代理凭证，避免把用户名和密码写入日志。"""
    parsed = urlparse(proxy)

    if not parsed.username and not parsed.password:
        return proxy

    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""

    return f"{parsed.scheme}://***:***@{hostname}{port}"


def proxy_config(is_proxy_env: str, proxy_env: str) -> tuple[bool, str]:
    """读取代理配置并检查代理是否可达，不可达则回退直连。"""
    enabled = (is_proxy_env or "false").strip().lower() == "true"
    proxy = normalize_proxy(proxy_env) or DEFAULT_PROXY_SERVER

    if not enabled:
        return False, proxy

    try:
        parsed = urlparse(proxy if "://" in proxy else f"socks5://{proxy}")
        sock = socket.create_connection(
            (parsed.hostname or "127.0.0.1", parsed.port or 1080),
            timeout=5,
        )
        sock.close()
    except Exception:  # noqa: BLE001
        log.warning("代理 %s 不可达，回退到直连模式", mask_proxy(proxy))
        return False, proxy

    return True, proxy


def requests_proxies(proxy: str) -> dict[str, str]:
    """requests 用 socks5h，让 DNS 也走代理。"""
    if not proxy:
        return {}

    url = proxy.replace("socks5://", "socks5h://", 1)

    return {"http": url, "https": url}


def get_current_ip(proxy: str = "") -> str:
    try:
        resp = requests.get(
            "https://api.ip.sb/ip",
            proxies=requests_proxies(proxy) or None,
            timeout=10,
        )
        return resp.text.strip()
    except Exception:  # noqa: BLE001
        return ""


# CDP 注入：Hook Shadow DOM，记录 Turnstile 复选框相对坐标
TURNSTILE_INJECT = """
(function() {
    if (window.self === window.top) return;
    try {
        var screenX = Math.floor(Math.random() * 400) + 800;
        var screenY = Math.floor(Math.random() * 200) + 400;
        Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
        Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });
    } catch (e) { }
    try {
        var orig = Element.prototype.attachShadow;
        Element.prototype.attachShadow = function(init) {
            var root = orig.call(this, init);
            if (root) {
                var check = function() {
                    var cb = root.querySelector('input[type="checkbox"]');
                    if (cb) {
                        var r = cb.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && window.innerWidth > 0) {
                            window.__ts_data = {
                                xR: (r.left + r.width / 2) / window.innerWidth,
                                yR: (r.top + r.height / 2) / window.innerHeight
                            };
                            return true;
                        }
                    }
                    return false;
                };
                if (!check()) {
                    var obs = new MutationObserver(function() {
                        if (check()) obs.disconnect();
                    });
                    obs.observe(root, { childList: true, subtree: true });
                }
            }
            return root;
        };
    } catch (e) {}
})();
"""

FIND_TURNSTILE_COORDS = """
var data = null;
for (var fi = 0; fi < window.frames.length; fi++) {
    try {
        var w = window.frames[fi];
        if (w.__ts_data) {
            var iframes = window.document.querySelectorAll('iframe');
            var box = null;
            for (var k = 0; k < iframes.length; k++) {
                try {
                    if (iframes[k].contentWindow === w) {
                        box = iframes[k].getBoundingClientRect();
                        break;
                    }
                } catch (e) {}
            }
            if (!box) {
                try {
                    var fEl = w.frameElement;
                    if (fEl) box = fEl.getBoundingClientRect();
                } catch (e) {}
            }
            if (box && box.width > 0) {
                data = {
                    cx: Math.round(box.x + box.width * w.__ts_data.xR),
                    cy: Math.round(box.y + box.height * w.__ts_data.yR)
                };
                w.__ts_data = null;
                break;
            }
        }
    } catch (e) {}
}
return data;
"""

EXTRACT_TOKEN = """
var els = document.querySelectorAll(
    'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
);
for (var i = 0; i < els.length; i++) {
    if (els[i].value && els[i].value.trim().length > 0) return els[i].value;
}
try {
    if (typeof turnstile !== 'undefined' && turnstile.getResponse) {
        var r = turnstile.getResponse();
        if (r) return r;
    }
} catch (e) {}
return '';
"""

CLICK_CHECKIN_JS = """
var kws = ['每日签到', '签到', 'Check-in', 'Check in', 'Daily'];
var els = document.querySelectorAll(
    'button, a, [role="button"], div[class*="checkin"], div[class*="check-in"]'
);
for (var i = 0; i < els.length; i++) {
    var t = (els[i].innerText || els[i].textContent || '').trim();
    if (!t || els[i].offsetParent === null) continue;
    for (var k = 0; k < kws.length; k++) {
        if (t.indexOf(kws[k]) >= 0) { els[i].click(); return t; }
    }
}
return '';
"""


def _click_checkin_button(sb: Any, name: str) -> bool:
    """点击「每日签到」，触发 Turnstile 弹窗。"""
    selectors = [
        'button:contains("每日签到")',
        'button:contains("签到")',
        'button:contains("Check-in")',
        'button:contains("Check in")',
    ]

    for sel in selectors:
        try:
            if sb.is_element_visible(sel):
                sb.click(sel)
                log.info("[%s] 已点击签到按钮: %s", name, sel)
                return True
        except Exception:  # noqa: BLE001
            continue

    clicked = sb.execute_script(CLICK_CHECKIN_JS)

    if clicked:
        log.info("[%s] 已通过 JS 点击签到按钮: %s", name, clicked)
        return True

    return False


def _inject_cookies(
    sb: Any,
    host: str,
    session_value: str,
    refresh_value: str,
) -> None:
    """用 CDP 注入登录 cookie（refresh 是 HttpOnly 且限定路径，add_cookie 做不到）。"""
    cookies: list[dict[str, Any]] = []

    if refresh_value:
        cookies.append(
            {
                "name": "new_api_refresh",
                "value": refresh_value,
                "domain": host,
                "path": "/api/user/auth",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Strict",
            }
        )
        cookies.append(
            {
                "name": "new_api_has_session",
                "value": "1",
                "domain": host,
                "path": "/",
                "secure": True,
                "sameSite": "Strict",
            }
        )

    if session_value:
        cookies.append(
            {
                "name": "session",
                "value": session_value,
                "domain": host,
                "path": "/",
                "secure": True,
            }
        )

    for cookie in cookies:
        try:
            sb.execute_cdp_cmd("Network.setCookie", cookie)
        except Exception as exc:  # noqa: BLE001
            log.warning("注入 cookie %s 失败：%s", cookie["name"], exc)


def _read_refresh(sb: Any, host: str) -> str:
    """读浏览器里当前的 new_api_refresh（页面加载时前端会自己刷新并轮换它）。"""
    try:
        cookies = sb.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", [])
    except Exception:  # noqa: BLE001
        return ""

    for cookie in cookies:
        if cookie.get("name") == "new_api_refresh" and host in str(
            cookie.get("domain", "")
        ):
            return str(cookie.get("value") or "")

    return ""


def get_turnstile_token(
    base_url: str,
    name: str,
    proxy: str = "",
    session_value: str = "",
    refresh_value: str = "",
) -> tuple[str, str]:
    """
    启动 UC 浏览器，注入登录 cookie，打开 /profile，点签到并过 Turnstile。

    返回 (turnstile_token, 浏览器里最新的 refresh)。
    refresh 在浏览器里可能已被轮换，调用方必须以返回值为准。
    proxy 为空表示直连。拿不到 token 时 token 为空字符串。
    """
    try:
        from seleniumbase import SB
    except ImportError:
        log.error("[%s] 未安装 seleniumbase，无法过 Turnstile", name)
        return "", ""

    host = urlparse(base_url).hostname or ""

    sb_kwargs: dict[str, Any] = {"uc": True, "browser": "chrome"}

    if proxy:
        log.info("[%s] 挂载代理: %s", name, mask_proxy(proxy))
        sb_kwargs["proxy"] = proxy
    else:
        log.info("[%s] 直连模式（未用代理）", name)

    ip = get_current_ip(proxy)

    if ip:
        log.info("[%s] 当前出口 IP: %s", name, ip)

    token = ""
    refresh_out = ""

    with SB(**sb_kwargs) as sb:
        try:
            log.info("[%s] 正在打开 %s (UC 模式)...", name, base_url)
            sb.uc_open_with_reconnect(base_url, reconnect_time=4)
            sb.sleep(2)

            _inject_cookies(sb, host, session_value, refresh_value)

            sb.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": TURNSTILE_INJECT},
            )

            profile_url = f"{base_url}/profile"
            log.info("[%s] 正在打开 %s (UC 模式)...", name, profile_url)
            sb.uc_open_with_reconnect(profile_url, reconnect_time=4)
            sb.sleep(3)

            # Turnstile widget 只在点击签到后弹出的 modal 里渲染
            if _click_checkin_button(sb, name):
                sb.sleep(3)
            else:
                log.info("[%s] 未找到签到按钮，直接检测页面 Turnstile", name)

            page_src = sb.get_page_source().lower()
            has_ts = (
                "turnstile" in page_src
                or "challenges.cloudflare.com" in page_src
            )

            if has_ts:
                log.info("[%s] Turnstile 已检测到，尝试 CDP 点击...", name)

                for attempt in range(1, 5):
                    log.info("[%s] CDP 尝试 %d/4 ...", name, attempt)

                    coords = sb.execute_script(FIND_TURNSTILE_COORDS)

                    if isinstance(coords, dict) and "cx" in coords:
                        cx, cy = coords["cx"], coords["cy"]
                        log.info("[%s] CDP 坐标 (%d, %d) 点击", name, cx, cy)

                        for event_type in ("mousePressed", "mouseReleased"):
                            sb.execute_cdp_cmd(
                                "Input.dispatchMouseEvent",
                                {
                                    "type": event_type,
                                    "x": cx,
                                    "y": cy,
                                    "button": "left",
                                    "clickCount": 1,
                                },
                            )
                            time.sleep(0.1)

                        log.info("[%s] 等待 Turnstile 验证 (8s)...", name)
                        time.sleep(8)
                    else:
                        try:
                            sb.uc_gui_click_captcha()
                            log.info(
                                "[%s] uc_gui_click_captcha() 已执行，等待...",
                                name,
                            )
                            time.sleep(10)
                        except Exception:  # noqa: BLE001
                            log.info("[%s] 无可点击目标，等待页面自解...", name)
                            time.sleep(6)

                    token = sb.execute_script(EXTRACT_TOKEN) or ""

                    if token:
                        log.info(
                            "[%s] Turnstile 通过，token 长度: %d",
                            name,
                            len(token),
                        )
                        break

                    log.info("[%s] 第 %d 次未获取到 token", name, attempt)
            else:
                log.info("[%s] 未检测到 Turnstile 页面元素", name)

            if not token:
                token = sb.execute_script(EXTRACT_TOKEN) or ""

                if token:
                    log.info("[%s] 从页面提取到 token（长度: %d）", name, len(token))
                else:
                    log.warning(
                        "[%s] 未找到 cf-turnstile-response，当前 URL: %s",
                        name,
                        sb.get_current_url(),
                    )

        except Exception as exc:  # noqa: BLE001
            log.error("[%s] 浏览器异常: %s", name, exc)

        refresh_out = _read_refresh(sb, host)

    return token, refresh_out
