/* Quick Quote - sales-rep interface.
 * Every number shown here comes from the deterministic engine over the API.
 * Nothing is computed client-side except layout. */
(() => {
"use strict";

// ------------------------------------------------------------ helpers
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const money  = (v, d = 2) => v == null ? "-" : "$" + Number(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const money4 = (v) => money(v, 4);
const num    = (v) => v == null ? "-" : Number(v).toLocaleString("en-US");
const pct    = (v) => v == null ? "-" : Number(v).toFixed(0) + "%";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** replaceChildren stringifies null, so children are filtered first. */
function mount(node, ...children) {
  node.replaceChildren(...children.flat().filter((child) => child != null && child !== false));
  return node;
}

const statusClass = (s) => s === "Accepted" ? "accepted" : s === "Needs Review" ? "review" : "unmatched";
const confClass   = (c) => (c || "").toLowerCase();
const badge = (text, cls) => el("span", {class: `badge ${cls}`}, text);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); if (body.detail) detail = body.detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

const jsonPost = (path, body) => api(path, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify(body),
});

// -------------------------------------------------------------- state
const state = {
  ingredientCatalog: [],
  packagingCatalog: [],
  formula: [],     // {name, claimed_mg, part_code, meta}
  packaging: [],   // {role, description, qty_per_bottle, listed_unit_cost, meta}
  preview: null,
  result: null,
  previewToken: 0,
};

// --------------------------------------------------------------- tabs
function showView(name) {
  $$("nav.tabs button").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.view === name));
  });
  $$("main .view").forEach((section) => {
    section.hidden = section.id !== `view-${name}`;
  });
  if (name === "quotes") loadQuotes();
  if (name === "reference") loadReference();
}
$$("nav.tabs button").forEach((button) =>
  button.addEventListener("click", () => showView(button.dataset.view)));

// ------------------------------------------------------ product form
const PRODUCT_FIELDS = [
  "customer", "brand", "formula_name", "customer_sku", "dosage_form",
  "capsule_size", "capsule_type", "serving_size", "servings_per_bottle",
  "count_per_bottle", "annual_volume_bottles", "moq", "timeline", "machine",
];
const INT_FIELDS = new Set(["servings_per_bottle", "count_per_bottle", "annual_volume_bottles", "moq"]);

function readProduct() {
  const product = {};
  for (const field of PRODUCT_FIELDS) {
    const input = $(`#p-${field}`);
    if (!input) continue;
    const raw = input.value.trim();
    if (!raw) { product[field] = null; continue; }
    product[field] = INT_FIELDS.has(field) ? parseInt(raw, 10) : raw;
    if (INT_FIELDS.has(field) && !Number.isFinite(product[field])) product[field] = null;
  }
  const split = (id) => $(`#${id}`).value.split(";").map((s) => s.trim()).filter(Boolean);
  product.claims = split("p-claims");
  product.testing = split("p-testing");
  return product;
}

function payload() {
  return {
    product: readProduct(),
    formula: state.formula.map(({name, claimed_mg, part_code}) => ({name, claimed_mg, part_code})),
    packaging: state.packaging.map(({role, description, qty_per_bottle, listed_unit_cost, part_code}) =>
      ({role, description, qty_per_bottle, listed_unit_cost, part_code})),
  };
}

// ----------------------------------------------------- catalog picker
function renderIngredientOptions(query) {
  const results = $("#ing-results");
  results.replaceChildren();
  const needle = query.trim().toLowerCase();

  const matches = state.ingredientCatalog.filter((item) =>
    !needle || item.display_name.toLowerCase().includes(needle) ||
    item.aliases.some((alias) => alias.includes(needle))
  ).slice(0, 40);

  if (!matches.length) {
    results.append(el("div", {class: "empty"},
      needle
        ? el("div", {},
            el("div", {}, `No identity matches "${query}".`),
            el("div", {class: "small", style: "margin-top:6px"},
              "Add it anyway to see it reported as Unmatched, which is what the engine will do."),
            el("button", {class: "btn small ghost", style: "margin-top:8px",
                          onclick: () => addIngredient({display_name: query.trim(), canonical: null,
                                                        expected_status: "Unmatched", po_options: []})},
              `Add "${query.trim()}" as written`))
        : "Start typing to search."));
    return;
  }

  for (const item of matches) {
    const best = item.po_options[0];
    results.append(el("div", {class: "option", onclick: () => addIngredient(item)},
      el("span", {class: "name"}, item.display_name),
      badge(item.expected_status, statusClass(item.expected_status)),
      el("span", {class: "meta"},
        best ? `${best.part_number} · ${money(best.latest_unit_cost)}/${best.uom}` : "no PO record"),
    ));
  }
}

function renderPackagingOptions(query) {
  const results = $("#pkg-results");
  results.replaceChildren();
  const needle = query.trim().toLowerCase();

  let count = 0;
  for (const item of state.packagingCatalog) {
    // Each PO size is its own selectable line, because size never substitutes.
    const options = item.po_options.length ? item.po_options : [null];
    for (const option of options) {
      const label = option ? option.description : item.display_name;
      const haystack = `${item.display_name} ${item.role} ${label}`.toLowerCase();
      if (needle && !haystack.includes(needle)) continue;
      if (count++ > 60) break;
      results.append(el("div", {class: "option", onclick: () => addPackaging(item, option)},
        el("span", {class: "name"}, label),
        badge(item.role.replace(/_/g, " "), "neutral"),
        el("span", {class: "meta"},
          option ? `${option.part_number} · ${money(option.latest_unit_cost, 4)}/${option.uom}` : "no PO record"),
      ));
    }
  }
  if (!count) results.append(el("div", {class: "empty"}, "No packaging identity matches that search."));
}

function addIngredient(item) {
  state.formula.push({
    name: item.display_name,
    claimed_mg: null,
    part_code: null,
    meta: item,
  });
  $("#ing-search").value = "";
  renderIngredientOptions("");
  renderFormula();
  schedulePreview();
}

function addPackaging(item, option) {
  state.packaging.push({
    role: item.role,
    description: option ? option.description : item.display_name,
    qty_per_bottle: null,
    listed_unit_cost: null,
    part_code: null,
    meta: item,
  });
  $("#pkg-search").value = "";
  renderPackagingOptions("");
  renderPackaging();
  schedulePreview();
}

// ------------------------------------------------------ builder rows
function lineStatus(kind, index) {
  const preview = state.preview;
  if (!preview) return null;
  const line = (kind === "formula" ? preview.ingredients : preview.packaging)[index];
  return line || null;
}

function renderFormula() {
  const body = $("#formula-table tbody");
  body.replaceChildren();
  $("#formula-empty").hidden = state.formula.length > 0;
  $("#formula-table").hidden = state.formula.length === 0;

  state.formula.forEach((line, index) => {
    const costed = lineStatus("formula", index);
    void costed;
    const status = costed ? costed.match.status : (line.meta?.expected_status || "Unmatched");
    const best = line.meta?.po_options?.[0];

    const context = costed
      ? (costed.match.matched_code
          ? `${costed.match.matched_code} · ${money(costed.cost_per_kg)}/kg${costed.cost_per_bottle != null ? ` · ${money4(costed.cost_per_bottle)}/bottle` : ""}`
          : costed.match.reason)
      : (best ? `${best.part_number} · ${money(best.latest_unit_cost)}/${best.uom}` : "no PO record");

    body.append(el("tr", {"data-idx": String(index)},
      el("td", {}, el("input", {value: line.name, oninput: (event) => {
        line.name = event.target.value; schedulePreview();
      }})),
      el("td", {class: "num"}, el("input", {
        type: "number", min: "0", step: "any", style: "text-align:right",
        value: line.claimed_mg ?? "", placeholder: "mg",
        oninput: (event) => {
          const value = parseFloat(event.target.value);
          line.claimed_mg = Number.isFinite(value) ? value : null;
          schedulePreview();
        }})),
      el("td", {}, el("input", {
        value: line.part_code ?? "", placeholder: "optional", class: "mono",
        oninput: (event) => { line.part_code = event.target.value.trim() || null; schedulePreview(); }})),
      el("td", {}, badge(status, statusClass(status))),
      el("td", {class: "small muted"}, context),
      el("td", {}, el("button", {class: "link", title: "Remove", onclick: () => {
        state.formula.splice(index, 1); renderFormula(); schedulePreview();
      }}, "Remove")),
    ));
  });
}

/* A size-bearing component is chosen from the PO records that exist, never
 * typed freehand: 175cc must not be substituted for 250cc. Roles with no PO
 * record, or only one, stay as plain text. */
function descriptionControl(line) {
  const options = line.meta?.po_options || [];
  if (options.length < 2) {
    return el("input", {value: line.description, oninput: (event) => {
      line.description = event.target.value;
      schedulePreview();
    }});
  }
  const chosen = options.some((option) => option.description === line.description);
  const select = el("select", {onchange: (event) => {
    line.description = event.target.value;
    schedulePreview();
  }});
  if (!chosen) {
    select.append(el("option", {value: line.description}, `Choose a size (${options.length} on file)`));
  }
  for (const option of options) {
    select.append(el("option", {value: option.description}, option.description));
  }
  select.value = line.description;
  return select;
}

function renderPackaging() {
  const body = $("#packaging-table tbody");
  body.replaceChildren();
  $("#packaging-empty").hidden = state.packaging.length > 0;
  $("#packaging-table").hidden = state.packaging.length === 0;

  state.packaging.forEach((line, index) => {
    const costed = lineStatus("packaging", index);
    const status = costed ? costed.match.status : "-";
    body.append(el("tr", {"data-idx": String(index)},
      el("td", {class: "small"}, line.role.replace(/_/g, " ")),
      el("td", {}, descriptionControl(line)),
      el("td", {class: "num"}, el("input", {
        type: "number", min: "0", step: "any", style: "text-align:right",
        value: line.qty_per_bottle ?? "",
        placeholder: costed?.qty_per_bottle != null ? String(costed.qty_per_bottle) : "auto",
        oninput: (event) => {
          const value = parseFloat(event.target.value);
          line.qty_per_bottle = Number.isFinite(value) ? value : null;
          schedulePreview();
        }})),
      el("td", {class: "num"}, el("input", {
        type: "number", min: "0", step: "any", style: "text-align:right",
        value: line.listed_unit_cost ?? "", placeholder: "from PO",
        oninput: (event) => {
          const value = parseFloat(event.target.value);
          line.listed_unit_cost = Number.isFinite(value) ? value : null;
          schedulePreview();
        }})),
      el("td", {}, costed ? badge(status, statusClass(status)) : el("span", {class: "muted small"}, "-")),
      el("td", {}, el("button", {class: "link", onclick: () => {
        state.packaging.splice(index, 1); renderPackaging(); schedulePreview();
      }}, "Remove")),
    ));
  });
}

// --------------------------------------------------- standard pack
const STANDARD_PACK = [
  ["capsule_shell", "vegetable capsule shell"],
  ["bottle", "hdpe bottle white"],
  ["cap", "cr cap white"],
  ["label", "pressure sensitive label"],
  ["neckband", "shrink neckband"],
  ["cotton", "cotton coil"],
  ["desiccant", "desiccant canister"],
  ["shipper", "corrugated shipper"],
];

$("#pkg-standard").addEventListener("click", () => {
  const capsuleType = ($("#p-capsule_type").value || "Vegetable").toLowerCase();
  for (const [role, canonical] of STANDARD_PACK) {
    let wanted = canonical;
    if (role === "capsule_shell") {
      wanted = capsuleType.startsWith("gel") ? "gelatin capsule shell" : "vegetable capsule shell";
    }
    const item = state.packagingCatalog.find((entry) => entry.canonical === wanted);
    if (!item) continue;
    if (state.packaging.some((line) => line.role === role)) continue;

    // For a size-bearing role leave the identity generic: the engine resolves
    // the capsule size from the product spec, and an ambiguous bottle size
    // should be chosen deliberately rather than guessed here.
    let option = null;
    if (role === "capsule_shell") {
      const size = $("#p-capsule_size").value;
      option = item.po_options.find((entry) => entry.capsule_size === size) || null;
    } else if (item.po_options.length === 1) {
      option = item.po_options[0];
    }
    // Where several sizes exist the rep chooses; nothing here guesses one.
    state.packaging.push({
      role, description: option ? option.description : item.display_name,
      qty_per_bottle: null, listed_unit_cost: null, part_code: null, meta: item,
    });
  }
  renderPackaging();
  schedulePreview();
});

// ------------------------------------------------------- live preview
let previewTimer = null;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 260);
}

/* A preview must not rebuild the rows: a rep may be mid-keystroke in one of
 * them. Only the cells the engine decides are replaced. */
function refreshStatuses(result) {
  for (const row of $$("#formula-table tbody tr")) {
    const costed = result.ingredients[Number(row.dataset.idx)];
    if (!costed) continue;
    const context = costed.match.matched_code
      ? `${costed.match.matched_code} · ${money(costed.cost_per_kg)}/kg` +
        (costed.cost_per_bottle != null ? ` · ${money4(costed.cost_per_bottle)}/bottle` : "")
      : costed.match.reason;
    mount(row.cells[3], badge(costed.match.status, statusClass(costed.match.status)));
    row.cells[4].textContent = context;
  }
  for (const row of $$("#packaging-table tbody tr")) {
    const costed = result.packaging[Number(row.dataset.idx)];
    if (!costed) continue;
    mount(row.cells[4], badge(costed.match.status, statusClass(costed.match.status)));
    const qty = row.cells[2].querySelector("input");
    if (qty && !qty.value && costed.qty_per_bottle != null) {
      qty.placeholder = String(costed.qty_per_bottle);
    }
  }
}

async function runPreview() {
  const hasLines = state.formula.length || state.packaging.length;
  $("#generate-btn").disabled = !hasLines;
  if (!hasLines) {
    state.preview = null;
    $("#live-panel").replaceChildren(el("p", {class: "empty"},
      "Add an ingredient or a packaging component to see costs."));
    return;
  }

  const token = ++state.previewToken;
  try {
    const result = await jsonPost("/api/quotes/preview", payload());
    if (token !== state.previewToken) return;   // a newer edit already won
    state.preview = result;
    renderLivePanel(result);
    refreshStatuses(result);
  } catch (error) {
    if (token !== state.previewToken) return;
    $("#live-panel").replaceChildren(el("div", {class: "alert danger"}, String(error.message)));
  }
}

function breakdownBars(summary) {
  const total = summary.primary_per_bottle || 1;
  const rows = [
    ["Raw materials", summary.raw_materials, ""],
    ["Packaging", summary.packaging, "pkg"],
    ["Manufacturing", summary.manufacturing, "mfg"],
  ];
  return el("div", {class: "bars"}, rows.map(([label, value, cls]) =>
    el("div", {class: "bar-row"},
      el("span", {class: "muted"}, label),
      el("span", {class: "bar-track"},
        el("span", {class: `bar-fill ${cls}`, style: `width:${Math.max(0, Math.min(100, value / total * 100))}%`})),
      el("span", {class: "amt"}, money(value)))));
}

function renderLivePanel(result) {
  const summary = result.summary;
  const manufacturing = result.manufacturing;
  const panel = $("#live-panel");
  const blocking = result.flags.filter((flag) => flag.severity === "blocking");

  mount(panel,
    el("div", {class: "headline"},
      el("div", {class: "label"}, "Cost per bottle · Accepted lines only"),
      el("div", {class: "value"}, money(summary.primary_per_bottle)),
      el("div", {class: "range"}, `${money(summary.low_per_bottle)} – ${money(summary.high_per_bottle)}`),
      el("div", {style: "margin-top:8px"},
        badge(`${summary.quote_confidence} confidence`, confClass(summary.quote_confidence)))),

    breakdownBars(summary),
    el("div", {class: "spacer"}),

    (summary.unmatched_count || summary.needs_review_count)
      ? el("div", {class: "alert danger"},
          el("strong", {}, "Cost exposure"),
          el("ul", {},
            summary.unmatched_count ? el("li", {},
              `${summary.unmatched_count} unmatched: ${summary.unmatched_items.join(", ")} — no cost.`) : null,
            summary.needs_review_count ? el("li", {},
              `${summary.needs_review_count} need review, ${money4(summary.tentative_exposure)}/bottle excluded.`) : null),
          el("div", {style: "margin-top:6px"}, "The total above understates true cost."))
      : el("div", {class: "alert ok"}, "Every line matched a single PO record."),

    !manufacturing.estimated
      ? el("div", {class: "alert warn"}, manufacturing.reason)
      : null,

    el("dl", {class: "kv"},
      el("dt", {}, "Components in formula"), el("dd", {}, manufacturing.component_count),
      el("dt", {}, "Machine"), el("dd", {}, manufacturing.machine_assumed ? `${manufacturing.machine} (assumed)` : manufacturing.machine),
      el("dt", {}, "Review flags"), el("dd", {}, `${result.flags.length} (${blocking.length} blocking)`)),

    summary.cost_drivers.length ? el("div", {},
      el("div", {class: "spacer"}),
      el("div", {class: "small muted", style: "margin-bottom:4px"}, "TOP COST DRIVERS"),
      el("dl", {class: "kv"}, summary.cost_drivers.slice(0, 5).flatMap((driver) => [
        el("dt", {}, driver.label),
        el("dd", {}, `${money(driver.cost_per_bottle)} · ${pct(driver.share_pct)}`)]))) : null,
  );
}

$("#generate-btn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  const original = button.textContent;
  button.replaceChildren(el("span", {class: "spinner"}), document.createTextNode(" Generating…"));
  try {
    const result = await jsonPost("/api/quotes", payload());
    showResult(result);
  } catch (error) {
    alert(`Could not generate the quote package.\n\n${error.message}`);
  } finally {
    button.textContent = original;
    button.disabled = false;
  }
});

$$("#product-form input, #product-form select").forEach((input) =>
  input.addEventListener("change", schedulePreview));

$("#ing-search").addEventListener("input", (event) => renderIngredientOptions(event.target.value));
$("#pkg-search").addEventListener("input", (event) => renderPackagingOptions(event.target.value));

// -------------------------------------------------------------- upload
let pendingFile = null;
const dropzone = $("#dropzone");
const fileInput = $("#file-input");

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("dragover", (event) => { event.preventDefault(); dropzone.classList.add("over"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("over");
  if (event.dataTransfer.files.length) setFile(event.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => { if (fileInput.files.length) setFile(fileInput.files[0]); });

function setFile(file) {
  pendingFile = file;
  $("#file-name").textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
  $("#upload-btn").disabled = false;
  $("#inspect-btn").disabled = false;
  $("#parse-preview").replaceChildren();
}

async function send(path, then) {
  if (!pendingFile) return;
  const form = new FormData();
  form.append("file", pendingFile);
  $("#parse-preview").replaceChildren(el("p", {class: "loading"}, "Reading the document…"));
  try {
    then(await api(path, {method: "POST", body: form}));
  } catch (error) {
    $("#parse-preview").replaceChildren(el("div", {class: "alert danger"}, String(error.message)));
  }
}

$("#upload-btn").addEventListener("click", () => send("/api/quotes/upload", showResult));
$("#inspect-btn").addEventListener("click", () => send("/api/quotes/parse-only", showParse));

function showParse(parsed) {
  const product = parsed.product;
  const set = Object.entries(product).filter(([, value]) =>
    value != null && value !== "" && !(Array.isArray(value) && !value.length));

  mount($("#parse-preview"),
    el("div", {class: "card"},
      el("h3", {}, `Parsed from ${parsed.source_name} (${parsed.source_format})`),
      el("div", {class: "body"},
        parsed.missing_fields.length
          ? el("div", {class: "alert warn"},
              el("strong", {}, `${parsed.missing_fields.length} expected field(s) not present: `),
              parsed.missing_fields.join(", "),
              el("div", {class: "small", style: "margin-top:6px"}, "These are recorded as missing; nothing is filled in."))
          : el("div", {class: "alert ok"}, "Every expected field was present."),
        parsed.parser_notes.length
          ? el("div", {class: "alert info"}, el("strong", {}, "Parser notes"),
              el("ul", {}, parsed.parser_notes.map((note) => el("li", {}, note))))
          : null,
        el("dl", {class: "kv"}, set.flatMap(([key, value]) => [
          el("dt", {}, key.replace(/_/g, " ")),
          el("dd", {}, Array.isArray(value) ? value.join("; ") : String(value))])),
        el("div", {class: "spacer"}),
        el("h4", {}, `Formula — ${parsed.formula.length} line(s)`),
        el("div", {class: "scroll-x"}, el("table", {},
          el("thead", {}, el("tr", {}, el("th", {}, "Ingredient"), el("th", {class: "num"}, "mg/serving"), el("th", {}, "Part code"))),
          el("tbody", {}, parsed.formula.map((line) => el("tr", {},
            el("td", {}, line.name),
            el("td", {class: "num"}, line.claimed_mg == null ? el("span", {class: "excluded"}, "not stated") : line.claimed_mg),
            el("td", {class: "mono"}, line.part_code || "-")))))),
        el("div", {class: "spacer"}),
        el("h4", {}, `Packaging — ${parsed.packaging.length} line(s)`),
        el("div", {class: "scroll-x"}, el("table", {},
          el("thead", {}, el("tr", {}, el("th", {}, "Role"), el("th", {}, "Description"), el("th", {class: "num"}, "Qty/bottle"))),
          el("tbody", {}, parsed.packaging.map((line) => el("tr", {},
            el("td", {}, line.role.replace(/_/g, " ")),
            el("td", {}, line.description),
            el("td", {class: "num"}, line.qty_per_bottle ?? "auto")))))),
        el("div", {class: "spacer"}),
        el("div", {class: "actions"},
          el("button", {class: "btn", onclick: () => send("/api/quotes/upload", showResult)}, "Cost this quote")))));
}

// -------------------------------------------------------------- result
function costCell(line, value) {
  return line.accepted && value != null
    ? el("td", {class: "num"}, money4(value))
    : el("td", {class: "num excluded"}, "Excluded");
}

function showResult(result) {
  state.result = result;
  const summary = result.summary;
  const product = result.product;
  const manufacturing = result.manufacturing;

  const form = [product.dosage_form, product.capsule_size && `size ${product.capsule_size}`,
                product.capsule_type, product.count_per_bottle && `${product.count_per_bottle} ct`,
                product.servings_per_bottle && `${product.servings_per_bottle} servings`]
                .filter(Boolean).join(" ") || "Not specified";

  const body = $("#result-body");
  mount(body,
    el("div", {class: "draft-banner"}, "INTERNAL DRAFT — not for release until Sales and Finance review."),

    el("div", {class: "cols"},
      el("div", {},
        el("div", {class: "card"},
          el("h3", {}, "Quote package"),
          el("div", {class: "body"},
            el("dl", {class: "kv"},
              el("dt", {}, "Customer"),  el("dd", {}, product.customer || "Not specified"),
              el("dt", {}, "Product"),   el("dd", {}, product.formula_name || "Not specified"),
              el("dt", {}, "Form"),      el("dd", {}, form),
              el("dt", {}, "Volume"),    el("dd", {}, product.annual_volume_bottles ? `${num(product.annual_volume_bottles)} bottles` : "Not specified"),
              el("dt", {}, "MOQ"),       el("dd", {}, product.moq ? num(product.moq) : "Not specified"),
              el("dt", {}, "Timeline"),  el("dd", {}, product.timeline || "Not specified"),
              el("dt", {}, "Source"),    el("dd", {}, `${result.parsed.source_name} (${result.parsed.source_format})`),
              el("dt", {}, "Quote ID"),  el("dd", {class: "mono"}, result.quote_id)),
            result.parsed.missing_fields.length
              ? el("div", {class: "alert warn", style: "margin-top:12px"},
                  el("strong", {}, "Missing fields: "), result.parsed.missing_fields.join(", "))
              : null)),

        bomCard(result),
        packagingCard(result),
        manufacturingCard(manufacturing),
        flagsCard(result)),

      el("div", {class: "sticky"},
        el("div", {class: "card"},
          el("h3", {}, "Cost summary"),
          el("div", {class: "body"},
            el("div", {class: "headline"},
              el("div", {class: "label"}, "Cost per bottle · Accepted lines only"),
              el("div", {class: "value"}, money(summary.primary_per_bottle)),
              el("div", {class: "range"}, `${money(summary.low_per_bottle)} – ${money(summary.high_per_bottle)}`),
              el("div", {style: "margin-top:8px"}, badge(`${summary.quote_confidence} confidence`, confClass(summary.quote_confidence)))),
            breakdownBars(summary),
            el("div", {class: "spacer"}),
            (summary.unmatched_count || summary.needs_review_count)
              ? el("div", {class: "alert danger"},
                  el("strong", {}, "Cost exposure"),
                  el("ul", {},
                    summary.unmatched_count ? el("li", {}, `${summary.unmatched_count} unmatched: ${summary.unmatched_items.join(", ")}`) : null,
                    summary.needs_review_count ? el("li", {}, `${summary.needs_review_count} need review · ${money4(summary.tentative_exposure)}/bottle excluded`) : null))
              : el("div", {class: "alert ok"}, "No cost exposure."),
            el("div", {class: "small muted", style: "margin-bottom:4px"}, "TOP COST DRIVERS"),
            el("dl", {class: "kv"}, summary.cost_drivers.slice(0, 5).flatMap((driver) => [
              el("dt", {}, driver.label), el("dd", {}, `${money(driver.cost_per_bottle)} · ${pct(driver.share_pct)}`)])))),

        el("div", {class: "card"},
          el("h3", {}, "Download"),
          el("div", {class: "body"},
            el("div", {class: "actions"},
              el("a", {class: "btn", href: `/api/quotes/${result.quote_id}/workbook.xlsx`}, "Six-tab workbook"),
              el("a", {class: "btn ghost", href: `/api/quotes/${result.quote_id}/quote.pdf`}, "Quote PDF")),
            el("p", {class: "small muted", style: "margin-bottom:0"},
              "Workbook tabs: Product Summary, BOM, Packaging, Cost Summary, Review Flags, Customer Quote Summary."))))),
  );
  showView("result");
  window.scrollTo(0, 0);
}

function bomCard(result) {
  const accepted = result.ingredients.filter((line) => line.accepted);
  const sum = (key) => accepted.reduce((total, line) => total + (line[key] || 0), 0);

  const head = el("tr", {},
    el("th", {}, "Ingredient"), el("th", {}, "Status"), el("th", {class: "num"}, "Claimed mg"),
    el("th", {class: "num"}, "Potency"), el("th", {class: "num"}, "Overage"),
    el("th", {class: "num"}, "Formula mg"), el("th", {class: "num"}, "$/kg"),
    el("th", {class: "num"}, "$/bottle"), el("th", {class: "num"}, "Low"),
    el("th", {class: "num"}, "High"), el("th", {}, "Conf"), el("th", {}, "Match reason"));

  const rows = result.ingredients.map((line) => el("tr", {},
    el("td", {}, line.name),
    el("td", {}, badge(line.match.status, statusClass(line.match.status))),
    el("td", {class: "num"}, line.claimed_mg ?? el("span", {class: "excluded"}, "not stated")),
    el("td", {class: "num"}, line.potency),
    el("td", {class: "num"}, line.overage_pct == null ? "-" : `${(line.overage_pct * 100).toFixed(0)}%`),
    el("td", {class: "num"}, line.formula_mg_per_serving == null ? "-" : line.formula_mg_per_serving.toFixed(1)),
    costCell(line, line.cost_per_kg),
    costCell(line, line.cost_per_bottle),
    costCell(line, line.cost_low),
    costCell(line, line.cost_high),
    el("td", {}, badge(line.confidence, confClass(line.confidence))),
    el("td", {class: "small muted"}, line.match.reason)));

  const totals = el("tr", {class: "total"},
    el("td", {colspan: "7"}, "Total — Accepted only"),
    el("td", {class: "num"}, money4(sum("cost_per_bottle"))),
    el("td", {class: "num"}, money4(sum("cost_low"))),
    el("td", {class: "num"}, money4(sum("cost_high"))),
    el("td", {colspan: "2"}, ""));

  const table = el("table", {}, el("thead", {}, head), el("tbody", {}, rows, totals));
  return el("div", {class: "card"},
    el("h3", {}, `BOM — raw materials (${accepted.length} of ${result.ingredients.length} accepted)`),
    el("div", {class: "body tight scroll-x"}, table));
}

function packagingCard(result) {
  const accepted = result.packaging.filter((line) => line.accepted);
  const sum = (key) => accepted.reduce((total, line) => total + (line[key] || 0), 0);

  const head = el("tr", {},
    el("th", {}, "Role"), el("th", {}, "Description"), el("th", {}, "Status"),
    el("th", {class: "num"}, "Qty/bottle"), el("th", {class: "num"}, "Unit cost"),
    el("th", {class: "num"}, "$/bottle"), el("th", {class: "num"}, "Low"),
    el("th", {class: "num"}, "High"), el("th", {}, "Conf"), el("th", {}, "Match reason"));

  const rows = result.packaging.map((line) => el("tr", {},
    el("td", {class: "small"}, line.role.replace(/_/g, " ")),
    el("td", {}, line.description),
    el("td", {}, badge(line.match.status, statusClass(line.match.status))),
    el("td", {class: "num"}, line.qty_per_bottle ?? "-"),
    costCell(line, line.unit_cost),
    costCell(line, line.cost_per_bottle),
    costCell(line, line.cost_low),
    costCell(line, line.cost_high),
    el("td", {}, badge(line.confidence, confClass(line.confidence))),
    el("td", {class: "small muted"}, line.match.reason)));

  const totals = el("tr", {class: "total"},
    el("td", {colspan: "5"}, "Total — Accepted only"),
    el("td", {class: "num"}, money4(sum("cost_per_bottle"))),
    el("td", {class: "num"}, money4(sum("cost_low"))),
    el("td", {class: "num"}, money4(sum("cost_high"))),
    el("td", {colspan: "2"}, ""));

  const table = el("table", {}, el("thead", {}, head), el("tbody", {}, rows, totals));
  return el("div", {class: "card"},
    el("h3", {}, `Packaging (${accepted.length} of ${result.packaging.length} accepted)`),
    el("div", {class: "body tight scroll-x"}, table));
}

function manufacturingCard(manufacturing) {
  return el("div", {class: "card"},
    el("h3", {}, "Manufacturing"),
    el("div", {class: "body"},
      manufacturing.estimated
        ? el("dl", {class: "kv"},
            el("dt", {}, "Components in formula"), el("dd", {}, manufacturing.component_count),
            el("dt", {}, "Bottles in run"), el("dd", {}, num(manufacturing.bottles_in_run)),
            el("dt", {}, "Compounding hours"), el("dd", {}, manufacturing.compounding_hours),
            el("dt", {}, "Compounding $/bottle"), el("dd", {}, money4(manufacturing.compounding_per_bottle)),
            el("dt", {}, "Encapsulation $/bottle"), el("dd", {}, money4(manufacturing.encapsulation_per_bottle)),
            el("dt", {}, "Bottling $/bottle"), el("dd", {}, money4(manufacturing.bottling_per_bottle)),
            el("dt", {}, "Total $/bottle"), el("dd", {}, money(manufacturing.total_per_bottle)),
            el("dt", {}, "Machine"), el("dd", {}, manufacturing.machine_assumed ? `${manufacturing.machine} (assumed)` : manufacturing.machine))
        : el("div", {class: "alert warn"}, manufacturing.reason)));
}

function flagsCard(result) {
  const grouped = result.flags_by_owner;
  return el("div", {class: "card"},
    el("h3", {}, `Review flags (${result.flags.length})`),
    el("div", {class: "body tight"},
      Object.entries(grouped).map(([owner, flags]) => el("div", {},
        el("div", {class: "owner-head"}, el("span", {}, owner), el("span", {}, `${flags.length}`)),
        flags.length
          ? flags.map((flag) => el("div", {class: `flag-item ${flag.severity === "blocking" ? "blocking" : ""}`},
              el("span", {class: "dot"}),
              el("span", {class: "what"}, flag.item),
              el("span", {}, flag.reason)))
          : el("div", {class: "flag-item"}, el("span", {class: "muted"}, "No flags for this owner."))))));
}

// -------------------------------------------------------------- quotes
async function loadQuotes() {
  const target = $("#quotes-body");
  target.replaceChildren(el("p", {class: "loading", style: "padding:14px"}, "Loading…"));
  try {
    const quotes = await api("/api/quotes");
    if (!quotes.length) {
      target.replaceChildren(el("p", {class: "empty"}, "No quotes generated yet."));
      return;
    }
    target.replaceChildren(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Created"), el("th", {}, "Customer"), el("th", {}, "Formula"),
        el("th", {}, "Source"), el("th", {class: "num"}, "$/bottle"), el("th", {}, "Confidence"),
        el("th", {class: "num"}, "Unmatched"), el("th", {class: "num"}, "Needs review"),
        el("th", {class: "num"}, "Flags"), el("th", {}, "Package"))),
      el("tbody", {}, quotes.map((quote) => el("tr", {},
        el("td", {class: "small nowrap"}, new Date(quote.created_at).toLocaleString()),
        el("td", {}, quote.customer || "-"),
        el("td", {}, quote.formula_name || "-"),
        el("td", {class: "small muted"}, quote.source_format),
        el("td", {class: "num"}, money(quote.primary_per_bottle)),
        el("td", {}, badge(quote.quote_confidence, confClass(quote.quote_confidence))),
        el("td", {class: "num"}, quote.unmatched_count),
        el("td", {class: "num"}, quote.needs_review_count),
        el("td", {class: "num"}, quote.flag_count),
        el("td", {class: "nowrap"},
          el("button", {class: "btn small ghost", onclick: async () =>
            showResult(await api(`/api/quotes/${quote.quote_id}`))}, "Open"),
          " ",
          el("a", {class: "btn small ghost", href: `/api/quotes/${quote.quote_id}/workbook.xlsx`}, "xlsx"),
          " ",
          el("a", {class: "btn small ghost", href: `/api/quotes/${quote.quote_id}/quote.pdf`}, "pdf")))))));
  } catch (error) {
    target.replaceChildren(el("div", {class: "alert danger"}, String(error.message)));
  }
}

// ----------------------------------------------------------- reference
async function loadReference() {
  const target = $("#reference-body");
  try {
    const overview = await api("/api/reference/overview");
    const rates = overview.rates;
    mount(target,
      el("div", {class: "card"},
        el("h3", {}, "Rates and thresholds every quote is costed against"),
        el("div", {class: "body"},
          el("dl", {class: "kv"},
            el("dt", {}, "Labor rate"), el("dd", {}, `${money(rates.labor_rate_per_hour)}/hr`),
            el("dt", {}, "Overhead rate"), el("dd", {}, `${money(rates.overhead_rate_per_hour)}/hr`),
            el("dt", {}, "Combined"), el("dd", {}, `${money(rates.combined_rate_per_hour)}/hr`),
            el("dt", {}, "Default machine"), el("dd", {}, rates.default_machine),
            el("dt", {}, "Default mfg loss factor"), el("dd", {}, rates.default_mfg_loss_factor),
            el("dt", {}, "Cost range band"), el("dd", {}, `±${rates.cost_range_pct}%`),
            el("dt", {}, "Stale PO threshold"), el("dd", {}, `${rates.stale_po_days} days`),
            el("dt", {}, "Price variance flag"), el("dd", {}, `>${rates.price_variance_flag_pct}% of latest`),
            el("dt", {}, "UoM sanity ceiling"), el("dd", {}, `${money(rates.uom_sanity_max_per_bottle)}/bottle`),
            el("dt", {}, "Major-spend threshold"), el("dd", {}, `>${rates.major_spend_pct}% of primary`)))),

      el("div", {class: "card"},
        el("h3", {}, "Reference tables loaded"),
        el("div", {class: "body"},
          el("dl", {class: "kv"}, Object.entries(overview.counts).flatMap(([key, value]) =>
            [el("dt", {}, key.replace(/_/g, " ")), el("dd", {}, num(value))])),
          el("p", {class: "small muted", style: "margin-bottom:0"}, `Source: ${overview.source_dir}`))),

      el("div", {class: "card"},
        el("h3", {}, "Compounding hours by component count"),
        el("div", {class: "body"},
          el("dl", {class: "kv"}, Object.entries(overview.compounding_hours).flatMap(([key, value]) =>
            [el("dt", {}, key), el("dd", {}, `${value} hrs`)])))),

      el("div", {class: "card"},
        el("h3", {}, "Capsule fill capacity"),
        el("div", {class: "body"},
          el("dl", {class: "kv"}, Object.entries(overview.capsule_fill_mg).flatMap(([size, mg]) =>
            [el("dt", {}, `Size ${size}`), el("dd", {}, `${num(mg)} mg`)])))),

      el("div", {class: "card"},
        el("h3", {}, "Distinct-identity guard list — pairs that must never be matched"),
        el("div", {class: "body tight scroll-x"},
          el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "Identity A"), el("th", {}, "Identity B"), el("th", {}, "Reason"))),
            el("tbody", {}, overview.guard_pairs.map((entry) => el("tr", {},
              el("td", {}, entry.pair[0]), el("td", {}, entry.pair[1]),
              el("td", {class: "small muted"}, entry.reason))))))),

      el("div", {class: "card"},
        el("h3", {}, "Overage by class"),
        el("div", {class: "body tight scroll-x scroll-y"},
          el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "Class"), el("th", {class: "num"}, "Multi-ingredient"), el("th", {class: "num"}, "Single-ingredient"))),
            el("tbody", {}, Object.entries(overview.overage_classes).map(([name, values]) => el("tr", {},
              el("td", {}, name), el("td", {class: "num"}, `${values.multi_pct}%`),
              el("td", {class: "num"}, `${values.single_pct}%`))))))),
    );
  } catch (error) {
    target.replaceChildren(el("div", {class: "alert danger"}, String(error.message)));
  }
}

// ---------------------------------------------------------------- boot
async function boot() {
  try {
    const [health, ingredients, packaging, overview] = await Promise.all([
      api("/api/health"),
      api("/api/reference/ingredients"),
      api("/api/reference/packaging"),
      api("/api/reference/overview"),
    ]);
    state.ingredientCatalog = ingredients;
    state.packagingCatalog = packaging;

    $("#ref-status").textContent =
      `${num(health.reference_rows)} PO records · ${num(health.identities)} identities`;

    const sizes = Object.keys(overview.capsule_fill_mg);
    const select = $("#p-capsule_size");
    for (const size of sizes) select.append(el("option", {value: size}, `Size ${size}`));
    select.value = "00";

    renderIngredientOptions("");
    renderPackagingOptions("");
    renderFormula();
    renderPackaging();
  } catch (error) {
    $("#ref-status").textContent = "reference data failed to load";
    console.error(error);
  }
}

boot();
})();
