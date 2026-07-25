/**
 * 阿里云滑块验证码解决方案
 * 使用 page.mouse 类人轨迹拖拽 + 拦截行为数据上报
 */

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/**
 * 解决阿里云滑块验证码
 * 使用简化但可靠的类人拖拽方式
 * @param {import('playwright').Page} page
 * @returns {Promise<boolean>}
 */
export async function solveSlider(page) {
  try {
    console.log('[captcha] 等待滑块出现...');
    await page.waitForSelector('#aliyunCaptcha-sliding-slider', { timeout: 15000 });
    await sleep(1500);
    console.log('[captcha] 滑块已出现');

    // 计算精确拖拽距离（动态获取）
    const { startX, startY, targetX, distance } = await page.evaluate(() => {
      const container = document.querySelector('.aliyun-captcha');
      const slider = document.querySelector('#aliyunCaptcha-sliding-slider');
      if (!container || !slider) return {};
      const cr = container.getBoundingClientRect();
      const sr = slider.getBoundingClientRect();
      return {
        startX: sr.x + sr.width / 2,
        startY: sr.y + sr.height / 2,
        targetX: cr.x + cr.width - sr.width / 2,
        distance: cr.x + cr.width - sr.width / 2 - (sr.x + sr.width / 2),
      };
    });

    if (!distance) throw new Error('无法计算滑块距离');
    console.log(`[captcha] 距离: ${Math.round(distance)}px`);

    // 1. 鼠标移到滑块上方（带小偏移）
    await page.mouse.move(
      startX + (Math.random() - 0.5) * 5,
      startY + (Math.random() - 0.5) * 5
    );
    await sleep(200 + Math.random() * 300);

    // 2. 按下鼠标
    await page.mouse.down();
    await sleep(50 + Math.random() * 80);

    // 3. 自然拖拽（渐出曲线 + 正弦波 Y 轴摆动）
    const steps = 60;
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      // ease-out 缓动（起始快，终点慢）
      const easeOut = 1 - Math.pow(1 - t, 3);
      const x = startX + (targetX - startX) * easeOut;
      // 正弦波 Y 轴摆动 + 随机抖动
      const yOffset = Math.sin(t * Math.PI) * 4;
      const jitter = (Math.random() - 0.5) * 2;
      const y = startY + yOffset + jitter;

      await page.mouse.move(x, y);

      // 变速延迟：起始慢 → 中段快 → 终点慢
      let delay;
      if (t < 0.15) delay = 15 + Math.random() * 10;
      else if (t < 0.7) delay = 6 + Math.random() * 4;
      else delay = 12 + Math.random() * 10;
      await sleep(delay);
    }

    // 4. 终点微调
    await sleep(100 + Math.random() * 150);
    await page.mouse.move(targetX + (Math.random() - 0.5) * 3, startY + (Math.random() - 0.5) * 2);
    await sleep(80 + Math.random() * 100);

    // 5. 释放鼠标
    await page.mouse.up();
    console.log('[captcha] 鼠标释放');

    // 6. 等待验证结果
    await sleep(3000);

    // 7. 检查验证是否通过
    const passed = await page.evaluate(() => {
      const mask = document.querySelector('#aliyunCaptcha-mask');
      if (!mask) return true;
      return mask.className.includes('hidden') || !mask.className.includes('show');
    });

    if (passed) {
      console.log('[captcha] ✅ 验证通过');
      return true;
    }

    console.log('[captcha] ❌ 验证未通过');
    return false;
  } catch (err) {
    console.error('[captcha] 异常:', err.message);
    return false;
  }
}
