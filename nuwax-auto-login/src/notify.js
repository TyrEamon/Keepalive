/**
 * Telegram 通知模块
 * 通过 TG Bot API 发送登录结果通知
 */

/**
 * 发送 TG 通知
 * @param {object} options
 * @param {string} options.status - 'success' | 'fail'
 * @param {string} options.message - 消息内容
 * @param {string} [options.credit] - 积分值（可选）
 * @param {string} [options.screenshotUrl] - 截图链接（可选）
 */
export async function sendNotify({ status, message, credit, screenshotUrl }) {
  const botToken = process.env.TG_BOT_TOKEN;
  const chatId = process.env.TG_CHAT_ID;

  if (!botToken || !chatId) {
    console.warn('[notify] TG_BOT_TOKEN 或 TG_CHAT_ID 未设置，跳过通知');
    return;
  }

  const emoji = status === 'success' ? '✅' : '❌';
  const now = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });

  let text = `${emoji} nuwax 自动登录${status === 'success' ? '成功' : '失败'}\n`;
  text += `━━━━━━━━━━━━━━━━\n`;
  text += `🕐 时间: ${now}\n`;
  text += `📋 状态: ${message}\n`;

  if (credit) {
    text += `💰 积分: ${credit}\n`;
  }

  if (screenshotUrl) {
    text += `📸 截图: ${screenshotUrl}`;
  }

  const url = `https://api.telegram.org/bot${botToken}/sendMessage`;

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        disable_web_page_preview: false,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      console.error('[notify] TG 发送失败:', data);
    } else {
      console.log('[notify] TG 通知已发送');
    }
  } catch (err) {
    console.error('[notify] TG 通知异常:', err.message);
  }
}