# nuwax-auto-login

自动登录 [nuwax.com](https://agent.nuwax.com) 女娲智能体OS，解决阿里云滑块验证码，自动查询积分并通知 TG。

## 功能

- ✅ 自动填写登录表单（手机号 + 密码）
- ✅ 阿里云滑块验证码类人轨迹自动滑动
- ✅ Cookie 跨运行持久化（GitHub Repository Variables）
- ✅ 定时自动运行（GitHub Actions，每天 UTC 16:00）
- ✅ 自动查询当前积分（`💰 积分: 3,000`）
- ✅ Telegram 通知登录结果 + 积分数据

## 目录结构

```
nuwax-auto-login/
├── src/
│   ├── index.js     # 主入口
│   ├── login.js     # Playwright 登录逻辑
│   ├── captcha.js   # 滑块验证码解决方案
│   ├── credits.js   # 积分查询
│   ├── cookie.js    # Cookie 持久化
│   └── notify.js    # Telegram 通知
├── package.json
└── README.md

.github/workflows/
  └── nuwax-login.yml
```

## GitHub Secrets 配置

在 GitHub 仓库 Settings → Secrets and variables → Actions 中配置：

| Secret | 说明 |
|--------|------|
| `NUWAX_PHONE` | 手机号 |
| `NUWAX_PASSWORD` | 密码 |
| `TG_BOT_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | 接收通知的 Chat ID |
| `GH_TOKEN` | GitHub PAT（权限: `repo` + `actions:write`） |

## GitHub Repository Variables 配置

| Variable | 说明 |
|----------|------|
| `NUWAX_COOKIE` | 登录后的 Cookie（脚本自动读写，首次为空） |

## 本地调试

```bash
cd nuwax-auto-login
npm install
npx playwright install chromium

# Linux/macOS
export NUWAX_PHONE="139*******"
export NUWAX_PASSWORD="your-password"
export TG_BOT_TOKEN="your-bot-token"
export TG_CHAT_ID="your-chat-id"
node src/index.js --debug

# Windows PowerShell
$env:NUWAX_PHONE="139*******"
$env:NUWAX_PASSWORD="your-password"
$env:TG_BOT_TOKEN="your-bot-token"
$env:TG_CHAT_ID="your-chat-id"
node src/index.js --debug
```

## TG 通知示例

```
✅ nuwax 自动登录成功
━━━━━━━━━━━━━━━━
🕐 时间: 2026/7/25 00:00:02
📋 状态: 已重新登录并续期 Cookie
💰 积分: 3,000
```

## 定时说明

- 默认每天 UTC 16:00（北京时间 00:00）运行
- 可通过 GitHub Actions 页面手动触发 `nuwax-login` workflow
- Cookie 有效时跳过登录，仅查询积分 + TG 通知
- Cookie 过期时自动执行完整登录流程
