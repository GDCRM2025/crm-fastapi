// Crea barra superior y submenú "Settings"
const host = document.querySelector("body");
const bar = document.createElement("div");
bar.id = "topbar";
bar.innerHTML = `
  <nav class="topnav">
    <a href="#/">Home</a>
    <a href="#/leads">Leads</a>
    <a href="#/cotizador">Cotizador</a>
    <a href="#/reportes">Reportes</a>
    <a href="#/operaciones">Operaciones</a>
    <a href="#/tools">Tools</a>
    <a href="#/conductores">Conductores</a>
    <a href="#/finanzas">Finanzas</a>

    <div class="menu-settings">
      <button class="btn-settings">⚙️ Settings</button>
      <div class="dropdown">
        <a href="#/settings/usuarios">Usuarios</a>
        <a href="#/settings/comunas">Comunas</a>
        <a href="#/settings/cartas">Cartas</a>
        <a href="#/settings/marcas">Marcas</a>
        <a href="#/settings/estados">Estados</a>
      </div>
    </div>
  </nav>
`;
host.prepend(bar);

// Mostrar/Ocultar submenú por hover (mouseenter/leave, sin perder foco al mover el mouse)
const node = bar.querySelector(".menu-settings");
node.addEventListener("mouseenter", () => node.classList.add("open"));
node.addEventListener("mouseleave", () => node.classList.remove("open"));
