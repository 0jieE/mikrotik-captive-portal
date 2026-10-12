(function () {
  // ---- DataTables -------------------------------------------------------------------
  var tables = {};
  if (window.jQuery && jQuery.fn.DataTable) {
    jQuery('table.datatable').each(function () {
      var el = jQuery(this);
      tables[this.id || ('t' + Object.keys(tables).length)] = el.DataTable({
        pageLength: parseInt(el.attr('data-page-length') || 25, 10),
        order: [],
      });
    });
  }

  // ---- select-all + bulk form (works across DataTables pages) ------------------------
  var selectAll = document.getElementById('select-all');
  var instructorsTable = tables['instructors-table'];
  function rowChecks(onlyFiltered) {
    if (instructorsTable) {
      var nodes = instructorsTable.rows(onlyFiltered ? { search: 'applied' } : {}).nodes();
      return jQuery(nodes).find('.row-check').toArray();
    }
    return Array.prototype.slice.call(document.querySelectorAll('.row-check'));
  }
  function updateCount() {
    var n = rowChecks(false).filter(function (c) { return c.checked; }).length;
    var out = document.getElementById('selected-count');
    if (out) out.textContent = n;
  }
  if (selectAll) {
    selectAll.addEventListener('change', function () {
      rowChecks(true).forEach(function (c) { c.checked = selectAll.checked; });
      updateCount();
    });
  }
  document.addEventListener('change', function (e) {
    if (e.target.classList && e.target.classList.contains('row-check')) updateCount();
  });
  var bulkForm = document.getElementById('bulk-form');
  if (bulkForm) {
    bulkForm.addEventListener('submit', function () {
      bulkForm.querySelectorAll('input[name=ids]').forEach(function (i) { i.remove(); });
      rowChecks(false).filter(function (c) { return c.checked; }).forEach(function (c) {
        var h = document.createElement('input');
        h.type = 'hidden'; h.name = 'ids'; h.value = c.value;
        bulkForm.appendChild(h);
      });
    });
    var actionSel = document.getElementById('bulk-action');
    var policySel = document.getElementById('bulk-policy');
    if (actionSel && policySel) {
      var toggle = function () { policySel.classList.toggle('d-none', actionSel.value !== 'policy'); };
      actionSel.addEventListener('change', toggle); toggle();
    }
  }

  // ---- confirmation modal for [data-confirm] forms/buttons ---------------------------
  var modalEl = document.getElementById('confirmModal');
  if (modalEl && window.bootstrap) {
    var modal = new bootstrap.Modal(modalEl);
    var pending = null;
    document.addEventListener('click', function (e) {
      var trigger = e.target.closest('[data-confirm]');
      if (!trigger) return;
      var form = trigger.closest('form');
      if (!form && trigger.tagName !== 'A') return;
      e.preventDefault();
      pending = { form: form, link: trigger.tagName === 'A' ? trigger.href : null, submitter: trigger };
      modalEl.querySelector('.modal-title').textContent = trigger.getAttribute('data-confirm-title') || 'Please confirm';
      modalEl.querySelector('.modal-body').textContent = trigger.getAttribute('data-confirm');
      var btn = modalEl.querySelector('#confirmOk');
      btn.textContent = trigger.getAttribute('data-confirm-btn') || 'Confirm';
      btn.className = 'btn ' + (trigger.getAttribute('data-confirm-class') || 'btn-danger');
      modal.show();
    });
    modalEl.querySelector('#confirmOk').addEventListener('click', function () {
      modal.hide();
      if (!pending) return;
      if (pending.link) { window.location = pending.link; }
      else if (pending.form.requestSubmit && pending.submitter.type === 'submit') { pending.form.requestSubmit(pending.submitter); }
      else { pending.form.submit(); }
      pending = null;
    });
  }

  // ---- Chart.js helper (theme: forest / emerald) ------------------------------------------------
  window.cdkChart = function (id, type, labels, data, label, color) {
    var el = document.getElementById(id);
    if (!el || !window.Chart) return;
    var main = color || '#2f8f5a';
    var fill = type === 'line' ? 'rgba(47,143,90,.14)' : (color || 'rgba(47,143,90,.85)');
    new Chart(el, {
      type: type,
      data: { labels: labels, datasets: [{ label: label, data: data, backgroundColor: fill, borderColor: main,
        borderWidth: type === 'line' ? 2.5 : 0, borderRadius: type === 'bar' ? 6 : 0, tension: 0.35, fill: type === 'line',
        pointRadius: type === 'line' ? 2 : 0, pointBackgroundColor: main }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { grid: { display: false }, ticks: { color: '#667563' } },
                  y: { beginAtZero: true, grid: { color: '#e9efe6' }, ticks: { color: '#667563' } } } },
    });
  };

  // ---- Sidebar: collapse to an icon rail (desktop) / slide-in drawer (tablet + phone) ----------
  var root = document.documentElement;
  var toggle = document.getElementById('sbToggle');
  var backdrop = document.getElementById('sbBackdrop');
  var sidebar = document.getElementById('appSidebar');
  if (toggle && sidebar) {
    var KEY = 'cdk.sidebar';
    var desktop = window.matchMedia('(min-width: 992px)');
    var sync = function () {
      var expanded = desktop.matches ? !root.classList.contains('sb-collapsed') : root.classList.contains('sb-open');
      toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    };
    var closeDrawer = function () { root.classList.remove('sb-open'); sync(); };
    toggle.addEventListener('click', function () {
      if (desktop.matches) {
        var collapsed = root.classList.toggle('sb-collapsed');
        try { localStorage.setItem(KEY, collapsed ? 'collapsed' : 'expanded'); } catch (e) {}
      } else {
        root.classList.toggle('sb-open');
      }
      sync();
    });
    if (backdrop) backdrop.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && root.classList.contains('sb-open')) { closeDrawer(); toggle.focus(); } });
    sidebar.addEventListener('click', function (e) { if (!desktop.matches && e.target.closest('a')) closeDrawer(); });
    var onChange = function () { if (desktop.matches) root.classList.remove('sb-open'); sync(); };
    if (desktop.addEventListener) desktop.addEventListener('change', onChange); else desktop.addListener(onChange);
    sync();
    // enable transitions only after first paint (no flash of animation on page load)
    window.requestAnimationFrame(function () { window.requestAnimationFrame(function () { root.classList.remove('no-anim'); }); });
  }
})();
