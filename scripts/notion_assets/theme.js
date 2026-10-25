/* Apply a saved appearance before the first paint. Storage can be disabled. */
(() => {
  let choice = document.documentElement.dataset.theme || "dark";
  try { choice = localStorage.getItem("notion-static-theme") || choice; } catch (_) {}
  if (!["light", "dark", "auto"].includes(choice)) choice = "dark";
  document.documentElement.dataset.theme = choice;
  document.documentElement.dataset.resolvedTheme = choice === "auto"
    ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : choice;
})();
