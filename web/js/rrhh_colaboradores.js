// @ts-nocheck
    const qs = (s, el=document) => el.querySelector(s);
    function token(){ return localStorage.getItem("token") || sessionStorage.getItem("token") || ""; }
    function headers(){ return token() ? { Authorization: "Bearer " + token() } : {}; }

    function applyThemeBootstrap(){
      const mode = (localStorage.getItem("gd_theme") || "dark");
      document.documentElement.classList.toggle("light", mode === "light");
      window.addEventListener("message", (ev) => {
        if (ev.data?.type === "theme"){
          document.documentElement.classList.toggle("light", ev.data.mode === "light");
        }
      });
    }
    applyThemeBootstrap();

    function fmt(n){
      if (n == null || n === "") return "—";
      const v = Number(n);
      if (Number.isFinite(v)) return v.toLocaleString("es-CL");
      return n;
    }

    let ROLES = [
      "ADMIN",
      "EJECUTIVO DE VENTAS",
      "JEFE DE OPERACIONES",
      "BODEGUERO",
      "COMPRAS",
      "CONDUCTOR (CHOP)",
      "OPERADOR",
      "MICE",
    ];
    let AFP_LIST = [];
    let SALUD_LIST = [];
    let staffEditing = null;

    async function loadRoles(){
      try{
        const r = await fetch("/settings/roles?limit=200", { headers: headers() });
        if (r.ok){
          const data = await r.json();
          const items = data.items || data || [];
          const fromDb = items.map(x => (x.nombre || x.rol || x.role || "")).filter(Boolean);
          if (fromDb.length) ROLES = fromDb;
        }
      }catch(_){ }
      const sel = qs("#stRol");
      if (sel){
        const current = sel.value;
        sel.innerHTML = `<option value="">Rol (obligatorio)</option>` + ROLES.map(r => `<option>${r}</option>`).join("");
        if (current) sel.value = current;
      }
    }
    async function loadAfp(){
      try{
        const r = await fetch("/rrhh/afp", { headers: headers() });
        if (!r.ok) return;
        const data = await r.json();
        AFP_LIST = data.items || [];
        const sel = qs("#stAfp");
        if (sel){
          const current = sel.value;
          sel.innerHTML = `<option value="">AFP (obligatorio)</option>` + AFP_LIST.map(a => `<option value="${a.nombre}">${a.nombre} (${a.pct_comision ?? ""}%)</option>`).join("");
          if (current) sel.value = current;
        }
      }catch(_){ }
    }
    async function loadSalud(){
      try{
        const r = await fetch("/rrhh/salud", { headers: headers() });
        if (!r.ok) return;
        const data = await r.json();
        SALUD_LIST = data.items || [];
        const sel = qs("#stSalud");
        if (sel){
          const current = sel.value;
          sel.innerHTML = `<option value="">Salud (obligatorio)</option>` + SALUD_LIST.map(s => `<option value="${s.nombre}">${s.nombre} (${s.pct_base ?? ""}%)</option>`).join("");
          if (current) sel.value = current;
        }
      }catch(_){ }
    }

    async function loadComunas(){
      try{
        const r = await fetch("/catalogos", { headers: headers() });
        if (!r.ok) return;
        const data = await r.json();
        const comunas = data.comunas || [];
        const sel = qs("#stComuna");
        if (sel){
          const current = sel.value;
          sel.innerHTML = `<option value=\"\">Comuna</option>` + comunas.map(c => `<option value=\"${c.nombre}\">${c.nombre}</option>`).join("");
          if (current) sel.value = current;
        }
      }catch(_){ }
    }

    function normalizeRut(r){ return String(r||"").replace(/[^0-9kK]/g,"").toUpperCase(); }
    function isValidRut(r){
      const clean = normalizeRut(r);
      if (clean.length < 2) return false;
      const body = clean.slice(0, -1);
      let dv = clean.slice(-1);
      let sum = 0, mul = 2;
      for (let i = body.length - 1; i >= 0; i--){
        sum += Number(body[i]) * mul;
        mul = mul === 7 ? 2 : mul + 1;
      }
      const res = 11 - (sum % 11);
      const dvCalc = res === 11 ? "0" : res === 10 ? "K" : String(res);
      return dvCalc === dv;
    }
    function isValidEmail(v){ return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(v||"").trim()); }
    function isValidPhoneCL(v){ const raw = String(v||"").replace(/\D/g,""); return raw.length >= 8; }

    function openModal(id){ closeAll(); qs(id).style.display = "flex"; }
    function closeAll(){ document.querySelectorAll('.modal').forEach(m => m.style.display='none'); }
    document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', closeAll));

    function openStaffModal(item){
      staffEditing = item || null;
      const ficha = item?.ficha || {};
      qs("#stArea").value = ficha.area || "";
      qs("#stCargo").value = ficha.cargo || "";
      qs("#stRenta").value = ficha.renta_liquida || "";
      qs("#stEntrevistador").value = ficha.entrevistador || "";
      qs("#stFechaEnt").value = ficha.fecha_entrevista || "";
      qs("#stApellidoP").value = ficha.apellido_paterno || "";
      qs("#stApellidoM").value = ficha.apellido_materno || "";
      qs("#stNombres").value = ficha.nombres || "";
      qs("#stRut").value = item?.rut || "";
      qs("#stNacimiento").value = ficha.fecha_nacimiento || "";
      qs("#stNacionalidad").value = ficha.nacionalidad || "";
      qs("#stGenero").value = ficha.genero || "";
      qs("#stCel").value = item?.telefono || "";
      qs("#stFijo").value = ficha.telefono_fijo || "";
      qs("#stEstadoCivil").value = ficha.estado_civil || "";
      qs("#stEmail").value = item?.email || "";
      qs("#stCalle").value = ficha.calle || "";
      qs("#stNumero").value = ficha.numero || "";
      qs("#stDepto").value = ficha.depto || "";
      qs("#stDireccionExtra").value = ficha.direccion_extra || "";
      qs("#stComuna").value = ficha.comuna || "";
      qs("#stCiudad").value = ficha.ciudad || "";
      qs("#stPais").value = ficha.pais || "";
      qs("#stEmergencia").value = ficha.emergencia_contacto || "";
      qs("#stEmergenciaTel").value = ficha.emergencia_telefono || "";
      qs("#stAlergico").value = ficha.alergico || "";
      qs("#stEnfermedad").value = ficha.enfermedad || "";
      qs("#stFamPar").value = ficha.fam_parentesco || "";
      qs("#stFamNombre").value = ficha.fam_nombre || "";
      qs("#stFamRut").value = ficha.fam_rut || "";
      qs("#stFamNac").value = ficha.fam_nacimiento || "";
      qs("#stFamCarga").value = ficha.fam_carga || "";
      qs("#stFamAct").value = ficha.fam_actividad || "";
      qs("#stPantalon").value = ficha.talla_pantalon || "";
      qs("#stChaqueta").value = ficha.talla_chaqueta || "";
      qs("#stPolera").value = ficha.talla_polera || "";
      qs("#stCalzado").value = ficha.talla_calzado || "";
      qs("#stGuantes").value = ficha.talla_guantes || "";
      qs("#stRol").value = item?.rol || "";
      qs("#stCentro").value = item?.centro_costo || "";
      qs("#stIngreso").value = item?.fecha_ingreso || "";
      qs("#stAfp").value = item?.afp || "";
      qs("#stAfpPct").value = item?.afp_pct || "";
      qs("#stSalud").value = item?.salud_tipo || "";
      qs("#stSaludPct").value = item?.salud_pct || "";
      qs("#stCuentaNum").value = ficha.cuenta_numero || "";
      qs("#stTipoCuenta").value = ficha.tipo_cuenta || "";
      qs("#stBanco").value = ficha.banco || "";
      qs("#stActivo").value = String(item?.is_active ?? true);
      openModal('#m1');
    }

    function requiredOk(){
      const required = [
        { id:"#stCargo", msg:"Cargo requerido" },
        { id:"#stRenta", msg:"Renta requerida" },
        { id:"#stApellidoP", msg:"Apellido paterno requerido" },
        { id:"#stApellidoM", msg:"Apellido materno requerido" },
        { id:"#stNombres", msg:"Nombres requeridos" },
        { id:"#stRut", msg:"RUT requerido" },
        { id:"#stEmail", msg:"Correo requerido" },
        { id:"#stCel", msg:"Teléfono requerido" },
        { id:"#stAfp", msg:"AFP requerida" },
        { id:"#stSalud", msg:"Salud requerida" },
        { id:"#stCuentaNum", msg:"Nº de cuenta requerido" },
        { id:"#stBanco", msg:"Banco requerido" },
        { id:"#stRol", msg:"Rol requerido" },
      ];
      for (const r of required){
        const val = (qs(r.id).value || "").trim();
        if (!val){ alert(r.msg); return false; }
      }
      if (!isValidRut(qs('#stRut').value)){ alert('RUT inválido'); return false; }
      if (!isValidEmail(qs('#stEmail').value)){ alert('Correo inválido'); return false; }
      if (!isValidPhoneCL(qs('#stCel').value)){ alert('Teléfono inválido'); return false; }
      return true;
    }

    qs("#next1").onclick = () => openModal('#m2');
    qs("#back2").onclick = () => openModal('#m1');
    qs("#next2").onclick = () => openModal('#m3');
    qs("#back3").onclick = () => openModal('#m2');
    qs("#next3").onclick = () => openModal('#m4');
    qs("#back4").onclick = () => openModal('#m3');
    qs("#next4").onclick = () => openModal('#m5');
    qs("#back5").onclick = () => openModal('#m4');
    qs("#next5").onclick = () => openModal('#m6');
    qs("#back6").onclick = () => openModal('#m5');

    qs("#stAfp").addEventListener("change", () => {
      const sel = qs("#stAfp").value;
      const item = AFP_LIST.find(a => a.nombre === sel);
      if (item) qs("#stAfpPct").value = item.pct_comision ?? "";
    });
    qs("#stSalud").addEventListener("change", () => {
      const sel = qs("#stSalud").value;
      const item = SALUD_LIST.find(a => a.nombre === sel);
      if (item) qs("#stSaludPct").value = item.pct_base ?? "";
    });

    function toast(msg){
      const t = document.createElement("div");
      t.className = "toast";
      t.textContent = msg;
      Object.assign(t.style, { position:"fixed", right:"16px", bottom:"16px", background:"rgba(15,23,42,.9)", color:"#fff", padding:"10px 12px", borderRadius:"10px", zIndex:99, fontWeight:"800" });
      document.body.appendChild(t);
      setTimeout(()=>t.remove(), 2200);
    }

    qs("#saveStaff").onclick = async () => {
      if (!requiredOk()) return;
      const ficha = {
        area: qs("#stArea").value,
        cargo: qs("#stCargo").value,
        renta_liquida: qs("#stRenta").value,
        entrevistador: qs("#stEntrevistador").value,
        fecha_entrevista: qs("#stFechaEnt").value,
        apellido_paterno: qs("#stApellidoP").value,
        apellido_materno: qs("#stApellidoM").value,
        nombres: qs("#stNombres").value,
        fecha_nacimiento: qs("#stNacimiento").value,
        nacionalidad: qs("#stNacionalidad").value,
        genero: qs("#stGenero").value,
        telefono_celular: qs("#stCel").value,
        telefono_fijo: qs("#stFijo").value,
        estado_civil: qs("#stEstadoCivil").value,
        calle: qs("#stCalle").value,
        numero: qs("#stNumero").value,
        depto: qs("#stDepto").value,
        direccion_extra: qs("#stDireccionExtra").value,
        comuna: qs("#stComuna").value,
        ciudad: qs("#stCiudad").value,
        pais: qs("#stPais").value,
        emergencia_contacto: qs("#stEmergencia").value,
        emergencia_telefono: qs("#stEmergenciaTel").value,
        alergico: qs("#stAlergico").value,
        enfermedad: qs("#stEnfermedad").value,
        fam_parentesco: qs("#stFamPar").value,
        fam_nombre: qs("#stFamNombre").value,
        fam_rut: qs("#stFamRut").value,
        fam_nacimiento: qs("#stFamNac").value,
        fam_carga: qs("#stFamCarga").value,
        fam_actividad: qs("#stFamAct").value,
        talla_pantalon: qs("#stPantalon").value,
        talla_chaqueta: qs("#stChaqueta").value,
        talla_polera: qs("#stPolera").value,
        talla_calzado: qs("#stCalzado").value,
        talla_guantes: qs("#stGuantes").value,
        cuenta_numero: qs("#stCuentaNum").value,
        tipo_cuenta: qs("#stTipoCuenta").value,
        banco: qs("#stBanco").value,
      };
      const payload = {
        colaborador: `${qs("#stApellidoP").value} ${qs("#stApellidoM").value} ${qs("#stNombres").value}`.trim(),
        rut: normalizeRut(qs("#stRut").value),
        email: qs("#stEmail").value,
        telefono: qs("#stCel").value,
        rol: qs("#stRol").value,
        centro_costo: qs("#stCentro").value,
        fecha_ingreso: qs("#stIngreso").value || null,
        afp: qs("#stAfp").value,
        afp_pct: Number(qs("#stAfpPct").value || 0) || null,
        salud_tipo: qs("#stSalud").value,
        salud_pct: Number(qs("#stSaludPct").value || 0) || null,
        is_active: qs("#stActivo").value === "true",
        observaciones: "",
        ficha,
      };
      const url = staffEditing ? `/rrhh/staff/${staffEditing.id_staff}` : "/rrhh/staff";
      const method = staffEditing ? "PUT" : "POST";
      const r = await fetch(url, { method, headers: { "Content-Type":"application/json", ...headers() }, body: JSON.stringify(payload) });
      if (!r.ok){ 
        const t = await r.text().catch(()=> "");
        toast("No se pudo guardar: " + (t || r.status));
        return; 
      }
      toast("Colaborador guardado");
      closeAll();
      await loadStaff();
    };

    async function loadStaff(){
      const r = await fetch("/rrhh/staff", { headers: headers() });
      if (!r.ok){
        const t = await r.text().catch(()=> "");
        qs("#rowsStaff").innerHTML = `<tr><td class=\"muted\">No se pudo cargar: ${t || r.status}</td></tr>`;
        return;
      }
      const data = await r.json();
      if (data.ok === false){
        qs("#rowsStaff").innerHTML = `<tr><td class=\"muted\">No se pudo cargar: ${data.detail || "Error"}</td></tr>`;
        return;
      }
      let items = data.items || [];
      const norm = (v) => String(v || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
      const q = norm(qs("#qStaff").value || "");
      if (q){ items = items.filter(i => norm(i.colaborador || "").includes(q)); }
      qs("#rowsStaff").innerHTML = items.map(i => {
        const afpLabel = i.afp ? (i.afp + " (" + fmt(i.afp_pct) + "%)") : "—";
        const saludLabel = i.salud_tipo ? (i.salud_tipo + " (" + fmt(i.salud_pct) + "%)") : "—";
        const activo = String(i.is_active ?? true) === "true" ? "Sí" : "No";
        return `
        <tr>
          <td><strong>${i.colaborador || "—"}</strong></td>
          <td>${i.rol || "—"}</td>
          <td>${i.centro_costo || "—"}</td>
          <td>${afpLabel}</td>
          <td>${saludLabel}</td>
          <td>${activo}</td>
          <td>
            <button class="btn" data-edit="${i.id_staff}">Editar</button>
            <button class="btn" data-ficha="${i.id_staff}">Ficha PDF</button>
            <button class="btn" data-del="${i.id_staff}">Eliminar</button>
          </td>
        </tr>
      `;
      }).join("") || `<tr><td class="muted">Sin datos.</td></tr>`;
      qs("#rowsStaff").querySelectorAll("[data-edit]").forEach(b => {
        const id = Number(b.dataset.edit || 0);
        const item = items.find(x => Number(x.id_staff) === id);
        b.addEventListener("click", () => openStaffModal(item));
      });
      qs("#rowsStaff").querySelectorAll("[data-del]").forEach(b => {
        const id = Number(b.dataset.del || 0);
        b.addEventListener("click", async () => {
          if (!confirm("¿Eliminar colaborador?")) return;
          const r = await fetch(`/rrhh/staff/${id}`, { method:"DELETE", headers: headers() });
          if (!r.ok){ toast("No se pudo eliminar"); return; }
          toast("Colaborador eliminado");
          await loadStaff();
        });
      });
      qs("#rowsStaff").querySelectorAll("[data-ficha]").forEach(b => {
        const id = Number(b.dataset.ficha || 0);
        b.addEventListener("click", () => {
          window.open(`/rrhh/staff/${id}/ficha`, "_blank");
        });
      });
    }

    qs("#btnReloadStaff").addEventListener("click", () => loadStaff());
    qs("#qStaff").addEventListener("input", () => loadStaff());
    qs("#btnNewStaff").addEventListener("click", () => openStaffModal(null));

    function applyLabels(){
      document.querySelectorAll('.modal .row').forEach(row => {
        const inputs = Array.from(row.querySelectorAll('.inp'));
        inputs.forEach(inp => {
          if (inp.parentElement?.classList.contains('field')) return;
          const labelText = inp.dataset.label || inp.getAttribute('placeholder') || inp.options?.[0]?.textContent || 'Campo';
          const wrap = document.createElement('div');
          wrap.className = 'field';
          const lab = document.createElement('div');
          lab.className = 'field-label';
          lab.textContent = labelText;
          inp.parentElement.replaceChild(wrap, inp);
          wrap.appendChild(lab);
          wrap.appendChild(inp);
        });
      });
    }

    async function init(){
      await loadRoles();
      await loadAfp();
      await loadSalud();
      await loadComunas();
      await loadStaff();
      applyLabels();
    }
    init();
