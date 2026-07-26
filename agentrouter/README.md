# AgentRouter 每日签到

自动为 [AgentRouter](https://agentrouter.org) 站点每日签到领取免费额度，并通过 Telegram Bot 发送通知。

> AgentRouter 使用 **GitHub OAuth 登录**，无法用账号密码自动登录。
> 因此本项目改用站点「个人设置 → 系统访问令牌」生成的 **Access Token** 免登录调用 API，令牌长期有效、稳定可靠。

## 一、获取访问令牌与用户 ID

1. 用 GitHub 登录 https://agentrouter.org
2. **访问令牌**：进入「个人设置 / 我的令牌 / API 令牌」页面 → 生成一个系统访问令牌（形如`As1Z****************==`），复制备用。
3. **用户 ID**：个人设置——用户名下面，有ID值。

## 二、配置 GitHub Actions

### Secrets（敏感信息）

在仓库 **Settings → Secrets and variables → Actions → Secrets** 中添加：

| Secret 名称 | 值 | 说明 |
|-------------|-----|------|
| `AGENTROUTER_ACCESS_TOKEN` | `As1Z****************==` | 系统访问令牌（**必需**） |
| `TG_BOT_TOKEN` | `123456:ABC-DEF...` | Telegram Bot Token（可选，不配置则跳过通知） |
| `TG_CHAT_ID` | `123456789` | Telegram Chat ID（可选，不配置则跳过通知） |

### Variables（非敏感信息）

在仓库 **Settings → Secrets and variables → Actions → Variables** 中添加：

| Variable 名称 | 值 | 说明 |
|---------------|-----|------|
| `AGENTROUTER_USER_ID` | `******` | 用户数字 ID（**必需**） |
| `AGENTROUTER_BASE_URL` | `https://agentrouter.org` | 可不填，默认已设 |

> 因为不再需要模拟登录，也就不需要 `GH_TOKEN` 与 session 写回逻辑，配置更简单。

### 触发方式

- **自动**：每天北京时间 `10:00`（UTC `02:00`）定时运行
- **手动**：GitHub → Actions → **AgentRouter 每日签到** → **Run workflow**

## 三、签到逻辑

1. 用 `AGENTROUTER_ACCESS_TOKEN` 作为 `Authorization: Bearer` 头，`AGENTROUTER_USER_ID` 作为 `New-Api-User` 头
2. 校验令牌有效性（`GET /api/user/self`）
3. 查询签到状态（`GET /api/user/checkin`）
4. 未签到则执行签到（`POST /api/user/checkin`）
5. 获取当前余额（`GET /api/user/self`）
6. 发送 TG 通知

## 四、本地测试

```bash
cd agentrouter
pip install -r requirements.txt

# 设置环境变量后运行
export AGENTROUTER_ACCESS_TOKEN="你的访问令牌"
export AGENTROUTER_USER_ID="你的用户ID"
python checkin.py
```

## 配额换算说明

脚本默认按 New-API 通用换算比例 `1 USD = 500000 quota` 计算余额与奖励金额。
如果 AgentRouter 实际比例不同，请修改 `checkin.py` 中的 `QUOTA_PER_UNIT` 常量。

## TG 通知效果

### 今日已签到
```
**AgentRouter 签到通知**
----------------
📅 **日期**：2026年07月26日
👤 **用户**：your_name
✅ **签到**：今日已签到
💰 **余额**：$5,746.24
```

### 今日新签到
```
**AgentRouter 签到通知**
----------------
📅 **日期**：2026年07月26日
👤 **用户**：your_name
🎉 **签到**：获得奖励 $1,748.25
💰 **余额**：$6,500.00
```
