const $ = (q) => document.querySelector(q);

function openForm() {
  chrome.windows.create({ url: chrome.runtime.getURL("form.html"), type:"popup", width:420, height:640, focused:true });
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#openForm").addEventListener("click", openForm);
  $("#clear").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ cmd: "setDraft", patch: {} });
    window.close();
  });

  const a = await chrome.runtime.sendMessage({ cmd:"getAuth" });
  const has = !!(a?.auth?.token);
  $("#dot").classList.toggle("on", has);
  $("#st").textContent = has ? "CRM: token OK" : "CRM: sin token (login en CRM)";
});
