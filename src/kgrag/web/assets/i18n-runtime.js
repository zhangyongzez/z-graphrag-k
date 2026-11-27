/* i18n 运行时：语言切换、翻译、DOM 自动替换
 *
 * 用法：
 *   t('nav.dashboard')                    -> "看板"
 *   t('kb.load_failed', {msg: 'x'})       -> "加载失败：x"
 *   <span data-i18n="nav.dashboard"></span>         文本
 *   <input data-i18n-placeholder="login.username">  placeholder
 *   <button data-i18n-title="common.close">         title
 *   setLang('en')                                   切换语言
 */

const SUPPORTED_LANGS = ["zh", "en"];
const LANG_STORAGE_KEY = "kgrag_lang";

function detectLang() {
  const saved = localStorage.getItem(LANG_STORAGE_KEY);
  if (saved && SUPPORTED_LANGS.includes(saved)) return saved;
  // 根据浏览器语言自动判断
  const nav = (navigator.language || navigator.userLanguage || "zh").toLowerCase();
  return nav.startsWith("zh") ? "zh" : "en";
}

let currentLang = detectLang();

/** 取当前语言下的文案，缺失时回退到中文，再回退到 key 本身 */
function t(key, vars) {
  const table = I18N[currentLang] || I18N.zh;
  let text = table[key];
  if (text === undefined) text = I18N.zh[key];
  if (text === undefined) return key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      text = text.replaceAll(`{${k}}`, v);
    }
  }
  return text;
}

/** 把 DOM 里所有 data-i18n* 属性替换成当前语言 */
function applyI18n(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  root.querySelectorAll("[data-i18n-html]").forEach((el) => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.documentElement.lang = currentLang === "zh" ? "zh-CN" : "en";
}

/** 切换语言并刷新页面上的所有文案 */
function setLang(lang) {
  if (!SUPPORTED_LANGS.includes(lang)) return;
  currentLang = lang;
  localStorage.setItem(LANG_STORAGE_KEY, lang);
  applyI18n();
  // 通知各页面重新渲染（动态生成的内容需要重绘）
  window.dispatchEvent(new CustomEvent("langchange", { detail: { lang } }));
}

function getLang() {
  return currentLang;
}
