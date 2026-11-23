/* DanClub Open Terminal — Zero-dependency interactive financial dashboard (MIT License) */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    overview: null,
    arb: null,
    flows: null,
    labs: null,
    feed: null,
    activeTab: "overview",
    activeSpreadPair: "SKHY_VS_SKHYNIX",
    activeSpreadRange: "24h",
    activeCsiCode: "ALL",
    activeEtfAsset: "BTC",
    activeFeedCat: "ALL",
    feedSearch: "",
    liveCrypto: {},
    liveTimer: null,
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
    return new Intl.NumberFormat("zh-CN", {
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
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  };

  /* ---------- Theme & Routing ---------- */
  function initTheme() {
    const saved = localStorage.getItem("danclub_theme");
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const theme = saved || (prefersDark ? "dark" : "light");
    applyTheme(theme);
    $("btn-theme").addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem("danclub_theme", next);
      applyTheme(next);
      rerenderCharts();
    });
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("btn-theme").textContent = theme === "dark" ? "浅色模式" : "深色模式";
  }

  function switchTab(tab) {
    const valid = ["overview", "arb", "flows", "labs", "feed", "open"];
    if (!valid.includes(tab)) tab = "overview";
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
    rerenderCharts();
  }

  function initTabs() {
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
    $("footer-api-link")?.addEventListener("click", (e) => {
      e.preventDefault();
      switchTab("open");
    });
    const initial = (window.location.hash || "#overview").slice(1);
    switchTab(initial);
  }

  /* ---------- Interactive SVG Charts (Dynamic Auto-Scaled Y-Axis!) ---------- */
  function renderLineChart(containerId, seriesList, opts = {}) {
    const box = $(containerId);
    if (!box) return;
    const validSeries = (seriesList || []).filter((s) => Array.isArray(s.points) && s.points.length >= 2);
    if (!validSeries.length) {
      box.innerHTML = `<div class="muted small" style="padding:80px 0;text-align:center;">暂无足够历史样本</div>`;
      return;
    }

    const W = 860;
    const H = opts.height || 240;
    const pad = { top: 18, right: 16, bottom: 28, left: 60 };
    const iw = W - pad.left - pad.right;
    const ih = H - pad.top - pad.bottom;

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

    let minY = Math.min(...allY);
    let maxY = Math.max(...allY);
    const span = maxY - minY;
    const margin = span > 1e-6 ? span * 0.14 : Math.max(Math.abs(maxY) * 0.05, 0.5);
    minY -= margin;
    maxY += margin;

    const maxLen = Math.max(...validSeries.map((s) => s.points.length));
    const xAt = (idx, len) => pad.left + (len <= 1 ? 0 : (idx / (len - 1)) * iw);
    const yAt = (v) => pad.top + ((maxY - v) / (maxY - minY)) * ih;

    const cs = getComputedStyle(document.documentElement);
    const lineCol = cs.getPropertyValue("--line").trim() || "#cbd5e1";
    const mutedCol = cs.getPropertyValue("--muted").trim() || "#64748b";
    const suffix = opts.suffix ?? "";
    const decimals = opts.decimals ?? 2;

    // Horizontal gridlines (5 ticks)
    let gridSvg = "";
    for (let i = 0; i <= 4; i++) {
      const val = maxY - (i / 4) * (maxY - minY);
      const y = pad.top + (i / 4) * ih;
      gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${lineCol}" stroke-dasharray="3 3" stroke-width="1"/>`;
      gridSvg += `<text x="${pad.left - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="${mutedCol}" font-size="11">${fmtNum(val, decimals)}${esc(suffix)}</text>`;
    }

    // Optional reference band (e.g. 72h mean ± 1σ)
    let bandSvg = "";
    if (opts.band && Number.isFinite(opts.band.mean) && Number.isFinite(opts.band.std)) {
      const yTop = yAt(opts.band.mean + opts.band.std);
      const yBot = yAt(opts.band.mean - opts.band.std);
      const yMid = yAt(opts.band.mean);
      bandSvg += `<rect x="${pad.left}" y="${yTop.toFixed(1)}" width="${iw}" height="${Math.max(1, yBot - yTop).toFixed(1)}" fill="rgba(37,99,235,0.08)"/>`;
      bandSvg += `<line x1="${pad.left}" y1="${yMid.toFixed(1)}" x2="${W - pad.right}" y2="${yMid.toFixed(1)}" stroke="#2563eb" stroke-dasharray="5 4" stroke-width="1.2" opacity="0.7"/>`;
    }

    // Reference zero or 1.0 line if inside dynamic range
    let refSvg = "";
    if (opts.refLine !== undefined && opts.refLine >= minY && opts.refLine <= maxY) {
      const ry = yAt(opts.refLine);
      refSvg += `<line x1="${pad.left}" y1="${ry.toFixed(1)}" x2="${W - pad.right}" y2="${ry.toFixed(1)}" stroke="${mutedCol}" stroke-dasharray="4 4" stroke-width="1.2"/>`;
    }

    // Series paths (plus optional range ribbon for 14d min-max)
    let seriesSvg = "";
    validSeries.forEach((s) => {
      const pts = s.points;
      if (pts.every((p) => Number.isFinite(p.low) && Number.isFinite(p.high))) {
        const upper = pts.map((p, i) => `${xAt(i, pts.length).toFixed(1)},${yAt(p.high).toFixed(1)}`).join(" ");
        const lower = [...pts]
          .reverse()
          .map((p, ri) => `${xAt(pts.length - 1 - ri, pts.length).toFixed(1)},${yAt(p.low).toFixed(1)}`)
          .join(" ");
        seriesSvg += `<polygon points="${upper} ${lower}" fill="${s.color}" opacity="0.14"/>`;
      }
      const d = pts
        .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(i, pts.length).toFixed(1)},${yAt(p.y).toFixed(1)}`)
        .join(" ");
      seriesSvg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;
      const last = pts[pts.length - 1];
      seriesSvg += `<circle cx="${xAt(pts.length - 1, pts.length).toFixed(1)}" cy="${yAt(last.y).toFixed(1)}" r="3.5" fill="${s.color}"/>`;
    });

    // X-axis labels (first, mid, last)
    const primary = validSeries[0].points;
    const xTicks = [0, Math.floor((primary.length - 1) / 2), primary.length - 1];
    let xSvg = "";
    xTicks.forEach((idx, k) => {
      const anchor = k === 0 ? "start" : k === 2 ? "end" : "middle";
      xSvg += `<text x="${xAt(idx, primary.length).toFixed(1)}" y="${H - 7}" text-anchor="${anchor}" fill="${mutedCol}" font-size="11">${esc(primary[idx].label)}</text>`;
    });

    // Legend
    const legendHtml =
      validSeries.length > 1 || opts.band
        ? `<div class="small muted" style="display:flex;gap:14px;flex-wrap:wrap;padding:0 6px 4px;">
            ${validSeries
              .map(
                (s) =>
                  `<span><i style="display:inline-block;width:10px;height:3px;background:${s.color};vertical-align:middle;margin-right:5px;border-radius:2px;"></i>${esc(s.name)}</span>`
              )
              .join("")}
            ${opts.band ? `<span><i style="display:inline-block;width:10px;height:8px;background:rgba(37,99,235,0.2);vertical-align:middle;margin-right:5px;border-radius:2px;"></i>72h 常态带 (均值 ${fmtNum(opts.band.mean, 2)}${esc(suffix)} ±1σ)</span>` : ""}
          </div>`
        : "";

    box.innerHTML = `
      ${legendHtml}
      <svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        ${gridSvg}${bandSvg}${refSvg}${seriesSvg}
        <line class="crosshair" x1="0" y1="${pad.top}" x2="0" y2="${H - pad.bottom}" stroke="${mutedCol}" stroke-dasharray="2 2" stroke-width="1" style="display:none;"/>
      </svg>
      <div class="chart-tooltip"></div>
    `;

    // Interactive hover tooltip
    const svg = box.querySelector("svg");
    const tip = box.querySelector(".chart-tooltip");
    const cross = box.querySelector(".crosshair");
    svg.addEventListener("mousemove", (ev) => {
      const rect = svg.getBoundingClientRect();
      const relX = ((ev.clientX - rect.left) / rect.width) * W;
      const clamped = Math.max(pad.left, Math.min(W - pad.right, relX));
      const frac = (clamped - pad.left) / iw;
      const idx = Math.max(0, Math.min(maxLen - 1, Math.round(frac * (maxLen - 1))));
      cross.setAttribute("x1", clamped.toFixed(1));
      cross.setAttribute("x2", clamped.toFixed(1));
      cross.style.display = "block";

      const p0 = primary[Math.min(idx, primary.length - 1)];
      const rows = validSeries
        .map((s) => {
          const p = s.points[Math.min(idx, s.points.length - 1)];
          const extra =
            Number.isFinite(p.low) && Number.isFinite(p.high)
              ? ` <span class="muted">(${fmtNum(p.low, decimals)} ~ ${fmtNum(p.high, decimals)}${esc(suffix)})</span>`
              : "";
          return `<div><span style="color:${s.color};font-weight:700;">●</span> ${esc(s.name)}: <b>${fmtNum(p.y, decimals)}${esc(suffix)}</b>${extra}</div>`;
        })
        .join("");
      tip.innerHTML = `<div class="muted" style="margin-bottom:2px;">${esc(p0.label)}</div>${rows}`;
      tip.style.display = "block";
      const leftPx = Math.min(box.clientWidth - tip.offsetWidth - 10, Math.max(10, ev.clientX - rect.left + 12));
      tip.style.left = `${leftPx}px`;
      tip.style.top = "12px";
    });
    svg.addEventListener("mouseleave", () => {
      tip.style.display = "none";
      cross.style.display = "none";
    });
  }

  function renderBarChart(containerId, items, opts = {}) {
    const box = $(containerId);
    if (!box) return;
    if (!Array.isArray(items) || !items.length) {
      box.innerHTML = `<div class="muted small" style="padding:80px 0;text-align:center;">暂无资金流数据</div>`;
      return;
    }

    const W = 860;
    const H = opts.height || 230;
    const pad = { top: 18, right: 16, bottom: 30, left: 60 };
    const iw = W - pad.left - pad.right;
    const ih = H - pad.top - pad.bottom;

    const validItems = items.filter((x) => x.value !== null && x.value !== undefined && Number.isFinite(Number(x.value)));
    if (!validItems.length) {
      box.innerHTML = `<div class="muted small" style="padding:80px 0;text-align:center;">暂无有效资金流数据</div>`;
      return;
    }

    const vals = validItems.map((x) => Number(x.value));
    const maxAbs = Math.max(10, ...vals.map((v) => Math.abs(v))) * 1.15;
    const yAt = (v) => pad.top + ((maxAbs - v) / (2 * maxAbs)) * ih;
    const zeroY = yAt(0);

    const cs = getComputedStyle(document.documentElement);
    const lineCol = cs.getPropertyValue("--line").trim() || "#cbd5e1";
    const mutedCol = cs.getPropertyValue("--muted").trim() || "#64748b";
    const upCol = cs.getPropertyValue("--up").trim() || "#079455";
    const downCol = cs.getPropertyValue("--down").trim() || "#d92d4b";

    let grid = "";
    [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs].forEach((v) => {
      const y = yAt(v);
      grid += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${v === 0 ? mutedCol : lineCol}" stroke-dasharray="${v === 0 ? "" : "3 3"}" stroke-width="${v === 0 ? 1.2 : 1}"/>`;
      grid += `<text x="${pad.left - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="${mutedCol}" font-size="11">${fmtSignedNum(v, 0)}M</text>`;
    });

    const step = iw / validItems.length;
    const barW = Math.max(10, Math.min(36, step * 0.62));
    let bars = "";
    validItems.forEach((it, i) => {
      const cx = pad.left + step * (i + 0.5);
      const v = Number(it.value);
      const y = v >= 0 ? yAt(v) : zeroY;
      const h = Math.max(2, Math.abs(yAt(v) - zeroY));
      const fill = v >= 0 ? upCol : downCol;
      bars += `<rect class="etf-bar" data-idx="${i}" x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="3" fill="${fill}" opacity="0.88"/>`;
      if (validItems.length <= 15 || i % 2 === 0) {
        bars += `<text x="${cx.toFixed(1)}" y="${H - 8}" text-anchor="middle" fill="${mutedCol}" font-size="10.5">${esc(it.label.slice(5))}</text>`;
      }
    });

    box.innerHTML = `
      <svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${bars}</svg>
      <div class="chart-tooltip"></div>
    `;
    const tip = box.querySelector(".chart-tooltip");
    box.querySelectorAll(".etf-bar").forEach((bar) => {
      bar.addEventListener("mousemove", (ev) => {
        const it = validItems[Number(bar.dataset.idx)];
        const rect = box.getBoundingClientRect();
        tip.innerHTML = `<div><b>${esc(it.label)}</b></div><div class="${dirCls(it.value)}">净流向：<b>${fmtSignedNum(it.value, 1, "M$")}</b></div>${it.sub ? `<div class="muted">${esc(it.sub)}</div>` : ""}`;
        tip.style.display = "block";
        const leftPx = Math.min(box.clientWidth - tip.offsetWidth - 10, Math.max(10, ev.clientX - rect.left + 12));
        tip.style.left = `${leftPx}px`;
        tip.style.top = "12px";
      });
      bar.addEventListener("mouseleave", () => {
        tip.style.display = "none";
      });
    });
  }

  /* ---------- Renderers ---------- */
  function renderLiveRibbon() {
    const ribbon = $("live-ribbon");
    if (!ribbon) return;
    const pills = [];
    const liveSyms = ["BTC", "ETH", "BNB", "SOL", "HYPE"];
    liveSyms.forEach((sym) => {
      const live = state.liveCrypto[sym];
      if (live) {
        pills.push({ sym, px: `$${fmtNum(live.price, sym === "HYPE" ? 3 : 2)}`, chg: live.chg_pct });
      }
    });
    // Append key macro anchors from snapshot
    const macro = state.overview?.macro_quotes || [];
    ["SPY", "QQQ", "NVDA", "MU", "SNDK", "MSTR", ".VIX", "US10Y", "XAU="].forEach((s) => {
      const hit = macro.find((m) => m.symbol === s);
      if (hit) {
        const prefix = s === ".VIX" || s === "US10Y" ? "" : "$";
        const suffix = s === "US10Y" ? "%" : "";
        pills.push({
          sym: s.replace("=", "").replace(".", ""),
          px: `${prefix}${fmtNum(hit.price, s === "US10Y" ? 3 : 2)}${suffix}`,
          chg: hit.chg_pct,
        });
      }
    });
    if (!pills.length) {
      ribbon.innerHTML = `<div class="tick-pill">正在同步公开行情…</div>`;
      return;
    }
    ribbon.innerHTML = pills
      .map(
        (p) =>
          `<div class="tick-pill"><span class="sym">${esc(p.sym)}</span><span class="px">${esc(p.px)}</span>${
            Number.isFinite(Number(p.chg)) ? `<span class="chg ${dirCls(p.chg)}">${fmtSignedPct(p.chg, 2)}</span>` : ""
          }</div>`
      )
      .join("");
  }

  function renderOverview() {
    const ov = state.overview;
    if (!ov) return;
    const bd = ov.bottom_dashboard || {};
    const fng = ov.fear_greed;
    const st = ov.stablecoins;
    const mstr = state.flows?.mstr?.latest;

    // 4 Top KPIs
    const kpis = [
      {
        label: "底部共振条件达成",
        val: `${fmtInt(bd.green)} / ${fmtInt(bd.total)}`,
        cls: bd.green >= 5 ? "up" : bd.green >= 3 ? "warn" : "down",
        sub: `绿灯 ${fmtInt(bd.green)} · 黄灯 ${fmtInt(bd.yellow)} · 红灯 ${fmtInt(bd.red)}`,
      },
      {
        label: "加密恐慌与贪婪指数",
        val: fng ? `${fmtNum(fng.value, 0)}` : "--",
        cls: fng && fng.value <= 25 ? "down" : fng && fng.value >= 75 ? "up" : "",
        sub: fng ? `${fng.label}` : "等待刷新",
      },
      {
        label: "全网稳定币总供给",
        val: st ? `$${fmtNum(st.total_b, 2)}B` : "--",
        cls: dirCls(st?.chg_7d_b),
        sub: st ? `1d ${fmtSignedNum(st.chg_1d_b, 2, "B")} · 7d ${fmtSignedNum(st.chg_7d_b, 2, "B")} · 30d ${fmtSignedNum(st.chg_30d_b, 2, "B")}` : "--",
      },
      {
        label: "MSTR 溢价倍数 (mNAV)",
        val: mstr ? `${fmtNum(mstr.mnav, 3)}x` : "--",
        cls: mstr && mstr.mnav < 1.0 ? "down" : "up",
        sub: mstr ? `持仓 ${fmtNum(mstr.holdings, 0)} BTC` : "SEC 8-K & Strategy KPI",
      },
    ];
    $("overview-kpis").innerHTML = kpis
      .map(
        (k) => `
      <article class="card kpi-card">
        <span class="kpi-label">${esc(k.label)}</span>
        <strong class="kpi-value ${k.cls}">${esc(k.val)}</strong>
        <span class="kpi-sub">${esc(k.sub)}</span>
      </article>`
      )
      .join("");

    // Bottom 7 items
    const scorePill = $("bottom-score-pill");
    scorePill.textContent = `${bd.green ?? 0} / ${bd.total ?? 7} 达标`;
    scorePill.className = `pill ${bd.green >= 5 ? "pill-green" : bd.green >= 3 ? "pill-yellow" : "pill-red"}`;

    const statusBadge = (s) => {
      if (s === "GREEN") return `<span class="pill pill-green">已达标</span>`;
      if (s === "YELLOW") return `<span class="pill pill-yellow">观察中</span>`;
      if (s === "RED") return `<span class="pill pill-red">未满足</span>`;
      return `<span class="pill">未知</span>`;
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

    // Daily brief
    const brief = state.feed?.daily_analysis;
    if (brief) {
      $("brief-time").textContent = brief.dt_bjt || "--";
      $("brief-box").textContent = `${brief.headline}\n${brief.body}`.trim();
    } else {
      $("brief-box").textContent = "等待下一轮 17:00 BJT 客观短线研判生成。";
    }

    // Miner metrics
    const miner = ov.btc_miner;
    if (miner) {
      $("miner-elec").textContent = `按电费 $${fmtNum(miner.elec_usd_kwh, 2)}/kWh 测算`;
      $("miner-box").innerHTML = [
        { k: "S21 Pro 关机价", v: `$${fmtNum(miner.s21pro_usd, 0)}` },
        { k: "S21 关机价", v: `$${fmtNum(miner.s21_usd, 0)}` },
        { k: "S19k 关机价", v: `$${fmtNum(miner.s19k_usd, 0)}` },
        { k: "全网算力", v: `${fmtNum(miner.hashrate_eh, 1)} EH/s` },
        { k: "预计下次难度", v: fmtSignedPct(miner.diff_chg_pct, 2), cls: dirCls(miner.diff_chg_pct) },
        { k: "距调难度剩余", v: `${fmtInt(miner.remaining_blocks)} 块 (${fmtNum(miner.progress_pct, 1)}%)` },
      ]
        .map((m) => `<div class="miner-tile"><span>${esc(m.k)}</span><strong class="${m.cls || ""}">${esc(m.v)}</strong></div>`)
        .join("");
    }

    // Macro grid
    $("macro-grid").innerHTML = (ov.macro_quotes || [])
      .map((m) => {
        const prefix = m.symbol === ".VIX" || m.symbol === "US10Y" || m.symbol === ".DXY" || m.symbol === "JPY=" || m.symbol === "CNH=" ? "" : "$";
        const suffix = m.symbol === "US10Y" ? "%" : "";
        return `
        <div class="macro-tile">
          <div class="macro-left">
            <div class="macro-sym">${esc(m.symbol)}</div>
            <div class="macro-name">${esc(m.name)} · ${esc(m.group)}</div>
          </div>
          <div class="macro-right">
            <div class="macro-px">${prefix}${fmtNum(m.price, m.symbol === "US10Y" ? 3 : 2)}${suffix}</div>
            <div class="macro-chg ${dirCls(m.chg_pct)}">${fmtSignedPct(m.chg_pct, 2)}</div>
          </div>
        </div>`;
      })
      .join("");

    // QDII table
    const qdii = ov.qdii_premium || [];
    $("qdii-tbody").innerHTML = qdii.length
      ? qdii
          .map(
            (q) => `
        <tr class="${q.prem_pct >= 15 ? "row-highlight" : ""}">
          <td><b>${esc(q.code)}</b> <span class="muted small">${esc(q.name)}</span></td>
          <td class="num">${fmtNum(q.px, 3)}</td>
          <td class="num">${fmtNum(q.iopv, 4)}</td>
          <td class="num ${dirCls(q.chg_pct)}">${fmtSignedPct(q.chg_pct, 2)}</td>
          <td class="num ${q.prem_pct >= 15 ? "down" : q.prem_pct >= 5 ? "warn" : "up"}"><b>${fmtSignedPct(q.prem_pct, 2)}</b></td>
        </tr>`
          )
          .join("")
      : `<tr><td colspan="5" class="muted">暂无 QDII 溢价数据</td></tr>`;
  }

  function renderArb() {
    const arb = state.arb;
    if (!arb) return;
    const sp = arb.spreads?.[state.activeSpreadPair];
    if (sp) {
      const lat = sp.latest || {};
      const b72 = sp.band_72h;
      const kpis = [
        { k: sp.leg_a, v: `$${fmtNum(lat.px_a, 2)}`, s: fmtTimeShort(lat.ts) },
        { k: sp.leg_b, v: `$${fmtNum(lat.px_b_adj, 2)}`, s: lat.px_b_raw && lat.px_b_raw !== lat.px_b_adj ? `原价 $${fmtNum(lat.px_b_raw, 2)}` : "实时基准" },
        {
          k: "当前跨市场差价",
          v: fmtSignedPct(lat.spread_pct, 3),
          s: `绝对差 ${fmtSignedNum(lat.spread_usd, 2, " USD")}`,
          cls: dirCls(lat.spread_pct),
        },
        {
          k: "72h 常态带偏离 (Z-Score)",
          v: b72 ? `${fmtSignedNum(b72.zscore, 2)}σ` : "--",
          s: b72 ? `均值 ${fmtNum(b72.mean, 2)}% · 区间 ${fmtNum(b72.min, 2)}~${fmtNum(b72.max, 2)}%` : "样本积累中",
          cls: b72 && Math.abs(b72.zscore) >= 1.5 ? "warn" : "",
        },
      ];
      $("spread-kpis").innerHTML = kpis
        .map(
          (x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong class="${x.cls || ""}">${esc(x.v)}</strong><span>${esc(x.s)}</span></div>`
        )
        .join("");

      if (state.activeSpreadRange === "24h") {
        $("spread-chart-meta").textContent = `${sp.title} · 近 24 小时 15 分钟采样（动态区间 Y 轴）`;
        const pts = (sp.series_24h || []).map((p) => ({ label: fmtTimeShort(p.ts), y: p.spread_pct }));
        renderLineChart("spread-chart-box", [{ name: "差价%", color: "#079455", points: pts }], {
          suffix: "%",
          decimals: 2,
          band: b72,
        });
      } else {
        $("spread-chart-meta").textContent = `${sp.title} · 近 14 天日度收盘差价与日内高低波动带`;
        const pts = (sp.daily_14d || []).map((d) => ({
          label: d.date,
          y: d.last,
          low: d.min,
          high: d.max,
        }));
        renderLineChart("spread-chart-box", [{ name: "日收盘差价%", color: "#2563eb", points: pts }], {
          suffix: "%",
          decimals: 2,
        });
      }
    }

    // CSI Basis table
    const csiAll = arb.csi_basis || [];
    const filtered = state.activeCsiCode === "ALL" ? csiAll : csiAll.filter((c) => c.code === state.activeCsiCode);
    $("csi-tbody").innerHTML = filtered.length
      ? filtered
          .map(
            (c) => `
        <tr class="${String(c.contract || "").endsWith("2706") ? "row-highlight" : ""}">
          <td><b>${esc(c.contract)}</b>${String(c.contract || "").endsWith("2706") ? ` <span class="badge badge-free">远月</span>` : ""}</td>
          <td>${esc(c.expiry)}</td>
          <td class="num">${esc(fmtInt(c.days_left))}天</td>
          <td class="num">${fmtNum(c.spot, 2)}</td>
          <td class="num">${fmtNum(c.fut, 2)}</td>
          <td class="num ${dirCls(c.basis_pts)}">${fmtSignedNum(c.basis_pts, 1)}</td>
          <td class="num ${dirCls(c.basis_pct)}">${fmtSignedPct(c.basis_pct, 2)}</td>
          <td class="num ${dirCls(c.ann_pct)}"><b>${fmtSignedPct(c.ann_pct, 2)}</b></td>
        </tr>`
          )
          .join("")
      : `<tr><td colspan="8" class="muted">暂无活跃股指期货贴水快照</td></tr>`;

    // Wallet summary + major halts (fail-closed: NEVER claim normal when source is offline/stale)
    const ws = arb.wallet_status || {};
    const wsSummary = Array.isArray(ws.summary) ? ws.summary : [];
    $("wallet-summary").innerHTML = wsSummary.length
      ? wsSummary
          .map(
            (w) => `
      <div class="wallet-card">
        <div class="wallet-card-head">
          <span>${esc(w.exchange)}</span>
          <span class="muted small">共 ${esc(fmtInt(w.total_coins))} 币</span>
        </div>
        <div class="wallet-card-sub">
          双停 <b class="${Number(w.both_halted) > 0 ? "warn" : ""}">${esc(fmtInt(w.both_halted))}</b> · 仅停充 ${esc(fmtInt(w.dep_only_halted))} · 仅停提 ${esc(fmtInt(w.wd_only_halted))}
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
          <td><b>${esc(h.coin)}</b></td>
          <td>${esc(h.exchange)}</td>
          <td>${h.dep_ok ? `<span class="up">正常</span>` : `<span class="down"><b>暂停</b></span>`}</td>
          <td>${h.wd_ok ? `<span class="up">正常</span>` : `<span class="down"><b>暂停</b></span>`}</td>
        </tr>`
        )
        .join("");
    } else if (walletHealthy) {
      $("wallet-halts-tbody").innerHTML = `<tr><td colspan="4" class="up">当前监控的主流核心币种在四所均充提正常（最新监测：${esc(fmtTimeShort(ws.updated_at))}）</td></tr>`;
    } else {
      $("wallet-halts-tbody").innerHTML = `<tr><td colspan="4" class="warn"><b>状态未知：</b>${esc(ws.reason || "充提数据源不可用或覆盖不足 4 所")}，请勿据此判断转账安全</td></tr>`;
    }
  }

  function renderFlows() {
    const fl = state.flows;
    if (!fl) return;
    const asset = state.activeEtfAsset;
    const etfRows = fl.etf_flows?.[asset] || [];
    const sum = fl.etf_flows?.summary?.[asset] || {};

    $("etf-kpis").innerHTML = [
      { k: `最新单日 (${sum.latest_date || "--"})`, v: fmtSignedNum(sum.latest_musd, 1, "M$"), cls: dirCls(sum.latest_musd) },
      { k: "近 5 交易日累计", v: fmtSignedNum(sum.sum_5d_musd, 1, "M$"), cls: dirCls(sum.sum_5d_musd) },
      { k: "近 10 交易日累计", v: fmtSignedNum(sum.sum_10d_musd, 1, "M$"), cls: dirCls(sum.sum_10d_musd) },
      { k: `统计窗口 (${sum.days || 0}日)`, v: fmtSignedNum(sum.sum_all_musd, 1, "M$"), cls: dirCls(sum.sum_all_musd) },
    ]
      .map((x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong class="${x.cls || ""}">${esc(x.v)}</strong></div>`)
      .join("");

    renderBarChart(
      "etf-chart-box",
      etfRows.map((r) => ({
        label: r.date,
        value: r.total_musd,
        sub: (r.issuers || []).slice(0, 4).map((i) => `${i.ticker} ${fmtSignedNum(i.musd, 1)}`).join(" | "),
      }))
    );

    $("etf-tbody").innerHTML = [...etfRows]
      .reverse()
      .map(
        (r) => `
      <tr>
        <td class="mono">${esc(r.date)}</td>
        <td class="num ${dirCls(r.total_musd)}"><b>${fmtSignedNum(r.total_musd, 1, "M$")}</b></td>
        <td class="small muted">${(r.issuers || [])
          .map((i) => `<span class="${dirCls(i.musd)}" style="margin-right:8px;white-space:nowrap;">${esc(i.ticker)} ${fmtSignedNum(i.musd, 1)}</span>`)
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
    mpill.className = `pill ${ml.mnav < 1.0 ? "pill-red" : "pill-green"}`;
    $("mstr-kpis").innerHTML = [
      { k: "官方持仓 BTC", v: fmtNum(ml.holdings, 0), s: `BTC 现价 $${fmtNum(ml.btc_price, 0)}` },
      { k: "SEC 8-K 持仓均价", v: ms.avg_cost ? `$${fmtNum(ms.avg_cost, 0)}` : "--", s: ms.filing_date ? `最新披露 ${ms.filing_date} (+${fmtNum(ms.bought, 0)} BTC)` : "--" },
    ]
      .map((x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong>${esc(x.v)}</strong><span>${esc(x.s)}</span></div>`)
      .join("");
    renderLineChart(
      "mstr-chart-box",
      [{ name: "mNAV 倍数", color: "#2563eb", points: (m.series_7d || []).map((p) => ({ label: fmtTimeShort(p.ts), y: p.mnav })) }],
      { height: 180, suffix: "x", decimals: 3, refLine: 1.0 }
    );

    // Hyperliquid
    const hl = fl.hyperliquid || {};
    $("hl-kpis").innerHTML = [
      { k: "全网总持仓 OI / 24h 量", v: fmtCompact(hl.platform?.total_oi_usd), s: `24h 成交额 ${fmtCompact(hl.platform?.total_vol_usd)}` },
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
        <td><b>${esc(c.coin)}</b></td>
        <td class="num">$${fmtNum(c.mark, c.mark < 10 ? 4 : 2)}</td>
        <td class="num">${fmtCompact(c.oi_usd)}</td>
        <td class="num">${fmtCompact(c.day_vol)}</td>
        <td class="num ${dirCls(c.funding_ann_pct)}">${fmtSignedPct(c.funding_ann_pct, 1)}</td>
      </tr>`
      )
      .join("");

    // Binance OI Radar
    const oi = fl.bn_oi_radar || [];
    $("oi-tbody").innerHTML = oi.length
      ? oi
          .map((r) => {
            const badgeCls = r.tone === "warn" ? "pill-yellow" : r.tone === "squeeze" ? "pill-info" : "";
            return `
          <tr>
            <td><b>${esc(r.base)}</b> <span class="muted small">${esc(r.symbol)}</span></td>
            <td class="num">$${fmtNum(r.price, r.price < 1 ? 5 : 2)}</td>
            <td class="num ${dirCls(r.chg_1h_oi_pct)}"><b>${fmtSignedPct(r.chg_1h_oi_pct, 2)}</b></td>
            <td class="num ${dirCls(r.chg_1h_px_pct)}">${fmtSignedPct(r.chg_1h_px_pct, 2)}</td>
            <td class="num">${fmtCompact(r.oi_usd)}</td>
            <td class="num">${fmtCompact(r.vol_24h)}</td>
            <td class="num ${dirCls(r.funding_8h_pct)}">${fmtSignedPct(r.funding_8h_pct, 4)}</td>
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
    const keys = ["FIXED", "VA", "MA200_FNG"];
    const firstDays = strats.FIXED?.days || 0;
    $("dca-days-pill").textContent = `已公开运行 ${firstDays} 天 · 截至 ${dca.latest_date || "--"}`;

    $("dca-strategies").innerHTML = keys
      .filter((k) => strats[k])
      .map((k) => {
        const s = strats[k];
        return `
        <div class="stat-tile">
          <small>${esc(s.label)}</small>
          <strong class="${dirCls(s.ret_pct)}">${fmtSignedPct(s.ret_pct, 2)}</strong>
          <span>投入 $${fmtNum(s.in, 0)} → 市值 $${fmtNum(s.mv, 0)} (${fmtSignedNum(s.pnl, 1, "U")})</span>
        </div>`;
      })
      .join("");

    const hist = dca.history || [];
    renderLineChart(
      "dca-chart-box",
      [
        { name: "固定定投 (FIXED)", color: "#2563eb", points: hist.map((h) => ({ label: h.date, y: h.FIXED?.ret_pct })) },
        { name: "价值平均 (VA)", color: "#079455", points: hist.map((h) => ({ label: h.date, y: h.VA?.ret_pct })) },
        { name: "200日均线+恐慌 (MA200_FNG)", color: "#d97706", points: hist.map((h) => ({ label: h.date, y: h.MA200_FNG?.ret_pct })) },
      ],
      { suffix: "%", decimals: 2, refLine: 0 }
    );

    const assets = Object.keys(strats.FIXED?.per_asset_ret_pct || {});
    $("dca-asset-tbody").innerHTML = assets
      .map(
        (a) => `
      <tr>
        <td><b>${esc(a)}</b></td>
        <td class="num ${dirCls(strats.FIXED?.per_asset_ret_pct?.[a])}">${fmtSignedPct(strats.FIXED?.per_asset_ret_pct?.[a], 2)}</td>
        <td class="num ${dirCls(strats.VA?.per_asset_ret_pct?.[a])}">${fmtSignedPct(strats.VA?.per_asset_ret_pct?.[a], 2)}</td>
        <td class="num ${dirCls(strats.MA200_FNG?.per_asset_ret_pct?.[a])}">${fmtSignedPct(strats.MA200_FNG?.per_asset_ret_pct?.[a], 2)}</td>
      </tr>`
      )
      .join("");

    // Promos
    const promos = labs.exchange_promos || [];
    $("promo-grid").innerHTML = promos.length
      ? promos
          .map((p) => {
            const href = safeHref(p.url);
            return `
          <article class="promo-card">
            <div class="promo-top">
              <span class="pill pill-info">${esc(p.exchange)}</span>
              <span class="small muted">${esc(p.worth_level)}</span>
            </div>
            <div class="promo-title">${
              href
                ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`
                : esc(p.title)
            }</div>
            <div class="promo-desc">${esc(p.summary)}</div>
          </article>`;
          })
          .join("")
      : `<div class="muted small">当前暂无符合低风险标准的公开活动</div>`;
  }

  function renderFeed() {
    const items = state.feed?.items || [];
    const q = state.feedSearch.trim().toLowerCase();
    const filtered = items.filter((it) => {
      if (state.activeFeedCat !== "ALL" && it.category !== state.activeFeedCat) return false;
      if (q && !`${it.headline} ${it.body}`.toLowerCase().includes(q)) return false;
      return true;
    });

    $("feed-list").innerHTML = filtered.length
      ? filtered
          .map(
            (it) => `
        <article class="feed-item">
          <div class="feed-meta">
            <span class="pill ${it.level === "major" ? "pill-red" : "pill-info"}">${esc(it.category)}</span>
            <span class="mono">${esc(it.dt_bjt)}</span>
          </div>
          <div class="feed-headline">${esc(it.headline)}</div>
          ${it.body ? `<div class="feed-body">${esc(it.body)}</div>` : ""}
        </article>`
          )
          .join("")
      : `<div class="muted small">没有匹配当前筛选条件的公开快讯</div>`;
  }

  function renderOpenApi() {
    const base = new URL("./api/v1/", window.location.href).href;
    const endpoints = [
      { file: "index.json", desc: "API 元信息、更新时间与接口清单" },
      { file: "overview.json", desc: "7项底部仪表盘、恐慌指数、稳定币、矿机关机价、宏观与 QDII" },
      { file: "arb.json", desc: "海力士/黄金跨市场价差曲线、中证贴水 2706、四所充提安全状态" },
      { file: "flows.json", desc: "15日 BTC/ETH 现货 ETF 矩阵、MSTR mNAV、Hyperliquid、币安 OI 雷达" },
      { file: "labs.json", desc: "3套模拟定投对比净值与分标的收益、低风险官方活动清单" },
      { file: "feed.json", desc: "脱敏高信噪比公开快讯、交易所公告与每日客观短线研判" },
    ];
    $("api-list").innerHTML = endpoints
      .map(
        (ep) => `
      <div class="api-row">
        <div>
          <div class="api-path">GET /api/v1/${esc(ep.file)}</div>
          <div class="small muted">${esc(ep.desc)}</div>
        </div>
        <div class="api-actions">
          <button class="btn btn-sm api-try" data-file="${esc(ep.file)}" type="button">在线试调</button>
          <button class="btn btn-sm api-copy" data-url="${esc(base + ep.file)}" type="button">复制 curl</button>
        </div>
      </div>`
      )
      .join("");

    document.querySelectorAll(".api-try").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const preview = $("api-preview");
        preview.textContent = `正在请求 ./api/v1/${btn.dataset.file} …`;
        try {
          const res = await fetch(`./api/v1/${btn.dataset.file}`);
          const data = await res.json();
          preview.textContent = JSON.stringify(data, null, 2).slice(0, 6000);
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
          setTimeout(() => (btn.textContent = "复制 curl"), 1500);
        } catch {
          $("api-preview").textContent = cmd;
        }
      });
    });
  }

  function rerenderCharts() {
    if (state.activeTab === "arb") renderArb();
    if (state.activeTab === "flows") renderFlows();
    if (state.activeTab === "labs") renderLabs();
  }

  /* ---------- Live Browser-Side Public Tickers (with graceful fallback) ---------- */
  async function fetchLiveCryptoTickers() {
    try {
      const [bnRes, hlRes] = await Promise.allSettled([
        fetch("https://api.binance.com/api/v3/ticker/24hr?symbols=%5B%22BTCUSDT%22,%22ETHUSDT%22,%22BNBUSDT%22,%22SOLUSDT%22%5D", { signal: AbortSignal.timeout(4500) }),
        fetch("https://api.hyperliquid.xyz/info", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: "metaAndAssetCtxs" }),
          signal: AbortSignal.timeout(4500),
        }),
      ]);

      let gotLive = false;
      if (bnRes.status === "fulfilled" && bnRes.value.ok) {
        const arr = await bnRes.value.json();
        if (Array.isArray(arr)) {
          arr.forEach((it) => {
            const sym = String(it.symbol || "").replace("USDT", "");
            if (sym) {
              state.liveCrypto[sym] = {
                price: Number(it.lastPrice),
                chg_pct: Number(it.priceChangePercent),
              };
              gotLive = true;
            }
          });
        }
      }

      if (hlRes.status === "fulfilled" && hlRes.value.ok) {
        const data = await hlRes.value.json();
        const universe = data?.[0]?.universe || [];
        const ctxs = data?.[1] || [];
        const idx = universe.findIndex((u) => u.name === "HYPE");
        if (idx >= 0 && ctxs[idx]) {
          const mark = Number(ctxs[idx].markPx);
          const prev = Number(ctxs[idx].prevDayPx);
          state.liveCrypto.HYPE = {
            price: mark,
            chg_pct: prev > 0 ? ((mark / prev) - 1) * 100 : null,
          };
          gotLive = true;
        }
      }

      // Fallback from exported Hyperliquid snapshot if browser cannot reach exchange APIs
      if (!gotLive && state.flows?.hyperliquid?.top_markets) {
        state.flows.hyperliquid.top_markets.forEach((c) => {
          if (["BTC", "ETH", "SOL", "HYPE"].includes(c.coin) && !state.liveCrypto[c.coin]) {
            state.liveCrypto[c.coin] = { price: c.mark, chg_pct: null };
          }
        });
      }

      $("live-badge").textContent = gotLive ? "实时直连 · 5s" : "CDN 快照模式";
      $("live-dot").classList.toggle("stale", !gotLive);
      renderLiveRibbon();
    } catch {
      $("live-badge").textContent = "CDN 快照模式";
      $("live-dot").classList.add("stale");
      renderLiveRibbon();
    }
  }

  async function loadSnapshots() {
    const bust = `v=${Math.floor(Date.now() / 60000)}`;
    const [overview, arb, flows, labs, feed] = await Promise.all([
      fetch(`./api/v1/overview.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/arb.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/flows.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/labs.json?${bust}`).then((r) => r.json()),
      fetch(`./api/v1/feed.json?${bust}`).then((r) => r.json()),
    ]);
    state.overview = overview;
    state.arb = arb;
    state.flows = flows;
    state.labs = labs;
    state.feed = feed;

    const gen = overview?.meta?.generated_at_bjt || "--";
    $("meta-updated").textContent = `最新快照：${gen}`;

    renderOverview();
    renderArb();
    renderFlows();
    renderLabs();
    renderFeed();
    renderLiveRibbon();
  }

  function bindControls() {
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
    $("btn-refresh")?.addEventListener("click", () => {
      loadSnapshots().catch(console.error);
      fetchLiveCryptoTickers();
    });
  }

  async function boot() {
    initTheme();
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
  }

  boot();
})();
