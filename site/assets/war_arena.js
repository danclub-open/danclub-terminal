/* DanClub Terminal — Tactical Air-Battle War Room & Entity-Deduplicated Whale Radar (MIT License) */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const COINS = ["BTC", "ETH", "SOL", "HYPE"];

  /* 6-Track Climax War Anthem Roster: Track #1 official YouTube embed (copyright-compliant) + 5 Public Domain/CC-BY MP3s */
  const WAR_TRACKS = [
    { id: "yt_heimat",    type: "youtube", videoId: "3183AsHdVGY", startSec: 45, endSec: 75, label: "🇩🇪 大德意志是我的家乡 (30s高潮)", short: "🇩🇪 大德意志是我的家乡", dur: 30 },
    { id: "valkyries",    type: "audio",   file: "./assets/audio/valkyries.mp3",    label: "🇩🇪 德 · 女武神的骑行 (25s)", short: "🇩🇪 女武神的骑行", dur: 25 },
    { id: "1812_cannon",  type: "audio",   file: "./assets/audio/1812_cannon.mp3",  label: "🇷🇺 俄 · 1812礼炮终章 (26s)", short: "🇷🇺 1812礼炮终章", dur: 26 },
    { id: "william_tell", type: "audio",   file: "./assets/audio/william_tell.mp3", label: "🇮🇹 意 · 威廉退尔冲锋 (25s)", short: "🇮🇹 威廉退尔冲锋", dur: 25 },
    { id: "toreador",     type: "audio",   file: "./assets/audio/toreador.mp3",     label: "🇫🇷 法 · 卡门斗牛士 (26s)",   short: "🇫🇷 卡门斗牛士",   dur: 26 },
    { id: "mars_war",     type: "audio",   file: "./assets/audio/mars_war.mp3",     label: "🇬🇧 英 · 火星战争使者 (24s)", short: "🇬🇧 火星战争使者", dur: 24 },
  ];

  function safeStoreGet(key, fallback = null) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : v;
    } catch {
      return fallback;
    }
  }

  function safeStoreSet(key, val) {
    try {
      localStorage.setItem(key, String(val));
    } catch {}
  }

  function readSavedVolume() {
    const raw = safeStoreGet("danclub_war_vol", null);
    if (raw === null || raw.trim() === "") return 0.75;
    const n = Number(raw);
    return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0.75;
  }

  function readSavedLikedIds() {
    const raw = safeStoreGet("danclub_barrage_liked", "");
    if (!raw) return new Set();
    try {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) {
        const clean = arr.map((x) => Number(x)).filter((n) => Number.isInteger(n) && n > 0).slice(-200);
        return new Set(clean);
      }
    } catch {}
    return new Set();
  }

  function addBoundedSet(setObj, val, maxSize) {
    if (setObj.has(val)) {
      setObj.delete(val);
    }
    setObj.add(val);
    while (setObj.size > maxSize) {
      const oldest = setObj.values().next().value;
      setObj.delete(oldest);
    }
  }

  function getBarrageApiBase() {
    if (typeof window.__DANCLUB_BARRAGE_API__ === "string" && window.__DANCLUB_BARRAGE_API__) {
      return window.__DANCLUB_BARRAGE_API__.replace(/\/+$/, "");
    }
    const host = (window.location && window.location.hostname) || "";
    if (host === "danclub.duckdns.org") return "/api/v1/barrage";
    return "https://danclub.duckdns.org/api/v1/barrage";
  }

  const war = {
    data: null,
    flows: null,
    activeCoin: "BTC",
    activeCohortCoin: "BTC",
    filterTier: "ALL",
    filterCoin: "ALL",
    filterSide: "ALL",
    filterXOnly: false,
    search: "",
    soundOn: true, // Default ON: auto-plays on load or instantaneously unlocks on first gesture!
    userMuted: false,
    hasUserGesture: false,
    autoUnlockArmed: false,
    localBlobUrl: null,
    volume: readSavedVolume(),
    trackIdx: 0, // Default #1: 大德意志是我的家乡
    bgm: null,
    ytApiPromise: null,
    ytPlayer: null,
    ytReady: false,
    ytUnavailable: false,
    ytFallbackTimer: null,
    ytBufferTimer: null,
    ytInstanceGen: 0,
    ytHasPlayedThisSeq: false,
    ytActiveSeq: 0,
    playSeq: 0,
    audioErrCount: 0,
    needsAudioReload: false,
    pausedByHidden: false,
    lastSfxTs: 0,
    audioCtx: null,
    wsHL: null,
    wsBN: null,
    wsOkxLiq: null,
    wsBnLiq: null,
    okxPingTimer: null,
    okxReconnectTimer: null,
    bnLiqReconnectTimer: null,
    bnLiqBackoffMs: 6000,
    okxCtValRetryTimer: null,
    okxCtValAttempts: 0,
    wsReconnectTimer: null,
    okxCtVal: new Map(),
    okxCtValLoaded: false,
    okxCtValLoading: false,
    liveMarkPx: {},
    liveLiqEvents: [],
    seenLiqKeys: new Set(),
    liqWsOkxUp: false,
    liqWsOkxSubscribed: false,
    liqWsBnUp: false,
    liqRailRafId: null,
    railTab: "liq",
    liqScope: "COIN",
    liqWin: "24h",
    liqTypeFilter: "ALL",
    lastMagRenderTs: 0,
    lastLiqSfxTs: 0,
    lastRadarBeepTs: 0,
    liqBeepOn: safeStoreGet("danclub_war_liq_beep", "1") !== "0",
    redCriticalSeen: new Map(),
    redCriticalPrevIds: new Map(),
    rafId: null,
    running: false,
    canvas: null,
    ctx: null,
    cssW: 900,
    cssH: 360,
    dpr: 1,
    frontline: 0.5,
    frontlineVel: 0,
    targetFrontline: 0.5,
    clashY: 180,
    clashPower: 0,
    clashSide: "LONG",
    shakeAmt: 0,
    bannerText: "",
    bannerSide: "LONG",
    bannerAlpha: 0,
    planes: [],
    sparks: [],
    rings: [],
    floatingHits: [],
    carriers: [],
    hoveredCarrier: null,
    mouseX: -1,
    mouseY: -1,
    flowBuckets: new Map(), // Per-second exact 60s rolling buckets: sec -> { buyUsd, sellUsd, count }
    lastFrameTs: 0,
    lastHudTs: 0,
    ambientTimer: 0,
    danmakuOn: false,
    danmakuActive: [],
    danmakuQueue: [],
    dmSpawnCooldown: 0,
  };

  const barrage = {
    initialized: false,
    online: false,
    pollTimer: null,
    pollInFlight: false,
    pollAbortCtrl: null,
    pollSeq: 0,
    pollCursor: 0,
    items: [],
    seenIds: new Set(),
    selfIds: new Set(),
    likedIds: readSavedLikedIds(),
    likingIds: new Set(),
    stats24h: { total: 0, long: 0, short: 0, neutral: 0, long_pct: 50, short_pct: 50 },
    composeSide: "LONG",
    filterSide: "ALL",
    cooldownUntil: 0,
    cooldownTimer: null,
    feedbackTimer: null,
    localSeq: -1,
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
    const n = strictNum(v);
    if (n === null) return "--";
    const abs = Math.abs(n);
    const sign = n < 0 ? "-" : "";
    if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
    return `${sign}$${abs.toFixed(0)}`;
  };

  const fmtSignedCompact = (v) => {
    const n = strictNum(v);
    if (n === null) return "--";
    return `${n > 0 ? "+" : ""}${fmtCompact(n)}`;
  };

  const fmtTimeShort = (ts) => {
    const n = strictNum(ts);
    if (n === null || n <= 0) return "--";
    const d = new Date(n * 1000);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}-${dd} ${hh}:${mi}`;
  };

  const fmtTimeShortBjt = (ts) => {
    const n = strictNum(ts);
    if (n === null || n <= 0) return "--";
    const d = new Date((n + 8 * 3600) * 1000);
    const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
    const dd = String(d.getUTCDate()).padStart(2, "0");
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mi = String(d.getUTCMinutes()).padStart(2, "0");
    return `${mm}-${dd} ${hh}:${mi}`;
  };

  /* Friendly display mapping strictly for Hyperliquid HIP-3 xyz:* on-chain equity/commodity perps */
  const HIP3_XYZ_MAP = {
    XYZ100: "纳指100永续",
    SP500: "标普500永续",
    GOLD: "黄金永续",
    SILVER: "白银永续",
    BRENTOIL: "布伦特原油永续",
    CL: "WTI原油永续",
    NVDA: "英伟达 NVDA",
    TSLA: "特斯拉 TSLA",
    AAPL: "苹果 AAPL",
    GOOGL: "谷歌 GOOGL",
    MSFT: "微软 MSFT",
    META: "Meta",
    MSTR: "Strategy MSTR",
    COIN: "Coinbase COIN",
    CRCL: "Circle CRCL",
    SOXL: "半导体3x SOXL",
    ANT: "Anthropic",
  };

  function fmtCoinLabel(raw) {
    const s = String(raw || "").trim();
    const colonIdx = s.indexOf(":");
    if (colonIdx <= 0) return { text: s, title: s, isHip3: false };
    const ns = s.slice(0, colonIdx).toLowerCase();
    if (ns !== "xyz") return { text: s, title: s, isHip3: false };
    const base = s.slice(colonIdx + 1).toUpperCase();
    const mapped = HIP3_XYZ_MAP[base];
    if (!mapped) {
      return { text: s, title: `Hyperliquid HIP-3 链上永续合约 ${s}`, isHip3: true };
    }
    return {
      text: `${mapped} (链上)`,
      title: `Hyperliquid HIP-3 链上永续合约 ${s}`,
      isHip3: true,
    };
  }

  /* ---------- 6-Track Hybrid War Anthem Radio (Official YouTube IFrame + Local MP3) + SFX ---------- */
  function setYtPipOpen(open, titleText = null) {
    const pip = $("war-yt-pip");
    if (!pip) return;
    pip.classList.toggle("open", Boolean(open));
    if (titleText) {
      const t = pip.querySelector(".war-yt-pip-title");
      if (t) t.textContent = titleText;
    }
  }

  function clearYtTimers() {
    if (war.ytFallbackTimer) {
      clearTimeout(war.ytFallbackTimer);
      war.ytFallbackTimer = null;
    }
    if (war.ytBufferTimer) {
      clearTimeout(war.ytBufferTimer);
      war.ytBufferTimer = null;
    }
  }

  function applyYtVolume() {
    if (!war.ytPlayer || !war.ytReady) return;
    try {
      const volPct = Math.round(war.volume * 100);
      war.ytPlayer.setVolume(volPct);
      if (volPct <= 1) war.ytPlayer.mute();
      else war.ytPlayer.unMute();
    } catch {}
  }

  function destroyYouTubePlayer(hidePip = true) {
    clearYtTimers();
    war.ytInstanceGen += 1;
    war.ytActiveSeq = -1;
    war.ytHasPlayedThisSeq = false;
    const oldPlayer = war.ytPlayer;
    war.ytPlayer = null;
    war.ytReady = false;
    if (oldPlayer) {
      try {
        oldPlayer.stopVideo?.();
      } catch {}
      try {
        oldPlayer.destroy?.();
      } catch {}
    }
    const body = document.querySelector("#war-yt-pip .war-yt-pip-body");
    if (body) {
      body.innerHTML = '<div id="war-yt-player"></div>';
    }
    if (hidePip) setYtPipOpen(false);
  }

  function stopYouTubePlayer(hidePip = true, forceDestroy = false) {
    clearYtTimers();
    war.ytActiveSeq = -1;
    war.ytHasPlayedThisSeq = false;
    if (forceDestroy || !war.ytReady) {
      destroyYouTubePlayer(hidePip);
      return;
    }
    if (war.ytPlayer && war.ytReady) {
      try {
        war.ytPlayer.pauseVideo();
      } catch {}
    }
    if (hidePip) setYtPipOpen(false);
  }

  function loadYouTubeApi(timeoutMs = 4500) {
    if (window.YT && typeof window.YT.Player === "function") {
      return Promise.resolve(window.YT);
    }
    if (war.ytApiPromise) return war.ytApiPromise;
    war.ytApiPromise = new Promise((resolve, reject) => {
      let settled = false;
      let scriptEl = null;
      const done = (ok, val) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (ok) {
          resolve(val);
        } else {
          if (scriptEl && scriptEl.parentNode) {
            scriptEl.parentNode.removeChild(scriptEl);
          }
          war.ytApiPromise = null;
          reject(val);
        }
      };
      const timer = setTimeout(() => done(false, new Error("yt_api_timeout")), timeoutMs);

      const prevReady = window.onYouTubeIframeAPIReady;
      const onReadyHook = () => {
        if (typeof prevReady === "function") {
          try { prevReady(); } catch {}
        }
        if (!settled && window.YT && typeof window.YT.Player === "function") {
          done(true, window.YT);
        }
      };
      window.onYouTubeIframeAPIReady = onReadyHook;

      const existing = document.querySelector('script[data-danclub-yt-api="1"]');
      if (existing) {
        scriptEl = existing;
      } else {
        scriptEl = document.createElement("script");
        scriptEl.src = "https://www.youtube.com/iframe_api";
        scriptEl.async = true;
        scriptEl.dataset.danclubYtApi = "1";
        scriptEl.onerror = () => done(false, new Error("yt_api_error"));
        document.head.appendChild(scriptEl);
      }
    });
    return war.ytApiPromise;
  }

  function nextPlayableIdx(fromIdx) {
    const n = WAR_TRACKS.length;
    for (let step = 1; step <= n; step++) {
      const idx = (fromIdx + step) % n;
      const t = WAR_TRACKS[idx];
      if (t && t.type === "youtube" && war.ytUnavailable) continue;
      return idx;
    }
    return Math.min(1, n - 1);
  }

  function fallbackFromYouTube(seq, reasonText) {
    if (war.playSeq !== seq) return;
    destroyYouTubePlayer(true);
    war.ytUnavailable = true;
    const fallbackIdx = WAR_TRACKS.findIndex((t) => t.type !== "youtube");
    if (fallbackIdx < 0) {
      war.soundOn = false;
      syncAudioUi(reasonText);
      return;
    }
    war.trackIdx = fallbackIdx;
    playCurrentAnthem(true, `${reasonText} · 已切至 ${WAR_TRACKS[fallbackIdx].short}`);
  }

  function createAudioElement(seq) {
    if (war.bgm) {
      try { war.bgm.pause(); } catch {}
      war.bgm.dataset.playSeq = "";
    }
    const el = new Audio();
    el.preload = "none";
    el.volume = war.volume;
    el.dataset.playSeq = String(seq);
    el.addEventListener("ended", () => {
      const cur = WAR_TRACKS[war.trackIdx];
      if (el !== war.bgm || !war.soundOn || !war.running || document.hidden) return;
      if (!cur || cur.type === "youtube" || el.dataset.playSeq !== String(war.playSeq)) return;
      war.audioErrCount = 0;
      war.trackIdx = nextPlayableIdx(war.trackIdx);
      playCurrentAnthem(true);
    });
    el.addEventListener("error", () => {
      const cur = WAR_TRACKS[war.trackIdx];
      if (el !== war.bgm || !war.soundOn || !war.running || document.hidden || war.pausedByHidden) return;
      if (!cur || cur.type === "youtube" || el.dataset.playSeq !== String(war.playSeq)) return;
      if (war.audioErrCount < 1) {
        war.audioErrCount += 1;
        war.trackIdx = nextPlayableIdx(war.trackIdx);
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
      btn.textContent = war.soundOn ? "⏸ 关闭战歌" : "▶ 开启战歌";
      btn.title = war.soundOn ? "点击立即暂停并关闭背景战歌与空战音效" : "点击开启多国高潮战歌与空战音效";
      btn.classList.toggle("active", war.soundOn);
    }
    if (prompt) {
      prompt.classList.toggle("hidden", war.soundOn);
    }
    if (status) {
      if (customStatus) {
        status.textContent = customStatus;
      } else if (war.soundOn) {
        const srcTag = track.type === "youtube" ? "YouTube 官方高潮" : "高潮";
        status.textContent = `正在轮播 [${war.trackIdx + 1}/${WAR_TRACKS.length}] ${track.short} · ${srcTag} ${track.dur}s`;
      } else {
        status.textContent = `首曲：${track.short} · 点击「开启战歌」或页面任意处开播`;
      }
    }
  }

  let _gestureUnlockHandler = null;
  function disarmGestureUnlock() {
    if (_gestureUnlockHandler) {
      window.removeEventListener("pointerdown", _gestureUnlockHandler, true);
      window.removeEventListener("keydown", _gestureUnlockHandler, true);
      _gestureUnlockHandler = null;
      war.autoUnlockArmed = false;
    }
  }

  function armGestureUnlock() {
    if (war.autoUnlockArmed || war.userMuted) return;
    war.autoUnlockArmed = true;
    _gestureUnlockHandler = (ev) => {
      const t = ev.target;
      if (ev.type === "keydown" && t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) {
        return;
      }
      war.hasUserGesture = true;
      if (t && typeof t.closest === "function" && t.closest('#btn-war-sound, #war-audio-prompt, #war-local-file, .war-local-btn, #war-track-select, #btn-war-next-track, #war-yt-pip, .nav-tab:not([data-tab="war"])')) {
        disarmGestureUnlock();
        return;
      }
      disarmGestureUnlock();
      if (!war.userMuted && war.running && !document.hidden) {
        war.audioErrCount = 0;
        playCurrentAnthem(false);
      }
    };
    window.addEventListener("pointerdown", _gestureUnlockHandler, true);
    window.addEventListener("keydown", _gestureUnlockHandler, true);
  }

  function playYouTubeTrack(track, seq, forceReload = false) {
    if (war.bgm) {
      war.bgm.dataset.playSeq = "";
      try { war.bgm.pause(); } catch {}
    }

    if (!war.hasUserGesture) {
      war.soundOn = false;
      armGestureUnlock();
      syncAudioUi(`默认首曲：${track.short} · 点击页面任意处即刻开播`);
      return;
    }

    // Every YouTube round gets a fresh player instance (new instGen) so stale onError/ENDED can never hit a new round
    destroyYouTubePlayer(false);

    war.soundOn = true;
    war.pausedByHidden = false;
    war.ytActiveSeq = seq;
    war.ytHasPlayedThisSeq = false;
    disarmGestureUnlock();
    setYtPipOpen(true, `${track.short} · YouTube`);
    syncAudioUi(`正在接入 YouTube 官方音源 · ${track.short}…`);

    clearYtTimers();
    war.ytFallbackTimer = setTimeout(() => {
      fallbackFromYouTube(seq, "YouTube 音源连接超时（国内网络需代理）");
    }, 6500);

    loadYouTubeApi(4500)
      .then((YT) => {
        if (war.playSeq !== seq || !war.soundOn || !war.running || document.hidden) return;
        const instGen = ++war.ytInstanceGen;
        war.ytPlayer = new YT.Player("war-yt-player", {
          width: "240",
          height: "135",
          videoId: track.videoId,
          playerVars: {
            autoplay: 0,
            controls: 1,
            rel: 0,
            modestbranding: 1,
            playsinline: 1,
            start: track.startSec || 0,
            end: track.endSec || undefined,
            origin: window.location.origin,
          },
          events: {
            onReady: (ev) => {
              if (instGen !== war.ytInstanceGen) {
                try { ev.target.destroy?.(); } catch {}
                return;
              }
              war.ytReady = true;
              applyYtVolume();
              if (war.playSeq !== war.ytActiveSeq || !war.soundOn || !war.running || document.hidden) {
                destroyYouTubePlayer(true);
                return;
              }
              const activeTrack = WAR_TRACKS[war.trackIdx];
              if (activeTrack && activeTrack.type === "youtube") {
                try {
                  ev.target.loadVideoById({
                    videoId: activeTrack.videoId,
                    startSeconds: activeTrack.startSec || 0,
                    endSeconds: activeTrack.endSec || undefined,
                  });
                } catch {}
              }
            },
            onStateChange: (ev) => {
              if (instGen !== war.ytInstanceGen || war.playSeq !== war.ytActiveSeq) return;
              const curSeq = war.ytActiveSeq;
              if (ev.data === YT.PlayerState.PLAYING) {
                clearYtTimers();
                war.ytUnavailable = false;
                war.ytHasPlayedThisSeq = true;
                applyYtVolume();
                syncAudioUi();
              } else if (ev.data === YT.PlayerState.BUFFERING) {
                if (war.ytHasPlayedThisSeq && war.soundOn && war.running && !document.hidden && !war.ytBufferTimer) {
                  war.ytBufferTimer = setTimeout(() => {
                    if (instGen === war.ytInstanceGen && war.playSeq === curSeq) {
                      fallbackFromYouTube(curSeq, "YouTube 缓冲中断");
                    }
                  }, 7500);
                }
              } else if (ev.data === YT.PlayerState.PAUSED) {
                if (war.ytBufferTimer) {
                  clearTimeout(war.ytBufferTimer);
                  war.ytBufferTimer = null;
                }
              } else if (ev.data === YT.PlayerState.ENDED) {
                if (!war.ytHasPlayedThisSeq) return;
                clearYtTimers();
                destroyYouTubePlayer(true);
                if (war.soundOn && war.running && !document.hidden) {
                  war.trackIdx = nextPlayableIdx(war.trackIdx);
                  playCurrentAnthem(true);
                }
              }
            },
            onError: () => {
              if (instGen !== war.ytInstanceGen || war.playSeq !== war.ytActiveSeq) return;
              fallbackFromYouTube(war.ytActiveSeq, "YouTube 音源受限");
            },
          },
        });
      })
      .catch(() => {
        fallbackFromYouTube(seq, "YouTube 音源未连通");
      });
  }

  function playCurrentAnthem(forceReload = false, customStatus = null) {
    const track = WAR_TRACKS[war.trackIdx] || WAR_TRACKS[0];
    const seq = ++war.playSeq;

    try {
      if (!war.audioCtx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (AC) war.audioCtx = new AC();
      }
      if (war.audioCtx && war.audioCtx.state === "suspended") {
        war.audioCtx.resume().catch(() => {});
      }
    } catch {}

    if (track.type === "youtube") {
      playYouTubeTrack(track, seq, forceReload);
      return;
    }

    // Synchronously destroy YouTube iframe before starting local MP3 -> zero dual-audio overlap!
    destroyYouTubePlayer(true);
    const needFreshEl =
      !war.bgm || forceReload || war.needsAudioReload || war.bgm.error || war.bgm.dataset.trackId !== track.id;
    const el = needFreshEl ? createAudioElement(seq) : war.bgm;
    el.volume = war.volume;
    el.dataset.playSeq = String(seq);

    if (needFreshEl) {
      war.needsAudioReload = false;
      el.dataset.trackId = track.id;
      el.src = track.file;
      el.load();
      el.currentTime = 0;
    }

    war.soundOn = true;
    war.pausedByHidden = false;
    disarmGestureUnlock();
    syncAudioUi(customStatus);

    const p = el.play();
    if (p && typeof p.catch === "function") {
      p.catch((err) => {
        if (war.playSeq !== seq) return;
        war.soundOn = false;
        const blocked = err && err.name === "NotAllowedError";
        if (!blocked) {
          war.needsAudioReload = true;
          syncAudioUi("战歌加载受阻，请点击「开启战歌」重试");
        } else {
          armGestureUnlock();
          syncAudioUi("战歌默认开启 · 点击页面任意处即刻燃起");
        }
      });
    }
  }

  function stopAnthem(keepPreference = false) {
    war.playSeq += 1;
    destroyYouTubePlayer(true);
    if (war.bgm) {
      war.bgm.dataset.playSeq = "";
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
    war.trackIdx = nextPlayableIdx(war.trackIdx);
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

  function syncLiqBeepUi() {
    const btn = $("btn-war-liq-beep");
    if (!btn) return;
    btn.textContent = war.liqBeepOn ? "强平音 开" : "强平音 关";
    btn.classList.toggle("btn-toggle-active", war.liqBeepOn);
    btn.setAttribute("aria-pressed", war.liqBeepOn ? "true" : "false");
  }

  function playLiqRadarBeep(isCritical = false, fromGesture = false) {
    if (!war.liqBeepOn && !fromGesture) return;
    const nowMs = Date.now();
    if (!fromGesture && nowMs - war.lastRadarBeepTs < 500) return;
    war.lastRadarBeepTs = nowMs;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      if (!war.audioCtx) {
        if (!fromGesture) return;
        war.audioCtx = new Ctx();
      }
      const ctx = war.audioCtx;
      const fire = () => {
        const now = ctx.currentTime + 0.01;
        const notes = isCritical
          ? [
              { freq: 987.77, start: 0.0, dur: 0.13, gain: 0.15 },
              { freq: 1318.51, start: 0.11, dur: 0.24, gain: 0.16 },
            ]
          : [{ freq: 783.99, start: 0.0, dur: 0.18, gain: 0.12 }];
        notes.forEach((n) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = isCritical ? "triangle" : "sine";
          osc.frequency.setValueAtTime(n.freq, now + n.start);
          gain.gain.setValueAtTime(0.0001, now + n.start);
          gain.gain.exponentialRampToValueAtTime(n.gain, now + n.start + 0.012);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + n.start + n.dur);
          osc.connect(gain).connect(ctx.destination);
          osc.start(now + n.start);
          osc.stop(now + n.start + n.dur + 0.02);
        });
      };
      if (ctx.state === "running") fire();
      else if (fromGesture && ctx.state === "suspended") ctx.resume().then(fire).catch(() => {});
    } catch {}
  }

  function playLiqExplosionSfx(usd) {
    if (war.liqBeepOn) {
      playLiqRadarBeep(usd >= 500_000, false);
    }
    if (!war.soundOn || war.userMuted || war.volume <= 0) return;
    const nowMs = Date.now();
    if (nowMs - war.lastLiqSfxTs < 320) return;
    war.lastLiqSfxTs = nowMs;
    try {
      const ctx = ensureAudioCtx();
      if (!ctx || ctx.state !== "running") return;
      const now = ctx.currentTime + 0.01;
      const scale = Math.max(0.2, Math.min(1, war.volume));
      const heavy = usd >= 500_000;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(heavy ? 185 : 240, now);
      osc.frequency.exponentialRampToValueAtTime(42, now + (heavy ? 0.42 : 0.26));
      gain.gain.setValueAtTime((heavy ? 0.22 : 0.14) * scale, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + (heavy ? 0.44 : 0.28));
      osc.connect(gain).connect(ctx.destination);
      osc.start(now);
      osc.stop(now + (heavy ? 0.46 : 0.3));
    } catch {}
  }

  function getMatchedLivePx(venue, coin) {
    const entry = war.liveMarkPx[`${venue}:${coin}`];
    if (!entry || !Number.isFinite(entry.px) || entry.px <= 0) return null;
    if (Date.now() - entry.ts > 15000) return null;
    return entry.px;
  }

  function liveWhaleDistPct(pos) {
    if (!pos || pos.liq_px === null || pos.liq_px === undefined || pos.liq_px === "") return null;
    const lq = Number(pos.liq_px);
    if (!Number.isFinite(lq) || lq <= 0) return null;
    const venues = Array.isArray(pos.venues) ? pos.venues : [];
    if (venues.length !== 1) return null;
    // Strict venue-matched official HL markPx (from activeAssetCtx WS); else verified snapshot distance/mark_px!
    const livePx = venues[0] === "HL" ? getMatchedLivePx("HL_MARK", pos.coin) : null;
    if (livePx === null) {
      if (
        pos.liq_dist_pct !== null &&
        pos.liq_dist_pct !== undefined &&
        pos.liq_dist_pct !== "" &&
        Number.isFinite(Number(pos.liq_dist_pct))
      ) {
        const sd = Number(pos.liq_dist_pct);
        return sd > 0 && sd <= 60 ? sd : null;
      }
      const snapPx = Number(pos.mark_px);
      if (!Number.isFinite(snapPx) || snapPx <= 0) return null;
      const snapDist = pos.side === "LONG" ? ((snapPx - lq) / snapPx) * 100 : ((lq - snapPx) / snapPx) * 100;
      return Number.isFinite(snapDist) && snapDist > 0 && snapDist <= 60 ? snapDist : null;
    }
    const raw = pos.side === "LONG" ? ((livePx - lq) / livePx) * 100 : ((lq - livePx) / livePx) * 100;
    if (!Number.isFinite(raw) || raw > 60) return null;
    return raw;
  }

  function fmtPxShort(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return "--";
    if (n >= 1000) return `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
    if (n >= 1) return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    return `$${n.toFixed(4)}`;
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
        const yFrac = narrow ? (idx + 0.55) / (rows + 0.1) : (idx + 0.85) / (rows + 0.7);
        const xBase = narrow
          ? (side === "LONG" ? 0.17 : 0.83)
          : (side === "LONG" ? 0.075 + (idx % 2) * 0.095 : 0.925 - (idx % 2) * 0.095);
        const sizeBase = narrow ? 12 : 15;
        const sizeMax = narrow ? 18 : 30;
        const size = Math.max(sizeBase, Math.min(sizeMax, (narrow ? 8 : 11) + Math.log10(Math.max(1e6, it.pos.usd) / 1e6) * (narrow ? 5.5 : 9.5)));
        war.carriers.push({
          ent: it.ent,
          pos: it.pos,
          side,
          xFrac: xBase,
          yFrac: narrow ? 0.13 + yFrac * 0.74 : 0.14 + yFrac * 0.72,
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

  /* ---------- Real-Time Public Liquidation Streams (OKX Verified ctVal + Binance Futures) ---------- */
  function updateLiqWsUi() {
    const dot = $("liq-ws-dot");
    const lbl = $("liq-ws-status");
    const okxReady = war.liqWsOkxUp && war.liqWsOkxSubscribed && war.okxCtValLoaded;
    const sources = [];
    if (okxReady) sources.push("OKX");
    if (war.liqWsBnUp) sources.push("Binance");
    const up = sources.length > 0;
    if (dot) dot.classList.toggle("online", up);
    if (lbl) {
      const exCnt = Number(war.data?.liquidations?.exchanges_count) || 0;
      const snapLbl = exCnt > 0 ? `${exCnt}所快照` : "CoinGlass 快照";
      if (up) {
        lbl.textContent = `${sources.join("+")} 实时强平 · ${snapLbl}`;
      } else if (war.liqWsOkxUp && !war.okxCtValLoaded) {
        lbl.textContent = `OKX 乘数加载中 · ${snapLbl}`;
      } else {
        lbl.textContent = `${snapLbl} · 实时流连接中`;
      }
    }
  }

  function scheduleLiqRailRender() {
    if (war.liqRailRafId !== null || !war.running || document.hidden) return;
    war.liqRailRafId = requestAnimationFrame(() => {
      war.liqRailRafId = null;
      if (war.running && !document.hidden && war.railTab === "liq") renderLiqRail();
    });
  }

  async function ensureOkxCtValMap() {
    if (war.okxCtValLoaded || war.okxCtValLoading || war.okxCtValAttempts >= 4) return;
    war.okxCtValLoading = true;
    war.okxCtValAttempts += 1;
    clearTimeout(war.okxCtValRetryTimer);
    war.okxCtValRetryTimer = null;
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const tid = ctrl ? setTimeout(() => ctrl.abort(), 5000) : null;
    try {
      const resp = await fetch("https://www.okx.com/api/v5/public/instruments?instType=SWAP", {
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = await resp.json();
      const rows = Array.isArray(body?.data) ? body.data : [];
      rows.forEach((it) => {
        const instId = String(it?.instId || "");
        if (!instId.endsWith("-USDT-SWAP")) return;
        const baseCoin = instId.split("-")[0].toUpperCase();
        const ctVal = Number(it?.ctVal);
        const ctCcy = String(it?.ctValCcy || "").toUpperCase();
        // Strict base-currency linear contract check so sz * ctVal * bkPx is mathematically valid
        if (Number.isFinite(ctVal) && ctVal > 0 && ctCcy === baseCoin) {
          war.okxCtVal.set(instId, ctVal);
        }
      });
      if (war.okxCtVal.size > 0) {
        war.okxCtValLoaded = true;
        updateLiqWsUi();
        return;
      }
      throw new Error("Empty OKX instrument list");
    } catch {
      // Bounded backoff retry while War Room stays active
      if (war.running && war.okxCtValAttempts < 4) {
        const delay = Math.min(30000, 5000 * Math.pow(2, war.okxCtValAttempts - 1));
        war.okxCtValRetryTimer = setTimeout(() => ensureOkxCtValMap(), delay);
      }
    } finally {
      if (tid) clearTimeout(tid);
      war.okxCtValLoading = false;
      updateLiqWsUi();
    }
  }

  function ingestLiquidation(coin, side, usd, px, exShort, rawTsMs, rawQtyStr, pxKind = "均价") {
    const c = String(coin || "").trim().toUpperCase().replace(/[^A-Z0-9:/_-]/g, "").slice(0, 16);
    if (!c || (side !== "LONG" && side !== "SHORT")) return;
    if (!Number.isFinite(usd) || usd < 5_000 || !Number.isFinite(px) || px <= 0) return;
    const tsMs = Number.isFinite(rawTsMs) && rawTsMs > 0 ? Math.floor(rawTsMs) : Date.now();
    const ts = Math.floor(tsMs / 1000);
    const qtyTag = String(rawQtyStr ?? Math.round(usd)).trim().slice(0, 24);
    const dedupKey = `${exShort}:${c}:${side}:${tsMs}:${qtyTag}`;
    if (war.seenLiqKeys.has(dedupKey)) return;
    addBoundedSet(war.seenLiqKeys, dedupKey, 240);

    war.liveLiqEvents.unshift({
      ts,
      ex: exShort === "BN" ? "Binance" : exShort,
      ex_short: exShort,
      coin: c,
      side,
      usd: Math.round(usd),
      px,
      px_kind: pxKind,
      live: true,
    });
    if (war.liveLiqEvents.length > 30) war.liveLiqEvents.length = 30;

    // Trigger real radar explosion blast ONLY when the liquidation matches the active battlefield coin!
    if (c === war.activeCoin && war.running) {
      const W = war.cssW || 800;
      const H = war.cssH || 350;
      const fx = W * war.frontline;
      const isLongLiq = side === "LONG";
      const bx = isLongLiq
        ? Math.max(46, fx * (0.35 + Math.random() * 0.45))
        : Math.min(W - 46, fx + (W - fx) * (0.2 + Math.random() * 0.45));
      const by = 48 + Math.random() * Math.max(60, H - 108);
      const heavy = usd >= 100_000;
      const mega = usd >= 500_000;

      war.rings.push({
        x: bx,
        y: by,
        r: 6,
        maxR: mega ? 108 : heavy ? 74 : 44,
        color: "#f59e0b",
        alpha: 0.96,
      });
      war.rings.push({
        x: bx,
        y: by,
        r: 4,
        maxR: mega ? 76 : heavy ? 50 : 30,
        color: isLongLiq ? "#10b981" : "#f43f5e",
        alpha: 0.92,
      });

      const sparkN = mega ? 34 : heavy ? 22 : 12;
      for (let k = 0; k < sparkN; k++) {
        const ang = Math.random() * Math.PI * 2;
        const spd = 55 + Math.random() * (mega ? 220 : 140);
        war.sparks.push({
          x: bx,
          y: by,
          vx: Math.cos(ang) * spd,
          vy: Math.sin(ang) * spd,
          life: 1.0,
          color: k % 2 === 0 ? "#f59e0b" : isLongLiq ? "#6ee7b7" : "#fda4af",
          size: mega ? 3.0 : 2.1,
        });
      }

      if (war.floatingHits.length < 22) {
        war.floatingHits.push({
          x: bx,
          y: by - 8,
          vy: -28,
          text: `爆${isLongLiq ? "多" : "空"} -${fmtCompact(usd)} (${exShort})`,
          color: "#fbbf24",
          big: heavy,
          alpha: 1.0,
        });
      }

      const pushDir = isLongLiq ? -1 : 1;
      war.frontlineVel = Math.max(-0.95, Math.min(0.95, war.frontlineVel + pushDir * (mega ? 0.55 : heavy ? 0.28 : 0.1)));
      war.clashY = by;
      war.clashSide = isLongLiq ? "SHORT" : "LONG";
      war.clashPower = Math.min(1.6, war.clashPower + (heavy ? 0.9 : 0.35));

      if (heavy) {
        war.shakeAmt = Math.max(war.shakeAmt, mega ? 10 : 5.5);
        war.bannerText = `实盘强平 · ${c} ${isLongLiq ? "多单爆仓" : "空单爆仓"} ${fmtCompact(usd)} (${exShort} ${pxKind} ${fmtPxShort(px)})`;
        war.bannerSide = isLongLiq ? "SHORT" : "LONG";
        war.bannerAlpha = 1.15;
        playLiqExplosionSfx(usd);
      }
    }

    // Coalesce rail DOM re-render and skip when current rail filters hide this event
    const visibleInRail =
      war.railTab === "liq" &&
      (war.liqTypeFilter === "ALL" || war.liqTypeFilter === "LIQ") &&
      (war.liqScope === "ALL" || c === war.activeCoin);
    if (visibleInRail) {
      scheduleLiqRailRender();
    }
  }

  function disconnectLiqSockets() {
    clearTimeout(war.okxReconnectTimer);
    clearTimeout(war.bnLiqReconnectTimer);
    clearTimeout(war.okxCtValRetryTimer);
    war.okxCtValRetryTimer = null;
    clearInterval(war.okxPingTimer);
    war.okxPingTimer = null;
    if (war.wsOkxLiq) {
      try { war.wsOkxLiq.close(); } catch {}
      war.wsOkxLiq = null;
    }
    if (war.wsBnLiq) {
      try { war.wsBnLiq.close(); } catch {}
      war.wsBnLiq = null;
    }
    war.liqWsOkxUp = false;
    war.liqWsOkxSubscribed = false;
    war.liqWsBnUp = false;
    updateLiqWsUi();
  }

  function connectOkxLiqSocket() {
    clearTimeout(war.okxReconnectTimer);
    clearInterval(war.okxPingTimer);
    war.okxPingTimer = null;
    if (war.wsOkxLiq) {
      try { war.wsOkxLiq.close(); } catch {}
      war.wsOkxLiq = null;
    }
    war.liqWsOkxUp = false;
    war.liqWsOkxSubscribed = false;
    if (!war.okxCtValLoaded) {
      ensureOkxCtValMap();
    }
    try {
      const wsO = new WebSocket("wss://ws.okx.com:8443/ws/v5/public");
      war.wsOkxLiq = wsO;
      wsO.addEventListener("open", () => {
        if (war.wsOkxLiq !== wsO) return;
        war.liqWsOkxUp = true;
        updateLiqWsUi();
        wsO.send(JSON.stringify({ op: "subscribe", args: [{ channel: "liquidation-orders", instType: "SWAP" }] }));
        clearInterval(war.okxPingTimer);
        war.okxPingTimer = setInterval(() => {
          if (war.wsOkxLiq === wsO && wsO.readyState === WebSocket.OPEN) {
            try { wsO.send("ping"); } catch {}
          }
        }, 25000);
      });
      wsO.addEventListener("message", (ev) => {
        if (war.wsOkxLiq !== wsO || ev.data === "pong") return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg?.event === "subscribe" && msg?.arg?.channel === "liquidation-orders") {
            war.liqWsOkxSubscribed = true;
            updateLiqWsUi();
            return;
          }
          if (msg?.arg?.channel !== "liquidation-orders" || !Array.isArray(msg.data)) return;
          msg.data.forEach((grp) => {
            const instId = String(grp?.instId || "");
            if (!instId.endsWith("-USDT-SWAP")) return;
            const ctVal = war.okxCtVal.get(instId);
            if (!ctVal) return; // Fail-closed: never guess contract multiplier
            const baseCoin = instId.split("-")[0];
            (grp.details || []).forEach((d) => {
              const px = Number(d?.bkPx);
              const sz = Number(d?.sz);
              const posSide = String(d?.posSide || "").toLowerCase();
              const ordSide = String(d?.side || "").toLowerCase();
              const side =
                posSide === "long" ? "LONG" : posSide === "short" ? "SHORT" : ordSide === "sell" ? "LONG" : ordSide === "buy" ? "SHORT" : "";
              if (!side || !Number.isFinite(px) || !Number.isFinite(sz) || px <= 0 || sz <= 0) return;
              const usd = sz * ctVal * px;
              const tsMs = Number(d?.ts) || Date.now();
              ingestLiquidation(baseCoin, side, usd, px, "OKX", tsMs, String(d?.sz ?? ""), "破产价");
            });
          });
        } catch {}
      });
      wsO.addEventListener("close", () => {
        if (war.wsOkxLiq === wsO) {
          war.wsOkxLiq = null;
          war.liqWsOkxUp = false;
          war.liqWsOkxSubscribed = false;
          clearInterval(war.okxPingTimer);
          war.okxPingTimer = null;
          updateLiqWsUi();
          if (war.running) {
            war.okxReconnectTimer = setTimeout(connectOkxLiqSocket, 6000);
          }
        }
      });
      wsO.addEventListener("error", () => {});
    } catch {}
  }

  function connectBnLiqSocket() {
    clearTimeout(war.bnLiqReconnectTimer);
    if (war.wsBnLiq) {
      try { war.wsBnLiq.close(); } catch {}
      war.wsBnLiq = null;
    }
    try {
      const wsB = new WebSocket("wss://fstream.binance.com/ws/!forceOrder@arr");
      war.wsBnLiq = wsB;
      wsB.addEventListener("open", () => {
        if (war.wsBnLiq !== wsB) return;
        war.liqWsBnUp = true;
        war.bnLiqBackoffMs = 6000;
        updateLiqWsUi();
      });
      wsB.addEventListener("message", (ev) => {
        if (war.wsBnLiq !== wsB) return;
        try {
          const msg = JSON.parse(ev.data);
          const o = msg?.o;
          if (!o || typeof o !== "object") return;
          const sym = String(o.s || "");
          if (!sym.endsWith("USDT")) return;
          const coin = sym.slice(0, -4);
          const px = Number(o.ap || o.p);
          const qty = Number(o.z || o.q);
          const side = o.S === "SELL" ? "LONG" : o.S === "BUY" ? "SHORT" : "";
          if (!side || !Number.isFinite(px) || !Number.isFinite(qty) || px <= 0 || qty <= 0) return;
          const tsMs = Number(o.T || msg.E) || Date.now();
          ingestLiquidation(coin, side, px * qty, px, "BN", tsMs, String(o.z || o.q || ""), "均价");
        } catch {}
      });
      wsB.addEventListener("close", () => {
        if (war.wsBnLiq === wsB) {
          war.wsBnLiq = null;
          war.liqWsBnUp = false;
          updateLiqWsUi();
          if (war.running) {
            const wait = war.bnLiqBackoffMs;
            war.bnLiqBackoffMs = Math.min(45000, Math.round(war.bnLiqBackoffMs * 1.8));
            war.bnLiqReconnectTimer = setTimeout(connectBnLiqSocket, wait);
          }
        }
      });
      wsB.addEventListener("error", () => {});
    } catch {}
  }

  function connectLiqSockets() {
    connectOkxLiqSocket();
    connectBnLiqSocket();
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

    // 1. Hyperliquid Public Trades + Official activeAssetCtx Mark Price WebSocket (zero 451 geo-block!)
    try {
      const ws = new WebSocket("wss://api.hyperliquid.xyz/ws");
      war.wsHL = ws;
      ws.addEventListener("open", () => {
        if (war.wsHL !== ws) return;
        ws.send(JSON.stringify({ method: "subscribe", subscription: { type: "trades", coin } }));
        ws.send(JSON.stringify({ method: "subscribe", subscription: { type: "activeAssetCtx", coin } }));
        if (statusEl) statusEl.textContent = `实况直连 · ${coin} (Hyperliquid)`;
      });
      ws.addEventListener("message", (ev) => {
        if (war.wsHL !== ws) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.channel === "activeAssetCtx" && msg.data?.ctx) {
            const mpx = Number(msg.data.ctx.markPx);
            const mCoin = String(msg.data.coin || coin).toUpperCase();
            if (Number.isFinite(mpx) && mpx > 0) {
              war.liveMarkPx[`HL_MARK:${mCoin}`] = { px: mpx, ts: Date.now() };
            }
          } else if (msg.channel === "trades" && Array.isArray(msg.data)) {
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

  /* ---------- High-Detail Multi-Layer Vector Aircraft & Dreadnought Carrier Models ---------- */
  function drawAircraft(ctx, x, y, dir, tier, color, ts = 0) {
    ctx.save();
    ctx.translate(x, y);
    if (dir < 0) ctx.scale(-1, 1);
    const flicker = 0.72 + Math.sin(ts * 0.045 + x * 0.12) * 0.28;
    const isLong = dir > 0;
    const accentGlow = isLong ? "#6ee7b7" : "#fda4af";
    const flameCol = isLong ? "#38bdf8" : "#f59e0b";

    if (tier === 1) {
      // Tier 1 (<$50K): Tactical Plasma Drone Dart with swept micro-fins & hot ion tail
      const tailG = ctx.createLinearGradient(-15, 0, 6, 0);
      tailG.addColorStop(0, "transparent");
      tailG.addColorStop(0.55, color);
      tailG.addColorStop(1, "#ffffff");
      ctx.strokeStyle = tailG;
      ctx.lineWidth = 2.0;
      ctx.beginPath();
      ctx.moveTo(-14, 0);
      ctx.lineTo(6, 0);
      ctx.stroke();

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(8, 0);
      ctx.lineTo(-2, -3.8);
      ctx.lineTo(-5, -3.2);
      ctx.lineTo(-2.5, 0);
      ctx.lineTo(-5, 3.2);
      ctx.lineTo(-2, 3.8);
      ctx.closePath();
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(6.5, 0, 1.5, 0, Math.PI * 2);
      ctx.fill();
    } else if (tier === 2) {
      // Tier 2 ($50K-$250K): F-22 Raptor Stealth Light Fighter with cockpit glass & afterburner
      // Dynamic pulsing afterburner jet cone
      const fLen = 9 + flicker * 8;
      const fGrad = ctx.createLinearGradient(-12 - fLen, 0, -10, 0);
      fGrad.addColorStop(0, "transparent");
      fGrad.addColorStop(0.5, flameCol);
      fGrad.addColorStop(1, "#ffffff");
      ctx.fillStyle = fGrad;
      ctx.beginPath();
      ctx.moveTo(-10, -2.2);
      ctx.lineTo(-12 - fLen, 0);
      ctx.lineTo(-10, 2.2);
      ctx.closePath();
      ctx.fill();

      // Trapezoidal swept stealth wings & canted twin tails
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 11;
      ctx.beginPath();
      ctx.moveTo(16, 0);
      ctx.lineTo(7, -2.6);
      ctx.lineTo(-2, -11.5);
      ctx.lineTo(-7.5, -10.5);
      ctx.lineTo(-6, -3.4);
      ctx.lineTo(-13, -6.2);
      ctx.lineTo(-14, -4.4);
      ctx.lineTo(-11, -1.6);
      ctx.lineTo(-11, 1.6);
      ctx.lineTo(-14, 4.4);
      ctx.lineTo(-13, 6.2);
      ctx.lineTo(-6, 3.4);
      ctx.lineTo(-7.5, 10.5);
      ctx.lineTo(-2, 11.5);
      ctx.lineTo(7, 2.6);
      ctx.closePath();
      ctx.fill();
      ctx.shadowBlur = 0;

      // Specular metallic ridge & tinted glass cockpit canopy
      ctx.strokeStyle = accentGlow;
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.moveTo(14, 0);
      ctx.lineTo(-9, 0);
      ctx.stroke();

      ctx.fillStyle = "#e0f2fe";
      ctx.beginPath();
      ctx.ellipse(6.5, 0, 3.6, 1.4, 0, 0, Math.PI * 2);
      ctx.fill();
    } else if (tier === 3) {
      // Tier 3 ($250K-$1M): Su-57 Heavy Twin-Engine Strike Interceptor with canards & dual plasma plumes
      const fLen = 13 + flicker * 10;
      [-3.4, 3.4].forEach((ey) => {
        const fg = ctx.createLinearGradient(-16 - fLen, ey, -14, ey);
        fg.addColorStop(0, "transparent");
        fg.addColorStop(0.45, flameCol);
        fg.addColorStop(1, "#ffffff");
        ctx.fillStyle = fg;
        ctx.beginPath();
        ctx.moveTo(-14, ey - 1.9);
        ctx.lineTo(-16 - fLen, ey);
        ctx.lineTo(-14, ey + 1.9);
        ctx.closePath();
        ctx.fill();
      });

      // Multi-segment armored fuselage + forward canards + broad swept strike wings
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 17;
      ctx.beginPath();
      ctx.moveTo(23, 0);
      ctx.lineTo(13, -3.2);
      ctx.lineTo(9, -7.8);   // Forward canard
      ctx.lineTo(5.5, -6.8);
      ctx.lineTo(6.5, -3.8);
      ctx.lineTo(-4, -17.5); // Swept strike wing
      ctx.lineTo(-11.5, -15.5);
      ctx.lineTo(-8.5, -5.5);
      ctx.lineTo(-17.5, -8.5);
      ctx.lineTo(-18.5, -5.8);
      ctx.lineTo(-14.5, -3.6);
      ctx.lineTo(-15.5, 0);
      ctx.lineTo(-14.5, 3.6);
      ctx.lineTo(-18.5, 5.8);
      ctx.lineTo(-17.5, 8.5);
      ctx.lineTo(-8.5, 5.5);
      ctx.lineTo(-11.5, 15.5);
      ctx.lineTo(-4, 17.5);
      ctx.lineTo(6.5, 3.8);
      ctx.lineTo(5.5, 6.8);
      ctx.lineTo(9, 7.8);
      ctx.lineTo(13, 3.2);
      ctx.closePath();
      ctx.fill();
      ctx.shadowBlur = 0;

      // Armor trim & twin-seater canopy + glowing nose radar
      ctx.strokeStyle = "rgba(255,255,255,0.68)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#e0f2fe";
      ctx.beginPath();
      ctx.ellipse(10.5, 0, 5.2, 1.9, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(21.5, 0, 1.6, 0, Math.PI * 2);
      ctx.fill();
    } else {
      // Tier 4 (>=$1M): B-2 Spirit Strategic Stealth Flying-Wing Bomber with 4 dorsal exhausts & Mach cone!
      // Shockwave Mach cone in front of nose
      ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(14, -26);
      ctx.quadraticCurveTo(34, 0, 14, 26);
      ctx.stroke();

      // 4 recessed strategic jet plumes
      [-10, -5, 5, 10].forEach((ey) => {
        const fg = ctx.createLinearGradient(-18 - flicker * 14, ey, -10, ey);
        fg.addColorStop(0, "transparent");
        fg.addColorStop(0.55, "#f59e0b");
        fg.addColorStop(1, "#ffffff");
        ctx.fillStyle = fg;
        ctx.beginPath();
        ctx.moveTo(-10, ey - 1.7);
        ctx.lineTo(-22 - flicker * 11, ey);
        ctx.lineTo(-10, ey + 1.7);
        ctx.closePath();
        ctx.fill();
      });

      // Iconic W-edge stealth flying-wing silhouette
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 26;
      ctx.beginPath();
      ctx.moveTo(28, 0);
      ctx.lineTo(-3, -29);
      ctx.lineTo(-14, -24);
      ctx.lineTo(-8, -14);
      ctx.lineTo(-18, -9);
      ctx.lineTo(-11, -3.5);
      ctx.lineTo(-17, 0);
      ctx.lineTo(-11, 3.5);
      ctx.lineTo(-18, 9);
      ctx.lineTo(-8, 14);
      ctx.lineTo(-14, 24);
      ctx.lineTo(-3, 29);
      ctx.closePath();
      ctx.fill();
      ctx.shadowBlur = 0;

      // Internal stealth panel seam lines
      ctx.strokeStyle = "rgba(255, 255, 255, 0.8)";
      ctx.lineWidth = 1.3;
      ctx.stroke();
      ctx.strokeStyle = "rgba(255, 255, 255, 0.32)";
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.moveTo(17, 0);
      ctx.lineTo(-5, -18);
      ctx.moveTo(17, 0);
      ctx.lineTo(-5, 18);
      ctx.stroke();

      // Cockpit visor + strobing wingtip navigation beacons
      ctx.fillStyle = "#fef08a";
      ctx.beginPath();
      ctx.ellipse(15, 0, 5.5, 2.4, 0, 0, Math.PI * 2);
      ctx.fill();
      if (Math.sin(ts * 0.025) > 0) {
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(-6, -26, 2.2, 0, Math.PI * 2);
        ctx.arc(-6, 26, 2.2, 0, Math.PI * 2);
        ctx.fill();
      }
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
    const isCarrierClass = c.ent.ship_class === "CARRIER";

    ctx.save();
    ctx.translate(x, y);

    // Slowly rotating tactical faction shield ring
    ctx.save();
    ctx.rotate((t * 0.00035) * (isLong ? 1 : -1));
    ctx.beginPath();
    ctx.arc(0, 0, s * 1.18, 0, Math.PI * 2);
    ctx.strokeStyle = hovered ? "#ffffff" : haloCol;
    ctx.globalAlpha = hovered ? 0.92 : 0.42;
    ctx.lineWidth = hovered ? 1.9 : 1.15;
    ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
    ctx.globalAlpha = 1;

    // Armored Orbital Dreadnought / Helicarrier hull facing the frontline
    ctx.save();
    if (!isLong) ctx.scale(-1, 1);

    // Dual aft ion thrusters
    const pulse = 0.7 + Math.sin(t * 0.012 + c.phase) * 0.3;
    [-s * 0.28, s * 0.28].forEach((ey) => {
      const tg = ctx.createLinearGradient(-s * (1.0 + pulse * 0.5), ey, -s * 0.72, ey);
      tg.addColorStop(0, "transparent");
      tg.addColorStop(1, isLong ? "#38bdf8" : "#f59e0b");
      ctx.fillStyle = tg;
      ctx.beginPath();
      ctx.moveTo(-s * 0.85, ey - s * 0.11);
      ctx.lineTo(-s * (1.12 + pulse * 0.38), ey);
      ctx.lineTo(-s * 0.85, ey + s * 0.11);
      ctx.closePath();
      ctx.fill();
    });

    ctx.fillStyle = isLong ? "rgba(16, 185, 129, 0.24)" : "rgba(244, 63, 94, 0.24)";
    ctx.strokeStyle = baseCol;
    ctx.lineWidth = isCarrierClass ? 1.7 : 1.3;
    ctx.shadowColor = baseCol;
    ctx.shadowBlur = hovered ? 20 : 11;
    ctx.beginPath();
    ctx.moveTo(s * 1.22, 0);
    ctx.lineTo(s * 0.52, -s * 0.42);
    ctx.lineTo(-s * 0.12, -s * 0.56);
    ctx.lineTo(-s * 0.88, -s * 0.66);
    ctx.lineTo(-s * 1.08, -s * 0.25);
    ctx.lineTo(-s * 0.78, 0);
    ctx.lineTo(-s * 1.08, s * 0.25);
    ctx.lineTo(-s * 0.88, s * 0.66);
    ctx.lineTo(-s * 0.12, s * 0.56);
    ctx.lineTo(s * 0.52, s * 0.42);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Flight-deck runway centerline & armored rib markings
    ctx.strokeStyle = "rgba(255,255,255,0.34)";
    ctx.lineWidth = 0.9;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(s * 0.9, 0);
    ctx.lineTo(-s * 0.6, 0);
    ctx.stroke();
    ctx.setLineDash([]);

    // Command bridge reactor core light
    ctx.fillStyle = haloCol;
    ctx.beginPath();
    ctx.arc(s * 0.18, 0, Math.max(2.6, s * 0.15), 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Real-time distance-to-liquidation HP bar (STRICT: only for verified single-venue liq_px!)
    const distPct = liveWhaleDistPct(c.pos);
    const isRedHp = distPct !== null && distPct < 5.0;
    const isWarnHp = distPct !== null && distPct >= 5.0 && distPct < 12.0;

    // Pulsing danger lock-on reticle & subtle smoke wisps for Red-HP (<5% from liquidation) real whales!
    if (isRedHp) {
      const pulse = 0.55 + Math.sin(t * 0.012 + c.phase) * 0.45;
      const b = s * 1.42;
      ctx.save();
      ctx.strokeStyle = "#f59e0b";
      ctx.globalAlpha = pulse;
      ctx.lineWidth = 1.6;
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
      // Rising subtle smoke circles
      for (let sm = 0; sm < 2; sm++) {
        const sp = ((t * 0.0011 + c.phase + sm * 0.5) % 1);
        ctx.fillStyle = "rgba(148, 163, 184, 0.35)";
        ctx.globalAlpha = (1 - sp) * 0.45;
        ctx.beginPath();
        ctx.arc((sm === 0 ? -4 : 5) + sp * 6, -s * 0.4 - sp * 18, 2.5 + sp * 4.5, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

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

    // Compact tactical nameplate + real HP bar under flagship (clamped safely inside canvas width on mobile)
    const narrow = war.cssW < 540;
    const tag = `[${tierCode}] ${c.ent.display.slice(0, narrow ? 10 : 12)}`;
    const amtTxt = isRedHp
      ? `红血 距强平 ${Math.max(0, distPct).toFixed(1)}%`
      : isWarnHp
        ? `${isLong ? "+" : "-"}${fmtCompact(c.pos.usd)} · 距强平 ${distPct.toFixed(1)}%`
        : `${isLong ? "+" : "-"}${fmtCompact(c.pos.usd)}`;
    ctx.font = `600 ${narrow ? 9 : 10}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    ctx.textAlign = "center";
    const halfW = Math.max(ctx.measureText(tag).width, ctx.measureText(amtTxt).width) / 2 + 6;
    const labelOffsetX = Math.max(halfW - x, Math.min(war.cssW - halfW - x, 0));

    let labelTopY = s + (narrow ? 11 : 13);
    if (distPct !== null) {
      const barW = Math.max(36, Math.min(62, s * 2.1));
      const barH = 4;
      const barX = labelOffsetX - barW / 2;
      const barY = s + 4;
      const ratio = Math.max(0.06, Math.min(1, Math.max(0, distPct) / 20));
      ctx.fillStyle = "rgba(15, 23, 42, 0.85)";
      ctx.fillRect(barX, barY, barW, barH);
      ctx.fillStyle = isRedHp ? "#f43f5e" : isWarnHp ? "#f59e0b" : "#10b981";
      ctx.fillRect(barX, barY, barW * ratio, barH);
      labelTopY = barY + barH + (narrow ? 9 : 11);
    }

    ctx.fillStyle = isRedHp ? "#fbbf24" : haloCol;
    ctx.fillText(tag, labelOffsetX, labelTopY);
    ctx.fillStyle = isRedHp ? "#fda4af" : "rgba(226, 232, 240, 0.85)";
    ctx.fillText(amtTxt, labelOffsetX, labelTopY + (narrow ? 10 : 11));

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
        verdict.textContent = "多头占优";
        verdict.className = "pill pill-ok";
      } else if (clampedLong <= 42) {
        verdict.textContent = "空头主导";
        verdict.className = "pill pill-risk";
      } else {
        verdict.textContent = "多空均衡";
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
    if (nowMs - war.lastMagRenderTs > 1200) {
      renderLiqMagnet();
      war.lastMagRenderTs = nowMs;
    }

    // Spring-damper tug-of-war frontline physics: individual strikes physically push & recoil the battle line!
    const springForce = (war.targetFrontline - war.frontline) * 8.5;
    war.frontlineVel += springForce * dt;
    war.frontlineVel *= Math.exp(-4.4 * dt);
    war.frontline = Math.max(0.14, Math.min(0.86, war.frontline + war.frontlineVel * dt));
    war.clashPower = Math.max(0, war.clashPower - dt * 2.1);

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

    // Dynamic Flexing Tug-of-War Plasma Energy Barrier (bows physically on heavy strikes!)
    const bowPx = (war.clashSide === "LONG" ? 1 : -1) * Math.min(34, war.clashPower * 24 + Math.abs(war.frontlineVel) * 95);
    const cy = Math.max(36, Math.min(H - 36, war.clashY || H * 0.5));
    const surgeCol = war.frontlineVel > 0.015 ? "#10b981" : war.frontlineVel < -0.015 ? "#f43f5e" : "#38bdf8";

    ctx.save();
    // Outer plasma glow wall
    const wallGrad = ctx.createLinearGradient(fx, 0, fx, H);
    wallGrad.addColorStop(0, "rgba(56, 189, 248, 0.04)");
    wallGrad.addColorStop(0.5, surgeCol);
    wallGrad.addColorStop(1, "rgba(56, 189, 248, 0.04)");
    ctx.strokeStyle = wallGrad;
    ctx.lineWidth = 2.4 + war.clashPower * 2.2;
    ctx.shadowColor = surgeCol;
    ctx.shadowBlur = 14 + war.clashPower * 16;
    ctx.beginPath();
    ctx.moveTo(fx, 0);
    ctx.quadraticCurveTo(fx + bowPx, cy, fx, H);
    ctx.stroke();

    // Inner bright core beam
    ctx.shadowBlur = 0;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.55)";
    ctx.lineWidth = 1.0;
    ctx.setLineDash([7, 5]);
    ctx.beginPath();
    ctx.moveTo(fx, 0);
    ctx.quadraticCurveTo(fx + bowPx * 0.85, cy, fx, H);
    ctx.stroke();
    ctx.setLineDash([]);

    // Tug-of-war momentum chevrons along the frontline when actively pushing!
    if (Math.abs(war.frontlineVel) > 0.012 || war.clashPower > 0.25) {
      const pushingRight = war.frontlineVel >= 0;
      const pushCol = pushingRight ? "#10b981" : "#f43f5e";
      const label = pushingRight ? "买盘推进 ▶▶" : "◀◀ 卖盘压制";
      ctx.font = "700 10px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillStyle = pushCol;
      ctx.textAlign = "center";
      ctx.fillText(label, Math.max(52, Math.min(W - 52, fx + bowPx * 0.5)), Math.max(36, cy - 16));
    }
    ctx.restore();

    // Zone Watermarks (compact on narrow mobile canvas so left/right NEVER collide!)
    ctx.font = "700 10.5px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillStyle = "rgba(16, 185, 129, 0.58)";
    ctx.textAlign = "left";
    ctx.fillText(W < 520 ? "◀ 多头主买" : "◀ LONG FLOW · 多头主动买盘与持仓大户", 12, 20);
    ctx.fillStyle = "rgba(244, 63, 94, 0.62)";
    ctx.textAlign = "right";
    ctx.fillText(W < 520 ? "空头主卖 ▶" : "空头主动卖盘与持仓大户 · SHORT FLOW ▶", W - 12, 20);

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

      drawAircraft(ctx, p.x, p.y, p.side === "LONG" ? 1 : -1, p.tier, col, ts);

      // Callout label on heavy $250K+ jets
      if (p.tier >= 3) {
        ctx.font = "700 10px ui-monospace, SFMono-Regular, Menlo, monospace";
        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.fillText(fmtCompact(p.usd), p.x, p.y - (p.tier === 4 ? 32 : 21));
      }

      if (crossed) {
        // Real directional tug-of-war knockback impulse on frontline!
        const dirSign = p.side === "LONG" ? 1 : -1;
        const pushMag = p.tier === 1 ? 0.032 : p.tier === 2 ? 0.115 : p.tier === 3 ? 0.26 : 0.58;
        war.frontlineVel = Math.max(-0.9, Math.min(0.9, war.frontlineVel + dirSign * pushMag));
        war.clashY = p.y;
        war.clashSide = p.side;
        war.clashPower = Math.min(1.6, war.clashPower + (p.tier === 1 ? 0.12 : p.tier === 2 ? 0.42 : p.tier === 3 ? 0.85 : 1.4));

        // Impact sparks & shockwave at battle frontline!
        const sparkCnt = p.tier === 1 ? 5 : p.tier === 2 ? 11 : p.tier === 3 ? 18 : 30;
        for (let k = 0; k < sparkCnt; k++) {
          const ang = Math.random() * Math.PI * 2;
          const spd = 45 + Math.random() * (p.tier * 62);
          war.sparks.push({
            x: fx,
            y: p.y,
            vx: Math.cos(ang) * spd,
            vy: Math.sin(ang) * spd,
            life: 1.0,
            color: k % 3 === 0 ? "#ffffff" : col,
            size: p.tier >= 3 ? 2.4 : 1.5,
          });
        }
        if (p.tier >= 2) {
          war.rings.push({
            x: fx,
            y: p.y,
            r: 5,
            maxR: p.tier === 2 ? 28 : p.tier === 3 ? 54 : 88,
            color: col,
            alpha: 0.9,
          });
          if (war.floatingHits.length < 18) {
            war.floatingHits.push({
              x: fx + dirSign * 14,
              y: p.y - 6,
              vy: -32 - p.tier * 6,
              text: `${p.side === "LONG" ? "+" : "-"}${fmtCompact(p.usd)}`,
              color: col,
              big: p.tier >= 3,
              alpha: 1.0,
            });
          }
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

    // 5b. Rising Floating Combat Impact Numbers
    const nextHits = [];
    for (const h of war.floatingHits) {
      h.y += h.vy * dt;
      h.alpha -= dt * 1.15;
      if (h.alpha > 0.04) {
        ctx.save();
        ctx.globalAlpha = h.alpha;
        ctx.font = `${h.big ? "800 12px" : "700 10.5px"} ui-monospace, SFMono-Regular, Menlo, monospace`;
        ctx.fillStyle = h.color;
        ctx.textAlign = "center";
        ctx.fillText(h.text, Math.max(34, Math.min(W - 34, h.x)), h.y);
        ctx.restore();
        nextHits.push(h);
      }
    }
    war.floatingHits = nextHits;

    // 5c. Live Flying Danmaku in Upper & Lower Sky Lanes (center combat zone stays 100% clear)
    updateAndDrawDanmaku(ctx, W, H, dt);

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
        const levSafe = Number.isFinite(Number(pos.lev)) && Number(pos.lev) > 0 ? ` · ${Number(pos.lev)}x` : "";
        const dPct = liveWhaleDistPct(pos);
        const liqLine = pos.liq_px
          ? ` · 强平 ${fmtPxShort(pos.liq_px)}${dPct !== null ? ` (距强平 ${Math.max(0, dPct).toFixed(1)}%)` : ""}`
          : "";
        tip.innerHTML = `
          <div><b>${esc(ent.display)}</b>${ent.x_handle && /^[A-Za-z0-9_]{2,15}$/.test(ent.x_handle) ? ` <span style="color:#38bdf8;">𝕏 @${esc(ent.x_handle)}</span>` : ""} <span class="muted">(${esc(ent.tier_label)})</span></div>
          <div class="${pos.side === "LONG" ? "ok" : "risk"}">${esc(pos.coin)} ${pos.side === "LONG" ? "做多" : "做空"} <b>${fmtCompact(pos.usd)}</b>${esc(levSafe)}</div>
          ${pos.entry_px || pos.liq_px ? `<div class="muted small mono">${pos.entry_px ? `开仓 ${fmtPxShort(pos.entry_px)}` : ""}${esc(liqLine)}</div>` : ""}
          ${pos.upnl != null ? `<div class="${pos.upnl >= 0 ? "ok" : "risk"}">当前浮盈亏: <b>${fmtSignedCompact(pos.upnl)}</b></div>` : ""}
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

  /* ---------- 1-Click Shareable 1200x675 Tactical Liquidation & Whale Battle Poster PNG ---------- */
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
    ctx.font = "700 17px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText("DANCLUB TERMINAL · LIQUIDATION RADAR & WHALE WAR ROOM", 54, 54);
    ctx.fillStyle = "#eef2f6";
    ctx.font = "700 36px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
    ctx.fillText(`${coin} 爆仓雷达与巨鲸阵营战报`, 54, 104);
    ctx.fillStyle = "#7c899e";
    ctx.font = "500 16px ui-monospace, SFMono-Regular, Menlo, monospace";
    const whaleTs = strictNum(war.data?.updated_at);
    ctx.fillText(
      `巨鲸快照 ${whaleTs !== null && whaleTs > 0 ? `${fmtTimeShortBjt(whaleTs)} BJT` : "--"} · 实体去重真持仓`,
      54,
      136
    );

    // Divergence Pill
    const sp = strictNum(div.spread_pct);
    ctx.fillStyle = sp !== null && sp >= 0 ? "rgba(16,185,129,0.14)" : "rgba(244,63,94,0.14)";
    ctx.strokeStyle = sp !== null && sp >= 0 ? "#10b981" : "#f43f5e";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(54, 156, 1092, 56, 10);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#eef2f6";
    ctx.font = "700 22px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
    ctx.fillText(
      `聪明钱 − 反指 多头占比差：${sp !== null ? `${sp > 0 ? "+" : ""}${sp.toFixed(1)}pp` : "--"}   |   ${div.label || "实盘监控中"}`,
      78,
      192,
      1044
    );

    // Two Faction Cards
    const drawFactionCard = (x, title, accent, obj) => {
      ctx.fillStyle = "#101520";
      ctx.strokeStyle = "rgba(148,163,184,0.22)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.roundRect(x, 230, 534, 232, 12);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = accent;
      ctx.font = "700 20px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
      ctx.fillText(title, x + 26, 268);

      const lpVal = strictNum(obj.notional_long_pct);
      const lpValid = lpVal !== null && lpVal >= 0 && lpVal <= 100;
      ctx.fillStyle = "#eef2f6";
      ctx.font = "700 42px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillText(lpValid ? `${lpVal.toFixed(1)}% 多` : "--", x + 26, 326);
      ctx.fillStyle = "#7c899e";
      ctx.font = "600 18px ui-monospace, SFMono-Regular, Menlo, monospace";
      const longEnt = strictNum(obj.long_entities);
      const shortEnt = strictNum(obj.short_entities);
      ctx.fillText(
        `实体 ${longEnt !== null && longEnt >= 0 ? Math.round(longEnt) : "--"} 多 vs ${shortEnt !== null && shortEnt >= 0 ? Math.round(shortEnt) : "--"} 空`,
        x + 26,
        358
      );

      // Bar (hide ratio bar completely when percentage is missing or out-of-range; allow true 0% & 100%)
      if (lpValid) {
        ctx.fillStyle = "#f43f5e";
        ctx.beginPath();
        ctx.roundRect(x + 26, 374, 482, 14, 4);
        ctx.fill();
        if (lpVal > 0) {
          ctx.fillStyle = "#10b981";
          ctx.beginPath();
          ctx.roundRect(x + 26, 374, (482 * lpVal) / 100, 14, 4);
          ctx.fill();
        }
      }

      ctx.fillStyle = "#c5cedb";
      ctx.font = "600 17px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillText(`多头 ${fmtCompact(obj.long_usd)}   |   空头 ${fmtCompact(obj.short_usd)}`, x + 26, 418, 482);
      const netVal = strictNum(obj.net_usd);
      ctx.fillStyle = netVal === null ? "#7c899e" : netVal >= 0 ? "#10b981" : "#f43f5e";
      ctx.fillText(`净敞口 ${fmtSignedCompact(obj.net_usd)}`, x + 26, 446, 482);
    };

    drawFactionCard(54, "S/A 级聪明钱阵营", "#f59e0b", sa);
    drawFactionCard(612, "C 级反指巨鲸阵营", "#a855f7", cc);

    // Bottom Strip — Left: CoinGlass 24h Liquidation Scoreboard
    const liqObj = war.data?.liquidations && typeof war.data.liquidations === "object" ? war.data.liquidations : {};
    const liq24Map = liqObj.windows?.["24h"] && typeof liqObj.windows["24h"] === "object" ? liqObj.windows["24h"] : {};
    const coinLiq = liq24Map[coin] && typeof liq24Map[coin] === "object" ? liq24Map[coin] : null;
    const allLiq = liq24Map.ALL && typeof liq24Map.ALL === "object" ? liq24Map.ALL : null;
    const coinTot = strictNum(coinLiq?.total_usd);
    const allTot = strictNum(allLiq?.total_usd);
    // Preserve valid coin-level zero instead of silently substituting ALL; only fall back to ALL when coin bucket is missing
    const hasCoinBucket = coinLiq !== null && coinTot !== null && coinTot >= 0;
    const hasAllBucket = allLiq !== null && allTot !== null && allTot >= 0;
    const activeLiq = hasCoinBucket ? coinLiq : hasAllBucket ? allLiq : null;
    const liqScopeLabel = hasCoinBucket ? coin : "全网";

    ctx.fillStyle = "#101520";
    ctx.strokeStyle = "rgba(148,163,184,0.22)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(54, 480, 534, 122, 12);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#f59e0b";
    ctx.font = "700 16px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
    const exCnt = strictNum(liqObj.exchanges_count);
    const liqTs = strictNum(liqObj.updated_at);
    const liqStaleTag =
      whaleTs !== null && whaleTs > 0 && liqTs !== null && liqTs > 0 && whaleTs - liqTs > 1800 ? " · 旧快照" : "";
    const liqTsTag = liqTs !== null && liqTs > 0 ? ` · ${fmtTimeShortBjt(liqTs)} BJT` : "";
    ctx.fillText(
      `CoinGlass 24h 全网爆仓 · ${liqScopeLabel}${exCnt !== null && exCnt > 0 ? ` (${Math.round(exCnt)}所)` : ""}${liqTsTag}${liqStaleTag}`,
      80,
      510,
      482
    );
    if (liqObj.available !== true) {
      ctx.fillStyle = "#7c899e";
      ctx.font = "600 16px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
      ctx.fillText("CoinGlass 全网爆仓数据源暂不可用", 80, 556, 482);
    } else if (!activeLiq) {
      ctx.fillStyle = "#7c899e";
      ctx.font = "600 16px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
      ctx.fillText("24h 爆仓窗口数据缺失", 80, 556, 482);
    } else {
      const lUsd = strictNum(activeLiq.long_usd);
      const sUsd = strictNum(activeLiq.short_usd);
      const tUsd = strictNum(activeLiq.total_usd);
      const liqCnt = strictNum(activeLiq.count);
      const lValid = lUsd !== null && lUsd >= 0;
      const sValid = sUsd !== null && sUsd >= 0;

      ctx.font = "700 19px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillStyle = "#10b981";
      ctx.fillText(`爆多 ${lValid ? fmtCompact(lUsd) : "--"}`, 80, 544, 235);
      ctx.fillStyle = "#f43f5e";
      ctx.textAlign = "right";
      ctx.fillText(`爆空 ${sValid ? fmtCompact(sUsd) : "--"}`, 562, 544, 235);
      ctx.textAlign = "left";

      // Only paint split ratio when both sides are known and sum > 0; allow true 0% and 100%
      if (lValid && sValid && lUsd + sUsd > 0) {
        const lPct = (lUsd / (lUsd + sUsd)) * 100;
        ctx.fillStyle = "#f43f5e";
        ctx.beginPath();
        ctx.roundRect(80, 558, 482, 10, 4);
        ctx.fill();
        if (lPct > 0) {
          ctx.fillStyle = "#10b981";
          ctx.beginPath();
          ctx.roundRect(80, 558, (482 * lPct) / 100, 10, 4);
          ctx.fill();
        }
      }

      ctx.fillStyle = "#94a3b8";
      ctx.font = "600 14px ui-monospace, SFMono-Regular, Menlo, monospace";
      const totStr = tUsd !== null && tUsd >= 0 ? fmtCompact(tUsd) : "--";
      const cntStr = liqCnt !== null && liqCnt >= 0 ? Math.round(liqCnt).toLocaleString() : "--";
      ctx.fillText(`24h 合计 ${totStr}  ·  ${cntStr} 笔实盘强平`, 80, 588, 482);
    }

    // Bottom Strip — Right: Verified Single-Venue Flagship Liquidation Walls
    ctx.fillStyle = "#101520";
    ctx.strokeStyle = "rgba(148,163,184,0.22)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(612, 480, 534, 122, 12);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#38bdf8";
    ctx.font = "700 17px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
    ctx.fillText(`${coin} 实盘大户强平分布（单所精确强平价）`, 638, 510, 482);

    const mag = war.data?.liq_magnets?.[coin];
    const lw = mag && typeof mag === "object" && mag.long_wall && typeof mag.long_wall === "object" ? mag.long_wall : null;
    const sw = mag && typeof mag === "object" && mag.short_wall && typeof mag.short_wall === "object" ? mag.short_wall : null;
    const lNearPx = strictNum(lw?.nearest_px);
    const lNearDist = strictNum(lw?.nearest_dist_pct);
    const l25Usd = strictNum(lw?.within_25pct_usd);
    const sNearPx = strictNum(sw?.nearest_px);
    const sNearDist = strictNum(sw?.nearest_dist_pct);
    const s25Usd = strictNum(sw?.within_25pct_usd);

    ctx.font = "600 15px ui-monospace, SFMono-Regular, Menlo, monospace";
    const hasLongWallHit = (lNearPx !== null && lNearPx > 0) || (l25Usd !== null && l25Usd > 0);
    if (!lw) {
      ctx.fillStyle = "#7c899e";
      ctx.fillText("⬇ 多头墙：暂无监测数据", 638, 548, 482);
    } else if (hasLongWallHit) {
      ctx.fillStyle = "#10b981";
      const pxStr = lNearPx !== null && lNearPx > 0 ? fmtPxShort(lNearPx) : "--";
      const distStr = lNearDist !== null && lNearDist >= 0 ? `-${lNearDist.toFixed(1)}%` : "--";
      const usdStr = l25Usd !== null && l25Usd >= 0 ? fmtCompact(l25Usd) : "--";
      ctx.fillText(`⬇ 多头墙：${pxStr} (${distStr}) · 25%内 ${usdStr}`, 638, 548, 482);
    } else {
      ctx.fillStyle = "#10b981";
      ctx.fillText("⬇ 多头墙：已覆盖，±25%内无单所旗舰强平位", 638, 548, 482);
    }

    const hasShortWallHit = (sNearPx !== null && sNearPx > 0) || (s25Usd !== null && s25Usd > 0);
    if (!sw) {
      ctx.fillStyle = "#7c899e";
      ctx.fillText("⬆ 空头墙：暂无监测数据", 638, 582, 482);
    } else if (hasShortWallHit) {
      ctx.fillStyle = "#f43f5e";
      const pxStr = sNearPx !== null && sNearPx > 0 ? fmtPxShort(sNearPx) : "--";
      const distStr = sNearDist !== null && sNearDist >= 0 ? `+${sNearDist.toFixed(1)}%` : "--";
      const usdStr = s25Usd !== null && s25Usd >= 0 ? fmtCompact(s25Usd) : "--";
      ctx.fillText(`⬆ 空头墙：${pxStr} (${distStr}) · 25%内 ${usdStr}`, 638, 582, 482);
    } else {
      ctx.fillStyle = "#f43f5e";
      ctx.fillText("⬆ 空头墙：已覆盖，±25%内无单所旗舰强平位", 638, 582, 482);
    }

    // Footer Watermark
    ctx.fillStyle = "#7c899e";
    ctx.font = "600 15px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText("danclub.duckdns.org  ·  100% 开源免费多市场情报终端  ·  仅供客观数据研究，不构成投资建议", 54, 642);

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
        `<span class="regime-chip" title="持续追踪的链上高净值地址总数">追踪地址库 <b>${Number(st.tracked_addresses || 0).toLocaleString()}</b></span>`,
        `<span class="regime-chip" title="已完成历史实盘盈亏建档的地址数">实盘建档 <b>${Number(st.profiled_whales || 0).toLocaleString()}</b></span>`,
        `<span class="regime-chip" title="同一主体多地址去重后当前持仓的大户实体数">活跃持仓大户 <b>${Number(st.active_entities || 0)}</b></span>`,
        `<span class="regime-chip">在场总名义仓位 <b>${fmtCompact(st.total_active_notional_usd)}</b></span>`,
        st.x_linked_count ? `<span class="regime-chip">公开 𝕏 账号 <b>${Number(st.x_linked_count)}</b></span>` : "",
        st.profile_coverage_pct ? `<span class="regime-chip" title="在役实盘大户中已完成历史盈亏建档的实体比例">建档覆盖率 <b>${Number(st.profile_coverage_pct).toFixed(0)}%</b></span>` : "",
      ].filter(Boolean).join("");
    }

    // Faction Confrontation Panel (follows activeCohortCoin: ALL / BTC / ETH / HYPE / SOL)
    const cKey = war.activeCohortCoin;
    const coh = w.cohorts?.[cKey] || {};
    const sa = coh.SMART_SA || {};
    const cc = coh.COUNTER_C || {};
    const div = coh.divergence || {};

    const divBox = $("war-div-banner");
    if (divBox) {
      const sp = strictNum(div.spread_pct);
      const cls = div.tone === "danger" ? "pill-risk" : div.tone === "bullish" ? "pill-ok" : div.tone === "warn" ? "pill-warn" : "pill-muted";
      divBox.innerHTML = `
        <span class="pill ${cls}" title="S/A盈利大户多头名义占比减去C级反指多头名义占比（单位：百分点 pp；各组多空方向需结合各自占比相对 50% 判断）">聪明钱 − 反指 多头占比差: ${sp !== null ? `${sp > 0 ? "+" : ""}${sp.toFixed(1)}pp` : "--"}</span>
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
      if (war.filterXOnly && !f.x_handle) return false;
      if (q) {
        const coinDisp = fmtCoinLabel(f.primary_coin).text;
        const posList = Array.isArray(f.positions) ? f.positions : [];
        const allPosDisp = posList
          .filter((p) => p && typeof p.coin === "string")
          .map((p) => `${p.coin} ${fmtCoinLabel(p.coin).text}`)
          .join(" ");
        if (!`${f.display || ""} ${f.x_handle || ""} ${f.short_addr || ""} ${f.primary_coin || ""} ${coinDisp} ${allPosDisp} ${f.tier_label || ""}`.toLowerCase().includes(q)) return false;
      }
      return true;
    });

    const safeXUrl = (f) =>
      f.x_handle && /^[A-Za-z0-9_]{2,15}$/.test(f.x_handle)
        ? `https://x.com/${f.x_handle}`
        : "";

    const shipBadge = (sc) =>
      sc === "CARRIER"
        ? `<span class="ship-tag ship-carrier" title="特大户：总名义仓位 ≥$50M">≥50M</span>`
        : sc === "CRUISER"
          ? `<span class="ship-tag ship-cruiser" title="大户：总名义仓位 $10M–$50M">≥10M</span>`
          : `<span class="ship-tag ship-destroyer" title="中大户：总名义仓位 $1M–$10M">≥1M</span>`;

    const tierTipMap = {
      S: "S级：历史实盘持续高额盈利，核心聪明钱样本",
      A: "A级：历史实盘盈利稳健，聪明钱观察样本",
      C: "C级：历史实盘持续亏损，常作反向拥挤度参考",
      F: "F级：高关注度公开大户，独立记录不混入S/C均值",
    };

    const tierPill = (f) => {
      const cls =
        f.tier_code === "S" || f.tier_code === "A"
          ? "pill-warn"
          : f.tier_code === "C"
            ? "pill-violet"
            : "pill-muted";
      const tip = tierTipMap[f.tier_code] || "链上实盘大户分层";
      return `<span class="pill ${cls}" title="${esc(tip)}">${esc(f.tier_label)}</span>`;
    };

    const freshEl = $("war-fleet-freshness");
    if (freshEl && war.data?.updated_at) {
      freshEl.textContent = `链上快照 ${fmtTimeShortBjt(war.data.updated_at)} BJT · 每 15 分钟实盘核验 · 已平仓大户自动剔除`;
    }

    tbody.innerHTML = filtered.length
      ? filtered
          .map((f) => {
            const sideCls = f.primary_side === "LONG" ? "ok" : "risk";
            const actCls = f.action === "反手" ? "pill-risk" : f.action === "加仓" ? "pill-info" : f.action === "减仓" ? "pill-warn" : "pill-muted";
            const pnl = f.alltime_pnl;
            const xUrl = safeXUrl(f);
            const pCoin = fmtCoinLabel(f.primary_coin);
            return `
          <tr class="fleet-row" data-eid="${esc(f.id)}" style="cursor:pointer;">
            <td>
              <div class="fleet-entity">
                ${shipBadge(f.ship_class)}
                <div>
                  <div class="mono fleet-name-row">
                    <b>${esc(f.display)}</b>
                    ${xUrl ? `<a class="whale-x-pill" href="${esc(xUrl)}" target="_blank" rel="noopener noreferrer" title="访问公开 X 主页 @${esc(f.x_handle)}">𝕏 @${esc(f.x_handle)}</a>` : ""}
                    ${f.addr_count > 1 ? `<span class="sess-tag">${f.addr_count}址归并</span>` : ""}
                  </div>
                  ${f.display !== f.short_addr ? `<div class="muted small mono">${esc(f.short_addr)}</div>` : ""}
                </div>
              </div>
            </td>
            <td>${tierPill(f)}</td>
            <td class="mono" title="${esc(pCoin.title)}">
              <span class="${sideCls}"><b>${esc(pCoin.text)} ${f.primary_side === "LONG" ? "多" : "空"}</b></span>
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
      : `<tr><td colspan="6" class="muted">暂无匹配筛选条件的活跃实盘大户</td></tr>`;

    tbody.querySelectorAll(".whale-x-pill").forEach((a) => {
      a.addEventListener("click", (ev) => ev.stopPropagation());
    });
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
    const mpnl = ent.month_pnl;
    const sizeTierLbl = ent.ship_class === "CARRIER" ? "特大户 ≥$50M" : ent.ship_class === "CRUISER" ? "大户 ≥$10M" : "中大户 ≥$1M";
    $("wd-title").textContent = `${ent.display} · ${sizeTierLbl}`;
    const xBadge = $("wd-x-badge");
    if (xBadge) {
      const validHandle = ent.x_handle && /^[A-Za-z0-9_]{2,15}$/.test(ent.x_handle) ? ent.x_handle : "";
      xBadge.innerHTML = validHandle
        ? `<a class="whale-x-pill whale-x-pill-lg" href="https://x.com/${esc(validHandle)}" target="_blank" rel="noopener noreferrer">𝕏 @${esc(validHandle)} ↗</a>`
        : "";
    }
    $("wd-sub").textContent = `${ent.short_addr} · ${ent.addr_count} 个关联地址去重合并 · ${ent.tier_label} · 链上快照 ${fmtTimeShortBjt(war.data?.updated_at)} BJT`;

    $("wd-kpis").innerHTML = [
      { k: "总名义仓位", v: fmtCompact(ent.total_notional_usd), cls: "" },
      { k: "全仓净敞口", v: fmtSignedCompact(ent.net_usd), cls: ent.net_usd >= 0 ? "ok" : "risk" },
      { k: "历史实盘 PnL", v: pnl != null ? fmtSignedCompact(pnl) : "--", cls: pnl != null ? (pnl >= 0 ? "ok" : "risk") : "" },
      { k: "近 30 日 PnL", v: mpnl != null ? fmtSignedCompact(mpnl) : "--", cls: mpnl != null ? (mpnl >= 0 ? "ok" : "risk") : "" },
      { k: "Hyperliquid 权益", v: ent.account_value ? fmtCompact(ent.account_value) : "--", cls: "" },
      { k: "近 7 日 PnL", v: ent.week_pnl != null ? fmtSignedCompact(ent.week_pnl) : "--", cls: ent.week_pnl != null ? (ent.week_pnl >= 0 ? "ok" : "risk") : "" },
    ]
      .map((x) => `<div class="stat-tile"><small>${esc(x.k)}</small><strong class="${x.cls}">${esc(x.v)}</strong></div>`)
      .join("");

    const fmtPx = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n) || n <= 0) return "--";
      return `$${n.toLocaleString("en-US", { maximumFractionDigits: n < 10 ? 4 : 2 })}`;
    };

    const maxPos = Math.max(1, ...(ent.positions || []).map((p) => p.usd));
    $("wd-positions").innerHTML = (ent.positions || [])
      .map((p) => {
        const w = Math.max(4, (p.usd / maxPos) * 100);
        const col = p.side === "LONG" ? "var(--ok)" : "var(--risk)";
        const levSafeNum = Number.isFinite(Number(p.lev)) && Number(p.lev) > 0 ? Number(p.lev) : null;
        const levTxt = levSafeNum
          ? `${levSafeNum}x${p.lev_type === "isolated" ? "逐仓" : p.lev_type === "cross" ? "全仓" : p.lev_type === "mixed" ? "混合" : ""}`
          : "";
        const venueTag = Array.isArray(p.venues) && p.venues.length > 1 ? `跨所(${p.venues.join("+")})` : "";
        const liqLabel = p.legs && p.legs > 1 ? "最近强平" : "强平";
        const upnlHtml = p.upnl != null ? `<span class="${p.upnl >= 0 ? "ok" : "risk"}">浮${p.upnl >= 0 ? "盈" : "亏"} ${fmtSignedCompact(p.upnl)}</span>` : "";
        const liqDist =
          p.liq_px && p.mark_px && p.mark_px > 0
            ? ` (${(((p.liq_px - p.mark_px) / p.mark_px) * 100).toFixed(1)}%)`
            : "";
        const pCoin = fmtCoinLabel(p.coin);
        return `
        <div class="wd-pos-row">
          <div class="wd-pos-top mono">
            <span title="${esc(pCoin.title)}">
              <b>${esc(pCoin.text)}</b>
              <span class="${p.side === "LONG" ? "ok" : "risk"}">${p.side === "LONG" ? "多" : "空"}</span>
              ${levTxt ? `<span class="sess-tag">${esc(levTxt)}</span>` : ""}
              ${venueTag ? `<span class="sess-tag">${esc(venueTag)}</span>` : ""}
            </span>
            <span><b>${fmtCompact(p.usd)}</b></span>
          </div>
          <div class="wd-pos-bar"><i style="width:${w.toFixed(0)}%;background:${col};"></i></div>
          ${
            p.entry_px || p.liq_px || p.upnl != null
              ? `<div class="wd-pos-meta mono">
                  <span>${p.entry_px ? `开仓 ${fmtPx(p.entry_px)}` : ""}${p.liq_px ? `${p.entry_px ? " · " : ""}${liqLabel} ${fmtPx(p.liq_px)}${liqDist}` : ""}</span>
                  ${upnlHtml}
                </div>`
              : ""
          }
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
              name: "全仓净敞口 ($M)",
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

  /* ---------- Live Sky-Lane Danmaku Engine & Frontline Message Wall ---------- */
  function syncDanmakuToggleUi() {
    const btn = $("btn-war-danmaku");
    if (!btn) return;
    btn.textContent = war.danmakuOn ? "弹幕 开" : "弹幕 关";
    btn.classList.toggle("active", war.danmakuOn);
    btn.title = war.danmakuOn ? "点击隐藏战场飞屏弹幕" : "点击开启战场飞屏弹幕";
  }

  function showBarrageFeedback(msg, kind = "ok", ttlMs = 3600) {
    const el = $("dm-feedback");
    if (!el) return;
    if (barrage.feedbackTimer) clearTimeout(barrage.feedbackTimer);
    if (!msg) {
      el.textContent = "";
      el.className = "dm-feedback small";
      return;
    }
    el.textContent = msg;
    el.className = `dm-feedback small is-${kind}`;
    if (ttlMs > 0) {
      barrage.feedbackTimer = setTimeout(() => {
        el.textContent = "";
        el.className = "dm-feedback small";
      }, ttlMs);
    }
  }

  function fmtBarrageTime(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return "刚刚";
    const diff = Math.floor(Date.now() / 1000) - n;
    if (diff < 45) return "刚刚";
    if (diff < 3600) return `${Math.max(1, Math.floor(diff / 60))}分钟前`;
    const d = new Date(n * 1000);
    const now = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) {
      return `${hh}:${mi}`;
    }
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${hh}:${mi}`;
  }

  function enqueueDanmaku(item, isSelf = false) {
    if (!item || !item.text) return;
    if (!war.danmakuOn && !isSelf) return;
    const entry = {
      id: Number(item.id) || 0,
      side: item.side === "LONG" || item.side === "SHORT" ? item.side : "NEUTRAL",
      nick: String(item.nick || "匿名战友").slice(0, 12),
      text: String(item.text || ""),
      isSelf: Boolean(isSelf),
    };
    if (isSelf) {
      war.danmakuQueue.unshift(entry);
      if (war.danmakuQueue.length > 28) war.danmakuQueue.pop();
    } else {
      if (war.danmakuQueue.length >= 24) war.danmakuQueue.shift();
      war.danmakuQueue.push(entry);
    }
  }

  function getDanmakuLanes(H) {
    if (H < 310) {
      return [20, Math.max(46, H - 20)];
    }
    return [21, 49, Math.max(96, H - 49), Math.max(124, H - 21)];
  }

  function updateAndDrawDanmaku(ctx, W, H, dt) {
    if (!war.danmakuOn) {
      if (war.danmakuActive.length) war.danmakuActive = [];
      return;
    }

    const narrow = W < 520;
    const lanes = getDanmakuLanes(H);
    const font = `600 ${narrow ? 11 : 12}px -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", sans-serif`;
    const tagFont = `700 ${narrow ? 9.5 : 10}px ui-monospace, SFMono-Regular, Menlo, monospace`;

    // Spawn queued danmaku into the least crowded upper/lower sky lane
    war.dmSpawnCooldown = Math.max(0, war.dmSpawnCooldown - dt);
    if (war.danmakuQueue.length > 0 && war.dmSpawnCooldown <= 0 && war.danmakuActive.length < 14) {
      let bestLane = -1;
      let bestRight = Infinity;
      for (let i = 0; i < lanes.length; i++) {
        let maxRight = -Infinity;
        for (const d of war.danmakuActive) {
          if (d.lane === i) {
            const r = d.x + d.w;
            if (r > maxRight) maxRight = r;
          }
        }
        if (maxRight < W - 26 && maxRight < bestRight) {
          bestRight = maxRight;
          bestLane = i;
        }
      }
      if (bestLane >= 0) {
        const next = war.danmakuQueue.shift();
        const maxChars = narrow ? 24 : 40;
        const rawText = next.text.length > maxChars ? `${next.text.slice(0, maxChars - 1)}…` : next.text;
        const showNick = next.nick && next.nick !== "匿名战友" ? `${next.nick}: ` : "";
        const label = `${showNick}${rawText}`;
        ctx.save();
        ctx.font = font;
        const textW = ctx.measureText(label).width;
        ctx.restore();
        const tagW = narrow ? 20 : 22;
        const pillH = narrow ? 21 : 23;
        const pillW = Math.ceil(tagW + textW + 20);
        const speed = (narrow ? 68 : 82) + Math.min(28, pillW * 0.08) + (next.isSelf ? 8 : 0);
        war.danmakuActive.push({
          id: Number(next.id) || 0,
          lane: bestLane,
          y: lanes[bestLane],
          x: W + 14,
          w: pillW,
          h: pillH,
          tagW,
          speed,
          side: next.side,
          label,
          isSelf: next.isSelf,
        });
        war.dmSpawnCooldown = next.isSelf ? 0.22 : 0.48;
      }
    }

    if (!war.danmakuActive.length) return;

    const kept = [];
    for (const d of war.danmakuActive) {
      d.x -= d.speed * dt;
      if (d.x + d.w < -20) continue;

      // Clamp y smoothly if canvas resized
      const laneY = lanes[Math.min(d.lane, lanes.length - 1)] || 22;
      const topY = laneY - d.h / 2;
      const sideCol = d.side === "LONG" ? "#10b981" : d.side === "SHORT" ? "#f43f5e" : "#38bdf8";
      const tagTxt = d.side === "LONG" ? "多" : d.side === "SHORT" ? "空" : "观";

      ctx.save();
      ctx.globalAlpha = d.isSelf ? 0.96 : 0.88;
      ctx.fillStyle = "rgba(8, 12, 20, 0.82)";
      ctx.strokeStyle = d.isSelf ? "#f59e0b" : sideCol;
      ctx.lineWidth = d.isSelf ? 1.6 : 1.0;
      ctx.beginPath();
      ctx.roundRect(d.x, topY, d.w, d.h, d.h / 2);
      ctx.fill();
      ctx.stroke();

      // Camp micro badge
      ctx.fillStyle = sideCol;
      ctx.beginPath();
      ctx.roundRect(d.x + 4, topY + 3.5, d.tagW - 2, d.h - 7, (d.h - 7) / 2);
      ctx.fill();

      ctx.font = tagFont;
      ctx.fillStyle = "#061018";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(tagTxt, d.x + 4 + (d.tagW - 2) / 2, laneY + 0.5);

      // Message text
      ctx.font = font;
      ctx.fillStyle = d.isSelf ? "#fef3c7" : "#f1f5f9";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(d.label, d.x + d.tagW + 8, laneY + 0.5);
      ctx.restore();

      kept.push(d);
    }
    war.danmakuActive = kept;
  }

  function renderBarragePulse() {
    const st = barrage.stats24h || {};
    const rawTotal = st.total;
    const total =
      rawTotal !== undefined && rawTotal !== null && Number.isFinite(Number(rawTotal))
        ? Math.max(0, Math.floor(Number(rawTotal)))
        : barrage.items.length;
    const badge = $("dm-count-badge");
    if (badge) badge.textContent = String(total);

    const lPct = Number.isFinite(Number(st.long_pct)) ? Math.max(0, Math.min(100, Number(st.long_pct))) : 50;
    const longInt = Math.round(lPct);
    const shortInt = 100 - longInt;
    const pLong = $("dm-pulse-long");
    const pShort = $("dm-pulse-short");
    const bLong = $("dm-pulse-bar-long");
    const bShort = $("dm-pulse-bar-short");
    if (pLong) pLong.textContent = `多 ${longInt}%`;
    if (pShort) pShort.textContent = `空 ${shortInt}%`;
    if (bLong) bLong.style.width = `${lPct}%`;
    if (bShort) bShort.style.width = `${100 - lPct}%`;
  }

  function setBarrageSyncStatus(online, text) {
    barrage.online = online;
    const dot = $("dm-sync-dot");
    const lbl = $("dm-sync-status");
    if (dot) dot.classList.toggle("online", online);
    if (lbl && text) lbl.textContent = text;
  }

  function renderBarrageWall() {
    const list = $("dm-msg-list");
    if (!list) return;
    renderBarragePulse();

    const filtered = barrage.items.filter((m) => {
      if (barrage.filterSide === "ALL") return true;
      return m.side === barrage.filterSide;
    });

    if (!filtered.length) {
      const hint =
        barrage.filterSide === "ALL"
          ? "前线暂静默，选择多/空阵营发第一条弹幕占领制空权；全网实时同步。"
          : `当前「${barrage.filterSide === "LONG" ? "多军" : "空军"}」暂无留言，来发第一条助威弹幕。`;
      list.innerHTML = `
        <div class="dm-pin">
          <span class="dm-badge dm-badge-sys">战情广播</span>
          <span>${esc(hint)}</span>
        </div>
      `;
      return;
    }

    const html = filtered
      .slice(0, 80)
      .map((m) => {
        const sideCls = m.side === "LONG" ? "is-long" : m.side === "SHORT" ? "is-short" : "is-neutral";
        const badgeCls = m.side === "LONG" ? "dm-badge-long" : m.side === "SHORT" ? "dm-badge-short" : "dm-badge-neutral";
        const badgeTxt = m.side === "LONG" ? "多" : m.side === "SHORT" ? "空" : "观";
        const isSelf = barrage.selfIds.has(m.id);
        const isLiked = barrage.likedIds.has(m.id);
        const isLiking = barrage.likingIds.has(m.id);
        const coinSafe = m.coin && m.coin !== "ALL" ? `<span class="dm-coin-tag">${esc(m.coin)}</span>` : "";
        const likesNum = Math.max(0, Number(m.likes) || 0);
        const likeBtn =
          m.id > 0
            ? `<button class="dm-like-btn${isLiked ? " liked" : ""}" type="button" data-action="like" data-id="${m.id}"${isLiking ? " disabled" : ""} title="点赞助威">▲ ${likesNum}</button>`
            : "";
        return `
          <div class="dm-item ${sideCls}${isSelf ? " is-self" : ""}">
            <div class="dm-item-meta">
              <span class="dm-badge ${badgeCls}">${badgeTxt}</span>
              ${coinSafe}
              <span class="dm-nick">${esc(m.nick || "匿名战友")}</span>
              <span class="dm-ts">${esc(fmtBarrageTime(m.ts))}</span>
              ${likeBtn}
            </div>
            <div class="dm-item-text" data-action="replay" data-id="${m.id}" title="点击重放该条弹幕">${esc(m.text)}</div>
          </div>
        `;
      })
      .join("");
    list.innerHTML = html;
  }

  function normalizeBarrageItem(raw) {
    const id = Number(raw?.id);
    if (!Number.isInteger(id)) return null;
    return {
      id,
      ts: Number(raw.ts) || Math.floor(Date.now() / 1000),
      coin: String(raw.coin || "BTC"),
      side: raw.side === "LONG" || raw.side === "SHORT" ? raw.side : "NEUTRAL",
      nick: String(raw.nick || "匿名战友"),
      text: String(raw.text || ""),
      likes: Math.max(0, Number(raw.likes) || 0),
    };
  }

  function mergeBarrageItems(newItems, { initial = false, launchNew = false } = {}) {
    if (!Array.isArray(newItems) || !newItems.length) return false;
    let changed = false;
    const freshArr = [];
    const byId = new Map(barrage.items.map((m) => [m.id, m]));
    for (const raw of newItems) {
      const clean = normalizeBarrageItem(raw);
      if (!clean) continue;
      const alreadySeen = barrage.seenIds.has(clean.id);
      addBoundedSet(barrage.seenIds, clean.id, 240);
      if (!alreadySeen) freshArr.push(clean);
      if (byId.has(clean.id)) {
        const existing = byId.get(clean.id);
        if (existing.likes !== clean.likes) {
          existing.likes = clean.likes;
          changed = true;
        }
        continue;
      }
      byId.set(clean.id, clean);
      barrage.items.push(clean);
      changed = true;
    }
    if (changed) {
      barrage.items.sort((a, b) => b.id - a.id);
      if (barrage.items.length > 80) barrage.items.length = 80;
    }
    if (initial && freshArr.length > 0 && war.danmakuOn) {
      const seeds = freshArr.slice(0, 5).reverse();
      seeds.forEach((m) => enqueueDanmaku(m, false));
    } else if (launchNew && freshArr.length > 0) {
      freshArr
        .sort((a, b) => a.id - b.id)
        .forEach((m) => enqueueDanmaku(m, barrage.selfIds.has(m.id)));
    }
    return changed;
  }

  function reconcileBarrageWindow(windowItems) {
    if (!Array.isArray(windowItems)) return false;
    const normalized = [];
    const validIds = new Set();
    for (const raw of windowItems) {
      const clean = normalizeBarrageItem(raw);
      if (!clean || clean.id <= 0 || validIds.has(clean.id)) continue;
      validIds.add(clean.id);
      normalized.push(clean);
    }
    normalized.sort((a, b) => b.id - a.id);
    if (normalized.length > 80) normalized.length = 80;

    // Detect if visible wall changed (new items, updated like counts, or admin-hidden removals)
    let changed = normalized.length !== barrage.items.length;
    if (!changed) {
      for (let i = 0; i < normalized.length; i++) {
        const a = normalized[i];
        const b = barrage.items[i];
        if (!b || a.id !== b.id || a.likes !== b.likes || a.text !== b.text) {
          changed = true;
          break;
        }
      }
    }

    // Evict any moderated/hidden message not in authoritative visible window from pending/active danmaku
    war.danmakuQueue = war.danmakuQueue.filter((q) => !(q.id > 0 && !validIds.has(q.id)));
    war.danmakuActive = war.danmakuActive.filter((d) => !(d.id > 0 && !validIds.has(d.id)));

    if (changed) {
      barrage.items = normalized;
    }
    return changed;
  }

  async function fetchJsonWithTimeout(url, opts = {}, timeoutMs = 6000, externalCtrl = null) {
    const ctrl = externalCtrl || (typeof AbortController === "function" ? new AbortController() : null);
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    try {
      const resp = await fetch(url, { ...opts, signal: ctrl ? ctrl.signal : undefined });
      const data = await resp.json().catch(() => null);
      return { resp, data };
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function pollBarrage() {
    if (barrage.pollInFlight || document.hidden || !war.running) return;
    barrage.pollInFlight = true;
    const seq = ++barrage.pollSeq;
    const base = getBarrageApiBase();
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    barrage.pollAbortCtrl = ctrl;

    try {
      let pages = 0;
      let anyChanged = false;
      const wasUninitialized = !barrage.initialized;
      let isInitial = wasUninitialized;

      while (pages < 4 && war.running && !document.hidden) {
        pages += 1;
        const url = isInitial
          ? `${base}?limit=80`
          : `${base}?after_id=${barrage.pollCursor}&limit=30&include_window=1`;
        const { resp, data } = await fetchJsonWithTimeout(url, { cache: "no-store" }, 6000, ctrl);
        if (seq !== barrage.pollSeq) return;
        if (!resp.ok || !data || data.ok !== true) throw new Error(`HTTP ${resp.status}`);

        // Cursor advances strictly via server-confirmed GET page next_cursor (never skipped by POST!)
        const nextCur = Number(data.next_cursor);
        if (Number.isInteger(nextCur) && nextCur >= barrage.pollCursor) {
          barrage.pollCursor = nextCur;
        }
        if (data.stats_24h && typeof data.stats_24h === "object") {
          barrage.stats24h = data.stats_24h;
        }

        const merged = mergeBarrageItems(data.items || [], {
          initial: isInitial,
          launchNew: !isInitial,
        });
        let pageChanged = merged;

        if (!isInitial && Array.isArray(data.window)) {
          if (reconcileBarrageWindow(data.window)) pageChanged = true;
        }

        barrage.initialized = true;
        if (isInitial || pageChanged) {
          anyChanged = true;
          renderBarrageWall();
        }
        isInitial = false;
        if (!data.has_more) break;
      }

      if (seq !== barrage.pollSeq) return;
      setBarrageSyncStatus(true, "全网实时同步");
      if (!anyChanged && !wasUninitialized) {
        renderBarragePulse();
      }
    } catch {
      if (seq !== barrage.pollSeq) return;
      if (!barrage.initialized) {
        renderBarrageWall();
      }
      setBarrageSyncStatus(false, "本地预览模式");
    } finally {
      if (barrage.pollAbortCtrl === ctrl) barrage.pollAbortCtrl = null;
      barrage.pollInFlight = false;
    }
  }

  function startBarragePolling() {
    if (barrage.pollTimer) return;
    pollBarrage();
    barrage.pollTimer = setInterval(() => {
      if (war.running && !document.hidden) pollBarrage();
    }, 4500);
  }

  function stopBarragePolling() {
    barrage.pollSeq += 1;
    if (barrage.pollAbortCtrl) {
      try {
        barrage.pollAbortCtrl.abort();
      } catch {}
      barrage.pollAbortCtrl = null;
    }
    if (barrage.pollTimer) {
      clearInterval(barrage.pollTimer);
      barrage.pollTimer = null;
    }
  }

  function updateSendCooldownUi() {
    const btn = $("btn-dm-send");
    if (!btn) return;
    const rem = Math.ceil((barrage.cooldownUntil - Date.now()) / 1000);
    if (rem > 0) {
      btn.disabled = true;
      btn.textContent = `${rem}s`;
    } else {
      btn.disabled = false;
      btn.textContent = "发射弹幕";
      if (barrage.cooldownTimer) {
        clearInterval(barrage.cooldownTimer);
        barrage.cooldownTimer = null;
      }
    }
  }

  function startSendCooldown(sec = 4) {
    barrage.cooldownUntil = Date.now() + sec * 1000;
    updateSendCooldownUi();
    if (barrage.cooldownTimer) clearInterval(barrage.cooldownTimer);
    barrage.cooldownTimer = setInterval(updateSendCooldownUi, 250);
  }

  function applyComposeSide(side) {
    barrage.composeSide = side === "SHORT" || side === "NEUTRAL" ? side : "LONG";
    const cls = barrage.composeSide === "LONG" ? "is-long" : barrage.composeSide === "SHORT" ? "is-short" : "is-neutral";
    $("dm-side-group")
      ?.querySelectorAll(".dm-side-btn")
      .forEach((b) => b.classList.toggle("active", b.dataset.side === barrage.composeSide));
    const btn = $("btn-dm-send");
    if (btn) {
      btn.classList.remove("is-long", "is-short", "is-neutral");
      btn.classList.add(cls);
    }
  }

  async function submitBarrage(ev) {
    ev.preventDefault();
    if (Date.now() < barrage.cooldownUntil) return;

    const nickEl = $("dm-nick");
    const textEl = $("dm-input");
    const nick = (nickEl?.value || "").trim();
    const text = (textEl?.value || "").trim();
    if (!text) {
      showBarrageFeedback("请先输入弹幕或盘面留言内容", "warn", 2400);
      textEl?.focus();
      return;
    }
    if (text.length > 80) {
      showBarrageFeedback("单条内容最多 80 字", "warn", 2800);
      return;
    }
    if (nick) safeStoreSet("danclub_barrage_nick", nick.slice(0, 16));

    const btn = $("btn-dm-send");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "发射中…";
    }

    const payload = {
      nick: nick || "匿名战友",
      side: barrage.composeSide,
      coin: war.activeCoin,
      text,
    };

    try {
      const { resp, data } = await fetchJsonWithTimeout(
        getBarrageApiBase(),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        6000
      );
      if (!resp.ok || !data || data.ok !== true) {
        const errMsg = (data && data.error) || `发送失败（HTTP ${resp.status}）`;
        showBarrageFeedback(errMsg, "err", 4200);
        const retry = Number(data?.retry_after);
        if (resp.status === 429 && Number.isFinite(retry) && retry > 0) {
          startSendCooldown(Math.min(15, Math.ceil(retry)));
        } else {
          updateSendCooldownUi();
        }
        return;
      }

      if (textEl) textEl.value = "";
      const item = data.item;
      if (item && Number.isInteger(Number(item.id))) {
        addBoundedSet(barrage.selfIds, Number(item.id), 100);
        if (data.stats_24h && typeof data.stats_24h === "object") {
          barrage.stats24h = data.stats_24h;
        }
        // Merge for immediate display ONLY — do NOT advance barrage.pollCursor so interleaved messages are never skipped!
        mergeBarrageItems([item], { initial: false, launchNew: false });
        if (war.danmakuOn) enqueueDanmaku(item, true);
        renderBarrageWall();
      }
      setBarrageSyncStatus(true, "全网实时同步");
      showBarrageFeedback(
        war.danmakuOn ? "弹幕已直飞战场并同步前线留言墙" : "已同步至前线留言墙（飞屏弹幕当前关闭）",
        "ok",
        2600
      );
      startSendCooldown(4);
    } catch {
      if (textEl) textEl.value = "";
      const localId = barrage.localSeq--;
      const localItem = {
        id: localId,
        ts: Math.floor(Date.now() / 1000),
        coin: payload.coin,
        side: payload.side,
        nick: payload.nick,
        text: payload.text,
        likes: 0,
      };
      addBoundedSet(barrage.selfIds, localId, 100);
      barrage.items.unshift(localItem);
      if (barrage.items.length > 80) barrage.items.length = 80;
      if (war.danmakuOn) enqueueDanmaku(localItem, true);
      setBarrageSyncStatus(false, "本地预览模式");
      renderBarrageWall();
      showBarrageFeedback("暂未连通弹幕服务器，已仅在本地战场飞屏预览", "warn", 3800);
      startSendCooldown(3);
    }
  }

  async function likeBarrageMessage(id) {
    if (!Number.isInteger(id) || id <= 0) return;
    if (barrage.likedIds.has(id) || barrage.likingIds.has(id)) return;
    barrage.likingIds.add(id);
    renderBarrageWall();

    try {
      const { resp, data } = await fetchJsonWithTimeout(
        `${getBarrageApiBase()}/like`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id }),
        },
        5000
      );
      if (resp.ok && data && data.ok === true) {
        addBoundedSet(barrage.likedIds, id, 200);
        safeStoreSet("danclub_barrage_liked", JSON.stringify(Array.from(barrage.likedIds)));
        const target = barrage.items.find((m) => m.id === id);
        if (target) {
          target.likes = Math.max(0, Number(data.likes) || target.likes);
        }
      } else {
        const errMsg = (data && data.error) || "点赞失败，请稍后重试";
        showBarrageFeedback(errMsg, "warn", 2600);
      }
    } catch {
      showBarrageFeedback("网络异常，点赞未确认，可重试", "warn", 2600);
    } finally {
      barrage.likingIds.delete(id);
      renderBarrageWall();
    }
  }

  /* ---------- Liquidation Magnet Ruler & Default Real-Time Loss / Kill Feed Rail ---------- */
  function setRailTab(tab) {
    war.railTab = tab === "chat" ? "chat" : "liq";
    $("war-rail-tabs")
      ?.querySelectorAll(".seg-btn")
      .forEach((b) => {
        const on = b.dataset.rail === war.railTab;
        b.classList.toggle("active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
    const liqPane = $("war-rail-liq");
    const chatPane = $("war-rail-chat");
    const winGrp = $("liq-win-group");
    const dmGrp = $("dm-filter-group");
    if (liqPane) liqPane.hidden = war.railTab !== "liq";
    if (chatPane) chatPane.hidden = war.railTab !== "chat";
    if (winGrp) winGrp.hidden = war.railTab !== "liq";
    if (dmGrp) dmGrp.hidden = war.railTab !== "chat";
    if (war.railTab === "liq") renderLiqRail();
    else renderBarrageWall();
  }

  function strictNum(v) {
    if (typeof v === "number") return Number.isFinite(v) ? v : null;
    if (typeof v === "string") {
      const s = v.trim();
      if (!s || !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(s)) return null;
      const n = Number(s);
      return Number.isFinite(n) ? n : null;
    }
    return null;
  }

  function renderLiqMagnet() {
    const coin = war.activeCoin;
    const markEl = $("war-mag-mark");
    const alertEl = $("war-mag-alert");
    const ticksEl = $("war-mag-ticks");
    const longEl = $("war-mag-long");
    const shortEl = $("war-mag-short");
    if (!markEl || !ticksEl || !longEl || !shortEl) return;

    const mag = war.data?.liq_magnets?.[coin] || null;
    const hlLivePx = getMatchedLivePx("HL_MARK", coin);
    const snapPx = Number(mag?.mark_px) || 0;
    const displayPx = hlLivePx || snapPx;
    const pxTag = hlLivePx ? "HL 标记价" : "快照";
    markEl.textContent = displayPx > 0 ? `${coin} ${fmtPxShort(displayPx)} (${pxTag})` : `${coin} --`;
    renderLeverageCrowding(coin);

    const flagships = war.data?.flagships || [];
    if (!flagships.length) {
      if (hlLivePx) war.redCriticalPrevIds.set(coin, new Set());
      ticksEl.innerHTML = "";
      longEl.textContent = "⬇ 多头强平墙：暂无单所旗舰强平数据";
      shortEl.textContent = "⬆ 空头强平墙：暂无单所旗舰强平数据";
      if (alertEl) {
        alertEl.textContent = "等待快照";
        alertEl.className = "pill pill-muted mag-alert";
      }
      return;
    }

    // Aggregate across ALL verified single-venue flagships (never truncated to the 6 plotted tick pins!)
    const longs = [];
    const shorts = [];
    flagships.forEach((ent) => {
      (ent.positions || []).forEach((p) => {
        if (p.coin !== coin) return;
        const lq = Number(p.liq_px);
        const venues = Array.isArray(p.venues) ? p.venues : [];
        if (!Number.isFinite(lq) || lq <= 0 || venues.length !== 1) return;
        const dist = liveWhaleDistPct(p);
        if (dist === null || dist <= 0 || dist > 25) return;
        const item = {
          id: ent.id,
          display: ent.display,
          tier_code: ent.tier_code,
          liq_px: lq,
          liveDist: dist,
          usd: Number(p.usd) || 0,
          lev: p.lev,
          venue: venues[0],
        };
        if (p.side === "LONG") longs.push(item);
        else shorts.push(item);
      });
    });

    longs.sort((a, b) => a.liveDist - b.liveDist || b.usd - a.usd);
    shorts.sort((a, b) => a.liveDist - b.liveDist || b.usd - a.usd);

    const redEntCount = new Set([...longs, ...shorts].filter((x) => x.liveDist < 5).map((x) => x.id)).size;
    const totalEntCount = new Set([...longs, ...shorts].map((x) => x.id)).size;

    if (alertEl) {
      if (redEntCount > 0) {
        alertEl.textContent = `高危警戒 · ${redEntCount}户距强平<5%`;
        alertEl.className = "pill pill-risk mag-alert";
      } else if (totalEntCount > 0) {
        alertEl.textContent = `监测 ${totalEntCount} 户单所大户`;
        alertEl.className = "pill pill-info mag-alert";
      } else {
        alertEl.textContent = "±25%内无近端强平";
        alertEl.className = "pill pill-muted mag-alert";
      }
    }

    const tickHtml = [];
    longs.slice(0, 6).forEach((t) => {
      const leftPct = Math.max(3, Math.min(48.5, 50 - (t.liveDist / 25) * 47));
      const danger = t.liveDist < 5 ? " is-danger" : "";
      const tip = `[${t.tier_code}级] ${t.display} (${t.venue}) · 多单 ${fmtCompact(t.usd)}${t.lev ? ` (${t.lev}x)` : ""} · 强平 ${fmtPxShort(t.liq_px)} (-${t.liveDist.toFixed(1)}%) · 点击查档`;
      tickHtml.push(
        `<button class="mag-tick is-long${danger}" type="button" data-ent-id="${esc(t.id)}" style="left:${leftPct.toFixed(1)}%;" title="${esc(tip)}" aria-label="${esc(tip)}"></button>`
      );
    });
    shorts.slice(0, 6).forEach((t) => {
      const leftPct = Math.max(51.5, Math.min(97, 50 + (t.liveDist / 25) * 47));
      const danger = t.liveDist < 5 ? " is-danger" : "";
      const tip = `[${t.tier_code}级] ${t.display} (${t.venue}) · 空单 ${fmtCompact(t.usd)}${t.lev ? ` (${t.lev}x)` : ""} · 强平 ${fmtPxShort(t.liq_px)} (+${t.liveDist.toFixed(1)}%) · 点击查档`;
      tickHtml.push(
        `<button class="mag-tick is-short${danger}" type="button" data-ent-id="${esc(t.id)}" style="left:${leftPct.toFixed(1)}%;" title="${esc(tip)}" aria-label="${esc(tip)}"></button>`
      );
    });
    ticksEl.innerHTML = tickHtml.join("");

    if (longs.length) {
      const topL = longs[0];
      const sumL = longs.reduce((acc, x) => acc + x.usd, 0);
      const cntL = new Set(longs.map((x) => x.id)).size;
      longEl.textContent = `⬇ 多头强平区：最近 ${fmtPxShort(topL.liq_px)} (-${topL.liveDist.toFixed(1)}%) · 25%内 ${fmtCompact(sumL)} (${cntL}户)`;
    } else {
      longEl.textContent = "⬇ 多头强平区：25%内无单所大户强平位";
    }
    if (shorts.length) {
      const topS = shorts[0];
      const sumS = shorts.reduce((acc, x) => acc + x.usd, 0);
      const cntS = new Set(shorts.map((x) => x.id)).size;
      shortEl.textContent = `⬆ 空头强平区：最近 ${fmtPxShort(topS.liq_px)} (+${topS.liveDist.toFixed(1)}%) · 25%内 ${fmtCompact(sumS)} (${cntS}户)`;
    } else {
      shortEl.textContent = "⬆ 空头强平区：25%内无单所大户强平位";
    }

    // Edge-triggered radar alert ONLY when a verified HL flagship newly crosses into <3% critical zone (5m cooldown)
    if (hlLivePx) {
      const nowMs = Date.now();
      const curCritIds = new Set([...longs, ...shorts].filter((x) => x.liveDist < 3.0).map((x) => x.id));
      const prevCritIds = war.redCriticalPrevIds.get(coin);
      if (!prevCritIds) {
        // First live HL observation for this coin: establish baseline silently
        curCritIds.forEach((id) => war.redCriticalSeen.set(`${coin}:${id}`, nowMs));
        war.redCriticalPrevIds.set(coin, curCritIds);
      } else {
        let triggered = false;
        curCritIds.forEach((id) => {
          if (prevCritIds.has(id)) return;
          const k = `${coin}:${id}`;
          const lastBeep = war.redCriticalSeen.get(k) || 0;
          if (nowMs - lastBeep > 300_000) {
            war.redCriticalSeen.set(k, nowMs);
            triggered = true;
          }
        });
        war.redCriticalPrevIds.set(coin, curCritIds);
        if (triggered) playLiqRadarBeep(true, false);
      }
    }
  }

  function renderLeverageCrowding(coin) {
    const el = $("war-mag-crowd");
    if (!el) return;
    const fl = war.flows;
    if (!fl) {
      el.innerHTML = `<span class="muted">杠杆拥挤度：等待资金流快照…</span>`;
      return;
    }
    const bn = (fl.bn_oi_radar || []).find((x) => String(x.base || "").toUpperCase() === coin) || null;
    const hl = (fl.hyperliquid?.top_markets || []).find((x) => String(x.coin || "").toUpperCase() === coin) || null;
    if (!bn && !hl) {
      el.innerHTML = `<span class="muted">杠杆拥挤度：暂无 ${esc(coin)} 持仓与费率数据</span>`;
      return;
    }

    const parts = [];
    const fundVals = [];
    const bnOi = strictNum(bn?.oi_usd);
    const bnOi1h = strictNum(bn?.chg_1h_oi_pct);
    const bnFund = strictNum(bn?.funding_ann_pct);
    if (bnOi !== null && bnOi > 0) {
      const oi1hTxt = bnOi1h !== null ? ` (${bnOi1h >= 0 ? "+" : ""}${bnOi1h.toFixed(2)}%)` : "";
      parts.push(`<span>BN 持仓 <b>${fmtCompact(bnOi)}</b><span class="muted">${esc(oi1hTxt)}</span></span>`);
    }
    if (bnFund !== null) {
      fundVals.push(bnFund);
      parts.push(`<span>BN 费率 <b class="${bnFund >= 10 ? "warn" : bnFund <= -8 ? "ok" : ""}">${bnFund >= 0 ? "+" : ""}${bnFund.toFixed(1)}%/年</b></span>`);
    }

    const hlOi = strictNum(hl?.oi_usd);
    const hlFund = strictNum(hl?.funding_ann_pct);
    if (hlOi !== null && hlOi > 0) {
      parts.push(`<span>HL 持仓 <b>${fmtCompact(hlOi)}</b></span>`);
    }
    if (hlFund !== null) {
      fundVals.push(hlFund);
      parts.push(`<span>HL 费率 <b class="${hlFund >= 10 ? "warn" : hlFund <= -8 ? "ok" : ""}">${hlFund >= 0 ? "+" : ""}${hlFund.toFixed(1)}%/年</b></span>`);
    }

    if (!parts.length) {
      el.innerHTML = `<span class="muted">杠杆拥挤度：暂无 ${esc(coin)} 有效持仓与费率</span>`;
      return;
    }

    let badgeTxt = "费率数据不足";
    let badgeCls = "pill-muted";
    if (fundVals.length > 0) {
      const avgFund = fundVals.reduce((a, b) => a + b, 0) / fundVals.length;
      if (avgFund >= 15) {
        badgeTxt = "多头极度拥挤 · 防洗多";
        badgeCls = "pill-risk";
      } else if (avgFund >= 8) {
        badgeTxt = "多头偏拥挤";
        badgeCls = "pill-warn";
      } else if (avgFund <= -15) {
        badgeTxt = "空头极度拥挤 · 防逼空";
        badgeCls = "pill-ok";
      } else if (avgFund <= -5) {
        badgeTxt = "空头偏拥挤";
        badgeCls = "pill-info";
      } else {
        badgeTxt = "杠杆中性";
        badgeCls = "pill-muted";
      }
    }

    el.innerHTML = `
      <div class="mag-crowd-metrics">${parts.join('<span class="muted">·</span>')}</div>
      <span class="pill ${badgeCls}">${esc(badgeTxt)}</span>
    `;
  }

  function renderLiqRhythm(hourlyArr) {
    const barsEl = $("liq-rhythm-bars");
    const stateEl = $("liq-rhythm-state");
    if (!barsEl || !stateEl) return;
    if (!Array.isArray(hourlyArr) || hourlyArr.length !== 24) {
      barsEl.innerHTML = "";
      stateEl.textContent = "无分时快照";
      stateEl.className = "mono muted";
      return;
    }

    let maxSide = 0;
    let sum24 = 0;
    let peakHr = 0;
    hourlyArr.forEach((b) => {
      const lu = Math.max(0, strictNum(b?.long_usd) ?? 0);
      const su = Math.max(0, strictNum(b?.short_usd) ?? 0);
      if (lu > maxSide) maxSide = lu;
      if (su > maxSide) maxSide = su;
      const tot = lu + su;
      sum24 += tot;
      if (tot > peakHr) peakHr = tot;
    });

    const last1 = hourlyArr[hourlyArr.length - 1] || {};
    const last1Tot = (strictNum(last1.long_usd) ?? 0) + (strictNum(last1.short_usd) ?? 0);
    const prev23Avg = (sum24 - last1Tot) / 23;
    const last3Tot = hourlyArr
      .slice(-3)
      .reduce((acc, b) => acc + (strictNum(b?.long_usd) ?? 0) + (strictNum(b?.short_usd) ?? 0), 0);

    if (sum24 <= 0) {
      stateEl.textContent = "24h 无大额爆仓";
      stateEl.className = "mono muted";
    } else if (last1Tot >= 2_000_000 || (last1Tot >= 250_000 && prev23Avg > 0 && last1Tot >= prev23Avg * 2.0)) {
      stateEl.textContent = `近1h放量 ${fmtCompact(last1Tot)}`;
      stateEl.className = "mono warn";
    } else if (last1Tot > 0) {
      stateEl.textContent = `近1h清算 ${fmtCompact(last1Tot)}`;
      stateEl.className = "mono";
    } else if (last3Tot >= 2_000_000) {
      stateEl.textContent = `近3h清算 ${fmtCompact(last3Tot)}`;
      stateEl.className = "mono";
    } else {
      stateEl.textContent = `峰值 ${fmtCompact(peakHr)} · 平稳`;
      stateEl.className = "mono muted";
    }

    const scale = maxSide > 0 ? maxSide : 1;
    barsEl.innerHTML = hourlyArr
      .map((b, idx) => {
        const lu = Math.max(0, Number(b?.long_usd) || 0);
        const su = Math.max(0, Number(b?.short_usd) || 0);
        const hUp = lu > 0 ? Math.max(14, Math.round((lu / scale) * 100)) : 0;
        const hDn = su > 0 ? Math.max(14, Math.round((su / scale) * 100)) : 0;
        const tStart = fmtBarrageTime(Number(b?.ts) || 0);
        const tip = `${tStart}起1h · 爆多 ${fmtCompact(lu)} / 爆空 ${fmtCompact(su)}`;
        const nowCls = idx === hourlyArr.length - 1 ? " is-now" : "";
        return `
          <div class="lr-col${nowCls}" title="${esc(tip)}">
            <div class="lr-half lr-half-up">${hUp > 0 ? `<div class="lr-bar lr-bar-long" style="height:${hUp}%;"></div>` : ""}</div>
            <div class="lr-half lr-half-dn">${hDn > 0 ? `<div class="lr-bar lr-bar-short" style="height:${hDn}%;"></div>` : ""}</div>
          </div>`;
      })
      .join("");
  }

  function renderLiqRail() {
    const coinBtn = $("liq-scope-coin-btn");
    if (coinBtn) coinBtn.textContent = war.activeCoin;

    const liq = war.data?.liquidations || null;
    const scopeKey = war.liqScope === "ALL" ? "ALL" : war.activeCoin;
    const winObj = liq?.available ? liq.windows?.[war.liqWin]?.[scopeKey] || null : null;

    const srcEl = $("liq-board-src");
    if (srcEl) {
      srcEl.textContent = `${liq?.source || "CoinGlass 全网爆仓"} · ${scopeKey === "ALL" ? "全网" : scopeKey} ${war.liqWin}汇总`;
    }

    const lValEl = $("liq-long-val");
    const sValEl = $("liq-short-val");
    const cntEl = $("liq-total-cnt");
    const bL = $("liq-bar-long");
    const bS = $("liq-bar-short");
    const exEl = $("liq-ex-row");

    if (!winObj) {
      if (lValEl) lValEl.textContent = "--";
      if (sValEl) sValEl.textContent = "--";
      if (cntEl) cntEl.textContent = "无快照";
      if (bL) bL.style.width = "50%";
      if (bS) bS.style.width = "50%";
      if (exEl) exEl.textContent = war.data ? "CoinGlass 快照暂不可用（实时强平流独立运行）" : "正在载入 CoinGlass 快照…";
    } else {
      const lUsd = Number(winObj.long_usd) || 0;
      const sUsd = Number(winObj.short_usd) || 0;
      const tot = lUsd + sUsd;
      const lPct = tot > 0 ? (lUsd / tot) * 100 : 50;
      const sPct = tot > 0 ? 100 - lPct : 50;
      if (lValEl) lValEl.textContent = tot > 0 ? `${fmtCompact(lUsd)} (${Math.round(lPct)}%)` : "$0";
      if (sValEl) sValEl.textContent = tot > 0 ? `${fmtCompact(sUsd)} (${Math.round(sPct)}%)` : "$0";
      if (cntEl) cntEl.textContent = `${Number(winObj.count) || 0} 笔`;
      if (bL) bL.style.width = `${lPct.toFixed(1)}%`;
      if (bS) bS.style.width = `${sPct.toFixed(1)}%`;
      if (exEl) {
        const exList = Array.isArray(winObj.top_exchanges) ? winObj.top_exchanges.slice(0, 3) : [];
        exEl.textContent = exList.length
          ? `主战场：${exList.map((e) => `${e.ex} ${e.pct}%`).join(" · ")}`
          : `当前 ${war.liqWin} 窗口已统计为 0 笔`;
      }
    }
    renderLiqRhythm(liq?.available ? liq.hourly_24h?.[scopeKey] : null);

    const feedEl = $("war-liq-feed");
    if (!feedEl) return;

    const filterCoin = war.liqScope === "COIN" ? war.activeCoin : null;
    const nowSec = Math.floor(Date.now() / 1000);

    // Collect typed buckets independently so counts & grouped sections make every data type unmistakably clear!
    const liqRows = [];
    const liveShown = [];
    war.liveLiqEvents.forEach((ev) => {
      if (filterCoin && ev.coin !== filterCoin) return;
      liveShown.push(ev);
      const ageSec = nowSec - (Number(ev.ts) || 0);
      const isFreshLive = ageSec >= -60 && ageSec <= 180;
      liqRows.push({ kind: "LIQ", ts: Number(ev.ts) || 0, prio: 1, live: isFreshLive, data: ev });
    });
    const usedLiveIdx = new Set();
    (liq?.recent_events || []).forEach((ev) => {
      if (filterCoin && ev.coin !== filterCoin) return;
      const evTs = Number(ev.ts) || 0;
      const evUsd = Number(ev.usd) || 0;
      const matchIdx = liveShown.findIndex(
        (lv, idx) =>
          !usedLiveIdx.has(idx) &&
          lv.coin === ev.coin &&
          lv.side === ev.side &&
          lv.ex_short === ev.ex_short &&
          Math.abs(lv.ts - evTs) <= 15 &&
          Math.abs(lv.usd - evUsd) <= Math.max(evUsd, lv.usd) * 0.12
      );
      if (matchIdx >= 0) {
        usedLiveIdx.add(matchIdx);
        return;
      }
      liqRows.push({ kind: "LIQ", ts: evTs, prio: 0, live: false, data: ev });
    });

    const wickRows = [];
    (liq?.wick_alerts || []).forEach((wk) => {
      if (filterCoin && wk.coin !== "ALL" && wk.coin !== filterCoin) return;
      const isWick = wk.kind === "WICK_UP" || wk.kind === "WICK_DOWN";
      if (isWick) {
        wickRows.push({ kind: "WICK", ts: Number(wk.ts) || 0, prio: 0, live: false, data: wk });
      } else {
        liqRows.push({ kind: "WICK", ts: Number(wk.ts) || 0, prio: 0, live: false, data: wk });
      }
    });
    liqRows.sort((a, b) => b.ts - a.ts || b.prio - a.prio);
    wickRows.sort((a, b) => b.ts - a.ts);

    const allMoves = war.data?.recent_moves || [];
    const whaleRows = (filterCoin ? allMoves.filter((m) => m.coin === filterCoin) : allMoves).map((mv, idx) => ({
      kind: "WHALE",
      ts: Number(mv.ts) || 0,
      prio: -idx,
      live: false,
      data: mv,
    }));

    // Update live per-type counts on filter tabs so traders see data composition at a glance!
    const cntMap = {
      ALL: liqRows.length + whaleRows.length + wickRows.length,
      LIQ: liqRows.length,
      WHALE: whaleRows.length,
      WICK: wickRows.length,
    };
    const lblMap = { ALL: "全部", LIQ: "强平单", WHALE: "大户调仓", WICK: "插针预警" };
    $("liq-feed-filter")
      ?.querySelectorAll(".seg-btn")
      .forEach((b) => {
        const k = b.dataset.kind;
        if (lblMap[k]) b.textContent = `${lblMap[k]} ${cntMap[k] ?? 0}`;
      });

    const renderRow = (r) => {
      if (r.kind === "LIQ") {
        const ev = r.data;
        const isLong = ev.side === "LONG";
        const pxLabel = ev.px_kind ? `${ev.px_kind} ` : "@ ";
        const pxTxt = ev.px ? ` · ${pxLabel}${fmtPxShort(ev.px)}` : "";
        const timeLbl = r.live
          ? `<span class="liq-tag liq-tag-live">实时</span><span class="liq-ts">${esc(fmtBarrageTime(ev.ts))}</span>`
          : `<span class="liq-ts">${esc(fmtBarrageTime(ev.ts))}</span>`;
        return `
          <div class="liq-item ${isLong ? "kind-liq-long" : "kind-liq-short"}">
            <div class="liq-item-top">
              <span class="liq-tag ${isLong ? "liq-tag-long" : "liq-tag-short"}">${isLong ? "爆多" : "爆空"}</span>
              <span class="liq-ex-pill">${esc(ev.ex_short || ev.ex || "CEX")}</span>
              <span class="liq-coin-lbl">${esc(ev.coin)}</span>
              ${timeLbl}
            </div>
            <div class="liq-item-body">
              ${isLong ? "多单强平" : "空单强平"} <b class="${isLong ? "ok" : "risk"}">${fmtCompact(ev.usd)}</b><span class="mono muted">${esc(pxTxt)}</span>
            </div>
          </div>`;
      }
      if (r.kind === "WICK") {
        const wk = r.data;
        const tagTxt = wk.kind === "WICK_UP" || wk.kind === "WICK_DOWN" ? "插针预警" : "爆仓波峰";
        return `
          <div class="liq-item kind-wick">
            <div class="liq-item-top">
              <span class="liq-tag liq-tag-wick">${tagTxt}</span>
              <span class="liq-coin-lbl">${esc(wk.coin)}</span>
              <span class="liq-ts">${esc(fmtBarrageTime(wk.ts))}</span>
            </div>
            <div class="liq-item-body"><b>${esc(wk.title)}</b>${wk.detail ? `<div class="muted small">${esc(wk.detail)}</div>` : ""}</div>
          </div>`;
      }
      const mv = r.data;
      const winTxt = mv.delta_window_h ? `${mv.delta_window_h}h · 快照` : "快照";
      const dirTxt = mv.side === "LONG" ? "多" : "空";
      const mvCoin = fmtCoinLabel(mv.coin);
      return `
        <div class="liq-item kind-whale" data-ent-id="${esc(mv.id)}" title="点击查看该实盘大户完整持仓档案">
          <div class="liq-item-top">
            <span class="liq-tag liq-tag-whale">大户调仓</span>
            <span class="liq-ex-pill">${esc(mv.tier_code)}级</span>
            <span class="liq-coin-lbl">${esc(mv.display)}</span>
            <span class="liq-ts">${esc(winTxt)}</span>
          </div>
          <div class="liq-item-body" title="${esc(mvCoin.title)}">
            主仓 ${esc(mvCoin.text)}（偏${dirTxt}）<b>${esc(mv.action)}</b> · 全账户净变动 <b class="${mv.delta_usd >= 0 ? "ok" : "risk"}">${fmtSignedCompact(mv.delta_usd)}</b>
          </div>
        </div>`;
    };

    if (war.liqTypeFilter === "ALL") {
      const blocks = [];
      if (liqRows.length) {
        blocks.push(`<div class="liq-sec-head"><span>实盘大额强平（最新）</span><span class="mono">${liqRows.length} 条</span></div>`);
        blocks.push(...liqRows.slice(0, 12).map(renderRow));
      }
      if (wickRows.length) {
        blocks.push(`<div class="liq-sec-head"><span>插针爆仓预警</span><span class="mono">${wickRows.length} 条</span></div>`);
        blocks.push(...wickRows.slice(0, 4).map(renderRow));
      }
      if (whaleRows.length) {
        blocks.push(`<div class="liq-sec-head"><span>实盘大户调仓（点击查档）</span><span class="mono">${whaleRows.length} 条</span></div>`);
        blocks.push(...whaleRows.slice(0, 8).map(renderRow));
      }
      feedEl.innerHTML = blocks.length
        ? blocks.join("")
        : `<div class="dm-empty">当前筛选条件下暂无${filterCoin ? ` ${esc(filterCoin)}` : ""} 爆仓或大户异动记录</div>`;
      return;
    }

    const pickedList =
      war.liqTypeFilter === "LIQ" ? liqRows : war.liqTypeFilter === "WICK" ? wickRows : whaleRows;
    feedEl.innerHTML = pickedList.length
      ? pickedList.slice(0, 40).map(renderRow).join("")
      : `<div class="dm-empty">当前筛选条件下暂无${filterCoin ? ` ${esc(filterCoin)}` : ""} ${esc(lblMap[war.liqTypeFilter] || "")}记录</div>`;
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
    renderLiqMagnet();
    renderLiqRail();
    if (!war.running) {
      war.running = true;
      war.okxCtValAttempts = 0;
      war.lastFrameTs = performance.now();
      war.rafId = requestAnimationFrame(tick);
      connectSockets();
      connectLiqSockets();
      loadYouTubeApi(4500).catch(() => {});
      if (!war.userMuted && !document.hidden && (war.soundOn || war.pausedByHidden)) {
        playCurrentAnthem(false);
      }
    }
  }

  function stopArena() {
    war.running = false;
    if (war.rafId) cancelAnimationFrame(war.rafId);
    war.rafId = null;
    if (war.liqRailRafId !== null) {
      cancelAnimationFrame(war.liqRailRafId);
      war.liqRailRafId = null;
    }
    disconnectSockets();
    disconnectLiqSockets();
    stopBarragePolling();
    disarmGestureUnlock();
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
      renderLiqMagnet();
      renderLiqRail();
      if (war.running) connectSockets();
    });

    $("war-rail-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn || !btn.dataset.rail) return;
      setRailTab(btn.dataset.rail);
    });

    $("liq-win-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn || !["1h", "24h"].includes(btn.dataset.win)) return;
      war.liqWin = btn.dataset.win;
      $("liq-win-group").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderLiqRail();
    });

    $("liq-scope-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn || !["COIN", "ALL"].includes(btn.dataset.scope)) return;
      war.liqScope = btn.dataset.scope;
      $("liq-scope-group").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderLiqRail();
    });

    $("liq-feed-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn || !["ALL", "LIQ", "WHALE", "WICK"].includes(btn.dataset.kind)) return;
      war.liqTypeFilter = btn.dataset.kind;
      $("liq-feed-filter").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderLiqRail();
    });

    const openWhaleById = (entId) => {
      if (!entId) return;
      const ent = (war.data?.flagships || []).find((f) => f.id === entId);
      if (ent) openEntityDrawer(ent);
    };

    $("war-mag-ticks")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-ent-id]");
      if (btn) openWhaleById(btn.dataset.entId);
    });

    $("war-liq-feed")?.addEventListener("click", (e) => {
      const row = e.target.closest(".liq-item.kind-whale[data-ent-id]");
      if (row) openWhaleById(row.dataset.entId);
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

    $("btn-war-x-only")?.addEventListener("click", () => {
      war.filterXOnly = !war.filterXOnly;
      $("btn-war-x-only").classList.toggle("btn-accent-ghost", war.filterXOnly);
      renderFleetTable();
    });

    $("war-fleet-search")?.addEventListener("input", (e) => {
      war.search = e.target.value;
      renderFleetTable();
    });

    syncAudioUi();
    syncLiqBeepUi();
    syncDanmakuToggleUi();
    setRailTab(war.railTab);
    applyComposeSide(barrage.composeSide);

    const savedNick = safeStoreGet("danclub_barrage_nick", "");
    if (savedNick && $("dm-nick")) {
      $("dm-nick").value = savedNick.slice(0, 16);
    }

    $("btn-war-liq-beep")?.addEventListener("click", () => {
      war.liqBeepOn = !war.liqBeepOn;
      safeStoreSet("danclub_war_liq_beep", war.liqBeepOn ? "1" : "0");
      syncLiqBeepUi();
      if (war.liqBeepOn) playLiqRadarBeep(false, true);
    });

    // Silently unlock AudioContext on first user gesture so independent liquidation radar beeps work even when music is muted
    const unlockRadarAudioOnce = () => {
      if (war.liqBeepOn) {
        try {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (Ctx) {
            if (!war.audioCtx) war.audioCtx = new Ctx();
            if (war.audioCtx.state === "suspended") war.audioCtx.resume().catch(() => {});
          }
        } catch {}
      }
      document.removeEventListener("pointerdown", unlockRadarAudioOnce, true);
      document.removeEventListener("keydown", unlockRadarAudioOnce, true);
    };
    document.addEventListener("pointerdown", unlockRadarAudioOnce, true);
    document.addEventListener("keydown", unlockRadarAudioOnce, true);

    $("btn-war-danmaku")?.addEventListener("click", () => {
      war.danmakuOn = !war.danmakuOn;
      safeStoreSet("danclub_war_danmaku", war.danmakuOn ? "1" : "0");
      if (!war.danmakuOn) {
        war.danmakuActive = [];
        war.danmakuQueue = [];
      }
      syncDanmakuToggleUi();
    });

    $("dm-side-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".dm-side-btn");
      if (!btn || !btn.dataset.side) return;
      applyComposeSide(btn.dataset.side);
    });

    $("dm-form")?.addEventListener("submit", submitBarrage);

    $("dm-filter-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (!btn || !btn.dataset.side) return;
      barrage.filterSide = btn.dataset.side;
      $("dm-filter-group")
        .querySelectorAll(".seg-btn")
        .forEach((b) => b.classList.toggle("active", b === btn));
      renderBarrageWall();
    });

    $("dm-msg-list")?.addEventListener("click", (e) => {
      const target = e.target.closest("[data-action]");
      if (!target) return;
      const action = target.dataset.action;
      const id = Number(target.dataset.id);
      if (action === "like") {
        likeBarrageMessage(id);
      } else if (action === "replay") {
        const msg = barrage.items.find((m) => m.id === id);
        if (!msg) return;
        if (!war.danmakuOn) {
          war.danmakuOn = true;
          safeStoreSet("danclub_war_danmaku", "1");
          syncDanmakuToggleUi();
        }
        enqueueDanmaku(msg, true);
        showBarrageFeedback("已重放该条弹幕飞越战场", "ok", 1800);
      }
    });

    $("btn-war-sound")?.addEventListener("click", () => {
      war.hasUserGesture = true;
      if (war.soundOn) {
        war.userMuted = true;
        disarmGestureUnlock();
        stopAnthem(false);
      } else {
        war.userMuted = false;
        war.audioErrCount = 0;
        if (WAR_TRACKS[war.trackIdx]?.type === "youtube") war.ytUnavailable = false;
        playCurrentAnthem(false);
      }
    });

    $("btn-war-yt-close")?.addEventListener("click", () => {
      war.hasUserGesture = true;
      war.userMuted = true;
      disarmGestureUnlock();
      stopAnthem(false);
    });

    $("war-audio-prompt")?.addEventListener("click", () => {
      war.hasUserGesture = true;
      war.userMuted = false;
      war.audioErrCount = 0;
      if (WAR_TRACKS[war.trackIdx]?.type === "youtube") war.ytUnavailable = false;
      playCurrentAnthem(false);
    });

    $("war-track-select")?.addEventListener("change", (e) => {
      const idx = Number(e.target.value);
      if (!Number.isInteger(idx) || idx < 0 || idx >= WAR_TRACKS.length) return;
      war.hasUserGesture = true;
      war.userMuted = false;
      war.audioErrCount = 0;
      if (WAR_TRACKS[idx]?.type === "youtube") war.ytUnavailable = false;
      war.trackIdx = idx;
      playCurrentAnthem(true);
    });

    $("btn-war-next-track")?.addEventListener("click", () => {
      war.hasUserGesture = true;
      war.userMuted = false;
      nextTrack(true);
    });

    $("war-local-file")?.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      war.hasUserGesture = true;
      if (war.localBlobUrl) {
        try { URL.revokeObjectURL(war.localBlobUrl); } catch {}
      }
      war.localBlobUrl = URL.createObjectURL(file);
      const cleanName = String(file.name || "自定义战歌").replace(/\.[^.]+$/, "").slice(0, 18);
      const customIdx = WAR_TRACKS.findIndex((t) => t.id === "local_custom");
      const entry = {
        id: "local_custom",
        type: "audio",
        file: war.localBlobUrl,
        label: `🎵 本地 · ${cleanName}`,
        short: `🎵 ${cleanName}`,
        dur: 30,
      };
      const sel = $("war-track-select");
      if (customIdx >= 0) {
        WAR_TRACKS[customIdx] = entry;
        war.trackIdx = customIdx;
        if (sel && sel.options[customIdx]) sel.options[customIdx].textContent = entry.label;
      } else {
        WAR_TRACKS.push(entry);
        war.trackIdx = WAR_TRACKS.length - 1;
        if (sel) {
          const opt = document.createElement("option");
          opt.value = String(war.trackIdx);
          opt.textContent = entry.label;
          sel.appendChild(opt);
        }
      }
      war.userMuted = false;
      war.audioErrCount = 0;
      playCurrentAnthem(true);
    });

    $("war-vol")?.addEventListener("input", (e) => {
      const pct = Number(e.target.value);
      if (!Number.isFinite(pct)) return;
      war.volume = Math.max(0, Math.min(1, pct / 100));
      if (war.bgm) war.bgm.volume = war.volume;
      applyYtVolume();
      safeStoreSet("danclub_war_vol", war.volume);
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (war.soundOn) stopAnthem(true);
      } else if (war.running) {
        if (war.soundOn && war.pausedByHidden) playCurrentAnthem(false);
        pollBarrage();
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
    setFlows(flowsData) {
      war.flows = flowsData;
      renderLeverageCrowding(war.activeCoin);
    },
    setData(whaleData) {
      war.data = whaleData;
      rebuildCarriers();
      renderWhalePanels();
      renderLiqMagnet();
      renderLiqRail();
      updateLiqWsUi();
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
