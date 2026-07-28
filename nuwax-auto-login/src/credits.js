/**
 * 积分查询模块
 * 登录后访问首页，提取积分数据
 */

const BALANCE_SELECTOR = 'span.balance-text___iJON5';

/**
 * 使用 Playwright 浏览器上下文访问首页提取积分
 * @param {import('playwright').BrowserContext} context - 浏览器上下文（已有 Cookie）
 * @returns {Promise<string|null>} 积分字符串，如 "2,797"
 */
export async function fetchCredits(context) {
  try {
    console.log('[credits] 正在查询积分...');
    const page = await context.newPage();

    await page.goto('https://agent.nuwax.com/home', {
      waitUntil: 'networkidle',
      timeout: 30000,
    });

    // 等待积分元素加载
    await page.waitForTimeout(2000);

    const balanceText = await page.evaluate((selector) => {
      const el = document.querySelector(selector);
      return el ? el.textContent?.trim() : null;
    }, BALANCE_SELECTOR);

    if (balanceText) {
      console.log(`[credits] 当前积分: ${balanceText}`);
    } else {
      console.log('[credits] 未找到积分元素（可能未登录或无权限）');
    }

    await page.close();
    return balanceText;
  } catch (err) {
    console.error('[credits] 查询积分异常:', err.message);
    return null;
  }
}

/**
 * 使用 Playwright 浏览器加载 Cookie 后查询积分
 * 解决 fetch 无法执行 JS 渲染、拿不到动态积分的问题
 * @param {string} cookieStr - JSON 序列化的 Cookie 字符串
 * @returns {Promise<string|null>} 积分字符串
 */
export async function fetchCreditsWithCookieViaBrowser(cookieStr) {
  if (!cookieStr) return null;

  let browser;
  try {
    const { chromium } = await import('playwright');

    const cookies = JSON.parse(cookieStr);
    if (!Array.isArray(cookies) || cookies.length === 0) {
      console.log('[credits] Cookie 格式无效或为空');
      return null;
    }

    console.log('[credits] 启动浏览器查询积分...');
    browser = await chromium.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
      ],
    });

    const context = await browser.newContext({
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      viewport: { width: 1280, height: 800 },
      locale: 'zh-CN',
    });

    // 注入已保存的 cookie，复用登录状态
    await context.addCookies(cookies);

    const page = await context.newPage();

    // 访问首页，等 JS 渲染完成
    await page.goto('https://agent.nuwax.com/home', {
      waitUntil: 'networkidle',
      timeout: 30000,
    });
    await page.waitForTimeout(2000);

    // 与 fetchCredits() 相同的 DOM 查询逻辑
    const balanceText = await page.evaluate((selector) => {
      const el = document.querySelector(selector);
      return el ? el.textContent?.trim() : null;
    }, BALANCE_SELECTOR);

    if (balanceText) {
      console.log(`[credits] 当前积分: ${balanceText}`);
    } else {
      console.log('[credits] 未找到积分元素（可能未登录或无权限）');
    }

    return balanceText;
  } catch (err) {
    console.error('[credits] 浏览器查询积分异常:', err.message);
    return null;
  } finally {
    if (browser) {
      try { await browser.close(); } catch (_) {}
    }
  }
}
