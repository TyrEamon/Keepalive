# IAMHC 多账号每日签到

给 **https://api.hcnsec.cn**（新疆幻城）做每日自动签到。

基于同仓库 `gorouter/checkin.py` 改写，认证方式改为**只用 session Cookie**。

---

## 为什么不用账号密码登录

该站的 `/api/user/login` 现在要求 **Cloudflare Turnstile token**。脚本拿不到这个 token，
所以密码登录这条路走不通 —— 旧版每次都会以 `登录失败：Turnstile token 为空` 结束，
还让工作流天天报红。

**改用 session Cookie 之后完全不需要登录**：只要 cookie 有效，直接就能查余额、签到、
并在每次运行后把服务端刷新的 cookie 自动写回 Secret。

---

## 需要配置的东西

### Secret：`IAMHC_ACCOUNTS_JSON`

```json
[
  {
    "name": "tyreamon",
    "user_id": "64195",
    "session": "把这里换成新的 session 值",
    "enabled": true
  },
  {
    "name": "tyrge01",
    "user_id": "64406",
    "session": "另一个账号的 session",
    "enabled": true
  },
  {
    "name": "账号3",
    "user_id": "xxxxx",
    "enabled": false
  }
]
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 否 | 备注名，只用于日志和通知 |
| `user_id` | **是** | 站点用户 ID，会作为 `New-Api-User` 请求头 |
| `session` | **是** | session Cookie 值 |
| `base_url` | 否 | 覆盖默认站点，默认 `https://api.hcnsec.cn` |
| `token` | 否 | 可选的 Bearer Token |
| `enabled` | 否 | 填 `false` 跳过该账号（会被保留，不会被删掉） |

> `session_b64`（旧版格式）仍然兼容，首次成功运行后会自动转成 `session`。

### Secret：`TG_BOT_TOKEN` / `TG_CHAT_ID`

Telegram 通知，可选。不配就跳过通知。

### Secret：`GH_TOKEN`

用于把刷新后的 session 写回 `IAMHC_ACCOUNTS_JSON`。
需要 fine-grained PAT，权限：**Secrets: Read and write**。

### Variable：`IAMHC_BASE_URL`

可选，不填默认 `https://api.hcnsec.cn`。

---

## 怎么拿 session（**唯一需要手动做的事**）

1. 浏览器正常打开 https://api.hcnsec.cn 并**登录**（人机验证自己过）
2. 登录完成后再开 F12 → **Application（应用程序）**
3. 左侧 `Storage` → `Cookies` → `https://api.hcnsec.cn`
4. 找到名为 **`session`** 的那一行，复制它的 **Value**
5. 粘贴进 Secret 里对应账号的 `session` 字段

> ⚠️ **别用 `document.cookie`** —— `session` 是 HttpOnly，JS 读不到，必须从 Application 面板复制。
>
> ⚠️ **别开着 F12 去点签到** —— 该站的 Turnstile 带反调试，开着 DevTools 会让验证失败。
> 顺序是：**先登录 → 再开 F12 拿 cookie → 关掉 F12**。

---

## 多久换一次

不需要经常换。每次运行成功后，脚本会把服务端刷新的 session 写回 Secret，
正常情况下能一直自动续期。

**只有当 cookie 真的失效时**，Telegram 会收到一条高亮的
「🔑 Session 已失效，需要你更新」，你按上面的步骤换一次即可。

这种时候工作流**不会报红**（退出码为 0），属于正常状态。

---

## 通知长什么样

```
IAMHC AI 多账号签到
📅 2026年10月04日

🎉 tyreamon：签到成功，获得 $0.25
　💰 余额：$6.57

✅ tyrge01：今日已签到
　💰 余额：$3.20
----------------
成功：2　失败：0　总计：2
```

需要换 cookie 时，标题会多一行：

```
🔑 1 个账号需要更新 Session

🔑 tyreamon：Session 已失效，需要你更新
　📋 浏览器登录后取 Cookies 里的 session 值
　⚙️ 更新 Secret IAMHC_ACCOUNTS_JSON 中该账号的 session 字段
```

---

## 和旧版的差别

| | 旧版 | 本版 |
|---|---|---|
| 认证 | session → 失败后密码登录 | **只用 session**，不做密码登录 |
| Turnstile | 卡在 `token 为空` | 签到被拦截时，用 seleniumbase 浏览器取 token 后重试（`turnstile.py`） |
| 依赖 | `requests` + `seleniumbase` | `requests` + `pysocks` + `seleniumbase`（seleniumbase 仅在被拦截时才导入） |
| session 失效 | 报错 + 工作流报红 | **Telegram 提醒 + 退出码 0** |
| 写回 Secret | 保留 disabled 账号 | 保留（同样） |
| 每账号 base_url | 支持 | 支持 |

登录接口本身仍然要 Turnstile，所以 session 失效后依旧需要人工换；
`turnstile.py` 只负责「已登录、但签到接口要 Turnstile token」这一种情况。
代理由环境变量 `IS_PROXY` / `PROXY_SERVER` 控制，默认关闭（Actions 直连）。
