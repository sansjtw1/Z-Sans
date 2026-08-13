/* Z-Sans Mermaid 渲染初始化
   - 使用 mermaid 内置 startOnLoad 就地渲染 <pre class="mermaid">
   - 渲染为 SVG div 替换原代码块
   - 跟随页面亮/暗主题
*/
(function () {
  function isDark() {
    return (
      document.documentElement.classList.contains('dark') ||
      document.documentElement.classList.contains('theme-dark')
    );
  }

  function initMermaid() {
    if (typeof mermaid === 'undefined') return;
    var has = document.querySelector('pre.mermaid, .mermaid');
    if (!has) return;
    var theme = isDark() ? 'dark' : 'default';
    mermaid.initialize({
      startOnLoad: true,
      theme: theme,
      securityLevel: 'loose',
      fontFamily: '"Geist", "Noto Sans SC", "Noto Sans CJK SC", sans-serif'
    });
    mermaid.run({ nodes: document.querySelectorAll('pre.mermaid') });
  }

  function waitAndRun() {
    if (typeof mermaid !== 'undefined') {
      initMermaid();
    } else {
      setTimeout(waitAndRun, 100);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', waitAndRun);
  } else {
    setTimeout(waitAndRun, 50);
  }
})();