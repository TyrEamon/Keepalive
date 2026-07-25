/**
 * 阿里云滑块验证码解决方案
 * 使用 page.mouse 类人轨迹拖拽 + 拦截行为数据上报
 */

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/**
 * 生成贝塞尔曲线路径点（更自然的曲线）
 */
function generateBezierPath(fromX, toX, startY, steps = 55) {
  const points = [];
  const distance = toX - fromX;

  // 控制点：更自然的曲线，Y 轴有轻微波动
  const cp1x = fromX + distance * (0.2 + Math.random() * 0.15);
  const cp1y = (Math.random() - 0.5) * 8;
  const cp2x = fromX + distance * (0.65 + Math.random() * 0.15);
  const cp2y = (Math.random() - 0.5) * 8;

  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const x = Math.pow(1 - t, 3) * fromX +
      3 * Math.pow(1 - t, 2) * t * cp1x +
      3 * (1 - t) * Math.pow(t, 2) * cp2x +
      Math.pow(t, 3) * toX;
    const y = Math.pow(1 - t, 3) * startY +
      3 * Math.pow(1 - t, 2) * t * cp1y +
      3 * (1 - t) * Math.pow(t, 2) * cp2y +
      Math.pow(t, 3) * startY;
    points.push({ x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 });
  }
  return points;
}

/**
 * 获取步进延迟（模拟人类变速 - 更自然）
 */
function getStepDelay(totalSteps, currentStep) {
  const progress = currentStep / totalSteps;
  // 起始阶段：慢（犹豫），中段：快（果断拖拽），末段：慢（微调）
  if (progress < 0.15) return 15 + Math.random() * 10;     // 起始犹豫
  if (progress < 0.3) return 10 + Math.random() * 6;       // 加速
  if (progress < 0.7) return 5 + Math.random() * 4;        // 高速拖拽
  if (progress < 0.9) return 8 + Math.random() * 6;        // 减速
  return 16 + Math.random() * 14;                           // 终点微调
}

/**
 * 解决阿里云滑块验证码
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

    // 1. 鼠标移到滑块上方
    await page.mouse.move(
      startX + (Math.random() - 0.5) * 3,
      startY + (Math.random() - 0.5) * 3,
      { steps: 5 }
    );
    await sleep(150 + Math.random() * 200);

    // 2. 按下鼠标
    await page.mouse.down();
    await sleep(40 + Math.random() * 60);

    // 3. 贝塞尔路径拖拽
    const pathPoints = generateBezierPath(startX, targetX);
    for (let i = 0; i < pathPoints.length; i++) {
      const point = pathPoints[i];
      const jitterX = (Math.random() - 0.5) * 1.5;
      const jitterY = (Math.random() - 0.5) * 1.5;
      await page.mouse.move(point.x + jitterX, startY + point.y + jitterY, { steps: 1 });
      await sleep(getStepDelay(pathPoints.length, i));
    }

    // 4. 终点微调
    await sleep(80 + Math.random() * 100);
    await page.mouse.move(targetX + (Math.random() - 0.5) * 2, startY, { steps: 2 });
    await sleep(60 + Math.random() * 80);

    // 5. 释放鼠标
    await page.mouse.up();
    console.log('[captcha] 鼠标释放');

    // 6. 等待验证结果
    await sleep(3000);

    // 7. 检查验证是否通过（mask 消失或滑块消失）
    const passed = await page.evaluate(() => {
      const mask = document.querySelector('#aliyunCaptcha-mask');
      if (!mask) return true; // mask 不存在 = 通过
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
