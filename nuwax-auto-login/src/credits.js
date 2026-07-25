/**
 * 积分查询模块
 * 登录后访问首页，提取余额数据
 */

const BALANCE_SELECTOR = 'span.balance-text___iJON5';

/**
 * 使用 Playwright 浏览器上下文访问首页提取余额
 * @param {import('playwright').BrowserContext} context - 浏览器上下文（已有 Cookie）
 * @returns {Promise<string|null>} 余额字符串，如 "2,797"
 */
export async function fetchCredits(context) {
  try {
    console.log('[credits] 正在查询余额...');
    const page = await context.newPage();

    await page.goto('https://agent.nuwax.com/home', {
      waitUntil: 'networkidle',
      timeout: 30000,
    });

    // 等待余额元素加载
    await page.waitForTimeout(2000);

    const balanceText = await page.evaluate((selector) => {
      const el = document.querySelector(selector);
      return el ? el.textContent?.trim() : null;
    }, BALANCE_SELECTOR);

    if (balanceText) {
      console.log(`[credits] 当前余额: ${balanceText}`);
    } else {
      console.log('[credits] 未找到余额元素（可能未登录或无权限）');
    }

    await page.close();
    return balanceText;
  } catch (err) {
    console.error('[credits] 查询余额异常:', err.message);
    return null;
  }
}

/**
 * 使用 Cookie 字符串通过 fetch 查询余额
 * @param {string} cookieStr - JSON 序列化的 Cookie 字符串
 * @returns {Promise<string|null>} 余额字符串
 */
export async function fetchCreditsWithCookie(cookieStr) {
  if (!cookieStr) return null;

  try {
    const cookies = JSON.parse(cookieStr);
    const cookieHeader = cookies
      .map((c) => `${c.name}=${c.value}`)
      .join('; ');

    const res = await fetch('https://agent.nuwax.com/home', {
      headers: {
        Cookie: cookieHeader,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      },
    });

    const html = await res.text();

    // 从 HTML 中提取余额（class 可能被哈希处理，用包含 balance-text 的类名匹配）
    const match = html.match(/balance-text[^>]*>([^<]+)</);
    if (match) {
      const balance = match[1].trim();
      console.log(`[credits] 当前余额: ${balance}`);
      return balance;
    }

    console.log('[credits] 未从页面找到余额数据');
    return null;
  } catch (err) {
    console.error('[credits] fetch 查询余额异常:', err.message);
    return null;
  }
}
