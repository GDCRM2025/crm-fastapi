import { mountMenu } from "./menu.js";
import { initRouter } from "./router.js";

const topbar = document.getElementById("topbar");
const app = document.getElementById("app");

mountMenu(topbar);
initRouter(app);
