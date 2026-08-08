// Persistencia + broadcast de tema para iframes
(function(){
  const HTML = document.documentElement;
  const saved = localStorage.getItem('gd_theme') || 'night';
  HTML.setAttribute('data-theme', saved);

  function setTheme(t){
    localStorage.setItem('gd_theme', t);
    HTML.setAttribute('data-theme', t);
    // avisa al iframe actual
    const f = document.getElementById('viewFrame');
    if (f && f.contentWindow) f.contentWindow.postMessage({type:'theme', value:t}, '*');
  }

  // expone global
  window.GD_THEME = { get:()=>localStorage.getItem('gd_theme')||'night', set:setTheme };

  // listener en páginas hijas (si cargan este mismo archivo)
  window.addEventListener('message', (ev)=>{
    if (ev.data?.type === 'theme') {
      document.documentElement.setAttribute('data-theme', ev.data.value);
    }
  });
})();
