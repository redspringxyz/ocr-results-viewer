// viewer.js — fetches data/outputs.json and renders the side-by-side grid.
// Vanilla ES module, no build step, no framework.

const DATA_URL = "./data/outputs.json";

const ALLOWED_TAGS = new Set([
  "a",
  "b",
  "blockquote",
  "br",
  "caption",
  "code",
  "div",
  "em",
  "figcaption",
  "figure",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "i",
  "img",
  "li",
  "ol",
  "p",
  "pre",
  "span",
  "strong",
  "sub",
  "sup",
  "table",
  "tbody",
  "td",
  "tfoot",
  "th",
  "thead",
  "tr",
  "u",
  "ul",
]);

// Minimal per-tag attribute allowlist. Anything not listed is stripped.
// We intentionally allow a handful of layout hints (colspan/rowspan, alt/src
// on <img>, href on <a>) so OCR tables and figures render. This is
// defense-in-depth only; the real trust model is "the exporter controls what
// lands in outputs.json". Never relax enough to permit event handlers or
// javascript: URLs.
const ALLOWED_ATTRS = {
  a: ["href", "title"],
  img: ["src", "alt", "title"],
  td: ["colspan", "rowspan", "align"],
  th: ["colspan", "rowspan", "align", "scope"],
  col: ["span"],
  colgroup: ["span"],
};

const URL_ATTRS = new Set(["href", "src"]);

function isSafeUrl(value) {
  if (typeof value !== "string") return false;
  const trimmed = value.trim().toLowerCase();
  if (
    trimmed.startsWith("javascript:") ||
    trimmed.startsWith("data:") ||
    trimmed.startsWith("vbscript:")
  ) {
    return false;
  }
  return true;
}

function sanitizeHtml(dirty) {
  if (!dirty) return "";
  const template = document.createElement("template");
  template.innerHTML = String(dirty);
  walkAndClean(template.content);
  return template.innerHTML;
}

function walkAndClean(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  const toRemove = [];
  let node = walker.nextNode();
  while (node) {
    const tag = node.tagName.toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) {
      toRemove.push(node);
    } else {
      const allowedForTag = ALLOWED_ATTRS[tag] || [];
      // iterate over a copy — attributes is a live NamedNodeMap
      for (const attr of Array.from(node.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith("on")) {
          node.removeAttribute(attr.name);
          continue;
        }
        if (!allowedForTag.includes(name)) {
          node.removeAttribute(attr.name);
          continue;
        }
        if (URL_ATTRS.has(name) && !isSafeUrl(attr.value)) {
          node.removeAttribute(attr.name);
        }
      }
    }
    node = walker.nextNode();
  }
  for (const el of toRemove) {
    // Preserve text content of stripped tags (like <script> body we threw out)
    // only when safe — for script/style/iframe we drop entirely.
    const destructive = ["script", "style", "iframe", "object", "embed"];
    if (destructive.includes(el.tagName.toLowerCase())) {
      el.remove();
    } else {
      el.replaceWith(...el.childNodes);
    }
  }
}

function escapeText(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatMs(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(tokens) {
  if (typeof tokens !== "number" || !Number.isFinite(tokens)) return "—";
  return `${tokens} tok`;
}

const state = {
  data: null,
  pageIndex: 0,
};

const els = {
  source: document.getElementById("source-line"),
  arxiv: document.getElementById("page-arxiv"),
  pageNo: document.getElementById("page-number"),
  category: document.getElementById("page-category"),
  image: document.getElementById("page-image"),
  grid: document.getElementById("model-grid"),
  prev: document.getElementById("prev-btn"),
  next: document.getElementById("next-btn"),
  shuffle: document.getElementById("shuffle-btn"),
  picker: document.getElementById("page-picker"),
};

async function main() {
  try {
    const res = await fetch(DATA_URL, { cache: "no-cache" });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    state.data = await res.json();
  } catch (err) {
    els.source.textContent = `Failed to load ${DATA_URL}: ${err.message}`;
    return;
  }

  if (!state.data || !Array.isArray(state.data.pages) || state.data.pages.length === 0) {
    els.source.textContent = "No pages in outputs.json.";
    return;
  }

  const generatedAt = state.data.generated_at || "unknown date";
  const source = state.data.source || "unknown";
  els.source.textContent = `${state.data.pages.length} pages × ${state.data.models.length} models · generated ${generatedAt} · source=${source}`;

  populatePicker();
  wireControls();
  render();
}

function populatePicker() {
  els.picker.innerHTML = "";
  state.data.pages.forEach((page, idx) => {
    const opt = document.createElement("option");
    opt.value = String(idx);
    opt.textContent = `${idx + 1}. ${page.pdf || page.id} — p${page.page ?? "?"}`;
    els.picker.appendChild(opt);
  });
}

function wireControls() {
  els.prev.addEventListener("click", () => go(-1));
  els.next.addEventListener("click", () => go(1));
  els.shuffle.addEventListener("click", shuffle);
  els.picker.addEventListener("change", (e) => {
    const idx = Number.parseInt(e.target.value, 10);
    if (Number.isFinite(idx)) {
      state.pageIndex = idx;
      render();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLSelectElement || e.target instanceof HTMLInputElement) {
      return;
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      go(-1);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      go(1);
    } else if (e.key === "s" || e.key === "S") {
      e.preventDefault();
      shuffle();
    }
  });
}

function go(delta) {
  const total = state.data.pages.length;
  state.pageIndex = (state.pageIndex + delta + total) % total;
  render();
}

function shuffle() {
  const total = state.data.pages.length;
  if (total <= 1) return;
  let next = state.pageIndex;
  while (next === state.pageIndex) {
    next = Math.floor(Math.random() * total);
  }
  state.pageIndex = next;
  render();
}

function render() {
  const page = state.data.pages[state.pageIndex];
  if (!page) return;

  els.picker.value = String(state.pageIndex);
  els.arxiv.textContent = page.pdf || page.id;
  els.pageNo.textContent = String(page.page ?? "?");
  els.category.textContent = page.primary_code || "—";
  els.image.src = page.image;
  els.image.alt = `Rendered page ${page.page ?? "?"} of ${page.pdf || page.id}`;

  renderModelGrid(page);
}

function renderModelGrid(page) {
  els.grid.innerHTML = "";
  const models = state.data.models || [];
  const outputs = page.outputs || {};

  for (const slug of models) {
    const card = buildCard(slug, outputs[slug]);
    els.grid.appendChild(card);
  }
}

function buildCard(slug, output) {
  const card = document.createElement("article");
  card.className = "model-card";
  card.dataset.model = slug;

  const header = document.createElement("header");
  header.className = "model-card__header";

  const title = document.createElement("h3");
  title.className = "model-card__title";
  title.textContent = slug;
  header.appendChild(title);

  const badges = document.createElement("div");
  badges.className = "model-card__badges";
  badges.append(...buildBadges(output));
  header.appendChild(badges);

  card.appendChild(header);

  const toggle = document.createElement("div");
  toggle.className = "model-card__toggle";
  toggle.innerHTML = `
    <span>View:</span>
    <button type="button" data-mode="html" aria-pressed="true">Rendered</button>
    <button type="button" data-mode="text" aria-pressed="false">Raw</button>
  `;
  card.appendChild(toggle);

  const body = document.createElement("div");
  body.className = "model-card__body";
  card.appendChild(body);

  const state = { mode: "html", output };
  fillBody(body, state);

  toggle.addEventListener("click", (e) => {
    const target = e.target instanceof HTMLButtonElement ? e.target : null;
    if (!target || !target.dataset.mode) return;
    const mode = target.dataset.mode;
    for (const btn of toggle.querySelectorAll("button")) {
      btn.setAttribute("aria-pressed", btn === target ? "true" : "false");
    }
    state.mode = mode;
    fillBody(body, state);
  });

  return card;
}

function buildBadges(output) {
  const out = [];
  if (!output) {
    const miss = document.createElement("span");
    miss.className = "badge badge--error";
    miss.textContent = "no output";
    out.push(miss);
    return out;
  }
  if (output.error) {
    const err = document.createElement("span");
    err.className = "badge badge--error";
    err.textContent = `error: ${output.error}`;
    out.push(err);
  }
  if (typeof output.latency_ms === "number") {
    const lat = document.createElement("span");
    const slow = output.latency_ms >= 10000;
    lat.className = `badge${slow ? " badge--slow" : ""}`;
    lat.textContent = formatMs(output.latency_ms);
    out.push(lat);
  }
  if (typeof output.tokens === "number") {
    const tok = document.createElement("span");
    tok.className = "badge";
    tok.textContent = formatTokens(output.tokens);
    out.push(tok);
  }
  return out;
}

function fillBody(body, cardState) {
  const { mode, output } = cardState;
  body.classList.remove("model-card__body--empty", "model-card__body--error");

  if (!output) {
    body.classList.add("model-card__body--empty");
    body.textContent = "(no output)";
    return;
  }

  if (output.error && !output.html && !output.text) {
    body.classList.add("model-card__body--error");
    body.textContent = `endpoint error: ${output.error}`;
    return;
  }

  if (mode === "text") {
    const pre = document.createElement("pre");
    pre.textContent = output.text || output.html || "(empty)";
    body.replaceChildren(pre);
    if (!output.text && !output.html) {
      body.classList.add("model-card__body--empty");
    }
    return;
  }

  // "html" mode — pass through sanitizer, fall back to escaped text.
  const html = output.html;
  if (!html) {
    if (output.text) {
      const pre = document.createElement("pre");
      pre.textContent = output.text;
      body.replaceChildren(pre);
    } else {
      body.classList.add("model-card__body--empty");
      body.textContent = "(empty output)";
    }
    return;
  }

  try {
    body.innerHTML = sanitizeHtml(html);
  } catch (err) {
    body.classList.add("model-card__body--error");
    body.textContent = `render failure: ${err.message}. Raw:\n${escapeText(html)}`;
  }
}

main();
