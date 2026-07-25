/**
 * Cookie 持久化模块
 * 从环境变量 $NUWAX_COOKIE 读取，写回 GitHub Repository Variable
 */
import { execSync } from 'child_process';

/**
 * 从环境变量读取 Cookie
 * @returns {string|null} Cookie JSON 字符串或 null
 */
export function readCookieFromEnv() {
  const cookieStr = process.env.NUWAX_COOKIE;
  if (!cookieStr || cookieStr === '' || cookieStr === 'undefined') {
    console.log('[cookie] 环境变量中未找到 Cookie');
    return null;
  }
  console.log('[cookie] 从环境变量读取到 Cookie');
  return cookieStr;
}

/**
 * 使用 gh CLI 将 Cookie 写入 GitHub Repository Variable
 * @param {string} cookieStr - JSON 序列化的 Cookie 字符串
 */
export async function saveCookieToVariable(cookieStr) {
  if (!cookieStr) {
    console.warn('[cookie] Cookie 为空，跳过保存');
    return;
  }

  // 对特殊字符做转义，避免 shell 解析问题
  const escaped = cookieStr.replace(/'/g, "'\\''");

  const cmd = `gh variable set NUWAX_COOKIE --body '${escaped}'`;

  console.log('[cookie] 正在写入 GitHub Variable...');
  try {
    execSync(cmd, { stdio: 'pipe', timeout: 30000 });
    console.log('[cookie] GitHub Variable 写入成功');
  } catch (err) {
    console.error('[cookie] 写入 GitHub Variable 失败:', err.message);
    // 写入失败不阻断流程
  }
}

/**
 * 校验 Cookie 是否有效
 * 带 Cookie 请求首页，检查是否跳转到登录页
 * @param {string} cookieStr - JSON 序列化的 Cookie 字符串
 * @returns {boolean} 是否有效
 */
export async function validateCookie(cookieStr) {
  if (!cookieStr) return false;

  try {
    const cookies = JSON.parse(cookieStr);

    // 使用 fetch 带 Cookie 请求首页
    const res = await fetch('https://agent.nuwax.com/login', {
      headers: {
        'Cookie': cookies.map(c => `${c.name}=${c.value}`).join('; '),
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      },
      redirect: 'manual',
    });

    // 如果正常返回页面内容（非登录页）=> Cookie 有效
    const text = await res.text();

    if (res.status === 200 && !text.includes('密码登录') && !text.includes('登录/注册')) {
      console.log('[cookie] Cookie 有效，无需重新登录');
      return true;
    }

    console.log('[cookie] Cookie 已过期或无效');
    return false;
  } catch (err) {
    console.error('[cookie] 校验失败:', err.message);
    return false;
  }
}