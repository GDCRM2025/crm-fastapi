export const qs=(s,el=document)=>el.querySelector(s);
export function setTheme(t){ const b=document.body; t==='dark'? (b.classList.add('theme-dark'),b.classList.remove('theme-light')) : (b.classList.add('theme-light'),b.classList.remove('theme-dark')); localStorage.setItem('theme',t); }
export function toggleTheme(){ setTheme(document.body.classList.contains('theme-dark')?'light':'dark'); }
export function currentTheme(){ return localStorage.getItem('theme')||'light'; }
export function page(title, inner){ return `<section class="card"><h1 class="h1">${title}</h1>${inner}</section>`; }
export function navigate(hash){ if(location.hash!==hash) location.hash=hash; else window.dispatchEvent(new HashChangeEvent('hashchange')); }
export function guardAuth(){ const t=localStorage.getItem('token'); if(!t){ location.href='/web/login.html'; return false;} return true; }
