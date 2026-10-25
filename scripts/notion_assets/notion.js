/* Progressive enhancements; navigation, reading and disclosures work without JS. */
(() => {
  "use strict";
  const root = document.documentElement;
  const menu = document.getElementById("site-menu");
  const menuButton = document.querySelector('[data-action="menu"]');
  const dialog = document.getElementById("site-search");
  const input = document.getElementById("search-input");
  const results = document.getElementById("search-results");
  const status = document.getElementById("search-status");
  let toastTimer;
  function toast(text) {
    const el = document.getElementById("site-status");
    clearTimeout(toastTimer); el.textContent = text;
    toastTimer = setTimeout(() => { el.textContent = ""; }, 2500);
  }
  function closeMenu() { menu.hidden = true; menuButton.setAttribute("aria-expanded", "false"); }
  function setTheme(choice) {
    root.dataset.theme = choice;
    root.dataset.resolvedTheme = choice === "auto"
      ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : choice;
    try { localStorage.setItem("notion-static-theme", choice); } catch (_) {}
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (root.dataset.theme === "auto") setTheme("auto");
  });
  function search() {
    const terms = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const matches = (window.NOTION_SEARCH_INDEX || []).map(page => {
      const title = page.title.toLocaleLowerCase(), text = page.text.toLocaleLowerCase();
      if (!terms.every(term => title.includes(term) || text.includes(term))) return null;
      return {page, score: terms.reduce((sum, term) => sum + (title.includes(term) ? 10 : 1), 0)};
    }).filter(Boolean).sort((a,b) => b.score - a.score).slice(0,30);
    results.replaceChildren();
    const base = new URL(document.body.dataset.root, location.href);
    for (const {page} of matches) {
      const li = document.createElement("li"), a = document.createElement("a");
      a.href = new URL(page.url, base).href;
      const title = document.createElement("strong"), preview = document.createElement("small");
      title.textContent = page.title; preview.textContent = page.text.slice(0,145);
      a.append(title,preview); li.append(a); results.append(li);
    }
    status.textContent = matches.length ? `${matches.length} ${matches.length === 1 ? "page" : "pages"}${matches.length === 30 ? " shown" : ""}` : "No matching pages";
  }
  function openSearch() { closeMenu(); dialog.showModal(); search(); input.focus(); }
  input.addEventListener("input", search);
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); results.querySelector("a")?.focus(); }
    if (e.key === "Enter") results.querySelector("a")?.click();
  });
  dialog.addEventListener("click", e => {
    if (e.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
    }
  });
  document.addEventListener("click", async e => {
    const button = e.target.closest("[data-action], [data-theme-choice]");
    if (button?.dataset.themeChoice) { setTheme(button.dataset.themeChoice); closeMenu(); menuButton.focus(); return; }
    switch (button?.dataset.action) {
      case "search": openSearch(); break;
      case "close-search": dialog.close(); break;
      case "menu": menu.hidden = !menu.hidden; menuButton.setAttribute("aria-expanded", String(!menu.hidden)); break;
      case "share":
        try { await navigator.clipboard.writeText(location.href); toast("Link copied"); }
        catch (_) { window.prompt("Copy this page link", location.href); }
        break;
      case "print": closeMenu(); window.print(); break;
      default: if (!menu.contains(e.target)) closeMenu();
    }
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeMenu(); }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); }
  });
  // Fragment links can target headings inside closed toggles.
  function revealTarget() {
    let id; try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    const target = document.getElementById(id);
    if (!target) return;
    for (let p = target.parentElement; p; p = p.parentElement) if (p.tagName === "DETAILS") p.open = true;
    if (location.hash) requestAnimationFrame(() => target.scrollIntoView());
  }
  window.addEventListener("hashchange", revealTarget); revealTarget();
  const entries = [...document.querySelectorAll(".outline-entry")];
  const headings = entries.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
  const outline = document.querySelector(".page-outline");
  const title = document.querySelector(".page-title");
  let scheduled = false;
  function updateOutline() {
    scheduled = false;
    if (!outline || !title) return;
    const top = Math.max(174, title.getBoundingClientRect().top + 18);
    outline.style.top = `${top}px`;
    outline.style.setProperty("--outline-height", `${Math.max(80, innerHeight - top - 80)}px`);
    let active = headings[0];
    let distance = Infinity;
    for (const heading of headings) {
      const box = heading.getBoundingClientRect();
      if (!box.height) continue;
      const next = Math.abs(box.top + box.height / 2 - (44 + (innerHeight - 44) / 4));
      if (next < distance) { active = heading; distance = next; }
    }
    entries.forEach(a => active && a.hash === `#${active.id}` ? a.setAttribute("aria-current", "location") : a.removeAttribute("aria-current"));
  }
  const scheduleOutline = () => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(updateOutline); }
  };
  window.addEventListener("scroll", scheduleOutline, {passive:true});
  window.addEventListener("resize", scheduleOutline);
  document.addEventListener("toggle", scheduleOutline, true);
  updateOutline();
})();
