// Aplica tema y propaga a iframes
export function applyTheme(){
  const t = localStorage.getItem('THEME') || 'night';
  document.documentElement.classList.toggle('light', t === 'day');
}
applyTheme();

// Propagación entre ventanas (index <-> iframes)
addEventListener('storage', e=>{ if(e.key==='THEME') applyTheme(); });
