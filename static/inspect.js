// Deals tab — parsed result next to the text it was parsed from.
//
// Rows are built here rather than server-side. Rendering all 653 rows with their
// detail panels up front was 1.6 MB and ~26k nodes and the page stopped
// responding; the expand-on-demand design only pays off if the hidden half is
// actually not built. Rows 2 and 3 are constructed the moment an item is opened.

const IN = {
  rows: [],
  active: new Set(),   // selected fault codes
  view: [],
  shown: 0,
  PAGE: 60,
};

const $ = id => document.getElementById(id);

const inEsc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function inMoney(v, unit) {
  if (v == null) return null;
  const decimals = unit === "ml" ? 4 : 2;
  return "$" + Number(v).toFixed(decimals);
}

function inUnitPrice(r) {
  return r.normalized_price != null
    ? `${inMoney(r.normalized_price, r.unit_norm)} / ${inEsc(r.unit_norm)}`
    : null;
}

// ── filtering ────────────────────────────────────────────────────────────────

function inCompute() {
  const store = $("in-store").value;
  const state = $("in-state").value;
  const sort  = $("in-sort").value;
  const q     = $("in-q").value.trim().toLowerCase();

  IN.view = IN.rows.filter(r => {
    if (store && r.store_name !== store) return false;
    if (state === "faulty" && !r._codes.length) return false;
    if (state === "clean"  &&  r._codes.length) return false;
    for (const c of IN.active) if (!r._codes.includes(c)) return false;
    if (q && !r._q.includes(q)) return false;
    return true;
  });

  const by = {
    "up-desc": (a, b) => (b.normalized_price ?? -1) - (a.normalized_price ?? -1),
    "up-asc":  (a, b) => (a.normalized_price ?? 1e12) - (b.normalized_price ?? 1e12),
    "n-desc":  (a, b) => b.faults.length - a.faults.length,
  }[sort];
  if (by) IN.view = [...IN.view].sort(by);

  IN.shown = IN.PAGE;
  inRender();
}

// ── row 1 ────────────────────────────────────────────────────────────────────

function inRowHTML(r) {
  const tags = r.faults.length
    ? r.faults.map(f =>
        `<span class="in-tag${/启发/.test(f.confidence) ? " guess" : ""}" title="${inEsc(f.rule)}">${inEsc(f.label)}</span>`
      ).join("")
    : '<span class="in-clean">无故障码</span>';

  const up = inUnitPrice(r);
  const priced = r.price_flyer != null ? "$" + r.price_flyer : "—";
  const size = r.per_unit_volume ? " · " + inEsc(r.per_unit_volume) : "";

  return `<article class="in-item" data-i="${r._i}">
    <div class="in-r1">
      <div>
        <span class="in-name">${inEsc(r.product_name)}</span>
        <span class="in-store">${inEsc(r.store_name)}</span>
        <div class="in-tags">${tags}</div>
      </div>
      <div class="in-parsed">${priced} / ${inEsc(r.unit_flyer) || "—"}${size}</div>
      <div class="in-up${up ? "" : " none"}">${up || "无单价"}</div>
    </div>
  </article>`;
}

// ── rows 2 and 3, built on open ──────────────────────────────────────────────

function inBodyHTML(r) {
  const up = inUnitPrice(r) || "—";
  const why = r.faults.map(f =>
    `<div><b>${inEsc(f.label)}</b> — ${inEsc(f.rule)}${f.detail ? "（" + inEsc(f.detail) + "）" : ""}</div>`
  ).join("");
  const codes = r.faults.length ? r.faults.map(f => inEsc(f.label)).join(" · ") : "无故障码";

  return `<div class="in-body">
    <div class="in-box">
      <h4>第二行 · 原文</h4>
      <pre>${inEsc(r.deal_conditions) || "（这条没有任何描述文字）"}</pre>
      ${r.brand ? `<div class="in-note">品牌字段：${inEsc(r.brand)}</div>` : ""}
      ${r.deal_type ? `<div class="in-note">促销字段：${inEsc(r.deal_type)}</div>` : ""}
    </div>
    <div class="in-box">
      <h4>第一行 · 解析结果</h4>
      <dl class="in-kv">
        <dt>价格</dt><dd>${r.price_flyer ?? "—"}</dd>
        <dt>单位</dt><dd>${inEsc(r.unit_flyer) || "—"}</dd>
        <dt>包装规格</dt><dd>${inEsc(r.per_unit_volume) || "（空）"}</dd>
        <dt>数量</dt><dd>${r.package_amount ?? "—"}</dd>
        <dt>算出单价</dt><dd>${up}</dd>
        <dt>有效期</dt><dd>${inEsc(r.b_from)} → ${inEsc(r.b_to)}</dd>
      </dl>
      ${why ? `<div class="in-why">${why}</div>` : ""}
    </div>
    <details class="in-hist">
      <summary>第三行 · 解析历史（1 个版本）▾</summary>
      <div class="in-ver">
        <div class="in-vh">v1 · 基线 · 当前解析器（还没改过）</div>
        <div class="in-vv">${up} · ${codes} · <span class="in-dim">未裁定</span></div>
      </div>
      <div class="in-note">改一次解析器多一层。裁定（确认／误报）和手动增删故障码还没做——先看这版检测得准不准。</div>
    </details>
  </div>`;
}

function inRender() {
  const slice = IN.view.slice(0, IN.shown);
  $("in-list").innerHTML = slice.map(inRowHTML).join("");
  $("in-count").textContent = `${IN.view.length} / ${IN.rows.length} 条`;
  $("in-empty").hidden = IN.view.length > 0;

  const rest = IN.view.length - slice.length;
  $("in-more").hidden = rest <= 0;
  $("in-more").textContent = `再显示 ${Math.min(rest, IN.PAGE)} 条（还剩 ${rest}）`;
}

// ── wiring ───────────────────────────────────────────────────────────────────

function inInit() {
  const holder = $("in-data");
  if (!holder) return;

  IN.rows = JSON.parse(holder.textContent);
  IN.rows.forEach((r, i) => {
    r._i = i;
    r._codes = r.faults.map(f => f.code);
    r._q = [r.product_name, r.deal_conditions, r.brand].filter(Boolean).join(" ").toLowerCase();
  });

  $("in-list").addEventListener("click", e => {
    const item = e.target.closest(".in-item");
    if (!item || e.target.closest(".in-hist")) return;
    const open = item.querySelector(".in-body");
    if (open) { open.remove(); return; }
    item.insertAdjacentHTML("beforeend", inBodyHTML(IN.rows[+item.dataset.i]));
  });

  document.querySelectorAll(".in-chip").forEach(c =>
    c.addEventListener("click", () => {
      const code = c.dataset.code;
      IN.active.has(code) ? IN.active.delete(code) : IN.active.add(code);
      c.setAttribute("aria-pressed", IN.active.has(code));
      inCompute();
    })
  );

  ["in-store", "in-state", "in-sort"].forEach(id => $(id).addEventListener("change", inCompute));
  $("in-q").addEventListener("input", inCompute);
  $("in-more").addEventListener("click", () => { IN.shown += IN.PAGE; inRender(); });
  $("in-reset").addEventListener("click", () => {
    IN.active.clear();
    document.querySelectorAll(".in-chip").forEach(c => c.setAttribute("aria-pressed", false));
    ["in-store", "in-state", "in-sort", "in-q"].forEach(id => ($(id).value = ""));
    inCompute();
  });

  inCompute();
}

inInit();
