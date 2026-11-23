/* DanClub Terminal — Zero-dependency 1:1 Responsive Financial Terminal (MIT License) */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    overview: null,
    arb: null,
    flows: null,
    labs: null,
    feed: null,
    activeTab: "war",
    activeMacroGroup: "ALL",
    activeSpreadPair: "SKHY_VS_SKHYNIX",
    activeSpreadRange: "24h",
    activeCsiCode: "ALL",
    activeEtfAsset: "BTC",
    activePromoCat: "ALL",
    activeFeedCat: "ALL",
    feedSearch: "",
    feedAlertMode: "chime",
    feedAlertScope: "all",
    feedShowEn: false,
    feedPrevSnapshotKeys: new Set(),
    feedSeenHistory: new Set(),
    feedFreshKeys: new Set(),
    feedUnreadCount: 0,
    feedInitialized: false,
    feedBaselineTs: 0,
    feedLatestGenMs: 0,
    feedPollInFlight: false,
    feedPollTimer: null,
    feedPollAbortCtrl: null,
    lastFeedPollMs: 0,
    feedAudioCtx: null,
    feedAudioUnlocked: false,
    liveCrypto: {},
    liveBstocks: {},
    prevLivePrice: {},
    liveTimer: null,
    resizeTimer: null,
  };

  /* ---------- Formatters & Escaping ---------- */
  const esc = (v) =>
    String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  const safeHref = (u) => {
    if (typeof u !== "string") return "";
    const trimmed = u.trim();
    return trimmed.startsWith("https://") ? trimmed : "";
  };

  const fmtNum = (v, d = 2) => {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    }).format(n);
  };

  const fmtInt = (v) => fmtNum(v, 0);

  const fmtCompact = (v) => {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    const abs = Math.abs(n);
    const sign = n < 0 ? "-" : "";
    if (abs >= 1e9) return `${sign}$${fmtNum(abs / 1e9, 2)}B`;
    if (abs >= 1e6) return `${sign}$${fmtNum(abs / 1e6, 2)}M`;
    if (abs >= 1e3) return `${sign}$${fmtNum(abs / 1e3, 1)}K`;
    return `${sign}$${fmtNum(abs, 2)}`;
  };

  const fmtSignedPct = (v, d = 2) => {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    return `${n > 0 ? "+" : ""}${fmtNum(n, d)}%`;
  };

  const fmtSignedNum = (v, d = 1, suffix = "") => {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    return `${n > 0 ? "+" : ""}${fmtNum(n, d)}${suffix}`;
  };

  const dirCls = (v) => {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return "";
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    return n > 0 ? "up" : "down";
  };

  const fmtTimeShort = (ts) => {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return "--";
    const d = new Date(n * 1000);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}-${dd} ${hh}:${mi}`;
  };

  const fmtTimeHM = (ts) => {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return "--";
    const d = new Date(n * 1000);
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mi}`;
  };

  const sessionZh = (sess) => {
    if (sess === "BSTOCKS_247") return "bStocks";
    if (sess === "PRE") return "盘前";
    if (sess === "POST") return "盘后";
    if (sess === "CLOSED") return "休市";
    return "";
  };

  const cssVar = (name, fallback) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

  /* ---------- Theme, Color Mode & Tab Routing ---------- */
  const safeStoreGet = (k) => {
    try { return localStorage.getItem(k); } catch { return null; }
  };
  const safeStoreSet = (k, v) => {
    try { localStorage.setItem(k, v); } catch {}
  };

  function initPreferences() {
    const savedTheme = safeStoreGet("danclub_theme_v2");
    applyTheme(savedTheme === "light" ? "light" : "dark");

    const savedColor = safeStoreGet("danclub_color_mode");
    applyColorMode(savedColor === "cn" ? "cn" : "intl");

    const savedAlertMode = safeStoreGet("danclub_feed_alert_mode");
    if (["off", "chime", "tts"].includes(savedAlertMode)) {
      state.feedAlertMode = savedAlertMode;
    }
    const savedAlertScope = safeStoreGet("danclub_feed_alert_scope");
    if (["all", "major"].includes(savedAlertScope)) {
      state.feedAlertScope = savedAlertScope;
    }
    state.feedShowEn = safeStoreGet("danclub_feed_show_en") === "1";
    updateFeedAlertUi();

    $("btn-theme")?.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      safeStoreSet("danclub_theme_v2", next);
      applyTheme(next);
      rerenderActiveCharts();
    });

    $("btn-color-mode")?.addEventListener("click", () => {
      const next = document.documentElement.dataset.colorMode === "cn" ? "intl" : "cn";
      safeStoreSet("danclub_color_mode", next);
      applyColorMode(next);
      rerenderActiveCharts();
    });
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const btn = $("btn-theme");
    if (btn) btn.textContent = theme === "dark" ? "浅色" : "深色";
  }

  function applyColorMode(mode) {
    document.documentElement.dataset.colorMode = mode;
    const btn = $("btn-color-mode");
    if (btn) btn.textContent = mode === "cn" ? "红涨绿跌" : "绿涨红跌";
  }

  function switchTab(tab, focusElId = null) {
    if (tab === "labs") tab = "dca";
    const valid = ["war", "overview", "arb", "flows", "earn", "dca", "feed", "open"];
    if (!valid.includes(tab)) tab = "war";
    state.activeTab = tab;
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    document.querySelectorAll(".tab-panel").forEach((sec) => {
      sec.classList.toggle("active", sec.id === `tab-${tab}`);
    });
    if (window.location.hash !== `#${tab}`) {
      history.replaceState(null, "", `#${tab}`);
    }
    window.DanClubWarRoom?.setActive(tab === "war");
    if (tab === "feed") {
      state.feedUnreadCount = 0;
      updateFeedBadge();
    }
    // Wait one frame so visible containers have true non-zero clientWidth for 1:1 SVG rendering!
    requestAnimationFrame(() => {
      rerenderActiveCharts();
      if (focusElId) {
        const el = $(focusElId);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.remove("card-flash");
          void el.offsetWidth;
          el.classList.add("card-flash");
        }
      }
    });
  }

  function initTabs() {
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
    $("footer-api-link")?.addEventListener("click", (e) => {
      e.preventDefault();
      switchTab("open");
    });
    const initial = (window.location.hash || "#war").slice(1);
    switchTab(initial);
  }

  /* ---------- 1:1 Pixel-Ratio Responsive SVG Chart Engine (ZERO preserveAspectRatio Distortion!) ---------- */
  function measureChartWidth(box, fallback = 680) {
    const cs = getComputedStyle(box);
    const padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    const w = Math.floor((box.clientWidth || 0) - padX);
    return Math.max(240, w > 60 ? w : fallback);
  }

  function renderLineChart(containerId, seriesList, opts = {}) {
    const box = $(containerId);
    if (!box) return;
    const validSeries = (seriesList || []).filter((s) => Array.isArray(s.points) && s.points.length >= 2);
    if (!validSeries.length) {
      box.innerHTML = `<div class="muted small" style="padding:56px 0;text-align:center;">暂无足够历史样本</div>`;
      return;
    }

    const W = measureChartWidth(box);
    const H = opts.height || (W < 420 ? 196 : 230);
    const compact = W < 420;
    const pad = {
      top: 14,
      right: 14,
      bottom: opts.hideX ? 10 : 24,
      left: compact ? 48 : 58,
    };
    const iw = Math.max(80, W - pad.left - pad.right);
    const ih = Math.max(60, H - pad.top - pad.bottom);

    const allY = [];
    validSeries.forEach((s) => {
      s.points.forEach((p) => {
        if (Number.isFinite(p.y)) allY.push(p.y);
        if (Number.isFinite(p.low)) allY.push(p.low);
        if (Number.isFinite(p.high)) allY.push(p.high);
      });
    });
    if (opts.band && Number.isFinite(opts.band.mean) && Number.isFinite(opts.band.std)) {
      allY.push(opts.band.mean - opts.band.std, opts.band.mean + opts.band.std);
    }
    if (!allY.length) {
      box.innerHTML = `<div class="muted small" style="padding:56px 0;text-align:center;">暂无有效数值样本</div>`;
      return;
    }

    let minY = Math.min(...allY);
    let maxY = Math.max(...allY);
    const span = maxY - minY;
    const margin = span > 1e-6 ? span * 0.14 : Math.max(Math.abs(maxY) * 0.04, 0.35);
    minY -= margin;
    maxY += margin;

    const maxLen = Math.max(...validSeries.map((s) => s.points.length));
    const xAt = (idx, len) => pad.left + (len <= 1 ? 0 : (idx / (len - 1)) * iw);
    const yAt = (v) => pad.top + ((maxY - v) / (maxY - minY)) * ih;

    const lineCol = cssVar("--line", "rgba(148,163,184,0.16)");
    const mutedCol = cssVar("--muted", "#7c899e");
    const surfaceCol = cssVar("--surface-2", "#151b26");
    const suffix = opts.suffix ?? "";
    const decimals = opts.decimals ?? 2;
    const yTicks = opts.yTicks || 4;

    // Horizontal gridlines & crisp 1:1 monospace Y-axis labels
    let gridSvg = "";
    for (let i = 0; i <= yTicks; i++) {
      const val = maxY - (i / yTicks) * (maxY - minY);
      const y = pad.top + (i / yTicks) * ih;
      gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${lineCol}" stroke-dasharray="3 3" stroke-width="1"/>`;
      gridSvg += `<text x="${pad.left - 7}" y="${(y + 3.5).toFixed(1)}" text-anchor="end" fill="${mutedCol}" font-size="${compact ? 10 : 10.5}" font-family="ui-monospace,SFMono-Regular,Menlo,monospace">${fmtNum(val, decimals)}${esc(suffix)}</text>`;
    }

    // Optional 72h mean ± 1σ reference band
    let bandSvg = "";
    if (opts.band && Number.isFinite(opts.band.mean) && Number.isFinite(opts.band.std)) {
      const yTop = yAt(opts.band.mean + opts.band.std);
      const yBot = yAt(opts.band.mean - opts.band.std);
      const yMid = yAt(opts.band.mean);
      bandSvg += `<rect x="${pad.left}" y="${yTop.toFixed(1)}" width="${iw}" height="${Math.max(1, yBot - yTop).toFixed(1)}" fill="rgba(56,189,248,0.09)"/>`;
      bandSvg += `<line x1="${pad.left}" y1="${yMid.toFixed(1)}" x2="${W - pad.right}" y2="${yMid.toFixed(1)}" stroke="#38bdf8" stroke-dasharray="4 4" stroke-width="1.1" opacity="0.75"/>`;
    }

    // Optional reference line (e.g. 0% or 1.0x parity)
    let refSvg = "";
    if (opts.refLine !== undefined && opts.refLine >= minY && opts.refLine <= maxY) {
      const ry = yAt(opts.refLine);
      refSvg += `<line x1="${pad.left}" y1="${ry.toFixed(1)}" x2="${W - pad.right}" y2="${ry.toFixed(1)}" stroke="${mutedCol}" stroke-dasharray="4 3" stroke-width="1.1" opacity="0.8"/>`;
    }

    // Gradients & series paths
    const gradId = `grad_${containerId.replace(/[^a-z0-9]/gi, "")}`;
    let defsSvg = "";
    let seriesSvg = "";
    validSeries.forEach((s, si) => {
      const pts = s.points;
      const validPts = pts.map((p, idx) => ({ ...p, idx })).filter((p) => Number.isFinite(p.y));
      if (validPts.length < 2) return;

      if (pts.every((p) => Number.isFinite(p.low) && Number.isFinite(p.high))) {
        const upper = pts.map((p, i) => `${xAt(i, pts.length).toFixed(1)},${yAt(p.high).toFixed(1)}`).join(" ");
        const lower = [...pts]
          .reverse()
          .map((p, ri) => `${xAt(pts.length - 1 - ri, pts.length).toFixed(1)},${yAt(p.low).toFixed(1)}`)
          .join(" ");
        seriesSvg += `<polygon points="${upper} ${lower}" fill="${s.color}" opacity="0.14"/>`;
      } else if (validSeries.length === 1 && opts.area !== false) {
        defsSvg += `<linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${s.color}" stop-opacity="0.22"/><stop offset="100%" stop-color="${s.color}" stop-opacity="0.01"/></linearGradient>`;
        const areaPath =
          validPts
            .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(p.idx, pts.length).toFixed(1)},${yAt(p.y).toFixed(1)}`)
            .join(" ") +
          ` L${xAt(validPts[validPts.length - 1].idx, pts.length).toFixed(1)},${(pad.top + ih).toFixed(1)} L${xAt(validPts[0].idx, pts.length).toFixed(1)},${(pad.top + ih).toFixed(1)} Z`;
        seriesSvg += `<path d="${areaPath}" fill="url(#${gradId})"/>`;
      }

      const d = validPts
        .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(p.idx, pts.length).toFixed(1)},${yAt(p.y).toFixed(1)}`)
        .join(" ");
      seriesSvg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${si === 0 ? 2 : 1.8}" stroke-linejoin="round" stroke-linecap="round"/>`;
      if (opts.showDots) {
        validPts.forEach((p) => {
          seriesSvg += `<circle cx="${xAt(p.idx, pts.length).toFixed(1)}" cy="${yAt(p.y).toFixed(1)}" r="2.8" fill="${s.color}" stroke="${surfaceCol}" stroke-width="1"/>`;
        });
      } else {
        const last = validPts[validPts.length - 1];
        seriesSvg += `<circle cx="${xAt(last.idx, pts.length).toFixed(1)}" cy="${yAt(last.y).toFixed(1)}" r="3.4" fill="${s.color}" stroke="${surfaceCol}" stroke-width="1.5"/>`;
      }
    });

    // Crisp X-axis labels — ACTUALLY INSERTED INTO SVG!
    let xSvg = "";
    const primary = validSeries[0].points;
    if (!opts.hideX && primary.length >= 2) {
      const tickCount = compact ? 3 : Math.min(5, primary.length);
      const usedIdx = new Set();
      for (let k = 0; k < tickCount; k++) {
        const idx = tickCount === 1 ? 0 : Math.round((k / (tickCount - 1)) * (primary.length - 1));
        if (usedIdx.has(idx)) continue;
        usedIdx.add(idx);
        const anchor = k === 0 ? "start" : k === tickCount - 1 ? "end" : "middle";
        const lbl = primary[idx].shortLabel || primary[idx].label;
        xSvg += `<text x="${xAt(idx, primary.length).toFixed(1)}" y="${H - 5}" text-anchor="${anchor}" fill="${mutedCol}" font-size="${compact ? 9.5 : 10.5}" font-family="ui-monospace,SFMono-Regular,Menlo,monospace">${esc(lbl)}</text>`;
      }
    }

    const legendHtml =
      validSeries.length > 1 || opts.band
        ? `<div class="chart-legend">
            ${validSeries
              .map(
                (s) =>
                  `<span class="chart-legend-item"><i style="display:inline-block;width:10px;height:2.5px;background:${s.color};border-radius:2px;"></i>${esc(s.name)}</span>`
              )
              .join("")}
            ${opts.band ? `<span class="chart-legend-item"><i style="display:inline-block;width:10px;height:8px;background:rgba(56,189,248,0.22);border:1px solid rgba(56,189,248,0.45);border-radius:2px;"></i>72h 均值 ${fmtNum(opts.band.mean, 2)}${esc(suffix)} ±1σ</span>` : ""}
          </div>`
        : "";

    box.innerHTML = `
      ${legendHtml}
      <svg class="chart-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
        ${defsSvg ? `<defs>${defsSvg}</defs>` : ""}
        ${gridSvg}${bandSvg}${refSvg}${seriesSvg}${xSvg}
        <line class="crosshair" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + ih}" stroke="${mutedCol}" stroke-dasharray="2 2" stroke-width="1" style="display:none;"/>
        <circle class="hover-dot" cx="0" cy="0" r="4" fill="${validSeries[0].color}" stroke="${surfaceCol}" stroke-width="1.5" style="display:none;"/>
      </svg>
      <div class="chart-tooltip"></div>
    `;

    // Interactive crosshair + tooltip (desktop mouse & mobile touch)
    const svg = box.querySelector("svg");
    const tip = box.querySelector(".chart-tooltip");
    const cross = box.querySelector(".crosshair");
    const dot = box.querySelector(".hover-dot");

    const updatePointer = (clientX) => {
      const rect = svg.getBoundingClientRect();
      const relX = ((clientX - rect.left) / Math.max(1, rect.width)) * W;
      const clamped = Math.max(pad.left, Math.min(W - pad.right, relX));
      const frac = (clamped - pad.left) / iw;
      const idx = Math.max(0, Math.min(maxLen - 1, Math.round(frac * (maxLen - 1))));
      const snapX = xAt(Math.min(idx, primary.length - 1), primary.length);
      cross.setAttribute("x1", snapX.toFixed(1));
      cross.setAttribute("x2", snapX.toFixed(1));
      cross.style.display = "block";

      const p0 = primary[Math.min(idx, primary.length - 1)];
      if (Number.isFinite(p0?.y)) {
        dot.setAttribute("cx", snapX.toFixed(1));
        dot.setAttribute("cy", yAt(p0.y).toFixed(1));
        dot.style.display = "block";
      } else {
        dot.style.display = "none";
      }

      const rows = validSeries
        .map((s) => {
          const p = s.points[Math.min(idx, s.points.length - 1)];
          if (!p || !Number.isFinite(p.y)) return "";
          const rangeTxt =
            Number.isFinite(p.low) && Number.isFinite(p.high)
              ? ` <span class="muted">(${fmtNum(p.low, decimals)}~${fmtNum(p.high, decimals)}${esc(suffix)})</span>`
              : "";
          const noteTxt = p.note ? `<div class="muted">${esc(p.note)}</div>` : "";
          return `<div><span style="color:${s.color};">●</span> ${esc(s.name)}: <b>${fmtNum(p.y, decimals)}${esc(suffix)}</b>${rangeTxt}</div>${noteTxt}`;
        })
        .join("");

      tip.innerHTML = `<div class="muted" style="margin-bottom:3px;">${esc(p0.label)}</div>${rows}`;
      tip.style.display = "block";
      const leftPx = Math.min(box.clientWidth - tip.offsetWidth - 10, Math.max(10, clientX - rect.left + 12));
      tip.style.left = `${leftPx}px`;
      tip.style.top = "10px";
    };

    const hidePointer = () => {
      tip.style.display = "none";
      cross.style.display = "none";
      dot.style.display = "none";
    };

    svg.addEventListener("mousemove", (ev) => updatePointer(ev.clientX));
    svg.addEventListener("mouseleave", hidePointer);
    svg.addEventListener("touchstart", (ev) => {
      if (ev.touches?.[0]) updatePointer(ev.touches[0].clientX);
    }, { passive: true });
    svg.addEventListener("touchmove", (ev) => {
      if (ev.touches?.[0]) updatePointer(ev.touches[0].clientX);
    }, { passive: true });
    svg.addEventListener("touchend", hidePointer);
  }

  function renderBarChart(containerId, items, opts = {}) {
    const box = $(containerId);
    if (!box) return;
    const validItems = (items || []).filter(
      (x) => x.value !== null && x.value !== undefined && Number.isFinite(Number(x.value))
    );
    if (!validItems.length) {
      box.innerHTML = `<div class="muted small" style="padding:56px 0;text-align:center;">暂无资金流数据</div>`;
      return;
    }

    const W = measureChartWidth(box);
    const H = opts.height || (W < 420 ? 196 : 228);
    const compact = W < 420;
    const pad = { top: 16, right: 14, bottom: 26, left: compact ? 48 : 58 };
    const iw = Math.max(80, W - pad.left - pad.right);
    const ih = Math.max(60, H - pad.top - pad.bottom);

    // Smart non-symmetric Y-axis: never waste 50% empty canvas when all flows are negative or positive!
    const vals = validItems.map((x) => Number(x.value));
    let minY = Math.min(0, ...vals);
    let maxY = Math.max(0, ...vals);
    const span = Math.max(10, maxY - minY);
    if (maxY === 0) maxY = span * 0.14;
    else maxY += span * 0.12;
    if (minY === 0) minY = -span * 0.14;
    else minY -= span * 0.12;

    const yAt = (v) => pad.top + ((maxY - v) / (maxY - minY)) * ih;
    const zeroY = yAt(0);

    const lineCol = cssVar("--line", "rgba(148,163,184,0.16)");
    const mutedCol = cssVar("--muted", "#7c899e");
    const upCol = cssVar("--up", "#10b981");
    const downCol = cssVar("--down", "#f43f5e");

    // 4 clean gridlines + explicit zero baseline
    let grid = "";
    for (let i = 0; i <= 4; i++) {
      const v = maxY - (i / 4) * (maxY - minY);
      const y = pad.top + (i / 4) * ih;
      grid += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${lineCol}" stroke-dasharray="3 3" stroke-width="1"/>`;
      grid += `<text x="${pad.left - 7}" y="${(y + 3.5).toFixed(1)}" text-anchor="end" fill="${mutedCol}" font-size="${compact ? 10 : 10.5}" font-family="ui-monospace,SFMono-Regular,Menlo,monospace">${fmtSignedNum(v, 0)}M</text>`;
    }
    grid += `<line x1="${pad.left}" y1="${zeroY.toFixed(1)}" x2="${W - pad.right}" y2="${zeroY.toFixed(1)}" stroke="${mutedCol}" stroke-width="1.2" opacity="0.85"/>`;

    const step = iw / validItems.length;
    const barW = Math.max(8, Math.min(40, step * 0.6));
    let bars = "";
    validItems.forEach((it, i) => {
      const cx = pad.left + step * (i + 0.5);
      const v = Number(it.value);
      const y = v >= 0 ? yAt(v) : zeroY;
      const h = Math.max(2, Math.abs(yAt(v) - zeroY));
      const fill = v >= 0 ? upCol : downCol;
      bars += `<rect class="etf-bar" data-idx="${i}" x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="3" fill="${fill}" opacity="0.9" style="cursor:pointer;"/>`;
      const showLabel = !compact || validItems.length <= 8 || i % 2 === 0 || i === validItems.length - 1;
      if (showLabel) {
        bars += `<text x="${cx.toFixed(1)}" y="${H - 6}" text-anchor="middle" fill="${mutedCol}" font-size="${compact ? 9.5 : 10.5}" font-family="ui-monospace,SFMono-Regular,Menlo,monospace">${esc(it.label.slice(5))}</text>`;
      }
    });

    box.innerHTML = `
      <svg class="chart-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${grid}${bars}</svg>
      <div class="chart-tooltip"></div>
    `;
    const tip = box.querySelector(".chart-tooltip");
    box.querySelectorAll(".etf-bar").forEach((bar) => {
      const showTip = (clientX) => {
        const it = validItems[Number(bar.dataset.idx)];
        const rect = box.getBoundingClientRect();
        tip.innerHTML = `<div><b>${esc(it.label)}</b></div><div class="${dirCls(it.value)}">净流向: <b>${fmtSignedNum(it.value, 1, "M$")}</b></div>${it.sub ? `<div class="muted">${esc(it.sub)}</div>` : ""}`;
        tip.style.display = "block";
        const leftPx = Math.min(box.clientWidth - tip.offsetWidth - 10, Math.max(10, clientX - rect.left + 12));
        tip.style.left = `${leftPx}px`;
        tip.style.top = "10px";
      };
      bar.addEventListener("mousemove", (ev) => showTip(ev.clientX));
      bar.addEventListener("mouseleave", () => { tip.style.display = "none"; });
      bar.addEventListener("touchstart", (ev) => {
        if (ev.touches?.[0]) showTip(ev.touches[0].clientX);
      }, { passive: true });
    });
  }

  /* ---------- Renderers ---------- */
  function renderLiveRibbon() {
    const ribbon = $("live-ribbon");
    if (!ribbon) return;

    const cryptoPills = [];
    ["BTC", "ETH", "SOL", "BNB", "HYPE"].forEach((sym) => {
      const live = state.liveCrypto[sym];
      if (!live || !Number.isFinite(live.price)) return;
      const prev = state.prevLivePrice[sym];
      const flash =
        Number.isFinite(prev) && prev !== live.price
          ? live.price > prev
            ? "flash-up"
            : "flash-down"
          : "";
      state.prevLivePrice[sym] = live.price;
      const dec = sym === "HYPE" ? 3 : live.price >= 1000 ? 1 : 2;
      cryptoPills.push({
        sym,
        px: `$${fmtNum(live.price, dec)}`,
        chg: live.chg_pct,
        sess: "",
        flash,
      });
    });

    const macro = state.overview?.macro_quotes || [];
    const macroPills = [];
    ["SPY", "QQQ", "NVDA", "MSTR", ".VIX", "US10Y", "XAU="].forEach((s) => {
      const hit = macro.find((m) => m.symbol === s);
      if (!hit || !Number.isFinite(Number(hit.price))) return;
      const liveBs = hit.session === "BSTOCKS_247" ? state.liveBstocks[s] : null;
      const pxVal = liveBs ? liveBs.price : Number(hit.price);
      const chgVal = liveBs ? liveBs.chg_pct : hit.chg_pct;
      const prefix = s === ".VIX" || s === "US10Y" ? "" : "$";
      const suffix = s === "US10Y" ? "%" : "";
      macroPills.push({
        sym: s.replace("=", "").replace(".", ""),
        px: `${prefix}${fmtNum(pxVal, s === "US10Y" ? 3 : 2)}${suffix}`,
        chg: chgVal,
        sess: sessionZh(hit.session),
        isBstock: hit.session === "BSTOCKS_247",
        flash: "",
      });
    });

    if (!cryptoPills.length && !macroPills.length) {
      ribbon.innerHTML = `<div class="tick-pill muted">正在同步公开行情…</div>`;
      return;
    }

    const renderPill = (p) =>
      `<div class="tick-pill ${p.flash}"><span class="sym">${esc(p.sym)}</span><span class="px">${esc(p.px)}</span>${
        p.chg !== null && p.chg !== undefined && Number.isFinite(Number(p.chg))
          ? `<span class="chg ${dirCls(p.chg)}">${fmtSignedPct(p.chg, 2)}</span>`
          : ""
      }${p.sess ? `<span class="sess${p.isBstock ? " sess-bstock" : ""}">${esc(p.sess)}</span>` : ""}</div>`;

    ribbon.innerHTML =
      cryptoPills.map(renderPill).join("") +
      (cryptoPills.length && macroPills.length ? `<span class="ribbon-divider" aria-hidden="true"></span>` : "") +
      macroPills.map(renderPill).join("");
  }

  function renderOverview() {
    const ov = state.overview;
    if (!ov) return;
    const bd = ov.bottom_dashboard || {};
    const fng = ov.fear_greed;
    const st = ov.stablecoins;
    const tga = ov.tga;
    const mstr = state.flows?.mstr?.latest;
    const mstrSec = state.flows?.mstr?.sec_latest;

    // 1. 5-Second Market Regime & Desk Radar
    const reg = ov.market_regime || {};
    const rBadge = $("regime-badge");
    if (rBadge) {
      rBadge.textContent = reg.label || "市场常态观察";
      rBadge.className = `regime-badge ${esc(reg.tone || "neutral")}`;
    }
    if ($("regime-hint")) $("regime-hint").textContent = reg.hint || "";

    const chips = [];
    if (reg.bottom_score) chips.push(`底部共振 <b>${esc(reg.bottom_score)}</b>`);
    if (reg.fng !== null && reg.fng !== undefined) chips.push(`恐慌 <b>${fmtNum(reg.fng, 0)}</b>`);
    if (reg.vix !== null && reg.vix !== undefined) chips.push(`VIX <b>${fmtNum(reg.vix, 2)}</b>`);
    if (reg.us10y !== null && reg.us10y !== undefined) chips.push(`US10Y <b>${fmtNum(reg.us10y, 3)}%</b>`);
    if (tga && Number.isFinite(Number(tga.bal_b))) {
      chips.push(`TGA <b>$${fmtNum(tga.bal_b, 1)}B</b> <span class="muted">(5d ${fmtSignedNum(tga.chg_5d_b, 1)}B)</span>`);
    }
    $("regime-chips").innerHTML = chips.map((c) => `<span class="regime-chip">${c}</span>`).join("");

    const signals = ov.desk_signals || [];
    const targetAnchorMap = {
      overview: "qdii-card",
      war: "war-arena-card",
      arb: "csi-card",
      flows: "oi-card",
    };
    $("desk-signals").innerHTML = signals.length
      ? signals
          .map((s) => {
            const metricCls =
              s.severity === "danger"
                ? "risk"
                : s.severity === "warn"
                  ? "warn"
                  : s.severity === "opportunity"
                    ? "cushion"
                    : "ok";
            const anchor =
              s.tag === "机构估值锚"
                ? "mstr-card"
                : s.tag === "跨市场偏离"
                  ? "spread-card"
                  : s.tag === "充提防飞刀"
                    ? "wallet-card"
                    : targetAnchorMap[s.target_tab] || "";
            return `
            <button class="signal-card sev-${esc(s.severity || "info")}" type="button" data-jump-tab="${esc(s.target_tab || "overview")}" data-jump-el="${esc(anchor)}">
              <div class="signal-top">
                <span class="signal-tag">${esc(s.tag)}</span>
                <span class="signal-metric ${metricCls}">${esc(s.metric)}</span>
              </div>
              <div class="signal-title">${esc(s.title)}</div>
              <div class="signal-detail">${esc(s.detail)}</div>
            </button>`;
          })
          .join("")
      : `<div class="muted small">当前无极端结构异常信号</div>`;

    $("desk-signals").querySelectorAll(".signal-card").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.jumpTab, btn.dataset.jumpEl || null));
    });

    // 2. 4 Core KPI Tiles (Neutral Stock + Semantic Visual Indicators)
    const segs = (bd.items || []).map((it) =>
      it.status === "GREEN" ? "g" : it.status === "YELLOW" ? "y" : it.status === "RED" ? "r" : ""
    );
    const fngPct = fng && Number.isFinite(Number(fng.value)) ? Math.max(0, Math.min(100, Number(fng.value))) : 50;
    const fngZh =
      fng?.label === "Extreme Fear"
        ? "极度恐慌"
        : fng?.label === "Fear"
          ? "恐慌"
          : fng?.label === "Greed"
            ? "贪婪"
            : fng?.label === "Extreme Greed"
              ? "极度贪婪"
              : fng?.label || "--";

    $("overview-kpis").innerHTML = `
      <article class="card kpi-card">
        <div class="kpi-head">
          <span class="kpi-label">底部周期共振</span>
          <span class="kpi-scope">≥5 项转绿观察</span>
        </div>
        <strong class="kpi-value">${fmtInt(bd.green)} <span class="muted" style="font-size:15px;">/ ${fmtInt(bd.total)}</span></strong>
        <div class="seg-bar">${segs.map((c) => `<span class="${c}"></span>`).join("")}</div>
        <div class="kpi-sub"><span>绿 ${fmtInt(bd.green)}</span><span>黄 ${fmtInt(bd.yellow)}</span><span>红 ${fmtInt(bd.red)}</span></div>
      </article>

      <article class="card kpi-card">
        <div class="kpi-head">
          <span class="kpi-label">加密恐慌贪婪指数</span>
          <span class="kpi-scope">0–100</span>
        </div>
        <strong class="kpi-value ${fngPct <= 20 ? "risk" : fngPct >= 75 ? "warn" : ""}">${fng ? fmtNum(fng.value, 0) : "--"}</strong>
        <div class="fng-track"><span class="fng-needle" style="left:${fngPct}%;"></span></div>
        <div class="kpi-sub"><span>${esc(fngZh)}</span><span class="mono">${esc(fng?.label || "")}</span></div>
      </article>

      <article class="card kpi-card">
        <div class="kpi-head">
          <span class="kpi-label">全网稳定币总供给</span>
          <span class="kpi-scope">DeFiLlama 全口径</span>
        </div>
        <strong class="kpi-value">${st ? `$${fmtNum(st.total_b, 2)}B` : "--"}</strong>
        <div class="kpi-sub mono">
          ${
            st
              ? `<span class="${dirCls(st.chg_1d_b)}">1d ${fmtSignedNum(st.chg_1d_b, 2, "B")}</span>
                 <span class="${dirCls(st.chg_7d_b)}">7d ${fmtSignedNum(st.chg_7d_b, 2, "B")}</span>
                 <span class="${dirCls(st.chg_30d_b)}">30d ${fmtSignedNum(st.chg_30d_b, 1, "B")}</span>`
              : "--"
          }
        </div>
      </article>

      <article class="card kpi-card">
        <div class="kpi-head">
          <span class="kpi-label">Strategy (MSTR) mNAV</span>
          <span class="kpi-scope">${mstr && mstr.mnav < 1.0 ? "低于净资产" : "相对溢价"}</span>
        </div>
        <strong class="kpi-value ${mstr && mstr.mnav < 1.0 ? "warn" : ""}">${mstr ? `${fmtNum(mstr.mnav, 3)}x` : "--"}</strong>
        <div class="kpi-sub mono">
          <span>${mstr ? `持仓 ${fmtNum(mstr.holdings, 0)} BTC` : "--"}</span>
          ${mstrSec?.avg_cost ? `<span>均价 $${fmtNum(mstrSec.avg_cost, 0)}</span>` : ""}
        </div>
      </article>
    `;

    // 3. Bottom 7-point matrix
    const scorePill = $("bottom-score-pill");
    scorePill.textContent = `${bd.green ?? 0} / ${bd.total ?? 7} 达标`;
    scorePill.className = `pill ${bd.green >= 5 ? "pill-ok" : bd.green >= 3 ? "pill-warn" : "pill-risk"}`;

    const statusBadge = (s) => {
      if (s === "GREEN") return `<span class="pill pill-ok">已达标</span>`;
      if (s === "YELLOW") return `<span class="pill pill-warn">观察中</span>`;
      if (s === "RED") return `<span class="pill pill-risk">未满足</span>`;
      return `<span class="pill pill-muted">未知</span>`;
    };
    $("bottom-list").innerHTML = (bd.items || [])
      .map(
        (it) => `
      <div class="bottom-item">
        <div>${statusBadge(it.status)}</div>
        <div>
          <div class="b-title">${esc(it.label)}</div>
          <div class="b-detail">${esc(it.detail || "暂无说明")}</div>
        </div>
      </div>`
      )
      .join("");

    // 4. Desk Brief (explicitly marks stale >14h reports!)
    const brief = state.feed?.daily_analysis;
    if (brief) {
      const bTime = $("brief-time");
      const dtShort = (brief.dt_bjt || "").slice(5, 16);
      bTime.textContent = brief.is_stale ? `昨日研判 · ${dtShort}` : dtShort || "--";
      bTime.className = `pill mono ${brief.is_stale ? "pill-warn" : "pill-muted"}`;
      $("brief-box").textContent = brief.body || "暂无正文";
    } else {
      $("brief-box").textContent = "等待下一轮 17:00 BJT 客观短线研判生成。";
    }

    // 5. Miner metrics
    const miner = ov.btc_miner;
    if (miner) {
      $("miner-elec").textContent = `电费 $${fmtNum(miner.elec_usd_kwh, 2)}/kWh`;
      const btcPx = state.liveCrypto.BTC?.price;
      const vsTag = (usd) => {
        if (!Number.isFinite(btcPx) || !Number.isFinite(usd) || usd <= 0) return "";
        return btcPx < usd ? "risk" : "";
      };
      $("miner-box").innerHTML = [
        { k: "S21 Pro 关机价", v: `$${fmtNum(miner.s21pro_usd, 0)}`, cls: vsTag(miner.s21pro_usd) },
        { k: "S21 关机价", v: `$${fmtNum(miner.s21_usd, 0)}`, cls: vsTag(miner.s21_usd) },
        { k: "S19k 关机价", v: `$${fmtNum(miner.s19k_usd, 0)}`, cls: vsTag(miner.s19k_usd) },
        { k: "全网算力", v: `${fmtNum(miner.hashrate_eh, 1)} EH/s` },
        { k: "下次难度预估", v: fmtSignedPct(miner.diff_chg_pct, 2), cls: dirCls(miner.diff_chg_pct) },
        { k: "调难度进度", v: `${fmtInt(miner.remaining_blocks)} 块 (${fmtNum(miner.progress_pct, 0)}%)` },
      ]
        .map((m) => `<div class="miner-tile"><span>${esc(m.k)}</span><strong class="${m.cls || ""}">${esc(m.v)}</strong></div>`)
        .join("");
    }

    // 6. US 10Y 14d mini sparkline chart (balances right stack & adds real macro depth!)
    const u10 = ov.us10y_history || [];
    if (u10.length) {
      const lastU = u10[u10.length - 1];
      const firstU = u10[0];
      const deltaBp = (lastU.yield_pct - firstU.yield_pct) * 100;
      $("us10y-latest").innerHTML = `最新 <b>${fmtNum(lastU.yield_pct, 3)}%</b> · 区间 ${fmtSignedNum(deltaBp, 0, "bp")}`;
      if (state.activeTab === "overview") {
        renderLineChart(
          "us10y-chart-box",
          [
            {
              name: "US10Y",
              color: cssVar("--accent", "#38bdf8"),
              points: u10.map((r) => ({ label: r.date, shortLabel: r.date.slice(5), y: r.yield_pct })),
            },
          ],
          { height: 126, suffix: "%", decimals: 2, yTicks: 3, refLine: 5.0 }
        );
      }
    }

    // 7. Macro Matrix (filterable, real reg_chg, session chip, 52W drawdown!)
    renderMacroGrid();

    // 8. QDII Premium Risk Table
    const qdii = ov.qdii_premium || [];
    if (ov.qdii_updated_at) {
      $("qdii-updated").textContent = `快照 ${fmtTimeShort(ov.qdii_updated_at)}`;
    }
    $("qdii-tbody").innerHTML = qdii.length
      ? qdii
          .map((q) => {
            const p = Number(q.prem_pct);
            const barW = Math.min(100, Math.max(4, Math.abs(p) * 2.2));
            const riskCls = p >= 20 ? "risk" : p >= 15 ? "warn" : p <= 0 ? "ok" : "";
            const barCol = p >= 20 ? "var(--risk)" : p >= 15 ? "var(--warn)" : "var(--ok)";
            return `
          <tr class="${p >= 20 ? "row-risk" : p >= 15 ? "row-highlight" : ""}">
            <td><b class="mono">${esc(q.code)}</b> <span class="muted small">${esc(q.name)}</span></td>
            <td class="num">${fmtNum(q.px, 3)}</td>
            <td class="num hide-xs muted">${fmtNum(q.iopv, 4)}</td>
            <td class="num ${dirCls(q.chg_pct)}">${fmtSignedPct(q.chg_pct, 2)}</td>
            <td class="num">
              <span class="prem-cell">
                <span class="prem-bar hide-xs"><i style="width:${barW.toFixed(0)}%;background:${barCol};"></i></span>
                <b class="${riskCls}">${fmtSignedPct(q.prem_pct, 2)}</b>
              </span>
            </td>
          </tr>`;
          })
          .join("")
      : `<tr><td colspan="5" class="muted">暂无 QDII 溢价数据</td></tr>`;
  }

  function renderMacroGrid() {
    const ov = state.overview;
    if (!ov) return;
    const all = ov.macro_quotes || [];
    const filtered = all.filter((m) => {
      if (state.activeMacroGroup === "ALL") return true;
      const isEq = ["美股指数", "波动率", "算力存储", "币股", "科技巨头", "EQUITY"].includes(m.group);
      return state.activeMacroGroup === "EQUITY" ? isEq : !isEq;
    });

    const hasBstocks = Boolean(ov.bstocks_active || all.some((m) => m.session === "BSTOCKS_247"));
    const anyClosed = all.some((m) => m.is_closed);
    if ($("macro-session-note")) {
      $("macro-session-note").textContent = hasBstocks
        ? "美股休市/盘外时段自动以币安现货 bStocks (24/7) 实时报价为主，下方保留官方上一常规盘昨收对照。"
        : anyClosed
          ? "美股当前处于休市/盘前时段：主涨跌幅为上一常规交易日真实收盘表现，拒绝 0.00% 死价误导。"
          : "美股常规交易时段实时更新，并附带距 52 周高点回撤深度。";
    }

    $("macro-grid").innerHTML = filtered
      .map((m) => {
        const prefix = [".VIX", "US10Y", ".DXY", "JPY=", "CNH="].includes(m.symbol) ? "" : "$";
        const suffix = m.symbol === "US10Y" ? "%" : "";
        const dec = m.symbol === "US10Y" ? 3 : m.symbol === "CNH=" ? 4 : 2;
        const isBs = m.session === "BSTOCKS_247";
        const liveBs = isBs ? state.liveBstocks[m.symbol] : null;
        const pxVal = liveBs ? liveBs.price : m.price;
        const chgVal = liveBs ? liveBs.chg_pct : m.chg_pct;
        const sTag = isBs ? "bStocks 24/7" : sessionZh(m.session);
        const hasExt = !isBs && m.ext_px !== null && m.ext_px !== undefined && m.ext_chg !== null && m.ext_chg !== undefined;
        const hasRegRef = isBs && m.reg_px !== null && m.reg_px !== undefined;
        return `
        <div class="macro-tile">
          <div class="macro-left">
            <div class="macro-sym-row">
              <span class="macro-sym">${esc(m.symbol.replace("=", "").replace(".", ""))}</span>
              ${sTag ? `<span class="sess-tag${isBs ? " sess-bstock" : ""}">${esc(sTag)}</span>` : ""}
            </div>
            <div class="macro-name">${esc(m.name)}</div>
            ${m.dd_pct !== null && m.dd_pct !== undefined ? `<div class="macro-dd">距52W高 ${fmtNum(m.dd_pct, 1)}%</div>` : ""}
          </div>
          <div class="macro-right">
            <div class="macro-px">${prefix}${fmtNum(pxVal, dec)}${suffix}</div>
            <div class="macro-chg ${dirCls(chgVal)}">${chgVal !== null && chgVal !== undefined ? fmtSignedPct(chgVal, 2) : "--"}</div>
            ${hasRegRef ? `<div class="macro-ext">昨收 $${fmtNum(m.reg_px, 2)}${m.reg_chg_pct != null ? ` (${fmtSignedPct(m.reg_chg_pct, 1)})` : ""}</div>` : ""}
            ${hasExt ? `<div class="macro-ext">${esc(sTag)} ${fmtSignedPct(m.ext_chg, 2)}</div>` : ""}
          </div>
        </div>`;
      })
      .join("");
  }

  function renderArb() {
    const arb = state.arb;
    if (!arb) return;
    const sp = arb.spreads?.[state.activeSpreadPair];
    if (sp) {
      const lat = sp.latest || {};
      const b72 = sp.band_72h;
      const zAbs = b72 ? Math.abs(Number(b72.zscore)) : 0;
      const kpis = [
        { k: sp.leg_a, v: `$${fmtNum(lat.px_a, 2)}`, s: fmtTimeShort(lat.ts) },
        {
          k: sp.leg_b,
          v: `$${fmtNum(lat.px_b_adj, 2)}`,
          s: lat.px_b_raw && lat.px_b_raw !== lat.px_b_adj ? `原价 $${fmtNum(lat.px_b_raw, 2)}` : "实时基准",
        },
        {
          k: "当前跨市场价差",
          v: fmtSignedPct(lat.spread_pct, 3),
          s: `绝对差 ${fmtSignedNum(lat.spread_usd, 2, " USD")}`,
          cls: "", // Structural spread stays neutral; Z-score carries signal color!
        },
        {
          k: "72h 常态带偏离 (Z-Score)",
          v: b72 ? `${fmtSignedNum(b72.zscore, 2)}σ` : "--",
          s: b72 ? `均值 ${fmtNum(b72.mean, 2)}% · 区间 ${fmtNum(b72.min, 2)}~${fmtNum(b72.max, 2)}%` : "样本积累中",
          cls: zAbs >= 1.5 ? "warn" : zAbs >= 1.0 ? "cushion" : "",
        },
      ];
      $("spread-kpis").innerHTML = kpis
        .map(
          (x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong class="${x.cls || ""}">${esc(x.v)}</strong><span>${esc(x.s)}</span></div>`
        )
        .join("");

      if (state.activeTab === "arb") {
        if (state.activeSpreadRange === "24h") {
          $("spread-chart-meta").textContent = `${sp.title} · 近 24 小时 15 分钟采样`;
          const pts = (sp.series_24h || []).map((p) => ({
            label: fmtTimeShort(p.ts),
            shortLabel: fmtTimeHM(p.ts),
            y: p.spread_pct,
          }));
          renderLineChart("spread-chart-box", [{ name: "价差%", color: cssVar("--ok", "#10b981"), points: pts }], {
            suffix: "%",
            decimals: 2,
            band: b72,
          });
        } else {
          $("spread-chart-meta").textContent = `${sp.title} · 近 14 天收盘价差与日内高低波动带`;
          const pts = (sp.daily_14d || []).map((d) => ({
            label: d.date,
            shortLabel: d.date.slice(5),
            y: d.last,
            low: d.min,
            high: d.max,
          }));
          renderLineChart("spread-chart-box", [{ name: "日收盘价差%", color: cssVar("--accent", "#38bdf8"), points: pts }], {
            suffix: "%",
            decimals: 2,
          });
        }
      }
    }

    // CSI Basis table (institutional cyan/emerald cushion styling!)
    const csiAll = arb.csi_basis || [];
    if (csiAll.length && $("csi-meta")) {
      const qd = csiAll[0].quote_date || "";
      const qt = (csiAll[0].quote_time || "").slice(0, 5);
      $("csi-meta").textContent = `行情快照 ${qd} ${qt} BJT · 负值表示贴水（期货低于现货），深色蓝青标注高年化贴水安全垫。`;
    }
    const filtered = state.activeCsiCode === "ALL" ? csiAll : csiAll.filter((c) => c.code === state.activeCsiCode);
    $("csi-tbody").innerHTML = filtered.length
      ? filtered
          .map((c) => {
            const isFar = String(c.contract || "").endsWith("2706");
            const ann = Number(c.ann_pct);
            const basisCls = ann <= -10 ? "cushion" : ann < 0 ? "ok" : "warn";
            return `
          <tr class="${isFar ? "row-highlight" : ""}">
            <td><b class="mono">${esc(c.contract)}</b>${isFar ? ` <span class="pill pill-cushion" style="padding:1px 5px;font-size:10px;">远月</span>` : ""}</td>
            <td class="hide-sm mono muted">${esc(c.expiry)}</td>
            <td class="num">${esc(fmtInt(c.days_left))}d</td>
            <td class="num hide-sm muted">${fmtNum(c.spot, 1)}</td>
            <td class="num">${fmtNum(c.fut, 1)}</td>
            <td class="num hide-xs muted">${fmtSignedNum(c.basis_pts, 1)}</td>
            <td class="num ${basisCls}">${fmtSignedPct(c.basis_pct, 2)}</td>
            <td class="num ${basisCls}"><b>${fmtSignedPct(c.ann_pct, 2)}</b></td>
          </tr>`;
          })
          .join("")
      : `<tr><td colspan="8" class="muted">暂无活跃股指期货贴水快照</td></tr>`;

    // CSI Term Curve Multi-Line Chart (balances right column & gives instant quant term-structure view!)
    if (state.activeTab === "arb" && csiAll.length) {
      const palette = { IC: "#38bdf8", IM: "#10b981", IF: "#f59e0b", IH: "#a78bfa" };
      const byCode = {};
      csiAll.forEach((c) => {
        if (!byCode[c.code]) byCode[c.code] = [];
        byCode[c.code].push(c);
      });
      const series = ["IM", "IC", "IF", "IH"]
        .filter((k) => byCode[k]?.length >= 2)
        .map((k) => ({
          name: k,
          color: palette[k],
          points: byCode[k]
            .sort((a, b) => a.days_left - b.days_left)
            .map((c) => ({
              label: `${c.contract.slice(2)} (${c.days_left}天)`,
              shortLabel: c.contract.slice(2),
              y: c.ann_pct,
            })),
        }));
      renderLineChart("csi-curve-box", series, {
        height: 185,
        suffix: "%",
        decimals: 1,
        showDots: true,
        area: false,
      });
    }

    // Wallet summary + major halts (fail-closed)
    const ws = arb.wallet_status || {};
    if (ws.updated_at && $("wallet-updated")) {
      $("wallet-updated").textContent = `检测 ${fmtTimeShort(ws.updated_at)}`;
    }
    const wsSummary = Array.isArray(ws.summary) ? ws.summary : [];
    $("wallet-summary").innerHTML = wsSummary.length
      ? wsSummary
          .map(
            (w) => `
      <div class="wallet-card">
        <div class="wallet-card-head">
          <span>${esc(w.exchange)}</span>
          <span class="muted small mono">${esc(fmtInt(w.total_coins))} 币</span>
        </div>
        <div class="wallet-card-sub">
          双停 <b class="${Number(w.both_halted) > 0 ? "warn" : ""}">${esc(fmtInt(w.both_halted))}</b> · 停充 ${esc(fmtInt(w.dep_only_halted))} · 停提 ${esc(fmtInt(w.wd_only_halted))}
        </div>
      </div>`
          )
          .join("")
      : `<div class="wallet-card"><div class="warn small"><b>充提数据源暂离线，状态未知</b></div></div>`;

    const halts = Array.isArray(ws.major_halts) ? ws.major_halts : [];
    const walletHealthy = ws.ok === true && Number(ws.exchanges_covered) >= 4;
    if (halts.length) {
      $("wallet-halts-tbody").innerHTML = halts
        .map(
          (h) => `
        <tr>
          <td><b class="mono">${esc(h.coin)}</b></td>
          <td>${esc(h.exchange)}</td>
          <td>${h.dep_ok ? `<span class="ok">正常</span>` : `<span class="risk"><b>暂停</b></span>`}</td>
          <td>${h.wd_ok ? `<span class="ok">正常</span>` : `<span class="risk"><b>暂停</b></span>`}</td>
        </tr>`
        )
        .join("");
    } else if (walletHealthy) {
      $("wallet-halts-tbody").innerHTML = `<tr><td colspan="4" class="ok">当前监控的主流核心币种在四所均充提正常</td></tr>`;
    } else {
      $("wallet-halts-tbody").innerHTML = `<tr><td colspan="4" class="warn"><b>状态未知：</b>${esc(ws.reason || "充提数据源不可用或覆盖不足 4 所")}</td></tr>`;
    }
  }

  function renderFlows() {
    const fl = state.flows;
    if (!fl) return;
    const asset = state.activeEtfAsset;
    const etfRows = fl.etf_flows?.[asset] || [];
    const sum = fl.etf_flows?.summary?.[asset] || {};
    const days = sum.days || etfRows.length || 0;

    if ($("etf-heading")) {
      $("etf-heading").textContent = `美国 ${asset} 现货 ETF 近 ${days} 交易日资金流矩阵`;
    }

    const streak = sum.streak;
    const streakText = streak
      ? `${streak.dir === "inflow" ? "连续净流入" : "连续净流出"} ${streak.days} 日`
      : "--";
    const streakCls = streak ? (streak.dir === "inflow" ? "up" : "down") : "";
    const worst = sum.worst_day;

    $("etf-kpis").innerHTML = [
      {
        k: `最新单日 (${sum.latest_date || "--"})`,
        v: fmtSignedNum(sum.latest_musd, 1, "M$"),
        s: "Farside 官方收盘口径",
        cls: dirCls(sum.latest_musd),
      },
      {
        k: "近 5 交易日累计",
        v: fmtSignedNum(sum.sum_5d_musd, 1, "M$"),
        s: "周度机构净流向",
        cls: dirCls(sum.sum_5d_musd),
      },
      {
        k: `近 ${days} 交易日累计`,
        v: fmtSignedNum(sum.sum_all_musd, 1, "M$"),
        s: "当前统计窗口汇总",
        cls: dirCls(sum.sum_all_musd),
      },
      {
        k: "连流节奏与极值日",
        v: streakText,
        s: worst ? `最大单日流出 ${fmtSignedNum(worst.musd, 1, "M$")} (${worst.date.slice(5)})` : "--",
        cls: streakCls,
      },
    ]
      .map((x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong class="${x.cls || ""}">${esc(x.v)}</strong><span>${esc(x.s)}</span></div>`)
      .join("");

    if (state.activeTab === "flows") {
      renderBarChart(
        "etf-chart-box",
        etfRows.map((r) => ({
          label: r.date,
          value: r.total_musd,
          sub: (r.issuers || []).slice(0, 4).map((i) => `${i.ticker} ${fmtSignedNum(i.musd, 1)}`).join(" | "),
        }))
      );
    }

    $("etf-tbody").innerHTML = [...etfRows]
      .reverse()
      .map(
        (r) => `
      <tr>
        <td class="mono">${esc(r.date)}</td>
        <td class="num ${dirCls(r.total_musd)}"><b>${fmtSignedNum(r.total_musd, 1, "M$")}</b></td>
        <td class="small mono">${(r.issuers || [])
          .map((i) => `<span class="${dirCls(i.musd)}" style="margin-right:9px;display:inline-block;">${esc(i.ticker)} ${fmtSignedNum(i.musd, 1)}</span>`)
          .join("")}</td>
      </tr>`
      )
      .join("");

    // MSTR
    const m = fl.mstr || {};
    const ml = m.latest || {};
    const ms = m.sec_latest || {};
    const mpill = $("mstr-mnav-pill");
    mpill.textContent = ml.mnav ? `mNAV ${fmtNum(ml.mnav, 3)}x` : "mNAV --";
    mpill.className = `pill mono ${ml.mnav < 1.0 ? "pill-warn" : "pill-ok"}`;
    $("mstr-kpis").innerHTML = [
      { k: "官方持仓 BTC", v: fmtNum(ml.holdings, 0), s: `BTC 估值参考 $${fmtNum(ml.btc_price, 0)}` },
      {
        k: "SEC 8-K 持仓均价",
        v: ms.avg_cost ? `$${fmtNum(ms.avg_cost, 0)}` : "--",
        s: ms.filing_date ? `最近披露 ${ms.filing_date} (+${fmtNum(ms.bought, 0)} BTC)` : "--",
      },
    ]
      .map((x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong>${esc(x.v)}</strong><span>${esc(x.s)}</span></div>`)
      .join("");

    if (state.activeTab === "flows") {
      renderLineChart(
        "mstr-chart-box",
        [
          {
            name: "mNAV",
            color: cssVar("--accent", "#38bdf8"),
            points: (m.series_7d || []).map((p) => ({
              label: fmtTimeShort(p.ts),
              shortLabel: fmtTimeShort(p.ts).slice(0, 5),
              y: p.mnav,
              note: p.btc_price ? `同期 BTC $${fmtNum(p.btc_price, 0)}` : "",
            })),
          },
        ],
        { height: 185, suffix: "x", decimals: 3, refLine: 1.0 }
      );
    }

    // Hyperliquid
    const hl = fl.hyperliquid || {};
    $("hl-kpis").innerHTML = [
      { k: "全网总持仓 OI", v: fmtCompact(hl.platform?.total_oi_usd), s: `24h 成交额 ${fmtCompact(hl.platform?.total_vol_usd)}` },
      {
        k: "援助基金 (AF) 持有 HYPE",
        v: hl.assistance_fund ? `${fmtNum(hl.assistance_fund.hype / 1e6, 2)}M` : "--",
        s: hl.assistance_fund?.hype_chg_24h != null ? `24h 增持 ${fmtSignedNum(hl.assistance_fund.hype_chg_24h, 0)} HYPE` : "--",
      },
    ]
      .map((x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong>${esc(x.v)}</strong><span>${esc(x.s)}</span></div>`)
      .join("");
    $("hl-tbody").innerHTML = (hl.top_markets || [])
      .map(
        (c) => `
      <tr>
        <td><b class="mono">${esc(c.coin)}</b></td>
        <td class="num">$${fmtNum(c.mark, c.mark < 10 ? 4 : 2)}</td>
        <td class="num">${fmtCompact(c.oi_usd)}</td>
        <td class="num hide-xs muted">${fmtCompact(c.day_vol)}</td>
        <td class="num ${dirCls(c.funding_ann_pct)}">${fmtSignedPct(c.funding_ann_pct, 1)}</td>
      </tr>`
      )
      .join("");

    // Binance OI & Extreme Funding Radar
    const oi = fl.bn_oi_radar || [];
    $("oi-tbody").innerHTML = oi.length
      ? oi
          .map((r) => {
            const badgeCls =
              r.tone === "danger"
                ? "pill-risk"
                : r.tone === "warn"
                  ? "pill-warn"
                  : r.tone === "squeeze"
                    ? "pill-cushion"
                    : r.tone === "info"
                      ? "pill-info"
                      : "pill-muted";
            const rowCls = r.tone === "danger" ? "row-risk" : r.tone === "warn" ? "row-highlight" : "";
            const frExt = r.funding_8h_pct !== null && Math.abs(r.funding_8h_pct) >= 0.08;
            return `
          <tr class="${rowCls}">
            <td><b class="mono">${esc(r.base)}</b> <span class="muted small hide-xs">${esc(r.symbol)}</span></td>
            <td class="num hide-sm">$${fmtNum(r.price, r.price < 1 ? 5 : 2)}</td>
            <td class="num ${dirCls(r.chg_1h_oi_pct)}"><b>${fmtSignedPct(r.chg_1h_oi_pct, 2)}</b></td>
            <td class="num ${dirCls(r.chg_1h_px_pct)}">${fmtSignedPct(r.chg_1h_px_pct, 2)}</td>
            <td class="num hide-sm">${fmtCompact(r.oi_usd)}</td>
            <td class="num hide-md muted">${fmtCompact(r.vol_24h)}</td>
            <td class="num ${frExt ? "risk" : dirCls(r.funding_8h_pct)}">
              <b>${fmtSignedPct(r.funding_8h_pct, 3)}</b>
              ${r.funding_ann_pct !== null && r.funding_ann_pct !== undefined ? `<span class="muted small"> (${fmtSignedNum(r.funding_ann_pct, 0, "%")})</span>` : ""}
            </td>
            <td><span class="pill ${badgeCls}">${esc(r.regime)}</span></td>
          </tr>`;
          })
          .join("")
      : `<tr><td colspan="8" class="muted">暂无币安 OI 雷达数据</td></tr>`;
  }

  function renderLabs() {
    const labs = state.labs;
    if (!labs) return;
    const dca = labs.dca_simulation || {};
    $("dca-disclaimer").textContent = dca.disclaimer || "";
    const strats = dca.strategies || {};
    const stratOrder = [
      { key: "FIXED", short: "固定定投 (FIXED)", color: "#38bdf8" },
      { key: "VA", short: "价值平均 (VA)", color: "#10b981" },
      { key: "MAFG", short: "均线+恐慌 (MAFG)", color: "#f59e0b" },
    ].filter((s) => strats[s.key]);

    const firstDays = strats.FIXED?.days || strats[stratOrder[0]?.key]?.days || 0;
    $("dca-days-pill").textContent = `已公开运行 ${firstDays} 天 · 截至 ${dca.latest_date || "--"}`;

    $("dca-strategies").style.gridTemplateColumns = `repeat(${Math.max(1, stratOrder.length)}, minmax(0, 1fr))`;
    $("dca-strategies").innerHTML = stratOrder
      .map(({ key }) => {
        const s = strats[key];
        return `
        <div class="stat-tile">
          <small>${esc(s.label)}</small>
          <strong class="${dirCls(s.ret_pct)}">${fmtSignedPct(s.ret_pct, 2)}</strong>
          <span>投入 $${fmtNum(s.in, 0)} → 市值 $${fmtNum(s.mv, 0)} (${fmtSignedNum(s.pnl, 1, "U")})</span>
        </div>`;
      })
      .join("");

    const hist = dca.history || [];
    if (state.activeTab === "dca") {
      renderLineChart(
        "dca-chart-box",
        stratOrder.map((st) => ({
          name: st.short,
          color: st.color,
          points: hist.map((h) => ({ label: h.date, shortLabel: h.date.slice(5), y: h[st.key]?.ret_pct })),
        })),
        { suffix: "%", decimals: 2, refLine: 0, showDots: hist.length <= 20, area: false }
      );
    }

    // Dynamic thead & tbody matching active strategies (ZERO empty `--` columns!)
    $("dca-asset-thead").innerHTML =
      `<th>标的</th>` + stratOrder.map((st) => `<th class="num">${esc(st.short)}</th>`).join("");
    const assets = Object.keys(strats[stratOrder[0]?.key]?.per_asset_ret_pct || {});
    $("dca-asset-tbody").innerHTML = assets
      .map(
        (a) =>
          `<tr><td><b class="mono">${esc(a)}</b></td>` +
          stratOrder
            .map((st) => {
              const v = strats[st.key]?.per_asset_ret_pct?.[a];
              return `<td class="num ${dirCls(v)}">${fmtSignedPct(v, 2)}</td>`;
            })
            .join("") +
          `</tr>`
      )
      .join("");

    // Curated Active Exchange Promos & Live Earn/Airdrop Radar (filterable!)
    const promos = labs.exchange_promos || [];
    const pCounts = { ALL: promos.length, HIGH_YIELD: 0, AIRDROP: 0, CAMPAIGN: 0, WARN: 0 };
    promos.forEach((p) => {
      const ec = p.earn_cat || (p.tier === "WARN" ? "WARN" : "CAMPAIGN");
      if (pCounts[ec] !== undefined) pCounts[ec] += 1;
    });
    const pLabels = { ALL: "全部", HIGH_YIELD: "高息理财", AIRDROP: "空投·打新", CAMPAIGN: "常规活动", WARN: "慎防反撸" };
    $("promo-cat-filter")?.querySelectorAll(".seg-btn").forEach((btn) => {
      const c = btn.dataset.cat;
      btn.textContent = `${pLabels[c] || c} (${pCounts[c] ?? 0})`;
    });
    const visiblePromos = promos.filter((p) => {
      if (state.activePromoCat === "ALL") return true;
      const ec = p.earn_cat || (p.tier === "WARN" ? "WARN" : "CAMPAIGN");
      return ec === state.activePromoCat;
    });

    $("promo-grid").innerHTML = visiblePromos.length
      ? visiblePromos
          .map((p) => {
            const href = safeHref(p.url);
            const ec = p.earn_cat || (p.tier === "WARN" ? "WARN" : "CAMPAIGN");
            const tierCls =
              ec === "WARN" ? "pill-warn" : ec === "HIGH_YIELD" ? "pill-ok" : ec === "AIRDROP" ? "pill-cushion" : "pill-muted";
            const badgeText = p.earn_cat_label || p.tier_label || p.worth_level;
            return `
          <article class="promo-card">
            <div class="promo-top">
              <span class="pill pill-info">${esc(p.exchange)}</span>
              <span class="pill ${tierCls}">${esc(badgeText)}</span>
            </div>
            <div class="promo-title">${
              href
                ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`
                : esc(p.title)
            }</div>
            <div class="promo-meta-row">
              ${p.reward ? `<div class="promo-kv"><span class="promo-k">收益</span><span>${esc(p.reward)}</span></div>` : ""}
              ${p.window ? `<div class="promo-kv"><span class="promo-k">时间</span><span class="mono">${esc(p.window)}</span></div>` : ""}
            </div>
            ${p.summary ? `<div class="promo-desc">${esc(p.summary)}</div>` : ""}
          </article>`;
          })
          .join("")
      : `<div class="muted small">当前分类暂无在期理财或羊毛活动</div>`;
  }

  /* ---------- Feed Sound Alerts, Chinese TTS & Live Unread Detection ---------- */
  function feedItemKey(it) {
    const idNum = Number(it?.id || 0);
    if (Number.isFinite(idNum) && idNum > 0) return `id:${idNum}`;
    const base = String(it?.headline_en || it?.headline || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, " ")
      .slice(0, 64);
    return `ts:${Number(it?.ts || 0)}|${base}`;
  }

  function addBoundedKey(setObj, key, limit) {
    if (!key || setObj.has(key)) return;
    setObj.add(key);
    while (setObj.size > limit) {
      const oldest = setObj.values().next().value;
      if (oldest === undefined) break;
      setObj.delete(oldest);
    }
  }

  function isMajorFeedItem(it) {
    const cc = String(it?.cat_code || "");
    const lvl = String(it?.level || "");
    return lvl === "major" || cc === "INTEL";
  }

  function ensureFeedAudioCtx(fromGesture = false) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!state.feedAudioCtx) {
        if (!fromGesture) return null;
        state.feedAudioCtx = new Ctx();
      }
      if (state.feedAudioCtx.state === "running") {
        state.feedAudioUnlocked = true;
      } else if (fromGesture && state.feedAudioCtx.state === "suspended") {
        state.feedAudioCtx
          .resume()
          .then(() => {
            state.feedAudioUnlocked = true;
            updateFeedAlertUi();
          })
          .catch(() => {});
      }
      return state.feedAudioCtx;
    } catch {
      return null;
    }
  }

  function scheduleFeedChimeNodes(ctx, isMajor) {
    try {
      const now = ctx.currentTime + 0.015;
      const notes = isMajor
        ? [
            { freq: 880, start: 0.0, dur: 0.14, gain: 0.15 },
            { freq: 1108.73, start: 0.1, dur: 0.15, gain: 0.14 },
            { freq: 1318.51, start: 0.2, dur: 0.28, gain: 0.16 },
          ]
        : [
            { freq: 659.25, start: 0.0, dur: 0.16, gain: 0.13 },
            { freq: 987.77, start: 0.11, dur: 0.24, gain: 0.11 },
          ];
      notes.forEach((n) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = isMajor ? "triangle" : "sine";
        osc.frequency.setValueAtTime(n.freq, now + n.start);
        gain.gain.setValueAtTime(0.0001, now + n.start);
        gain.gain.exponentialRampToValueAtTime(n.gain, now + n.start + 0.014);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + n.start + n.dur);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + n.start);
        osc.stop(now + n.start + n.dur + 0.02);
      });
    } catch {}
  }

  function playFeedChime(isMajor = false, fromGesture = false) {
    const ctx = ensureFeedAudioCtx(fromGesture);
    if (!ctx) return false;
    if (ctx.state === "running") {
      scheduleFeedChimeNodes(ctx, isMajor);
      return true;
    }
    if (fromGesture && ctx.state === "suspended") {
      ctx
        .resume()
        .then(() => {
          state.feedAudioUnlocked = true;
          scheduleFeedChimeNodes(ctx, isMajor);
          updateFeedAlertUi();
        })
        .catch(() => {});
      return true;
    }
    return false;
  }

  function stopFeedSpeech() {
    try {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    } catch {}
  }

  function speakFeedHeadlines(headlines) {
    if (!("speechSynthesis" in window) || typeof window.SpeechSynthesisUtterance !== "function") {
      return false;
    }
    const list = (headlines || [])
      .map((h) =>
        String(h || "")
          .replace(/https?:\/\/\S+/gi, "")
          .replace(/[#*_`~]/g, " ")
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, 75)
      )
      .filter(Boolean)
      .slice(0, 2);
    if (!list.length) return false;
    try {
      const synth = window.speechSynthesis;
      synth.cancel();
      const voices = typeof synth.getVoices === "function" ? synth.getVoices() : [];
      const zhVoice =
        voices.find((v) => /zh[-_]?cn/i.test(String(v.lang || ""))) ||
        voices.find((v) => /^zh/i.test(String(v.lang || ""))) ||
        null;
      list.forEach((txt) => {
        const u = new window.SpeechSynthesisUtterance(txt);
        u.lang = "zh-CN";
        if (zhVoice) u.voice = zhVoice;
        u.rate = 1.08;
        u.pitch = 1.0;
        u.volume = 0.92;
        synth.speak(u);
      });
      return true;
    } catch {
      return false;
    }
  }

  function updateFeedBadge() {
    const el = $("nav-feed-badge");
    if (!el) return;
    if (state.feedUnreadCount > 0 && state.activeTab !== "feed") {
      el.textContent = state.feedUnreadCount > 99 ? "99+" : `+${state.feedUnreadCount}`;
      el.hidden = false;
    } else {
      el.textContent = "0";
      el.hidden = true;
    }
  }

  function updateFeedAlertUi(customMsg = "") {
    $("feed-alert-mode")
      ?.querySelectorAll(".seg-btn")
      .forEach((b) => b.classList.toggle("active", b.dataset.mode === state.feedAlertMode));
    $("feed-alert-scope")
      ?.querySelectorAll(".seg-btn")
      .forEach((b) => b.classList.toggle("active", b.dataset.scope === state.feedAlertScope));
    const enBtn = $("btn-feed-show-en");
    if (enBtn) {
      enBtn.classList.toggle("btn-toggle-active", state.feedShowEn);
      enBtn.setAttribute("aria-pressed", state.feedShowEn ? "true" : "false");
    }
    const st = $("feed-alert-status");
    if (!st) return;
    if (customMsg) {
      st.textContent = customMsg;
      return;
    }
    const scopeTxt = state.feedAlertScope === "major" ? "仅重磅/预警" : "全部快讯";
    if (state.feedAlertMode === "off") {
      st.textContent = "已静音 · 仍每45秒自动拉取新快讯";
    } else if (state.feedAlertMode === "tts") {
      const ttsOk = "speechSynthesis" in window && typeof window.SpeechSynthesisUtterance === "function";
      if (!ttsOk) {
        st.textContent = `浏览器不支持语音合成，将回退提示音 · ${scopeTxt}`;
      } else {
        st.textContent = state.feedAudioUnlocked
          ? `中文语音播报已就绪 · ${scopeTxt} · 每45秒监测`
          : `语音播报待机（点击页面任意处解锁声音） · ${scopeTxt}`;
      }
    } else {
      st.textContent = state.feedAudioUnlocked
        ? `提示音已就绪 · ${scopeTxt} · 每45秒监测`
        : `提示音待机（点击页面任意处解锁声音） · ${scopeTxt}`;
    }
  }

  function validFeedTs(ts, nowSec) {
    const n = Number(ts);
    if (!Number.isFinite(n)) return 0;
    return n >= 1577836800 && n <= nowSec + 300 ? n : 0;
  }

  function parseSnapshotGenMs(meta) {
    const raw = String(meta?.generated_at_bjt || "").trim();
    if (!raw) return 0;
    const iso = raw.replace(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*([+-]\d{2}:?\d{2}|Z)?$/, "$1T$2$3");
    const ms = Date.parse(iso);
    return Number.isFinite(ms) ? ms : 0;
  }

  function ingestFeedData(feedObj, { notify = false } = {}) {
    if (!feedObj || !Array.isArray(feedObj.items)) return false;
    const snapGenMs = parseSnapshotGenMs(feedObj.meta);
    if (state.feedLatestGenMs > 0 && (snapGenMs === 0 || snapGenMs < state.feedLatestGenMs)) {
      // Drop unversioned or stale out-of-order response once a valid versioned snapshot has landed
      return false;
    }
    if (snapGenMs > state.feedLatestGenMs) {
      state.feedLatestGenMs = snapGenMs;
    }

    state.feed = feedObj;
    if (snapGenMs > 0 && feedObj.meta?.generated_at_bjt) {
      const gen = String(feedObj.meta.generated_at_bjt).replace(" +08:00", " BJT");
      const metaEl = $("meta-updated");
      if (metaEl) metaEl.textContent = `快照 ${gen.slice(5)}`;
    }

    const items = feedObj.items;
    const nowSec = Math.floor(Date.now() / 1000);
    let maxValidTs = 0;
    const nextSnapshotKeys = new Set();
    items.forEach((it) => {
      nextSnapshotKeys.add(feedItemKey(it));
      const vts = validFeedTs(it?.ts, nowSec);
      if (vts > maxValidTs) maxValidTs = vts;
    });

    if (!state.feedInitialized) {
      state.feedPrevSnapshotKeys = nextSnapshotKeys;
      for (let i = items.length - 1; i >= 0; i--) {
        addBoundedKey(state.feedSeenHistory, feedItemKey(items[i]), 400);
      }
      state.feedBaselineTs = maxValidTs;
      state.feedInitialized = true;
      renderFeed();
      updateFeedBadge();
      updateFeedAlertUi();
      return true;
    }

    // Self-heal baseline using the exact same validFeedTs guard (handles NaN, negative, stale, or future values)
    if (state.feedBaselineTs !== 0 && !validFeedTs(state.feedBaselineTs, nowSec)) {
      state.feedBaselineTs = maxValidTs;
    }

    const newlyArrived = [];
    const cutoffTs = state.feedBaselineTs > 0 ? state.feedBaselineTs - 300 : 0;
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i];
      const key = feedItemKey(it);
      const alreadySeen = state.feedPrevSnapshotKeys.has(key) || state.feedSeenHistory.has(key);
      if (!alreadySeen) {
        const vts = validFeedTs(it?.ts, nowSec);
        if (vts > 0 && (cutoffTs === 0 || vts >= cutoffTs)) {
          newlyArrived.push(it);
          addBoundedKey(state.feedFreshKeys, key, 30);
        }
      }
    }
    newlyArrived.reverse();

    // Commit previous snapshot full keyset + bounded history AFTER diffing (immune to >400 self-eviction!)
    state.feedPrevSnapshotKeys = nextSnapshotKeys;
    for (let i = items.length - 1; i >= 0; i--) {
      addBoundedKey(state.feedSeenHistory, feedItemKey(items[i]), 400);
    }
    if (maxValidTs > state.feedBaselineTs) {
      state.feedBaselineTs = maxValidTs;
    }

    if (notify && newlyArrived.length > 0) {
      const matched =
        state.feedAlertScope === "major" ? newlyArrived.filter(isMajorFeedItem) : newlyArrived;
      if (state.activeTab !== "feed") {
        state.feedUnreadCount = Math.min(999, state.feedUnreadCount + newlyArrived.length);
        updateFeedBadge();
      }
      if (matched.length > 0 && state.feedAlertMode !== "off") {
        const hasMajor = matched.some(isMajorFeedItem);
        const chimed = playFeedChime(hasMajor, false);
        let spoken = false;
        if (state.feedAlertMode === "tts") {
          spoken = speakFeedHeadlines(matched.map((x) => x.headline));
        }
        const nowStr = fmtTimeHM(Math.floor(Date.now() / 1000));
        if (chimed || spoken) {
          updateFeedAlertUi(
            `${nowStr} 收到 ${matched.length} 条新快讯（${spoken ? "已语音播报" : hasMajor ? "重磅提示音" : "提示音"}）`
          );
        } else {
          updateFeedAlertUi(`${nowStr} 收到 ${matched.length} 条新快讯（点击页面任意处解锁声音）`);
        }
      }
    }

    renderFeed();
    return true;
  }

  async function pollFeedLive() {
    if (state.feedPollInFlight) return;
    state.feedPollInFlight = true;
    state.lastFeedPollMs = Date.now();
    const ctrl = new AbortController();
    state.feedPollAbortCtrl = ctrl;
    const timeoutId = window.setTimeout(() => ctrl.abort(), 8000);
    try {
      const res = await fetch(`./api/v1/feed.json?t=${Date.now()}`, {
        cache: "no-store",
        signal: ctrl.signal,
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data || !Array.isArray(data.items)) return;
      ingestFeedData(data, { notify: true });
    } catch {
      // Fail-soft on transient timeout/offline
    } finally {
      window.clearTimeout(timeoutId);
      if (state.feedPollAbortCtrl === ctrl) state.feedPollAbortCtrl = null;
      state.feedPollInFlight = false;
    }
  }

  function renderFeed() {
    const items = state.feed?.items || [];
    // Update live 6-category counts in filter pills
    const counts = { ALL: items.length, CRYPTO: 0, MACRO: 0, LISTING: 0, EARN: 0, MOVES: 0, INTEL: 0 };
    items.forEach((it) => {
      const cc = it.cat_code || "CRYPTO";
      if (counts[cc] !== undefined) counts[cc] += 1;
    });
    const labels = {
      ALL: "全部",
      CRYPTO: "加密快讯",
      MACRO: "美股·宏观",
      LISTING: "上新·公告",
      EARN: "理财·羊毛",
      MOVES: "异动·OI",
      INTEL: "深度预警",
    };
    $("feed-cat-filter")?.querySelectorAll(".seg-btn").forEach((btn) => {
      const c = btn.dataset.cat;
      btn.textContent = `${labels[c] || c} (${counts[c] ?? 0})`;
    });

    const q = state.feedSearch.trim().toLowerCase();
    const filtered = items.filter((it) => {
      const cc = it.cat_code || "CRYPTO";
      if (state.activeFeedCat !== "ALL" && cc !== state.activeFeedCat) return false;
      if (
        q &&
        !`${it.headline} ${it.headline_en || ""} ${it.source} ${it.category} ${it.impact_label} ${it.market_ref} ${it.body} ${(it.coins || []).join(" ")}`
          .toLowerCase()
          .includes(q)
      )
        return false;
      return true;
    });

    const catPillCls = (cc, level) => {
      if (cc === "INTEL" || level === "major") return "pill-risk";
      if (cc === "EARN") return "pill-ok";
      if (cc === "MACRO") return "pill-warn";
      if (cc === "MOVES") return "pill-violet";
      if (cc === "LISTING") return "pill-cushion";
      return "pill-info";
    };

    $("feed-list").innerHTML = filtered.length
      ? filtered
          .map((it) => {
            const tone = it.impact_tone || "neutral";
            const impCls =
              tone === "bearish" ? "pill-risk" : tone === "bullish" ? "pill-ok" : tone === "warn" ? "pill-warn" : "pill-muted";
            const cc = it.cat_code || "CRYPTO";
            const href = safeHref(it.url);
            const hasEn = Boolean(it.headline_en && it.headline_en !== it.headline);
            const isFresh = state.feedFreshKeys.has(feedItemKey(it));
            return `
        <article class="feed-item tone-${esc(tone)}${isFresh ? " is-fresh" : ""}">
          <div class="feed-meta">
            ${isFresh ? `<span class="pill feed-new-pill">新</span>` : ""}
            <span class="pill ${catPillCls(cc, it.level)}">${esc(it.category || labels[cc] || "快讯")}</span>
            ${hasEn ? `<span class="pill pill-tr" title="已自动译为简体中文">已译</span>` : ""}
            ${it.source ? `<span class="pill pill-muted">${esc(it.source)}</span>` : ""}
            <span class="mono">${esc((it.dt_bjt || "").slice(5, 16))}</span>
            ${href ? `<a class="feed-ext-link" href="${esc(href)}" target="_blank" rel="noopener noreferrer">原文 ↗</a>` : ""}
          </div>
          <div class="feed-headline">${esc(it.headline)}</div>
          ${state.feedShowEn && hasEn ? `<div class="feed-en-sub">EN: ${esc(it.headline_en)}</div>` : ""}
          ${it.body ? `<div class="muted small" style="margin-top:4px;">${esc(it.body)}</div>` : ""}
          ${
            it.impact_label || it.market_ref
              ? `<div class="feed-foot">
                  ${it.impact_label ? `<span class="pill ${impCls}">${esc(it.impact_label)}</span>` : ""}
                  ${it.market_ref ? `<span class="feed-ref">${esc(it.market_ref)}</span>` : ""}
                </div>`
              : ""
          }
        </article>`;
          })
          .join("")
      : `<div class="muted small">没有匹配当前筛选条件的公开快讯</div>`;
  }

  function renderOpenApi() {
    const base = new URL("./api/v1/", window.location.href).href;
    const endpoints = [
      { file: "index.json", desc: "元信息、更新时间与全部接口索引" },
      { file: "overview.json", desc: "市场态势、异常雷达、底部仪表盘、宏观回撤、矿机与 QDII" },
      { file: "whales.json", desc: "实盘巨鲸多空背离、清算引力墙、CoinGlass 全网爆仓汇总与 24h 分时节奏" },
      { file: "arb.json", desc: "海力士/黄金价差带、中证贴水 2706 期限表、四所充提安全" },
      { file: "flows.json", desc: "BTC/ETH 现货 ETF 矩阵、MSTR mNAV、Hyperliquid、币安双维雷达" },
      { file: "labs.json", desc: "3 套模拟定投对比净值与在期交易所活动清单" },
      { file: "feed.json", desc: "去重脱敏多源实时快讯（含英文自动译中文原题对照）、交易所公告与每日研判" },
    ];
    $("api-list").innerHTML = endpoints
      .map(
        (ep) => `
      <div class="api-row">
        <div class="api-info">
          <div class="api-path">GET /api/v1/${esc(ep.file)}</div>
          <div class="api-desc">${esc(ep.desc)}</div>
        </div>
        <div class="api-actions">
          <button class="btn btn-sm api-try" data-file="${esc(ep.file)}" type="button">试调</button>
          <button class="btn btn-sm api-copy" data-url="${esc(base + ep.file)}" type="button">curl</button>
        </div>
      </div>`
      )
      .join("");

    document.querySelectorAll(".api-try").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const preview = $("api-preview");
        preview.textContent = `GET ./api/v1/${btn.dataset.file} …`;
        try {
          const res = await fetch(`./api/v1/${btn.dataset.file}`);
          const data = await res.json();
          preview.textContent = JSON.stringify(data, null, 2).slice(0, 6500);
        } catch (err) {
          preview.textContent = `请求失败: ${err.message}`;
        }
      });
    });

    document.querySelectorAll(".api-copy").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const cmd = `curl -sS "${btn.dataset.url}" | jq .`;
        try {
          await navigator.clipboard.writeText(cmd);
          btn.textContent = "已复制";
          setTimeout(() => (btn.textContent = "curl"), 1500);
        } catch {
          $("api-preview").textContent = cmd;
        }
      });
    });
  }

  function rerenderActiveCharts() {
    if (state.activeTab === "overview") renderOverview();
    else if (state.activeTab === "war") window.DanClubWarRoom?.rerender();
    else if (state.activeTab === "arb") renderArb();
    else if (state.activeTab === "flows") renderFlows();
    else if (state.activeTab === "dca") renderLabs();
  }

  /* ---------- Live Browser-Side Crypto + Binance Spot bStocks Tickers (Zero 451 Geo-Block!) ---------- */
  async function fetchLiveCryptoTickers() {
    try {
      const syms = encodeURIComponent(
        JSON.stringify(["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "SPYBUSDT", "QQQBUSDT", "NVDABUSDT", "MSTRBUSDT"])
      );
      const [bnRes, hlRes] = await Promise.allSettled([
        fetch(`https://data-api.binance.vision/api/v3/ticker/24hr?symbols=${syms}`, {
          signal: AbortSignal.timeout(4500),
        }),
        fetch("https://api.hyperliquid.xyz/info", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: "metaAndAssetCtxs" }),
          signal: AbortSignal.timeout(4500),
        }),
      ]);

      const bnFresh = new Set();
      let gotLive = false;
      const bstockMap = { SPYBUSDT: "SPY", QQQBUSDT: "QQQ", NVDABUSDT: "NVDA", MSTRBUSDT: "MSTR" };

      if (bnRes.status === "fulfilled" && bnRes.value.ok) {
        const arr = await bnRes.value.json();
        if (Array.isArray(arr)) {
          arr.forEach((it) => {
            const rawSym = String(it.symbol || "");
            const px = Number(it.lastPrice);
            const chg = Number(it.priceChangePercent);
            if (!Number.isFinite(px) || px <= 0) return;
            if (bstockMap[rawSym]) {
              state.liveBstocks[bstockMap[rawSym]] = { price: px, chg_pct: Number.isFinite(chg) ? chg : null };
              gotLive = true;
              return;
            }
            const sym = rawSym.replace("USDT", "");
            if (sym) {
              state.liveCrypto[sym] = { price: px, chg_pct: Number.isFinite(chg) ? chg : null };
              bnFresh.add(sym);
              gotLive = true;
            }
          });
        }
      }

      // Hyperliquid updates HYPE unconditionally AND refreshes BTC/ETH/BNB/SOL every cycle whenever Binance wasn't fresh (e.g. HTTP 451)
      if (hlRes.status === "fulfilled" && hlRes.value.ok) {
        const data = await hlRes.value.json();
        const universe = data?.[0]?.universe || [];
        const ctxs = data?.[1] || [];
        ["BTC", "ETH", "SOL", "BNB", "HYPE"].forEach((sym) => {
          if (bnFresh.has(sym)) return;
          const idx = universe.findIndex((u) => u.name === sym);
          if (idx >= 0 && ctxs[idx]) {
            const mark = Number(ctxs[idx].markPx);
            const prev = Number(ctxs[idx].prevDayPx);
            if (Number.isFinite(mark) && mark > 0) {
              state.liveCrypto[sym] = {
                price: mark,
                chg_pct: Number.isFinite(prev) && prev > 0 ? ((mark / prev) - 1) * 100 : null,
              };
              gotLive = true;
            }
          }
        });
      }

      // Fallback only for symbols still empty from server snapshot
      if (state.flows?.hyperliquid?.top_markets) {
        state.flows.hyperliquid.top_markets.forEach((c) => {
          if (["BTC", "ETH", "SOL", "BNB", "HYPE"].includes(c.coin) && !state.liveCrypto[c.coin]) {
            state.liveCrypto[c.coin] = { price: Number(c.mark), chg_pct: null };
          }
        });
      }

      $("live-badge").textContent = gotLive ? "实时 · 5s" : "快照模式";
      $("live-dot").classList.toggle("stale", !gotLive);
      renderLiveRibbon();
    } catch {
      $("live-badge").textContent = "快照模式";
      $("live-dot").classList.add("stale");
      renderLiveRibbon();
    }
  }

  async function loadSnapshots() {
    const bust = `v=${Math.floor(Date.now() / 30000)}`;
    const [overview, arb, flows, labs, feed, whales] = await Promise.all([
      fetch(`./api/v1/overview.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/arb.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/flows.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/labs.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/feed.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/whales.json?${bust}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    state.overview = overview;
    state.arb = arb;
    state.flows = flows;
    state.labs = labs;
    window.DanClubWarRoom?.setFlows?.(flows ?? null);
    if (whales) window.DanClubWarRoom?.setData(whales);

    // Seed crypto ribbon immediately from snapshot before first live WS/HTTP response lands
    (flows?.hyperliquid?.top_markets || []).forEach((c) => {
      if (["BTC", "ETH", "SOL", "BNB", "HYPE"].includes(c.coin) && !state.liveCrypto[c.coin] && Number.isFinite(Number(c.mark))) {
        state.liveCrypto[c.coin] = { price: Number(c.mark), chg_pct: null };
      }
    });

    const ovGenMs = parseSnapshotGenMs(overview?.meta);
    if (state.feedLatestGenMs === 0 || (ovGenMs > 0 && ovGenMs >= state.feedLatestGenMs)) {
      const gen = (overview?.meta?.generated_at_bjt || "--").replace(" +08:00", " BJT");
      $("meta-updated").textContent = `快照 ${gen.slice(5)}`;
    }
    state.lastFeedPollMs = Date.now();

    renderOverview();
    renderArb();
    renderFlows();
    renderLabs();
    ingestFeedData(feed, { notify: state.feedInitialized });
    renderLiveRibbon();
  }

  function bindControls() {
    $("macro-group-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.activeMacroGroup = btn.dataset.group;
      $("macro-group-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderMacroGrid();
    });
    $("spread-pair-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.activeSpreadPair = btn.dataset.pair;
      $("spread-pair-tabs").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderArb();
    });
    $("spread-range-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.activeSpreadRange = btn.dataset.range;
      $("spread-range-tabs").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderArb();
    });
    $("csi-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.activeCsiCode = btn.dataset.code;
      $("csi-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderArb();
    });
    $("etf-asset-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.activeEtfAsset = btn.dataset.asset;
      $("etf-asset-tabs").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderFlows();
    });
    $("promo-cat-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.activePromoCat = btn.dataset.cat;
      $("promo-cat-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderLabs();
    });
    $("feed-cat-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.activeFeedCat = btn.dataset.cat;
      $("feed-cat-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderFeed();
    });
    $("feed-search")?.addEventListener("input", (e) => {
      state.feedSearch = e.target.value;
      renderFeed();
    });
    $("feed-alert-mode")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      const mode = btn.dataset.mode;
      if (!["off", "chime", "tts"].includes(mode)) return;
      state.feedAlertMode = mode;
      safeStoreSet("danclub_feed_alert_mode", mode);
      if (mode === "off") {
        stopFeedSpeech();
        updateFeedAlertUi();
      } else if (mode === "chime") {
        stopFeedSpeech();
        playFeedChime(false, true);
        updateFeedAlertUi("已开启快讯提示音 · 每45秒自动监测新快讯");
      } else {
        playFeedChime(false, true);
        const sample = state.feed?.items?.[0]?.headline || "市场快讯中文语音播报已开启";
        const ok = speakFeedHeadlines([sample]);
        updateFeedAlertUi(
          ok
            ? "已开启中文语音播报 · 正在播报最新快讯"
            : "当前浏览器不支持语音合成，已回退提示音提醒"
        );
      }
    });
    $("feed-alert-scope")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      const scope = btn.dataset.scope;
      if (!["all", "major"].includes(scope)) return;
      state.feedAlertScope = scope;
      safeStoreSet("danclub_feed_alert_scope", scope);
      updateFeedAlertUi();
    });
    $("btn-feed-test-sound")?.addEventListener("click", () => {
      const isMajorPreview = state.feedAlertScope === "major";
      playFeedChime(isMajorPreview, true);
      if (state.feedAlertMode === "tts") {
        const sample = state.feed?.items?.[0]?.headline || "丹俱乐部终端快讯语音播报已就绪";
        const ok = speakFeedHeadlines([sample]);
        updateFeedAlertUi(ok ? "试听中 · 中文语音播报已触发" : "已试听提示音（浏览器不支持语音合成）");
      } else if (state.feedAlertMode === "off") {
        updateFeedAlertUi("试听完成 · 当前为静音，切换提示音或语音播报后自动提醒");
      } else {
        updateFeedAlertUi(isMajorPreview ? "已试听重磅三连提示音 · 声音已就绪" : "已试听快讯提示音 · 声音已就绪");
      }
    });
    $("btn-feed-show-en")?.addEventListener("click", () => {
      state.feedShowEn = !state.feedShowEn;
      safeStoreSet("danclub_feed_show_en", state.feedShowEn ? "1" : "0");
      updateFeedAlertUi();
      renderFeed();
    });
    $("btn-refresh")?.addEventListener("click", () => {
      loadSnapshots().catch(console.error);
      fetchLiveCryptoTickers();
    });

    // Unlock Web Audio silently on first user interaction so background feed chimes play cleanly
    const unlockOnce = () => {
      if (state.feedAlertMode !== "off") {
        ensureFeedAudioCtx(true);
        if (state.feedAudioUnlocked) updateFeedAlertUi();
      }
      document.removeEventListener("pointerdown", unlockOnce, true);
      document.removeEventListener("keydown", unlockOnce, true);
    };
    document.addEventListener("pointerdown", unlockOnce, true);
    document.addEventListener("keydown", unlockOnce, true);

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && Date.now() - state.lastFeedPollMs >= 15000) {
        pollFeedLive();
      }
    });
    window.addEventListener("pagehide", () => {
      stopFeedSpeech();
      try { state.feedPollAbortCtrl?.abort(); } catch {}
    });

    // Re-render active 1:1 SVG charts cleanly whenever viewport width changes
    window.addEventListener("resize", () => {
      clearTimeout(state.resizeTimer);
      state.resizeTimer = setTimeout(rerenderActiveCharts, 120);
    });
  }

  window.DanClubCharts = { renderLineChart, renderBarChart };

  async function boot() {
    initPreferences();
    window.DanClubWarRoom?.init();
    initTabs();
    bindControls();
    renderOpenApi();
    try {
      await loadSnapshots();
    } catch (err) {
      $("meta-updated").textContent = `快照加载失败：${err.message}`;
    }
    fetchLiveCryptoTickers();
    state.liveTimer = window.setInterval(fetchLiveCryptoTickers, 5000);
    state.feedPollTimer = window.setInterval(() => {
      if (!document.hidden) pollFeedLive();
    }, 45000);
  }

  boot();
})();
