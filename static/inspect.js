// Deals tab — parsed result, the text it came from, and what changed between
// parser versions. Three stacked rows per item:
//
//   第一行  every parsed field, always visible, plus its fault codes
//   第二行  the raw flyer text, full width, on open
//   第三行  the parse history, full width, on open
//
// Rows are built here rather than server-side. Rendering all 653 rows with their
// detail panels up front was 1.6 MB and ~26k nodes and the page stopped
// responding; expand-on-demand only pays off if the hidden half is not built.

const IN = { rows: [], catalog: {}, active: new Set(), view: [], shown: 0, PAGE: 60 };

const $ = id => document.getElementById(id);

const inEsc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const inNum = (v, unit) =>
  v == null ? null : "$" + Number(v).toFixed(unit === "ml" ? 4 : 2);

const inUnitPrice = r => r.normalized_price != null
  ? `${inNum(r.normalized_price, r.unit_norm)} / ${inEsc(r.unit_norm)}` : null;

const inPkg = r => {
  if (r.package_amount && r.per_unit_volume && r.package_amount !== 1)
    return `${r.package_amount} × ${r.per_unit_volume}`;
  if (r.per_unit_volume) return String(r.per_unit_volume);
  if (r.package_amount && r.package_amount !== 1) return `${r.package_amount} ct`;
  return null;
};

const inCell = v => v == null || v === "" ? '<span class="in-nil">—</span>' : inEsc(v);

// ── filtering ────────────────────────────────────────────────────────────────

function inCompute() {
  const store = $("in-store").value, state = $("in-state").value;
  const sort = $("in-sort").value, q = $("in-q").value.trim().toLowerCase();

  IN.view = IN.rows.filter(r => {
    if (store && r.store_name !== store) return false;
    if (state === "faulty" && !r.faults.length) return false;
    if (state === "clean" && r.faults.length) return false;
    if (state === "edited" && !r._edited) return false;
    for (const c of IN.active) if (!r.faults.some(f => f.code === c)) return false;
    if (q && !r._q.includes(q)) return false;
    return true;
  });

  const by = {
    "up-desc": (a, b) => (b.normalized_price ?? -1) - (a.normalized_price ?? -1),
    "up-asc": (a, b) => (a.normalized_price ?? 1e12) - (b.normalized_price ?? 1e12),
    "n-desc": (a, b) => b.faults.length - a.faults.length,
  }[sort];
  if (by) IN.view = [...IN.view].sort(by);

  IN.shown = IN.PAGE;
  inRender();
}

// ── 第一行 ───────────────────────────────────────────────────────────────────
// Ten fields, same information the old deals table carried. Truncated where it
// has to be, but present -- a row that shows four fields cannot tell you which
// of the other six is wrong.

function inTagsHTML(r) {
  const tags = r.faults.map(f => `
    <span class="in-tag ${f.origin === "manual" ? "manual" : /启发/.test(f.confidence) ? "guess" : ""}"
          title="${inEsc(f.rule)}${f.detail ? " — " + inEsc(f.detail) : ""}">
      ${inEsc(f.label)}<button class="in-x" data-act="dismiss" data-code="${f.code}" title="去掉这个码">×</button>
    </span>`).join("");

  const gone = (r.dismissed || []).map(f => `
    <span class="in-tag off" title="${inEsc(f.rule)}">
      ${inEsc(f.label)}<i>${inEsc(f.verdict_label)}</i><button class="in-x" data-act="undo" data-code="${f.code}" title="撤销">↺</button>
    </span>`).join("");

  return `<div class="in-tags">${tags}${gone}
    <button class="in-add" data-act="add" title="加一个故障码">+</button>
  </div>`;
}

function inRowHTML(r) {
  const up = inUnitPrice(r);
  return `<article class="in-item${r._edited ? " edited" : ""}" data-i="${r._i}">
    <div class="in-r1">
      <div class="c-store">${inEsc(r.store_name)}</div>
      <div class="c-name" title="${inEsc(r.product_name)}">${inEsc(r.product_name)}</div>
      <div class="c-cat">${inCell(r.category)}</div>
      <div class="c-pkg">${inCell(inPkg(r))}</div>
      <div class="c-price">${r.price_flyer != null ? "$" + r.price_flyer : '<span class="in-nil">—</span>'}${r.unit_flyer ? '<span class="in-u"> / ' + inEsc(r.unit_flyer) + "</span>" : ""}</div>
      <div class="c-orig">${r.original_price != null ? "$" + Number(r.original_price).toFixed(2) : '<span class="in-nil">—</span>'}</div>
      <div class="c-deal" title="${inEsc(r.deal_type)}">${inCell(r.deal_type)}</div>
      <div class="c-cond" title="${inEsc(r.deal_conditions)}">${inCell((r.deal_conditions || "").replace(/\s*\n\s*/g, " · "))}</div>
      <div class="c-up${up ? "" : " nil"}">${up || "无单价"}</div>
      <div class="c-valid">${inEsc(r.b_from)}<br>${inEsc(r.b_to)}</div>
      <div class="c-tags">${inTagsHTML(r)}</div>
    </div>
  </article>`;
}

// ── 第二行 and 第三行, each on its own full-width row, built on open ─────────

function inBodyHTML(r) {
  const up = inUnitPrice(r) || "—";
  const why = r.faults.map(f =>
    `<div><b>${inEsc(f.label)}</b> — ${inEsc(f.rule)}${f.detail ? "（" + inEsc(f.detail) + "）" : ""}</div>`
  ).join("");
  const codes = r.faults.length ? r.faults.map(f => inEsc(f.label)).join(" · ") : "无故障码";

  return `<div class="in-body">
    <section class="in-row2">
      <h4>第二行 · 原文</h4>
      <pre>${inEsc(r.deal_conditions) || "（这条没有任何描述文字）"}</pre>
      <div class="in-meta">
        <span>商品名原样：${inCell(r.product_name)}</span>
        <span>品牌：${inCell(r.brand)}</span>
        <span>促销字段：${inCell(r.deal_type)}</span>
      </div>
      ${why ? `<div class="in-why">${why}</div>` : ""}
    </section>

    <section class="in-row3">
      <h4>第三行 · 解析历史</h4>
      <div class="in-ver">
        <div class="in-vh">v1 · 基线 · 当前解析器（还没改过）</div>
        <div class="in-vv">${up} · ${codes} · <span class="in-dim">未裁定</span></div>
      </div>
      <div class="in-note">改一次解析器多一层，这里会累积成每个版本一条。裁定结果（误报／已解决）会记在对应的那一层上。</div>
    </section>
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

// ── editing fault codes ──────────────────────────────────────────────────────

async function inSave(deal_id, code, action, verdict) {
  const res = await fetch("/api/fault-mark", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ deal_id, code, action, verdict }),
  });
  if (!res.ok) {
    const msg = (await res.json()).error || "未知错误";
    const bar = $("in-count");
    bar.textContent = "保存失败：" + msg;
    bar.style.color = "#ff6b60";
    setTimeout(() => { bar.style.color = ""; inRender(); }, 4000);
    return false;
  }
  return true;
}

function inRedraw(r) {
  const el = document.querySelector(`.in-item[data-i="${r._i}"]`);
  if (!el) return;
  const wasOpen = !!el.querySelector(".in-body");
  el.outerHTML = inRowHTML(r);
  if (wasOpen) {
    const fresh = document.querySelector(`.in-item[data-i="${r._i}"]`);
    fresh.insertAdjacentHTML("beforeend", inBodyHTML(r));
  }
}

// A dismissal has to say which kind it is. Without that, "the code is gone"
// means both "the rule misfired" and "the fault was real and is handled", and
// the two together cannot produce a false-positive rate.
//
// Shown as an inline popover, not a modal dialog: a modal blocks the whole page
// until dismissed, and picking from a list is the interaction being asked for.

function inClosePop() { document.querySelector(".in-pop")?.remove(); }

function inPopover(anchor, items, onPick) {
  inClosePop();
  const pop = document.createElement("div");
  pop.className = "in-pop";
  pop.innerHTML = items.map(([v, label, hint]) =>
    `<button data-v="${inEsc(v)}">${inEsc(label)}${hint ? `<i>${inEsc(hint)}</i>` : ""}</button>`
  ).join("");

  const box = anchor.getBoundingClientRect();
  pop.style.left = Math.min(box.left + window.scrollX, window.innerWidth - 260) + "px";
  pop.style.top = box.bottom + window.scrollY + 5 + "px";
  document.body.appendChild(pop);

  pop.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    inClosePop();
    onPick(b.dataset.v);
  });
  setTimeout(() => document.addEventListener("click", inClosePop, { once: true }), 0);
}

const VERDICTS = [
  ["false_positive", "误报", "规则判错了，这条其实是对的"],
  ["resolved", "已解决", "故障是真的，已经处理掉了"],
];

function inHandleTagClick(r, btn) {
  const act = btn.dataset.act, code = btn.dataset.code;
  const done = () => { r._edited = true; inRedraw(r); };

  if (act === "dismiss") {
    inPopover(btn, VERDICTS, async verdict => {
      if (!await inSave(r.id, code, "remove", verdict)) return;
      const f = r.faults.find(x => x.code === code);
      r.faults = r.faults.filter(x => x.code !== code);
      (r.dismissed ||= []).push({ ...f, verdict,
        verdict_label: verdict === "false_positive" ? "误报" : "已解决" });
      done();
    });
  }

  if (act === "undo") {
    inSave(r.id, code, "clear").then(ok => {
      if (!ok) return;
      const f = (r.dismissed || []).find(x => x.code === code);
      r.dismissed = (r.dismissed || []).filter(x => x.code !== code);
      if (f) r.faults.push({ ...f, origin: "auto" });
      done();
    });
  }

  if (act === "add") {
    const taken = new Set(r.faults.map(f => f.code));
    const items = Object.entries(IN.catalog)
      .filter(([c]) => !taken.has(c))
      .map(([c, spec]) => [c, spec.label, ""]);
    if (!items.length) return;
    inPopover(btn, items, async code2 => {
      if (!await inSave(r.id, code2, "add")) return;
      r.faults.push({
        code: code2, label: IN.catalog[code2].label,
        rule: "手动添加——检测器没看出来", confidence: "手动",
        detail: "", origin: "manual",
      });
      done();
    });
  }
}

// ── wiring ───────────────────────────────────────────────────────────────────

function inInit() {
  const holder = $("in-data");
  if (!holder) return;

  IN.rows = JSON.parse(holder.textContent);
  IN.catalog = JSON.parse($("in-catalog").textContent);
  IN.rows.forEach((r, i) => {
    r._i = i;
    r._edited = r.faults.some(f => f.origin === "manual") || (r.dismissed || []).length > 0;
    r._q = [r.product_name, r.deal_conditions, r.brand, r.category]
      .filter(Boolean).join(" ").toLowerCase();
  });

  $("in-list").addEventListener("click", e => {
    const item = e.target.closest(".in-item");
    if (!item) return;
    const r = IN.rows[+item.dataset.i];

    const btn = e.target.closest("[data-act]");
    if (btn) { e.stopPropagation(); inHandleTagClick(r, btn); return; }
    if (e.target.closest(".in-body")) return;

    const open = item.querySelector(".in-body");
    if (open) open.remove();
    else item.insertAdjacentHTML("beforeend", inBodyHTML(r));
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
