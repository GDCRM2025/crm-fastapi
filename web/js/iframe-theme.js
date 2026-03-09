/*
  Recibe tema desde el padre (index) y lo aplica en el iframe.
  Compatible con:
   - { cmd:"apply-theme", theme:"day"|"night" }
   - { type:"theme", mode:"light"|"dark" }
*/
function _apply(theme){
  const isDay = (theme === "day" || theme === "light");
  document.documentElement.classList.toggle("light", isDay);
  document.documentElement.setAttribute("data-theme", isDay ? "day" : "night");
}

addEventListener("message", (ev) => {
  const d = ev.data || {};
  if (d.cmd === "apply-theme" && d.theme){
    _apply(d.theme);
  }
  if (d.type === "theme" && d.mode){
    _apply(d.mode);
  }
  if (d.type === "prefs" && d.prefs){
    const p = d.prefs;
    if (p.font) document.documentElement.style.setProperty("--font", p.font);
    if (p.accent) document.documentElement.style.setProperty("--accent", p.accent);
    if (p.text) document.documentElement.style.setProperty("--text", p.text);
    if (p.fontSize) document.documentElement.style.setProperty("font-size", `${p.fontSize}px`);
  }
});

// Bootstrap por si el iframe se abre directo (sin padre)
try{
  const t = localStorage.getItem("THEME") || "night";
  _apply(t);
}catch(_){}
