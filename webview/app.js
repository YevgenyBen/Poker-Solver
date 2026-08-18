(function () {
  "use strict";

  const RANKS = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"];
  // Matches api/main.py's PREWARM_STACK_DEPTHS — picking one of these is
  // (after the server's first startup) always an instant response.
  const PRESET_STACKS = [20, 40, 50, 75, 100, 150, 200];

  const grid = document.getElementById("grid");
  const detail = document.getElementById("detail");
  const statusEl = document.getElementById("status");
  const slider = document.getElementById("stack-slider");
  const stackValueEl = document.getElementById("stack-value");
  const presetsEl = document.getElementById("presets");

  /** hand label ("AKs", "72o", "TT") for grid position (row, col), both
   * 0-indexed over RANKS (high to low). Diagonal = pairs, upper-right
   * triangle = suited, lower-left triangle = offsuit — the standard
   * range-chart layout. */
  function handLabelAt(row, col) {
    const high = RANKS[Math.min(row, col)];
    const low = RANKS[Math.max(row, col)];
    if (row === col) return high + low;
    return row < col ? high + low + "s" : high + low + "o";
  }

  function colorForAction(actionLabel) {
    if (actionLabel.startsWith("fold")) return "var(--fold)";
    if (actionLabel.startsWith("call_or_check")) return "var(--call)";
    if (actionLabel.startsWith("raise")) return "var(--raise)";
    if (actionLabel.startsWith("all_in")) return "var(--allin)";
    return "#999";
  }

  // Fixed left-to-right stacking order for the in-cell gradient and the
  // detail panel's bars, regardless of the order keys happen to arrive
  // in from the API.
  const ACTION_ORDER = ["fold", "call_or_check", "raise", "all_in"];

  function sortedEntries(freqs) {
    return Object.entries(freqs).sort((a, b) => {
      const rank = (label) => ACTION_ORDER.findIndex((prefix) => label.startsWith(prefix));
      return rank(a[0]) - rank(b[0]);
    });
  }

  function gradientFor(freqs) {
    const entries = sortedEntries(freqs);
    let cursor = 0;
    const stops = [];
    for (const [action, freq] of entries) {
      if (freq <= 0) continue;
      const start = cursor;
      cursor += freq * 100;
      stops.push(`${colorForAction(action)} ${start}% ${cursor}%`);
    }
    if (stops.length === 0) return "#999";
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }

  const cells = []; // {el, hand}

  function buildGrid() {
    for (let row = 0; row < 13; row++) {
      for (let col = 0; col < 13; col++) {
        const hand = handLabelAt(row, col);
        const cell = document.createElement("div");
        cell.className = "cell";
        cell.textContent = hand;
        cell.dataset.hand = hand;
        cell.addEventListener("click", () => selectHand(hand));
        grid.appendChild(cell);
        cells.push({ el: cell, hand });
      }
    }
  }

  let latestOpeningRange = null;
  let selectedHandLabel = null;

  function renderGrid(openingRange) {
    latestOpeningRange = openingRange;
    for (const { el, hand } of cells) {
      const freqs = openingRange[hand];
      el.style.background = freqs ? gradientFor(freqs) : "#999";
    }
    if (selectedHandLabel && openingRange[selectedHandLabel]) {
      renderDetail(selectedHandLabel, openingRange[selectedHandLabel]);
    }
  }

  function selectHand(hand) {
    selectedHandLabel = hand;
    for (const { el, hand: h } of cells) {
      el.classList.toggle("selected", h === hand);
    }
    if (latestOpeningRange && latestOpeningRange[hand]) {
      renderDetail(hand, latestOpeningRange[hand]);
    }
  }

  function renderDetail(hand, freqs) {
    const rows = sortedEntries(freqs)
      .map(([action, freq]) => {
        const pct = (freq * 100).toFixed(1);
        return `
          <div class="detail-row">
            <span class="label">${action}</span>
            <span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:${colorForAction(action)}"></span></span>
            <span class="pct">${pct}%</span>
          </div>`;
      })
      .join("");
    detail.innerHTML = `<h2>${hand}</h2>${rows}`;
  }

  function setStatus(text) {
    statusEl.textContent = text;
  }

  function highlightActivePreset(stackBb) {
    for (const button of presetsEl.children) {
      button.classList.toggle("active", Number(button.dataset.stack) === stackBb);
    }
  }

  function buildPresets() {
    for (const depth of PRESET_STACKS) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${depth}bb`;
      button.dataset.stack = String(depth);
      button.addEventListener("click", () => {
        slider.value = String(depth);
        stackValueEl.textContent = `${depth}bb`;
        fetchOpeningRange(depth);
      });
      presetsEl.appendChild(button);
    }
  }

  let inFlightController = null;

  async function fetchOpeningRange(stackBb) {
    highlightActivePreset(stackBb);
    setStatus("Solving…");

    if (inFlightController) inFlightController.abort();
    const controller = new AbortController();
    inFlightController = controller;

    try {
      const response = await fetch(`/solve/${stackBb}`, { signal: controller.signal });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${response.status})`);
      }
      const data = await response.json();
      renderGrid(data.opening_range);
      setStatus(`Solved in ${data.elapsed_seconds.toFixed(2)}s (${data.iterations} iterations)`);
    } catch (err) {
      if (err.name === "AbortError") return;
      setStatus(`Error: ${err.message}`);
    }
  }

  let debounceTimer = null;

  function onSliderInput() {
    const stackBb = Number(slider.value);
    stackValueEl.textContent = `${stackBb}bb`;
    highlightActivePreset(stackBb);
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => fetchOpeningRange(stackBb), 350);
  }

  buildGrid();
  buildPresets();
  slider.addEventListener("input", onSliderInput);
  fetchOpeningRange(Number(slider.value));
})();
