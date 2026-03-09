import { Home } from "./pages/home.js";
import { Settings } from "./pages/settings.js";

const root = document.getElementById("app");

function render(view) {
  // view es string con HTML
  root.innerHTML = view;
}

function currentRoute() {
  const hash = location.hash || "#/";
  // #/settings/usuarios -> ["", "settings", "usuarios"]
  const parts = hash.replace(/^#/, "").split("/"); 
  return parts; 
}

function handleRoute() {
  const parts = currentRoute();

  // Rutas:
  // #/                -> Home
  // #/settings        -> Settings (landing)
  // #/settings/:ent   -> Settings con entidad
  if (parts.length === 1 || parts[1] === "") {
    render(Home());
    return;
  }
  if (parts[1] === "settings") {
    const entidad = parts[2] || "";
    render(Settings(entidad));
    return;
  }

  // fallback
  render(Home());
}

export function initRouter() {
  handleRoute();
  window.addEventListener("hashchange", handleRoute);
}
