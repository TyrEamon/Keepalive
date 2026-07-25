/**
 * Playwright 登录逻辑
 * 启动浏览器 → 拦截行为数据上报 → 填写表单 → 触发滑块 → 类人拖拽 → 登录成功 → 提取 Cookie
 */
import { chromium } from 'playwright';
import { solveSlider } from './captcha.js';

/**
 * 执行登录流程
 * @param {object} options
 * @returns {Promise<{success: boolean, cookies: string|null, context: object|null}>}
 */
export async function doLogin({ phone, password, debug = false }) {
  let browser;
  let context;
  let screenshotPath = null;

  try {
    console.log('[login] 启动 Chromium...');
    browser = await chromium.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-blink-features=AutomationControlled',
      ],
    });

    context = await browser.newContext({
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      viewport: { width: 1280, height: 800 },
      locale: 'zh-CN',
    });

    // 隐藏自动化检测特征
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => false });
      window.chrome = { runtime: {} };
    });

    const page = await context.newPage();

    // 拦截 Aliyun CAPTCHA 行为数据上报（关键：防止自动化检测）
    await page.route('**/upload.captcha-open.aliyuncs.com/**', (route) => {
      route.abort();
    });
    await page.route('**/upload.captcha-open-b.aliyuncs.com/**', (route) => {
      route.abort();
    });
    // 拦截设备指纹采集
    await page.route('**/cloudauth-device*', (route) => {
      route.abort();
    });

    // 导航到登录页
    console.log('[login] 正在访问登录页...');
    await page.goto('https://agent.nuwax.com/login', {
      waitUntil: 'networkidle',
      timeout: 30000,
    });
    await page.waitForTimeout(2000);

    // 填写手机号
    console.log('[login] 填写手机号...');
    const phoneInput = page.locator('#login_phoneOrEmail');
    await phoneInput.waitFor({ state: 'visible', timeout: 10000 });
    await phoneInput.click();
    await phoneInput.fill('');
    await phoneInput.fill(phone);
    await page.waitForTimeout(300);

    // 填写密码
    console.log('[login] 填写密码...');
    const pwdInput = page.locator('#login_password');
    await pwdInput.waitFor({ state: 'visible', timeout: 5000 });
    await pwdInput.click();
    await pwdInput.fill('');
    await pwdInput.fill(password);
    await page.waitForTimeout(300);

    // 点击登录按钮
    console.log('[login] 点击登录按钮...');
    const loginBtn = page.locator('button.ant-btn-primary');
    await loginBtn.waitFor({ state: 'visible', timeout: 5000 });
    await loginBtn.click();

    // 等待滑块加载
    console.log('[login] 等待滑块验证码加载...');
    await page.waitForTimeout(3000);

    // 解决滑块验证码（最多重试 2 次）
    let captchaSuccess = false;
    for (let attempt = 1; attempt <= 2; attempt++) {
      console.log(`[login] 滑块尝试第 ${attempt} 次...`);
      captchaSuccess = await solveSlider(page);
      if (captchaSuccess) break;
      await page.waitForTimeout(2000);
    }

    if (!captchaSuccess) {
      if (debug) {
        screenshotPath = `screenshots/fail-${Date.now()}.png`;
        await page.screenshot({ path: screenshotPath, fullPage: true });
      }
      await browser.close();
      return { success: false, cookies: null, screenshotPath, context: null };
    }

    // 等待页面自动跳转（验证通过后会自动提交表单）
    console.log('[login] 等待登录完成...');
    let redirectHappened = false;
    for (let i = 0; i < 10; i++) {
      await page.waitForTimeout(1000);
      const url = page.url();
      if (!url.includes('/login')) {
        console.log(`[login] 页面已跳转到: ${url}`);
        redirectHappened = true;
        break;
      }
    }

    // 如果没自动跳转，手动点击登录按钮
    if (!redirectHappened) {
      console.log('[login] 尝试手动点击登录按钮...');
      try {
        const btn = page.locator('button.ant-btn-primary');
        const isEnabled = await btn.isEnabled();
        if (isEnabled) {
          await btn.click({ timeout: 5000 });
          await page.waitForTimeout(3000);
        }
      } catch (e) {
        console.log('[login] 手动点击失败:', e.message);
      }
    }

    // 提取 Cookie
    const cookies = await context.cookies();
    const cookieStr = JSON.stringify(cookies);

    console.log(`[login] 登录成功，提取到 ${cookies.length} 个 Cookie`);

    // 不关闭浏览器，让调用者用 context 查积分
    return { success: true, cookies: cookieStr, screenshotPath: null, context };
  } catch (err) {
    console.error('[login] 登录异常:', err.message);
    if (browser && debug) {
      try {
        const pages = context?.pages();
        if (pages?.length > 0) {
          screenshotPath = `screenshots/error-${Date.now()}.png`;
          await pages[0].screenshot({ path: screenshotPath, fullPage: true });
        }
      } catch (_) {}
    }
    if (browser) await browser.close();
    return { success: false, cookies: null, screenshotPath, context: null };
  }
}