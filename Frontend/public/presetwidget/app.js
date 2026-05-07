let DATA = null;

const sidebar = document.getElementById('sidebar');
const content = document.getElementById('content');
const search = document.getElementById('search');
const countEl = document.getElementById('count');

let activeCategory = null;
let searchTerm = '';

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderSidebar() {
  let html = '';
  DATA.categories.forEach(cat => {
    const cls = (cat.key === activeCategory && !searchTerm) ? 'cat-item active' : 'cat-item';
    html += `<div class="${cls}" data-cat="${cat.key}"><span>${escapeHtml(cat.label)}</span><span class="num">${cat.n_presets}</span></div>`;
  });
  sidebar.innerHTML = html;
  sidebar.querySelectorAll('[data-cat]').forEach(el => {
    el.addEventListener('click', () => {
      activeCategory = el.getAttribute('data-cat');
      searchTerm = '';
      search.value = '';
      render();
    });
  });
}

function presetPill(name) {
  const meta = DATA.preset_meta[name];
  const tooltip = meta ? ` title="${escapeHtml(meta)}"` : '';
  const cls = meta ? 'preset has-desc' : 'preset';
  return `<span class="${cls}"${tooltip}>${escapeHtml(name)}</span>`;
}

function renderCategory(cat) {
  let html = `<div class="cat-header">
    <h2>${escapeHtml(cat.label)}</h2>
    <p class="hint">${escapeHtml(cat.hint)}</p>
    <p class="meta">${cat.n_presets} presets across ${cat.n_plugins} plugins</p>
  </div>`;
  cat.plugins.forEach(plug => {
    html += `<div class="plugin-card">
      <div class="plugin-header">
        <span class="plugin-name">${escapeHtml(plug.plugin)}</span>
        <span class="plugin-role">${escapeHtml(plug.role)}</span>
      </div>
      <div class="preset-list">${plug.presets.map(presetPill).join('')}</div>
    </div>`;
  });
  return html;
}

function renderSearch() {
  const term = searchTerm.toLowerCase();
  let html = `<div class="cat-header"><h2>Search results</h2><p class="hint">Matches across all categories</p></div>`;
  let total = 0;
  DATA.categories.forEach(cat => {
    const hits = [];
    cat.plugins.forEach(plug => {
      const matching = plug.presets.filter(p => p.toLowerCase().includes(term));
      if (matching.length) hits.push({ plug, matching });
    });
    if (!hits.length) return;
    html += `<div class="search-section"><div class="search-section-label">${escapeHtml(cat.label)}</div>`;
    hits.forEach(h => {
      html += `<div class="plugin-card">
        <div class="plugin-header">
          <span class="plugin-name">${escapeHtml(h.plug.plugin)}</span>
          <span class="plugin-role">${escapeHtml(h.plug.role)}</span>
        </div>
        <div class="preset-list">${h.matching.map(presetPill).join('')}</div>
      </div>`;
      total += h.matching.length;
    });
    html += `</div>`;
  });
  if (total === 0) html += `<p class="empty">No presets match "${escapeHtml(searchTerm)}".</p>`;
  countEl.textContent = `${total} preset${total === 1 ? '' : 's'}`;
  return html;
}

function render() {
  if (!DATA) return;
  if (searchTerm) {
    content.innerHTML = renderSearch();
  } else {
    const cat = DATA.categories.find(c => c.key === activeCategory) || DATA.categories[0];
    activeCategory = cat.key;
    content.innerHTML = renderCategory(cat);
    countEl.textContent = `${cat.n_presets} preset${cat.n_presets === 1 ? '' : 's'}`;
  }
  renderSidebar();
}

let searchDebounce;
search.addEventListener('input', e => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    searchTerm = e.target.value.trim();
    render();
  }, 80);
});

fetch('data.json')
  .then(r => r.json())
  .then(d => {
    DATA = d;
    activeCategory = d.categories[0].key;
    render();
  })
  .catch(err => {
    content.innerHTML = `<p class="empty">Failed to load preset data: ${escapeHtml(err.message)}</p>`;
  });
