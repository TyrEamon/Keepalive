# AgentRouter 每日签到

自动登录 [AgentRouter](https://agentrouter.org) 站点，每日签到领取免费额度，并通过 Telegram Bot 发送通知。

> 本项目基于同类 New-API / One-API 站点的签到接口实现，与 `iamhc` 项目结构一致。

## 目录结构

```
├── .github/workflows/agentrouter-checkin.yml   # GitHub Actions 定时工作流
├── checkin.py                                   # 主签到脚本
├── notify.py                                    # TG 通知组件（独立）
├── requirements.txt                             # Python 依赖
└── README.md                                    # 本文件
```

## 配置

### Variables（全部放入 Variables，无需 Secrets）

在仓库 **Settings → Secrets and variables → Actions → Variables** 中添加：

| Variable 名称 | 值 | 说明 |
|---------------|-----|------|
| `AGENTROUTER_BASE_URL` | `https://agentrouter.org` | 可不填，默认已设 |
| `AGENTROUTER_USER_ID` | `******` | 用户数字 ID |
| `AGENTROUTER_USERNAME` | `*********` | 登录用户名 |
| `AGENTROUTER_PASSWORD` | `************` | 登录密码 |

> `AGENTROUTER_SESSION_COOKIE` 变量由脚本首次运行时通过 `gh variable set` **自动创建**，无需手动添加。

#### 如何获取 `AGENTROUTER_USER_ID`

登录网站后，在浏览器开发者工具（F12）→ Network 中查看任意 `/api/user/...` 请求的请求头 `New-Api-User`，其值即为用户数字 ID。

### Personal Access Token（必需，用于写回 Variables）

`gh variable set` 需要 PAT 才能写入 Variables，在仓库 **Settings → Secrets and variables → Actions → Secrets** 中添加：

| Secret 名称 | 值 | 说明 |
|-------------|-----|------|
| `GH_TOKEN` | `ghp_xxxx...` | GitHub PAT，需 `repo` 或 `actions-variables: write` 权限 |

> **创建 PAT**：GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → 选择目标仓库 → 勾选 **Actions → Variables: write** 权限

### TG 通知（可选）

| Secret 名称 | 值 | 说明 |
|-------------|-----|------|
| `TG_BOT_TOKEN` | `123456:ABC-DEF...` | Telegram Bot Token（不配置则跳过通知） |
| `TG_CHAT_ID` | `123456789` | Telegram Chat ID（不配置则跳过通知）|

### 触发方式

- **自动**：每天北京时间 `10:00`（UTC `02:00`）定时运行
- **手动**：GitHub → Actions → **AgentRouter 每日签到** → **Run workflow**

## 工作流程

```
首次运行
  └─ AGENTROUTER_SESSION_COOKIE 为空 → 登录 → 签到 → 查余额
       → 编码 session 到 session.cookie.b64
       → gh variable set AGENTROUTER_SESSION_COOKIE ← 写回 Variables

后续运行
  └─ ${{ vars.AGENTROUTER_SESSION_COOKIE }} → 解码恢复 session
       → 有效 → 跳过登录；无效 → 重新登录
       → 签到 → 查余额
       → 再次编码 session，更新 Variables
```

## 签到逻辑

1. 从 `AGENTROUTER_SESSION_COOKIE` 变量恢复 session cookie
2. 如果失效，用用户名密码登录（`POST /api/user/login`）
3. 查询签到状态（`GET /api/user/checkin`）
4. 未签到则执行签到（`POST /api/user/checkin`）
5. 获取当前余额（`GET /api/user/self`）
6. 将 session 编码写回 `AGENTROUTER_SESSION_COOKIE` 变量
7. 发送 TG 通知

## 本地测试

```bash
cd agentrouter
pip install -r requirements.txt

# 设置环境变量后运行
export AGENTROUTER_USERNAME="你的用户名"
export AGENTROUTER_PASSWORD="你的密码"
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
👤 **用户**：yuti●●●●●
✅ **签到**：今日已签到
💰 **余额**：$5,746.24
```

### 今日新签到
```
**AgentRouter 签到通知**
----------------
📅 **日期**：2026年07月26日
👤 **用户**：yuti●●●●●
🎉 **签到**：获得奖励 $1,748.25
💰 **余额**：$6,500.00
```
