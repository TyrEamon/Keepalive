# AgentRouter 每日签到

自动为 [AgentRouter](https://agentrouter.org) 站点每日签到领取免费额度，并通过 Telegram Bot 发送通知。

## 核心机制（重要，先理解再配置）

通过分析站点前端 JS 得出：

1. **签到 = 每天登录一次**。AgentRouter 没有独立的签到接口，签到是在**登录时由后端自动触发**的——登录成功返回的数据里带 `checked_in` 字段，为 `true` 表示本次登录触发了当日签到、额度已到账。
2. 站点用 **GitHub OAuth** 登录，但 OAuth 回调需要动态 `code`，无法自动化。好在 **`POST /api/user/login`（用户名/邮箱 + 密码登录）无需人机验证**，只要账号设置了密码，脚本就能用它自动登录、触发签到。
3. 站点前置了**阿里云 WAF**，GitHub Actions 的海外 IP 会被拦截返回 JS 验证页（非 JSON）。因此需通过**代理**走国内/香港/新加坡等未被拦截的 IP。脚本支持配置**多个代理**并逐个尝试，用第一个能绕过 WAF 的。

## 一、准备工作

1. 用 GitHub 登录 https://agentrouter.org
2. **绑定邮箱 + 设置密码**：进入「个人设置」→ 绑定邮箱并设置登录密码。
   - GitHub 用户默认没有密码（且改密码会校验旧密码，形成死锁）。**通过「绑定/更换邮箱」即可获得设置密码的入口**。
   - 之后可用**邮箱**或**用户名**（形如 `github_<你的ID>`）作为登录账号。
3. **准备代理**：一个能访问该站点且未被 WAF 拦截的 SOCKS5 / HTTP 代理（国内 / 香港 / 新加坡 IP）。

## 二、配置 GitHub Actions

### Secrets（敏感信息）

**Settings → Secrets and variables → Actions → Secrets** 中添加：

| Secret 名称 | 值 | 说明 |
|-------------|-----|------|
| `TG_BOT_TOKEN` | `123456:ABC-DEF...` | Telegram Bot Token（可选） |
| `TG_CHAT_ID` | `123456789` | Telegram Chat ID（可选） |

### Variables（非敏感信息）

**Settings → Secrets and variables → Actions → Variables** 中添加：

| Variable 名称 | 值 | 说明 |
|---------------|-----|------|
| `AGENTROUTER_USERNAME` | `你的邮箱或用户名` | 登录账号，邮箱或 `github_<ID>`（**必需**） |
| `AGENTROUTER_PASSWORD` | `你的登录密码` | 登录密码（**必需**） |
| `SOCKS5_PROXY` | `socks5://user:pass@host:port` | 绕过 WAF 的代理（**必需**，见下） |
| `AGENTROUTER_BASE_URL` | `https://agentrouter.org` | 可不填，默认已设 |

### 代理配置格式（`SOCKS5_PROXY`）

- **格式**：`socks5://用户名:密码@主机:端口`（无需备注）
  - 支持 `socks5://`、`socks://`（自动转 `socks5h://`，DNS 也走代理）、`http://`
  - IPv6 主机需用方括号，如 `socks5://user:pass@[2922:...:3d7b]:39827`
- **多个代理**：用**换行**或**逗号**分隔，脚本会逐个尝试，用第一个能绕过 WAF 的；全部失败再尝试直连兜底。

> **代理是否必需？** 取决于**运行环境的 IP**，与登录方式无关：
> - **GitHub Actions**：**必须配代理**，否则被阿里云 WAF 拦截（即最初 `Expecting value: line 1 column 1` 报错的根源）。
> - **本地国内 IP**：可**不配**（直连即可）。
> 提示：IPv6 代理在 GitHub Actions 上出站支持不稳定，建议优先使用 IPv4 代理放在第一个。

### 触发方式

- **自动**：每天北京时间约 `10:15`（UTC `02:15`）定时运行
- **手动**：GitHub → Actions → **AgentRouter 每日签到** → **Run workflow**

## 三、签到流程

1. 解析 `SOCKS5_PROXY`，逐个尝试代理请求公开接口 `/api/status`，选出能绕过 WAF 的连接
2. 用该连接 `POST /api/user/login` 提交用户名/邮箱 + 密码登录（= 触发签到）
3. 用登录返回的 `access_token` 查询最新余额 `quota`
4. 发送 TG 通知

## 四、本地测试

```bash
cd agentrouter
pip install -r requirements.txt

# Windows PowerShell 示例（本地国内 IP 可省略 SOCKS5_PROXY）
$env:AGENTROUTER_USERNAME="你的邮箱或用户名"
$env:AGENTROUTER_PASSWORD="你的密码"
$env:SOCKS5_PROXY="socks5://user:pass@host:port"
python checkin.py
```

## 配额换算说明

脚本默认按 New-API 通用换算比例 `1 USD = 500000 quota` 计算余额。
如与实际不符，请修改 `checkin.py` 中的 `QUOTA_PER_UNIT` 常量。

## 常见问题

- **`Expecting value: line 1 column 1 (char 0)` / 日志出现 `aliyun_waf`**：请求被阿里云 WAF 拦截，说明当前 IP（或代理）不可用，请检查/更换 `SOCKS5_PROXY`。
- **`用户名或密码错误`**：确认已在网站「个人设置」绑定邮箱并设置了密码，且用户名/邮箱正确。

## TG 通知效果

### 今日已签到
```
**AgentRouter 签到通知**
----------------
📅 **日期**：2026年07月26日
👤 **用户**：admi●●●●●
✅ **签到**：今日已签到
💰 **余额**：$106.30
```

### 今日新签到
```
**AgentRouter 签到通知**
----------------
📅 **日期**：2026年07月26日
👤 **用户**：admi●●●●●
🎉 **签到**：签到成功，额度已到账
💰 **余额**：$106.30
```
