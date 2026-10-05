# IAMHC 多账号每日签到

给 **https://api.hcnsec.cn**（新疆幻城）做每日自动签到。

基于同仓库 `gorouter/checkin.py` 改写，认证方式改为**用 `new_api_refresh` 换 access token**。

---

## 为什么不用账号密码登录

该站的 `/api/user/login` 现在要求 **Cloudflare Turnstile token**。脚本拿不到这个 token，
所以密码登录这条路走不通 —— 旧版每次都会以 `登录失败：Turnstile token 为空` 结束，
还让工作流天天报红。

**改用 refresh token 之后完全不需要登录**：只要 refresh 有效，直接就能换 access token、查余额、签到、
并在每次运行后把服务端刷新的 cookie 自动写回 Secret。

---

## 需要配置的东西

### Secret：`IAMHC_ACCOUNTS_JSON`

```json
[
  {
    "name": "tyreamon",
    "user_id": "64195",
    "refresh": "把这里换成 new_api_refresh 的值",
    "enabled": true
  },
  {
    "name": "tyrge01",
    "user_id": "64406",
    "refresh": "另一个账号的 new_api_refresh",
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
| `refresh` | **是**（新版站点） | `new_api_refresh` cookie 的值，脚本用它换 access token，**每次运行都会轮换并自动写回** |
| `session` | 否 | 旧版 session cookie，站点已改用 refresh，仅作兼容保留 |
| `base_url` | 否 | 覆盖默认站点，默认 `https://api.hcnsec.cn` |
| `token` | 否 | 可选的 Bearer Token |
| `enabled` | 否 | 填 `false` 跳过该账号（会被保留，不会被删掉） |

> `session_b64`（旧版格式）仍然兼容，首次成功运行后会自动转成 `session`。

### Secret：`TG_BOT_TOKEN` / `TG_CHAT_ID`

Telegram 通知，可选。不配就跳过通知。

### Secret：`GH_TOKEN`

用于把轮换后的 refresh 写回 `IAMHC_ACCOUNTS_JSON`。
需要 fine-grained PAT，权限：**Secrets: Read and write**。

### Variable：`IAMHC_BASE_URL`

可选，不填默认 `https://api.hcnsec.cn`。

---

## 怎么拿 refresh（**唯一需要手动做的事**）

站点现在不用 `session` cookie 了，登录后只有两个 cookie：`new_api_refresh`（HttpOnly）和
`new_api_has_session`（值恒为 1，没用）。要的是 **`new_api_refresh`**。

1. 开一个**无痕窗口**打开 https://api.hcnsec.cn 并登录（人机验证自己过）
2. 登录完成后再开 F12 → **Application（应用程序）** → `Cookies` → `https://api.hcnsec.cn`
3. 复制 **`new_api_refresh`** 的 Value（整串，含中间的 `.`）
4. 粘贴进 Secret 里对应账号的 `refresh` 字段
5. **关掉无痕窗口，不要点退出登录，也不要再用这个窗口访问站点**

> ⚠️ refresh 是**一次一换**的：任何一次刷新（包括你自己的浏览器页面加载）都会让旧值作废。
> 所以别用日常浏览器的登录态去取，否则你一刷新页面，Secret 里的值就失效了。
>
> ⚠️ 别开着 F12 去点签到 —— 该站的 Turnstile 带反调试，开着 DevTools 会让验证失败。

---

## 多久换一次

每次运行时，脚本先用 `refresh` 调 `/api/user/auth/refresh` 换 access token，再用它签到，
同时把服务端轮换出的新 `refresh` 写回 Secret（需要 `GH_TOKEN`，没配的话下次就会失效）。

**登录会话有固定寿命：refresh 响应里的 `session.expires_at` 比 `created_at` 晚 30 天，
轮换后的 cookie `Expires` 也落在同一个绝对时间，所以刷新大概率不会延长它**（根据一次抓包推断，
还没有跨 30 天验证过）。到期后
Telegram 会收到「🔑 登录凭证已失效，需要你更新」，按上面的步骤重新取一次 `refresh` 即可。

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
　📋 无痕窗口重新登录后，取 Cookies 里的 new_api_refresh 值
　⚙️ 更新 Secret IAMHC_ACCOUNTS_JSON 中该账号的 refresh 字段
```

---

## 和旧版的差别

| | 旧版 | 本版 |
|---|---|---|
| 认证 | session → 失败后密码登录 | **refresh 换 access token**，不做密码登录 |
| Turnstile | 卡在 `token 为空` | 签到被拦截时，用 seleniumbase 浏览器取 token 后重试（`turnstile.py`） |
| 依赖 | `requests` + `seleniumbase` | `requests` + `pysocks` + `seleniumbase`（seleniumbase 仅在被拦截时才导入） |
| 凭证失效 | 报错 + 工作流报红 | **Telegram 提醒 + 退出码 0** |
| 写回 Secret | 保留 disabled 账号 | 保留（同样） |
| 每账号 base_url | 支持 | 支持 |

登录接口本身仍然要 Turnstile，所以 refresh 失效后依旧需要人工换；
`turnstile.py` 只负责「已登录、但签到接口要 Turnstile token」这一种情况。
代理由 workflow 的 `IAMHC_USE_PROXY` Variable 和 `IAMHC_PROXY_SERVER` Secret 控制，默认关闭（Actions 直连）。
需要非直连时，在仓库 Settings → Secrets and variables → Actions 中设置：

- Variable：`IAMHC_USE_PROXY` = `true`
- Secret：`IAMHC_PROXY_SERVER` = 完整代理地址，例如 `socks://用户:密码@主机:端口#us`

代码会自动把 `socks://` 转成 `socks5://`，并忽略末尾的 `#us` 标签。代理凭证不要写入 workflow 或提交到仓库。
