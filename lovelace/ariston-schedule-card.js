/*
 * Ariston schedule card — a weekly comfort/eco grid editor for one heating
 * time program (Func/TProg), for the `ariston_local` integration.
 *
 * Local-first by design: the boiler owns the schedule. This card renders what
 * the integration last read from the boiler (the tprog sensor) and, on Save,
 * writes your edits back via the `ariston_local.set_time_program` service. It
 * never holds the schedule itself — reopen it and you see the boiler's truth.
 *
 * Dependency-free (no build step, no Lit): drop it in as a Lovelace resource.
 *
 * Config:
 *   type: custom:ariston-schedule-card
 *   entity: sensor.ariston_gateway_XXXX_time_program_1   # the tprog sensor
 *   program_id: 1                                        # Func/TProg id
 *   title: Heating schedule                              # optional
 *
 * Grid resolution is 30 min (48 cells/day); real schedules sit on :00/:30
 * boundaries. For finer (15-min) edits, call the service directly.
 */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; // index 0 => day 1
const COLS = 48; // 30-minute cells
const STEP = 30; // minutes per cell

const hhmm = (cell) => {
  const m = cell * STEP;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
};
const cellOf = (s) => {
  const [h, m] = s.split(":").map(Number);
  return Math.round((h * 60 + m) / STEP);
};

class AristonScheduleCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("Define 'entity' (the time-program sensor).");
    this._config = config;
    this._programId =
      config.program_id ?? Number(String(config.entity).match(/(\d+)$/)?.[1]);
    if (this._programId === undefined || Number.isNaN(this._programId))
      throw new Error("Define 'program_id' (e.g. 1).");
    // 7 days x 48 cells, true = comfort
    this._grid = DAYS.map(() => new Array(COLS).fill(false));
    this._dirty = false;
    this._built = false;
    this._painting = null; // value being painted during a drag
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    // Only pull from the device when the user has no unsaved edits.
    if (!this._dirty) this._syncFromDevice();
  }

  getCardSize() {
    return 9;
  }

  _state() {
    return this._hass?.states?.[this._config.entity];
  }

  // --- device -> grid -----------------------------------------------------
  _syncFromDevice() {
    const st = this._state();
    if (!st) return;
    const attrs = st.attributes || {};
    for (const row of this._grid) row.fill(false);
    for (const prof of attrs.profiles || []) {
      for (const win of prof.comfort_windows || []) {
        const [a, b] = [cellOf(win[0]), cellOf(win[1])];
        for (const d of prof.days || []) {
          const row = this._grid[d - 1];
          if (!row) continue;
          for (let c = a; c < b && c < COLS; c++) row[c] = true;
        }
      }
    }
    if (this._comfortInput && document.activeElement !== this._comfortInput)
      this._comfortInput.value = attrs.comfort ?? "";
    if (this._ecoInput && document.activeElement !== this._ecoInput)
      this._ecoInput.value = attrs.eco ?? "";
    this._paint();
  }

  // --- grid -> service payload -------------------------------------------
  _rowWindows(row) {
    const windows = [];
    let start = null;
    for (let c = 0; c <= COLS; c++) {
      const on = c < COLS && row[c];
      if (on && start === null) start = c;
      else if (!on && start !== null) {
        windows.push([hhmm(start), hhmm(c)]);
        start = null;
      }
    }
    return windows;
  }

  _profiles() {
    // Group days that share an identical window-set into one profile.
    const byKey = new Map();
    this._grid.forEach((row, i) => {
      const windows = this._rowWindows(row);
      const key = JSON.stringify(windows);
      if (!byKey.has(key)) byKey.set(key, { windows, days: [] });
      byKey.get(key).days.push(i + 1);
    });
    return [...byKey.values()];
  }

  async _save() {
    const data = { id: this._programId, profiles: this._profiles() };
    const comfort = parseFloat(this._comfortInput.value);
    const eco = parseFloat(this._ecoInput.value);
    if (!Number.isNaN(comfort)) data.comfort = comfort;
    if (!Number.isNaN(eco)) data.eco = eco;
    this._status("Writing to the boiler…");
    try {
      await this._hass.callService("ariston_local", "set_time_program", data);
      this._dirty = false;
      this._status("Saved. Re-reading from the boiler…");
    } catch (err) {
      this._status(`Error: ${err.message || err}`, true);
    }
  }

  // --- rendering ----------------------------------------------------------
  _build() {
    this._built = true;
    const card = document.createElement("ha-card");
    card.header = this._config.title || "Heating schedule";
    const root = document.createElement("div");
    root.style.cssText = "padding:12px 16px 16px;";
    card.appendChild(root);

    const grid = document.createElement("div");
    grid.style.cssText =
      "display:grid;grid-template-columns:34px 1fr;gap:4px;align-items:center;";
    root.appendChild(grid);

    // hour ruler
    grid.appendChild(document.createElement("div"));
    const ruler = document.createElement("div");
    ruler.style.cssText =
      "display:grid;grid-template-columns:repeat(24,1fr);font-size:9px;color:var(--secondary-text-color);";
    for (let h = 0; h < 24; h++) {
      const t = document.createElement("div");
      t.textContent = h % 3 === 0 ? String(h) : "";
      ruler.appendChild(t);
    }
    grid.appendChild(ruler);

    this._cells = [];
    DAYS.forEach((name, i) => {
      const label = document.createElement("div");
      label.textContent = name;
      label.style.cssText = "font-size:12px;color:var(--primary-text-color);";
      grid.appendChild(label);

      const rowEl = document.createElement("div");
      rowEl.style.cssText = `display:grid;grid-template-columns:repeat(${COLS},1fr);height:22px;border-radius:4px;overflow:hidden;`;
      const rowCells = [];
      for (let c = 0; c < COLS; c++) {
        const cell = document.createElement("div");
        cell.style.cssText =
          "height:100%;cursor:pointer;border-right:1px solid var(--divider-color);";
        if (c % 2 === 1) cell.style.borderRight = "1px solid var(--card-background-color)";
        const toggle = (paintVal) => {
          this._grid[i][c] = paintVal;
          this._dirty = true;
          this._paintCell(i, c);
        };
        cell.addEventListener("mousedown", (e) => {
          e.preventDefault();
          this._painting = !this._grid[i][c];
          toggle(this._painting);
        });
        cell.addEventListener("mouseenter", () => {
          if (this._painting !== null) toggle(this._painting);
        });
        rowCells.push(cell);
        rowEl.appendChild(cell);
      }
      this._cells.push(rowCells);
      grid.appendChild(rowEl);
    });
    window.addEventListener("mouseup", () => (this._painting = null));

    // setpoints + actions
    const controls = document.createElement("div");
    controls.style.cssText =
      "display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:14px;";
    const mkNum = (label) => {
      const wrap = document.createElement("label");
      wrap.style.cssText = "font-size:12px;display:flex;gap:6px;align-items:center;";
      wrap.append(label);
      const inp = document.createElement("input");
      inp.type = "number";
      inp.step = "0.5";
      inp.style.cssText = "width:64px;";
      inp.addEventListener("input", () => (this._dirty = true));
      wrap.appendChild(inp);
      controls.appendChild(wrap);
      return inp;
    };
    this._comfortInput = mkNum("Comfort °C");
    this._ecoInput = mkNum("Eco °C");

    const spacer = document.createElement("div");
    spacer.style.flex = "1";
    controls.appendChild(spacer);

    this._statusEl = document.createElement("span");
    this._statusEl.style.cssText = "font-size:12px;color:var(--secondary-text-color);";
    controls.appendChild(this._statusEl);

    const save = document.createElement("mwc-button");
    save.setAttribute("raised", "");
    save.textContent = "Save to boiler";
    save.addEventListener("click", () => this._save());
    controls.appendChild(save);

    const revert = document.createElement("mwc-button");
    revert.textContent = "Revert";
    revert.addEventListener("click", () => {
      this._dirty = false;
      this._syncFromDevice();
      this._status("");
    });
    controls.appendChild(revert);

    root.appendChild(controls);

    const note = document.createElement("div");
    note.style.cssText =
      "font-size:11px;color:var(--secondary-text-color);margin-top:8px;";
    note.textContent =
      "Shaded = comfort, blank = eco. Click or drag to edit (30-min grid). " +
      "The boiler owns the schedule; Save writes your edits to it.";
    root.appendChild(note);

    this.innerHTML = "";
    this.appendChild(card);
    this._paint();
  }

  _paintCell(i, c) {
    const on = this._grid[i][c];
    this._cells[i][c].style.background = on
      ? "var(--primary-color)"
      : "var(--secondary-background-color)";
  }

  _paint() {
    if (!this._cells) return;
    for (let i = 0; i < DAYS.length; i++)
      for (let c = 0; c < COLS; c++) this._paintCell(i, c);
  }

  _status(text, isError = false) {
    if (!this._statusEl) return;
    this._statusEl.textContent = text;
    this._statusEl.style.color = isError
      ? "var(--error-color)"
      : "var(--secondary-text-color)";
  }
}

customElements.define("ariston-schedule-card", AristonScheduleCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "ariston-schedule-card",
  name: "Ariston schedule card",
  description: "Weekly comfort/eco editor for an Ariston heating time program.",
});
