/*
 * Ariston schedule card — weekly comfort/eco editor for one heating time
 * program (Func/TProg), for the `ariston_local` integration.
 *
 * Local-first by design: the boiler owns the schedule. The card renders what
 * the integration last read from the boiler (the time-program sensor) and, on
 * Save, writes your edits through `ariston_local.set_time_program`. It never
 * keeps a copy of its own: reload and you see the boiler's truth.
 *
 * Dependency-free (no build step, no Lit): add it as a Lovelace resource.
 *
 *   type: custom:ariston-schedule-card
 *   entity: sensor.ariston_gateway_XXXX_time_program_1   # the time-program sensor
 *   program_id: 1          # optional, defaults to the entity id's trailing number
 *   title: Programme       # optional
 *   show_setpoints: true   # optional, false hides the comfort/eco steppers
 */

const MIN_TEMP = 5;   // set_time_program accepts 5..35 °C
const MAX_TEMP = 35;
const DAY = 1440;     // minutes

const STRINGS = {
  en: {
    title: "Heating schedule",
    days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    comfort: "Comfort", eco: "Eco",
    now_comfort: "Comfort until {t}", now_eco: "Eco until {t}",
    now_eco_day: "Eco for the rest of the day",
    all_eco: "Eco all day",
    add: "Add a period", remove: "Remove this period", close: "Close",
    copy_to: "Copy this day to", weekdays: "Mon–Fri", weekend: "Weekend",
    all_week: "Whole week", copied: "Copied",
    unsaved: "Unsaved changes", cancel: "Cancel", save: "Save",
    saving: "Saving…", saved: "Schedule saved to the boiler",
    syncing: "Waiting for the boiler…",
    kept: "The boiler kept a different schedule",
    changed: "Changed on the boiler meanwhile — Save overwrites it, Cancel reloads it",
    error: "Could not save: {e}", unavailable: "Time program unavailable",
    edit_day: "Edit {d}", end_note: "Periods end at 23:45 at the latest.",
  },
  fr: {
    title: "Programme de chauffage",
    days: ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    comfort: "Confort", eco: "Éco",
    now_comfort: "Confort jusqu'à {t}", now_eco: "Éco jusqu'à {t}",
    now_eco_day: "Éco jusqu'à la fin de la journée",
    all_eco: "Éco toute la journée",
    add: "Ajouter une plage", remove: "Supprimer cette plage", close: "Fermer",
    copy_to: "Copier ce jour vers", weekdays: "Lun–Ven", weekend: "Week-end",
    all_week: "Toute la semaine", copied: "Copié",
    unsaved: "Modifications non enregistrées", cancel: "Annuler",
    save: "Enregistrer", saving: "Enregistrement…",
    saved: "Programme enregistré sur la chaudière",
    syncing: "En attente de la chaudière…",
    kept: "La chaudière a conservé un programme différent",
    changed: "Modifié entre-temps sur la chaudière — Enregistrer l'écrase, Annuler le recharge",
    error: "Échec de l'enregistrement : {e}", unavailable: "Programme indisponible",
    edit_day: "Modifier {d}", end_note: "Une plage se termine au plus tard à 23:45.",
  },
};

// --- pure schedule model ----------------------------------------------------
// week: 7 arrays (Mon..Sun) of [startMin, endMin] comfort windows.

const toMin = (s) => {
  const [h, m] = String(s).split(":").map(Number);
  return h * 60 + m;
};
const fmt = (min) =>
  `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

/** Snap to the slot grid, order, and merge overlapping/touching windows. */
function normalize(windows, slot) {
  const snapped = windows
    .map(([a, b]) => {
      const s = Math.min(Math.max(Math.round(a / slot) * slot, 0), DAY - slot);
      const e = Math.min(Math.max(Math.round(b / slot) * slot, s + slot), DAY);
      return [s, e];
    })
    .sort((x, y) => x[0] - y[0]);
  const out = [];
  for (const w of snapped) {
    const last = out[out.length - 1];
    if (last && w[0] <= last[1]) last[1] = Math.max(last[1], w[1]);
    else out.push([...w]);
  }
  return out;
}

function weekFromAttrs(attrs, slot) {
  const week = Array.from({ length: 7 }, () => []);
  for (const prof of attrs.profiles || [])
    for (const d of prof.days || [])
      for (const [a, b] of prof.comfort_windows || [])
        if (week[d - 1]) week[d - 1].push([toMin(a), toMin(b)]);
  return week.map((w) => normalize(w, slot));
}

/** Days with identical windows share one profile, as the boiler stores them. */
function profilesOf(week) {
  const byKey = new Map();
  week.forEach((windows, i) => {
    const key = JSON.stringify(windows);
    if (!byKey.has(key)) byKey.set(key, { days: [], windows: windows.map(([a, b]) => [fmt(a), fmt(b)]) });
    byKey.get(key).days.push(i + 1);
  });
  return [...byKey.values()];
}

const sameWeek = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** A new comfort window for "add": 2 h in the middle of the day's largest eco
 *  gap, kept a slot clear of its neighbours so it doesn't merge into them. */
function freeWindow(windows, slot) {
  if (!windows.length) return [7 * 60, 9 * 60];
  const last = DAY - slot;
  const edges = [[0, 0], ...windows, [last, last]];
  let best = null;
  for (let i = 0; i < edges.length - 1; i++) {
    const a = edges[i][1] + (i === 0 ? 0 : slot);
    const b = edges[i + 1][0] - (i === edges.length - 2 ? 0 : slot);
    if (b - a >= slot && (!best || b - a > best[1] - best[0])) best = [a, b];
  }
  if (!best) return null;
  const len = Math.min(120, best[1] - best[0]);
  const start = Math.round((best[0] + (best[1] - best[0] - len) / 2) / slot) * slot;
  return [start, start + Math.round(len / slot) * slot];
}

// --- card -------------------------------------------------------------------

class AristonScheduleCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass?.states || {}).find(
      (e) => e.startsWith("sensor.") && e.includes("time_program"));
    return { entity: entity || "sensor.ariston_gateway_xxxx_time_program_1" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Define 'entity' (the time-program sensor).");
    const programId =
      config.program_id ?? Number(String(config.entity).match(/(\d+)$/)?.[1]);
    if (programId === undefined || Number.isNaN(programId))
      throw new Error("Define 'program_id' (e.g. 1).");
    this._config = { show_setpoints: true, ...config };
    this._programId = programId;
    this._device = null;   // {week, comfort, eco} as last read from the boiler
    this._edit = null;     // working copy while editing, else null
    this._base = null;     // device copy the edit started from
    this._open = null;     // index of the day whose editor is open
    this._status = null;   // {text, kind} shown in the footer
    this._saving = false;
    this._awaiting = null; // after a save: {week, until} until the read-back lands
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
      this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
      this.shadowRoot.addEventListener("focusout", (e) => this._onFocusOut(e));
    }
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const st = hass.states[this._config.entity];
    const sig = st ? `${st.last_updated}` : "missing";
    if (!first && sig === this._sig) {
      this._renderNow(); // keep the "now" line and marker current
      return;
    }
    this._sig = sig;
    this._sync(st);
    // Don't pull the rug out from under a time field being typed in: the
    // re-render waits for focus to leave it (see _onFocusOut).
    if (this.shadowRoot.activeElement?.matches("input")) this._pendingRender = true;
    else this._render();
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  // --- state ----------------------------------------------------------------

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").slice(0, 2);
    return STRINGS[lang] || STRINGS.en;
  }

  get _slot() {
    const st = this._hass?.states[this._config.entity];
    return Number(st?.attributes?.slot_minutes) || 15;
  }

  _sync(st) {
    if (!st || !st.attributes?.profiles) {
      this._device = null;
      return;
    }
    const a = st.attributes;
    this._device = {
      week: weekFromAttrs(a, this._slot),
      comfort: Number(a.comfort),
      eco: Number(a.eco),
    };
    if (this._awaiting) {
      if (sameWeek(this._device, this._awaiting.target)) {
        this._awaiting = null;
        this._edit = null;
        this._flash(this._t.saved, "ok");
      } else if (Date.now() > this._awaiting.until) {
        this._awaiting = null;
        this._edit = null;
        this._flash(this._t.kept, "warn");
      }
    }
  }

  get _view() {
    if (this._awaiting) return this._awaiting.target;
    return this._edit || this._device;
  }

  get _dirty() {
    return !!this._edit && !sameWeek(this._edit, this._device);
  }

  _startEdit() {
    if (!this._edit) {
      this._edit = structuredClone(this._device);
      this._base = structuredClone(this._device);
    }
    return this._edit;
  }

  _flash(text, kind) {
    this._status = { text, kind };
    clearTimeout(this._flashTimer);
    this._flashTimer = setTimeout(() => {
      this._status = null;
      this._render();
    }, 4000);
  }

  // --- events ---------------------------------------------------------------

  _onClick(e) {
    const el = e.target.closest("[data-action]");
    if (!el || this._saving) return;
    const { action } = el.dataset;
    const day = Number(el.dataset.day);
    const idx = Number(el.dataset.idx);
    const slot = this._slot;

    if (action === "open") {
      this._open = this._open === day ? null : day;
    } else if (action === "close") {
      this._open = null;
    } else if (action === "add") {
      const edit = this._startEdit();
      const w = freeWindow(edit.week[day], slot);
      if (w) edit.week[day] = normalize([...edit.week[day], w], slot);
    } else if (action === "remove") {
      this._startEdit().week[day].splice(idx, 1);
    } else if (action === "copy") {
      const edit = this._startEdit();
      for (const d of el.dataset.targets.split(",").map(Number))
        if (d !== day) edit.week[d] = structuredClone(edit.week[day]);
      this._flash(this._t.copied, "ok");
    } else if (action === "temp") {
      const edit = this._startEdit();
      const key = el.dataset.key;
      const v = Math.round((edit[key] + Number(el.dataset.delta)) * 2) / 2;
      edit[key] = Math.min(Math.max(v, MIN_TEMP), MAX_TEMP);
    } else if (action === "cancel") {
      this._edit = null;
      this._open = null;
      this._status = null;
    } else if (action === "save") {
      this._save();
      return;
    }
    this._render();
  }

  _onChange(e) {
    const el = e.target;
    if (!el.matches("input[type=time]") || !el.value) return;
    const day = Number(el.dataset.day);
    const w = this._startEdit().week[day][Number(el.dataset.idx)];
    w[el.dataset.edge === "start" ? 0 : 1] = toMin(el.value);
    // Live preview in the day bars only: the editor (and the field being
    // typed in) is left alone until focus leaves it, see _onFocusOut.
    this._renderBars();
    this._renderFooter();
  }

  _onFocusOut(e) {
    const editor = e.target.closest?.(".editor");
    if (!editor || editor.contains(e.relatedTarget)) return;
    let render = this._pendingRender;
    this._pendingRender = false;
    if (this._edit) {
      const day = Number(editor.dataset.day);
      const tidy = normalize(this._edit.week[day], this._slot);
      if (JSON.stringify(tidy) !== JSON.stringify(this._edit.week[day])) {
        this._edit.week[day] = tidy;
        render = true;
      }
    }
    if (render) this._render();
  }

  async _save() {
    const edit = this._edit;
    edit.week = edit.week.map((w) => normalize(w, this._slot));
    const data = { id: this._programId, profiles: profilesOf(edit.week) };
    if (this._config.show_setpoints) {
      data.comfort = edit.comfort;
      data.eco = edit.eco;
    }
    this._saving = true;
    this._status = { text: this._t.saving, kind: "info" };
    this._render();
    try {
      await this._hass.callService("ariston_local", "set_time_program", data);
      const target = structuredClone(edit);
      if (!this._config.show_setpoints) {
        target.comfort = this._device.comfort;
        target.eco = this._device.eco;
      }
      this._awaiting = { target, until: Date.now() + 20000 };
      this._open = null;
      this._status = { text: this._t.syncing, kind: "info" };
      // the read-back usually lands with the service call; this catches a slow one
      setTimeout(() => {
        if (this._awaiting) {
          this._sync(this._hass.states[this._config.entity]);
          this._render();
        }
      }, 21000);
    } catch (err) {
      this._status = { text: this._t.error.replace("{e}", err?.message || err), kind: "error" };
    } finally {
      this._saving = false;
      this._render();
    }
  }

  // --- rendering ------------------------------------------------------------

  _num(v) {
    const lang = this._hass?.locale?.language || "en";
    return Number.isFinite(v)
      ? v.toLocaleString(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
      : "–";
  }

  _today() {
    const now = new Date();
    return { day: (now.getDay() + 6) % 7, min: now.getHours() * 60 + now.getMinutes() };
  }

  _nowText() {
    if (!this._device) return "";
    const t = this._t;
    const { day, min } = this._today();
    const w = this._device.week[day];
    const cur = w.find(([a, b]) => a <= min && min < b);
    if (cur) return { cls: "comfort", text: t.now_comfort.replace("{t}", fmt(cur[1])) };
    const next = w.find(([a]) => a > min);
    return next
      ? { cls: "eco", text: t.now_eco.replace("{t}", fmt(next[0])) }
      : { cls: "eco", text: t.now_eco_day };
  }

  _summary(windows) {
    return windows.length
      ? windows.map(([a, b]) => `${fmt(a)}–${fmt(b)}`).join(" · ")
      : this._t.all_eco;
  }

  _render() {
    const root = this.shadowRoot;
    if (!root || !this._config) return;
    const t = this._t;
    const title = this._config.title || t.title;
    if (!this._hass || !this._device) {
      root.innerHTML = `<style>${STYLE}</style><ha-card><div class="head">
        <div class="title">${esc(title)}</div></div>
        <div class="empty">${this._hass ? t.unavailable : ""}</div></ha-card>`;
      return;
    }
    root.innerHTML = `<style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title">${esc(title)}</div>
          <div class="now"></div>
        </div>
        ${this._config.show_setpoints ? this._setpointsHtml() : this._legendHtml()}
        <div class="week"></div>
        <div class="footer"></div>
      </ha-card>`;
    this._renderNow();
    this._renderWeek();
    this._renderFooter();
  }

  _legendHtml() {
    const t = this._t;
    return `<div class="setpoints legend">
      <span class="chip comfort"><span class="sw"></span>${t.comfort}</span>
      <span class="chip eco"><span class="sw"></span>${t.eco}</span></div>`;
  }

  _setpointsHtml() {
    const t = this._t;
    const v = this._view;
    const stepper = (key, label) => `
      <div class="chip ${key}">
        <span class="sw"></span><span class="lbl">${label}</span>
        <button class="step" data-action="temp" data-key="${key}" data-delta="-0.5"
          aria-label="${label} −0,5 °C" ${this._awaiting ? "disabled" : ""}>−</button>
        <span class="val">${this._num(v[key])} °C</span>
        <button class="step" data-action="temp" data-key="${key}" data-delta="0.5"
          aria-label="${label} +0,5 °C" ${this._awaiting ? "disabled" : ""}>+</button>
      </div>`;
    return `<div class="setpoints">${stepper("comfort", t.comfort)}${stepper("eco", t.eco)}</div>`;
  }

  _renderNow() {
    const el = this.shadowRoot?.querySelector(".now");
    if (!el) return;
    const now = this._nowText();
    el.className = `now ${now.cls || ""}`;
    el.innerHTML = now.text ? `<span class="dot"></span>${esc(now.text)}` : "";
    const marker = this.shadowRoot.querySelector(".marker");
    if (marker) marker.style.left = `${(this._today().min / DAY) * 100}%`;
  }

  _barHtml(d, windows) {
    const segs = windows.map(([a, b]) =>
      `<span class="seg" style="left:${(a / DAY) * 100}%;width:${((b - a) / DAY) * 100}%"></span>`).join("");
    const { day: today, min } = this._today();
    const marker = d === today
      ? `<span class="marker" style="left:${(min / DAY) * 100}%"></span>` : "";
    return segs + marker;
  }

  _changed(d, windows) {
    return !!this._edit && JSON.stringify(windows) !== JSON.stringify(this._device.week[d]);
  }

  _renderWeek() {
    const el = this.shadowRoot?.querySelector(".week");
    if (!el) return;
    const t = this._t;
    const today = this._today().day;
    const ticks = [0, 6, 12, 18, 24]
      .map((h) => `<span style="left:${(h / 24) * 100}%">${h}h</span>`).join("");
    const rows = this._view.week.map((windows, d) => {
      const open = this._open === d;
      return `
        <button class="day ${d === today ? "today" : ""} ${open ? "open" : ""}"
          data-action="open" data-day="${d}" aria-expanded="${open}"
          aria-label="${t.edit_day.replace("{d}", t.days[d])}" ${this._awaiting ? "disabled" : ""}>
          <span class="label">${t.days[d]}<span class="mod" ${this._changed(d, windows) ? "" : "hidden"}></span></span>
          <span class="bar">${this._barHtml(d, windows)}</span>
          <span class="sum">${this._summary(windows)}</span>
        </button>
        ${open ? this._editorHtml(d, windows) : ""}`;
    }).join("");
    el.innerHTML = `<div class="axis"><span></span><span class="ticks">${ticks}</span></div>${rows}`;
  }

  /** Refresh the day bars and summaries in place, leaving any editor intact. */
  _renderBars() {
    const days = this.shadowRoot?.querySelectorAll(".day");
    if (!days) return;
    this._view.week.forEach((windows, d) => {
      const row = days[d];
      row.querySelector(".bar").innerHTML = this._barHtml(d, windows);
      row.querySelector(".sum").textContent = this._summary(windows);
      row.querySelector(".mod").hidden = !this._changed(d, windows);
    });
  }

  _editorHtml(d, windows) {
    const t = this._t;
    const step = this._slot * 60;
    const rows = windows.map(([a, b], i) => `
      <div class="win">
        <input type="time" step="${step}" min="00:00" max="${fmt(DAY - 2 * this._slot)}"
          value="${fmt(a)}" data-day="${d}" data-idx="${i}" data-edge="start">
        <span class="arrow">→</span>
        <input type="time" step="${step}" min="00:00" max="${fmt(DAY - this._slot)}"
          value="${fmt(Math.min(b, DAY - this._slot))}" data-day="${d}" data-idx="${i}" data-edge="end">
        <button class="icon" data-action="remove" data-day="${d}" data-idx="${i}"
          aria-label="${t.remove}" title="${t.remove}">✕</button>
      </div>`).join("");
    const groups = [[t.weekdays, [0, 1, 2, 3, 4]], [t.weekend, [5, 6]], [t.all_week, [0, 1, 2, 3, 4, 5, 6]]];
    const copy = groups.map(([label, ds]) =>
      `<button class="pill" data-action="copy" data-day="${d}" data-targets="${ds}">${label}</button>`).join("") +
      t.days.map((name, i) => i === d ? "" :
        `<button class="pill small" data-action="copy" data-day="${d}" data-targets="${i}">${name}</button>`).join("");
    return `
      <div class="editor" data-day="${d}">
        ${rows || `<div class="hint">${t.all_eco}</div>`}
        <div class="row">
          <button class="text" data-action="add" data-day="${d}">＋ ${t.add}</button>
          <span class="spacer"></span>
          <button class="text" data-action="close">${t.close}</button>
        </div>
        <div class="copy"><span class="hint">${t.copy_to}</span>${copy}</div>
        <div class="hint note">${t.end_note}</div>
      </div>`;
  }

  _renderFooter() {
    const el = this.shadowRoot?.querySelector(".footer");
    if (!el) return;
    const t = this._t;
    const dirty = this._dirty && !this._awaiting;
    const conflict = dirty && this._base && !sameWeek(this._base, this._device);
    const status = this._status;
    if (!dirty && !status) {
      el.innerHTML = "";
      el.className = "footer";
      return;
    }
    el.className = "footer show";
    el.innerHTML = `
      ${conflict ? `<div class="msg warn">${t.changed}</div>` : ""}
      <div class="bar-row">
        <span class="msg ${status?.kind || ""}">${esc(status?.text || (dirty ? t.unsaved : ""))}</span>
        ${dirty ? `
          <button class="text" data-action="cancel" ${this._saving ? "disabled" : ""}>${t.cancel}</button>
          <button class="primary" data-action="save" ${this._saving ? "disabled" : ""}>${this._saving ? t.saving : t.save}</button>` : ""}
      </div>`;
  }
}

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STYLE = `
  :host {
    --comfort: var(--state-climate-heat-color, #ff8100);
    --eco: var(--state-climate-cool-color, #2b9af9);
    --label-w: 40px;
  }
  ha-card { padding: 16px; overflow: hidden; }
  button { font: inherit; color: inherit; }
  .head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .title { font-size: 1.15em; font-weight: 500; }
  .now { font-size: .9em; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px; }
  .now .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--eco); }
  .now.comfort .dot { background: var(--comfort); }
  .empty { color: var(--secondary-text-color); padding: 12px 0 4px; }

  .setpoints { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0 4px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 6px 4px 10px;
    border-radius: 18px; background: var(--secondary-background-color); font-size: .9em; }
  .legend .chip { padding: 4px 10px; }
  .chip .sw { width: 10px; height: 10px; border-radius: 3px; background: var(--eco); }
  .chip.comfort .sw { background: var(--comfort); }
  .chip .lbl { color: var(--secondary-text-color); }
  .chip .val { min-width: 4.5em; text-align: center; font-weight: 500; font-variant-numeric: tabular-nums; }
  .step { width: 28px; height: 28px; border-radius: 50%; border: none; cursor: pointer;
    background: var(--card-background-color); font-size: 1.1em; line-height: 1; }
  .step:hover:not([disabled]) { background: var(--divider-color); }

  .week { margin-top: 10px; }
  .axis { display: grid; grid-template-columns: var(--label-w) 1fr; }
  .ticks { position: relative; height: 16px; font-size: .72em; color: var(--secondary-text-color); }
  .ticks span { position: absolute; transform: translateX(-50%); }
  .ticks span:first-child { transform: none; }
  .ticks span:last-child { transform: translateX(-100%); }

  .day { display: grid; grid-template-columns: var(--label-w) 1fr; grid-template-rows: auto auto;
    column-gap: 0; row-gap: 2px; width: 100%; padding: 6px 0; border: none; background: none;
    cursor: pointer; text-align: left; border-top: 1px solid var(--divider-color); }
  .day:hover:not([disabled]) .bar { filter: brightness(1.08); }
  .day.open { background: color-mix(in srgb, var(--primary-color) 6%, transparent); }
  .label { grid-row: 1 / span 2; align-self: center; font-size: .9em; position: relative; }
  .day.today .label { font-weight: 700; color: var(--primary-color); }
  .mod { position: absolute; top: -2px; right: 10px; width: 6px; height: 6px; border-radius: 50%;
    background: var(--warning-color, #ffa600); }
  .bar { position: relative; height: 18px; border-radius: 5px;
    background: color-mix(in srgb, var(--eco) 22%, transparent); overflow: hidden; }
  .seg { position: absolute; top: 0; bottom: 0; background: var(--comfort); border-radius: 4px; }
  .marker { position: absolute; top: -2px; bottom: -2px; width: 2px; margin-left: -1px;
    background: var(--primary-text-color); box-shadow: 0 0 0 1px var(--card-background-color); }
  .sum { font-size: .78em; color: var(--secondary-text-color); font-variant-numeric: tabular-nums;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .editor { margin: 0 0 8px var(--label-w); padding: 10px 12px; border-radius: 10px;
    background: var(--secondary-background-color); display: flex; flex-direction: column; gap: 8px; }
  .win { display: flex; align-items: center; gap: 8px; }
  .win input { font: inherit; padding: 6px 8px; border-radius: 6px; color: var(--primary-text-color);
    border: 1px solid var(--divider-color); background: var(--card-background-color);
    font-variant-numeric: tabular-nums; color-scheme: light dark; }
  .arrow { color: var(--secondary-text-color); }
  .row { display: flex; align-items: center; gap: 8px; }
  .spacer { flex: 1; }
  .copy { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .hint { font-size: .82em; color: var(--secondary-text-color); }
  .note { font-size: .75em; }
  .icon { border: none; background: none; cursor: pointer; padding: 6px; border-radius: 50%;
    color: var(--secondary-text-color); }
  .icon:hover { background: var(--divider-color); color: var(--error-color, #db4437); }
  .text { border: none; background: none; cursor: pointer; padding: 6px 10px; border-radius: 8px;
    color: var(--primary-color); font-weight: 500; }
  .text:hover:not([disabled]) { background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
  .pill { border: 1px solid var(--divider-color); background: var(--card-background-color);
    border-radius: 14px; padding: 3px 10px; cursor: pointer; font-size: .82em; }
  .pill.small { padding: 3px 8px; }
  .pill:hover { border-color: var(--primary-color); color: var(--primary-color); }
  button[disabled] { opacity: .5; cursor: default; }

  .footer { display: none; }
  .footer.show { display: block; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
  .bar-row { display: flex; align-items: center; gap: 8px; }
  .bar-row .msg { flex: 1; }
  .msg { font-size: .88em; color: var(--secondary-text-color); }
  .msg.ok { color: var(--success-color, #43a047); }
  .msg.warn { color: var(--warning-color, #ffa600); margin-bottom: 6px; }
  .msg.error { color: var(--error-color, #db4437); }
  .primary { border: none; cursor: pointer; padding: 8px 16px; border-radius: 18px; font-weight: 500;
    background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .primary:hover:not([disabled]) { filter: brightness(1.1); }

  @media (min-width: 560px) {
    :host { --label-w: 48px; }
  }
`;

customElements.define("ariston-schedule-card", AristonScheduleCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "ariston-schedule-card",
  name: "Ariston schedule card",
  description: "Weekly comfort/eco editor for an Ariston heating time program.",
  preview: false,
});
