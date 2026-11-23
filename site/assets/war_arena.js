/* DanClub Terminal — Tactical Air-Battle War Room & Entity-Deduplicated Whale Radar (MIT License) */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const COINS = ["BTC", "ETH", "SOL", "HYPE"];

  /* 5 International Climax War Anthems (Public Domain / CC-BY Wikimedia Commons, 24-26s peak cuts) */
  const WAR_TRACKS = [
    { id: "valkyries",    file: "./assets/audio/valkyries.mp3",    label: "🇩🇪 德 · 女武神的骑行 (25s)", short: "🇩🇪 女武神的骑行", dur: 25 },
    { id: "1812_cannon",  file: "./assets/audio/1812_cannon.mp3",  label: "🇷🇺 俄 · 1812礼炮终章 (26s)", short: "🇷🇺 1812礼炮终章", dur: 26 },
    { id: "william_tell", file: "./assets/audio/william_tell.mp3", label: "🇮🇹 意 · 威廉退尔冲锋 (25s)", short: "🇮🇹 威廉退尔冲锋", dur: 25 },
    { id: "toreador",     file: "./assets/audio/toreador.mp3",     label: "🇫🇷 法 · 卡门斗牛士 (26s)",   short: "🇫🇷 卡门斗牛士",   dur: 26 },
    { id: "mars_war",     file: "./assets/audio/mars_war.mp3",     label: "🇬🇧 英 · 火星战争使者 (24s)", short: "🇬🇧 火星战争使者", dur: 24 },
  ];

  function readSavedVolume() {
    try {
      const raw = localStorage.getItem("danclub_war_vol");
      if (raw === null || raw.trim() === "") return 0.75;
      const n = Number(raw);
      return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0.75;
    } catch {
      return 0.75;
    }
  }

  const war = {
    data: null,
    activeCoin: "BTC",
    activeCohortCoin: "BTC",
    filterTier: "ALL",
    filterCoin: "ALL",
    filterSide: "ALL",
    search: "",
    soundOn: false,
    volume: readSavedVolume(),
    trackIdx: Math.floor(Math.random() * WAR_TRACKS.length),
    bgm: null,
    playSeq: 0,
    audioErrCount: 0,
    needsAudioReload: false,
    pausedByHidden: false,
    lastSfxTs: 0,
    audioCtx: null,
    wsHL: null,
    wsBN: null,
    wsReconnectTimer: null,
    rafId: null,
    running: false,
    canvas: null,
    ctx: null,
    cssW: 900,
    cssH: 360,
    dpr: 1,
    frontline: 0.5,
    targetFrontline: 0.5,
    shakeAmt: 0,
    bannerText: "",
    bannerSide: "LONG",
    bannerAlpha: 0,
    planes: [],
    sparks: [],
    rings: [],
    carriers: [],
    hoveredCarrier: null,
    mouseX: -1,
    mouseY: -1,
    flowBuckets: new Map(), // Per-second exact 60s rolling buckets: sec -> { buyUsd, sellUsd, count }
    lastFrameTs: 0,
    lastHudTs: 0,
    ambientTimer: 0,
  };

  /* ---------- Utilities ---------- */
  const esc = (v) =>
    String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  const fmtCompact = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    const abs = Math.abs(n);
    const sign = n < 0 ? "-" : "";
    if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
    return `${sign}$${abs.toFixed(0)}`;
  };

  const fmtSignedCompact = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    return `${n > 0 ? "+" : ""}${fmtCompact(n)}`;
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

  /* ---------- 5-Nation War Anthem Radio + Procedural Combat SFX ---------- */
  function ensureAudioElement() {
    if (war.bgm) return war.bgm;
    const el = new Audio();
    el.preload = "none";
    el.volume = war.volume;
    el.addEventListener("ended", () => {
      if (!war.soundOn || !war.running || document.hidden) return;
      war.audioErrCount = 0;
      war.trackIdx = (war.trackIdx + 1) % WAR_TRACKS.length;
      playCurrentAnthem(true);
    });
    el.addEventListener("error", () => {
      if (!war.soundOn || !war.running || document.hidden || war.pausedByHidden) return;
      if (war.audioErrCount < 1) {
        war.audioErrCount += 1;
        war.trackIdx = (war.trackIdx + 1) % WAR_TRACKS.length;
        playCurrentAnthem(true);
      } else {
        war.soundOn = false;
        syncAudioUi("战歌音频加载失败，请检查网络后重试");
      }
    });
    war.bgm = el;
    return el;
  }

  function syncAudioUi(customStatus = null) {
    const btn = $("btn-war-sound");
    const sel = $("war-track-select");
    const prompt = $("war-audio-prompt");
    const status = $("war-audio-status");
    const volSlider = $("war-vol");
    const track = WAR_TRACKS[war.trackIdx] || WAR_TRACKS[0];

    if (sel && Number(sel.value) !== war.trackIdx) {
      sel.value = String(war.trackIdx);
    }
    if (volSlider && Math.round(war.volume * 100) !== Number(volSlider.value)) {
      volSlider.value = String(Math.round(war.volume * 100));
    }
    if (btn) {
      btn.textContent = war.soundOn ? "🔊 战歌激燃中" : "⚔️ 开启战歌";
      btn.classList.toggle("active", war.soundOn);
    }
    if (prompt) {
      prompt.classList.toggle("hidden", war.soundOn);
    }
    if (status) {
      if (customStatus) {
        status.textContent = customStatus;
      } else if (war.soundOn) {
        status.textContent = `正在轮播 [${war.trackIdx + 1}/${WAR_TRACKS.length}] ${track.short} · 高潮 ${track.dur}s`;
      } else {
        status.textContent = "5 首多国交响战歌高潮待命 · 自动轮播";
      }
    }
  }

  function playCurrentAnthem(forceReload = false) {
    const el = ensureAudioElement();
    const track = WAR_TRACKS[war.trackIdx] || WAR_TRACKS[0];
    const seq = ++war.playSeq;
    el.volume = war.volume;

    if (forceReload || war.needsAudioReload || el.error || el.dataset.trackId !== track.id) {
      war.needsAudioReload = false;
      el.dataset.trackId = track.id;
      el.src = track.file;
      el.load();
      el.currentTime = 0;
    }

    war.soundOn = true;
    war.pausedByHidden = false;
    syncAudioUi();

    try {
      if (!war.audioCtx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (AC) war.audioCtx = new AC();
      }
      if (war.audioCtx && war.audioCtx.state === "suspended") {
        war.audioCtx.resume().catch(() => {});
      }
    } catch {}

    const p = el.play();
    if (p && typeof p.catch === "function") {
      p.catch((err) => {
        if (war.playSeq !== seq) return;
        war.soundOn = false;
        const blocked = err && err.name === "NotAllowedError";
        if (!blocked) war.needsAudioReload = true;
        syncAudioUi(blocked ? "浏览器限制自动播放，请点击「开启战歌」解锁" : "战歌加载受阻，请点击「开启战歌」重试");
      });
    }
  }

  function stopAnthem(keepPreference = false) {
    war.playSeq += 1;
    if (war.bgm) {
      try {
        war.bgm.pause();
      } catch {}
    }
    if (war.audioCtx && war.audioCtx.state !== "closed") {
      try {
        war.audioCtx.suspend().catch(() => {});
      } catch {}
    }
    if (!keepPreference) {
      war.soundOn = false;
      war.pausedByHidden = false;
      syncAudioUi();
    } else if (war.soundOn) {
      war.pausedByHidden = true;
    }
  }

  function nextTrack(autoStart = true) {
    war.audioErrCount = 0;
    war.trackIdx = (war.trackIdx + 1) % WAR_TRACKS.length;
    if (autoStart || war.soundOn) {
      playCurrentAnthem(true);
    } else {
      syncAudioUi();
    }
  }

  function playTacticalTone(side, tier) {
    if (!war.soundOn || !war.running || document.hidden || war.pausedByHidden || war.volume <= 0.01) return;
    const nowMs = performance.now();
    if (tier < 4 && nowMs - war.lastSfxTs < 170) return;
    war.lastSfxTs = nowMs;
    try {
      if (!war.audioCtx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        war.audioCtx = new AC();
      }
      if (war.audioCtx.state !== "running") {
        war.audioCtx.resume().catch(() => {});
        return;
      }
      const ctx = war.audioCtx;
      const now = ctx.currentTime;
      const scale = war.volume * 0.55;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      if (tier >= 4) {
        // Heavy B-2 Strategic Bomber sub-bass horn + drop
        osc.type = "sawtooth";
        const f0 = side === "LONG" ? 220 : 140;
        osc.frequency.setValueAtTime(f0, now);
        osc.frequency.exponentialRampToValueAtTime(48, now + 0.38);
        gain.gain.setValueAtTime(0.14 * scale, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.42);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.43);
      } else if (tier === 3) {
        // Heavy twin-engine strike chirp
        osc.type = "triangle";
        const f0 = side === "LONG" ? 640 : 390;
        osc.frequency.setValueAtTime(f0, now);
        osc.frequency.exponentialRampToValueAtTime(f0 * 0.58, now + 0.16);
        gain.gain.setValueAtTime(0.08 * scale, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.17);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.18);
      } else if (tier === 2) {
        // Light fighter laser sweep
        osc.type = "sine";
        const f0 = side === "LONG" ? 860 : 520;
        osc.frequency.setValueAtTime(f0, now);
        osc.frequency.exponentialRampToValueAtTime(f0 * 0.72, now + 0.08);
        gain.gain.setValueAtTime(0.045 * scale, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.09);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.095);
      } else {
        // Subtle kinetic tracer ping for active dogfight cadence
        osc.type = "sine";
        const f0 = side === "LONG" ? 1080 : 720;
        osc.frequency.setValueAtTime(f0, now);
        gain.gain.setValueAtTime(0.018 * scale, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.045);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.05);
      }
    } catch {
      // Ignore audio permission failures gracefully
    }
  }

  /* ---------- Persistent Whale Flagships Positioned in Left/Right Airspace ---------- */
  function rebuildCarriers() {
    war.carriers = [];
    const list = war.data?.flagships || [];
    if (!list.length) return;

    const coin = war.activeCoin;
    const longs = [];
    const shorts = [];
    list.forEach((ent) => {
      const pos = (ent.positions || []).find((p) => p.coin === coin);
      if (!pos || pos.usd < 1_500_000) return;
      const item = { ent, pos };
      if (pos.side === "LONG") longs.push(item);
      else shorts.push(item);
    });

    longs.sort((a, b) => b.pos.usd - a.pos.usd);
    shorts.sort((a, b) => b.pos.usd - a.pos.usd);

    const narrow = war.cssW > 0 && war.cssW < 540;
    const maxPerSide = narrow ? 3 : 6;
    const placeSide = (arr, side) => {
      const top = arr.slice(0, maxPerSide);
      top.forEach((it, idx) => {
        const rows = Math.max(1, top.length);
        const yFrac = (idx + 0.85) / (rows + 0.7);
        const xBase = narrow
          ? (side === "LONG" ? 0.16 : 0.84)
          : (side === "LONG" ? 0.075 + (idx % 2) * 0.095 : 0.925 - (idx % 2) * 0.095);
        const sizeBase = narrow ? 13 : 15;
        const sizeMax = narrow ? 22 : 30;
        const size = Math.max(sizeBase, Math.min(sizeMax, (narrow ? 9 : 11) + Math.log10(Math.max(1e6, it.pos.usd) / 1e6) * (narrow ? 7 : 9.5)));
        war.carriers.push({
          ent: it.ent,
          pos: it.pos,
          side,
          xFrac: xBase,
          yFrac: 0.14 + yFrac * 0.72,
          size,
          phase: idx * 1.15 + (side === "LONG" ? 0 : 2.3),
          x: 0,
          y: 0,
        });
      });
    };
    placeSide(longs, "LONG");
    placeSide(shorts, "SHORT");
  }

  /* ---------- Trade Ingestion & Tiered Aircraft Spawner ---------- */
  function ingestTrade(side, usd, price, source = "HL") {
    if (!Number.isFinite(usd) || usd <= 0) return;
    const sec = Math.floor(Date.now() / 1000);
    let b = war.flowBuckets.get(sec);
    if (!b) {
      b = { buyUsd: 0, sellUsd: 0, count: 0 };
      war.flowBuckets.set(sec, b);
    }
    if (side === "LONG") b.buyUsd += usd;
    else b.sellUsd += usd;
    b.count += 1;

    // Tiered capital-to-aircraft mapping
    let tier = 1;
    if (usd >= 1_000_000) tier = 4;      // Strategic B-2 Stealth Bomber
    else if (usd >= 250_000) tier = 3;   // Heavy Twin-Engine Interceptor
    else if (usd >= 50_000) tier = 2;    // Light Swept-Wing Fighter

    // Throttle Tier 1 micro-drones when screen is busy so 60fps stays silky smooth
    if (tier === 1 && war.planes.length > 68 && Math.random() > 0.35) return;
    if (war.planes.length > 110 && tier <= 2) return;

    const W = war.cssW;
    const H = war.cssH;
    const y = 38 + Math.random() * Math.max(80, H - 86);
    const fromLeft = side === "LONG";
    const speedBase = tier === 1 ? 300 : tier === 2 ? 235 : tier === 3 ? 175 : 132;
    const vx = (fromLeft ? 1 : -1) * (speedBase + Math.random() * 55);

    war.planes.push({
      side,
      tier,
      usd,
      source,
      x: fromLeft ? -24 : W + 24,
      y,
      vx,
      vy: (Math.random() - 0.5) * 14,
      trail: [],
      alive: true,
    });

    if (tier >= 2 || usd >= 10_000) playTacticalTone(side, tier);

    if (tier === 4) {
      war.shakeAmt = 9;
      war.bannerText = `${fromLeft ? "多军" : "空军"}战略轰炸机突防 · 单笔 ${fmtCompact(usd)}`;
      war.bannerSide = side;
      war.bannerAlpha = 1.0;
      war.rings.push({
        x: fromLeft ? W * 0.16 : W * 0.84,
        y,
        r: 8,
        maxR: 92,
        color: fromLeft ? "#10b981" : "#f43f5e",
        alpha: 0.9,
      });
    } else if (tier === 3) {
      war.shakeAmt = Math.max(war.shakeAmt, 3.5);
    }
  }

  /* ---------- Public WebSocket Trade Feeds (Hyperliquid Global + Binance Optional) ---------- */
  function disconnectSockets() {
    clearTimeout(war.wsReconnectTimer);
    if (war.wsHL) {
      try { war.wsHL.close(); } catch {}
      war.wsHL = null;
    }
    if (war.wsBN) {
      try { war.wsBN.close(); } catch {}
      war.wsBN = null;
    }
  }

  function connectSockets() {
    disconnectSockets();
    const coin = war.activeCoin;
    war.flowBuckets.clear();
    const statusEl = $("war-ws-status");
    if (statusEl) statusEl.textContent = `连接 ${coin} 实况成交流…`;

    // 1. Hyperliquid Public Trades WebSocket (works globally without 451 geo-block!)
    try {
      const ws = new WebSocket("wss://api.hyperliquid.xyz/ws");
      war.wsHL = ws;
      ws.addEventListener("open", () => {
        if (war.wsHL !== ws) return;
        ws.send(JSON.stringify({ method: "subscribe", subscription: { type: "trades", coin } }));
        if (statusEl) statusEl.textContent = `实况直连 · ${coin} (Hyperliquid)`;
      });
      ws.addEventListener("message", (ev) => {
        if (war.wsHL !== ws) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.channel === "trades" && Array.isArray(msg.data)) {
            msg.data.forEach((tr) => {
              const px = Number(tr.px);
              const sz = Number(tr.sz);
              const side = tr.side === "B" ? "LONG" : "SHORT";
              if (Number.isFinite(px) && Number.isFinite(sz)) {
                ingestTrade(side, px * sz, px, "HL");
              }
            });
          }
        } catch {}
      });
      ws.addEventListener("close", () => {
        if (war.wsHL === ws && war.running) {
          war.wsReconnectTimer = setTimeout(connectSockets, 4000);
        }
      });
      ws.addEventListener("error", () => {});
    } catch {}

    // 2. Optional Binance aggTrade WS for extra depth on BTC/ETH/SOL (silently ignored if 451)
    if (["BTC", "ETH", "SOL"].includes(coin)) {
      try {
        const wsB = new WebSocket(`wss://stream.binance.com:9443/ws/${coin.toLowerCase()}usdt@aggTrade`);
        war.wsBN = wsB;
        wsB.addEventListener("open", () => {
          if (war.wsBN === wsB && statusEl) {
            statusEl.textContent = `双源实况 · ${coin} (Hyperliquid + Binance)`;
          }
        });
        wsB.addEventListener("message", (ev) => {
          if (war.wsBN !== wsB) return;
          try {
            const d = JSON.parse(ev.data);
            const px = Number(d.p);
            const qty = Number(d.q);
            // m === true means buyer is maker -> taker sold -> SHORT strike
            const side = d.m ? "SHORT" : "LONG";
            if (Number.isFinite(px) && Number.isFinite(qty)) {
              ingestTrade(side, px * qty, px, "BN");
            }
          } catch {}
        });
        wsB.addEventListener("error", () => {});
      } catch {}
    }
  }

  /* ---------- Vector Aircraft & Carrier Drawing Primitives ---------- */
  function drawAircraft(ctx, x, y, dir, tier, color) {
    ctx.save();
    ctx.translate(x, y);
    if (dir < 0) ctx.scale(-1, 1);

    if (tier === 1) {
      // Micro-drone / kinetic tracer dart
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(-10, 0);
      ctx.lineTo(5, 0);
      ctx.stroke();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(5, 0, 1.5, 0, Math.PI * 2);
      ctx.fill();
    } else if (tier === 2) {
      // Swept-wing Light Fighter ($50k - $250k)
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.moveTo(12, 0);
      ctx.lineTo(-4, -7.5);
      ctx.lineTo(-9, -6.5);
      ctx.lineTo(-5, -1.5);
      ctx.lineTo(-11, -3.5);
      ctx.lineTo(-11, 3.5);
      ctx.lineTo(-5, 1.5);
      ctx.lineTo(-9, 6.5);
      ctx.lineTo(-4, 7.5);
      ctx.closePath();
      ctx.fill();
    } else if (tier === 3) {
      // Heavy Twin-Engine Strike Interceptor ($250k - $1M)
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 14;
      ctx.beginPath();
      ctx.moveTo(18, 0);
      ctx.lineTo(4, -4);
      ctx.lineTo(-5, -13);
      ctx.lineTo(-11, -11);
      ctx.lineTo(-7, -3.5);
      ctx.lineTo(-15, -5.5);
      ctx.lineTo(-14, 0);
      ctx.lineTo(-15, 5.5);
      ctx.lineTo(-7, 3.5);
      ctx.lineTo(-11, 11);
      ctx.lineTo(-5, 13);
      ctx.lineTo(4, 4);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(8, 0, 1.8, 0, Math.PI * 2);
      ctx.fill();
    } else {
      // Tier 4: B-2 Strategic Stealth Flying-Wing Bomber (>= $1M)
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 22;
      ctx.beginPath();
      ctx.moveTo(24, 0);
      ctx.lineTo(-4, -22);
      ctx.lineTo(-14, -18);
      ctx.lineTo(-8, -9);
      ctx.lineTo(-16, -5);
      ctx.lineTo(-11, 0);
      ctx.lineTo(-16, 5);
      ctx.lineTo(-8, 9);
      ctx.lineTo(-14, 18);
      ctx.lineTo(-4, 22);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.75)";
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawWhaleCarrier(ctx, c, t) {
    const bob = Math.sin(t * 0.0018 + c.phase) * 3.2;
    const x = c.xFrac * war.cssW;
    const y = c.yFrac * war.cssH + bob;
    c.x = x;
    c.y = y;

    const isLong = c.side === "LONG";
    const baseCol = isLong ? "#10b981" : "#f43f5e";
    const tierCode = c.ent.tier_code;
    const haloCol =
      tierCode === "S" || tierCode === "A"
        ? "#f59e0b"
        : tierCode === "C"
          ? "#a855f7"
          : "#38bdf8";
    const s = c.size;
    const hovered = war.hoveredCarrier === c;

    ctx.save();
    ctx.translate(x, y);

    // Subtle faction aura ring
    ctx.beginPath();
    ctx.arc(0, 0, s * 1.12, 0, Math.PI * 2);
    ctx.strokeStyle = hovered ? "#ffffff" : haloCol;
    ctx.globalAlpha = hovered ? 0.9 : 0.36;
    ctx.lineWidth = hovered ? 1.8 : 1.1;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    // Carrier stealth hull silhouette facing the frontline
    ctx.save();
    if (!isLong) ctx.scale(-1, 1);
    ctx.fillStyle = isLong ? "rgba(16, 185, 129, 0.22)" : "rgba(244, 63, 94, 0.22)";
    ctx.strokeStyle = baseCol;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = baseCol;
    ctx.shadowBlur = hovered ? 18 : 10;
    ctx.beginPath();
    ctx.moveTo(s * 1.15, 0);
    ctx.lineTo(s * 0.35, -s * 0.48);
    ctx.lineTo(-s * 0.85, -s * 0.62);
    ctx.lineTo(-s * 1.05, -s * 0.22);
    ctx.lineTo(-s * 0.72, 0);
    ctx.lineTo(-s * 1.05, s * 0.22);
    ctx.lineTo(-s * 0.85, s * 0.62);
    ctx.lineTo(s * 0.35, s * 0.48);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Bridge core light
    ctx.fillStyle = haloCol;
    ctx.beginPath();
    ctx.arc(s * 0.15, 0, Math.max(2.5, s * 0.14), 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Targeting brackets on hover
    if (hovered) {
      const b = s * 1.35;
      ctx.strokeStyle = "#38bdf8";
      ctx.lineWidth = 1.5;
      [
        [-1, -1],
        [1, -1],
        [-1, 1],
        [1, 1],
      ].forEach(([dx, dy]) => {
        ctx.beginPath();
        ctx.moveTo(dx * b, dy * (b - 6));
        ctx.lineTo(dx * b, dy * b);
        ctx.lineTo(dx * (b - 6), dy * b);
        ctx.stroke();
      });
    }

    // Compact tactical nameplate under flagship (clamped safely inside canvas width on mobile)
    const narrow = war.cssW < 540;
    const tag = `[${tierCode}] ${c.ent.display.slice(0, narrow ? 10 : 12)}`;
    const amtTxt = `${isLong ? "+" : "-"}${fmtCompact(c.pos.usd)}`;
    ctx.font = `600 ${narrow ? 9 : 10}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    ctx.textAlign = "center";
    const halfW = Math.max(ctx.measureText(tag).width, ctx.measureText(amtTxt).width) / 2 + 6;
    const labelOffsetX = Math.max(halfW - x, Math.min(war.cssW - halfW - x, 0));
    ctx.fillStyle = haloCol;
    ctx.fillText(tag, labelOffsetX, s + (narrow ? 11 : 13));
    ctx.fillStyle = "rgba(226, 232, 240, 0.85)";
    ctx.fillText(amtTxt, labelOffsetX, s + (narrow ? 21 : 24));

    ctx.restore();
  }

  /* ---------- Main 60fps Tactical Canvas Loop ---------- */
  function resizeCanvas() {
    const canvas = war.canvas;
    if (!canvas) return;
    const wrap = canvas.parentElement;
    if (!wrap) return;
    const isFs = Boolean(document.fullscreenElement);
    const w = Math.max(280, Math.floor(wrap.clientWidth));
    const h = isFs ? Math.max(460, window.innerHeight - 190) : w < 520 ? 280 : 350;
    const wasNarrow = war.cssW > 0 && war.cssW < 540;
    war.dpr = Math.min(2, window.devicePixelRatio || 1);
    war.cssW = w;
    war.cssH = h;
    canvas.width = Math.floor(w * war.dpr);
    canvas.height = Math.floor(h * war.dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    if (war.data && wasNarrow !== (w < 540)) rebuildCarriers();
  }

  function updateHudMetrics(now) {
    // Time-evict per-second buckets older than 60s (100% exact dollar volume, ZERO count-cap distortion!)
    const nowSec = Math.floor(now / 1000);
    const minSec = nowSec - 59;
    let buyUsd = 0;
    let sellUsd = 0;
    let totalCount = 0;
    for (const [sec, b] of war.flowBuckets.entries()) {
      if (sec < minSec) {
        war.flowBuckets.delete(sec);
      } else {
        buyUsd += b.buyUsd;
        sellUsd += b.sellUsd;
        totalCount += b.count;
      }
    }
    const totalFlow = buyUsd + sellUsd;
    const flowLongPct = totalFlow > 0 ? (buyUsd / totalFlow) * 100 : 50;

    // Blend real-time 60s taker flow (60%) + active whale directional ratio (40%)
    const coh = war.data?.cohorts?.[war.activeCoin] || {};
    const allWhaleLongPct = coh.ALL_DIRECTIONAL?.notional_long_pct ?? 50;
    const smartLongPct = coh.SMART_SA?.notional_long_pct;
    const counterLongPct = coh.COUNTER_C?.notional_long_pct;

    const powerLong = totalFlow > 50_000 ? flowLongPct * 0.6 + allWhaleLongPct * 0.4 : allWhaleLongPct;
    const clampedLong = Math.max(12, Math.min(88, powerLong));
    war.targetFrontline = clampedLong / 100;

    // Update DOM power bar & micro telemetry
    const barL = $("war-power-long");
    const barR = $("war-power-short");
    const lblL = $("war-pct-long");
    const lblR = $("war-pct-short");
    const verdict = $("war-verdict");
    if (barL) barL.style.width = `${clampedLong.toFixed(1)}%`;
    if (barR) barR.style.width = `${(100 - clampedLong).toFixed(1)}%`;
    if (lblL) lblL.textContent = `${clampedLong.toFixed(1)}%`;
    if (lblR) lblR.textContent = `${(100 - clampedLong).toFixed(1)}%`;
    if (verdict) {
      if (clampedLong >= 58) {
        verdict.textContent = "多军压制";
        verdict.className = "pill pill-ok";
      } else if (clampedLong <= 42) {
        verdict.textContent = "空军主导";
        verdict.className = "pill pill-risk";
      } else {
        verdict.textContent = "两军拉锯";
        verdict.className = "pill pill-warn";
      }
    }

    const sub = $("war-power-sub");
    if (sub) {
      const tps = (totalCount / 60).toFixed(1);
      const netFlow = buyUsd - sellUsd;
      const parts = [
        `60s 主动买 ${fmtCompact(buyUsd)} / 卖 ${fmtCompact(sellUsd)} (净 ${fmtSignedCompact(netFlow)})`,
        `流速 ${tps} 笔/s`,
      ];
      if (smartLongPct != null) parts.push(`S级聪明钱 ${smartLongPct.toFixed(1)}%多`);
      if (counterLongPct != null) parts.push(`C级反指 ${counterLongPct.toFixed(1)}%多`);
      sub.textContent = parts.join(" · ");
    }
  }

  function tick(ts) {
    if (!war.running) return;
    war.rafId = requestAnimationFrame(tick);
    if (document.hidden || !war.ctx) return;

    const dt = Math.min(0.05, (ts - (war.lastFrameTs || ts)) / 1000);
    war.lastFrameTs = ts;

    const nowMs = Date.now();
    if (nowMs - war.lastHudTs > 450) {
      updateHudMetrics(nowMs);
      war.lastHudTs = nowMs;
    }

    // Smooth frontline easing
    war.frontline += (war.targetFrontline - war.frontline) * Math.min(1, dt * 3.5);
    const W = war.cssW;
    const H = war.cssH;
    const fx = W * war.frontline;

    // Gentle ambient carrier defense fire if trade tape is temporarily quiet (>1.2s)
    war.ambientTimer += dt;
    if (war.ambientTimer > 0.65 && war.planes.length < 10 && war.carriers.length > 0) {
      war.ambientTimer = 0;
      const c = war.carriers[Math.floor(Math.random() * war.carriers.length)];
      war.planes.push({
        side: c.side,
        tier: 1,
        usd: 18000,
        source: "PATROL",
        x: c.x || (c.side === "LONG" ? W * 0.1 : W * 0.9),
        y: c.y || H * 0.5,
        vx: (c.side === "LONG" ? 1 : -1) * (240 + Math.random() * 60),
        vy: (Math.random() - 0.5) * 16,
        trail: [],
        alive: true,
      });
    }

    const ctx = war.ctx;
    ctx.save();
    ctx.scale(war.dpr, war.dpr);

    // Subtle tactical shake on >=$250K / >=$1M strikes
    if (war.shakeAmt > 0.2) {
      const sx = (Math.random() - 0.5) * war.shakeAmt;
      const sy = (Math.random() - 0.5) * war.shakeAmt;
      ctx.translate(sx, sy);
      war.shakeAmt *= 0.88;
    } else {
      war.shakeAmt = 0;
    }

    // 1. Tactical Dark Radar Backdrop
    const bgGrad = ctx.createLinearGradient(0, 0, W, 0);
    bgGrad.addColorStop(0, "rgba(16, 185, 129, 0.08)");
    bgGrad.addColorStop(Math.max(0.1, war.frontline - 0.14), "#070a10");
    bgGrad.addColorStop(Math.min(0.9, war.frontline + 0.14), "#070a10");
    bgGrad.addColorStop(1, "rgba(244, 63, 94, 0.09)");
    ctx.fillStyle = "#070a10";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, W, H);

    // Radar Gridlines
    ctx.strokeStyle = "rgba(148, 163, 184, 0.065)";
    ctx.lineWidth = 1;
    const step = 44;
    ctx.beginPath();
    for (let gx = step; gx < W; gx += step) {
      ctx.moveTo(gx, 0);
      ctx.lineTo(gx, H);
    }
    for (let gy = step; gy < H; gy += step) {
      ctx.moveTo(0, gy);
      ctx.lineTo(W, gy);
    }
    ctx.stroke();

    // Concentric radar range rings centered on dynamic frontline
    ctx.strokeStyle = "rgba(56, 189, 248, 0.09)";
    [54, 110, 170].forEach((r) => {
      ctx.beginPath();
      ctx.arc(fx, H * 0.5, r, 0, Math.PI * 2);
      ctx.stroke();
    });

    // Dynamic Battle Frontline Laser
    const fGrad = ctx.createLinearGradient(fx, 0, fx, H);
    fGrad.addColorStop(0, "rgba(56, 189, 248, 0.05)");
    fGrad.addColorStop(0.5, "rgba(56, 189, 248, 0.65)");
    fGrad.addColorStop(1, "rgba(56, 189, 248, 0.05)");
    ctx.strokeStyle = fGrad;
    ctx.lineWidth = 1.6;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(fx, 0);
    ctx.lineTo(fx, H);
    ctx.stroke();
    ctx.setLineDash([]);

    // Zone Watermarks
    ctx.font = "700 10.5px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillStyle = "rgba(16, 185, 129, 0.55)";
    ctx.textAlign = "left";
    ctx.fillText("◀ LONG AIRSPACE · 绿军多头", 14, 20);
    ctx.fillStyle = "rgba(244, 63, 94, 0.6)";
    ctx.textAlign = "right";
    ctx.fillText("红军空头 · SHORT AIRSPACE ▶", W - 14, 20);

    // 2. Back Layer — Hovering Real Whale Flagships (clickable!)
    war.hoveredCarrier = null;
    war.carriers.forEach((c) => {
      drawWhaleCarrier(ctx, c, ts);
      if (war.mouseX >= 0 && Math.hypot(war.mouseX - c.x, war.mouseY - c.y) <= c.size * 1.45) {
        war.hoveredCarrier = c;
      }
    });
    if (war.canvas) {
      war.canvas.style.cursor = war.hoveredCarrier ? "pointer" : "crosshair";
    }

    // 3. Front Layer — Real-Time Tiered Aircraft & Frontline Impacts
    const nextPlanes = [];
    for (const p of war.planes) {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.y < 28) { p.y = 28; p.vy = Math.abs(p.vy); }
      if (p.y > H - 28) { p.y = H - 28; p.vy = -Math.abs(p.vy); }

      p.trail.push({ x: p.x, y: p.y });
      const maxTrail = p.tier === 1 ? 4 : p.tier === 2 ? 9 : 14;
      if (p.trail.length > maxTrail) p.trail.shift();

      const col = p.side === "LONG" ? "#10b981" : "#f43f5e";
      const crossed = p.side === "LONG" ? p.x >= fx : p.x <= fx;

      // Draw contrail
      if (p.trail.length >= 2) {
        ctx.strokeStyle = col;
        ctx.globalAlpha = p.tier === 1 ? 0.35 : 0.45;
        ctx.lineWidth = p.tier >= 3 ? 2.2 : 1.1;
        ctx.beginPath();
        p.trail.forEach((pt, i) => (i === 0 ? ctx.moveTo(pt.x, pt.y) : ctx.lineTo(pt.x, pt.y)));
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      drawAircraft(ctx, p.x, p.y, p.side === "LONG" ? 1 : -1, p.tier, col);

      // Callout label on heavy $250K+ jets
      if (p.tier >= 3) {
        ctx.font = "700 10px ui-monospace, SFMono-Regular, Menlo, monospace";
        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.fillText(fmtCompact(p.usd), p.x, p.y - (p.tier === 4 ? 26 : 16));
      }

      if (crossed) {
        // Impact sparks & shockwave at battle frontline!
        const sparkCnt = p.tier === 1 ? 4 : p.tier === 2 ? 9 : p.tier === 3 ? 16 : 26;
        for (let k = 0; k < sparkCnt; k++) {
          const ang = Math.random() * Math.PI * 2;
          const spd = 40 + Math.random() * (p.tier * 55);
          war.sparks.push({
            x: fx,
            y: p.y,
            vx: Math.cos(ang) * spd,
            vy: Math.sin(ang) * spd,
            life: 1.0,
            color: k % 3 === 0 ? "#ffffff" : col,
            size: p.tier >= 3 ? 2.3 : 1.4,
          });
        }
        if (p.tier >= 2) {
          war.rings.push({
            x: fx,
            y: p.y,
            r: 4,
            maxR: p.tier === 2 ? 24 : p.tier === 3 ? 46 : 78,
            color: col,
            alpha: 0.85,
          });
        }
        p.alive = false;
      }
      if (p.alive && p.x > -40 && p.x < W + 40) nextPlanes.push(p);
    }
    war.planes = nextPlanes;

    // 4. Shockwave Rings
    const nextRings = [];
    for (const r of war.rings) {
      r.r += (r.maxR - r.r) * Math.min(1, dt * 8);
      r.alpha -= dt * 1.9;
      if (r.alpha > 0.03) {
        ctx.beginPath();
        ctx.arc(r.x, r.y, r.r, 0, Math.PI * 2);
        ctx.strokeStyle = r.color;
        ctx.globalAlpha = r.alpha;
        ctx.lineWidth = 1.6;
        ctx.stroke();
        ctx.globalAlpha = 1;
        nextRings.push(r);
      }
    }
    war.rings = nextRings;

    // 5. Impact Sparks
    const nextSparks = [];
    for (const sp of war.sparks) {
      sp.x += sp.vx * dt;
      sp.y += sp.vy * dt;
      sp.vx *= 0.94;
      sp.vy *= 0.94;
      sp.life -= dt * 2.2;
      if (sp.life > 0.04) {
        ctx.fillStyle = sp.color;
        ctx.globalAlpha = sp.life;
        ctx.fillRect(sp.x, sp.y, sp.size, sp.size);
        ctx.globalAlpha = 1;
        nextSparks.push(sp);
      }
    }
    war.sparks = nextSparks;

    // 6. Tier 4 Strategic Bomber Tactical Sky-Alert Banner
    if (war.bannerAlpha > 0.02 && war.bannerText) {
      const bCol = war.bannerSide === "LONG" ? "#10b981" : "#f43f5e";
      ctx.save();
      ctx.globalAlpha = Math.min(1, war.bannerAlpha);
      ctx.font = "700 12.5px ui-monospace, SFMono-Regular, Menlo, monospace";
      const tw = ctx.measureText(war.bannerText).width + 28;
      ctx.fillStyle = "rgba(9, 13, 21, 0.88)";
      ctx.strokeStyle = bCol;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.roundRect((W - tw) / 2, 32, tw, 28, 6);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#ffffff";
      ctx.textAlign = "center";
      ctx.fillText(war.bannerText, W / 2, 50);
      ctx.restore();
      war.bannerAlpha -= dt * 0.28;
    }

    // 7. Carrier Hover Tooltip inside Canvas
    const tip = $("war-canvas-tip");
    if (tip) {
      if (war.hoveredCarrier) {
        const { ent, pos } = war.hoveredCarrier;
        const pnl = ent.alltime_pnl;
        tip.innerHTML = `
          <div><b>${esc(ent.display)}</b> <span class="muted">(${esc(ent.tier_label)})</span></div>
          <div class="${pos.side === "LONG" ? "ok" : "risk"}">${esc(pos.coin)} ${pos.side === "LONG" ? "做多" : "做空"} <b>${fmtCompact(pos.usd)}</b></div>
          <div class="muted">全账户总仓位 ${fmtCompact(ent.total_notional_usd)} · ${ent.addr_count} 址归并</div>
          ${pnl != null ? `<div class="${pnl >= 0 ? "ok" : "risk"}">历史实盘 PnL: <b>${fmtSignedCompact(pnl)}</b></div>` : ""}
          <div style="color:#38bdf8;margin-top:2px;">点击展开单舰完整持仓档案 →</div>
        `;
        tip.style.display = "block";
        const left = Math.min(W - tip.offsetWidth - 10, Math.max(10, war.hoveredCarrier.x + 18));
        const top = Math.min(H - tip.offsetHeight - 10, Math.max(10, war.hoveredCarrier.y - 24));
        tip.style.left = `${left}px`;
        tip.style.top = `${top}px`;
      } else {
        tip.style.display = "none";
      }
    }

    ctx.restore();
  }

  /* ---------- 1-Click Shareable 1200x675 Tactical Battle Poster PNG ---------- */
  function exportBattlePoster() {
    const c = document.createElement("canvas");
    c.width = 1200;
    c.height = 675;
    const ctx = c.getContext("2d");
    const coin = war.activeCoin;
    const coh = war.data?.cohorts?.[coin] || war.data?.cohorts?.ALL || {};
    const sa = coh.SMART_SA || {};
    const cc = coh.COUNTER_C || {};
    const div = coh.divergence || {};

    // Backdrop
    ctx.fillStyle = "#080b10";
    ctx.fillRect(0, 0, 1200, 675);
    const gL = ctx.createRadialGradient(200, 320, 10, 200, 320, 420);
    gL.addColorStop(0, "rgba(16,185,129,0.16)");
    gL.addColorStop(1, "transparent");
    ctx.fillStyle = gL;
    ctx.fillRect(0, 0, 1200, 675);
    const gR = ctx.createRadialGradient(1000, 320, 10, 1000, 320, 420);
    gR.addColorStop(0, "rgba(244,63,94,0.16)");
    gR.addColorStop(1, "transparent");
    ctx.fillStyle = gR;
    ctx.fillRect(0, 0, 1200, 675);

    // Header
    ctx.fillStyle = "#38bdf8";
    ctx.font = "700 18px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText("DANCLUB TERMINAL · TACTICAL WHALE WAR ROOM", 54, 62);
    ctx.fillStyle = "#eef2f6";
    ctx.font = "700 38px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
    ctx.fillText(`${coin} 多空阵营战报 · 聪明钱 vs 反指巨鲸`, 54, 118);
    ctx.fillStyle = "#7c899e";
    ctx.font = "500 17px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText(`快照时间 ${fmtTimeShort(war.data?.updated_at)} BJT · 实体去重真持仓`, 54, 152);

    // Divergence Pill
    const sp = div.spread_pct;
    ctx.fillStyle = "rgba(244,63,94,0.15)";
    ctx.strokeStyle = sp != null && sp >= 0 ? "#10b981" : "#f43f5e";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(54, 178, 1092, 68, 10);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#eef2f6";
    ctx.font = "700 24px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
    ctx.fillText(
      `Smart − Counter 背离度：${sp != null ? `${sp > 0 ? "+" : ""}${sp.toFixed(1)}%` : "--"}   |   ${div.label || "实盘监控中"}`,
      80,
      221
    );

    // Two Faction Cards
    const drawFactionCard = (x, title, accent, obj) => {
      ctx.fillStyle = "#101520";
      ctx.strokeStyle = "rgba(148,163,184,0.22)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.roundRect(x, 276, 528, 286, 12);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = accent;
      ctx.font = "700 22px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
      ctx.fillText(title, x + 28, 322);

      const lp = Number(obj.notional_long_pct ?? 50);
      ctx.fillStyle = "#eef2f6";
      ctx.font = "700 48px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillText(`${lp.toFixed(1)}% 多`, x + 28, 392);
      ctx.fillStyle = "#7c899e";
      ctx.font = "600 20px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillText(`实体 ${obj.long_entities ?? 0} 多 vs ${obj.short_entities ?? 0} 空`, x + 28, 430);

      // Bar
      ctx.fillStyle = "#f43f5e";
      ctx.beginPath();
      ctx.roundRect(x + 28, 452, 472, 16, 4);
      ctx.fill();
      ctx.fillStyle = "#10b981";
      ctx.beginPath();
      ctx.roundRect(x + 28, 452, Math.max(6, (472 * lp) / 100), 16, 4);
      ctx.fill();

      ctx.fillStyle = "#c5cedb";
      ctx.font = "600 18px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillText(`多头 ${fmtCompact(obj.long_usd)}   |   空头 ${fmtCompact(obj.short_usd)}`, x + 28, 504);
      ctx.fillStyle = (obj.net_usd || 0) >= 0 ? "#10b981" : "#f43f5e";
      ctx.fillText(`净敞口 ${fmtSignedCompact(obj.net_usd)}`, x + 28, 538);
    };

    drawFactionCard(54, "S/A 级聪明钱阵营", "#f59e0b", sa);
    drawFactionCard(618, "C 级反指巨鲸阵营", "#a855f7", cc);

    // Footer Watermark
    ctx.fillStyle = "#7c899e";
    ctx.font = "600 16px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText("danclub.duckdns.org  ·  100% 开源免费多市场情报终端  ·  仅供客观数据研究，不构成投资建议", 54, 628);

    const a = document.createElement("a");
    a.download = `danclub-war-${coin.toLowerCase()}-${new Date().toISOString().slice(0, 10)}.png`;
    a.href = c.toDataURL("image/png");
    a.click();
  }

  /* ---------- Faction Confrontation, Divergence Chart, Fleet Table & Entity Drawer ---------- */
  function renderWhalePanels() {
    const w = war.data;
    if (!w) return;

    const st = w.stats || {};
    if ($("war-stats-bar")) {
      $("war-stats-bar").innerHTML = [
        `<span class="regime-chip">追踪巨鲸库 <b>${Number(st.tracked_addresses || 0).toLocaleString()}</b> 址</span>`,
        `<span class="regime-chip">实盘建档 <b>${Number(st.profiled_whales || 0).toLocaleString()}</b> 鲸</span>`,
        `<span class="regime-chip">在役方向实体 <b>${Number(st.active_entities || 0)}</b> 个</span>`,
        `<span class="regime-chip">在场总名义仓位 <b>${fmtCompact(st.total_active_notional_usd)}</b></span>`,
      ].join("");
    }

    // Faction Confrontation Panel (follows activeCohortCoin: ALL / BTC / ETH / HYPE / SOL)
    const cKey = war.activeCohortCoin;
    const coh = w.cohorts?.[cKey] || {};
    const sa = coh.SMART_SA || {};
    const cc = coh.COUNTER_C || {};
    const div = coh.divergence || {};

    const divBox = $("war-div-banner");
    if (divBox) {
      const sp = div.spread_pct;
      const cls = div.tone === "danger" ? "pill-risk" : div.tone === "bullish" ? "pill-ok" : div.tone === "warn" ? "pill-warn" : "pill-muted";
      divBox.innerHTML = `
        <span class="pill ${cls}">Smart − Counter 背离度: ${sp != null ? `${sp > 0 ? "+" : ""}${sp.toFixed(1)}%` : "--"}</span>
        <span class="small" style="color:var(--text-2);font-weight:600;">${esc(div.label || "数据更新中")}</span>
      `;
    }

    const renderFactionBox = (title, badgeCls, badgeTxt, obj) => {
      const lp = Number(obj.notional_long_pct ?? 50);
      const sp = Math.max(0, 100 - lp);
      return `
        <div class="faction-box">
          <div class="faction-head">
            <strong>${esc(title)}</strong>
            <span class="pill ${badgeCls}">${esc(badgeTxt)}</span>
          </div>
          <div class="faction-pct-row mono">
            <span class="ok"><b>${lp.toFixed(1)}%</b> 多</span>
            <span class="muted small">${obj.long_entities ?? 0} 多 vs ${obj.short_entities ?? 0} 空</span>
            <span class="risk"><b>${sp.toFixed(1)}%</b> 空</span>
          </div>
          <div class="faction-bar">
            <i class="f-long" style="width:${lp.toFixed(1)}%;"></i>
            <i class="f-short" style="width:${sp.toFixed(1)}%;"></i>
          </div>
          <div class="faction-foot mono">
            <span>多 ${fmtCompact(obj.long_usd)}</span>
            <span>空 ${fmtCompact(obj.short_usd)}</span>
            <b class="${(obj.net_usd || 0) >= 0 ? "ok" : "risk"}">净 ${fmtSignedCompact(obj.net_usd)}</b>
          </div>
        </div>
      `;
    };

    if ($("war-factions-grid")) {
      $("war-factions-grid").innerHTML =
        renderFactionBox("S/A 级聪明钱阵营", "pill-warn", "高胜率实盘", sa) +
        renderFactionBox("C 级反指巨鲸阵营", "pill-violet", "历史反向锚", cc);
    }

    // Divergence Curve (uses shared 1:1 responsive SVG engine from app.js)
    const series = w.series?.[cKey] || [];
    if (window.DanClubCharts?.renderLineChart && $("war-div-chart")) {
      window.DanClubCharts.renderLineChart(
        "war-div-chart",
        [
          {
            name: "S/A 聪明钱多头%",
            color: "#f59e0b",
            points: series.map((p) => ({
              label: fmtTimeShort(p.ts),
              shortLabel: fmtTimeShort(p.ts).slice(6),
              y: p.smart_long_pct,
              note: p.btc_price ? `BTC $${Number(p.btc_price).toLocaleString()}` : "",
            })),
          },
          {
            name: "C 级反指多头%",
            color: "#a855f7",
            points: series.map((p) => ({
              label: fmtTimeShort(p.ts),
              shortLabel: fmtTimeShort(p.ts).slice(6),
              y: p.counter_long_pct,
            })),
          },
        ],
        { height: 205, suffix: "%", decimals: 1, refLine: 50, area: false }
      );
    }

    renderFleetTable();
  }

  function renderFleetTable() {
    const tbody = $("war-fleet-tbody");
    if (!tbody || !war.data) return;
    const all = war.data.flagships || [];
    const q = war.search.trim().toLowerCase();

    const filtered = all.filter((f) => {
      if (war.filterTier !== "ALL") {
        if (war.filterTier === "SA" && !["S", "A"].includes(f.tier_code)) return false;
        if (war.filterTier === "C" && f.tier_code !== "C") return false;
        if (war.filterTier === "F" && f.tier_code !== "F") return false;
      }
      if (war.filterCoin !== "ALL") {
        const hasCoin = (f.positions || []).some((p) =>
          war.filterCoin === "TRADFI" ? p.coin.includes(":") : p.coin === war.filterCoin
        );
        if (!hasCoin) return false;
      }
      if (war.filterSide !== "ALL" && f.primary_side !== war.filterSide) return false;
      if (q && !`${f.display} ${f.short_addr} ${f.primary_coin} ${f.tier_label}`.toLowerCase().includes(q)) return false;
      return true;
    });

    const shipBadge = (sc) =>
      sc === "CARRIER"
        ? `<span class="ship-tag ship-carrier" title="航空母舰 ≥$50M">航母</span>`
        : sc === "CRUISER"
          ? `<span class="ship-tag ship-cruiser" title="巡洋舰 $10M-$50M">巡洋</span>`
          : `<span class="ship-tag ship-destroyer" title="驱逐舰 $1M-$10M">驱逐</span>`;

    const tierPill = (f) => {
      const cls =
        f.tier_code === "S" || f.tier_code === "A"
          ? "pill-warn"
          : f.tier_code === "C"
            ? "pill-violet"
            : "pill-muted";
      return `<span class="pill ${cls}">${esc(f.tier_label)}</span>`;
    };

    tbody.innerHTML = filtered.length
      ? filtered
          .map((f) => {
            const sideCls = f.primary_side === "LONG" ? "ok" : "risk";
            const actCls = f.action === "反手" ? "pill-risk" : f.action === "加仓" ? "pill-info" : f.action === "减仓" ? "pill-warn" : "pill-muted";
            const pnl = f.alltime_pnl;
            return `
          <tr class="fleet-row" data-eid="${esc(f.id)}" style="cursor:pointer;">
            <td>
              <div class="fleet-entity">
                ${shipBadge(f.ship_class)}
                <div>
                  <div class="mono"><b>${esc(f.display)}</b>${f.addr_count > 1 ? ` <span class="sess-tag">${f.addr_count}址归并</span>` : ""}</div>
                  ${f.display !== f.short_addr ? `<div class="muted small mono">${esc(f.short_addr)}</div>` : ""}
                </div>
              </div>
            </td>
            <td>${tierPill(f)}</td>
            <td class="mono">
              <span class="${sideCls}"><b>${esc(f.primary_coin)} ${f.primary_side === "LONG" ? "多" : "空"}</b></span>
              <span class="muted"> ${fmtCompact(f.primary_usd)}</span>
            </td>
            <td class="num hide-xs"><b>${fmtCompact(f.total_notional_usd)}</b></td>
            <td class="hide-sm">
              <span class="pill ${actCls}">${esc(f.action)}</span>
              ${f.delta_usd != null && Math.abs(f.delta_usd) >= 500000 ? `<span class="muted small mono"> ${fmtSignedCompact(f.delta_usd)}${f.delta_window_h ? ` (${f.delta_window_h >= 20 ? "24h" : `${f.delta_window_h}h`})` : ""}</span>` : ""}
            </td>
            <td class="num hide-xs ${pnl != null ? (pnl >= 0 ? "ok" : "risk") : "muted"}"><b>${pnl != null ? fmtSignedCompact(pnl) : "--"}</b></td>
          </tr>`;
          })
          .join("")
      : `<tr><td colspan="6" class="muted">暂无匹配筛选条件的在役巨鲸实体</td></tr>`;

    tbody.querySelectorAll(".fleet-row").forEach((tr) => {
      tr.addEventListener("click", () => {
        const hit = all.find((x) => x.id === tr.dataset.eid);
        if (hit) openEntityDrawer(hit);
      });
    });
  }

  function openEntityDrawer(ent) {
    const drawer = $("whale-drawer");
    const backdrop = $("whale-drawer-backdrop");
    if (!drawer || !backdrop) return;

    const pnl = ent.alltime_pnl;
    $("wd-title").textContent = `${ent.display} · ${ent.ship_label}`;
    $("wd-sub").textContent = `${ent.short_addr} · ${ent.addr_count} 个关联地址去重合并 · ${ent.tier_label}`;

    $("wd-kpis").innerHTML = [
      { k: "总名义仓位", v: fmtCompact(ent.total_notional_usd), cls: "" },
      { k: "全仓净敞口", v: fmtSignedCompact(ent.net_usd), cls: ent.net_usd >= 0 ? "ok" : "risk" },
      { k: "历史实盘 PnL", v: pnl != null ? fmtSignedCompact(pnl) : "--", cls: pnl != null ? (pnl >= 0 ? "ok" : "risk") : "" },
      { k: "Hyperliquid 权益", v: ent.account_value ? fmtCompact(ent.account_value) : "--", cls: "" },
    ]
      .map((x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong class="${x.cls}">${esc(x.v)}</strong></div>`)
      .join("");

    const maxPos = Math.max(1, ...(ent.positions || []).map((p) => p.usd));
    $("wd-positions").innerHTML = (ent.positions || [])
      .map((p) => {
        const w = Math.max(4, (p.usd / maxPos) * 100);
        const col = p.side === "LONG" ? "var(--ok)" : "var(--risk)";
        return `
        <div class="wd-pos-row">
          <div class="wd-pos-top mono">
            <span><b>${esc(p.coin)}</b> <span class="${p.side === "LONG" ? "ok" : "risk"}">${p.side === "LONG" ? "LONG" : "SHORT"}</span></span>
            <span><b>${fmtCompact(p.usd)}</b></span>
          </div>
          <div class="wd-pos-bar"><i style="width:${w.toFixed(0)}%;background:${col};"></i></div>
        </div>`;
      })
      .join("");

    drawer.classList.add("open");
    backdrop.classList.add("open");

    requestAnimationFrame(() => {
      const hist = ent.history || [];
      if (window.DanClubCharts?.renderLineChart && $("wd-chart")) {
        window.DanClubCharts.renderLineChart(
          "wd-chart",
          [
            {
              name: "全仓净敞口 (M$)",
              color: ent.net_usd >= 0 ? "#10b981" : "#f43f5e",
              points: hist.map((h) => ({
                label: fmtTimeShort(h.ts),
                shortLabel: fmtTimeShort(h.ts).slice(6),
                y: Number((h.net_usd / 1e6).toFixed(2)),
              })),
            },
          ],
          { height: 170, suffix: "M", decimals: 1, refLine: 0 }
        );
      }
    });
  }

  function closeEntityDrawer() {
    $("whale-drawer")?.classList.remove("open");
    $("whale-drawer-backdrop")?.classList.remove("open");
  }

  /* ---------- Lifecycle & Event Wiring ---------- */
  function startArena() {
    if (!war.canvas) {
      war.canvas = $("war-canvas");
      war.ctx = war.canvas?.getContext("2d") || null;
    }
    if (!war.canvas) return;
    resizeCanvas();
    rebuildCarriers();
    renderWhalePanels();
    if (!war.running) {
      war.running = true;
      war.lastFrameTs = performance.now();
      war.rafId = requestAnimationFrame(tick);
      connectSockets();
      if (war.soundOn && war.pausedByHidden && !document.hidden) {
        playCurrentAnthem(false);
      }
    }
  }

  function stopArena() {
    war.running = false;
    if (war.rafId) cancelAnimationFrame(war.rafId);
    war.rafId = null;
    disconnectSockets();
    stopAnthem(true);
  }

  function bindWarEvents() {
    $("war-coin-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn || !COINS.includes(btn.dataset.coin)) return;
      war.activeCoin = btn.dataset.coin;
      war.activeCohortCoin = btn.dataset.coin;
      $("war-coin-tabs").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      $("war-cohort-tabs")?.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.coin === war.activeCohortCoin));
      war.planes = [];
      war.sparks = [];
      war.rings = [];
      rebuildCarriers();
      renderWhalePanels();
      if (war.running) connectSockets();
    });

    $("war-cohort-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      war.activeCohortCoin = btn.dataset.coin;
      $("war-cohort-tabs").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderWhalePanels();
    });

    $("war-tier-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      war.filterTier = btn.dataset.tier;
      $("war-tier-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderFleetTable();
    });

    $("war-fleet-coin-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      war.filterCoin = btn.dataset.coin;
      $("war-fleet-coin-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderFleetTable();
    });

    $("war-side-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn) return;
      war.filterSide = btn.dataset.side;
      $("war-side-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderFleetTable();
    });

    $("war-fleet-search")?.addEventListener("input", (e) => {
      war.search = e.target.value;
      renderFleetTable();
    });

    syncAudioUi();

    $("btn-war-sound")?.addEventListener("click", () => {
      if (war.soundOn) {
        stopAnthem(false);
      } else {
        war.audioErrCount = 0;
        playCurrentAnthem(false);
      }
    });

    $("war-audio-prompt")?.addEventListener("click", () => {
      war.audioErrCount = 0;
      playCurrentAnthem(false);
    });

    $("war-track-select")?.addEventListener("change", (e) => {
      const idx = Number(e.target.value);
      if (!Number.isInteger(idx) || idx < 0 || idx >= WAR_TRACKS.length) return;
      war.audioErrCount = 0;
      war.trackIdx = idx;
      playCurrentAnthem(true);
    });

    $("btn-war-next-track")?.addEventListener("click", () => nextTrack(true));

    $("war-vol")?.addEventListener("input", (e) => {
      const pct = Number(e.target.value);
      if (!Number.isFinite(pct)) return;
      war.volume = Math.max(0, Math.min(1, pct / 100));
      if (war.bgm) war.bgm.volume = war.volume;
      try {
        localStorage.setItem("danclub_war_vol", String(war.volume));
      } catch {}
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (war.soundOn) stopAnthem(true);
      } else if (war.running && war.soundOn && war.pausedByHidden) {
        playCurrentAnthem(false);
      }
    });

    $("btn-war-poster")?.addEventListener("click", exportBattlePoster);

    $("btn-war-fullscreen")?.addEventListener("click", () => {
      const card = $("war-arena-card");
      if (!card) return;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else card.requestFullscreen?.().catch(() => {});
    });

    document.addEventListener("fullscreenchange", () => {
      setTimeout(resizeCanvas, 80);
    });

    const canvas = $("war-canvas");
    if (canvas) {
      canvas.addEventListener("mousemove", (ev) => {
        const r = canvas.getBoundingClientRect();
        war.mouseX = ev.clientX - r.left;
        war.mouseY = ev.clientY - r.top;
      });
      canvas.addEventListener("mouseleave", () => {
        war.mouseX = -1;
        war.mouseY = -1;
      });
      canvas.addEventListener("click", () => {
        if (war.hoveredCarrier) openEntityDrawer(war.hoveredCarrier.ent);
      });
    }

    $("wd-close")?.addEventListener("click", closeEntityDrawer);
    $("whale-drawer-backdrop")?.addEventListener("click", closeEntityDrawer);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeEntityDrawer();
    });

    window.addEventListener("resize", () => {
      if (war.running) resizeCanvas();
    });
  }

  window.DanClubWarRoom = {
    init() {
      bindWarEvents();
    },
    setData(whaleData) {
      war.data = whaleData;
      rebuildCarriers();
      renderWhalePanels();
    },
    setActive(active) {
      if (active) startArena();
      else stopArena();
    },
    rerender() {
      if (war.running) resizeCanvas();
      renderWhalePanels();
    },
  };
})();
