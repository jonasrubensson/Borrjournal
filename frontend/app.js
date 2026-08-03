/* Borrjournal - webbgränssnitt. Ingen build-kedja, medvetet: en fil att läsa och ändra i. */
"use strict";

// Höjs i takt med backend/app/version.py. Går de isär körs gammal backend-kod.
const UI_VERSION = "3.5.0";

const S = {
  token: localStorage.getItem("bj_token") || null,
  user: JSON.parse(localStorage.getItem("bj_user") || "null"),
  route: "oversikt",
  size: "normal",
  company: null,
  id: null,
  tab: "journal",
  step: 0,
  form: {},
  data: {},
  filter: {},
  loading: false,
};

const $ = (s) => document.querySelector(s);
const root = () => $("#root");
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, unit = "") => (v === null || v === undefined || v === "" ? "—" : `${v}${unit}`);
const STATUS = { ok: "I drift", soon: "Service snart", action: "Åtgärd krävs" };
const tag = (s) => `<span class="tag ${s}">${STATUS[s] || s}</span>`;

function dt(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = (n) => String(n).padStart(2, "0");
  const date = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return withTime ? `${date} ${p(d.getHours())}:${p(d.getMinutes())}` : date;
}
function nowStamp() {
  return dt(new Date().toISOString());
}
function bytes(n) {
  if (!n) return "—";
  return n < 1024 * 1024 ? `${Math.round(n / 1024)} kB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/* Varje vy tar ett nummer när den startar. Hinner användaren klicka vidare
   medan vyn hämtar data, har numret gått vidare och den gamla vyn låter bli
   att skriva till skärmen. Utan detta kan en långsam vy skriva över den nya. */
let renderSeq = 0;
const claim = () => ++renderSeq;
const current = (token) => token === renderSeq;

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast up" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), 3600);
}

/* ---------------- API ---------------- */
async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (S.token) opts.headers.Authorization = `Bearer ${S.token}`;
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(`/api${path}`, opts);
  if (res.status === 401 && S.token) {
    logout("Sessionen har gått ut. Logga in igen.");
    throw new Error("401");
  }
  if (!res.ok) {
    let detail = `Fel ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) {}
    if (res.status === 403 && detail === "totp_setup_required") {
      forceTotpSetup();
      throw new Error("401");
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

function logout(msg) {
  S.token = null;
  S.user = null;
  localStorage.removeItem("bj_token");
  localStorage.removeItem("bj_user");
  render();
  if (msg) toast(msg, true);
}

/* ---------------- routing ---------------- */
/* Fem i navigationen, valda efter hur dagen ser ut: vad ska jag göra, vem gäller det,
   vad är inbokat, vad ska faktureras. Resten ligger under Mer, som öppnas vid behov. */
const ROUTES = [
  ["oversikt", "Idag", "M3 10 L10 3 L17 10 M5 8 v9 h10 V8"],
  ["kunder", "Kunder", "M10 9 a3 3 0 1 0 0-6 a3 3 0 0 0 0 6 M3 17 c0-4 3-6 7-6 s7 2 7 6"],
  ["besok", "Besök", "M10 17 s6-5.3 6-10a6 6 0 1 0-12 0c0 4.7 6 10 6 10z M10 5 v4 M8 7 h4"],
  ["ekonomi", "Fakturera", "M3 5 h14 v10 H3z M3 8 h14 M6 12 h4"],
  ["mer", "Mer", "M4 6 h12 M4 10 h12 M4 14 h12"],
];

const MER_POSTER = [
  ["paminnelser", "Påminnelser", "Allt som ska bevakas"],
  ["artiklar", "Artiklar och lager", "Prislista och saldon"],
  ["mallar", "Offertmallar", "Underlag för vanliga jobb"],
  ["journal", "Journal, alla kunder", "Vad som hänt den senaste tiden"],
  ["pumpar", "Pumpar och modeller", "Hitta alla med en viss modell"],
  ["anlaggningar", "Anläggningar", "Hela flottan med filter"],
  ["nara", "Jobb i närheten", "Vad som ligger runt omkring"],
  ["ny", "Registrera anläggning", "Ny brunn eller pump"],
  ["konto", "Mitt konto", "Lösenord, tvåfaktor, textstorlek"],
  ["handelser", "Systemhändelser", "Fel i bakgrunden"],
];

function go(route, id) {
  const hash = id ? `#/${route}/${id}` : `#/${route}`;
  if (location.hash === hash) applyHash();
  else location.hash = hash;
}

function applyHash() {
  const parts = (location.hash || "#/oversikt").replace(/^#\/?/, "").split("/");
  S.route = parts[0] || "oversikt";
  S.id = parts[1] || null;
  if (S.route === "kund") S.tab = parts[2] || "oversikt";
  if (S.route === "admin") S.tab = parts[1] || "konton";
  if (S.route === "ny" && !S.id) S.step = S.step || 0;
  window.scrollTo(0, 0);
  render();
}
window.addEventListener("hashchange", applyHash);

/* ---------------- inloggning ---------------- */
let loginNeedsTotp = false;

function viewLogin(error = "") {
  // Namnet hämtas separat, kräver ingen inloggning via /api/version

  root().innerHTML = `
  <div class="login">
    <form class="box" id="loginform" autocomplete="on">
      <img id="loginlogo" alt="" hidden style="max-width:180px;max-height:70px;display:block;margin-bottom:14px">
      <div class="bn" id="loginnamn">Borrjournal</div><span class="bs">KUND &amp; ANLÄGGNING</span>
      ${error ? `<div class="err">${esc(error)}</div>` : ""}
      <label class="f" for="u">Användarnamn</label>
      <input id="u" name="username" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false">
      <label class="f" for="p">Lösenord</label>
      <input id="p" name="password" type="password" autocomplete="current-password">
      ${
        loginNeedsTotp
          ? `<label class="f" for="t">Engångskod</label>
             <input id="t" name="otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6">`
          : ""
      }
      <button class="btn pri" id="lg" type="submit">Logga in</button>
    </form>
  </div>`;
  // Riktig form: Enter fungerar, och lösenordshanterare beter sig som de ska.
  $("#loginform").addEventListener("submit", (e) => {
    e.preventDefault();
    doLogin();
  });
  ($("#t") || $("#u")).focus();
}

async function doLogin() {
  const body = { username: $("#u").value.trim(), password: $("#p").value };
  const totp = $("#t");
  if (totp) body.totp_code = totp.value.trim();
  try {
    const res = await api("/login", { method: "POST", body });
    S.token = res.token;
    S.user = res.user;
    localStorage.setItem("bj_token", res.token);
    localStorage.setItem("bj_user", JSON.stringify(res.user));
    loginNeedsTotp = false;
    if (res.totp_setup_required) return forceTotpSetup();
    S.company = null;
    await laddaForetag();
    go("oversikt");
    toast(`Inloggad som ${res.user.full_name || res.user.username}`);
  } catch (e) {
    if (e.status === 428) {
      loginNeedsTotp = true;
      viewLogin("Ange engångskoden från din autentiseringsapp.");
      return;
    }
    viewLogin(e.message);
  }
}

/* ---------------- skal ---------------- */
function shell(inner) {
  const u = S.user || {};
  const initials = (u.full_name || u.username || "?")
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  const navHtml = ROUTES.map(([r, label, d]) => {
    const iMer = MER_POSTER.some(([k]) => k === S.route);
    const on =
      S.route === r ||
      (r === "kunder" && ["kund", "offert", "order"].includes(S.route)) ||
      (r === "besok" && S.route === "besok") ||
      (r === "mer" && (iMer || S.route === "admin"));
    const badge = r === "paminnelser" && S.badge ? `<span class="cnt badge">${S.badge}</span>` : "";
    return `<a href="#/${r}" class="${on ? "on" : ""}">
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="${d}" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      <span>${label}</span>${badge}</a>`;
  }).join("");

  return `
  <div class="app">
    <aside class="side">
      <div class="brand">
        ${
          S.company && S.company.har_logotyp
            ? `<img class="logo" data-auth-src="/api/company/logo" alt="${esc(S.company.namn || "Logotyp")}">`
            : `<svg width="20" height="26" viewBox="0 0 20 26" fill="none" aria-hidden="true">
          <path d="M10 1 L10 25" stroke="#1F7A8C" stroke-width="2"/>
          <path d="M4 7h12M4 13h12M4 19h12" stroke="#5E7C87" stroke-width="1.4"/>
          <path d="M10 25 l-4-4 h8 z" fill="#1F7A8C"/></svg>
        <span class="bn">${esc((S.company && S.company.namn) || "Borrjournal")}<span class="bs">KUND &amp; ANLÄGGNING</span></span>`
        }
      </div>
      <div class="navgroup">Register</div>
      <nav class="nav">${navHtml}</nav>
      <div class="foot">
        <button onclick="go('konto')" style="text-align:left">${esc(u.full_name || u.username || "")}<br>${esc(u.role || "")}</button><br>
        <button onclick="logout()">Logga ut</button></div>
    </aside>
    <div class="main">
      <div class="top">
        <div class="search">
          <span class="ic"><svg width="15" height="15" viewBox="0 0 15 15" fill="none"><circle cx="6.5" cy="6.5" r="4.7" stroke="currentColor" stroke-width="1.5"/><path d="M10 10 L14 14" stroke="currentColor" stroke-width="1.5"/></svg></span>
          <input id="gq" type="search" placeholder="Sök kund, brunn, pumpmodell, serienr, journal" autocomplete="off">
          <div id="gres"></div>
        </div>
        <button class="btn ghost sm" onclick="go('nara')" title="Jobb i närheten" aria-label="Jobb i närheten">
          <svg width="15" height="15" viewBox="0 0 20 20" fill="none"><path d="M10 18s6-5.3 6-10a6 6 0 1 0-12 0c0 4.7 6 10 6 10z" stroke="currentColor" stroke-width="1.5"/><circle cx="10" cy="8" r="2.2" stroke="currentColor" stroke-width="1.5"/></svg>
          <span class="hidemob">Nära</span></button>
        <button class="btn pri sm" onclick="go('ny')">+ Ny</button>
        <button class="who" onclick="go('konto')" title="Mitt konto"
          style="background:none;border:none;padding:6px 4px;cursor:pointer">
          <span class="av">${esc(initials)}</span><span>${esc(u.full_name || u.username || "")}</span></button>
      </div>
      <main class="view" id="view">${inner}</main>
    </div>
  </div>`;
}

async function laddaForetag() {
  if (S.company) return S.company;
  try {
    S.company = await api("/company");
  } catch (_) {
    S.company = { namn: "", har_logotyp: false };
  }
  return S.company;
}

function mountShell(inner) {
  root().innerHTML = shell(inner);
  hydreraBilder(root());
  const q = $("#gq");
  q.value = S.gq || "";
  q.oninput = debounce(globalSearch, 250);
  q.onkeydown = (e) => {
    if (e.key === "Escape") {
      q.value = "";
      $("#gres").innerHTML = "";
    }
  };
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search")) {
      const g = $("#gres");
      if (g) g.innerHTML = "";
    }
  });
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

async function globalSearch() {
  const q = $("#gq").value.trim();
  S.gq = q;
  const box = $("#gres");
  if (q.length < 2) {
    box.innerHTML = "";
    return;
  }
  const r = await api(`/search?q=${encodeURIComponent(q)}`);
  const groups = [];
  if (r.customers.length)
    groups.push(
      `<div class="grp">Kunder</div>` +
        r.customers
          .map(
            (c) => `<button onclick="go('kund','${c.id}')">${esc(c.name)}
        <span class="s">${esc(c.customer_no)} · ${esc(c.property_designation || "")} ${esc(c.municipality || "")}</span></button>`
          )
          .join("")
    );
  if (r.facilities.length)
    groups.push(
      `<div class="grp">Anläggningar och pumpar</div>` +
        r.facilities
          .map(
            (f) => `<button onclick="go('kund','${f.customer_id}')">${esc(f.facility_no)} · ${esc(f.facility_type)}
        <span class="s">${esc(f.customer.name)} · ${esc([f.pump_manufacturer, f.pump_model].filter(Boolean).join(" ") || "ingen pump")} ${f.pump_serial ? "· " + esc(f.pump_serial) : ""}</span></button>`
          )
          .join("")
    );
  if (r.journal.length)
    groups.push(
      `<div class="grp">Journal</div>` +
        r.journal
          .map(
            (j) => `<button onclick="go('kund','${j.customer_id}')">${esc(j.title)}
        <span class="s">${esc(j.customer.name)} · ${dt(j.created_at)}</span></button>`
          )
          .join("")
    );
  if (r.files.length)
    groups.push(
      `<div class="grp">Filer</div>` +
        r.files
          .map(
            (f) => `<button onclick="go('kund','${f.customer.id}')">${esc(f.filename)}
        <span class="s">${esc(f.customer.name)}</span></button>`
          )
          .join("")
    );
  box.innerHTML = groups.length
    ? `<div class="results">${groups.join("")}</div>`
    : `<div class="results"><div class="grp">Inga träffar</div></div>`;
}


/* ---------------- bilder bakom inloggning ----------------
   En <img src="..."> skickar ingen Authorization-header, så bilder från API:et
   blir 401 och visas som brutna. Lösningen är att hämta dem med token och lägga
   in resultatet som en blob-URL. Alternativet, token i frågesträngen, hade
   hamnat i webbserverloggar och referrers. */
const BLOBBAR = new Set();

function slappBlobbar() {
  for (const url of BLOBBAR) URL.revokeObjectURL(url);
  BLOBBAR.clear();
}

async function laddaBild(el) {
  const path = el.dataset.authSrc;
  if (!path || el.dataset.laddad) return;
  el.dataset.laddad = "1";
  try {
    const res = await fetch(path, { headers: { Authorization: `Bearer ${S.token}` } });
    if (!res.ok) throw new Error(String(res.status));
    const url = URL.createObjectURL(await res.blob());
    BLOBBAR.add(url);
    el.src = url;
    el.classList.remove("laddar");
  } catch (e) {
    el.classList.remove("laddar");
    el.classList.add("trasig");
    el.removeAttribute("src");
    el.alt = "Kunde inte visa bilden";
  }
}

/* Laddar bara det som syns, så en kund med femtio foton inte drar hem allt på en gång. */
let bildObservator = null;
function hydreraBilder(root = document) {
  const bilder = root.querySelectorAll("img[data-auth-src]:not([data-laddad])");
  if (!bilder.length) return;
  if (!("IntersectionObserver" in window)) {
    bilder.forEach(laddaBild);
    return;
  }
  if (!bildObservator) {
    bildObservator = new IntersectionObserver(
      (poster) => {
        for (const p of poster) {
          if (p.isIntersecting) {
            laddaBild(p.target);
            bildObservator.unobserve(p.target);
          }
        }
      },
      { rootMargin: "200px" }
    );
  }
  bilder.forEach((b) => bildObservator.observe(b));
}

/* ---------------- brunnsprofil ---------------- */
function profile(f) {
  const H = 210;
  const max = Math.max(f.total_depth_m || 0, 1);
  const sc = (d) => ((d || 0) / max) * H;
  const yj = sc(f.soil_depth_m);
  const yv = sc(f.water_level_m);
  const yf = sc(f.casing_length_m);
  return `<div class="profile">
    <svg width="86" height="${H + 26}" viewBox="0 0 86 ${H + 26}" role="img" aria-label="Profil, djup ${num(f.total_depth_m)} meter">
      <g class="strata">
        <rect x="20" y="14" width="46" height="${yj}" fill="#8A7A63"/>
        <rect x="20" y="${14 + yj}" width="46" height="${H - yj}" fill="#5D6E75"/>
        <rect x="20" y="${14 + yv}" width="46" height="${H - yv}" fill="#2A6F80" opacity=".55"/>
        <rect x="20" y="14" width="46" height="${yf}" fill="none" stroke="#C9A227" stroke-width="2.5"/>
        <rect x="41" y="14" width="4" height="${H}" fill="#0E1F2A" opacity=".55"/>
      </g>
      <line x1="14" y1="14" x2="72" y2="14" stroke="#0E1F2A" stroke-width="1.5"/>
      <text x="43" y="${H + 24}" font-family="IBM Plex Mono, monospace" font-size="9" fill="#6B7A80" text-anchor="middle">BERG</text>
    </svg>
    <div class="lbls">
      <div class="plabel"><span class="t">Markyta</span><span class="d">0 m</span></div>
      <div class="plabel"><span class="t">Foderrör</span><span class="d">${num(f.casing_length_m, " m")}</span></div>
      <div class="plabel"><span class="t">Vattennivå</span><span class="d">${num(f.water_level_m, " m")}</span></div>
      <div class="plabel"><span class="t">Totalt djup</span><span class="d">${num(f.total_depth_m, " m")}</span></div>
    </div></div>`;
}

/* ---------------- vyer ---------------- */
async function viewDashboard() {
  const token = claim();
  mountShell(`<div class="skel" style="width:40%"></div><div class="skel"></div><div class="skel"></div>`);
  const d = await api("/dashboard");
  const attention = d.attention.length
    ? d.attention
        .map(
          (f) => `<div class="filerow" style="cursor:pointer" onclick="go('kund','${f.customer_id}')">
      <div class="ftype ${f.status === "action" ? "pdf" : "other"}" style="${f.status === "action" ? "background:#A6402F" : "background:#B3801F"}">${esc(f.facility_no.replace(/^B-/, ""))}</div>
      <div style="flex:1;min-width:0"><div style="font-weight:600">${esc(f.customer.name)}</div>
        <div class="fmeta">${esc(f.facility_type)} · ${num(f.total_depth_m, " m")} · service senast ${f.service_due || "okänt"}</div></div>
      ${tag(f.status)}</div>`
        )
        .join("")
    : `<div class="empty"><div class="big">Inget att planera in</div><p>Alla anläggningar ligger inom sitt serviceintervall.</p></div>`;

  if (!current(token)) return;
  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">${dt(new Date().toISOString(), false)}</div><h1>Översikt</h1>
      <p class="lead">${d.counts.customers} kunder och ${d.counts.facilities} anläggningar i registret.</p></div>
    <div class="row">
      ${S.user.role === "lasare" ? "" : `<button class="btn ghost" onclick="go('besok')">Offert på förfrågan</button>`}
      <button class="btn pri" onclick="go('ny')">+ Ny brunn eller pump</button>
    </div>
  </div>
  <div class="stats">
    <div class="stat"><div class="v">${d.counts.customers}</div><div class="l">Kunder</div></div>
    <div class="stat"><div class="v">${d.counts.facilities}</div><div class="l">Anläggningar</div></div>
    <div class="stat warn"><div class="v">${d.counts.soon}</div><div class="l">Service snart</div></div>
    <div class="stat bad"><div class="v">${d.counts.action}</div><div class="l">Åtgärd krävs</div></div>
  </div>
  <div class="grid2">
    <div class="card"><div class="hd"><h2>Senaste journalanteckningar</h2></div><div class="pad" style="padding-top:4px">
      ${
        d.latest_journal.length
          ? d.latest_journal
              .map(
                (j) => `<div class="jentry" style="cursor:pointer" onclick="go('kund','${j.customer_id}')">
        <div class="jmeta"><span class="dt">${dt(j.created_at, false)}</span>${dt(j.created_at).slice(11)} · ${esc(j.author_name)}</div>
        <div class="jbody"><div class="h"><span class="ttl">${esc(j.title)}</span><span class="tag n">${esc(j.entry_type)}</span></div>
          <p class="tsub">${esc(j.customer.name)}</p></div></div>`
              )
              .join("")
          : `<div class="empty"><div class="big">Journalen är tom</div><p>Anteckningar du skriver hamnar här.</p></div>`
      }
    </div></div>
    <div class="card"><div class="hd"><h2>Att planera in</h2></div><div class="pad" style="padding-top:4px">${attention}</div></div>
  </div>`;
}

async function viewCustomers() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const rows = await api("/customers");
  S.data.customers = rows;
  const f = S.filter.customerStatus || "";
  const shown = f ? rows.filter((c) => c.status === f) : rows;
  if (!current(token)) return;
  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Register</div><h1>Kunder</h1><p class="lead">${shown.length} av ${rows.length} visas.</p></div>
    <div class="row">
      <select style="width:auto" onchange="S.filter.customerStatus=this.value;viewCustomers()">
        ${[["", "Alla statusar"], ["ok", "I drift"], ["soon", "Service snart"], ["action", "Åtgärd krävs"]]
          .map(([v, l]) => `<option value="${v}"${f === v ? " selected" : ""}>${l}</option>`)
          .join("")}
      </select>
      <button class="btn pri" onclick="go('ny')">+ Ny anläggning</button>
    </div>
  </div>
  <div class="card">
    <table><thead><tr><th>Kundnr</th><th>Namn</th><th>Fastighet</th><th>Anläggningar</th><th>Pump</th><th>Status</th></tr></thead>
    <tbody>${shown
      .map((c) => {
        const pumps = [...new Set(c.facilities.map((x) => [x.pump_manufacturer, x.pump_model].filter(Boolean).join(" ")).filter(Boolean))];
        return `<tr class="clickable" onclick="go('kund','${c.id}')">
        <td data-l="Kundnr" class="tid">${esc(c.customer_no)}</td>
        <td data-l="Namn"><div class="tname">${esc(c.name)}</div><div class="tsub">${esc(c.customer_type)} · ${esc(c.phone || "")}</div></td>
        <td data-l="Fastighet">${esc(c.property_designation || "—")}<div class="tsub">${esc(c.municipality || "")}</div></td>
        <td data-l="Anläggningar" class="tid">${c.facilities.map((x) => esc(x.facility_no)).join(", ") || "—"}</td>
        <td data-l="Pump" class="tid">${esc(pumps.join(", ") || "—")}</td>
        <td data-l="Status">${tag(c.status)}</td></tr>`;
      })
      .join("")}</tbody></table>
    ${shown.length ? "" : `<div class="empty"><div class="big">Inga kunder att visa</div><p>Byt filter eller registrera en ny anläggning.</p></div>`}
  </div>`;
}

async function viewPumps() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const [pumps, facets] = await Promise.all([api("/pumps"), api("/facets")]);
  if (!current(token)) return;
  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Flotta</div><h1>Pumpar och modeller</h1>
      <p class="lead">En rad per modell. Klicka på en modell för att se alla berörda kunder – underlaget du behöver om en serie visar sig ha fabriksfel.</p></div>
  </div>
  <div class="card">
    <table><thead><tr><th>Tillverkare</th><th>Modell</th><th>Antal</th><th>Först installerad</th><th>Senast installerad</th><th></th></tr></thead>
    <tbody>${
      pumps.length
        ? pumps
            .map(
              (p) => `<tr class="clickable" onclick="openModel('${encodeURIComponent(p.pump_manufacturer)}','${encodeURIComponent(p.pump_model)}')">
      <td data-l="Tillverkare"><span class="tname">${esc(p.pump_manufacturer || "Okänd")}</span></td>
      <td data-l="Modell" class="mono">${esc(p.pump_model)}</td>
      <td data-l="Antal"><span class="tag n">${p.count} st</span></td>
      <td data-l="Först" class="tid">${esc(p.first_installed || "—")}</td>
      <td data-l="Senast" class="tid">${esc(p.last_installed || "—")}</td>
      <td>→</td></tr>`
            )
            .join("")
        : ""
    }</tbody></table>
    ${pumps.length ? "" : `<div class="empty"><div class="big">Inga pumpar registrerade</div><p>Fyll i tillverkare och modell på en anläggning, så syns den här.</p></div>`}
  </div>
  <p class="lead" style="margin-top:16px">Behöver du filtrera på annat än modell – djup, typ, installationsdatum – finns
    <a href="#/anlaggningar">hela anläggningslistan</a> med fler filter. Modeller i registret: ${facets.models.length}.</p>`;
}

function openModel(manufacturer, model) {
  S.filter = { pump_manufacturer: decodeURIComponent(manufacturer), pump_model: decodeURIComponent(model) };
  go("anlaggningar");
}

async function viewFacilities() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const facets = await api("/facets");
  const f = S.filter;
  const qs = new URLSearchParams();
  ["pump_manufacturer", "pump_model", "facility_type", "status", "installed_from", "installed_to"].forEach((k) => {
    if (f[k]) qs.set(k, f[k]);
  });
  const rows = await api(`/facilities?${qs}`);
  const active = [f.pump_manufacturer, f.pump_model, f.facility_type, f.status && STATUS[f.status]].filter(Boolean);

  const opt = (value, label, current) => `<option value="${esc(value)}"${current === value ? " selected" : ""}>${esc(label)}</option>`;
  if (!current(token)) return;
  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Flotta</div><h1>Anläggningar</h1>
      <p class="lead">${rows.length} träffar${active.length ? " · filter: " + esc(active.join(" · ")) : ""}</p></div>
    <div class="row">
      <button class="btn ghost sm" onclick="clearFilter()">Rensa filter</button>
      <button class="btn sm" onclick="exportCsv()">Exportera CSV</button>
    </div>
  </div>
  <div class="card" style="margin-bottom:16px"><div class="pad">
    <div class="fgrid">
      <div><label class="f">Tillverkare</label><select onchange="setFilter('pump_manufacturer',this.value)">
        ${opt("", "Alla", f.pump_manufacturer || "")}${facets.manufacturers.map((m) => opt(m, m, f.pump_manufacturer || "")).join("")}</select></div>
      <div><label class="f">Modell</label><select onchange="setFilter('pump_model',this.value)">
        ${opt("", "Alla", f.pump_model || "")}${facets.models.map((m) => opt(m, m, f.pump_model || "")).join("")}</select></div>
      <div><label class="f">Typ</label><select onchange="setFilter('facility_type',this.value)">
        ${opt("", "Alla", f.facility_type || "")}${facets.facility_types.map((m) => opt(m, m, f.facility_type || "")).join("")}</select></div>
      <div><label class="f">Status</label><select onchange="setFilter('status',this.value)">
        ${opt("", "Alla", f.status || "")}${Object.entries(STATUS).map(([v, l]) => opt(v, l, f.status || "")).join("")}</select></div>
      <div><label class="f">Installerad från</label><input type="date" value="${esc(f.installed_from || "")}" onchange="setFilter('installed_from',this.value)"></div>
      <div><label class="f">Installerad till</label><input type="date" value="${esc(f.installed_to || "")}" onchange="setFilter('installed_to',this.value)"></div>
    </div>
  </div></div>
  <div class="card">
    <table><thead><tr><th>ID</th><th>Kund</th><th>Typ</th><th>Djup</th><th>Pump</th><th>Serienr</th><th>Installerad</th><th>Status</th></tr></thead>
    <tbody>${rows
      .map(
        (x) => `<tr class="clickable" onclick="go('kund','${x.customer_id}')">
      <td data-l="ID" class="tid">${esc(x.facility_no)}</td>
      <td data-l="Kund"><span class="tname">${esc(x.customer.name)}</span><div class="tsub">${esc(x.customer.property_designation || "")} ${esc(x.customer.municipality || "")}</div></td>
      <td data-l="Typ">${esc(x.facility_type)}</td>
      <td data-l="Djup" class="tid">${num(x.total_depth_m, " m")}</td>
      <td data-l="Pump">${esc([x.pump_manufacturer, x.pump_model].filter(Boolean).join(" ") || "—")}</td>
      <td data-l="Serienr" class="tid">${esc(x.pump_serial || "—")}</td>
      <td data-l="Installerad" class="tid">${esc(x.pump_installed_at || "—")}</td>
      <td data-l="Status">${tag(x.status)}</td></tr>`
      )
      .join("")}</tbody></table>
    ${rows.length ? "" : `<div class="empty"><div class="big">Inga träffar</div><p>Lätta på filtren för att se fler anläggningar.</p></div>`}
  </div>`;
}

function setFilter(key, value) {
  if (value) S.filter[key] = value;
  else delete S.filter[key];
  viewFacilities();
}
function clearFilter() {
  S.filter = {};
  viewFacilities();
}
async function exportCsv() {
  const qs = new URLSearchParams();
  ["pump_manufacturer", "pump_model", "status"].forEach((k) => S.filter[k] && qs.set(k, S.filter[k]));
  const res = await fetch(`/api/facilities.csv?${qs}`, { headers: { Authorization: `Bearer ${S.token}` } });
  if (!res.ok) return toast("Exporten misslyckades", true);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `anlaggningar-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("CSV nedladdad");
}

async function viewJournalAll() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const [rows, facets] = await Promise.all([api("/journal"), api("/facets")]);
  const t = S.filter.entryType || "";
  const shown = t ? rows.filter((j) => j.entry_type === t) : rows;
  if (!current(token)) return;
  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Alla kunder</div><h1>Journal</h1>
      <p class="lead">Datum, tid och signatur sätts av servern när anteckningen sparas.</p></div>
    <select style="width:auto" onchange="S.filter.entryType=this.value;viewJournalAll()">
      <option value="">Alla typer</option>
      ${facets.entry_types.map((x) => `<option${t === x ? " selected" : ""}>${esc(x)}</option>`).join("")}
    </select>
  </div>
  <div class="card"><div class="pad">${
    shown.length
      ? shown.map((j) => journalEntryHtml(j, true)).join("")
      : `<div class="empty"><div class="big">Inga anteckningar</div></div>`
  }</div></div>`;
}

function journalEntryHtml(j, showCustomer = false) {
  const struken = j.retracted;
  const kanRedigera = !showCustomer && S.user.role !== "lasare";
  return `<div class="jentry"${showCustomer ? ` style="cursor:pointer" onclick="go('kund','${j.customer_id}')"` : ""}>
    <div class="jmeta"><span class="dt">${dt(j.created_at, false)}</span>${dt(j.created_at).slice(11)}<br>${esc(j.author_name)}</div>
    <div class="jbody"${struken ? ' style="opacity:.62"' : ""}>
      <div class="h"><span class="ttl"${struken ? ' style="text-decoration:line-through"' : ""}>${esc(j.title)}</span>
        <span class="tag n">${esc(j.entry_type)}</span>
        ${struken ? `<span class="tag action">Struken</span>` : ""}
        ${showCustomer && j.customer ? `<span class="tsub">${esc(j.customer.name)}</span>` : ""}</div>
      <p${struken ? ' style="text-decoration:line-through"' : ""}>${esc(j.body)}</p>
      ${
        struken
          ? `<p class="tsub" style="margin-top:6px">Struken ${dt(j.retracted_at)} av ${esc(j.retracted_by)}${
              j.retraction_reason ? ": " + esc(j.retraction_reason) : ""
            }</p>`
          : ""
      }
      ${
        j.attachments && j.attachments.length
          ? `<div class="jatt">${j.attachments.map((a) => `<a href="#" onclick="openFile('${a.id}');return false">↳ ${esc(a.filename)}</a>`).join("")}</div>`
          : ""
      }
      ${
        kanRedigera
          ? `<div class="row" style="margin-top:8px;gap:8px">
        ${
          struken
            ? `<button class="btn ghost sm" onclick="retractEntry('${j.id}', true)">Ångra strykning</button>`
            : `<button class="btn ghost sm" onclick="retractEntry('${j.id}', false)">Stryk</button>`
        }
        ${S.user.role === "admin" ? `<button class="btn danger sm" onclick="deleteEntry('${j.id}')">Radera</button>` : ""}
      </div>`
          : ""
      }
    </div></div>`;
}

async function retractEntry(id, undo) {
  let body = { undo: true };
  if (!undo) {
    const reason = prompt(
      "Varför stryks anteckningen? Texten står kvar men märks som struken, så historiken är intakt."
    );
    if (reason === null) return;
    body = { reason };
  }
  await api(`/journal/${id}`, { method: "PATCH", body });
  toast(undo ? "Strykningen ångrad" : "Anteckningen struken");
  S.data.journal = await api(`/customers/${S.data.customer.id}/journal`);
  renderTab();
}

async function deleteEntry(id) {
  if (!confirm("Radera anteckningen helt? Det går inte att ångra.\n\nÖverväg Stryk i stället, då bevaras historiken.")) return;
  await api(`/journal/${id}`, { method: "DELETE" });
  toast("Anteckningen raderad");
  S.data.journal = await api(`/customers/${S.data.customer.id}/journal`);
  renderTab();
}

/* ---------------- kundvy ---------------- */
async function viewCustomer() {
  const token = claim();
  mountShell(`<div class="skel" style="width:35%"></div><div class="skel"></div><div class="skel"></div>`);
  const id = S.id;
  const [c, journal, files, reminders, offerter, order] = await Promise.all([
    api(`/customers/${id}`),
    api(`/customers/${id}/journal`),
    api(`/customers/${id}/files`),
    api(`/reminders?status=open&customer_id=${id}`),
    api(`/quotes?customer_id=${id}`),
    api(`/work-orders?customer_id=${id}`),
  ]);
  S.data.customer = c;
  S.data.journal = journal;
  S.data.files = files;
  S.data.reminders = reminders;
  S.data.quotes = offerter;
  S.data.orders = order;

  const docs = files.filter((f) => f.kind === "dokument");
  const imgs = files.filter((f) => f.kind === "bild");
  const facility = c.facilities[0];
  const T = (id_, label, n) =>
    `<button class="${S.tab === id_ ? "on" : ""}" onclick="go('kund','${c.id}/${id_}')">${label}${
      n !== undefined ? `<span class="c">${n}</span>` : ""
    }</button>`;

  if (!current(token)) return;
  $("#view").innerHTML = `
  <button class="back" onclick="go('kunder')">← Alla kunder</button>
  <div class="chead">
    <div class="spread" style="margin-bottom:0">
      <div><div class="eyebrow">${esc(c.customer_no)} · registrerad ${dt(c.created_at, false)}</div>
        <h1>${esc(c.name)}</h1>
        <p class="lead">${esc(c.property_designation || "")}${c.municipality ? ", " + esc(c.municipality) : ""}</p></div>
      <div class="row">${tag(c.status)}
        ${S.user.role === "lasare" ? "" : `<button class="btn ghost sm" onclick="editCustomer()">Redigera kund</button>`}
        <button class="btn sm" onclick="go('kund','${c.id}/journal')">+ Journalanteckning</button></div>
    </div>
    <div class="facts">
      <div class="fact"><div class="k">Kundtyp</div><div class="v">${esc(c.customer_type)}</div></div>
      <div class="fact"><div class="k">Telefon</div><div class="v"><a href="tel:${esc(c.phone)}">${esc(c.phone || "—")}</a></div></div>
      <div class="fact"><div class="k">E-post</div><div class="v"><a href="mailto:${esc(c.email)}">${esc(c.email || "—")}</a></div></div>
      <div class="fact"><div class="k">Anläggningar</div><div class="v mono" style="font-size:13.5px">${c.facilities.map((x) => esc(x.facility_no)).join(", ") || "—"}</div></div>
      <div class="fact"><div class="k">Service senast</div><div class="v mono" style="font-size:13.5px">${esc(facility?.service_due || "—")}</div></div>
    </div>
    <div id="cedit"></div>
  </div>
  <div class="grid2">
    <div>
      <div class="tabs">${T("oversikt", "Översikt")}${T("journal", "Journal", journal.length)}${T("ekonomi", "Ekonomi", offerter.length + order.length)}${T("filer", "Filer", files.length)}${T("anlaggning", "Anläggning", c.facilities.length)}</div>
      <div class="card"><div class="pad" id="tabbody"></div></div>
    </div>
    ${
      facility
        ? `<div class="card"><div class="hd"><h2>${esc(facility.facility_no)} · profil</h2>${tag(facility.status)}</div>
      <div class="pad">${profile(facility)}
        <div class="facts tight" style="margin-top:16px">
          <div class="fact"><div class="k">Typ</div><div class="v">${esc(facility.facility_type)}</div></div>
          <div class="fact"><div class="k">Jorddjup</div><div class="v mono">${num(facility.soil_depth_m, " m")}</div></div>
          <div class="fact"><div class="k">Kapacitet</div><div class="v mono">${num(facility.capacity_lph, " l/h")}</div></div>
          <div class="fact"><div class="k">Pump</div><div class="v">${esc([facility.pump_manufacturer, facility.pump_model].filter(Boolean).join(" ") || facility.pump_status || "—")}</div></div>
          <div class="fact"><div class="k">Serienummer</div><div class="v mono" style="font-size:13px">${esc(facility.pump_serial || "—")}</div></div>
          <div class="fact"><div class="k">Installerad</div><div class="v mono">${esc(facility.pump_installed_at || "—")}</div></div>
        </div></div></div>`
        : ""
    }
  </div>
  <div id="sharebox"></div>`;
  renderTab();

  // "Slå ihop med resan" hämtas efter att kundkortet visats, så sidan inte
  // får vänta på den. Infogas bara om användaren står kvar på samma kund,
  // annars skriver ett sent svar över den vy man just bytt till.
  const trip = c.facilities.find((f) => f.latitude != null && f.longitude != null) || c.facilities[0];
  if (trip) {
    try {
      const html = await nearbyCard(trip);
      const view = $("#view");
      if (html && view && current(token) && S.route === "kund" && S.id === c.id) {
        view.insertAdjacentHTML("beforeend", html);
      }
    } catch (err) {
      console.warn("kunde inte hämta jobb i närheten:", err.message);
    }
  }
}

function renderTab() {
  const c = S.data.customer;
  const body = $("#tabbody");
  if (!body) return;
  slappBlobbar();
  if (S.tab === "journal") body.innerHTML = tabJournal(c, S.data.journal);
  else if (S.tab === "ekonomi") body.innerHTML = tabEconomy(c, S.data.quotes, S.data.orders);
  else if (S.tab === "filer") body.innerHTML = tabFiler(c, S.data.files);
  else if (S.tab === "anlaggning") body.innerHTML = tabFacilities(c);
  else body.innerHTML = tabOversikt(c);
  wireUploads();
  hydreraBilder(body);
}

function tabJournal(c, journal) {
  const readOnly = S.user.role === "lasare" || !c.facilities.length;
  const facilityOptions = c.facilities
    .map((f) => `<option value="${f.id}">${esc(f.facility_no)} · ${esc(f.facility_type)}</option>`)
    .join("");
  if (!c.facilities.length && S.user.role !== "lasare") {
    return `<div class="empty"><div class="big">Ingen anläggning ännu</div>
      <p>Journalen hör alltid till en brunn eller pump. Lägg till en anläggning först,
      så går det att skriva anteckningar.</p>
      <button class="btn pri sm" style="margin-top:10px" onclick="startFacilityFor('${c.id}')">
        Lägg till anläggning</button></div>`;
  }
  return `
  ${
    readOnly
      ? ""
      : `<div class="jnew">
    <div class="spread" style="margin-bottom:0;align-items:center">
      <strong style="font-family:var(--cond);text-transform:uppercase;letter-spacing:.05em">Ny anteckning</strong>
      <span class="stamp">${nowStamp()} · ${esc(S.user.full_name || S.user.username)}</span>
    </div>
    <p class="hint">Tidsstämpeln sätts av servern när du sparar, inte av telefonens klocka.</p>
    <div class="fgrid">
      <div><label class="f" for="jtyp">Typ av händelse</label>
        <select id="jtyp">${["Service", "Borrning", "Installation", "Telefon", "Besök", "Anmärkning", "Vattenprov", "Övrigt"]
          .map((t) => `<option>${t}</option>`)
          .join("")}</select></div>
      <div><label class="f" for="jfac">Gäller anläggning</label>
        <select id="jfac">${facilityOptions}</select>
        ${
          c.facilities.length
            ? ""
            : `<div class="hint" style="color:var(--alert)">Kunden har ingen anläggning än.
               Lägg till en under fliken Anläggning först.</div>`
        }</div>
    </div>
    <label class="f" for="jttl">Rubrik</label>
    <input id="jttl" placeholder="T.ex. Filterbyte och tryckkontroll">
    <label class="f" for="jtxt">Anteckning</label>
    <textarea id="jtxt" placeholder="Mätvärden, åtgärder, vad kunden sa, vad som ska följas upp."></textarea>
    <div class="fgrid">
      <div><label class="f" for="jfup">Följ upp senast <span style="text-transform:none;letter-spacing:0">(valfritt)</span></label>
        <input id="jfup" type="date"></div>
      <div><label class="f" for="jfupt">Rubrik på uppföljningen</label>
        <input id="jfupt" placeholder="Lämna tom för att återanvända rubriken ovan"></div>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="btn pri sm" id="jsave">Spara anteckning</button>
      <span class="hint" style="margin:0">Fyller du i ett datum skapas en påminnelse kopplad till anteckningen.</span>
    </div></div>`
  }
  ${journal.length ? journal.map((j) => journalEntryHtml(j)).join("") : `<div class="empty"><div class="big">Journalen är tom</div><p>Första anteckningen skriver du här ovanför.</p></div>`}`;
}

async function saveJournal() {
  const btn = $("#jsave");
  btn.disabled = true;
  try {
    await api(`/customers/${S.data.customer.id}/journal`, {
      method: "POST",
      body: {
        entry_type: $("#jtyp").value,
        facility_id: $("#jfac").value,
        title: $("#jttl").value,
        body: $("#jtxt").value,
        followup_date: $("#jfup").value || "",
        followup_title: $("#jfupt").value || "",
      },
    });
    toast($("#jfup").value ? "Anteckning och uppföljning sparade" : "Anteckning sparad");
    S.data.journal = await api(`/customers/${S.data.customer.id}/journal`);
    S.data.reminders = await api(`/reminders?status=open&customer_id=${S.data.customer.id}`);
    refreshBadge();
    renderTab();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}

function fileIcon(f) {
  if (f.kind === "bild") return "img";
  if (f.content_type.includes("pdf")) return "pdf";
  if (f.content_type.includes("word")) return "doc";
  return "other";
}
function fileLabel(f) {
  const t = fileIcon(f);
  return t === "pdf" ? "PDF" : t === "doc" ? "DOCX" : t === "img" ? "BILD" : "FIL";
}

function tabFiles(c, files) {
  const readOnly = S.user.role === "lasare";
  return `
  ${
    readOnly
      ? ""
      : `<div class="drop" id="drop">
    <div class="big">Släpp filer här</div>
    <p style="margin:6px 0 0">PDF, DOCX, XLSX eller bild. Kopplas till ${esc(c.name)}.</p>
    <button class="btn ghost sm" style="margin-top:10px" onclick="document.getElementById('fin').click()">Välj filer</button>
    <input type="file" id="fin" multiple hidden accept=".pdf,.docx,.doc,.xlsx,.txt,image/*">
    <div class="progress" id="prog" hidden><i style="width:0"></i></div>
  </div>`
  }
  <div class="imgs" style="margin-top:16px">${
    files.length
      ? files.map((f) => docCard(f, readOnly)).join("")
      : ""
  }</div>
  ${files.length ? "" : `<div class="empty"><div class="big">Inga dokument</div><p>Ladda upp borrprotokoll, intyg och offerter här.</p></div>`}`;
}

function docCard(f, readOnly) {
  const typ = fileIcon(f);
  const preview = f.has_thumb
    ? `<img class="laddar" data-auth-src="/api/files/${f.id}/thumb" alt="${esc(f.filename)}">`
    : `<div class="noprev ${typ}"><span>${fileLabel(f)}</span></div>`;
  return `<div class="thumb">
    <button class="previewbtn" onclick="openFile('${f.id}')" title="Öppna ${esc(f.filename)}">${preview}</button>
    <div class="cap">
      <span style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
        title="${esc(f.filename)}">${esc(f.caption || f.filename)}</span>
      <span class="m">${bytes(f.size_bytes)} · ${dt(f.uploaded_at, false)}${
        readOnly ? "" : ` · <button onclick="deleteFile('${f.id}')" class="linkbtn">ta bort</button>`
      }</span>
    </div></div>`;
}

function tabImages(c, imgs) {
  const readOnly = S.user.role === "lasare";
  return `
  <div class="imgs">
    ${imgs.map((f) => docCard(f, readOnly)).join("")}
    ${
      readOnly
        ? ""
        : `<button class="thumb" style="border-style:dashed;cursor:pointer;background:#FAFCFC;display:grid;place-items:center;min-height:150px;color:var(--ink-2)"
        onclick="document.getElementById('fin').click()">
        <span style="text-align:center"><span style="font-size:22px">+</span><br>
        <span style="font-family:var(--cond);text-transform:uppercase;font-size:13px;letter-spacing:.05em">Lägg till bild</span></span></button>`
    }
  </div>
  ${
    readOnly
      ? ""
      : `<div class="row" style="margin-top:14px">
    <input type="file" id="fin" multiple hidden accept="image/*" capture="environment">
    <button class="btn ghost sm" onclick="document.getElementById('fin').click()">Ta foto eller välj bild</button>
    <span class="hint" style="margin:0">På telefonen öppnas kameran direkt.</span>
    <div class="progress" id="prog" hidden style="width:100%"><i style="width:0"></i></div>
  </div>`
  }
  ${imgs.length ? "" : `<p class="hint" style="margin-top:12px">Inga bilder än. Foton från borrplatsen hamnar här.</p>`}`;
}

function tabFacilities(c) {
  return (
    c.facilities
      .map(
        (f) => `<div style="padding-bottom:16px;margin-bottom:16px;border-bottom:1px solid #E9EEEE">
    <div class="spread" style="margin-bottom:6px">
      <div><div class="eyebrow">${esc(f.facility_no)}</div><strong style="font-size:16px">${esc(f.facility_type)}</strong></div>
      ${tag(f.status)}</div>
    <div class="facts" style="border-top:none;padding-top:6px;margin-top:0">
      <div class="fact"><div class="k">Borrad</div><div class="v mono">${esc(f.drilled_at || "—")}</div></div>
      <div class="fact"><div class="k">Totalt djup</div><div class="v mono">${num(f.total_depth_m, " m")}</div></div>
      <div class="fact"><div class="k">Jorddjup</div><div class="v mono">${num(f.soil_depth_m, " m")}</div></div>
      <div class="fact"><div class="k">Foderrör</div><div class="v mono">${num(f.casing_length_m, " m")}</div></div>
      <div class="fact"><div class="k">Vattennivå</div><div class="v mono">${num(f.water_level_m, " m")}</div></div>
      <div class="fact"><div class="k">Kapacitet</div><div class="v mono">${num(f.capacity_lph, " l/h")}</div></div>
      <div class="fact"><div class="k">Pump</div><div class="v">${esc([f.pump_manufacturer, f.pump_model].filter(Boolean).join(" ") || "—")}</div></div>
      <div class="fact"><div class="k">Serienummer</div><div class="v mono" style="font-size:13px">${esc(f.pump_serial || "—")}</div></div>
      <div class="fact"><div class="k">Pumpdjup</div><div class="v mono">${num(f.pump_depth_m, " m")}</div></div>
      <div class="fact"><div class="k">Tryckkärl</div><div class="v">${esc(f.pressure_tank || "—")}</div></div>
      <div class="fact"><div class="k">Serviceintervall</div><div class="v mono">${f.service_interval_months} mån</div></div>
      <div class="fact"><div class="k">Senaste service</div><div class="v mono">${esc(f.last_service_at || "—")}</div></div>
      <div class="fact"><div class="k">Koordinater</div><div class="v mono" style="font-size:12.5px">${esc(f.coordinates || "—")}</div></div>
      <div class="fact"><div class="k">Vattenprov</div><div class="v">${esc(f.water_sample || "—")}</div></div>
    </div>
    ${f.bedrock_notes ? `<p class="lead" style="margin-top:10px"><span class="eyebrow">Berg och lager</span>${esc(f.bedrock_notes)}</p>` : ""}
    ${f.access_notes ? `<p class="lead" style="margin-top:10px"><span class="eyebrow">Åtkomst</span>${esc(f.access_notes)}</p>` : ""}
    ${
      S.user.role === "lasare"
        ? ""
        : `<div class="row" style="margin-top:12px">
      <button class="btn ghost sm" onclick="editFacility('${f.id}')">Redigera</button>
      <button class="btn sm" onclick="pumpChange('${f.id}')">Byt pump</button>
      <button class="btn ghost sm" onclick="markService('${f.id}')">Service idag</button>
      <button class="btn ghost sm" onclick="setStatus('${f.id}','${f.status_manual === "action" ? "ok" : "action"}')">
        ${f.status_manual === "action" ? "Rensa flagga" : "Flagga åtgärd"}</button>
      <button class="btn ghost sm" onclick="facilityBriefing('${f.id}')">Grannbrunnar</button>
      <button class="btn ghost sm" onclick="shareDialog({facility_id:'${f.id}'})">Dela</button>
      <button class="btn danger sm" onclick="removeFacility('${f.id}','${esc(f.facility_no)}')">Ta bort</button>
    </div>
    <div id="fedit-${f.id}"></div>`
    }
  </div>`
      )
      .join("") +
    (S.user.role === "lasare"
      ? ""
      : `<button class="btn ghost sm" onclick="startFacilityFor('${c.id}')">+ Lägg till anläggning på denna kund</button>`)
  );
}

async function markService(facilityId) {
  const today = new Date().toISOString().slice(0, 10);
  await api(`/facilities/${facilityId}`, { method: "PATCH", body: { last_service_at: today, status: "ok" } });
  toast(`Service registrerad ${today}`);
  viewCustomer();
}
async function setStatus(facilityId, status) {
  await api(`/facilities/${facilityId}`, { method: "PATCH", body: { status } });
  toast(status === "action" ? "Flaggad för åtgärd" : "Flaggan rensad");
  viewCustomer();
}






/* ---------------- platsbesök ---------------- */
const BESOK_STATUS = {
  planerat: ["Inbokat", "n"],
  genomfort: ["Besökt", "n"],
  offert: ["Offert lämnad", "soon"],
  vunnen: ["Blev kund", "ok"],
  forlorad: ["Blev inget", "action"],
};

async function viewVisits() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const filter = S.filter.visitStatus || "aktiva";
  const visits = await api(`/visits?status=${filter}`);
  if (!current(token)) return;

  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Innan det finns en kund</div><h1>Platsbesök</h1>
      <p class="lead">Här ligger de du varit ute hos eller ska åka till, utan att de behöver läggas
      upp som kunder. Blir det affär skapas kunden av besöket med ett klick, och det som redan är
      ifyllt följer med.</p></div>
    <div class="row">
      <select style="width:auto" onchange="S.filter.visitStatus=this.value;viewVisits()">
        ${[["aktiva", "Pågående"], ["", "Alla"], ["vunnen", "Blev kund"], ["forlorad", "Blev inget"]]
          .map(([v, l]) => `<option value="${v}"${filter === v ? " selected" : ""}>${l}</option>`)
          .join("")}
      </select>
      ${
        S.user.role === "lasare"
          ? ""
          : `<button class="btn ghost" onclick="nyForfragan()">Offert på förfrågan</button>
             <button class="btn pri" onclick="newVisit()">+ Nytt besök</button>`
      }
    </div>
  </div>
  <div id="forfraganform"></div>
  <div id="visitform"></div>
  <div class="card">
    <table><thead><tr><th>Nr</th><th>Kontakt</th><th>Fastighet</th><th>Planerat</th>
      <th>Ärende</th><th>Status</th></tr></thead>
    <tbody>${visits
      .map((v) => {
        const [text, klass] = BESOK_STATUS[v.status] || [v.status, "n"];
        return `<tr class="clickable" onclick="go('besok','${v.id}')">
        <td data-l="Nr" class="tid">${esc(v.visit_no)}</td>
        <td data-l="Kontakt"><span class="tname">${esc(v.contact_name || "—")}</span>
          <div class="tsub">${esc(v.phone || "")}</div></td>
        <td data-l="Fastighet">${esc(v.property_designation || v.address || "—")}
          <div class="tsub">${esc(v.municipality || "")}</div></td>
        <td data-l="Planerat" class="tid">${esc(v.planned_at || "—")}</td>
        <td data-l="Ärende">${esc((v.errand || "").slice(0, 40))}</td>
        <td data-l="Status"><span class="tag ${klass}">${esc(text)}</span></td></tr>`;
      })
      .join("")}</tbody></table>
    ${visits.length ? "" : `<div class="empty"><div class="big">Inga besök här</div>
      <p>Lägg upp ett besök när någon hör av sig, så har du underlaget med dig när du åker.</p></div>`}
  </div>`;
}

/* Två formulär på samma sida som öppnas ovanpå varandra gör att det nedre
   hamnar utanför skärmen på telefon, och det ser ut som att knappen är död.
   Därför stängs det andra, och sidan scrollar till det som öppnades. */
function stangAndraFormular(behall) {
  for (const id of ["visitform", "forfraganform", "artform", "sharebox"]) {
    if (id === behall) continue;
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  }
  const kort = document.getElementById("forfragankort");
  if (kort && behall !== "forfraganform") kort.remove();
}

function newVisit() {
  const box = $("#visitform");
  if (box.innerHTML) return (box.innerHTML = "");
  stangAndraFormular("visitform");
  box.innerHTML = `
  <div class="card" style="margin-bottom:16px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Nytt platsbesök</h2></div>
    <div class="pad">
      <p class="hint" style="margin-top:0">Fyll i så lite eller mycket du vill. Det enda som krävs
        är något att känna igen platsen på. Anger du adress eller fastighet slås koordinaten upp
        automatiskt, så att underlaget om grannbrunnar finns direkt.</p>
      <div class="fgrid">
        ${fld("v_name", "Kontaktperson", "")}
        ${fld("v_phone", "Telefon", "", "tel")}
        ${fld("v_prop", "Fastighetsbeteckning", "")}
        ${fld("v_addr", "Adress", "")}
        ${fld("v_mun", "Kommun", "")}
        ${fld("v_date", "Planerat besök", "", "date")}
      </div>
      <label class="f" for="v_errand">Ärende</label>
      <input id="v_errand" placeholder="Vill borra för vatten, dålig kapacitet i gamla brunnen">
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="saveNewVisit()">Spara besök</button>
        <button class="btn ghost sm" onclick="document.getElementById('visitform').innerHTML=''">Avbryt</button>
      </div>
    </div></div>`;
  scrollTill(box);
  const f = $("#v_name");
  if (f) f.focus();
}

async function _saveNewVisit() {
  const body = {
    contact_name: val("v_name").trim(),
    phone: val("v_phone"),
    property_designation: val("v_prop").trim(),
    address: val("v_addr"),
    municipality: val("v_mun"),
    planned_at: val("v_date"),
    errand: val("v_errand"),
  };
  if (!body.contact_name && !body.property_designation && !body.address)
    return toast("Ange kontaktperson, fastighet eller adress", true);
  try {
    const v = await api("/visits", { method: "POST", body });
    toast(v.geocode ? `${v.visit_no} skapat, koordinat: ${v.geocode}` : `${v.visit_no} skapat`);
    go("besok", v.id);
  } catch (e) {
    toast(e.message, true);
  }
}

async function viewVisit() {
  const token = claim();
  mountShell(`<div class="skel" style="width:35%"></div><div class="skel"></div>`);
  const v = await api(`/visits/${S.id}`);
  if (!current(token)) return;
  S.data.visit = v;
  const [text, klass] = BESOK_STATUS[v.status] || [v.status, "n"];
  const laser = S.user.role === "lasare";

  $("#view").innerHTML = `
  <button class="back" onclick="go('besok')">← Alla besök</button>
  <div class="chead">
    <div class="spread" style="margin-bottom:0">
      <div><div class="eyebrow">${esc(v.visit_no)} · upplagt ${dt(v.created_at, false)} av ${esc(v.created_by)}</div>
        <h1>${esc(v.contact_name || v.property_designation || v.address || "Platsbesök")}</h1>
        <p class="lead">${esc(v.property_designation || "")}${v.municipality ? ", " + esc(v.municipality) : ""}</p></div>
      <div class="row"><span class="tag ${klass}">${esc(text)}</span>
        ${
          laser
            ? ""
            : v.customer_id
              ? `<button class="btn sm" onclick="go('kund','${v.customer_id}')">Öppna kunden</button>`
              : `<button class="btn pri sm" onclick="convertVisit()">Blev kund</button>`
        }
      </div>
    </div>
    <div class="facts">
      <div class="fact"><div class="k">Telefon</div><div class="v"><a href="tel:${esc(v.phone)}">${esc(v.phone || "—")}</a></div></div>
      <div class="fact"><div class="k">E-post</div><div class="v">${esc(v.email || "—")}</div></div>
      <div class="fact"><div class="k">Adress</div><div class="v">${esc(v.address || "—")}</div></div>
      <div class="fact"><div class="k">Planerat</div><div class="v mono">${esc(v.planned_at || "—")}</div></div>
      <div class="fact"><div class="k">Koordinat</div><div class="v mono" style="font-size:12.5px">${
        v.latitude
          ? `${v.latitude}, ${v.longitude}`
          : v.geocode_status === "pagar"
            ? `<span class="muted">hämtas…</span>`
            : "—"
      }</div></div>
      ${v.quote_amount ? `<div class="fact"><div class="k">Offert</div><div class="v mono">${v.quote_amount.toLocaleString("sv-SE")} kr</div></div>` : ""}
    </div>
  </div>

  <div class="grid2">
    <div>
      <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Inför besöket</h2>
        <span class="tag n">SGU</span></div>
        <div class="pad" id="briefing"><div class="skel"></div><div class="skel"></div></div></div>

      ${
        laser
          ? ""
          : `<div class="card"><div class="hd"><h2>Anteckningar och status</h2></div><div class="pad">
        <label class="f" for="v_errand2">Ärende</label>
        <input id="v_errand2" value="${esc(v.errand || "")}">
        <label class="f" for="v_notes">Anteckningar från platsen</label>
        <textarea id="v_notes" placeholder="Åtkomst för riggen, var kunden vill ha hålet, vad som sades.">${esc(v.notes || "")}</textarea>
        <div class="fgrid">
          ${fld("v_name2", "Kontaktperson", v.contact_name || "")}
          ${fld("v_phone2", "Telefon", v.phone || "", "tel")}
          ${fld("v_mail", "E-post", v.email || "", "email")}
          ${fld("v_prop2", "Fastighetsbeteckning", v.property_designation || "")}
          ${fld("v_addr2", "Adress", v.address || "")}
          ${fld("v_mun2", "Kommun", v.municipality || "")}
          ${fld("v_date2", "Planerat besök", v.planned_at || "", "date")}
          <div><label class="f" for="v_status">Status</label><select id="v_status">
            ${Object.entries(BESOK_STATUS)
              .map(([k, [l]]) => `<option value="${k}"${v.status === k ? " selected" : ""}>${l}</option>`)
              .join("")}</select></div>
          ${fld("v_quote", "Offertsumma (kr)", v.quote_amount ?? "", "number")}
        </div>
        <label class="f" for="v_coord">Koordinat</label>
        <input id="v_coord" value="${esc(v.coordinates || "")}"
          placeholder="Fylls i automatiskt från adressen">
        <div class="row" style="margin-top:8px">
          <button class="btn ghost sm" onclick="visitPosition()">Hämta min position</button>
          <button class="btn ghost sm" onclick="visitGeocode()">Slå upp adressen igen</button>
          <span class="hint" id="v_coordhint" style="margin:0">${
            v.latitude
              ? "Ändrar du adressen och sparar slås koordinaten upp på nytt."
              : "Saknas. Fyll i adress och spara, eller hämta din position på plats."
          }</span>
        </div>
        <div class="row" style="margin-top:14px">
          <button class="btn pri sm" onclick="saveVisit()">Spara</button>
          <button class="btn sm" onclick="valjMall(null,'${v.id}')">Skapa offert</button>
          <button class="btn ghost sm" onclick="shareDialog({visit_id:'${v.id}'})">Dela med extern borrare</button>
          <button class="btn danger sm" style="margin-left:auto" onclick="removeVisit()">Ta bort</button>
        </div>
      </div></div>`
      }
    </div>
    <div id="visitside"></div>
  </div>
  <div id="sharebox"></div>`;

  loadBriefing({ visit_id: v.id });
  loadVisitNearby(v);
  loadVisitQuotes(v);
}

async function loadVisitQuotes(v) {
  const offerter = await api(`/quotes?visit_id=${v.id}`);
  if (!offerter.length || S.route !== "besok" || S.id !== v.id) return;
  const holder = document.createElement("div");
  holder.innerHTML = `<div class="card" style="margin-top:18px">
    <div class="hd"><h2>Offerter på det här besöket</h2><span class="tag n">${offerter.length}</span></div>
    <div class="pad" style="padding-top:2px">${offerter
      .map((q) => {
        const [text, klass] = OFFERT_STATUS[q.status] || [q.status, "n"];
        return `<div class="filerow" style="cursor:pointer" onclick="go('offert','${q.id}')">
        <div class="ftype pdf">OFF</div>
        <div style="flex:1;min-width:0"><div style="font-weight:600">${esc(q.title || q.quote_no)}</div>
          <div class="fmeta">${esc(q.quote_no)} · ${dt(q.created_at, false)}</div></div>
        <span class="mono" style="font-weight:600">${kr(q.totals.brutto)} kr</span>
        <span class="tag ${klass}">${text}</span></div>`;
      })
      .join("")}</div></div>`;
  $("#view").appendChild(holder.firstElementChild);
}

/* Egna jobb och andra besök nära det här besöket. Hämtas efter att sidan visats,
   och infogas bara om användaren står kvar på samma besök. */
async function loadVisitNearby(v) {
  const token = renderSeq;
  const box = $("#visitside");
  if (!box) return;
  if (v.latitude == null) {
    box.innerHTML = `<div class="card"><div class="hd"><h2>Slå ihop med resan</h2></div>
      <div class="pad"><p class="hint" style="margin:0">Besöket saknar koordinat. Fyll i adressen
      och spara, så slås den upp automatiskt.</p></div></div>`;
    return;
  }
  box.innerHTML = `<div class="card"><div class="hd"><h2>Slå ihop med resan</h2></div>
    <div class="pad"><div class="skel"></div><div class="skel"></div></div></div>`;
  try {
    const r = await api(`/visits/${v.id}/nearby?radius_km=${S.filter.tripRadius || 30}`);
    if (token !== renderSeq || S.id !== v.id) return;
    const besok = r.results.filter((x) => x.typ === "besok");
    const jobb = r.results.filter((x) => x.typ !== "besok");
    box.innerHTML = `<div class="card">
      <div class="hd"><h2>Slå ihop med resan</h2>
        <span class="tag n">${r.results.length} inom ${r.radius_km} km</span></div>
      <div class="pad" style="padding-top:2px">
        ${
          r.results.length
            ? (besok.length
                ? `<div class="eyebrow" style="margin:6px 0 4px">Andra besök</div>` +
                  besok.map((h) => nearbyRow(h, false)).join("")
                : "") +
              (jobb.length
                ? `<div class="eyebrow" style="margin:14px 0 4px">Anläggningar som behöver något</div>` +
                  jobb.map((h) => nearbyRow(h, false)).join("")
                : "") +
              `<div class="row" style="margin-top:12px">
                 <button class="btn ghost sm" onclick="planeraFranBesok('${v.id}')">Planera runda härifrån</button>
                 ${[30, 60, 100]
                   .map(
                     (km) =>
                       `<button class="btn ghost sm" style="${
                         km === (S.filter.tripRadius || 30)
                           ? "border-color:var(--water);color:var(--water-dark)"
                           : ""
                       }" onclick="S.filter.tripRadius=${km};loadVisitNearby(S.data.visit)">${km} km</button>`
                   )
                   .join("")}
               </div>`
            : `<div class="empty"><div class="big">Inget annat i närheten</div>
               <p>Varken inbokade besök eller anläggningar som behöver något inom ${r.radius_km} km.</p>
               <div class="row" style="justify-content:center;margin-top:10px">
                 ${[30, 60, 100]
                   .map(
                     (km) =>
                       `<button class="btn ghost sm" onclick="S.filter.tripRadius=${km};loadVisitNearby(S.data.visit)">${km} km</button>`
                   )
                   .join("")}</div></div>`
        }
      </div></div>`;
  } catch (e) {
    box.innerHTML = `<div class="card"><div class="pad"><p class="hint" style="margin:0">${esc(e.message)}</p></div></div>`;
  }
}

function planeraFranBesok(visitId) {
  const v = S.data.visit;
  if (!v || v.latitude == null) return toast("Besöket saknar koordinat", true);
  S.origin = { latitude: v.latitude, longitude: v.longitude };
  S.filter.radius = S.filter.tripRadius || 30;
  go("nara");
}

async function _saveVisit() {
  const v = S.data.visit;
  const body = {
    contact_name: val("v_name2").trim(),
    phone: val("v_phone2"),
    email: val("v_mail"),
    property_designation: val("v_prop2").trim(),
    address: val("v_addr2"),
    municipality: val("v_mun2"),
    planned_at: val("v_date2"),
    errand: val("v_errand2"),
    notes: val("v_notes"),
    status: val("v_status"),
    quote_amount: numVal("v_quote"),
  };
  // Skicka bara med koordinaten om den ändrats för hand. Annars låter vi servern
  // slå upp den nya adressen, i stället för att den gamla koordinaten skriver över.
  if (val("v_coord") !== (v.coordinates || "")) body.coordinates = val("v_coord");

  try {
    const uppdaterad = await api(`/visits/${v.id}`, { method: "PATCH", body });
    toast(
      uppdaterad.geocode
        ? `Sparat. Koordinat hämtad: ${uppdaterad.geocode}`
        : "Besöket sparat"
    );
    viewVisit();
  } catch (e) {
    toast(e.message, true);
  }
}

async function visitPosition() {
  const hint = $("#v_coordhint");
  hint.textContent = "Hämtar position…";
  try {
    const pos = await GEO.get();
    $("#v_coord").value = `${pos.lat.toFixed(6)}, ${pos.lon.toFixed(6)}`;
    hint.textContent = `Hämtad, ±${Math.round(pos.acc)} m. Spara för att uppdatera underlaget.`;
  } catch (e) {
    hint.innerHTML = `<span style="color:var(--alert)">${esc(e.message)}</span>`;
  }
}

async function visitGeocode() {
  const hint = $("#v_coordhint");
  // Läs det som står i fälten just nu, så att uppslaget funkar innan man sparat
  const adress = [val("v_addr2"), val("v_prop2")].filter(Boolean).join(", ");
  const kommun = val("v_mun2") || S.data.visit.municipality || "";
  if (!adress) return toast("Fyll i adress eller fastighetsbeteckning först", true);
  hint.textContent = "Slår upp…";
  try {
    const r = await api(
      `/geocode?q=${encodeURIComponent(adress)}&municipality=${encodeURIComponent(kommun)}`
    );
    $("#v_coord").value = `${r.latitude}, ${r.longitude}`;
    hint.innerHTML = r.approximate
      ? `<span style="color:var(--brass)">Hittade bara ${esc(r.short_label)}, alltså trakten och
         inte adressen. Justera på plats med Hämta min position.</span>`
      : `Hittade ${esc(r.short_label)}. Spara för att uppdatera underlaget.`;
  } catch (e) {
    hint.innerHTML = `<span style="color:var(--alert)">${esc(e.message)}</span>`;
  }
}

async function removeVisit() {
  const v = S.data.visit;
  if (!confirm(`Ta bort ${v.visit_no}? Det går inte att ångra.`)) return;
  await api(`/visits/${v.id}`, { method: "DELETE" });
  toast("Besöket borttaget");
  go("besok");
}

async function _convertVisit() {
  const v = S.data.visit;
  const namn = prompt(
    "Vad ska kunden heta i registret?",
    v.contact_name || v.property_designation || ""
  );
  if (namn === null) return;
  try {
    const r = await api(`/visits/${v.id}/convert`, {
      method: "POST",
      body: { name: namn.trim(), create_facility: true },
    });
    toast(`${r.customer.customer_no} skapad från ${v.visit_no}`);
    S.data.customers = null;
    go("kund", r.customer.id);
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- underlag från SGU ---------------- */
function spann(s, enhet) {
  if (!s) return "—";
  return s.min === s.max
    ? `${s.median} ${enhet}`
    : `${s.min}–${s.max} ${enhet} <span class="muted">(median ${s.median})</span>`;
}

async function loadBriefing(params) {
  const box = $("#briefing");
  if (!box) return;
  const qs = new URLSearchParams({ ...params, radius_m: S.filter.sguRadius || 1000 });
  let b;
  try {
    b = await api(`/sgu/briefing?${qs}`);
  } catch (e) {
    box.innerHTML = `<p class="hint" style="margin:0">${esc(e.message)}</p>
      ${
        e.message.includes("koordinat")
          ? ""
          : `<p class="hint">Har ni inte hämtat SGU-data än gör en administratör det under
             Inställningar → SGU.</p>`
      }`;
    return;
  }

  if (!b.antal) {
    box.innerHTML = `<p class="lead" style="margin-top:0">Inga registrerade brunnar inom
      ${b.radius_m} m. Antingen är trakten oborrad, eller så saknas den i Brunnsarkivet.</p>
      ${radieVal()}`;
    return;
  }

  const svag = b.svag_kapacitet_andel;
  box.innerHTML = `
  <p class="lead" style="margin-top:0">
    <strong>${b.antal} grannbrunnar</strong> inom ${b.radius_m} m,
    varav ${b.antal_vattenbrunnar} vattenbrunnar och ${b.antal_energibrunnar} energibrunnar.</p>

  <div class="facts" style="border-top:none;padding-top:4px;grid-template-columns:1fr 1fr">
    <div class="fact"><div class="k">Berg på</div><div class="v">${spann(b.jorddjup, "m")}</div></div>
    <div class="fact"><div class="k">Borrdjup, vatten</div><div class="v">${spann(b.borrdjup_vatten, "m")}</div></div>
    <div class="fact"><div class="k">Kapacitet</div><div class="v">${spann(b.kapacitet, "l/h")}</div></div>
    <div class="fact"><div class="k">Grundvattennivå</div><div class="v">${spann(b.grundvattenniva, "m")}</div></div>
    ${
      b.borrdjup_energi
        ? `<div class="fact"><div class="k">Borrdjup, energi</div><div class="v">${spann(b.borrdjup_energi, "m")}</div></div>`
        : ""
    }
    ${
      svag !== null
        ? `<div class="fact"><div class="k">Under 600 l/h</div>
           <div class="v">${svag} % av grannarna ${svag >= 30 ? "⚠" : ""}</div></div>`
        : ""
    }
  </div>

  ${
    b.jorddjup && b.borrdjup_vatten
      ? `<p class="lead" style="margin-top:14px;padding:12px;background:#F4F9FA;border-radius:3px">
         Att räkna med: <strong>foderrör kring ${Math.ceil(b.jorddjup.median) + 2} m</strong>
         (grannarnas jorddjup ${b.jorddjup.min}–${b.jorddjup.max} m plus marginal ner i berg),
         och <strong>borrdjup omkring ${Math.round(b.borrdjup_vatten.median)} m</strong>.
         ${svag >= 30 ? "Var beredd på svag kapacitet, flera grannar ligger under 600 l/h." : ""}</p>`
      : ""
  }

  <details style="margin-top:12px">
    <summary style="cursor:pointer;font-family:var(--cond);text-transform:uppercase;
      letter-spacing:.05em;font-weight:600">Närmaste brunnarna</summary>
    <table style="margin-top:10px"><thead><tr><th>Avstånd</th><th>Borrad</th><th>Djup</th>
      <th>Berg</th><th>Kapacitet</th><th>Typ</th></tr></thead>
      <tbody>${b.narmaste
        .map(
          (w) => `<tr><td data-l="Avstånd" class="tid">${w.avstand_m} m</td>
        <td data-l="Borrad" class="tid">${esc(w.borrdatum || "—")}</td>
        <td data-l="Djup" class="tid">${w.totaldjup ?? "—"} m</td>
        <td data-l="Berg" class="tid">${w.djup_till_berg ?? "—"} m</td>
        <td data-l="Kapacitet" class="tid">${w.vattenmangd ?? "—"} l/h</td>
        <td data-l="Typ">${esc(w.anvandning_text)}</td></tr>`
        )
        .join("")}</tbody></table>
  </details>

  ${radieVal()}
  <p class="hint" style="margin-top:12px">${esc(b.vattenkvalitet)}</p>
  <p class="hint">Källa: ${esc(b.kalla)}. Lägesnoggrannheten varierar, många brunnar är satta på
    fastighetens mittpunkt snarare än på hålet.</p>`;
}

function radieVal() {
  const r = S.filter.sguRadius || 1000;
  return `<div class="row" style="margin-top:12px">
    <span class="hint" style="margin:0">Radie:</span>
    ${[500, 1000, 2000, 5000]
      .map(
        (x) =>
          `<button class="btn ghost sm" style="${x === r ? "border-color:var(--water);color:var(--water-dark)" : ""}"
        onclick="setRadie(${x})">${x >= 1000 ? x / 1000 + " km" : x + " m"}</button>`
      )
      .join("")}
  </div>`;
}

function setRadie(m) {
  S.filter.sguRadius = m;
  if (S.route === "besok" && S.id) loadBriefing({ visit_id: S.id });
  else if (S.data.customer) {
    const f = S.data.customer.facilities.find((x) => x.latitude != null);
    if (f) loadBriefing({ facility_id: f.id });
  }
}

/* ---------------- dela med extern borrare ---------------- */
async function shareDialog(target) {
  const box = $("#sharebox");
  if (!box) return;
  const qs = new URLSearchParams(target).toString();
  const { fields, kind } = await api(`/share/fields?${qs}`);
  box.innerHTML = `
  <div class="card" style="margin-top:18px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Dela med extern borrare</h2></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">Skickas som e-post från din egen server. Inget öppnas
        utåt, och bara det du kryssar i följer med.
        ${
          kind === "visit"
            ? "Uppgifterna hämtas från besöket: fastighet, adress, koordinat, kontakt och anteckningar. Borrdata finns inte än, hålet är ju inte borrat."
            : "Utskicket loggas i kundens journal."
        }</p>
      <div class="fgrid">
        ${fld("sh_to", "Mottagare", "", "email", 'placeholder="borrare@firma.se"')}
        ${fld("sh_sub", "Ämne", "", "text", 'placeholder="Lämna tomt för automatiskt ämne"')}
      </div>
      <label class="f">Vad ska följa med</label>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:6px">
        ${fields
          .map(
            (f) => `<label style="display:flex;gap:8px;align-items:flex-start;font-size:14px">
          <input type="checkbox" class="sharefield" value="${f.key}" style="width:auto;margin-top:3px"
            ${f.default ? "checked" : ""}>
          <span>${esc(f.label)}</span></label>`
          )
          .join("")}
      </div>
      <label class="f" for="sh_msg">Meddelande</label>
      <textarea id="sh_msg" placeholder="Hej, kan du ta det här jobbet vecka 34?"></textarea>
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="sendShare(${JSON.stringify(JSON.stringify(target)).replace(/"/g, "&quot;")})">Skicka</button>
        <button class="btn ghost sm" onclick="document.getElementById('sharebox').innerHTML=''">Avbryt</button>
      </div>
    </div></div>`;
  scrollTill(box);
}

async function sendShare(targetJson) {
  const target = typeof targetJson === "string" ? JSON.parse(targetJson) : targetJson;
  const fields = [...document.querySelectorAll(".sharefield:checked")].map((c) => c.value);
  try {
    const r = await api("/share", {
      method: "POST",
      body: {
        ...target,
        recipient: val("sh_to"),
        subject: val("sh_sub"),
        message: val("sh_msg"),
        fields,
      },
    });
    toast(`Skickat till ${r.recipient}`);
    $("#sharebox").innerHTML = "";
  } catch (e) {
    toast(e.message, true);
  }
}



function tabQuotes(c, offerter) {
  const laser = S.user.role === "lasare";
  return `
  ${
    laser
      ? ""
      : `<div class="row" style="margin-bottom:14px">
    <button class="btn pri sm" onclick="valjMall('${c.id}')">+ Ny offert</button>
    <span class="hint" style="margin:0">Skapar ett utkast du fyller med rader.</span></div>`
  }
  ${
    offerter.length
      ? offerter
          .map((q) => {
            const [text, klass] = OFFERT_STATUS[q.status] || [q.status, "n"];
            return `<div class="filerow" style="cursor:pointer" onclick="go('offert','${q.id}')">
        <div class="ftype pdf">OFF</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600">${esc(q.title || q.quote_no)}</div>
          <div class="fmeta">${esc(q.quote_no)} · ${dt(q.created_at, false)}${
            q.valid_until ? ` · giltig till ${q.valid_until}` : ""
          }${q.sent_at ? ` · skickad ${dt(q.sent_at, false)}` : ""}</div></div>
        <span class="mono" style="font-weight:600">${kr(q.totals.brutto)} kr</span>
        <span class="tag ${klass}">${text}</span></div>`;
          })
          .join("")
      : `<div class="empty"><div class="big">Inga offerter</div>
         <p>En offert kan mejlas som PDF och sparas automatiskt bland kundens dokument.</p></div>`
  }`;
}

function tabOrders(c, order) {
  const laser = S.user.role === "lasare";
  return `
  ${
    laser
      ? ""
      : `<div class="row" style="margin-bottom:14px">
    <button class="btn pri sm" onclick="nyOrder('${c.id}')">+ Ny arbetsorder</button>
    <span class="hint" style="margin:0">För material och arbete som ska faktureras.</span></div>`
  }
  ${
    order.length
      ? order
          .map((o) => {
            const [text, klass] = ORDER_STATUS[o.status] || [o.status, "n"];
            return `<div class="filerow" style="cursor:pointer" onclick="go('order','${o.id}')">
        <div class="ftype" style="background:${
          o.status === "betald" ? "#2E7D5B" : o.status === "oppen" ? "var(--stone)" : "#B3801F"
        }">AO</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600">${esc(o.title || o.order_no)}</div>
          <div class="fmeta">${esc(o.order_no)}${o.performed_at ? ` · utförd ${o.performed_at}` : ""}${
            o.invoice_no ? ` · faktura ${esc(o.invoice_no)}` : ""
          } · material ${krRund(o.material_total)} kr, arbete ${krRund(o.labour_total)} kr</div></div>
        <span class="mono" style="font-weight:600">${kr(o.totals.brutto)} kr</span>
        <span class="tag ${klass}">${text}</span></div>`;
          })
          .join("")
      : `<div class="empty"><div class="big">Inga arbetsorder</div>
         <p>Journalen berättar vad som hände. Arbetsordern håller reda på vad som gick åt,
         så att inget glöms bort vid faktureringen.</p></div>`
  }`;
}

async function nyOffert(customerId, visitId) {
  const titel = prompt("Vad gäller offerten?", "");
  if (titel === null) return;
  try {
    const q = await api("/quotes", {
      method: "POST",
      body: customerId ? { customer_id: customerId, title: titel } : { visit_id: visitId, title: titel },
    });
    toast(`${q.quote_no} skapad`);
    go("offert", q.id);
  } catch (e) {
    toast(e.message, true);
  }
}

async function _nyOrder(customerId) {
  const titel = prompt("Vad gäller arbetsordern?", "");
  if (titel === null) return;
  const c = S.data.customer;
  const anl = c && c.facilities.length ? c.facilities[0].id : null;
  try {
    const o = await api("/work-orders", {
      method: "POST",
      body: { customer_id: customerId, title: titel, facility_id: anl },
    });
    toast(`${o.order_no} skapad`);
    go("order", o.id);
  } catch (e) {
    toast(e.message, true);
  }
}


async function laddaUppLogo() {
  const fil = ($("#logofil").files || [])[0];
  if (!fil) return toast("Välj en bildfil först", true);
  const fd = new FormData();
  fd.append("file", fil);
  try {
    const r = await api("/company/logo", { method: "POST", body: fd });
    toast(`Logotypen uppladdad, ${r.bredd}×${r.hojd} px`);
    S.company = null;
    await laddaForetag();
    adminCompany();
  } catch (e) {
    toast(e.message, true);
  }
}

async function taBortLogo() {
  if (!confirm("Ta bort logotypen?")) return;
  await api("/company/logo", { method: "DELETE" });
  S.company = null;
  await laddaForetag();
  toast("Logotypen borttagen");
  adminCompany();
}

/* ---------------- offertmallar ---------------- */
async function valjMall(customerId, visitId) {
  const mallar = await api("/quote-templates");
  const box = document.getElementById(customerId ? "tabbody" : "view");
  const html = `
  <div class="card" id="mallval" style="margin-bottom:16px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Vad gäller offerten?</h2></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">Välj en mall så fylls rubrik, texter och rader i åt dig.
        Allt går att ändra efteråt. Priser hämtas från artikelregistret där artikeln finns.</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px">
        ${mallar
          .map(
            (m) => `<button class="mallkort" onclick="skapaUrMall('${m.id}','${customerId || ""}','${visitId || ""}')">
          <strong>${esc(m.name)}</strong>
          <span>${esc(m.description || "")}</span>
          <span class="m">${m.line_count} rader · ca ${krRund(m.estimate)} kr</span></button>`
          )
          .join("")}
        <button class="mallkort" onclick="skapaUrMall('','${customerId || ""}','${visitId || ""}')">
          <strong>Tom offert</strong><span>Börja från noll och skriv allt själv</span></button>
      </div>
      <button class="btn ghost sm" style="margin-top:12px" onclick="document.getElementById('mallval').remove()">Avbryt</button>
    </div></div>`;
  const gammal = document.getElementById("mallval");
  if (gammal) gammal.remove();
  stangAndraFormular(null);
  const holder = document.createElement("div");
  holder.innerHTML = html;
  box.prepend(holder.firstElementChild);
  scrollTill(document.getElementById("mallval"));
}

async function skapaUrMall(mallId, customerId, visitId) {
  const titel = mallId ? null : prompt("Vad gäller offerten?", "");
  if (titel === null && !mallId) return;
  try {
    const body = customerId ? { customer_id: customerId } : { visit_id: visitId };
    if (mallId) body.template_id = mallId;
    else body.title = titel;
    const q = await api("/quotes", { method: "POST", body });
    toast(`${q.quote_no} skapad${q.lines.length ? ` med ${q.lines.length} rader` : ""}`);
    go("offert", q.id);
  } catch (e) {
    toast(e.message, true);
  }
}

async function sparaSomMall() {
  const q = S.data.quote;
  const namn = prompt("Vad ska mallen heta?", q.title || "");
  if (namn === null || !namn.trim()) return;
  try {
    const m = await api("/quote-templates", {
      method: "POST",
      body: { name: namn.trim(), from_quote_id: q.id, description: "Sparad från " + q.quote_no },
    });
    toast(`Mallen "${m.name}" sparad med ${m.line_count} rader`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function viewTemplates() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const mallar = await api("/quote-templates");
  if (!current(token)) return;
  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Offerter</div><h1>Mallar</h1>
      <p class="lead">Färdiga underlag för de jobb ni gör ofta. Tre finns från början och går att
        skriva om. Egna mallar skapar du enklast genom att göra en offert som du är nöjd med och
        välja Spara som mall.</p></div>
  </div>
  <div class="card"><div class="pad">
    ${mallar
      .map(
        (m) => `<div class="filerow">
      <div class="ftype ${m.is_builtin ? "other" : "doc"}">${m.is_builtin ? "STD" : "EGEN"}</div>
      <div style="flex:1;min-width:0">
        <div style="font-weight:600">${esc(m.name)}</div>
        <div class="fmeta">${esc(m.description || "")} · ${m.line_count} rader · ca ${krRund(m.estimate)} kr</div>
      </div>
      ${
        S.user.role === "lasare"
          ? ""
          : `<button class="btn ghost sm" onclick="visaMall('${m.id}')">Visa</button>
             <button class="btn danger sm" onclick="taBortMall('${m.id}','${esc(m.name)}')">Ta bort</button>`
      }</div>`
      )
      .join("")}
  </div></div>
  <div id="malldetalj"></div>`;
}

async function visaMall(id) {
  const m = (await api("/quote-templates")).find((x) => x.id === id);
  const box = $("#malldetalj");
  if (!m) return;
  box.innerHTML = `<div class="card" style="margin-top:16px">
    <div class="hd"><h2>${esc(m.name)}</h2></div><div class="pad">
      ${fld("m_namn", "Namn", m.name)}
      ${fld("m_titel", "Rubrik på offerten", m.title)}
      <label class="f" for="m_intro">Inledande text</label>
      <textarea id="m_intro">${esc(m.intro)}</textarea>
      <label class="f" for="m_villkor">Villkor</label>
      <textarea id="m_villkor">${esc(m.terms)}</textarea>
      <div class="eyebrow" style="margin-top:14px">Rader i mallen</div>
      <table><thead><tr><th>Benämning</th><th>Typ</th><th>Antal</th><th>Enhet</th><th>À-pris</th></tr></thead>
      <tbody>${m.lines
        .map(
          (r) => `<tr><td data-l="Benämning">${esc(r.name)}</td>
        <td data-l="Typ">${esc(RAD_TYP[r.kind] || r.kind)}</td>
        <td data-l="Antal" class="tid">${r.quantity}</td>
        <td data-l="Enhet" class="tid">${esc(r.unit || "st")}</td>
        <td data-l="À-pris" class="tid">${krRund(r.unit_price)}</td></tr>`
        )
        .join("")}</tbody></table>
      <p class="hint">Priserna här är utgångsvärden. Finns artikeln i registret används dagens pris
        därifrån när offerten skapas.</p>
      <div class="row" style="margin-top:12px">
        <button class="btn pri sm" onclick="sparaMall('${m.id}')">Spara ändringar</button>
        <button class="btn ghost sm" onclick="kopieraMall('${m.id}')">Spara som ny mall</button>
        <button class="btn ghost sm" onclick="document.getElementById('malldetalj').innerHTML=''">Stäng</button>
      </div>
    </div></div>`;
  scrollTill(box);
}

async function sparaMall(id) {
  await api(`/quote-templates/${id}`, {
    method: "PATCH",
    body: {
      name: val("m_namn"),
      title: val("m_titel"),
      intro: val("m_intro"),
      terms: val("m_villkor"),
    },
  });
  toast("Mallen sparad");
  viewTemplates();
}

async function kopieraMall(id) {
  const m = (await api("/quote-templates")).find((x) => x.id === id);
  const namn = prompt("Vad ska den nya mallen heta?", `${m.name} (kopia)`);
  if (namn === null || !namn.trim()) return;
  await api("/quote-templates", {
    method: "POST",
    body: {
      name: namn.trim(),
      description: m.description,
      title: val("m_titel") || m.title,
      intro: val("m_intro") || m.intro,
      terms: val("m_villkor") || m.terms,
      valid_days: m.valid_days,
      lines: m.lines,
    },
  });
  toast("Ny mall sparad");
  viewTemplates();
}

async function taBortMall(id, namn) {
  if (!confirm(`Ta bort mallen "${namn}"?`)) return;
  await api(`/quote-templates/${id}`, { method: "DELETE" });
  toast("Mallen borttagen");
  viewTemplates();
}


/* Översikt: svarar på "vad är läget med den här kunden" utan att man klickar runt.
   Byggd för att vara det första man ser, både på kontoret och i bilen. */
function tabOversikt(c) {
  const laser = S.user.role === "lasare";
  const journal = S.data.journal || [];
  const offerter = S.data.quotes || [];
  const order = S.data.orders || [];
  const paminnelser = S.data.reminders || [];
  const filer = S.data.files || [];

  const attGora = [
    ...paminnelser.map((r) => ({
      text: r.title,
      under: `${r.due_date}${r.overdue ? " · försenad" : ""}`,
      prio: r.overdue ? 3 : 2,
      klick: `go('kund','${c.id}/oversikt')`,
    })),
    ...offerter
      .filter((q) => q.status === "skickad")
      .map((q) => ({
        text: `${q.quote_no} väntar på besked`,
        under: `${kr(q.totals.brutto)} kr${q.valid_until ? ` · gäller till ${q.valid_until}` : ""}`,
        prio: 2,
        klick: `go('offert','${q.id}')`,
      })),
    ...order
      .filter((o) => o.status === "utford")
      .map((o) => ({
        text: `${o.order_no} är utförd men inte fakturerad`,
        under: `${kr(o.totals.brutto)} kr`,
        prio: 3,
        klick: `go('order','${o.id}')`,
      })),
    ...order
      .filter((o) => o.status === "fakturerad")
      .map((o) => ({
        text: `${o.order_no} väntar på betalning`,
        under: `${kr(o.totals.brutto)} kr${o.invoice_no ? ` · faktura ${esc(o.invoice_no)}` : ""}`,
        prio: 2,
        klick: `go('order','${o.id}')`,
      })),
  ].sort((a, b) => b.prio - a.prio);

  const anl = c.facilities[0];
  const bilder = filer.filter((f) => f.kind === "bild").slice(0, 4);

  return `
  ${
    laser
      ? ""
      : `<div class="row" style="margin-bottom:16px">
    <button class="btn pri" onclick="go('kund','${c.id}/journal')">+ Journalanteckning</button>
    <button class="btn" onclick="valjMall('${c.id}')">+ Offert</button>
    <button class="btn" onclick="nyOrder('${c.id}')">+ Arbetsorder</button>
    <button class="btn ghost" onclick="go('kund','${c.id}/filer')">+ Foto eller dokument</button>
  </div>`
  }

  ${
    attGora.length
      ? `<div class="card" style="margin-bottom:16px">
      <div class="hd"><h2>Att göra</h2><span class="tag ${attGora.some((x) => x.prio === 3) ? "action" : "soon"}">${attGora.length}</span></div>
      <div class="pad" style="padding-top:2px">
        ${attGora
          .map(
            (x) => `<div class="filerow" style="cursor:pointer" onclick="${x.klick}">
          <div class="ftype" style="background:${x.prio === 3 ? "#A6402F" : "#B3801F"}">!</div>
          <div style="flex:1;min-width:0"><div style="font-weight:600">${esc(x.text)}</div>
            <div class="fmeta">${x.under}</div></div></div>`
          )
          .join("")}
      </div></div>`
      : `<div class="card" style="margin-bottom:16px"><div class="pad">
      <p class="lead" style="margin:0">Inget öppet just nu. Inga obetalda fakturor, inga offerter
      utan besked och inga påminnelser som förfallit.</p></div></div>`
  }

  <div class="card" style="margin-bottom:16px">
    <div class="hd"><h2>Senast i journalen</h2>
      <button class="btn ghost sm" style="margin-left:auto" onclick="go('kund','${c.id}/journal')">Hela journalen</button></div>
    <div class="pad" style="padding-top:2px">
      ${
        journal.length
          ? journal.slice(0, 3).map((j) => journalEntryHtml(j)).join("")
          : `<p class="hint" style="margin:0">Inga anteckningar än.</p>`
      }
    </div></div>

  ${
    bilder.length
      ? `<div class="card"><div class="hd"><h2>Senaste bilderna</h2>
      <button class="btn ghost sm" style="margin-left:auto" onclick="go('kund','${c.id}/filer')">Alla filer</button></div>
      <div class="pad"><div class="imgs">${bilder.map((f) => docCard(f, true)).join("")}</div></div></div>`
      : ""
  }`;
}

/* Dokument och bilder var samma sak delat på filtyp. Nu ett ställe med filter. */
function tabFiler(c, filer) {
  const laser = S.user.role === "lasare";
  const filter = S.filter.filtyp || "alla";
  const visade =
    filter === "alla" ? filer : filer.filter((f) => f.kind === (filter === "bilder" ? "bild" : "dokument"));
  const antalBilder = filer.filter((f) => f.kind === "bild").length;
  const antalDok = filer.length - antalBilder;

  return `
  <div class="row" style="margin-bottom:14px">
    ${[["alla", `Alla ${filer.length}`], ["bilder", `Bilder ${antalBilder}`], ["dokument", `Dokument ${antalDok}`]]
      .map(
        ([v, l]) =>
          `<button class="btn ${filter === v ? "" : "ghost"} sm" onclick="S.filter.filtyp='${v}';renderTab()">${l}</button>`
      )
      .join("")}
  </div>
  ${
    laser
      ? ""
      : `<div class="drop" id="drop">
    <div class="big">Släpp filer här</div>
    <p style="margin:6px 0 0">Foton, borrprotokoll, intyg och offerter. Kopplas till ${esc(c.name)}.</p>
    <div class="row" style="justify-content:center;margin-top:10px">
      <button class="btn ghost sm" onclick="document.getElementById('fin').click()">Välj filer</button>
      <button class="btn ghost sm" onclick="document.getElementById('kamera').click()">Ta foto</button>
    </div>
    <input type="file" id="fin" multiple hidden accept=".pdf,.docx,.doc,.xlsx,.txt,image/*">
    <input type="file" id="kamera" hidden accept="image/*" capture="environment">
    <div class="progress" id="prog" hidden><i style="width:0"></i></div>
  </div>`
  }
  <div class="imgs" style="margin-top:16px">${visade.map((f) => docCard(f, laser)).join("")}</div>
  ${
    visade.length
      ? ""
      : `<div class="empty"><div class="big">Inga filer här</div>
         <p>Ladda upp foton från borrplatsen, borrprotokoll och intyg.</p></div>`
  }`;
}

/* Offert och order är ett flöde, inte två register. */
function tabEconomy(c, offerter, order) {
  const laser = S.user.role === "lasare";
  const summaObetalt = order
    .filter((o) => ["utford", "fakturerad"].includes(o.status))
    .reduce((s, o) => s + o.totals.brutto, 0);

  return `
  ${
    laser
      ? ""
      : `<div class="row" style="margin-bottom:14px">
    <button class="btn pri sm" onclick="valjMall('${c.id}')">+ Ny offert</button>
    <button class="btn sm" onclick="nyOrder('${c.id}')">+ Ny arbetsorder</button>
    ${
      summaObetalt
        ? `<span class="tag soon" style="margin-left:auto">${kr(summaObetalt)} kr väntar på betalning</span>`
        : ""
    }
  </div>`
  }

  <div class="eyebrow">Arbetsorder</div>
  ${
    order.length
      ? order
          .map((o) => {
            const [text, klass] = ORDER_STATUS[o.status] || [o.status, "n"];
            return `<div class="filerow" style="cursor:pointer" onclick="go('order','${o.id}')">
        <div class="ftype" style="background:${
          o.status === "betald" ? "#2E7D5B" : o.status === "oppen" ? "var(--stone)" : "#B3801F"
        }">AO</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600">${esc(o.title || o.order_no)}</div>
          <div class="fmeta">${esc(o.order_no)}${o.performed_at ? ` · utförd ${o.performed_at}` : ""}${
            o.invoice_no ? ` · faktura ${esc(o.invoice_no)}` : ""
          } · material ${krRund(o.material_total)}, arbete ${krRund(o.labour_total)} kr</div></div>
        <span class="mono" style="font-weight:600">${kr(o.totals.brutto)} kr</span>
        <span class="tag ${klass}">${text}</span></div>`;
          })
          .join("")
      : `<p class="hint">Inga arbetsorder. Journalen berättar vad som hände, arbetsordern håller
         reda på vad som gick åt så att inget glöms bort vid faktureringen.</p>`
  }

  <div class="eyebrow" style="margin-top:20px">Offerter</div>
  ${
    offerter.length
      ? offerter
          .map((q) => {
            const [text, klass] = OFFERT_STATUS[q.status] || [q.status, "n"];
            return `<div class="filerow" style="cursor:pointer" onclick="go('offert','${q.id}')">
        <div class="ftype pdf">OFF</div>
        <div style="flex:1;min-width:0"><div style="font-weight:600">${esc(q.title || q.quote_no)}</div>
          <div class="fmeta">${esc(q.quote_no)} · ${dt(q.created_at, false)}${
            q.sent_at ? ` · skickad ${dt(q.sent_at, false)}` : ""
          }</div></div>
        <span class="mono" style="font-weight:600">${kr(q.totals.brutto)} kr</span>
        <span class="tag ${klass}">${text}</span></div>`;
          })
          .join("")
      : `<p class="hint">Inga offerter än.</p>`
  }`;
}



/* Någon ringer och vill ha ett pris. Ingen kund, inget besök, bara ett namn.
   Blir det affär gör man kund av offerten med ett klick. */
async function nyForfragan() {
  const box = $("#forfraganform") || $("#visitform") || $("#view");
  if (document.getElementById("forfragankort")) {
    document.getElementById("forfragankort").remove();
    return;
  }
  stangAndraFormular("forfraganform");
  const mallar = await api("/quote-templates");
  const holder = document.createElement("div");
  holder.innerHTML = `
  <div class="card" id="forfragankort" style="margin-bottom:16px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Offert på telefonförfrågan</h2></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">För den som ringer och vill ha ett pris. Ingen kund läggs
        upp, inget besök bokas. Blir det affär gör du kund av offerten med ett klick.</p>
      <div class="fgrid">
        ${fld("ff_namn", "Vem gäller det", "", "text", 'placeholder="Namn eller företag"')}
        ${fld("ff_mail", "E-post", "", "email")}
        ${fld("ff_adr", "Adress eller fastighet", "")}
        <div><label class="f" for="ff_mall">Mall</label><select id="ff_mall">
          <option value="">Tom offert</option>
          ${mallar.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("")}
        </select></div>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="skapaForfragan()">Skapa offert</button>
        <button class="btn ghost sm" onclick="document.getElementById('forfragankort').remove()">Avbryt</button>
      </div>
    </div></div>`;
  box.prepend(holder.firstElementChild);
  scrollTill(document.getElementById("forfragankort"));
  if ($("#ff_namn")) $("#ff_namn").focus();
}

async function _skapaForfragan() {
  const namn = val("ff_namn").trim();
  if (!namn) return toast("Ange åtminstone vem offerten ska till", true);
  try {
    const body = {
      recipient_name: namn,
      recipient_email: val("ff_mail"),
      recipient_address: val("ff_adr"),
    };
    if (val("ff_mall")) body.template_id = val("ff_mall");
    const q = await api("/quotes", { method: "POST", body });
    toast(`${q.quote_no} skapad`);
    go("offert", q.id);
  } catch (e) {
    toast(e.message, true);
  }
}

async function _offertTillKund() {
  const q = S.data.quote;
  const namn = prompt("Vad ska kunden heta i registret?", q.recipient_name || "");
  if (namn === null || !namn.trim()) return;
  const telefon = prompt("Telefonnummer (kan lämnas tomt):", "") || "";
  try {
    const r = await api(`/quotes/${q.id}/to-customer`, {
      method: "POST",
      body: { name: namn.trim(), phone: telefon, create_facility: true },
    });
    toast(`${r.customer.customer_no} skapad från ${q.quote_no}`);
    S.data.customers = null;
    go("kund", r.customer.id);
  } catch (e) {
    toast(e.message, true);
  }
}


/* ---------------- systemhändelser ---------------- */
/* Bakgrundsjobb har ingen användare att svara. Utan den här listan blir ett
   misslyckat adressuppslag en rad i containerloggen som ingen läser. */
async function viewEvents() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const data = await api("/events?limit=80");
  if (!current(token)) return;

  const ikon = { fel: ["#A6402F", "!"], varning: ["#B3801F", "!"], info: ["var(--stone)", "i"] };

  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Drift</div><h1>Systemhändelser</h1>
      <p class="lead">Saker som gått fel i bakgrunden, där ingen stod och tittade: adressuppslag,
        hämtningar från SGU, utskick och oväntade serverfel.</p></div>
    ${
      data.open && S.user.role !== "lasare"
        ? `<button class="btn" onclick="kvitteraHandelser()">Kvittera alla (${data.open})</button>`
        : ""
    }
  </div>

  <div class="card"><div class="pad" style="padding-top:6px">
    ${
      data.events.length
        ? data.events
            .map((e) => {
              const [farg, tecken] = ikon[e.level] || ikon.info;
              return `<div class="filerow" style="${e.acknowledged ? "opacity:.55" : ""}">
        <div class="ftype" style="background:${farg}">${tecken}</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600">${esc(e.message)}</div>
          <div class="fmeta">${dt(e.at)} · ${esc(e.source)}${
            e.reference ? ` · referens ${esc(e.reference)}` : ""
          }${e.acknowledged ? " · kvitterad" : ""}</div>
          ${
            e.detail
              ? `<details style="margin-top:4px"><summary class="tsub" style="cursor:pointer">Detaljer</summary>
                 <pre style="white-space:pre-wrap;font-size:11.5px;font-family:var(--mono);
                 background:#F4F7F7;padding:8px;border-radius:3px;margin:6px 0 0;overflow-x:auto">${esc(e.detail.slice(0, 2500))}</pre></details>`
              : ""
          }
        </div>
        ${
          e.object_type === "visit"
            ? `<button class="btn ghost sm" onclick="go('besok','${e.object_id}')">Öppna</button>`
            : e.object_type === "facility"
              ? `<button class="btn ghost sm" onclick="go('anlaggningar')">Anläggningar</button>`
              : ""
        }</div>`;
            })
            .join("")
        : `<div class="empty"><div class="big">Inget har gått fel</div>
           <p>Här hamnar fel från bakgrundsjobben. Tomt är bra.</p></div>`
    }
  </div></div>`;
}

async function kvitteraHandelser() {
  await api("/events/acknowledge", { method: "POST", body: {} });
  toast("Kvitterade");
  viewEvents();
}

/* ---------------- förhandsgranskning ---------------- */
/* PDF:en renderas av servern, samma kod som skickas till kund. Alternativet vore
   att härma layouten i HTML, men då visar förhandsgranskningen något annat än det
   kunden får, vilket är värre än ingen förhandsgranskning alls. */
let forhandTimer = null;
let forhandUrl = null;

function forhandsPanel(path) {
  return `<div class="card" id="forhandskort" style="margin-top:16px">
    <div class="hd"><h2>Förhandsgranskning</h2>
      <span class="hint" id="forhandstatus" style="margin:0"></span>
      <button class="btn ghost sm" style="margin-left:auto" onclick="doljForhand()">Dölj</button>
    </div>
    <div class="pad" style="padding:0">
      <iframe id="forhandsram" title="Förhandsgranskning av dokumentet"
        style="width:100%;height:70vh;min-height:420px;border:0;display:block;background:#EFF2F1"></iframe>
    </div>
    <div class="pad" style="border-top:1px solid var(--line)">
      <p class="hint" style="margin:0">Uppdateras automatiskt när du ändrar rader eller texter.
        Det här är exakt det kunden får.</p>
    </div></div>`;
}

async function uppdateraForhand(path, direkt = false) {
  const ram = document.getElementById("forhandsram");
  if (!ram) return;
  clearTimeout(forhandTimer);
  const status = document.getElementById("forhandstatus");

  const kor = async () => {
    if (status) status.textContent = "Uppdaterar…";
    try {
      const res = await fetch(path, { headers: { Authorization: `Bearer ${S.token}` } });
      if (!res.ok) throw new Error("kunde inte hämta");
      const blob = await res.blob();
      if (forhandUrl) URL.revokeObjectURL(forhandUrl);
      forhandUrl = URL.createObjectURL(blob);
      const ram2 = document.getElementById("forhandsram");
      if (ram2) ram2.src = forhandUrl + "#toolbar=0&navpanes=0";
      if (status) status.textContent = `Uppdaterad ${new Date().toLocaleTimeString("sv-SE").slice(0, 5)}`;
    } catch (_) {
      if (status) status.textContent = "Kunde inte visa";
    }
  };
  if (direkt) return kor();
  // Kort fördröjning, annars byggs en PDF per tangenttryckning
  forhandTimer = setTimeout(kor, 700);
}

function visaForhand(path) {
  S.forhand = path;
  try {
    localStorage.setItem("bj_forhand", "1");
  } catch (_) {}
  const knapp = document.getElementById("forhandsknapp");
  if (knapp) knapp.remove();
  const holder = document.createElement("div");
  holder.innerHTML = forhandsPanel(path);
  $("#view").appendChild(holder.firstElementChild);
  uppdateraForhand(path, true);
}

function doljForhand() {
  S.forhand = null;
  try {
    localStorage.removeItem("bj_forhand");
  } catch (_) {}
  const kort = document.getElementById("forhandskort");
  if (kort) kort.remove();
  if (forhandUrl) {
    URL.revokeObjectURL(forhandUrl);
    forhandUrl = null;
  }
}

/* Kopplar in panelen på offert- och ordervyn */
function kopplaForhand(path) {
  let pa = false;
  try {
    pa = localStorage.getItem("bj_forhand") === "1";
  } catch (_) {}
  if (pa) visaForhand(path);
  else {
    const holder = document.createElement("div");
    holder.innerHTML = `<div class="row" id="forhandsknapp" style="margin-top:16px">
      <button class="btn ghost" onclick="visaForhand('${path}')">Visa förhandsgranskning</button>
      <span class="hint" style="margin:0">Uppdateras medan du bygger dokumentet.</span></div>`;
    $("#view").appendChild(holder.firstElementChild);
  }
}


/* Spärrar knappen medan anropet pågår. Ett dubbelklick blev annars två
   sparningar, vilket i värsta fall gav en krock på löpnumret. */
async function medanSparas(handelse, arbete) {
  const knapp = handelse && handelse.currentTarget ? handelse.currentTarget : null;
  const text = knapp ? knapp.textContent : "";
  if (knapp) {
    if (knapp.disabled) return;
    knapp.disabled = true;
    knapp.textContent = "Sparar…";
  }
  try {
    return await arbete();
  } finally {
    if (knapp && document.body.contains(knapp)) {
      knapp.disabled = false;
      knapp.textContent = text;
    }
  }
}

/* Enkel spärr för funktioner som anropas utan händelse */
const pagaende = new Set();
async function enGang(nyckel, arbete) {
  if (pagaende.has(nyckel)) return;
  pagaende.add(nyckel);
  try {
    return await arbete();
  } finally {
    pagaende.delete(nyckel);
  }
}


/* Sparfunktionerna körs bara en gång åt gången */
const saveNewVisit = (...a) => enGang('saveNewVisit', () => _saveNewVisit(...a));
const sparaNyArtikel = (...a) => enGang('sparaNyArtikel', () => _sparaNyArtikel(...a));
const skapaForfragan = (...a) => enGang('skapaForfragan', () => _skapaForfragan(...a));
const saveVisit = (...a) => enGang('saveVisit', () => _saveVisit(...a));
const sparaOffert = (...a) => enGang('sparaOffert', () => _sparaOffert(...a));
const sparaOrder = (...a) => enGang('sparaOrder', () => _sparaOrder(...a));
const laggRad = (...a) => enGang('laggRad', () => _laggRad(...a));
const convertVisit = (...a) => enGang('convertVisit', () => _convertVisit(...a));
const offertTillKund = (...a) => enGang('offertTillKund', () => _offertTillKund(...a));
const skapaOrderFranOffert = (...a) => enGang('skapaOrderFranOffert', () => _skapaOrderFranOffert(...a));
const genomforSkick = (...a) => enGang('genomforSkick', () => _genomforSkick(...a));
const saveCustomer = (...a) => enGang('saveCustomer', () => _saveCustomer(...a));
const nyOrder = (...a) => enGang('nyOrder', () => _nyOrder(...a));

/* ---------------- pengar: gemensamt ---------------- */
const kr = (v) =>
  (v ?? 0).toLocaleString("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const krRund = (v) => Math.round(v ?? 0).toLocaleString("sv-SE");

const RAD_TYP = { material: "Material", arbete: "Arbete", ovrigt: "Övrigt" };
const OFFERT_STATUS = {
  utkast: ["Utkast", "n"],
  skickad: ["Skickad", "soon"],
  accepterad: ["Accepterad", "ok"],
  avslagen: ["Avslagen", "action"],
  utgangen: ["Utgången", "n"],
};
const ORDER_STATUS = {
  oppen: ["Öppen", "n"],
  utford: ["Utförd, ej fakturerad", "soon"],
  fakturerad: ["Fakturerad, ej betald", "soon"],
  betald: ["Betald", "ok"],
  makulerad: ["Makulerad", "n"],
};

async function oppnaPdf(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${S.token}` } });
  if (!res.ok) return toast("Kunde inte skapa PDF", true);
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function skrivUt(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${S.token}` } });
  if (!res.ok) return toast("Kunde inte skapa PDF", true);
  const url = URL.createObjectURL(await res.blob());
  const ram = document.createElement("iframe");
  ram.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
  ram.src = url;
  ram.onload = () => {
    try {
      ram.contentWindow.focus();
      ram.contentWindow.print();
    } catch (_) {
      window.open(url, "_blank");
    }
  };
  document.body.appendChild(ram);
  setTimeout(() => {
    ram.remove();
    URL.revokeObjectURL(url);
  }, 60000);
}

/* Radtabell som används av både offert och arbetsorder */
function radTabell(dok, laser, kanRedigera) {
  const grupper = ["material", "arbete", "ovrigt"];
  let html = `<table><thead><tr><th>Benämning</th><th>Antal</th><th>Enhet</th>
    <th>À-pris</th><th>Summa</th>${kanRedigera ? "<th></th>" : ""}</tr></thead><tbody>`;
  for (const g of grupper) {
    const rader = dok.lines.filter((r) => (r.kind || "material") === g);
    if (!rader.length) continue;
    html += `<tr><td colspan="${kanRedigera ? 6 : 5}" style="padding-top:12px">
      <span class="eyebrow" style="margin:0">${RAD_TYP[g]}</span></td></tr>`;
    for (const r of rader) {
      html += `<tr>
        <td data-l="Benämning"><span class="tname">${esc(r.name)}</span>
          ${r.article_no ? `<span class="tsub"> ${esc(r.article_no)}</span>` : ""}
          ${r.note ? `<div class="tsub">${esc(r.note)}</div>` : ""}
          ${r.discount_percent ? `<div class="tsub">rabatt ${r.discount_percent} %</div>` : ""}</td>
        <td data-l="Antal" class="tid">${r.quantity}</td>
        <td data-l="Enhet" class="tid">${esc(r.unit)}</td>
        <td data-l="À-pris" class="tid">${kr(r.unit_price)}</td>
        <td data-l="Summa" class="tid"><strong>${kr(r.line_total)}</strong></td>
        ${
          kanRedigera
            ? `<td><div class="row" style="gap:4px">
            <button class="btn ghost sm" onclick="redigeraRad('${r.id}',${r.quantity},${r.unit_price})">Ändra</button>
            <button class="btn danger sm" onclick="taBortRad('${r.id}')">×</button></div></td>`
            : ""
        }</tr>`;
    }
  }
  html += `</tbody></table>
  <div class="pad" style="border-top:1px solid var(--line)">
    <div style="max-width:320px;margin-left:auto">
      ${sumRad("Netto", dok.totals.netto)}
      ${sumRad("Moms", dok.totals.moms)}
      <div style="border-top:1px solid var(--ink);margin:6px 0 0;padding-top:6px">
        ${sumRad("Att betala", dok.totals.brutto, true)}
      </div>
    </div></div>`;
  return html;
}

function sumRad(etikett, varde, fet = false) {
  return `<div style="display:flex;justify-content:space-between;padding:3px 0;
    font-size:${fet ? "16px" : "14px"};font-weight:${fet ? "600" : "400"}">
    <span>${etikett}</span><span class="mono">${kr(varde)} kr</span></div>`;
}

async function redigeraRad(id, antal, pris) {
  const nyttAntal = prompt("Antal:", antal);
  if (nyttAntal === null) return;
  const nyttPris = prompt("À-pris:", pris);
  if (nyttPris === null) return;
  await api(`/lines/${id}`, {
    method: "PATCH",
    body: {
      quantity: parseFloat(String(nyttAntal).replace(",", ".")) || 0,
      unit_price: parseFloat(String(nyttPris).replace(",", ".")) || 0,
    },
  });
  laddaOmDokument();
}

async function taBortRad(id) {
  if (!confirm("Ta bort raden?")) return;
  await api(`/lines/${id}`, { method: "DELETE" });
  laddaOmDokument();
}

function laddaOmDokument() {
  if (S.route === "offert") viewQuote();
  else if (S.route === "order") viewOrder();
}

/* Anropas när något ändrats som syns i dokumentet */
function forhandUppdatera() {
  if (S.forhand) uppdateraForhand(S.forhand);
}

/* Radväljare med artiklar, används av både offert och order */
async function radFormular(malId, typ) {
  const artiklar = S.data.articles || (await api("/articles"));
  S.data.articles = artiklar;
  return `
  <div class="jnew" style="margin-top:14px">
    <strong style="font-family:var(--cond);text-transform:uppercase;letter-spacing:.05em">Lägg till rad</strong>
    <div class="fgrid">
      <div style="grid-column:1/-1"><label class="f" for="ln_art">Artikel ur registret</label>
        <select id="ln_art" onchange="artikelVald()">
          <option value="">Fri rad, skriv själv nedan</option>
          ${artiklar
            .map(
              (a) =>
                `<option value="${a.id}" data-pris="${a.sales_price}" data-enhet="${esc(a.unit)}"
                  data-namn="${esc(a.name)}" data-lager="${a.track_stock ? a.stock : ""}">
                  ${esc(a.article_no)} · ${esc(a.name)} · ${krRund(a.sales_price)} kr/${esc(a.unit)}${
                    a.track_stock ? ` · lager ${a.stock}` : ""
                  }</option>`
            )
            .join("")}
        </select></div>
      <div><label class="f" for="ln_namn">Benämning</label>
        <input id="ln_namn" oninput="kollaArtikelnamn(this.value)">
        <div class="hint" id="ln_match"></div></div>
      <div><label class="f" for="ln_typ">Typ</label><select id="ln_typ">
        <option value="material">Material</option><option value="arbete">Arbete</option>
        <option value="ovrigt">Övrigt</option></select></div>
      ${fld("ln_antal", "Antal", 1, "number")}
      ${fld("ln_enhet", "Enhet", "st")}
      ${fld("ln_pris", "À-pris", "", "number")}
      ${fld("ln_rabatt", "Rabatt %", "", "number")}
    </div>
    ${fld("ln_note", "Anteckning på raden", "")}
    <button class="btn pri sm" style="margin-top:12px" onclick="laggRad('${malId}','${typ}')">Lägg till</button>
  </div>`;
}

/* Letar upp liknande artiklar medan man skriver, så att samma sak inte läggs in
   under fem olika namn. Träffen går att använda direkt. */
let matchTimer;
function kollaArtikelnamn(text) {
  clearTimeout(matchTimer);
  const ruta = $("#ln_match");
  if (!ruta) return;
  if ($("#ln_art") && $("#ln_art").value) {
    ruta.textContent = "";
    return;
  }
  if (!text || text.trim().length < 3) {
    ruta.textContent = "";
    return;
  }
  matchTimer = setTimeout(async () => {
    try {
      const r = await api(`/articles/match?name=${encodeURIComponent(text)}`);
      if (!r.traffar.length) {
        ruta.innerHTML = `<span class="muted">Ny benämning. Du får frågan om att lägga upp den
          som artikel när raden sparas.</span>`;
        return;
      }
      ruta.innerHTML =
        `<span style="color:var(--brass)">Liknande finns redan:</span> ` +
        r.traffar
          .map(
            (t) =>
              `<button class="linkbtn" style="color:var(--water-dark);text-decoration:underline"
            onclick="valjMatchning('${t.id}','${esc(t.name)}',${t.sales_price},'${esc(t.unit)}')">
            ${esc(t.article_no)} ${esc(t.name)} · ${krRund(t.sales_price)} kr/${esc(t.unit)}</button>`
          )
          .join(" · ");
    } catch (_) {}
  }, 350);
}

function valjMatchning(id, namn, pris, enhet) {
  const val_ = $("#ln_art");
  if (val_) val_.value = id;
  $("#ln_namn").value = namn;
  $("#ln_pris").value = pris;
  $("#ln_enhet").value = enhet;
  $("#ln_match").innerHTML = `<span class="muted">Använder artikeln ur registret.</span>`;
}

function artikelVald() {
  const val_ = $("#ln_art");
  const o = val_.selectedOptions[0];
  if (!o || !val_.value) return;
  $("#ln_namn").value = o.dataset.namn || "";
  $("#ln_pris").value = o.dataset.pris || "";
  $("#ln_enhet").value = o.dataset.enhet || "st";
}

async function _laggRad(malId, typ) {
  const body = {
    article_id: val("ln_art") || null,
    name: val("ln_namn").trim(),
    kind: val("ln_typ"),
    unit: val("ln_enhet") || "st",
    quantity: numVal("ln_antal") ?? 1,
    unit_price: numVal("ln_pris") ?? 0,
    discount_percent: numVal("ln_rabatt") ?? 0,
    note: val("ln_note"),
  };
  if (!body.article_id && !body.name) return toast("Välj artikel eller skriv en benämning", true);
  const fritext = !body.article_id;
  try {
    const vag = typ === "quote" ? `/quotes/${malId}/lines` : `/work-orders/${malId}/lines`;
    await api(vag, { method: "POST", body });
    toast("Raden tillagd");
    forhandUppdatera();

    // Fritext som inte finns i registret: erbjud att lägga upp den, så att den
    // går att välja nästa gång och räknas med i lagret.
    if (fritext && body.name.length > 2 && body.unit_price > 0) {
      const r = await api(`/articles/match?name=${encodeURIComponent(body.name)}`);
      const exakt = r.traffar.some((t) => t.name.toLowerCase() === body.name.toLowerCase());
      if (!exakt && confirm(`Lägg upp "${body.name}" som artikel i registret?\n\nDå kan du välja den nästa gång, och den räknas med i lagret.`)) {
        await sparaRadSomArtikel(body);
      }
    }
    laddaOmDokument();
  } catch (e) {
    toast(e.message, true);
  }
}

async function sparaRadSomArtikel(rad) {
  const lagerfors = rad.kind === "material";
  try {
    const a = await api("/articles", {
      method: "POST",
      body: {
        name: rad.name,
        category: rad.kind === "arbete" ? "Arbete" : "",
        unit: rad.unit,
        sales_price: rad.unit_price,
        track_stock: lagerfors,
        stock: 0,
      },
    });
    S.data.articles = null;
    toast(
      lagerfors
        ? `${a.article_no} upplagd. Fyll i inköpspris och saldo under Artiklar.`
        : `${a.article_no} upplagd i registret.`
    );
  } catch (e) {
    toast(e.message, true);
  }
}


/* ---------------- offert ---------------- */
async function viewQuote() {
  const token = claim();
  mountShell(`<div class="skel" style="width:35%"></div><div class="skel"></div>`);
  const q = await api(`/quotes/${S.id}`);
  if (!current(token)) return;
  S.data.quote = q;
  const [text, klass] = OFFERT_STATUS[q.status] || [q.status, "n"];
  const laser = S.user.role === "lasare";
  const kanRedigera = !laser && ["utkast", "skickad"].includes(q.status);

  $("#view").innerHTML = `
  <button class="back" onclick="${q.customer_id ? `go('kund','${q.customer_id}/offerter')` : `go('besok','${q.visit_id}')`}">← Tillbaka</button>
  <div class="chead">
    <div class="spread" style="margin-bottom:0">
      <div><div class="eyebrow">${esc(q.quote_no)} · skapad ${dt(q.created_at, false)} av ${esc(q.created_by)}</div>
        <h1>${esc(q.title || "Offert")}</h1>
        <p class="lead">${esc(q.recipient_name)}${q.customer_name ? ` · ${esc(q.customer_name)}` : ""}
          ${
            !q.customer_id && !q.visit_id
              ? `<span class="tag n" style="margin-left:8px">Förfrågan, ingen kund än</span>`
              : ""
          }</p></div>
      <div class="row"><span class="tag ${klass}">${esc(text)}</span>
        <span class="mono" style="font-size:19px;font-weight:600">${kr(q.totals.brutto)} kr</span></div>
    </div>
    <div class="facts">
      <div class="fact"><div class="k">Giltig till</div><div class="v mono">${esc(q.valid_until || "—")}</div></div>
      <div class="fact"><div class="k">Mottagare</div><div class="v">${esc(q.recipient_email || "—")}</div></div>
      <div class="fact"><div class="k">Netto</div><div class="v mono">${kr(q.totals.netto)} kr</div></div>
      <div class="fact"><div class="k">Moms</div><div class="v mono">${kr(q.totals.moms)} kr</div></div>
      ${q.sent_at ? `<div class="fact"><div class="k">Skickad</div><div class="v mono" style="font-size:13px">${dt(q.sent_at)}</div></div>` : ""}
      ${q.decided_at ? `<div class="fact"><div class="k">Besked</div><div class="v mono">${esc(q.decided_at)}</div></div>` : ""}
    </div>
  </div>

  <div class="row" style="margin-bottom:16px">
    <button class="btn" onclick="oppnaPdf('/api/quotes/${q.id}/pdf')">Visa PDF</button>
    <button class="btn ghost" onclick="skrivUt('/api/quotes/${q.id}/pdf')">Skriv ut</button>
    <button class="btn ghost" onclick="laddaNer('/api/quotes/${q.id}/pdf?ladda_ner=true','${esc(q.quote_no)}.pdf')">Ladda ner</button>
    ${laser ? "" : `<button class="btn pri" onclick="skickaOffert()">Mejla till kund</button>`}
    ${laser ? "" : `<button class="btn ghost" onclick="sparaSomMall()">Spara som mall</button>`}
    ${
      !laser && !q.customer_id && !q.visit_id
        ? `<button class="btn pri" onclick="offertTillKund()">Blev kund</button>`
        : ""
    }
    ${
      !laser && q.status === "skickad"
        ? `<button class="btn ghost" onclick="offertBesked('accepterad')">Accepterad</button>
           <button class="btn ghost" onclick="offertBesked('avslagen')">Avslagen</button>`
        : ""
    }
    ${
      !laser && q.status === "accepterad" && q.customer_id
        ? `<button class="btn pri" onclick="skapaOrderFranOffert()">Skapa arbetsorder</button>`
        : ""
    }
  </div>
  <div id="sendbox"></div>

  <div class="card">
    <div class="hd"><h2>Rader</h2><span class="tag n">${q.lines.length}</span></div>
    ${
      q.lines.length
        ? radTabell(q, laser, kanRedigera)
        : `<div class="empty"><div class="big">Inga rader än</div>
           <p>Lägg till material och arbete nedan. Artiklar ur registret fyller i pris och enhet åt dig.</p></div>`
    }
    <div class="pad" id="radform">${kanRedigera ? "" : ""}</div>
  </div>

  ${
    laser
      ? ""
      : `<div class="card" style="margin-top:16px"><div class="hd"><h2>Text och villkor</h2></div>
    <div class="pad">
      <div class="fgrid">
        ${fld("q_titel", "Rubrik", q.title || "")}
        ${fld("q_giltig", "Giltig till", q.valid_until || "", "date")}
        ${fld("q_mott", "Mottagarens namn", q.recipient_name || "")}
        ${fld("q_mail", "E-post", q.recipient_email || "", "email")}
        ${fld("q_adr", "Adress", q.recipient_address || "")}
        ${fld("q_rabatt", "Rabatt på hela offerten %", q.discount_percent || "", "number")}
      </div>
      <label class="f" for="q_intro">Inledande text</label>
      <textarea id="q_intro" oninput="forhandUtkast()"></textarea>
      <label class="f" for="q_villkor">Villkor</label>
      <textarea id="q_villkor" oninput="forhandUtkast()"></textarea>
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="sparaOffert()">Spara</button>
        <button class="btn danger sm" style="margin-left:auto" onclick="taBortOffert()">Ta bort offerten</button>
      </div>
    </div></div>`
  }`;

  if (kanRedigera) $("#radform").innerHTML = await radFormular(q.id, "quote");
  // Textrutorna fylls efter renderingen, så att innehållet inte behöver escapas två gånger
  if ($("#q_intro")) $("#q_intro").value = q.intro || "";
  if ($("#q_villkor")) $("#q_villkor").value = q.terms || "";
  kopplaForhand(`/api/quotes/${q.id}/pdf`);
}

async function _sparaOffert() {
  try {
    await api(`/quotes/${S.data.quote.id}`, {
      method: "PATCH",
      body: {
        title: val("q_titel"),
        valid_until: val("q_giltig"),
        recipient_name: val("q_mott"),
        recipient_email: val("q_mail"),
        recipient_address: val("q_adr"),
        discount_percent: numVal("q_rabatt") ?? 0,
        intro: val("q_intro"),
        terms: val("q_villkor"),
      },
    });
    toast("Offerten sparad");
    viewQuote();
  } catch (e) {
    toast(e.message, true);
  }
}

/* Sparar tyst och uppdaterar förhandsgranskningen medan man skriver texten */
let utkastTimer;
function forhandUtkast() {
  if (!S.forhand) return;
  clearTimeout(utkastTimer);
  utkastTimer = setTimeout(async () => {
    try {
      await api(`/quotes/${S.data.quote.id}`, {
        method: "PATCH",
        body: { intro: val("q_intro"), terms: val("q_villkor"), title: val("q_titel") },
      });
      forhandUppdatera();
    } catch (_) {}
  }, 900);
}

async function offertBesked(status) {
  await api(`/quotes/${S.data.quote.id}`, { method: "PATCH", body: { status } });
  toast(status === "accepterad" ? "Markerad som accepterad" : "Markerad som avslagen");
  viewQuote();
}

async function taBortOffert() {
  const q = S.data.quote;
  if (!confirm(`Ta bort ${q.quote_no}? Det går inte att ångra.`)) return;
  await api(`/quotes/${q.id}`, { method: "DELETE" });
  toast("Offerten borttagen");
  if (q.customer_id) go("kund", q.customer_id + "/offerter");
  else go("besok", q.visit_id);
}

function skickaOffert() {
  const q = S.data.quote;
  const box = $("#sendbox");
  if (box.innerHTML) return (box.innerHTML = "");
  box.innerHTML = `
  <div class="card" style="margin-bottom:16px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Mejla offerten</h2></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">PDF:en bifogas, sparas bland kundens dokument och
        journalförs. Skickas från din egen server.</p>
      <div class="fgrid">
        ${fld("sq_to", "Mottagare", q.recipient_email || "", "email")}
        ${fld("sq_amne", "Ämne", `Offert ${q.quote_no}${q.title ? " – " + q.title : ""}`)}
      </div>
      <label class="f" for="sq_msg">Meddelande</label>
      <textarea id="sq_msg" placeholder="Lämna tomt så skrivs ett standardmeddelande med summa och giltighetstid."></textarea>
      <div class="row" style="margin-top:12px">
        <button class="btn pri sm" onclick="genomforSkick()">Skicka</button>
        <button class="btn ghost sm" onclick="document.getElementById('sendbox').innerHTML=''">Avbryt</button>
      </div>
    </div></div>`;
}

async function _genomforSkick() {
  try {
    const r = await api(`/quotes/${S.data.quote.id}/send`, {
      method: "POST",
      body: { recipient: val("sq_to"), subject: val("sq_amne"), message: val("sq_msg") },
    });
    toast(
      r.saved_to_customer
        ? `Skickad till ${r.recipient} och sparad bland dokumenten`
        : `Skickad till ${r.recipient}`
    );
    viewQuote();
  } catch (e) {
    toast(e.message, true);
  }
}

async function _skapaOrderFranOffert() {
  const q = S.data.quote;
  try {
    const o = await api("/work-orders", {
      method: "POST",
      body: {
        customer_id: q.customer_id,
        facility_id: q.facility_id,
        quote_id: q.id,
        title: q.title,
        copy_quote_lines: true,
      },
    });
    toast(`${o.order_no} skapad med ${o.lines.length} rader från offerten`);
    go("order", o.id);
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- arbetsorder ---------------- */
async function viewOrder() {
  const token = claim();
  mountShell(`<div class="skel" style="width:35%"></div><div class="skel"></div>`);
  const o = await api(`/work-orders/${S.id}`);
  if (!current(token)) return;
  S.data.order = o;
  const [text, klass] = ORDER_STATUS[o.status] || [o.status, "n"];
  const laser = S.user.role === "lasare";
  const kanRedigera = !laser && ["oppen", "utford"].includes(o.status);

  $("#view").innerHTML = `
  <button class="back" onclick="go('kund','${o.customer_id}/order')">← Tillbaka till kunden</button>
  <div class="chead">
    <div class="spread" style="margin-bottom:0">
      <div><div class="eyebrow">${esc(o.order_no)} · skapad ${dt(o.created_at, false)}</div>
        <h1>${esc(o.title || "Arbetsorder")}</h1>
        <p class="lead">${esc(o.customer_name)}</p></div>
      <div class="row"><span class="tag ${klass}">${esc(text)}</span>
        <span class="mono" style="font-size:19px;font-weight:600">${kr(o.totals.brutto)} kr</span></div>
    </div>
    <div class="facts">
      <div class="fact"><div class="k">Material</div><div class="v mono">${kr(o.material_total)} kr</div></div>
      <div class="fact"><div class="k">Arbete</div><div class="v mono">${kr(o.labour_total)} kr</div></div>
      <div class="fact"><div class="k">Utförd</div><div class="v mono">${esc(o.performed_at || "—")}</div></div>
      <div class="fact"><div class="k">Fakturerad</div><div class="v mono">${esc(o.invoiced_at || "—")}</div></div>
      <div class="fact"><div class="k">Fakturanr</div><div class="v mono">${esc(o.invoice_no || "—")}</div></div>
      <div class="fact"><div class="k">Betald</div><div class="v mono">${esc(o.paid_at || "—")}</div></div>
    </div>
  </div>

  ${
    laser
      ? ""
      : `<div class="card" style="margin-bottom:16px"><div class="hd"><h2>Status</h2>
      ${o.stock_deducted ? `<span class="tag ok">Lagret draget</span>` : `<span class="tag n">Lagret ej draget</span>`}</div>
    <div class="pad">
      <div class="row">
        ${Object.entries(ORDER_STATUS)
          .filter(([k]) => k !== "makulerad")
          .map(
            ([k, [l, kl]]) =>
              `<button class="btn ${o.status === k ? "pri" : "ghost"} sm"
                onclick="orderStatus('${k}')">${l}</button>`
          )
          .join("")}
      </div>
      <p class="hint">Materialet dras från lagret när ordern markeras utförd, en gång.
        Fakturerad och betald sätter datum automatiskt om de är tomma.</p>
      <div class="fgrid" style="margin-top:8px">
        ${fld("o_faktnr", "Fakturanummer", o.invoice_no || "")}
        ${fld("o_utford", "Utförd datum", o.performed_at || "", "date")}
        ${fld("o_av", "Utförd av", o.performed_by || "")}
      </div>
      <button class="btn ghost sm" style="margin-top:12px" onclick="sparaOrder()">Spara uppgifter</button>
    </div></div>`
  }

  <div class="row" style="margin-bottom:16px">
    <button class="btn ghost" onclick="oppnaPdf('/api/work-orders/${o.id}/pdf')">Visa PDF</button>
    <button class="btn ghost" onclick="skrivUt('/api/work-orders/${o.id}/pdf')">Skriv ut</button>
    ${laser ? "" : `<button class="btn ghost" onclick="sparaOrderPdf()">Spara bland dokumenten</button>`}
  </div>

  <div class="card">
    <div class="hd"><h2>Material och arbete</h2><span class="tag n">${o.lines.length} rader</span></div>
    ${
      o.lines.length
        ? radTabell(o, laser, kanRedigera)
        : `<div class="empty"><div class="big">Inga rader än</div>
           <p>Fyll i vad som gick åt, så syns det vid faktureringen.</p></div>`
    }
    <div class="pad" id="radform"></div>
  </div>

  ${
    laser
      ? ""
      : `<div class="card" style="margin-top:16px"><div class="hd"><h2>Beskrivning</h2></div>
    <div class="pad">
      ${fld("o_titel", "Rubrik", o.title || "")}
      <label class="f" for="o_beskr">Vad som gjordes</label>
      <textarea id="o_beskr">${esc(o.description || "")}</textarea>
      <div class="row" style="margin-top:12px">
        <button class="btn pri sm" onclick="sparaOrder()">Spara</button>
        ${
          ["fakturerad", "betald"].includes(o.status)
            ? ""
            : `<button class="btn danger sm" style="margin-left:auto" onclick="taBortOrder()">Ta bort</button>`
        }
      </div>
    </div></div>`
  }`;

  if (kanRedigera) $("#radform").innerHTML = await radFormular(o.id, "order");
  kopplaForhand(`/api/work-orders/${o.id}/pdf`);
}

async function orderStatus(status) {
  const o = S.data.order;
  if (status === "utford" && !o.stock_deducted) {
    const material = o.lines.filter((r) => r.kind === "material" && r.article_id).length;
    if (
      material &&
      !confirm(`Markera som utförd? ${material} materialrader dras från lagret. Det görs bara en gång.`)
    )
      return;
  }
  try {
    const r = await api(`/work-orders/${o.id}`, { method: "PATCH", body: { status } });
    toast(
      r.stock_lines_deducted
        ? `${ORDER_STATUS[status][0]}. ${r.stock_lines_deducted} rader drogs från lagret.`
        : ORDER_STATUS[status][0]
    );
    viewOrder();
  } catch (e) {
    toast(e.message, true);
  }
}

async function _sparaOrder() {
  try {
    await api(`/work-orders/${S.data.order.id}`, {
      method: "PATCH",
      body: {
        title: val("o_titel") || S.data.order.title,
        description: val("o_beskr") ?? S.data.order.description,
        invoice_no: val("o_faktnr") ?? "",
        performed_at: val("o_utford") ?? "",
        performed_by: val("o_av") ?? "",
      },
    });
    toast("Sparad");
    viewOrder();
  } catch (e) {
    toast(e.message, true);
  }
}

async function sparaOrderPdf() {
  try {
    await api(`/work-orders/${S.data.order.id}/save-pdf`, { method: "POST" });
    toast("Sparad bland kundens dokument");
  } catch (e) {
    toast(e.message, true);
  }
}

async function taBortOrder() {
  const o = S.data.order;
  if (!confirm(`Ta bort ${o.order_no}?`)) return;
  try {
    await api(`/work-orders/${o.id}`, { method: "DELETE" });
    toast("Borttagen");
    go("kund", o.customer_id + "/order");
  } catch (e) {
    toast(e.message, true);
  }
}


/* ---------------- artikelregister ---------------- */
async function viewArticles() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const q = S.filter.artQ || "";
  const [artiklar, sum] = await Promise.all([
    api(`/articles?${new URLSearchParams({ q, ...(S.filter.artLow ? { low_stock: "true" } : {}) })}`),
    api("/articles/summary"),
  ]);
  if (!current(token)) return;
  S.data.articles = artiklar;

  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Lager och prislista</div><h1>Artiklar</h1>
      <p class="lead">Det du har hemma och det du säljer. Artiklar härifrån fyller i pris och enhet
      på offerter och arbetsorder, och lagret dras när en order markeras utförd.</p></div>
    ${S.user.role === "lasare" ? "" : `<button class="btn pri" onclick="nyArtikel()">+ Ny artikel</button>`}
  </div>

  <div class="stats">
    <div class="stat"><div class="v">${sum.antal}</div><div class="l">Artiklar</div></div>
    <div class="stat"><div class="v">${krRund(sum.lagervarde)}</div><div class="l">Lagervärde, kr</div></div>
    <div class="stat ${sum.laga_saldon.length ? "warn" : ""}"><div class="v">${sum.laga_saldon.length}</div>
      <div class="l">Under min-nivå</div></div>
    <div class="stat"><div class="v">${sum.kategorier.length}</div><div class="l">Kategorier</div></div>
  </div>

  <div id="artform"></div>

  <div class="card">
    <div class="hd"><h2>Register</h2>
      <input id="artsok" placeholder="Sök artikel, nummer eller leverantör" value="${esc(q)}"
        style="max-width:260px;margin-left:auto" oninput="artikelSok(this.value)">
      <button class="btn ghost sm" onclick="S.filter.artLow=!S.filter.artLow;viewArticles()">
        ${S.filter.artLow ? "Visa alla" : "Bara låga saldon"}</button></div>
    <table><thead><tr><th>Nr</th><th>Benämning</th><th>Kategori</th><th>Inpris</th>
      <th>Utpris</th><th>Marginal</th><th>Lager</th><th></th></tr></thead>
    <tbody>${artiklar
      .map(
        (a) => `<tr>
      <td data-l="Nr" class="tid">${esc(a.article_no)}</td>
      <td data-l="Benämning"><span class="tname">${esc(a.name)}</span>
        ${a.supplier ? `<div class="tsub">${esc(a.supplier)}</div>` : ""}</td>
      <td data-l="Kategori">${esc(a.category || "—")}</td>
      <td data-l="Inpris" class="tid">${krRund(a.purchase_price)}</td>
      <td data-l="Utpris" class="tid">${krRund(a.sales_price)}</td>
      <td data-l="Marginal" class="tid">${
        a.margin_percent === null ? "—" : `${krRund(a.margin)} kr · ${a.margin_percent} %`
      }</td>
      <td data-l="Lager">${
        a.track_stock
          ? `<span class="${a.low_stock ? "tag soon" : "tid"}">${a.stock} ${esc(a.unit)}${
              a.min_stock ? ` (min ${a.min_stock})` : ""
            }</span>`
          : `<span class="tsub">lagerförs ej</span>`
      }</td>
      <td><div class="row" style="gap:4px">
        ${
          S.user.role === "lasare"
            ? ""
            : `${a.track_stock ? `<button class="btn ghost sm" onclick="justeraLager('${a.id}','${esc(a.name)}',${a.stock})">Lager</button>` : ""}
               <button class="btn ghost sm" onclick="redigeraArtikel('${a.id}')">Ändra</button>`
        }</div></td></tr>`
      )
      .join("")}</tbody></table>
    ${
      artiklar.length
        ? ""
        : `<div class="empty"><div class="big">Inga artiklar</div>
           <p>Lägg upp det du har hemma i lager och det du brukar debitera.</p></div>`
    }
  </div>`;
}

let artTimer;
function artikelSok(v) {
  clearTimeout(artTimer);
  artTimer = setTimeout(() => {
    S.filter.artQ = v;
    viewArticles();
  }, 300);
}

function artikelFormular(a) {
  return `<div class="fgrid">
      ${fld("a_namn", "Benämning", a ? a.name : "")}
      ${fld("a_nr", "Artikelnummer", a ? a.article_no : "", "text", 'placeholder="Sätts automatiskt"')}
      ${fld("a_kat", "Kategori", a ? a.category : "", "text", 'placeholder="Rör, Pumpar, El, Arbete"')}
      ${fld("a_enhet", "Enhet", a ? a.unit : "st")}
      ${fld("a_in", "Inköpspris", a ? a.purchase_price : "", "number")}
      ${fld("a_ut", "Försäljningspris", a ? a.sales_price : "", "number")}
      ${fld("a_moms", "Moms %", a ? a.vat_percent : 25, "number")}
      ${fld("a_lev", "Leverantör", a ? a.supplier : "")}
      ${a ? "" : fld("a_saldo", "Ingående saldo", "", "number")}
      ${fld("a_min", "Min-nivå för varning", a ? a.min_stock : "", "number")}
    </div>
    <div class="row" style="margin-top:10px">
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="a_lager" ${!a || a.track_stock ? "checked" : ""} style="width:auto">
        Lagerförs, saldot dras vid utförd order</label>
    </div>`;
}

function nyArtikel() {
  const box = $("#artform");
  if (box.innerHTML) return (box.innerHTML = "");
  stangAndraFormular("artform");
  box.innerHTML = `<div class="card" style="margin-bottom:16px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Ny artikel</h2></div>
    <div class="pad">${artikelFormular(null)}
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="sparaNyArtikel()">Spara</button>
        <button class="btn ghost sm" onclick="document.getElementById('artform').innerHTML=''">Avbryt</button>
      </div></div></div>`;
  // Utan detta hamnar formuläret nedanför statistikrutorna, alltså utanför
  // skärmen på telefon, och det ser ut som att knappen inte gör något.
  scrollTill(box);
  const f = $("#a_namn");
  if (f) f.focus();
}

async function _sparaNyArtikel() {
  if (!val("a_namn").trim()) return toast("Artikeln behöver ett namn", true);
  try {
    await api("/articles", {
      method: "POST",
      body: {
        name: val("a_namn").trim(),
        article_no: val("a_nr").trim(),
        category: val("a_kat"),
        unit: val("a_enhet") || "st",
        purchase_price: numVal("a_in") ?? 0,
        sales_price: numVal("a_ut") ?? 0,
        vat_percent: numVal("a_moms") ?? 25,
        supplier: val("a_lev"),
        stock: numVal("a_saldo") ?? 0,
        min_stock: numVal("a_min") ?? 0,
        track_stock: $("#a_lager").checked,
      },
    });
    toast("Artikeln sparad");
    S.data.articles = null;
    viewArticles();
  } catch (e) {
    toast(e.message, true);
  }
}

function redigeraArtikel(id) {
  const a = (S.data.articles || []).find((x) => x.id === id);
  const box = $("#artform");
  if (!a) return;
  stangAndraFormular("artform");
  box.innerHTML = `<div class="card" style="margin-bottom:16px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>${esc(a.article_no)} ${esc(a.name)}</h2></div>
    <div class="pad">${artikelFormular(a)}
      <p class="hint">Saldot ändras inte här, utan under Lager, så att varje förändring går att
        förklara i efterhand.</p>
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="sparaArtikel('${a.id}')">Spara</button>
        <button class="btn ghost sm" onclick="visaRorelser('${a.id}')">Lagerhistorik</button>
        <button class="btn ghost sm" onclick="document.getElementById('artform').innerHTML=''">Avbryt</button>
        <button class="btn danger sm" style="margin-left:auto" onclick="avaktiveraArtikel('${a.id}')">Avaktivera</button>
      </div>
      <div id="rorelser"></div></div></div>`;
  scrollTill(box);
}

async function sparaArtikel(id) {
  try {
    await api(`/articles/${id}`, {
      method: "PATCH",
      body: {
        name: val("a_namn").trim(),
        article_no: val("a_nr").trim(),
        category: val("a_kat"),
        unit: val("a_enhet"),
        purchase_price: numVal("a_in") ?? 0,
        sales_price: numVal("a_ut") ?? 0,
        vat_percent: numVal("a_moms") ?? 25,
        supplier: val("a_lev"),
        min_stock: numVal("a_min") ?? 0,
        track_stock: $("#a_lager").checked,
      },
    });
    toast("Sparad");
    S.data.articles = null;
    viewArticles();
  } catch (e) {
    toast(e.message, true);
  }
}

async function avaktiveraArtikel(id) {
  if (!confirm("Avaktivera artikeln? Den försvinner ur listan men står kvar på gamla order.")) return;
  await api(`/articles/${id}`, { method: "DELETE" });
  toast("Avaktiverad");
  S.data.articles = null;
  viewArticles();
}

async function justeraLager(id, namn, saldo) {
  const svar = prompt(`Nytt saldo för ${namn} (nu ${saldo}). Skriv +12 för inleverans.`, "");
  if (svar === null || !svar.trim()) return;
  const text = svar.trim().replace(",", ".");
  const body = text.startsWith("+") || text.startsWith("-")
    ? { change: parseFloat(text), reason: parseFloat(text) > 0 ? "inkop" : "justering" }
    : { set_to: parseFloat(text), reason: "justering" };
  if (isNaN(body.change ?? body.set_to)) return toast("Kunde inte tolka talet", true);
  const anteckning = prompt("Anteckning, till exempel leverantör eller följesedel:", "") || "";
  try {
    await api(`/articles/${id}/stock`, { method: "POST", body: { ...body, note: anteckning } });
    toast("Saldot uppdaterat");
    S.data.articles = null;
    viewArticles();
  } catch (e) {
    toast(e.message, true);
  }
}

async function visaRorelser(id) {
  const box = $("#rorelser");
  if (box.innerHTML) return (box.innerHTML = "");
  const rader = await api(`/articles/${id}/movements`);
  box.innerHTML = `<div style="margin-top:14px">
    <div class="eyebrow">Lagerhistorik</div>
    ${
      rader.length
        ? rader
            .map(
              (r) => `<div class="filerow">
        <div class="ftype" style="background:${r.change > 0 ? "#2E7D5B" : "#A6402F"}">
          ${r.change > 0 ? "+" : ""}${r.change}</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:500">${esc(r.reason)} · saldo efter ${r.balance_after}</div>
          <div class="fmeta">${dt(r.at)} · ${esc(r.by_user)}${r.note ? " · " + esc(r.note) : ""}</div></div>
      </div>`
            )
            .join("")
        : `<p class="hint">Inga rörelser än.</p>`
    }</div>`;
}

/* ---------------- ekonomiöversikt ---------------- */
async function viewEconomy() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const filter = S.filter.ekStatus || "aktiva";
  const [sum, order, offerter] = await Promise.all([
    api("/work-orders/summary"),
    api(`/work-orders?status=${filter}`),
    api("/quotes?status=aktiva"),
  ]);
  if (!current(token)) return;

  const orderRad = (o) => {
    const [text, klass] = ORDER_STATUS[o.status] || [o.status, "n"];
    return `<tr class="clickable" onclick="go('order','${o.id}')">
      <td data-l="Nr" class="tid">${esc(o.order_no)}</td>
      <td data-l="Kund"><span class="tname">${esc(o.customer_name)}</span>
        <div class="tsub">${esc(o.title || "")}</div></td>
      <td data-l="Utförd" class="tid">${esc(o.performed_at || "—")}</td>
      <td data-l="Fakturanr" class="tid">${esc(o.invoice_no || "—")}</td>
      <td data-l="Belopp" class="tid"><strong>${kr(o.totals.brutto)}</strong></td>
      <td data-l="Status"><span class="tag ${klass}">${text}</span></td></tr>`;
  };

  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Uppföljning</div><h1>Att fakturera</h1>
      <p class="lead">Arbetsorder som är utförda men inte fakturerade, och fakturor som väntar på
      betalning. Det är här saker annars glöms bort.</p></div>
    <div class="row">
    <select style="width:auto" onchange="S.filter.ekStatus=this.value;viewEconomy()">
      ${[["aktiva", "Pågående"], ["att_fakturera", "Att fakturera"], ["obetalda", "Obetalda"],
         ["betald", "Betalda"], ["", "Alla"]]
        .map(([v, l]) => `<option value="${v}"${filter === v ? " selected" : ""}>${l}</option>`)
        .join("")}
    </select></div>
  </div>
  <div id="forfraganform"></div>

  <div class="stats">
    <div class="stat ${sum.att_fakturera ? "warn" : ""}"><div class="v">${sum.att_fakturera}</div>
      <div class="l">Att fakturera</div></div>
    <div class="stat ${sum.att_fakturera ? "warn" : ""}"><div class="v">${krRund(sum.att_fakturera_belopp)}</div>
      <div class="l">Kronor att fakturera</div></div>
    <div class="stat ${sum.obetalda ? "bad" : ""}"><div class="v">${sum.obetalda}</div>
      <div class="l">Obetalda fakturor</div></div>
    <div class="stat"><div class="v">${krRund(sum.obetalda_belopp)}</div>
      <div class="l">Kronor utestående</div></div>
  </div>

  <div class="card" style="margin-bottom:18px">
    <div class="hd"><h2>Arbetsorder</h2><span class="tag n">${order.length}</span></div>
    <table><thead><tr><th>Nr</th><th>Kund</th><th>Utförd</th><th>Fakturanr</th>
      <th>Belopp</th><th>Status</th></tr></thead>
    <tbody>${order.map(orderRad).join("")}</tbody></table>
    ${order.length ? "" : `<div class="empty"><div class="big">Inget här</div><p>Byt filter för att se fler.</p></div>`}
  </div>

  <div class="card">
    <div class="hd"><h2>Öppna offerter</h2><span class="tag n">${offerter.length}</span></div>
    <table><thead><tr><th>Nr</th><th>Mottagare</th><th>Rubrik</th><th>Giltig till</th>
      <th>Belopp</th><th>Status</th></tr></thead>
    <tbody>${offerter
      .map((q) => {
        const [text, klass] = OFFERT_STATUS[q.status] || [q.status, "n"];
        return `<tr class="clickable" onclick="go('offert','${q.id}')">
        <td data-l="Nr" class="tid">${esc(q.quote_no)}</td>
        <td data-l="Mottagare"><span class="tname">${esc(q.recipient_name)}</span></td>
        <td data-l="Rubrik">${esc(q.title || "—")}</td>
        <td data-l="Giltig till" class="tid">${esc(q.valid_until || "—")}</td>
        <td data-l="Belopp" class="tid"><strong>${kr(q.totals.brutto)}</strong></td>
        <td data-l="Status"><span class="tag ${klass}">${text}</span></td></tr>`;
      })
      .join("")}</tbody></table>
    ${offerter.length ? "" : `<div class="empty"><div class="big">Inga öppna offerter</div></div>`}
  </div>`;
}


/* ---------------- mer ---------------- */
async function viewMore() {
  const token = claim();
  mountShell("");
  const [sum, handelser] = await Promise.all([
    api("/reminders/summary").catch(() => ({ overdue: 0, open: 0 })),
    api("/events?only_open=true&limit=1").catch(() => ({ open: 0 })),
  ]);
  if (!current(token)) return;

  const extra = {
    paminnelser: sum.open ? `${sum.open} öppna${sum.overdue ? `, ${sum.overdue} försenade` : ""}` : "",
    handelser: handelser.open ? `${handelser.open} att titta på` : "",
  };

  $("#view").innerHTML = `
  <div class="spread"><div><div class="eyebrow">Allt annat</div><h1>Mer</h1></div></div>
  <div class="card"><div class="pad" style="padding-top:6px">
    ${MER_POSTER.map(
      ([vag, namn, beskrivning]) => `<button class="merrad" onclick="go('${vag}')">
      <span><strong>${namn}</strong><span class="m">${extra[vag] || beskrivning}</span></span>
      <span class="pil">→</span></button>`
    ).join("")}
    ${
      S.user.role === "admin"
        ? `<button class="merrad" onclick="go('admin','konton')">
        <span><strong>Inställningar</strong><span class="m">Konton, företag, notiser, backup</span></span>
        <span class="pil">→</span></button>`
        : ""
    }
    <button class="merrad" onclick="logout()">
      <span><strong>Logga ut</strong><span class="m">${esc(S.user.full_name || S.user.username)}</span></span>
      <span class="pil">→</span></button>
  </div></div>
  <p class="hint" id="versionsrad" style="text-align:center;margin-top:14px"></p>`;

  // Versionerna längst ned, så att man kan svara på frågan utan terminal
  try {
    const v = await fetch("/api/version", { cache: "no-store" }).then((r) => r.json());
    const rad = $("#versionsrad");
    if (rad)
      rad.innerHTML =
        v.in_sync === false
          ? `<span style="color:var(--alert)">Backend ${esc(v.version)}, gränssnitt
             ${esc(v.ui_version || "okänt")} — inte i takt</span>`
          : `Version ${esc(v.version)}`;
  } catch (_) {}
}

/* ---------------- mitt konto ---------------- */
const ROLL_TEXT = {
  admin: "Administratör – kan allt, inklusive konton och backup",
  tekniker: "Tekniker – kan läsa och skriva kunder, journal och filer",
  lasare: "Läsare – kan bara läsa",
};

async function viewAccount() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const [me, devices] = await Promise.all([api("/me"), api("/notifications/push/status")]);
  if (!current(token)) return;
  S.user = { ...S.user, ...me };
  localStorage.setItem("bj_user", JSON.stringify(S.user));

  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Inloggad som ${esc(me.username)}</div>
      <h1>${esc(me.full_name || me.username)}</h1>
      <p class="lead">${esc(ROLL_TEXT[me.role] || me.role)}</p></div>
    <button class="btn ghost" onclick="logout()">Logga ut</button>
  </div>

  <div class="grid2">
    <div>
      <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Tvåfaktor</h2>
        <span class="tag ${me.totp_enabled ? "ok" : me.totp_required ? "action" : "n"}">
          ${me.totp_enabled ? "Påslagen" : me.totp_required ? "Krävs, ej påslagen" : "Av"}</span></div>
        <div class="pad" id="totpbox">${totpBoxHtml(me)}</div></div>

      <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Byt lösenord</h2></div>
        <div class="pad">
          <div class="fgrid">
            <div><label class="f" for="pw_old">Nuvarande lösenord</label>
              <input id="pw_old" type="password" autocomplete="current-password"></div>
            <div><label class="f" for="pw_new">Nytt lösenord</label>
              <input id="pw_new" type="password" autocomplete="new-password"></div>
            <div><label class="f" for="pw_new2">Upprepa nytt</label>
              <input id="pw_new2" type="password" autocomplete="new-password"></div>
          </div>
          <div class="hint">Minst 10 tecken. En fras med tre ord är både lättare att minnas och
            svårare att gissa än ett kort krångligt lösenord.</div>
          <button class="btn pri sm" style="margin-top:14px" onclick="changePassword()">Byt lösenord</button>
        </div></div>
    </div>

    <div>
      <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Textstorlek</h2></div>
        <div class="pad">
          <p class="lead" style="margin-top:0">Gäller den här enheten och sparas.</p>
          ${sizePicker()}
        </div></div>

      <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Lägg till på hemskärmen</h2></div>
        <div class="pad" id="installbox">${installHtml()}</div></div>

      <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Vad du blir påmind om</h2></div>
        <div class="pad">
          <p class="lead" style="margin-top:0">Påminnelser knyts till den som senast var på jobbet,
            skrev offerten eller lade upp besöket.</p>
          ${[
            ["mina", "Mina egna", "Det jag ansvarar för, plus sådant ingen tagit"],
            ["alla", "Allas", "Allt som händer i företaget"],
            ["inga", "Inga", "Notiser stängda, påminnelserna finns kvar i appen"],
          ]
            .map(
              ([v, rubrik, text]) => `<label style="display:flex;gap:10px;align-items:flex-start;
              padding:9px 0;border-bottom:1px solid #E9EEEE;font-size:14.5px">
              <input type="radio" name="scope" value="${v}" style="width:auto;margin-top:4px"
                ${(me.notify_scope || "mina") === v ? "checked" : ""} onchange="sattOmfang('${v}')">
              <span><strong>${rubrik}</strong><span class="hint" style="display:block;margin:0">${text}</span></span>
            </label>`
            )
            .join("")}
          ${
            me.role === "admin"
              ? `<p class="hint">Som administratör står du på Allas från början, så att inget faller
                 mellan stolarna när någon är sjuk eller slutar.</p>`
              : ""
          }
        </div></div>

      <div class="card"><div class="hd"><h2>Notiser på den här enheten</h2></div><div class="pad">
        <div id="pushbanner"></div>
        <p class="hint">Notiser hör till enheten, inte kontot. Varje telefon och dator slås på en gång.</p>
        ${
          devices.devices.length
            ? devices.devices
                .map(
                  (d) => `<div class="filerow"><div class="ftype other">ENHET</div>
          <div style="flex:1;min-width:0">
            <div style="font-weight:500;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((d.user_agent || "okänd enhet").slice(0, 70))}</div>
            <div class="fmeta">tillagd ${dt(d.created_at, false)}${d.last_used_at ? " · senaste notis " + dt(d.last_used_at) : ""}</div></div></div>`
                )
                .join("")
            : `<p class="hint">Inga enheter registrerade än.</p>`
        }
      </div></div>
    </div>
  </div>`;

  const banner = $("#pushbanner");
  if (banner) banner.innerHTML = await pushBanner();
}

function totpBoxHtml(me) {
  if (me.totp_enabled)
    return `<p class="lead" style="margin-top:0">Engångskod krävs vid inloggning.
      ${
        me.totp_required
          ? "Tvåfaktor är obligatorisk för ditt konto och kan inte stängas av."
          : "Tappar du telefonen kan en administratör nollställa den åt dig."
      }</p>
      ${
        me.totp_required
          ? ""
          : `<div class="row"><input id="totp_pw" type="password" placeholder="Ditt lösenord" style="max-width:240px">
             <button class="btn danger sm" onclick="totpDisable()">Stäng av</button></div>`
      }`;
  return `<p class="lead" style="margin-top:0">Skydda kontot med en engångskod från Google
      Authenticator, Aegis, 1Password eller liknande. Utan den räcker lösenordet för att komma in
      i hela kundregistret.</p>
    <button class="btn pri sm" onclick="totpStart()">Slå på tvåfaktor</button>`;
}

async function sattOmfang(scope) {
  try {
    await api("/me/notify-scope", { method: "PUT", body: { scope } });
    S.user.notify_scope = scope;
    toast(
      scope === "alla"
        ? "Du får påminnelser om allt"
        : scope === "mina"
          ? "Du får bara dina egna påminnelser"
          : "Notiser om påminnelser avstängda"
    );
  } catch (e) {
    toast(e.message, true);
  }
}

async function changePassword() {
  const gammalt = val("pw_old");
  const nytt = val("pw_new");
  if (nytt !== val("pw_new2")) return toast("De nya lösenorden är inte lika", true);
  if (nytt.length < 10) return toast("Nytt lösenord måste vara minst 10 tecken", true);
  try {
    await api("/me/password", {
      method: "POST",
      body: { current_password: gammalt, new_password: nytt },
    });
    toast("Lösenordet bytt");
    viewAccount();
  } catch (e) {
    toast(e.message, true);
  }
}

/* Tvingad uppsättning: servern nekar allt annat tills tvåfaktor är på plats. */
async function forceTotpSetup() {
  root().innerHTML = `
  <div class="login">
    <div class="box" style="max-width:520px">
      <div class="bn">Tvåfaktor krävs</div>
      <span class="bs">INNAN DU KAN FORTSÄTTA</span>
      <p class="lead">Din administratör har gjort tvåfaktor obligatorisk. Skanna koden med
        Google Authenticator, Aegis, 1Password eller liknande och bekräfta med sex siffror.</p>
      <div id="totpbox"></div>
      <button class="btn ghost sm" style="margin-top:14px" onclick="logout()">Logga ut i stället</button>
    </div>
  </div>`;
  await totpStart(true);
}

/* ---------------- textstorlek ---------------- */
const SIZES = [
  ["normal", "Normal"],
  ["stor", "Stor"],
  ["storre", "Större"],
  ["storst", "Störst"],
];

function applySize(name) {
  const value = SIZES.some(([k]) => k === name) ? name : "normal";
  if (value === "normal") document.documentElement.removeAttribute("data-scale");
  else document.documentElement.setAttribute("data-scale", value);
  try {
    localStorage.setItem("bj_size", value);
  } catch (_) {}
  S.size = value;
}

function sizePicker() {
  return `<div class="sizepick" role="group" aria-label="Textstorlek">
    ${SIZES.map(
      ([k, label]) =>
        `<button class="${S.size === k ? "on" : ""}" onclick="setSize('${k}')"
          title="${label} text" aria-pressed="${S.size === k}">A</button>`
    ).join("")}</div>`;
}

function setSize(name) {
  applySize(name);
  if (S.route === "konto") viewAccount();
  toast(`Textstorlek: ${(SIZES.find(([k]) => k === name) || [])[1]}`);
}

/* ---------------- installera på hemskärmen ---------------- */
const INSTALL = {
  prompt: null,
  installed: () =>
    (typeof window.matchMedia === "function" &&
      window.matchMedia("(display-mode: standalone)").matches) ||
    navigator.standalone === true,
  isIos: () =>
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1),
};

// Chrome och Edge på Android och dator skickar den här strax efter laddning.
// Safari gör det inte, och har inget motsvarande API.
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  INSTALL.prompt = e;
  const box = document.getElementById("installbox");
  if (box) box.innerHTML = installHtml();
});
window.addEventListener("appinstalled", () => {
  INSTALL.prompt = null;
  toast("Borrjournal tillagd på hemskärmen");
  const box = document.getElementById("installbox");
  if (box) box.innerHTML = installHtml();
});

function installHtml() {
  try {
    return installHtmlInner();
  } catch (e) {
    console.warn("installationskortet kunde inte ritas", e);
    return `<p class="hint" style="margin:0">Installation stöds inte i den här webbläsaren.</p>`;
  }
}

function installHtmlInner() {
  if (INSTALL.installed())
    return `<p class="hint" style="margin:0">Appen körs redan från hemskärmen. Notiser fungerar.</p>`;

  if (!window.isSecureContext)
    return `<p class="hint" style="margin:0;color:var(--brass)">Installation kräver HTTPS.
      Sidan körs över vanlig HTTP, och då erbjuder varken Android eller iPhone att lägga till
      den på hemskärmen.</p>`;

  if (INSTALL.prompt)
    return `<div class="row">
      <button class="btn pri" onclick="doInstall()">Lägg till på hemskärmen</button>
      <span class="hint" style="margin:0">Öppnas i eget fönster, utan adressfält.</span>
    </div>`;

  if (INSTALL.isIos())
    return `<div>
      <p class="lead" style="margin-top:0">På iPhone och iPad finns ingen knapp att trycka på:
        Safari tillåter inte att en webbplats installerar sig själv. Tre steg i stället:</p>
      <ol style="margin:0;padding-left:20px;line-height:1.9">
        <li>Tryck på <strong>Dela</strong> längst ner i Safari (fyrkanten med pilen uppåt)</li>
        <li>Bläddra ner och välj <strong>Lägg till på hemskärmen</strong></li>
        <li>Öppna Borrjournal från hemskärmen, inte från Safari</li>
      </ol>
      <p class="hint">Kräver iOS 16.4 eller senare för notiser. Använder du Chrome på iPhone
        måste du göra det från Safari, det är den enda webbläsaren på iOS som kan installera.</p>
    </div>`;

  return `<p class="hint" style="margin:0">Den här webbläsaren erbjuder ingen installation.
    Prova Chrome eller Edge på Android och dator, eller Safari på iPhone.</p>`;
}

async function doInstall() {
  if (!INSTALL.prompt) return toast("Webbläsaren erbjuder ingen installation just nu", true);
  INSTALL.prompt.prompt();
  const { outcome } = await INSTALL.prompt.userChoice;
  INSTALL.prompt = null;
  const box = document.getElementById("installbox");
  if (box) box.innerHTML = installHtml();
  toast(outcome === "accepted" ? "Installerad" : "Installationen avbröts");
}

/* ---------------- tvåfaktor ---------------- */
async function totpStart(tvingad = false) {
  const box = $("#totpbox");
  try {
    const r = await api("/me/totp/start", { method: "POST" });
    box.innerHTML = `
    <p class="lead" style="margin-top:0">Skanna koden med din autentiseringsapp och skriv in
      de sex siffrorna för att bekräfta. Hoppar du över bekräftelsen slås ingenting på.</p>
    <div class="row" style="align-items:flex-start;gap:20px">
      <img class="laddar qr" data-auth-src="/api/me/totp/qr?t=${Date.now()}"
        alt="QR-kod för tvåfaktor" width="180" height="180">
      <div style="flex:1;min-width:200px">
        <label class="f">Kan du inte skanna? Skriv in nyckeln</label>
        <code style="display:block;font-family:var(--mono);font-size:13px;word-break:break-all;
          background:#F6F8F8;border:1px solid var(--line);padding:8px;border-radius:3px">${esc(r.secret)}</code>
        <label class="f" for="totp_code">Engångskod</label>
        <input id="totp_code" inputmode="numeric" maxlength="6" placeholder="123456" style="max-width:160px">
        <div class="row" style="margin-top:12px">
          <button class="btn pri sm" onclick="totpConfirm(${tvingad})">Bekräfta</button>
          ${tvingad ? "" : `<button class="btn ghost sm" onclick="viewAccount()">Avbryt</button>`}
        </div>
      </div>
    </div>`;
    hydreraBilder(box);
    const el = $("#totp_code");
    el.focus();
    el.onkeydown = (e) => e.key === "Enter" && totpConfirm();
  } catch (e) {
    toast(e.message, true);
  }
}

async function totpConfirm(tvingad = false) {
  try {
    await api("/me/totp/confirm", { method: "POST", body: { code: val("totp_code") } });
    toast("Tvåfaktor påslagen. Nästa inloggning kräver engångskod.");
    if (tvingad) go("oversikt");
    else viewAccount();
  } catch (e) {
    toast(e.message, true);
  }
}

async function totpDisable() {
  if (!confirm("Stänga av tvåfaktor? Kontot skyddas då bara av lösenordet.")) return;
  try {
    await api("/me/totp/disable", { method: "POST", body: { password: val("totp_pw") } });
    toast("Tvåfaktor avstängd");
    viewAccount();
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- adressuppslag ---------------- */
async function lookupAddress() {
  const hint = $("#coordhint");
  const address = [S.form.address, S.form.property_designation].filter(Boolean).join(", ");
  if (!address) return toast("Fyll i adress eller fastighetsbeteckning först", true);
  hint.textContent = "Slår upp adressen…";
  try {
    const r = await api(
      `/geocode?q=${encodeURIComponent(address)}&municipality=${encodeURIComponent(S.form.municipality || "")}`
    );
    S.form.latitude = r.latitude;
    S.form.longitude = r.longitude;
    S.form.coordinates = `${r.latitude}, ${r.longitude}`;
    const field = $("#fld_coordinates");
    if (field) field.value = S.form.coordinates;
    hint.innerHTML = `Hittade <strong>${esc(r.label.split(",").slice(0, 3).join(", "))}</strong>.
      Kontrollera att det stämmer, adressuppslag träffar sällan exakt på borrhålet.`;
  } catch (e) {
    hint.innerHTML = `<span style="color:var(--alert)">${esc(e.message)}</span>`;
  }
}

/* ---------------- redigera och ta bort ---------------- */
function fld(id, label, value, type = "text", extra = "") {
  return `<div><label class="f" for="${id}">${label}</label>
    <input id="${id}" type="${type}" value="${esc(value ?? "")}" ${extra}
      ${type === "number" ? 'step="any" inputmode="decimal"' : ""}></div>`;
}
function sel(id, label, value, options) {
  return `<div><label class="f" for="${id}">${label}</label>
    <select id="${id}">${options
      .map((o) => `<option${String(value) === String(o) ? " selected" : ""}>${esc(o)}</option>`)
      .join("")}</select></div>`;
}
const val = (id) => (document.getElementById(id) || {}).value ?? "";
// Alla webbläsare har inte scrollIntoView med optioner. Ska aldrig fälla en knapp.
const scrollTill = (el) => {
  try {
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (_) {}
};
const numVal = (id) => {
  const v = val(id).trim().replace(",", ".");
  return v === "" ? null : isNaN(parseFloat(v)) ? null : parseFloat(v);
};

function editFacility(facilityId) {
  const f = S.data.customer.facilities.find((x) => x.id === facilityId);
  const box = document.getElementById("fedit-" + facilityId);
  if (!f || !box) return;
  if (box.innerHTML) return (box.innerHTML = "");

  box.innerHTML = `
  <div class="card" style="margin-top:14px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Redigera ${esc(f.facility_no)}</h2></div>
    <div class="pad">
      <fieldset><legend>Brunn</legend><div class="fgrid">
        ${sel("e_type", "Typ", f.facility_type, ["Bergborrad brunn", "Energibrunn", "Grävd brunn", "Filterbrunn"])}
        ${fld("e_drilled", "Borrdatum", f.drilled_at, "date")}
        ${fld("e_soil", "Jorddjup (m)", f.soil_depth_m, "number")}
        ${fld("e_casing", "Foderrör (m)", f.casing_length_m, "number")}
        ${fld("e_depth", "Totalt djup (m)", f.total_depth_m, "number")}
        ${fld("e_water", "Vattennivå (m)", f.water_level_m, "number")}
        ${fld("e_cap", "Kapacitet (l/h)", f.capacity_lph, "number")}
        <div><label class="f" for="e_coord">Koordinater</label>
          <input id="e_coord" value="${esc(f.coordinates || "")}"
            placeholder="59.72, 18.94 eller N 6620123 E 674321">
          <div class="row" style="margin-top:6px">
            <button class="btn ghost sm" type="button" onclick="lookupForFacility()">Hämta från kundens adress</button>
            <span class="hint" id="e_coordhint" style="margin:0"></span>
          </div></div>
      </div></fieldset>

      <fieldset style="margin-top:14px"><legend>Pump</legend><div class="fgrid">
        ${fld("e_pman", "Tillverkare", f.pump_manufacturer)}
        ${fld("e_pmod", "Modell", f.pump_model)}
        ${fld("e_pser", "Serienummer", f.pump_serial)}
        ${fld("e_pdep", "Pumpdjup (m)", f.pump_depth_m, "number")}
        ${fld("e_ptank", "Tryckkärl", f.pressure_tank)}
        ${fld("e_pinst", "Installerad", f.pump_installed_at, "date")}
        ${sel("e_pstat", "Pumpstatus", f.pump_status, ["", "Installerad", "Ska installeras", "Kunden ordnar själv", "Ingen pump (energibrunn)"])}
      </div>
      <p class="hint">Ska pumpen bytas ut mot en ny, använd <strong>Byt pump</strong> i stället.
        Då skrivs den gamla pumpen in i journalen först.</p></fieldset>

      <fieldset style="margin-top:14px"><legend>Service och giltighet</legend><div class="fgrid">
        ${sel("e_int", "Serviceintervall (mån)", f.service_interval_months, [12, 24, 36, 0])}
        ${fld("e_last", "Senaste service", f.last_service_at, "date")}
        ${fld("e_wsamp", "Vattenprov taget", f.water_sample_at, "date")}
        ${sel("e_wval", "Provet giltigt (mån)", f.water_sample_valid_months, [12, 24, 36, 60])}
        ${fld("e_clabel", "Intyg, benämning", f.certificate_label, "text", 'placeholder="T.ex. entreprenörsintyg"')}
        ${fld("e_cexp", "Intyget går ut", f.certificate_expires_at, "date")}
      </div></fieldset>

      <div><label class="f" for="e_bedrock">Berg och lager</label>
        <textarea id="e_bedrock">${esc(f.bedrock_notes || "")}</textarea></div>
      <div><label class="f" for="e_access">Åtkomst</label>
        <textarea id="e_access">${esc(f.access_notes || "")}</textarea></div>

      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="saveFacility('${f.id}')">Spara</button>
        <button class="btn ghost sm" onclick="document.getElementById('fedit-${f.id}').innerHTML=''">Avbryt</button>
      </div>
    </div></div>`;
  scrollTill(box);
}

async function lookupForFacility() {
  const c = S.data.customer;
  const hint = $("#e_coordhint");
  const address = [c.address, c.property_designation].filter(Boolean).join(", ");
  if (!address) return toast("Kunden saknar adress och fastighetsbeteckning", true);
  hint.textContent = "Slår upp…";
  try {
    const r = await api(
      `/geocode?q=${encodeURIComponent(address)}&municipality=${encodeURIComponent(c.municipality || "")}`
    );
    $("#e_coord").value = `${r.latitude}, ${r.longitude}`;
    hint.innerHTML = `Hittade ${esc(r.label.split(",").slice(0, 3).join(", "))}`;
  } catch (e) {
    hint.innerHTML = `<span style="color:var(--alert)">${esc(e.message)}</span>`;
  }
}

async function saveFacility(facilityId) {
  const body = {
    facility_type: val("e_type"),
    drilled_at: val("e_drilled"),
    soil_depth_m: numVal("e_soil"),
    casing_length_m: numVal("e_casing"),
    total_depth_m: numVal("e_depth"),
    water_level_m: numVal("e_water"),
    capacity_lph: numVal("e_cap"),
    coordinates: val("e_coord"),
    pump_manufacturer: val("e_pman").trim(),
    pump_model: val("e_pmod").trim(),
    pump_serial: val("e_pser").trim(),
    pump_depth_m: numVal("e_pdep"),
    pressure_tank: val("e_ptank"),
    pump_installed_at: val("e_pinst"),
    pump_status: val("e_pstat"),
    service_interval_months: parseInt(val("e_int"), 10) || 0,
    last_service_at: val("e_last"),
    water_sample_at: val("e_wsamp"),
    water_sample_valid_months: parseInt(val("e_wval"), 10) || 36,
    certificate_label: val("e_clabel"),
    certificate_expires_at: val("e_cexp"),
    bedrock_notes: val("e_bedrock"),
    access_notes: val("e_access"),
  };
  try {
    await api(`/facilities/${facilityId}`, { method: "PATCH", body });
    await api("/reminders/scan", { method: "POST" });
    toast("Anläggningen sparad");
    viewCustomer();
  } catch (e) {
    toast(e.message, true);
  }
}

function pumpChange(facilityId) {
  const f = S.data.customer.facilities.find((x) => x.id === facilityId);
  const box = document.getElementById("fedit-" + facilityId);
  if (!f || !box) return;
  const gammal = [f.pump_manufacturer, f.pump_model].filter(Boolean).join(" ") || "ingen pump";
  box.innerHTML = `
  <div class="card" style="margin-top:14px;border-color:#E3CE9E">
    <div class="hd" style="background:#FBF5E8"><h2>Byt pump på ${esc(f.facility_no)}</h2></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">Nuvarande: <strong>${esc(gammal)}</strong>${
        f.pump_serial ? ` (serienr ${esc(f.pump_serial)})` : ""
      }. Den skrivs in i journalen innan den nya ersätter den, så historiken finns kvar.</p>
      <div class="fgrid">
        ${fld("np_man", "Tillverkare", f.pump_manufacturer, "text", 'placeholder="Grundfos"')}
        ${fld("np_mod", "Modell", "", "text", 'placeholder="SQ 2-70"')}
        ${fld("np_ser", "Serienummer", "")}
        ${fld("np_dep", "Pumpdjup (m)", f.pump_depth_m, "number")}
        ${fld("np_tank", "Tryckkärl", f.pressure_tank)}
        ${fld("np_date", "Installerad", new Date().toISOString().slice(0, 10), "date")}
      </div>
      <label class="f" for="np_note">Anteckning till journalen</label>
      <textarea id="np_note" placeholder="Varför byttes pumpen, vad noterades vid uppdragning."></textarea>
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="savePumpChange('${f.id}')">Registrera pumpbyte</button>
        <button class="btn ghost sm" onclick="document.getElementById('fedit-${f.id}').innerHTML=''">Avbryt</button>
      </div>
    </div></div>`;
  scrollTill(box);
}

async function savePumpChange(facilityId) {
  if (!val("np_mod").trim()) return toast("Fyll i modell på den nya pumpen", true);
  try {
    await api(`/facilities/${facilityId}/pump-change`, {
      method: "POST",
      body: {
        pump_manufacturer: val("np_man"),
        pump_model: val("np_mod"),
        pump_serial: val("np_ser"),
        pump_depth_m: numVal("np_dep"),
        pressure_tank: val("np_tank"),
        pump_installed_at: val("np_date"),
        pump_status: "Installerad",
        note: val("np_note"),
      },
    });
    toast("Pumpbytet registrerat och journalfört");
    viewCustomer();
  } catch (e) {
    toast(e.message, true);
  }
}

async function removeFacility(facilityId, label) {
  if (
    !confirm(
      `Ta bort ${label}?\n\nJournalanteckningarna och filerna finns kvar på kunden, men lossas från anläggningen. Påminnelser för den försvinner.`
    )
  )
    return;
  try {
    await api(`/facilities/${facilityId}`, { method: "DELETE" });
    toast(`${label} borttagen`);
    viewCustomer();
  } catch (e) {
    toast(e.message, true);
  }
}

function facilityBriefing(facilityId) {
  const box = document.getElementById("fedit-" + facilityId);
  if (!box) return;
  if (box.dataset.mode === "sgu") {
    box.innerHTML = "";
    box.dataset.mode = "";
    return;
  }
  box.dataset.mode = "sgu";
  box.innerHTML = `<div class="card" style="margin-top:14px">
    <div class="hd"><h2>Grannbrunnar enligt SGU</h2><span class="tag n">SGU</span></div>
    <div class="pad" id="briefing"><div class="skel"></div><div class="skel"></div></div></div>`;
  loadBriefing({ facility_id: facilityId });
}

function editCustomer() {
  const c = S.data.customer;
  const box = document.getElementById("cedit");
  if (!box) return;
  if (box.innerHTML) return (box.innerHTML = "");
  box.innerHTML = `
  <div class="card" style="margin-top:14px;border-color:#C9DFE3">
    <div class="hd" style="background:#F4F9FA"><h2>Redigera ${esc(c.customer_no)}</h2></div>
    <div class="pad">
      <div class="fgrid">
        ${fld("c_name", "Namn", c.name)}
        ${sel("c_type", "Kundtyp", c.customer_type, ["Privat", "Företag", "Förening", "Kommun"])}
        ${fld("c_phone", "Telefon", c.phone, "tel")}
        ${fld("c_email", "E-post", c.email, "email")}
        ${fld("c_org", "Person- eller org.nr", c.org_no)}
        ${fld("c_prop", "Fastighetsbeteckning", c.property_designation)}
        ${fld("c_addr", "Adress", c.address)}
        ${fld("c_mun", "Kommun", c.municipality)}
        ${fld("c_inv", "Fakturaadress", c.invoice_address)}
      </div>
      <label class="f" for="c_notes">Anteckningar om kunden</label>
      <textarea id="c_notes">${esc(c.notes || "")}</textarea>
      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" onclick="saveCustomer()">Spara</button>
        <button class="btn ghost sm" onclick="document.getElementById('cedit').innerHTML=''">Avbryt</button>
        ${
          S.user.role === "admin"
            ? `<button class="btn danger sm" style="margin-left:auto" onclick="removeCustomer()">Ta bort kunden helt</button>`
            : ""
        }
      </div>
    </div></div>`;
}

async function _saveCustomer() {
  if (!val("c_name").trim()) return toast("Kunden behöver ett namn", true);
  try {
    await api(`/customers/${S.data.customer.id}`, {
      method: "PATCH",
      body: {
        name: val("c_name").trim(),
        customer_type: val("c_type"),
        phone: val("c_phone"),
        email: val("c_email"),
        org_no: val("c_org"),
        property_designation: val("c_prop"),
        address: val("c_addr"),
        municipality: val("c_mun"),
        invoice_address: val("c_inv"),
        notes: val("c_notes"),
      },
    });
    toast("Kunden sparad");
    viewCustomer();
  } catch (e) {
    toast(e.message, true);
  }
}

async function removeCustomer() {
  const c = S.data.customer;
  const svar = prompt(
    `Detta raderar ${c.name} med alla anläggningar, journalanteckningar, filer och bilder. Det går inte att ångra.\n\nSkriv kundnumret ${c.customer_no} för att bekräfta:`
  );
  if (svar === null) return;
  if (svar.trim().toUpperCase() !== c.customer_no.toUpperCase()) return toast("Fel kundnummer, inget raderat", true);
  try {
    await api(`/customers/${c.id}`, { method: "DELETE" });
    toast(`${c.name} raderad`);
    S.data.customers = null;
    go("kunder");
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- filer ---------------- */
function wireUploads() {
  const kamera = $("#kamera");
  if (kamera) kamera.onchange = () => uploadFiles([...kamera.files]);
  const input = $("#fin");
  const drop = $("#drop");
  if (input) input.onchange = () => uploadFiles([...input.files]);
  if (drop) {
    drop.ondragover = (e) => {
      e.preventDefault();
      drop.classList.add("hot");
    };
    drop.ondragleave = () => drop.classList.remove("hot");
    drop.ondrop = (e) => {
      e.preventDefault();
      drop.classList.remove("hot");
      uploadFiles([...e.dataTransfer.files]);
    };
  }
  const save = $("#jsave");
  if (save) save.onclick = saveJournal;
}

async function uploadFiles(files) {
  if (!files.length) return;
  const prog = $("#prog");
  const bar = prog?.querySelector("i");
  if (prog) prog.hidden = false;
  let done = 0;
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      await api(`/customers/${S.data.customer.id}/files`, { method: "POST", body: fd });
    } catch (e) {
      toast(`${file.name}: ${e.message}`, true);
    }
    done++;
    if (bar) bar.style.width = `${(done / files.length) * 100}%`;
  }
  toast(done > 1 ? `${done} filer uppladdade` : "Filen uppladdad");
  S.data.files = await api(`/customers/${S.data.customer.id}/files`);
  renderTab();
}

async function openFile(id) {
  // Filerna kräver token, så de hämtas som blob och öppnas lokalt.
  const res = await fetch(`/api/files/${id}`, { headers: { Authorization: `Bearer ${S.token}` } });
  if (!res.ok) return toast("Filen kunde inte öppnas", true);
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function deleteFile(id) {
  if (!confirm("Ta bort filen permanent?")) return;
  await api(`/files/${id}`, { method: "DELETE" });
  S.data.files = await api(`/customers/${S.data.customer.id}/files`);
  renderTab();
  toast("Filen borttagen");
}

/* ---------------- registrera ny anläggning ---------------- */
const STEPS = [
  ["Kund", "Kunduppgifter"],
  ["Fastighet", "Fastighet och plats"],
  ["Borrning", "Borrning och berg"],
  ["Pump", "Pump och installation"],
  ["Granska", "Granska och skapa"],
];

function startFacilityFor(customerId) {
  S.form = { existing_customer_id: customerId };
  S.step = 1;
  go("ny");
}

function fInput(key, label, type = "text", ph = "", hint = "") {
  const v = S.form[key] ?? "";
  return `<div><label class="f" for="fld_${key}">${label}</label>
    <input id="fld_${key}" type="${type}" placeholder="${esc(ph)}" value="${esc(v)}"
      ${type === "number" ? 'step="any" inputmode="decimal"' : ""}
      oninput="S.form['${key}']=this.value">${hint ? `<div class="hint">${esc(hint)}</div>` : ""}</div>`;
}
function fSelect(key, label, options) {
  return `<div><label class="f" for="fld_${key}">${label}</label>
    <select id="fld_${key}" onchange="S.form['${key}']=this.value">
      ${options.map((o) => `<option${S.form[key] === o ? " selected" : ""}>${esc(o)}</option>`).join("")}
    </select></div>`;
}
function fArea(key, label, ph) {
  return `<div><label class="f" for="fld_${key}">${label}</label>
    <textarea id="fld_${key}" placeholder="${esc(ph)}" oninput="S.form['${key}']=this.value">${esc(S.form[key] ?? "")}</textarea></div>`;
}

async function viewNewFacility() {
  const s = S.step;
  const existing = S.form.existing_customer_id;
  let customerName = "";
  if (existing) {
    if (!S.data.customers) S.data.customers = await api("/customers");
    customerName = S.data.customers.find((c) => c.id === existing)?.name || "befintlig kund";
  }

  const stepBodies = [
    `<fieldset><legend>Kunduppgifter</legend>
      ${
        existing
          ? `<p class="lead">Anläggningen läggs på <strong>${esc(customerName)}</strong>.
             <button class="btn ghost sm" style="margin-left:8px" onclick="delete S.form.existing_customer_id;viewNewFacility()">Ny kund istället</button></p>`
          : `<div class="fgrid">
        ${fSelect("customer_type", "Kundtyp", ["Privat", "Företag", "Förening", "Kommun"])}
        ${fInput("name", "Namn eller företag", "text", "Erik & Maja Lundqvist")}
        ${fInput("phone", "Telefon", "tel", "070-000 00 00")}
        ${fInput("email", "E-post", "email", "namn@example.se")}
        ${fInput("org_no", "Person- eller org.nr", "text", "", "Används på borrprotokoll och faktura")}
        ${fInput("invoice_address", "Fakturaadress", "text", "Gata, postnr, ort")}
      </div>`
      }</fieldset>`,
    `<fieldset><legend>Fastighet och plats</legend>
      <div class="fgrid">
        ${fInput("property_designation", "Fastighetsbeteckning", "text", "Vässlan 3:14")}
        ${fInput("municipality", "Kommun", "text", "Norrtälje")}
        ${fInput("address", "Adress till borrplatsen", "text", "Vässlanvägen 12")}
        <div><label class="f" for="fld_coordinates">Koordinater</label>
          <input id="fld_coordinates" type="text" placeholder="59.7231, 18.9412 eller N 6620123 E 674321"
            value="${esc(S.form.coordinates ?? "")}" oninput="S.form['coordinates']=this.value;checkCoord(this.value)">
          <div class="row" style="margin-top:6px">
            <button class="btn ghost sm" type="button" onclick="lookupAddress()">Hämta från adressen</button>
            <button class="btn ghost sm" type="button" onclick="fillPosition()">Hämta min position</button>
            <span class="hint" id="coordhint" style="margin:0">Decimalgrader eller SWEREF 99 TM, båda fungerar.</span>
          </div></div>
        ${fSelect("permit_status", "Anmälan till kommunen", ["Inte påbörjad", "Inskickad", "Beviljad", "Krävs inte"])}
      </div>
      ${fArea("access_notes", "Åtkomst och förutsättningar", "Framkomlighet för rigg, lutning, elskåp, var slam får läggas.")}
    </fieldset>`,
    `<fieldset><legend>Borrning och berg</legend>
      <div class="fgrid">
        ${fSelect("facility_type", "Typ av anläggning", ["Bergborrad brunn", "Energibrunn", "Grävd brunn", "Filterbrunn"])}
        ${fInput("drilled_at", "Borrdatum", "date")}
        ${fInput("soil_depth_m", "Jorddjup (m)", "number", "6")}
        ${fInput("casing_length_m", "Foderrör, längd (m)", "number", "6,5")}
        ${fInput("total_depth_m", "Totalt borrdjup (m)", "number", "72")}
        ${fInput("water_level_m", "Vattennivå från markyta (m)", "number", "14")}
        ${fInput("capacity_lph", "Uppmätt kapacitet (l/h)", "number", "1400")}
        ${fSelect("water_sample", "Vattenprov", ["Ej taget", "Skickat till labb", "Godkänt", "Anmärkning"])}
      </div>
      ${fArea("bedrock_notes", "Bergarter och vattenförande sprickor", "Morän 0-6 m, granit 6-72 m, spricka vid 47 m med god tillrinning.")}
    </fieldset>`,
    `<fieldset><legend>Pump och installation</legend>
      <div class="fgrid">
        ${fSelect("pump_status", "Pumpstatus", ["Ska installeras", "Installerad", "Kunden ordnar själv", "Ingen pump (energibrunn)"])}
        ${fInput("pump_manufacturer", "Tillverkare", "text", "Grundfos", "Egen kolumn, så flottan kan filtreras vid fabriksfel")}
        ${fInput("pump_model", "Modell", "text", "SQ 2-70")}
        ${fInput("pump_serial", "Serienummer", "text", "GF-2026-00123")}
        ${fInput("pump_depth_m", "Pumpens nivå (m)", "number", "45")}
        ${fInput("pressure_tank", "Hydrofor eller tryckkärl", "text", "Wilo 60 l")}
        ${fInput("pump_installed_at", "Driftsatt", "date")}
        ${fInput("last_service_at", "Senaste service", "date", "", "Lämna tom om ingen service gjorts")}
        ${fSelect("service_interval_months", "Serviceintervall", ["12", "24", "36", "0"])}
      </div></fieldset>`,
    `<fieldset><legend>Granska och skapa</legend>
      <p class="lead" style="margin-bottom:14px">Servern skapar ${existing ? "en anläggning" : "ett kundkort, en anläggning"} och den första journalanteckningen med tidsstämpel.</p>
      <table class="sumtable">
        <tr><td>Kund</td><td>${esc(existing ? customerName : S.form.name || "—")}</td></tr>
        <tr><td>Kontakt</td><td>${esc(S.form.phone || "—")} · ${esc(S.form.email || "—")}</td></tr>
        <tr><td>Fastighet</td><td>${esc(S.form.property_designation || "—")}, ${esc(S.form.municipality || "—")}</td></tr>
        <tr><td>Typ</td><td>${esc(S.form.facility_type || "Bergborrad brunn")}</td></tr>
        <tr><td>Djup / vattennivå</td><td>${esc(S.form.total_depth_m || "—")} m / ${esc(S.form.water_level_m || "—")} m</td></tr>
        <tr><td>Foderrör / jorddjup</td><td>${esc(S.form.casing_length_m || "—")} m / ${esc(S.form.soil_depth_m || "—")} m</td></tr>
        <tr><td>Kapacitet</td><td>${esc(S.form.capacity_lph || "—")} l/h</td></tr>
        <tr><td>Pump</td><td>${esc([S.form.pump_manufacturer, S.form.pump_model].filter(Boolean).join(" ") || "—")} · ${esc(S.form.pump_status || "—")}</td></tr>
        <tr><td>Serienummer</td><td>${esc(S.form.pump_serial || "—")}</td></tr>
      </table>
      ${fArea("first_note", "Första journalanteckningen", "Vad gjordes vid borrningen, vad återstår.")}
    </fieldset>`,
  ];

  mountShell(`
  <div class="spread">
    <div><div class="eyebrow">Registrering · steg ${s + 1} av 5</div><h1>Ny brunn eller pump</h1></div>
    <button class="btn ghost sm" onclick="S.form={};S.step=0;go('oversikt')">Avbryt</button>
  </div>
  <div class="steps">${STEPS.map(
    (st, i) => `<button class="step ${i === s ? "on" : i < s ? "done" : ""}" onclick="S.step=${i};viewNewFacility()">
      <span class="n">${i < s ? "✓" : i + 1}</span><span class="tx">${st[0]}</span></button>`
  ).join("")}</div>
  <div class="card"><div class="pad">${stepBodies[s]}
    <div class="wfoot">
      <button class="btn ghost" ${s === 0 ? "disabled" : ""} onclick="S.step=${Math.max(0, s - 1)};viewNewFacility()">← Föregående</button>
      ${
        s < 4
          ? `<button class="btn pri" onclick="S.step=${s + 1};viewNewFacility()">Nästa: ${STEPS[s + 1][1]} →</button>`
          : `<button class="btn pri" id="obsave">Skapa ${existing ? "anläggning" : "kund och anläggning"}</button>`
      }
    </div></div></div>`);
  const save = $("#obsave");
  if (save) save.onclick = submitNewFacility;
}

let coordTimer;
function checkCoord(value) {
  clearTimeout(coordTimer);
  coordTimer = setTimeout(async () => {
    const hint = $("#coordhint");
    if (!hint) return;
    if (!value.trim()) {
      hint.textContent = "Decimalgrader eller SWEREF 99 TM, båda fungerar.";
      delete S.form.latitude;
      delete S.form.longitude;
      return;
    }
    const r = await api(`/coordinates/parse?q=${encodeURIComponent(value)}`);
    if (r.ok) {
      S.form.latitude = r.latitude;
      S.form.longitude = r.longitude;
      hint.innerHTML = `Tolkat som <strong>${r.latitude}, ${r.longitude}</strong>`;
    } else {
      delete S.form.latitude;
      delete S.form.longitude;
      hint.innerHTML = `<span style="color:var(--alert)">Kunde inte tolka koordinaten ännu</span>`;
    }
  }, 400);
}

async function fillPosition() {
  const hint = $("#coordhint");
  hint.textContent = "Hämtar position…";
  try {
    const pos = await GEO.get();
    S.form.coordinates = `${pos.lat.toFixed(6)}, ${pos.lon.toFixed(6)}`;
    S.form.latitude = pos.lat;
    S.form.longitude = pos.lon;
    $("#fld_coordinates").value = S.form.coordinates;
    hint.textContent = `Position hämtad, noggrannhet ±${Math.round(pos.acc)} m.`;
  } catch (e) {
    hint.innerHTML = `<span style="color:var(--alert)">${esc(e.message)}</span>`;
  }
}

function numOrNull(v) {
  if (v === undefined || v === null || String(v).trim() === "") return null;
  const n = parseFloat(String(v).replace(",", "."));
  return isNaN(n) ? null : n;
}

async function submitNewFacility() {
  const f = S.form;
  if (!f.existing_customer_id && !(f.name || "").trim()) {
    toast("Kunden behöver ett namn", true);
    S.step = 0;
    return viewNewFacility();
  }
  const btn = $("#obsave");
  btn.disabled = true;
  const payload = {
    existing_customer_id: f.existing_customer_id || null,
    customer: {
      name: f.name || "Ny kund",
      customer_type: f.customer_type || "Privat",
      org_no: f.org_no || "",
      phone: f.phone || "",
      email: f.email || "",
      invoice_address: f.invoice_address || "",
      property_designation: f.property_designation || "",
      address: f.address || "",
      municipality: f.municipality || "",
    },
    facility: {
      facility_type: f.facility_type || "Bergborrad brunn",
      drilled_at: f.drilled_at || "",
      coordinates: f.coordinates || "",
      latitude: f.latitude ?? null,
      longitude: f.longitude ?? null,
      access_notes: f.access_notes || "",
      permit_status: f.permit_status || "",
      soil_depth_m: numOrNull(f.soil_depth_m),
      casing_length_m: numOrNull(f.casing_length_m),
      total_depth_m: numOrNull(f.total_depth_m),
      water_level_m: numOrNull(f.water_level_m),
      capacity_lph: numOrNull(f.capacity_lph),
      bedrock_notes: f.bedrock_notes || "",
      water_sample: f.water_sample || "",
      pump_manufacturer: (f.pump_manufacturer || "").trim(),
      pump_model: (f.pump_model || "").trim(),
      pump_serial: (f.pump_serial || "").trim(),
      pump_depth_m: numOrNull(f.pump_depth_m),
      pump_status: f.pump_status || "",
      pressure_tank: f.pressure_tank || "",
      pump_installed_at: f.pump_installed_at || "",
      last_service_at: f.last_service_at || "",
      service_interval_months: parseInt(f.service_interval_months || "12", 10),
    },
    first_note: f.first_note || "",
  };
  try {
    const res = await api("/new-facility", { method: "POST", body: payload });
    S.form = {};
    S.step = 0;
    S.data.customers = null;
    toast(`${res.facility.facility_no} skapad`);
    go("kund", res.customer.id);
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}


/* ---------------- notiser på enheten ---------------- */
const PUSH = {
  supported: () =>
    "serviceWorker" in navigator && "PushManager" in window && "Notification" in window,
  secure: () => window.isSecureContext === true,
  isIos: () =>
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1),
  installed: () =>
    (typeof window.matchMedia === "function" &&
      window.matchMedia("(display-mode: standalone)").matches) ||
    navigator.standalone === true,

  async ready() {
    if (!("serviceWorker" in navigator)) return null;
    try {
      return await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    } catch (e) {
      console.warn("service worker kunde inte registreras", e);
      return null;
    }
  },

  async state() {
    if (!PUSH.supported()) return "saknas";
    if (Notification.permission === "denied") return "nekad";
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && (await reg.pushManager.getSubscription());
    return sub ? "på" : "av";
  },

  async enable() {
    if (!PUSH.supported()) throw new Error("Enheten stöder inte notiser.");
    if (PUSH.isIos() && !PUSH.installed())
      throw new Error(
        "På iPhone måste appen först läggas till på hemskärmen. Dela-knappen → Lägg till på hemskärmen."
      );
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Notiser nekades i enhetens inställningar.");

    const reg = (await navigator.serviceWorker.getRegistration()) || (await PUSH.ready());
    if (!reg) throw new Error("Service worker saknas.");
    await navigator.serviceWorker.ready;

    const { public_key } = await api("/notifications/push/key");
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: b64ToBytes(public_key),
    });
    await api("/notifications/push/subscribe", { method: "POST", body: sub.toJSON() });
    return true;
  },

  async disable() {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && (await reg.pushManager.getSubscription());
    if (sub) {
      await api("/notifications/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } });
      await sub.unsubscribe();
    }
  },
};

function b64ToBytes(base64) {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

async function togglePush() {
  const state = await PUSH.state();
  try {
    if (state === "på") {
      await PUSH.disable();
      toast("Notiser avstängda på den här enheten");
    } else {
      await PUSH.enable();
      toast("Notiser påslagna på den här enheten");
    }
  } catch (e) {
    toast(e.message, true);
  }
  if (S.route === "paminnelser") viewReminders();
  else if (S.route === "admin") viewAdmin();
}

async function testPush() {
  try {
    await api("/notifications/push/test", { method: "POST" });
    toast("Testnotis skickad");
  } catch (e) {
    toast(e.message, true);
  }
}

async function pushBanner() {
  const state = await PUSH.state();
  if (!PUSH.secure())
    return `<div class="hint" style="color:var(--brass)">Notiser kräver HTTPS. Sidan körs över vanlig HTTP,
      så webbläsaren tillåter varken notiser eller installation på hemskärmen. Allt annat fungerar,
      och påminnelser via e-post går fram som vanligt.</div>`;
  if (state === "saknas")
    return `<div class="hint">Den här webbläsaren stöder inte notiser. E-post fungerar ändå.</div>`;
  if (state === "nekad")
    return `<div class="err">Notiser är blockerade för sidan. Tillåt dem i webbläsarens sidinställningar och ladda om.</div>`;
  if (PUSH.isIos() && !PUSH.installed())
    return `<div class="err">För notiser på iPhone: tryck Dela och välj <strong>Lägg till på hemskärmen</strong>. Öppna appen därifrån, då kan du slå på notiser. (Kräver iOS 16.4 eller senare.)</div>`;
  return `<div class="row" style="margin-bottom:14px">
    <span class="tag ${state === "på" ? "ok" : "n"}">Notiser ${state === "på" ? "på" : "av"} på denna enhet</span>
    <button class="btn ghost sm" onclick="togglePush()">${state === "på" ? "Stäng av" : "Slå på notiser"}</button>
    ${state === "på" ? `<button class="btn ghost sm" onclick="testPush()">Skicka testnotis</button>` : ""}
  </div>`;
}

/* ---------------- påminnelser ---------------- */
const KIND_LABEL = {
  service: "Service",
  vattenprov: "Vattenprov",
  intyg: "Intyg",
  uppfoljning: "Uppföljning",
  egen: "Egen",
};

async function refreshBadge() {
  try {
    const sum = await api("/reminders/summary");
    S.badge = sum.overdue || 0;
    const el = document.querySelector(".nav .badge");
    if (el) el.textContent = S.badge;
  } catch (_) {}
}

function reminderRow(r) {
  const left =
    r.days_left === null
      ? ""
      : r.days_left < 0
        ? `${Math.abs(r.days_left)} dagar sen`
        : r.days_left === 0
          ? "idag"
          : `om ${r.days_left} dagar`;
  return `<div class="filerow">
    <div class="ftype" style="background:${r.overdue ? "#A6402F" : r.days_left <= 7 ? "#B3801F" : "var(--water-dark)"}">
      ${esc(r.due_date.slice(8))}<br>${esc(r.due_date.slice(5, 7))}</div>
    <div style="flex:1;min-width:0">
      <div style="font-weight:600">${esc(r.title)}</div>
      <div class="fmeta">${esc(r.due_date)}${r.due_time ? " " + esc(r.due_time) : ""} · ${esc(left)}${
        r.customer_name ? " · " + esc(r.customer_name) : ""
      }${
        r.assigned_name && !r.mine ? " · " + esc(r.assigned_name) : r.mine ? " · du" : " · ingen ansvarig"
      }${
        r.remind_at && !r.notified_at
          ? " · påminner " + dt(r.remind_at)
          : r.notified_channels
            ? " · meddelat via " + esc(r.notified_channels)
            : ""
      }</div>
      ${r.body ? `<div class="tsub" style="margin-top:3px">${esc(r.body.slice(0, 160))}</div>` : ""}
    </div>
    <span class="tag n">${esc(KIND_LABEL[r.kind] || r.kind)}</span>
    ${
      S.user.role === "lasare"
        ? ""
        : `<div class="row" style="gap:6px">
      ${
        r.status === "open"
          ? `<button class="btn ghost sm" onclick="completeReminder('${r.id}')">Klar</button>
             <button class="btn ghost sm" onclick="omplanera('${r.id}')">Ändra tid</button>
             <button class="btn ghost sm" onclick="snoozeReminder('${r.id}',7)">+7 d</button>`
          : `<button class="btn ghost sm" onclick="reopenReminder('${r.id}')">Öppna igen</button>`
      }
      <button class="btn danger sm" onclick="deleteReminder('${r.id}')">Ta bort</button></div>`
    }
    ${r.customer_id ? `<button class="btn ghost sm" onclick="go('kund','${r.customer_id}')">Kund</button>` : ""}
  </div>`;
}

async function viewReminders() {
  const token = claim();
  mountShell(`<div class="skel" style="width:30%"></div><div class="skel"></div>`);
  const status = S.filter.reminderStatus || "open";
  const scope = S.filter.reminderScope || (S.user.role === "admin" ? "alla" : "mina");
  const [items, sum, customers] = await Promise.all([
    api(`/reminders?status=${status}&scope=${scope}`),
    api("/reminders/summary"),
    S.data.customers ? Promise.resolve(S.data.customers) : api("/customers"),
  ]);
  S.data.customers = customers;
  S.badge = sum.overdue || 0;

  const overdue = items.filter((r) => r.overdue);
  const week = items.filter((r) => !r.overdue && r.days_left !== null && r.days_left <= 7);
  const later = items.filter((r) => !r.overdue && (r.days_left === null || r.days_left > 7));
  const group = (title, rows) =>
    rows.length
      ? `<div class="card" style="margin-bottom:16px"><div class="hd"><h2>${title}</h2><span class="tag n">${rows.length}</span></div>
         <div class="pad" style="padding-top:2px">${rows.map(reminderRow).join("")}</div></div>`
      : "";

  if (!current(token)) return;
  $("#view").innerHTML = `
  <div class="spread">
    <div><div class="eyebrow">Bevakning</div><h1>Påminnelser</h1>
      <p class="lead">Service, vattenprov, intyg, obetalda fakturor, offerter utan besked och
      besök utan återkoppling. ${
        scope === "mina"
          ? "Visar dina egna och sådana ingen tagit ansvar för."
          : "Visar allas."
      }</p></div>
    <div class="row">
      <select style="width:auto" onchange="S.filter.reminderScope=this.value;viewReminders()">
        <option value="mina"${scope === "mina" ? " selected" : ""}>Mina</option>
        <option value="alla"${scope === "alla" ? " selected" : ""}>Allas</option>
      </select>
      <select style="width:auto" onchange="S.filter.reminderStatus=this.value;viewReminders()">
        <option value="open"${status === "open" ? " selected" : ""}>Öppna</option>
        <option value="done"${status === "done" ? " selected" : ""}>Kvitterade</option>
      </select>
      ${S.user.role === "lasare" ? "" : `<button class="btn ghost" onclick="runScan()">Kör genomsökning</button>`}
    </div>
  </div>
  <div id="pushbanner"></div>
  <div class="stats">
    <div class="stat bad"><div class="v">${sum.overdue}</div><div class="l">Försenade</div></div>
    <div class="stat warn"><div class="v">${sum.this_week}</div><div class="l">Inom en vecka</div></div>
    <div class="stat"><div class="v">${sum.next_30_days}</div><div class="l">Inom 30 dagar</div></div>
    <div class="stat"><div class="v">${sum.open}</div><div class="l">Öppna totalt</div></div>
  </div>
  ${
    S.user.role === "lasare" || !customers.some((c) => c.facilities.length)
      ? ""
      : `<div class="card" style="margin-bottom:16px"><div class="hd"><h2>Ny påminnelse</h2></div><div class="pad">
    <div class="fgrid">
      <div><label class="f" for="rt">Rubrik</label><input id="rt" placeholder="Ring om hydroforbyte"></div>
      <div><label class="f" for="rd">Gäller datum</label><input id="rd" type="date"></div>
      <div><label class="f" for="rc">Gäller anläggning</label><select id="rc">
        ${customers
          .flatMap((c) =>
            c.facilities.map(
              (f) =>
                `<option value="${f.id}">${esc(c.name)} · ${esc(f.facility_no)} ${esc(f.facility_type)}</option>`
            )
          )
          .join("")}</select></div>
      <div><label class="f" for="rpd">Påminn den</label>
        <input id="rpd" type="date">
        <div class="hint">Lämna tom så påminner den på förfallodagen.</div></div>
      <div><label class="f" for="rpt">Klockan</label>
        <input id="rpt" type="time" value="07:30">
        <div class="hint">Din lokala tid.</div></div>
    </div>
    <label class="f" for="rb">Anteckning</label><textarea id="rb" style="min-height:60px"></textarea>
    <button class="btn pri sm" style="margin-top:12px" id="rsave">Spara påminnelse</button>
  </div></div>`
  }
  ${group("Försenade", overdue)}
  ${group("Inom en vecka", week)}
  ${group(status === "done" ? "Kvitterade" : "Längre fram", later)}
  ${items.length ? "" : `<div class="card"><div class="empty"><div class="big">Inga påminnelser här</div><p>Automatiska påminnelser dyker upp när en anläggning har datum för service, vattenprov eller intyg.</p></div></div>`}`;

  const banner = $("#pushbanner");
  if (banner) banner.innerHTML = await pushBanner();
  const save = $("#rsave");
  if (save) save.onclick = createReminder;
}

async function createReminder() {
  const title = $("#rt").value.trim();
  const due = $("#rd").value;
  const facility = $("#rc").value;
  if (!title || !due) return toast("Rubrik och datum behövs", true);
  if (!facility) return toast("Välj vilken anläggning påminnelsen gäller", true);

  // Klienten räknar om till UTC, så vald tid gäller där användaren står.
  const paminnDag = val("rpd") || due;
  const paminnTid = val("rpt") || "07:30";
  const remindAt = new Date(`${paminnDag}T${paminnTid}`);
  if (isNaN(remindAt)) return toast("Ogiltig tidpunkt för påminnelsen", true);
  try {
    await api("/reminders", {
      method: "POST",
      body: {
        title,
        due_date: due,
        due_time: paminnDag === due ? paminnTid : "",
        remind_at: remindAt.toISOString(),
        body: $("#rb").value,
        facility_id: facility,
        kind: "egen",
      },
    });
    toast("Påminnelse sparad");
    viewReminders();
  } catch (e) {
    toast(e.message, true);
  }
}

async function omplanera(id) {
  const dag = prompt("Vilket datum ska påminnelsen gå ut? (ÅÅÅÅ-MM-DD)", new Date().toISOString().slice(0, 10));
  if (dag === null) return;
  const tid = prompt("Vilken tid? (TT:MM)", "07:30");
  if (tid === null) return;
  const nar = new Date(`${dag}T${tid}`);
  if (isNaN(nar)) return toast("Kunde inte tolka datum eller tid", true);
  try {
    await api(`/reminders/${id}`, { method: "PATCH", body: { remind_at: nar.toISOString() } });
    toast(`Påminner ${dt(nar.toISOString())}`);
    await afterReminderChange();
  } catch (e) {
    toast(e.message, true);
  }
}

async function completeReminder(id) {
  await api(`/reminders/${id}`, { method: "PATCH", body: { done: true } });
  toast("Kvitterad");
  await afterReminderChange();
}
async function reopenReminder(id) {
  await api(`/reminders/${id}`, { method: "PATCH", body: { done: false } });
  await afterReminderChange();
}
async function snoozeReminder(id, days) {
  await api(`/reminders/${id}`, { method: "PATCH", body: { snooze_days: days } });
  toast(`Flyttad ${days} dagar framåt`);
  await afterReminderChange();
}
async function deleteReminder(id) {
  if (!confirm("Ta bort påminnelsen?")) return;
  await api(`/reminders/${id}`, { method: "DELETE" });
  await afterReminderChange();
}
async function afterReminderChange() {
  if (S.route === "kund") {
    S.data.reminders = await api(`/reminders?status=open&customer_id=${S.data.customer.id}`);
    renderTab();
    refreshBadge();
  } else {
    viewReminders();
  }
}
async function runScan() {
  const r = await api("/reminders/scan", { method: "POST" });
  toast(r.created ? `${r.created} nya påminnelser skapades` : "Inget nytt att skapa");
  viewReminders();
}

function tabReminders(c, items) {
  return `
  ${
    items.length
      ? items.map(reminderRow).join("")
      : `<div class="empty"><div class="big">Inga öppna påminnelser</div>
         <p>Sätt ett datum för service, vattenprov eller intyg på anläggningen, då skapas de automatiskt.</p></div>`
  }
  ${
    S.user.role === "lasare"
      ? ""
      : `<div class="row" style="margin-top:14px">
    <button class="btn ghost sm" onclick="go('paminnelser')">Alla påminnelser</button>
    <button class="btn ghost sm" onclick="runScanFor('${c.id}')">Uppdatera automatiska</button></div>`
  }`;
}
async function runScanFor(customerId) {
  await api("/reminders/scan", { method: "POST" });
  S.data.reminders = await api(`/reminders?status=open&customer_id=${customerId}`);
  renderTab();
  toast("Automatiska påminnelser uppdaterade");
}


/* ---------------- jobb i närheten ---------------- */
const GEO = {
  supported: () => "geolocation" in navigator,
  // Platstjänst, service workers och notiser kräver säker kontext: HTTPS eller localhost.
  secure: () => window.isSecureContext === true,
  get: () =>
    new Promise((resolve, reject) => {
      if (!GEO.secure())
        return reject(
          new Error(
            "Platstjänster kräver HTTPS. Sidan körs över vanlig HTTP, och då blockerar " +
              "webbläsaren positionen. Sätt ett certifikat i din reverse proxy, eller " +
              "skriv in koordinaten för hand så länge."
          )
        );
      if (!GEO.supported()) return reject(new Error("Enheten har ingen platstjänst."));
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude, acc: pos.coords.accuracy }),
        (err) => {
          const texts = {
            1: "Platsåtkomst nekad. Tillåt plats för sidan i webbläsarens inställningar.",
            2: "Positionen kunde inte bestämmas. Prova igen utomhus.",
            3: "Tog för lång tid att hitta positionen.",
          };
          reject(new Error(texts[err.code] || "Kunde inte hämta positionen."));
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
      );
    }),
};

function mapLink(lat, lon) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
}

function routeLink(origin, stops) {
  const dest = stops[stops.length - 1];
  const waypoints = stops.slice(0, -1).map((s) => `${s.latitude},${s.longitude}`).join("|");
  let url = `https://www.google.com/maps/dir/?api=1&destination=${dest.latitude},${dest.longitude}`;
  if (origin) url += `&origin=${origin.latitude || origin.lat},${origin.longitude || origin.lon}`;
  if (waypoints) url += `&waypoints=${encodeURIComponent(waypoints)}`;
  return url;
}

function nearbyRow(h, selectable = true) {
  const prio = h.priority === 3 ? "action" : h.priority === 2 ? "soon" : "n";
  const arBesok = h.typ === "besok";
  return `<div class="filerow">
    ${
      selectable
        ? `<input type="checkbox" class="stop" style="width:auto;flex:0 0 auto" data-lat="${h.latitude}" data-lon="${h.longitude}"
             ${h.priority > 0 ? "checked" : ""} aria-label="Ta med ${esc(h.customer_name)} i rundan">`
        : ""
    }
    <div class="ftype" style="background:${
      arBesok
        ? "#155F6E"
        : h.priority === 3
          ? "#A6402F"
          : h.priority === 2
            ? "#B3801F"
            : "var(--stone)"
    }">
      ${h.distance_km < 10 ? h.distance_km.toFixed(1) : Math.round(h.distance_km)}<br>km</div>
    <div style="flex:1;min-width:0">
      <div style="font-weight:600">${esc(h.customer_name)}
        ${arBesok ? `<span class="tag n" style="margin-left:6px">Besök</span>` : ""}</div>
      <div class="fmeta">${esc(arBesok ? h.visit_no : h.facility_no)} · ${esc(h.bearing)} ·
        ${esc(h.property_designation || "")} ${esc(h.municipality || "")}</div>
      <div class="tsub" style="margin-top:2px"><span class="tag ${prio}" style="margin-right:6px">${esc(h.reason)}</span>
        ${arBesok && h.errand ? esc(h.errand) : ""}</div>
    </div>
    <div class="row" style="gap:6px">
      ${h.phone ? `<a class="btn ghost sm" href="tel:${esc(h.phone)}">Ring</a>` : ""}
      <a class="btn ghost sm" href="${mapLink(h.latitude, h.longitude)}" target="_blank" rel="noopener">Karta</a>
      <button class="btn ghost sm" onclick="${
        arBesok ? `go('besok','${h.visit_id}')` : `go('kund','${h.customer_id}')`
      }">Öppna</button>
    </div>
  </div>`;
}

async function viewNearby() {
  const token = claim();
  const radius = S.filter.radius || 25;
  const onlyJobs = S.filter.onlyJobs !== false;
  mountShell(`
  <div class="spread">
    <div><div class="eyebrow">Planering</div><h1>Jobb i närheten</h1>
      <p class="lead">Hämta din position, eller klistra in en koordinat. Anläggningar som behöver
      något sorteras först, därefter efter avstånd – en försenad service två mil bort är oftast
      mer värd en avstickare än en fungerande brunn på samma gata.</p></div>
  </div>
  <div class="card" style="margin-bottom:16px"><div class="pad">
    <div class="row">
      <button class="btn pri" id="gps">Använd min position</button>
      <span class="hint" style="margin:0">eller</span>
      <input id="coord" placeholder="59.7231, 18.9412 eller N 6620123 E 674321" style="flex:1;min-width:220px">
      <button class="btn ghost" id="usecoord">Sök här</button>
    </div>
    <div class="fgrid" style="margin-top:10px">
      <div><label class="f" for="rad">Radie</label>
        <select id="rad" onchange="S.filter.radius=parseFloat(this.value);runNearby()">
          ${[5, 10, 25, 50, 100].map((r) => `<option value="${r}"${radius === r ? " selected" : ""}>${r} km</option>`).join("")}
        </select></div>
      <div><label class="f" for="oj">Visa</label>
        <select id="oj" onchange="S.filter.onlyJobs=this.value==='1';runNearby()">
          <option value="1"${onlyJobs ? " selected" : ""}>Bara sådant som behöver något</option>
          <option value="0"${!onlyJobs ? " selected" : ""}>Alla anläggningar</option>
        </select></div>
      <div><label class="f" for="ib">Inbokade besök</label>
        <select id="ib" onchange="S.filter.medBesok=this.value==='1';runNearby()">
          <option value="1"${S.filter.medBesok !== false ? " selected" : ""}>Ta med</option>
          <option value="0"${S.filter.medBesok === false ? " selected" : ""}>Bara anläggningar</option>
        </select></div>
    </div>
    <div id="geohint" class="hint">${
      GEO.secure()
        ? ""
        : `<span style="color:var(--brass)">Sidan körs över HTTP, så webbläsaren blockerar platstjänster.
           Klistra in en koordinat nedan, eller sätt upp HTTPS i din reverse proxy.</span>`
    }</div>
  </div></div>
  <div id="nearres"></div>`);

  $("#gps").onclick = async () => {
    const btn = $("#gps");
    btn.disabled = true;
    btn.textContent = "Hämtar position…";
    try {
      const pos = await GEO.get();
      S.origin = { latitude: pos.lat, longitude: pos.lon };
      $("#geohint").textContent = `Position hämtad, noggrannhet ±${Math.round(pos.acc)} m.`;
      await runNearby();
    } catch (e) {
      $("#geohint").innerHTML = `<span style="color:var(--alert)">${esc(e.message)}</span>`;
    }
    btn.disabled = false;
    btn.textContent = "Använd min position";
  };
  $("#usecoord").onclick = async () => {
    const q = $("#coord").value.trim();
    if (!q) return;
    const r = await api(`/coordinates/parse?q=${encodeURIComponent(q)}`);
    if (!r.ok) return ($("#geohint").innerHTML = `<span style="color:var(--alert)">Kunde inte tolka koordinaten.</span>`);
    S.origin = { latitude: r.latitude, longitude: r.longitude };
    $("#geohint").textContent = `Tolkat som ${r.latitude}, ${r.longitude}.`;
    await runNearby();
  };

  if (S.origin) await runNearby();
  else
    $("#nearres").innerHTML = `<div class="card"><div class="empty">
      <div class="big">Ingen position vald</div>
      <p>Tryck på Använd min position, eller klistra in en koordinat från borrprotokollet.</p></div></div>`;
}

async function runNearby() {
  const token = claim();
  if (!S.origin) return;
  const box = $("#nearres");
  if (box) box.innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  const radius = S.filter.radius || 25;
  const onlyJobs = S.filter.onlyJobs !== false;
  const r = await api(
    `/nearby?lat=${S.origin.latitude}&lon=${S.origin.longitude}&radius_km=${radius}` +
      `&only_jobs=${onlyJobs}&include_visits=${S.filter.medBesok !== false}`
  );
  S.data.nearby = r.results;
  if (!current(token) || !$("#nearres")) return;
  box.innerHTML = `
  <div class="card"><div class="hd"><h2>${r.results.length} inom ${radius} km</h2>
    <span class="tag n">${r.with_coordinates} anläggningar har koordinater</span>
    ${r.results.length ? `<button class="btn sm" style="margin-left:auto" onclick="openRoute()">Öppna rundan i kartan</button>` : ""}</div>
    <div class="pad" style="padding-top:2px">
      ${
        r.results.length
          ? r.results.map((h) => nearbyRow(h)).join("") +
            `<p class="hint" style="margin-top:12px">Bocka ur det du inte ska åka till, tryck sedan
             Öppna rundan i kartan. Stoppen läggs in i ordning med din position som start.</p>`
          : `<div class="empty"><div class="big">Inget hittat inom ${radius} km</div>
             <p>Öka radien, eller visa alla anläggningar i stället för bara sådana som behöver något.</p></div>`
      }
    </div></div>`;
}

function openRoute() {
  const stops = [...document.querySelectorAll(".stop:checked")].map((el) => ({
    latitude: el.dataset.lat,
    longitude: el.dataset.lon,
  }));
  if (!stops.length) return toast("Bocka i minst ett stopp", true);
  if (stops.length > 10) return toast("Kartan klarar högst 10 stopp åt gången", true);
  window.open(routeLink(S.origin, stops), "_blank", "noopener");
}

async function nearbyCard(facility) {
  if (!facility) return "";
  const r = await api(`/facilities/${facility.id}/nearby?radius_km=30&only_jobs=true&limit=6`);
  if (r.missing_coordinates)
    return `<div class="card" style="margin-top:18px"><div class="hd"><h2>Jobb i närheten</h2></div>
      <div class="pad"><p class="hint" style="margin:0">${esc(r.hint)}</p></div></div>`;
  if (!r.results.length) return "";
  return `<div class="card" style="margin-top:18px">
    <div class="hd"><h2>Slå ihop med resan</h2><span class="tag n">${r.results.length} inom 30 km</span></div>
    <div class="pad" style="padding-top:2px">
      ${r.results.map((h) => nearbyRow(h, false)).join("")}
      <div class="row" style="margin-top:12px">
        <button class="btn ghost sm" onclick="planFrom('${facility.id}')">Planera runda härifrån</button>
      </div></div></div>`;
}

function planFrom(facilityId) {
  const f = S.data.customer.facilities.find((x) => x.id === facilityId);
  if (!f || f.latitude === null) return toast("Anläggningen saknar koordinater", true);
  S.origin = { latitude: f.latitude, longitude: f.longitude };
  S.filter.radius = 30;
  go("nara");
}

/* ---------------- administration ---------------- */
async function viewAdmin() {
  const token = claim();
  const tab =
    S.tab && ["konton", "foretag", "notiser", "sgu", "backup", "logg"].includes(S.tab)
      ? S.tab
      : "konton";
  const T = (id, label) =>
    `<button class="${tab === id ? "on" : ""}" onclick="go('admin','${id}')">${label}</button>`;
  mountShell(`
    <div class="spread"><div><div class="eyebrow">Administration</div><h1>Inställningar</h1></div></div>
    <div class="tabs">${T("konton", "Konton")}${T("foretag", "Företag")}${T("notiser", "Notiser")}${T("sgu", "SGU")}${T("backup", "Backup")}${T("logg", "Logg")}</div>
    <div id="adminbody"><div class="skel"></div><div class="skel"></div></div>`);

  if (tab === "konton") await adminUsers();
  else if (tab === "foretag") await adminCompany();
  else if (tab === "sgu") await adminSgu();
  else if (tab === "notiser") await adminNotifications();
  else if (tab === "backup") await adminBackup();
  else await adminLog();
}

async function adminUsers() {
  const token = claim();
  const [users, sec] = await Promise.all([api("/users"), api("/security")]);
  if (!current(token) || !$("#adminbody")) return;
  S.data.users = users;

  const rad = (u) => {
    const jag = u.id === S.user.id;
    return `<tr>
      <td data-l="Användare"><span class="mono">${esc(u.username)}</span>
        ${jag ? `<span class="tag n" style="margin-left:6px">du</span>` : ""}</td>
      <td data-l="Namn">${esc(u.full_name || "—")}</td>
      <td data-l="Roll">${esc(u.role)}</td>
      <td data-l="Tvåfaktor">${
        u.totp_enabled
          ? `<span class="tag ok">På</span>`
          : u.totp_required || sec.require_totp_all
            ? `<span class="tag action">Krävs, ej satt</span>`
            : `<span class="tag n">Av</span>`
      }</td>
      <td data-l="Senast" class="tid">${dt(u.last_login)}</td>
      <td data-l="Status">${
        u.is_active ? `<span class="tag ok">Aktivt</span>` : `<span class="tag action">Avstängt</span>`
      }</td>
      <td data-l=""><button class="btn ghost sm" onclick="editUser('${u.id}')">Hantera</button></td>
    </tr>`;
  };

  $("#adminbody").innerHTML = `
  <div class="card" style="margin-bottom:18px">
    <div class="hd"><h2>Säkerhet för alla konton</h2>
      <span class="tag ${sec.require_totp_all ? "ok" : "n"}">${sec.require_totp_all ? "Tvåfaktor krävs" : "Frivilligt"}</span></div>
    <div class="pad">
      <label style="display:flex;gap:10px;align-items:flex-start;font-size:14.5px">
        <input type="checkbox" id="req_all" ${sec.require_totp_all ? "checked" : ""}
          style="width:auto;margin-top:3px" onchange="setRequireAll(this.checked)">
        <span>Kräv tvåfaktor för alla användare
          <span class="hint" style="display:block;margin-top:2px">Den som inte har det påslaget
          möts av uppsättningen vid nästa sidladdning och kommer inte vidare förrän det är klart.
          Gäller även dig.</span></span>
      </label>
    </div></div>

  <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Användare</h2>
    <span class="tag n">${users.length}</span></div>
    <table><thead><tr><th>Användare</th><th>Namn</th><th>Roll</th><th>Tvåfaktor</th>
      <th>Senast inloggad</th><th>Status</th><th></th></tr></thead>
    <tbody>${users.map(rad).join("")}</tbody></table>
    <div id="userbox"></div>
  </div>

  <div class="card"><div class="hd"><h2>Nytt konto</h2></div><div class="pad">
    <div class="fgrid">
      <div><label class="f" for="nu">Användarnamn</label><input id="nu" autocapitalize="none"></div>
      <div><label class="f" for="nn">Namn</label><input id="nn"></div>
      <div><label class="f" for="np">Lösenord</label><input id="np" type="password" autocomplete="new-password"></div>
      <div><label class="f" for="nr">Roll</label><select id="nr">
        <option value="tekniker">tekniker – läsa och skriva</option>
        <option value="admin">admin – även konton och backup</option>
        <option value="lasare">lasare – bara läsa</option></select></div>
    </div>
    <button class="btn pri sm" style="margin-top:14px" id="ncreate">Skapa konto</button>
  </div></div>`;

  $("#ncreate").onclick = async () => {
    try {
      await api("/users", {
        method: "POST",
        body: {
          username: val("nu").trim(),
          full_name: val("nn").trim(),
          password: val("np"),
          role: val("nr"),
        },
      });
      toast("Kontot skapat");
      adminUsers();
    } catch (e) {
      toast(e.message, true);
    }
  };
}

async function setRequireAll(on) {
  try {
    await api("/security", { method: "PUT", body: { require_totp_all: on } });
    toast(on ? "Tvåfaktor krävs nu för alla konton" : "Tvåfaktor är frivilligt igen");
    adminUsers();
  } catch (e) {
    toast(e.message, true);
    adminUsers();
  }
}

function editUser(userId) {
  const u = (S.data.users || []).find((x) => x.id === userId);
  const box = $("#userbox");
  if (!u || !box) return;
  if (box.dataset.open === userId) {
    box.innerHTML = "";
    box.dataset.open = "";
    return;
  }
  box.dataset.open = userId;
  const jag = u.id === S.user.id;

  box.innerHTML = `
  <div class="pad" style="border-top:1px solid var(--line);background:#F8FAFA">
    <div class="spread" style="margin-bottom:10px">
      <h2 style="margin:0">Hantera ${esc(u.username)}</h2>
      <button class="btn ghost sm" onclick="editUser('${u.id}')">Stäng</button>
    </div>
    <div class="fgrid">
      <div><label class="f" for="uu_name">Användarnamn</label>
        <input id="uu_name" value="${esc(u.username)}" autocapitalize="none">
        <div class="hint">Byte påverkar inte inloggade sessioner.</div></div>
      <div><label class="f" for="uu_full">Namn</label><input id="uu_full" value="${esc(u.full_name || "")}"></div>
      <div><label class="f" for="uu_mail">E-post</label><input id="uu_mail" type="email" value="${esc(u.email || "")}"></div>
      <div><label class="f" for="uu_role">Roll</label>
        <select id="uu_role" ${jag ? "disabled" : ""}>
          ${["tekniker", "admin", "lasare"]
            .map((r) => `<option value="${r}"${u.role === r ? " selected" : ""}>${r}</option>`)
            .join("")}</select>
        ${jag ? `<div class="hint">Du kan inte ändra din egen roll.</div>` : ""}</div>
    </div>

    <div class="row" style="margin-top:14px;gap:18px">
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="uu_totp" ${u.totp_required ? "checked" : ""} style="width:auto">
        Kräv tvåfaktor för just den här användaren</label>
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="uu_active" ${u.is_active ? "checked" : ""}
          ${jag ? "disabled" : ""} style="width:auto">
        Kontot är aktivt</label>
    </div>

    <label class="f" for="uu_pw">Sätt nytt lösenord</label>
    <div class="row">
      <input id="uu_pw" type="password" placeholder="Lämna tomt för att behålla" style="flex:1;min-width:200px">
      <button class="btn ghost sm" onclick="slumpaLosen()">Slumpa</button>
    </div>
    <div class="hint" id="uu_pwhint">Minst 10 tecken. Be användaren byta vid första inloggningen.</div>

    <div class="row" style="margin-top:16px">
      <button class="btn pri sm" onclick="saveUser('${u.id}')">Spara</button>
      ${
        u.totp_enabled
          ? `<button class="btn ghost sm" onclick="resetTotp('${u.id}','${esc(u.username)}')">
               Nollställ tvåfaktor</button>`
          : ""
      }
      ${
        jag
          ? ""
          : `<button class="btn danger sm" style="margin-left:auto"
               onclick="toggleActive('${u.id}', ${!u.is_active})">
               ${u.is_active ? "Stäng av kontot" : "Aktivera kontot"}</button>`
      }
    </div>
  </div>`;
  scrollTill(box);
}

function slumpaLosen() {
  const ord = ["berg", "brunn", "foder", "kax", "pump", "spricka", "vatten", "borr", "slam", "rör",
               "grus", "morän", "tryck", "nivå", "filter", "hydrofor"];
  const p = () => ord[Math.floor(Math.random() * ord.length)];
  const losen = `${p()}-${p()}-${p()}-${Math.floor(10 + Math.random() * 89)}`;
  $("#uu_pw").type = "text";
  $("#uu_pw").value = losen;
  $("#uu_pwhint").innerHTML =
    `<strong>Skriv ner det nu:</strong> det går inte att läsa ut igen efter att du sparat.`;
}

async function saveUser(userId) {
  const body = {
    username: val("uu_name").trim(),
    full_name: val("uu_full").trim(),
    email: val("uu_mail").trim(),
    totp_required: $("#uu_totp").checked,
  };
  if (!$("#uu_role").disabled) body.role = val("uu_role");
  if (!$("#uu_active").disabled) body.is_active = $("#uu_active").checked;
  if (val("uu_pw")) body.new_password = val("uu_pw");
  try {
    await api(`/users/${userId}`, { method: "PATCH", body });
    toast("Kontot sparat");
    adminUsers();
  } catch (e) {
    toast(e.message, true);
  }
}

async function toggleActive(userId, aktivera) {
  if (!aktivera && !confirm("Stänga av kontot? Användaren loggas ut vid nästa anrop.")) return;
  try {
    await api(`/users/${userId}`, { method: "PATCH", body: { is_active: aktivera } });
    toast(aktivera ? "Kontot aktiverat" : "Kontot avstängt");
    adminUsers();
  } catch (e) {
    toast(e.message, true);
  }
}

async function resetTotp(userId, namn) {
  if (!confirm(`Nollställa tvåfaktor för ${namn}? Använd när någon tappat sin telefon.`)) return;
  try {
    await api(`/users/${userId}`, { method: "PATCH", body: { reset_totp: true } });
    toast("Tvåfaktor nollställd, användaren får sätta upp den på nytt");
    adminUsers();
  } catch (e) {
    toast(e.message, true);
  }
}

async function adminNotifications() {
  const token = claim();
  const mail = await api("/notifications/email");
  if (!current(token) || !$("#adminbody")) return;
  $("#adminbody").innerHTML = `
  <p class="lead" style="margin-top:0;margin-bottom:16px">Notiser, tvåfaktor och textstorlek för
    ditt eget konto finns under <a href="#/konto">Mitt konto</a>. Här nedan ställs e-post in för
    hela installationen.</p>

  <div class="card"><div class="hd"><h2>E-post</h2>
    <span class="tag ${mail.enabled ? "ok" : "n"}">${mail.enabled ? "Aktiv" : "Av"}</span></div><div class="pad">
    <div class="fgrid">
      <div><label class="f" for="mh">SMTP-server</label><input id="mh" value="${esc(mail.host || "")}" placeholder="smtp.leverantor.se"></div>
      <div><label class="f" for="mp">Port</label><input id="mp" type="number" value="${esc(mail.port || 587)}"></div>
      <div><label class="f" for="ms">Kryptering</label><select id="ms">
        ${["starttls", "ssl", "none"].map((x) => `<option value="${x}"${mail.security === x ? " selected" : ""}>${x === "none" ? "ingen" : x.toUpperCase()}</option>`).join("")}</select></div>
      <div><label class="f" for="mu">Användarnamn</label><input id="mu" value="${esc(mail.username || "")}" autocapitalize="none"></div>
      <div><label class="f" for="mw">Lösenord</label><input id="mw" type="password" placeholder="${mail.password_set ? "sparat, lämna tomt för att behålla" : "anges en gång"}"></div>
      <div><label class="f" for="mf">Avsändare</label><input id="mf" value="${esc(mail.sender || "")}" placeholder="borrjournal@dinfirma.se"></div>
    </div>
    <label class="f" for="mr">Mottagare av påminnelser</label>
    <input id="mr" value="${esc((mail.recipients || []).join(", "))}" placeholder="mikael@dinfirma.se, anna@dinfirma.se">
    <div class="hint">Kommaseparerat. Ett samlat mejl skickas per genomsökning, inte ett per påminnelse.</div>
    <div class="row" style="margin-top:14px">
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="me" ${mail.enabled ? "checked" : ""} style="width:auto"> Skicka påminnelser via e-post</label>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn pri sm" id="msave">Spara</button>
      <button class="btn ghost sm" id="mtest">Skicka testmejl</button>
    </div>
  </div></div>`;

  const collect = () => ({
    enabled: $("#me").checked,
    host: $("#mh").value.trim(),
    port: parseInt($("#mp").value, 10) || 587,
    security: $("#ms").value,
    username: $("#mu").value.trim(),
    password: $("#mw").value,
    sender: $("#mf").value.trim(),
    recipients: $("#mr").value,
  });
  $("#msave").onclick = async () => {
    try {
      await api("/notifications/email", { method: "PUT", body: collect() });
      toast("E-postinställningar sparade");
      adminNotifications();
    } catch (e) {
      toast(e.message, true);
    }
  };
  $("#mtest").onclick = async () => {
    try {
      await api("/notifications/email", { method: "PUT", body: collect() });
      const r = await api("/notifications/email/test", { method: "POST", body: {} });
      toast(`Testmejl skickat till ${r.sent_to.join(", ")}`);
    } catch (e) {
      toast(e.message, true);
    }
  };
}

async function adminBackup() {
  const token = claim();
  const data = await api("/backups");
  const sch = data.schedule;
  if (!current(token) || !$("#adminbody")) return;
  $("#adminbody").innerHTML = `
  <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Skapa backup</h2>
    <span class="tag n">${data.engine === "pg_dump" ? "pg_dump" : "logisk JSON-dump"}</span></div><div class="pad">
    <p class="lead" style="margin-top:0">Varje backup är en tar.gz med databasdump, alla uppladdade filer,
      tumnaglar och ett manifest. ${
        data.postgres && !data.pg_dump_available
          ? `<strong>Obs:</strong> pg_dump saknas i containern, så en logisk JSON-dump används i stället.`
          : ""
      }</p>
    <div class="row">
      <button class="btn pri" id="bnow">Skapa backup nu</button>
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="b_filer" checked style="width:auto">
        Ta med dokument och bilder</label>
    </div>
    <div class="hint" style="margin-top:8px">
      Upptaget av backuper: ${bytes(data.usage.backup_bytes)} · ledigt på disk: ${bytes(data.usage.free_bytes)}<br>
      Lagras i <code>${esc(data.backup_dir)}</code>${
        data.backup_dir_extern
          ? ` <span class="tag ok" style="margin-left:6px">egen plats</span>`
          : ` <span class="tag soon" style="margin-left:6px">samma volym som filerna</span>`
      }
    </div>
    ${
      data.backup_dir_extern
        ? ""
        : `<p class="hint">Backuperna ligger på samma volym som det de skyddar. Det räcker mot
           misstag i databasen, men inte mot att disken går sönder. Sätt <code>BACKUP_DIR</code>
           i <code>.env</code> till en monterad nätverksdisk eller extern volym, till exempel
           <code>BACKUP_DIR=/backup</code> med en rad
           <code>- /mnt/nas/borrjournal:/backup</code> under volumes i compose.</p>`
    }
  </div></div>

  <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Kundpaket</h2></div><div class="pad">
    <p class="lead" style="margin-top:0">Ett kundpaket innehåller allt om en eller alla kunder:
      uppgifter, anläggningar, journal, påminnelser, dokument och bilder med läsbara filnamn.
      Till skillnad från en backup kan det läsas in i ett system som redan har data, utan att
      röra det som finns där. Det är vad du vill ha om servern ska byggas om, eller om en enskild
      kund råkat raderas.</p>
    <div class="row">
      <button class="btn sm" onclick="exportPaket()">Exportera alla kunder</button>
      <button class="btn ghost sm" onclick="valjKundExport()">Exportera en kund</button>
    </div>
    <div id="exportval"></div>
    <label class="f" style="margin-top:16px">Läs in kundpaket</label>
    <div class="row">
      <input type="file" id="paketfil" accept=".gz,.tar,application/gzip" style="flex:1;min-width:200px">
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="paket_ersatt" style="width:auto">
        Skriv över kunder med samma kundnummer</label>
      <button class="btn sm" onclick="importPaket()">Läs in</button>
    </div>
    <div class="hint">Utan överskrivning hoppas kunder som redan finns över, och du får veta vilka.</div>
    <div id="importresultat"></div>
  </div></div>

  <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Schema</h2>
    <span class="tag ${sch.enabled ? "ok" : "n"}">${sch.enabled ? "Aktivt" : "Av"}</span></div><div class="pad">
    <div class="fgrid">
      <div><label class="f" for="sh">Timme</label><input id="sh" type="number" min="0" max="23" value="${sch.hour}"></div>
      <div><label class="f" for="sm">Minut</label><input id="sm" type="number" min="0" max="59" value="${sch.minute}"></div>
      <div><label class="f" for="sk">Behåll antal dagar</label><input id="sk" type="number" min="1" value="${sch.keep_days}"></div>
      <div><label class="f" for="sr">Genomsök påminnelser kl.</label><input id="sr" type="number" min="0" max="23" value="${sch.reminder_scan_hour}"></div>
    </div>
    <div class="row" style="margin-top:14px">
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="se" ${sch.enabled ? "checked" : ""} style="width:auto"> Kör automatisk backup varje natt</label>
    </div>
    <div class="hint">De tre senaste backuperna behålls alltid, även om de är äldre än gränsen.</div>
    <button class="btn pri sm" style="margin-top:14px" id="ssave">Spara schema</button>
  </div></div>

  <div class="card"><div class="hd"><h2>Backuper</h2><span class="tag n">${data.backups.length}</span></div>
    <table><thead><tr><th>Skapad</th><th>Typ</th><th>Motor</th><th>Storlek</th><th>Status</th><th></th></tr></thead>
    <tbody>${
      data.backups.length
        ? data.backups
            .map(
              (b) => `<tr>
      <td data-l="Skapad" class="tid">${dt(b.created_at)}<div class="tsub">${esc(b.created_by)}</div></td>
      <td data-l="Typ">${esc(b.trigger)}</td>
      <td data-l="Motor" class="mono" style="font-size:12.5px">${esc(b.engine || "—")}</td>
      <td data-l="Storlek" class="tid">${bytes(b.size_bytes)}
        <div class="tsub">${
          b.file_count === null || b.file_count === undefined
            ? ""
            : b.file_count
              ? `${b.file_count} filer, ${bytes(b.file_bytes)}`
              : "utan filer"
        }</div></td>
      <td data-l="Status">${b.status === "klar" ? `<span class="tag ok">Klar</span>` : `<span class="tag action">Fel</span>`}
        ${b.status !== "klar" ? `<div class="tsub">${esc((b.detail || "").slice(0, 90))}</div>` : ""}</td>
      <td data-l=""><div class="row" style="gap:6px">
        ${b.exists ? `<button class="btn ghost sm" onclick="downloadBackup('${b.id}','${esc(b.filename)}')">Ladda ner</button>
        <button class="btn ghost sm" onclick="showRestore('${b.id}')">Återställ</button>` : ""}
        <button class="btn danger sm" onclick="deleteBackup('${b.id}')">Ta bort</button></div></td></tr>`
            )
            .join("")
        : ""
    }</tbody></table>
    ${data.backups.length ? "" : `<div class="empty"><div class="big">Inga backuper än</div><p>Skapa en nu, eller vänta på nattens körning.</p></div>`}
  </div>
  <div id="restorebox"></div>`;

  $("#bnow").onclick = async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Skapar backup…";
    try {
      const r = await api("/backups", {
        method: "POST",
        body: { include_files: $("#b_filer").checked },
      });
      toast(
        r.file_count
          ? `Backup klar, ${bytes(r.size_bytes)} med ${r.file_count} filer`
          : `Backup klar, ${bytes(r.size_bytes)}`
      );
    } catch (err) {
      toast(err.message, true);
    }
    adminBackup();
  };
  $("#ssave").onclick = async () => {
    try {
      await api("/backups/schedule", {
        method: "PUT",
        body: {
          enabled: $("#se").checked,
          hour: parseInt($("#sh").value, 10),
          minute: parseInt($("#sm").value, 10),
          keep_days: parseInt($("#sk").value, 10),
          reminder_scan_hour: parseInt($("#sr").value, 10),
        },
      });
      toast("Schemat sparat");
      adminBackup();
    } catch (e) {
      toast(e.message, true);
    }
  };
}


/* ---------------- kundpaket ---------------- */
async function laddaNer(path, filnamn) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${S.token}` } });
  if (!res.ok) {
    let d = `Fel ${res.status}`;
    try {
      d = (await res.json()).detail || d;
    } catch (_) {}
    return toast(d, true);
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = filnamn;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Nedladdat");
}

async function exportPaket(customerId) {
  const q = customerId ? `?customer_ids=${customerId}` : "";
  const datum = new Date().toISOString().slice(0, 10);
  toast("Bygger paketet, det kan ta en stund…");
  await laddaNer(
    `/api/backups/packages/export${q}`,
    customerId ? `kund-${datum}.tar.gz` : `alla-kunder-${datum}.tar.gz`
  );
}

async function valjKundExport() {
  const box = $("#exportval");
  if (box.innerHTML) return (box.innerHTML = "");
  const kunder = S.data.customers || (await api("/customers"));
  S.data.customers = kunder;
  box.innerHTML = `<div class="row" style="margin-top:10px">
    <select id="expkund" style="flex:1;min-width:200px">
      ${kunder.map((c) => `<option value="${c.id}">${esc(c.customer_no)} ${esc(c.name)}</option>`).join("")}
    </select>
    <button class="btn sm" onclick="exportPaket(val('expkund'))">Exportera</button>
    <button class="btn ghost sm" onclick="document.getElementById('exportval').innerHTML=''">Avbryt</button>
  </div>`;
}

async function importPaket() {
  const input = $("#paketfil");
  const fil = input.files && input.files[0];
  if (!fil) return toast("Välj en paketfil först", true);
  const ersatt = $("#paket_ersatt").checked;
  if (
    ersatt &&
    !confirm(
      "Kunder med samma kundnummer raderas och ersätts av innehållet i paketet. Det går inte att ångra. Fortsätt?"
    )
  )
    return;

  const box = $("#importresultat");
  box.innerHTML = `<div class="skel"></div>`;
  const fd = new FormData();
  fd.append("file", fil);
  fd.append("replace", ersatt ? "true" : "false");
  try {
    const r = await api("/backups/packages/import", { method: "POST", body: fd });
    box.innerHTML = `<div class="card" style="margin-top:12px;border-color:#B6D6C6">
      <div class="pad">
        <strong>${r.skapade.length} kunder inlästa</strong>, ${r.filer} filer.
        ${r.skapade.length ? `<ul style="margin:8px 0 0;padding-left:20px">${r.skapade.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        ${
          r.hoppade.length
            ? `<p class="hint" style="margin-top:10px">Hoppade över ${r.hoppade.length} som redan
               fanns: ${r.hoppade.map(esc).join(", ")}. Kryssa i överskrivning om de ska ersättas.</p>`
            : ""
        }
        ${r.ersatta.length ? `<p class="hint">Ersatte: ${r.ersatta.map(esc).join(", ")}</p>` : ""}
      </div></div>`;
    toast(`${r.skapade.length} kunder inlästa`);
    S.data.customers = null;
  } catch (e) {
    box.innerHTML = `<div class="err" style="margin-top:12px">${esc(e.message)}</div>`;
  }
}

async function downloadBackup(id, filename) {
  const res = await fetch(`/api/backups/${id}/download`, {
    headers: { Authorization: `Bearer ${S.token}` },
  });
  if (!res.ok) return toast("Nedladdningen misslyckades", true);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Backup nedladdad");
}

async function deleteBackup(id) {
  if (!confirm("Ta bort backupen permanent?")) return;
  await api(`/backups/${id}`, { method: "DELETE" });
  toast("Backupen borttagen");
  adminBackup();
}

async function showRestore(id) {
  const g = await api(`/backups/${id}/restore-guide`);
  $("#restorebox").innerHTML = `
  <div class="card" style="margin-top:18px;border-color:#E0BAB2">
    <div class="hd" style="background:#FBEEEC"><h2>Återställ ${esc(g.filename)}</h2></div>
    <div class="pad">
      <div class="err" style="margin-top:0">${esc(g.warning)}</div>
      <p class="lead">Återställning görs från terminalen med avsikt. En webbknapp som skriver över
        databasen är för lätt att trycka på av misstag, och appen kan inte läsa in en dump
        i den databas den själv använder utan att först kopplas ner.</p>
      <ol class="mono" style="font-size:12.5px;line-height:1.9;background:#F8FAFA;border:1px solid var(--line);border-radius:3px;padding:14px 14px 14px 32px">
        ${g.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      <div class="row">
        <button class="btn ghost sm" onclick="copySteps(${JSON.stringify(JSON.stringify(g.steps)).replace(/"/g, "&quot;")})">Kopiera kommandon</button>
        <button class="btn ghost sm" onclick="document.getElementById('restorebox').innerHTML=''">Stäng</button>
      </div>
    </div></div>`;
  scrollTill($("#restorebox"));
}

function copySteps(json) {
  const steps = typeof json === "string" ? JSON.parse(json) : json;
  navigator.clipboard
    .writeText(steps.join("\n"))
    .then(() => toast("Kommandona kopierade"))
    .catch(() => toast("Kunde inte kopiera, markera texten i stället", true));
}

async function adminCompany() {
  const token = claim();
  const f = await api("/company");
  if (!current(token) || !$("#adminbody")) return;
  $("#adminbody").innerHTML = `
  <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Logotyp</h2>
    <span class="tag ${f.har_logotyp ? "ok" : "n"}">${f.har_logotyp ? "Uppladdad" : "Saknas"}</span></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">Visas i appen, på inloggningssidan och överst på varje
        offert och arbetsorder. PNG med genomskinlig bakgrund ser bäst ut.</p>
      <div class="row" style="align-items:center">
        ${
          f.har_logotyp
            ? `<img data-auth-src="/api/company/logo" alt="Logotyp"
                style="max-width:220px;max-height:80px;border:1px solid var(--line);
                border-radius:3px;padding:8px;background:#fff">`
            : ""
        }
        <div>
          <input type="file" id="logofil" accept="image/png,image/jpeg,image/webp">
          <div class="hint">Skalas automatiskt. Max 8 MB.</div>
        </div>
        <button class="btn pri sm" onclick="laddaUppLogo()">Ladda upp</button>
        ${f.har_logotyp ? `<button class="btn danger sm" onclick="taBortLogo()">Ta bort</button>` : ""}
      </div>
    </div></div>

  <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Automatiska påminnelser</h2></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">Systemet håller reda på tre saker som annars rinner ut i
        sanden: obetalda fakturor, offerter utan besked och besök utan återkoppling. Påminnelsen
        stängs av sig själv när saken är löst.</p>
      <div class="fgrid">
        ${fld("f_betalvillkor", "Betalningsvillkor, dagar", f.betalningsvillkor_dagar ?? 30, "number")}
        ${fld("f_obetald", "Påminn om obetalt efter, dagar", f.paminn_obetald_efter_dagar ?? 7, "number")}
        ${fld("f_offert", "Följ upp offert efter, dagar", f.paminn_offert_efter_dagar ?? 10, "number")}
      </div>
      <div class="hint">Fakturan förfaller efter betalningsvillkoren. Påminnelsen kommer så många
        dagar därefter. Med 30 och 7 dyker den upp 37 dagar efter fakturadatum.</div>
    </div></div>

  <div class="card"><div class="hd"><h2>Uppgifter på offerter och arbetsorder</h2></div><div class="pad">
    <p class="lead" style="margin-top:0">Det här står överst på varje PDF som skickas till kund.</p>
    <div class="fgrid">
      ${fld("f_namn", "Företagsnamn", f.namn || "")}
      ${fld("f_orgnr", "Organisationsnummer", f.orgnr || "")}
      ${fld("f_adress", "Adress", f.adress || "")}
      ${fld("f_postnr", "Postnummer", f.postnr || "")}
      ${fld("f_ort", "Ort", f.ort || "")}
      ${fld("f_tel", "Telefon", f.telefon || "")}
      ${fld("f_mail", "E-post", f.epost || "", "email")}
      ${fld("f_giltig", "Offert giltig, dagar", f.offert_giltig_dagar ?? 30, "number")}
    </div>
    <div class="row" style="margin-top:10px">
      <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
        <input type="checkbox" id="f_fskatt" ${f.f_skatt ? "checked" : ""} style="width:auto">
        Godkänd för F-skatt, visas på offerten</label>
    </div>
    <label class="f" for="f_villkor">Standardvillkor på offerter</label>
    <textarea id="f_villkor">${esc(f.villkor || "")}</textarea>
    <button class="btn pri sm" style="margin-top:14px" id="f_spara">Spara</button>
  </div></div>`;
  hydreraBilder($("#adminbody"));
  $("#f_spara").onclick = async () => {
    try {
      await api("/company", {
        method: "PUT",
        body: {
          namn: val("f_namn"), orgnr: val("f_orgnr"), adress: val("f_adress"),
          postnr: val("f_postnr"), ort: val("f_ort"), telefon: val("f_tel"),
          epost: val("f_mail"), f_skatt: $("#f_fskatt").checked, villkor: val("f_villkor"),
          offert_giltig_dagar: parseInt(val("f_giltig"), 10) || 30,
          betalningsvillkor_dagar: parseInt(val("f_betalvillkor"), 10) || 30,
          paminn_obetald_efter_dagar: parseInt(val("f_obetald"), 10) || 7,
          paminn_offert_efter_dagar: parseInt(val("f_offert"), 10) || 10,
        },
      });
      toast("Företagsuppgifterna sparade");
      S.company = null;
      await laddaForetag();
    } catch (e) {
      toast(e.message, true);
    }
  };
}

async function adminSgu() {
  const token = claim();
  const [st, delningar] = await Promise.all([api("/sgu/status"), api("/share/log?limit=20")]);
  if (!current(token) || !$("#adminbody")) return;
  const conf = st.installning || { lan: [], auto: true, dagar: 7 };
  const hamtade = Object.fromEntries(st.lan.map((l) => [l.lanskod, l]));

  $("#adminbody").innerHTML = `
  <div class="card" style="margin-bottom:18px"><div class="hd"><h2>Brunnsarkivet</h2>
    <span class="tag ${st.totalt ? "ok" : "n"}">${st.totalt.toLocaleString("sv-SE")} brunnar</span></div>
    <div class="pad">
      <p class="lead" style="margin-top:0">Kryssa i de län ni jobbar i. De hämtas automatiskt och
        hålls uppdaterade, du behöver inte göra något mer. SGU uppdaterar sina öppna data en gång i
        veckan, så appen hämtar om när lokala data är äldre än ${conf.dagar} dagar.</p>

      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:4px 14px;
        margin:14px 0;max-height:none">
        ${st.tillgangliga_lan
          .map((l) => {
            const h = hamtade[l.kod];
            return `<label style="display:flex;gap:9px;align-items:baseline;font-size:14.5px;padding:3px 0">
            <input type="checkbox" class="sgulan" value="${l.kod}" style="width:auto"
              ${conf.lan.includes(l.kod) ? "checked" : ""}>
            <span>${esc(l.namn)}
              ${
                h
                  ? `<span class="hint" style="display:block;margin:0">${h.antal.toLocaleString("sv-SE")} brunnar, ${dt(h.hamtad, false)}</span>`
                  : `<span class="hint" style="display:block;margin:0">inte hämtat</span>`
              }</span></label>`;
          })
          .join("")}
      </div>

      <div class="row">
        <label style="display:flex;gap:8px;align-items:center;font-size:14px;width:auto">
          <input type="checkbox" id="sgu_auto" ${conf.auto ? "checked" : ""} style="width:auto">
          Håll uppdaterade automatiskt</label>
        <div style="width:auto"><label class="f" for="sgu_dagar" style="margin:0 0 2px">Hämta om efter</label>
          <input id="sgu_dagar" type="number" min="1" max="90" value="${conf.dagar}" style="width:90px"></div>
        <span class="hint" style="margin:0;align-self:flex-end">dagar</span>
      </div>

      <div class="row" style="margin-top:14px">
        <button class="btn pri sm" id="sgu_spara">Spara val</button>
        <button class="btn ghost sm" id="sgu_nu">Hämta valda nu</button>
        <span class="hint" id="sgu_hint" style="margin:0">Ett län tar från några sekunder till ett par minuter.</span>
      </div>

      <p class="hint" style="margin-top:14px">Data hämtas från SGU:s bulkfiler per län. Licens
        Creative Commons Erkännande 4.0, SGU anges som källa i underlaget. Brunnsarkivet innehåller
        ingen vattenkvalitet. Brunnar utan koordinat i SGU:s register hoppas över, de går inte att
        placera på kartan.</p>
    </div></div>

  <div class="card"><div class="hd"><h2>Senast delat med externa</h2></div>
    ${
      delningar.length
        ? `<table><thead><tr><th>Tid</th><th>Mottagare</th><th>Innehåll</th><th>Av</th></tr></thead>
           <tbody>${delningar
             .map(
               (d) => `<tr><td data-l="Tid" class="tid">${dt(d.sent_at)}</td>
             <td data-l="Mottagare">${esc(d.recipient)}</td>
             <td data-l="Innehåll" class="tsub">${esc(d.fields)}</td>
             <td data-l="Av">${esc(d.sent_by)}</td></tr>`
             )
             .join("")}</tbody></table>`
        : `<div class="empty"><div class="big">Inget delat än</div>
           <p>Utskick till externa borrare loggas här och i kundens journal.</p></div>`
    }
  </div>`;

  const valda = () => [...document.querySelectorAll(".sgulan:checked")].map((c) => c.value);

  const sparaVal = async () => {
    await api("/sgu/settings", {
      method: "PUT",
      body: {
        lan: valda(),
        auto: $("#sgu_auto").checked,
        dagar: parseInt(val("sgu_dagar"), 10) || 7,
      },
    });
  };

  $("#sgu_spara").onclick = async () => {
    try {
      await sparaVal();
      toast(
        valda().length
          ? `${valda().length} län sparade, hämtas automatiskt`
          : "Inga län valda, ingen automatisk hämtning"
      );
      adminSgu();
    } catch (e) {
      toast(e.message, true);
    }
  };

  $("#sgu_nu").onclick = async (e) => {
    const lista = valda();
    if (!lista.length) return toast("Kryssa i minst ett län först", true);
    const knapp = e.target;
    knapp.disabled = true;
    try {
      await sparaVal();
      for (let i = 0; i < lista.length; i++) {
        const namn = st.tillgangliga_lan.find((l) => l.kod === lista[i]).namn;
        knapp.textContent = `Hämtar ${i + 1} av ${lista.length}…`;
        $("#sgu_hint").textContent = `${namn} pågår, låt fliken vara öppen.`;
        try {
          const r = await api("/sgu/sync", { method: "POST", body: { lanskod: lista[i] } });
          $("#sgu_hint").textContent =
            `${r.namn}: ${r.sparade.toLocaleString("sv-SE")} brunnar på ${r.sekunder} s` +
            (r.utan_koordinat ? `, ${r.utan_koordinat.toLocaleString("sv-SE")} utan koordinat` : "");
        } catch (err) {
          toast(`${namn}: ${err.message}`, true);
        }
      }
      toast("Hämtningen klar");
    } finally {
      adminSgu();
    }
  };
}

async function adminLog() {
  const token = claim();
  const audit = await api("/audit?limit=100");
  if (!current(token) || !$("#adminbody")) return;
  $("#adminbody").innerHTML = `
  <div class="card"><div class="hd"><h2>Händelselogg</h2></div>
    <table><thead><tr><th>Tid</th><th>Användare</th><th>Händelse</th><th>Objekt</th><th>IP</th></tr></thead>
    <tbody>${audit
      .map(
        (a) => `<tr><td data-l="Tid" class="tid">${dt(a.at)}</td><td data-l="Användare">${esc(a.actor)}</td>
      <td data-l="Händelse" class="mono" style="font-size:12.5px">${esc(a.action)}</td>
      <td data-l="Objekt" class="tid">${esc(a.object_type)} ${esc(a.detail || "")}</td>
      <td data-l="IP" class="tid">${esc(a.ip_address || "—")}</td></tr>`
      )
      .join("")}</tbody></table></div>`;
}

/* ---------------- render ---------------- */
function render() {
  claim();
  const vy = S.route + "|" + S.id + "|" + S.tab;
  if (!S.token || !S.user) return viewLogin();
  const views = {
    oversikt: viewDashboard,
    kunder: viewCustomers,
    kund: viewCustomer,
    pumpar: viewPumps,
    anlaggningar: viewFacilities,
    journal: viewJournalAll,
    paminnelser: viewReminders,
    mer: viewMore,
    nara: viewNearby,
    ny: viewNewFacility,
    konto: viewAccount,
    offert: viewQuote,
    order: viewOrder,
    artiklar: viewArticles,
    mallar: viewTemplates,
    handelser: viewEvents,
    ekonomi: viewEconomy,
    besok: (S.id ? viewVisit : viewVisits),
    admin: viewAdmin,
  };
  const fn = views[S.route] || viewDashboard;
  Promise.resolve(fn())
    .then(() => hydreraBilder())
    .catch((e) => {
    // Svälj bara om användaren hunnit navigera bort. Ett fel i den vy som
    // faktiskt visas ska alltid synas, annars står man inför en halv sida
    // utan att veta varför.
    if (S.route + "|" + S.id + "|" + S.tab !== vy || e.message === "401") return;
    console.error("fel i vyn", S.route, e);
    mountShell(`<div class="err">${esc(e.message)}</div>
      <button class="btn ghost sm" onclick="render()">Försök igen</button>`);
  });
}

try {
  applySize(localStorage.getItem("bj_size") || "normal");
} catch (_) {
  S.size = "normal";
}

// Företagsnamn och logotyp behövs innan skalet ritas, annars hinner
// standardnamnet blinka förbi.
(async () => {
  if (S.token) await laddaForetag();
  applyHash();
})();

// Varnar om gränssnittet och servern inte är i takt. Beror nästan alltid på att
// webbläsaren håller kvar en gammal app.js, inte på att backend är gammal.
(async () => {
  try {
    const r = await fetch("/api/version", { cache: "no-store" });
    const { version, ui_version } = await r.json();
    if (version === UI_VERSION) return;

    // Servern läser gränssnittets version från disk. Skiljer den sig från vad
    // den här sidan kör är det webbläsaren som ligger efter, inte servern.
    const filenPaDisk = ui_version || "okänd";
    const bara_cache = filenPaDisk === version;

    const bar = document.createElement("div");
    bar.style.cssText =
      "position:fixed;left:0;right:0;top:0;z-index:300;background:#B3801F;color:#fff;" +
      "padding:9px 14px;font:13.5px/1.5 system-ui,sans-serif;text-align:center;" +
      "display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap";
    bar.innerHTML = bara_cache
      ? `<span>Servern och gränssnittet kör ${esc(version)}, men den här fliken
         har kvar ${UI_VERSION}. Hämta om sidan.</span>`
      : `<span>Backend kör ${esc(version)}, gränssnittsfilen på servern är
         ${esc(filenPaDisk)}. Delarna är inte i takt, byt ut båda och bygg om.</span>`;
    const knapp = document.createElement("button");
    knapp.textContent = "Hämta om nu";
    knapp.style.cssText =
      "background:#fff;color:#7A5712;border:none;border-radius:3px;padding:6px 14px;" +
      "font:inherit;font-weight:600;cursor:pointer";
    knapp.onclick = () => hamtaOmAppen(knapp);
    bar.appendChild(knapp);
    document.body.appendChild(bar);
  } catch (_) {}
})();

/* Rensar allt som kan hålla kvar gammal kod och laddar om.
   Service workern och webbläsarens cache är de två som brukar ligga kvar. */
async function hamtaOmAppen(knapp) {
  if (knapp) {
    knapp.disabled = true;
    knapp.textContent = "Hämtar…";
  }
  try {
    if ("caches" in window) {
      const nycklar = await caches.keys();
      await Promise.all(nycklar.map((k) => caches.delete(k)));
    }
  } catch (_) {}
  try {
    const regs = await navigator.serviceWorker?.getRegistrations?.();
    for (const reg of regs || []) {
      if (reg.waiting) reg.waiting.postMessage("ta-over");
      await reg.unregister();
    }
  } catch (_) {}
  // Ny adress tvingar förbi allt som ändå ligger kvar
  location.replace(location.pathname + "?uppdaterad=" + Date.now() + location.hash);
}

if (S.token) {
  PUSH.ready();
  refreshBadge();
}
