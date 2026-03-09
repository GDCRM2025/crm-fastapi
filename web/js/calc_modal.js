// web/js/calc_modal.js
(function(){
  const qs = (s, el=document) => el.querySelector(s);

  // evita duplicar
  if (window.openGdCalculator) return;

  const tpl = document.createElement("div");
  tpl.innerHTML = `
  <div id="gdCalcModal" style="
    position:fixed; inset:0; display:none; z-index:80;
    align-items:center; justify-content:center;
  ">
    <div id="gdCalcBg" style="
      position:absolute; inset:0;
      background: rgba(0,0,0,.55);
      backdrop-filter: blur(8px);
    "></div>

    <div id="gdCalcBox" style="
      position:relative;
      width: 360px;
      border-radius: 22px;
      border: 1px solid rgba(255,255,255,.14);
      background: rgba(12,16,22,.88);
      box-shadow: 0 30px 80px rgba(0,0,0,.55);
      padding: 14px;
    ">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:10px;">
        <div style="font-weight:900; letter-spacing:.04em; opacity:.9;">Calculadora</div>
        <button id="gdCalcClose" style="
          height:34px; width:34px; border-radius:999px;
          border:1px solid rgba(255,255,255,.16);
          background: rgba(255,255,255,.06);
          color:#fff; cursor:pointer; font-weight:900;
        ">✕</button>
      </div>

      <div id="gdCalcScreen" style="
        margin-top:10px;
        border-radius: 16px;
        border:1px solid rgba(255,255,255,.12);
        background: rgba(255,255,255,.06);
        padding: 12px;
        font-size: 34px;
        font-weight: 900;
        text-align:right;
        overflow:hidden;
        white-space:nowrap;
        text-overflow:ellipsis;
      ">0</div>

      <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-top:12px;">
        <button class="k" data-k="C">C</button>
        <button class="k" data-k="±">±</button>
        <button class="k" data-k="%">%</button>
        <button class="k op" data-k="/">÷</button>

        <button class="k" data-k="7">7</button>
        <button class="k" data-k="8">8</button>
        <button class="k" data-k="9">9</button>
        <button class="k op" data-k="*">×</button>

        <button class="k" data-k="4">4</button>
        <button class="k" data-k="5">5</button>
        <button class="k" data-k="6">6</button>
        <button class="k op" data-k="-">−</button>

        <button class="k" data-k="1">1</button>
        <button class="k" data-k="2">2</button>
        <button class="k" data-k="3">3</button>
        <button class="k op" data-k="+">+</button>

        <button class="k spec" data-k="WEBPAY">Webpay +4%</button>
        <button class="k" data-k="0">0</button>
        <button class="k" data-k=".">.</button>
        <button class="k eq" data-k="=">=</button>

        <button class="k spec" style="grid-column: 1 / span 4;" data-k="IVA">Agregar IVA 19%</button>
      </div>
    </div>
  </div>
  `;

  document.body.appendChild(tpl.firstElementChild);

  // styles on keys
  const style = document.createElement("style");
  style.textContent = `
    #gdCalcBox .k{
      height: 46px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,.12);
      background: rgba(255,255,255,.06);
      color: #fff;
      font-weight: 900;
      cursor:pointer;
    }
    #gdCalcBox .k:hover{ background: rgba(255,255,255,.10); }
    #gdCalcBox .k.op{ background: rgba(34,197,94,.16); border-color: rgba(34,197,94,.28); }
    #gdCalcBox .k.eq{ background: rgba(59,130,246,.22); border-color: rgba(59,130,246,.30); }
    #gdCalcBox .k.spec{ background: rgba(245,158,11,.18); border-color: rgba(245,158,11,.28); }
  `;
  document.head.appendChild(style);

  const modal = qs("#gdCalcModal");
  const bg = qs("#gdCalcBg");
  const close = qs("#gdCalcClose");
  const screen = qs("#gdCalcScreen");

  let cur = "0";
  let acc = null;
  let op = null;
  let fresh = true;

  function show(){
    screen.textContent = cur;
  }
  function reset(){
    cur = "0"; acc = null; op = null; fresh = true; show();
  }
  function toNum(s){
    const n = Number(String(s).replace(",","."));
    return Number.isFinite(n) ? n : 0;
  }

  function applyOp(a, b, o){
    if (o === "+") return a + b;
    if (o === "-") return a - b;
    if (o === "*") return a * b;
    if (o === "/") return b === 0 ? a : a / b;
    return b;
  }

  function press(k){
    if (/^\d$/.test(k)){
      if (fresh) { cur = k; fresh = false; }
      else cur = (cur === "0") ? k : (cur + k);
      return show();
    }

    if (k === "."){
      if (fresh){ cur = "0."; fresh = false; return show(); }
      if (!cur.includes(".")) cur += ".";
      return show();
    }

    if (k === "C") return reset();

    if (k === "±"){
      cur = String(-toNum(cur));
      fresh = false;
      return show();
    }

    if (k === "%"){
      cur = String(toNum(cur) / 100);
      fresh = false;
      return show();
    }

    if (k === "WEBPAY"){
      cur = String(Math.round(toNum(cur) * 1.04));
      fresh = true;
      return show();
    }

    if (k === "IVA"){
      cur = String(Math.round(toNum(cur) * 1.19));
      fresh = true;
      return show();
    }

    if (["+","-","*","/"].includes(k)){
      if (acc === null) acc = toNum(cur);
      else if (!fresh) acc = applyOp(acc, toNum(cur), op);
      op = k;
      fresh = true;
      cur = String(acc);
      return show();
    }

    if (k === "="){
      if (op === null || acc === null) return show();
      const res = applyOp(acc, toNum(cur), op);
      cur = String(res);
      acc = null; op = null; fresh = true;
      return show();
    }
  }

  document.querySelectorAll("#gdCalcBox .k").forEach(b => {
    b.addEventListener("click", () => press(b.dataset.k));
  });

  function open(){
    modal.style.display = "flex";
  }
  function hide(){
    modal.style.display = "none";
  }

  bg.addEventListener("click", hide);
  close.addEventListener("click", hide);

  window.openGdCalculator = () => { reset(); open(); };
})();
