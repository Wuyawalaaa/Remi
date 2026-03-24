let activeStore    = "";
let activeCategory = "";
let activeSearch   = "";
let activeTab      = "deals";

const tabButtons = Array.from(document.querySelectorAll(".view-tab"));
const tabPanels  = Array.from(document.querySelectorAll(".tab-panel"));

function setActiveTab(tabName, syncHash = true) {
  activeTab = tabName === "assistant" ? "assistant" : "deals";

  tabButtons.forEach(button => {
    const isActive = button.dataset.tab === activeTab;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  tabPanels.forEach(panel => {
    const isActive = panel.dataset.panel === activeTab;
    panel.classList.toggle("active", isActive);
    panel.hidden = !isActive;
  });

  if (syncHash) {
    const nextHash = "#" + activeTab;
    if (window.location.hash !== nextHash) {
      history.replaceState(null, "", nextHash);
    }
  }
}

tabButtons.forEach(button => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});

window.addEventListener("hashchange", () => {
  const hashTab = window.location.hash === "#assistant" ? "assistant" : "deals";
  setActiveTab(hashTab, false);
});

// ── Filter chip clicks ────────────────────────────────────────────────────────

document.getElementById("store-filters").addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  document.querySelectorAll("#store-filters .chip").forEach(c => c.classList.remove("active"));
  chip.classList.add("active");
  activeStore = chip.dataset.value;
  loadDeals();
});

document.getElementById("category-filters").addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  document.querySelectorAll("#category-filters .chip").forEach(c => c.classList.remove("active"));
  chip.classList.add("active");
  activeCategory = chip.dataset.value;
  loadDeals();
});

// ── Search ────────────────────────────────────────────────────────────────────

document.getElementById("search-input").addEventListener("input", e => {
  activeSearch = e.target.value.trim().toLowerCase();
  loadDeals();
});

// ── Data loading ──────────────────────────────────────────────────────────────

async function loadDeals() {
  const params = new URLSearchParams();
  if (activeStore)    params.set("store",    activeStore);
  if (activeCategory) params.set("category", activeCategory);

  const res   = await fetch("/api/deals?" + params.toString());
  let   deals = await res.json();

  if (activeSearch) {
    deals = deals.filter(d => d.product_name.toLowerCase().includes(activeSearch));
  }

  renderDeals(deals);
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function formatSalePrice(deal) {
  if (deal.price_flyer != null) return "$" + parseFloat(deal.price_flyer).toFixed(2) + (deal.unit_flyer ? " / " + deal.unit_flyer : "");
  return "—";
}

function formatNormPrice(deal) {
  if (!deal.normalized_price || !deal.unit_norm) return "—";
  const unit = deal.unit_norm;
  const decimals = unit === "ml" ? 4 : 2;
  return "$" + parseFloat(deal.normalized_price).toFixed(decimals) + " / " + unit;
}

function renderDeals(deals) {
  const tbody      = document.getElementById("deals-tbody");
  const emptyState = document.getElementById("empty-state");
  const countEl    = document.getElementById("deal-count");

  tbody.innerHTML = "";

  if (deals.length === 0) {
    emptyState.style.display = "block";
    countEl.textContent = "0 deals";
    return;
  }

  emptyState.style.display = "none";
  countEl.textContent = deals.length + " deal" + (deals.length !== 1 ? "s" : "");

  deals.forEach(deal => {
    const tr = document.createElement("tr");

    const discPct = deal.discount_percentage
      ? Math.round(deal.discount_percentage)
      : (deal.original_price && deal.price_flyer && deal.original_price > deal.price_flyer
          ? Math.round((1 - deal.price_flyer / deal.original_price) * 100)
          : null);

    const tags = (deal.quality_tags || []).map(t => `<span class="quality-tag">${t}</span>`).join("");
    const namePrefix = deal.normalized_price == null ? "🚨 " : "";
    const nameHtml = namePrefix + deal.product_name + (tags ? `<div class="quality-tags" style="margin-top:4px">${tags}</div>` : "");

    const dealHtml = discPct
      ? `<span class="discount-badge">-${discPct}%</span>${deal.deal_type ? ` <span class="deal-cond">${deal.deal_type}</span>` : ""}`
      : (deal.deal_type ? `<span class="deal-cond">${deal.deal_type}</span>` : "—");

    const normHtml = deal.normalized_price
      ? formatNormPrice(deal)
      : `<span class="norm-missing">—</span>`;

    const origHtml = deal.original_price ? "$" + parseFloat(deal.original_price).toFixed(2) : "—";
    const validHtml = deal.valid_from && deal.valid_to
      ? deal.valid_from.slice(5) + " – " + deal.valid_to.slice(5)
      : "—";
    const packageLabel = deal.package_label || "—";
    const conditionsLabel = deal.deal_conditions || "—";

    tr.innerHTML = `
      <td><span class="store-badge">${deal.store_name}</span></td>
      <td class="col-product">${nameHtml}</td>
      <td><span class="category-tag">${deal.category || "—"}</span></td>
      <td class="col-pkg" title="${escapeHtml(packageLabel)}">${packageLabel}</td>
      <td class="col-price">${formatSalePrice(deal)}</td>
      <td class="col-orig">${origHtml}</td>
      <td>${dealHtml}</td>
      <td class="col-cond" title="${escapeHtml(conditionsLabel)}">${conditionsLabel}</td>
      <td class="col-norm">${normHtml}</td>
      <td class="col-valid">${validHtml}</td>
    `;
    tbody.appendChild(tr);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

// ── Initial load ──────────────────────────────────────────────────────────────
setActiveTab(window.location.hash === "#assistant" ? "assistant" : "deals", false);
loadDeals();

// ── Chat agent ────────────────────────────────────────────────────────────────

let chatSessionId = null;

const chatMessages  = document.getElementById("chat-messages");
const chatInput     = document.getElementById("chat-input");
const chatSend      = document.getElementById("chat-send");
const listPanel     = document.getElementById("shopping-list-panel");
const listContent   = document.getElementById("shopping-list-content");
const listClear     = document.getElementById("list-clear");

function appendBubble(text, role) {
  const div = document.createElement("div");
  div.className = "chat-bubble " + role;
  div.textContent = text;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return div;
}

function setLoading(on) {
  chatSend.disabled = on;
  chatInput.disabled = on;
  chatSend.textContent = on ? "…" : "Send";
}

function renderShoppingList(list) {
  if (!list || list.length === 0) return;
  listPanel.style.display = "block";
  listContent.innerHTML = "";

  let totalSave = 0;
  list.forEach(group => (group.items || []).forEach(item => { totalSave += parseFloat(item.save_amount) || 0; }));

  if (totalSave > 0) {
    const footer = document.createElement("div");
    footer.className = "list-savings-footer";
    footer.innerHTML = `Total savings <span>$${totalSave.toFixed(2)}</span>`;
    listContent.appendChild(footer);
  }

  // Merge groups with the same store name
  const merged = [];
  const storeIndex = {};
  list.forEach(group => {
    if (storeIndex[group.store] !== undefined) {
      merged[storeIndex[group.store]].items.push(...(group.items || []));
    } else {
      storeIndex[group.store] = merged.length;
      merged.push({ store: group.store, items: [...(group.items || [])] });
    }
  });
  merged.sort((a, b) => (a.store === "—") - (b.store === "—"));

  merged.forEach(group => {
    if (!group.items || group.items.length === 0) return;
    const section = document.createElement("div");
    section.className = "list-store-section";

    const heading = document.createElement("div");
    heading.className = "list-store-name";
    heading.textContent = group.store;
    section.appendChild(heading);

    (group.items || []).forEach(item => {
      const row = document.createElement("div");
      row.className = "list-item-row";

      const nameEl = document.createElement("span");
      nameEl.className = "list-item-name";
      nameEl.textContent = item.name;

      totalSave += parseFloat(item.save_amount) || 0;

      const priceEl = document.createElement("span");
      priceEl.className = "list-item-price";
      priceEl.textContent = item.price || "";

      row.appendChild(nameEl);
      row.appendChild(priceEl);

      if (item.note) {
        const noteEl = document.createElement("div");
        noteEl.className = "list-item-note";
        noteEl.textContent = item.note;
        row.appendChild(noteEl);
      }

      section.appendChild(row);
    });

    listContent.appendChild(section);
  });
}

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text) return;

  chatInput.value = "";
  appendBubble(text, "user");
  setLoading(true);

  const loadingEl = appendBubble("…", "assistant loading");

  try {
    const res  = await fetch("/api/chat", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ message: text, session_id: chatSessionId }),
    });
    const data = await res.json();

    if (data.error) {
      loadingEl.textContent = "Error: " + data.error;
      loadingEl.classList.add("error");
    } else {
      chatSessionId = data.session_id;
      loadingEl.textContent = data.reply;
      loadingEl.classList.remove("loading");
      renderShoppingList(data.shopping_list);
    }
  } catch (err) {
    loadingEl.textContent = "Network error. Try again.";
    loadingEl.classList.add("error");
  }

  setLoading(false);
}

chatSend.addEventListener("click", sendMessage);
chatInput.addEventListener("keydown", e => { if (e.key === "Enter") sendMessage(); });

listClear.addEventListener("click", () => {
  chatSessionId = null;
  listPanel.style.display = "none";
  listContent.innerHTML = "";
  chatMessages.innerHTML = "";
  appendBubble("Hi! Tell me what you'd like to buy and I'll build your shopping list with the best deals this week.", "assistant");
});
